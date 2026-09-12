# -*- coding: utf-8 -*-
"""
Сценарии: что происходит между заданиями и банком времени.

Здесь собирается день ребёнка: какие задания назначены, что уже сделано,
сколько минут заработано. Веб-слой поверх этого только рисует — вся логика,
которую имеет смысл проверять, живёт тут.

Главное правило слоя: **браузер не может объявить награду**. Он присылает
только идентификатор попытки и ответы. Сколько минут стоит задание, засчитана
ли попытка и не была ли награда выдана раньше — решает сервер по своим данным.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date

from app import bank, tasks

# Состояния задания в конкретном дне
AVAILABLE = "available"      # можно проходить
IN_PROGRESS = "in_progress"  # попытка начата, ответы не отправлены
EARNED = "earned"            # награда за сегодня уже получена


class ServiceError(Exception):
    """Ошибка сценария: неверный переход, чужая попытка, устаревшие данные."""


# --------------------------------------------------------------------------
# Пилот: оценка ребёнка и сводка для родителя
# --------------------------------------------------------------------------

RATINGS = ("boring", "ok", "fun")
RATING_LABELS = {"boring": "скучно", "ok": "нормально", "fun": "интересно"}


def rate_attempt(conn: sqlite3.Connection, *, child_id: int, attempt_id: int,
                 rating: str, at: str, comment: str = "") -> None:
    """
    Ставит оценку попытке. Добровольно: методика просит одну оценку в конце,
    а не опрос после каждого клика. Повторная оценка заменяет прежнюю.
    Комментарий — по желанию, пара слов «почему»: это единственное место,
    где сын говорит с родителем своими словами, поэтому он хранится как есть.
    """
    comment = " ".join(comment.split())[:500]
    if rating not in RATINGS:
        raise ServiceError("Непонятная оценка")
    row = conn.execute(
        "SELECT at.id, a.task_id, a.day FROM attempt at JOIN assignment a ON a.id = at.assignment_id "
        " WHERE at.id = ? AND a.child_id = ?", (attempt_id, child_id),
    ).fetchone()
    if row is None:
        raise ServiceError("Это не твоя попытка")
    with conn:
        conn.execute(
            "INSERT INTO rating (child_id, attempt_id, task_id, day, rating, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (attempt_id) DO UPDATE SET rating = excluded.rating, "
            "  comment = excluded.comment, created_at = excluded.created_at",
            (child_id, attempt_id, row["task_id"], row["day"], rating, comment, at),
        )


@dataclass(frozen=True)
class PilotDay:
    day: str
    offered: int      # назначено заданий
    started: int      # начато попыток
    finished: int     # заработано наград
    hints: int        # открыто подсказок в миссиях
    ratings: dict     # {"boring": n, "ok": n, "fun": n}
    manual: int = 0   # минут добавлено родителем руками, по данным исполнителя


@dataclass(frozen=True)
class PilotItem:
    day: str
    title: str
    skill: str
    finished: bool
    hints: int
    rating: str       # "" если не оценил
    comment: str = ""


def pilot_summary(conn: sqlite3.Connection, child_id: int, *, since: str) -> tuple[list[PilotDay], list[PilotItem]]:
    """
    Что предложено, начато, закончено, сколько подсказок и какие оценки — по
    дням с даты since. Ровно тот минимум, который методика просит собирать
    (§10), без времени решения как штрафа и без «процента знаний».
    """
    days: dict[str, dict] = {}

    def bucket(day: str) -> dict:
        return days.setdefault(day, {"offered": 0, "started": 0, "finished": 0, "hints": 0,
                                     "ratings": {r: 0 for r in RATINGS}})

    for r in conn.execute("SELECT day, COUNT(*) n FROM assignment WHERE child_id = ? AND day >= ? GROUP BY day",
                          (child_id, since)):
        bucket(r["day"])["offered"] = r["n"]
    for r in conn.execute(
        "SELECT a.day, COUNT(*) n FROM attempt at JOIN assignment a ON a.id = at.assignment_id "
        " WHERE a.child_id = ? AND a.day >= ? GROUP BY a.day", (child_id, since)):
        bucket(r["day"])["started"] = r["n"]
    for r in conn.execute("SELECT day, COUNT(*) n FROM reward WHERE child_id = ? AND day >= ? GROUP BY day",
                          (child_id, since)):
        bucket(r["day"])["finished"] = r["n"]
    for r in conn.execute(
        "SELECT a.day, at.hints_used FROM attempt at JOIN assignment a ON a.id = at.assignment_id "
        " WHERE a.child_id = ? AND a.day >= ?", (child_id, since)):
        opened = json.loads(r["hints_used"] or "{}")
        bucket(r["day"])["hints"] += sum(int(v) for v in opened.values())
    for r in conn.execute("SELECT day, rating, COUNT(*) n FROM rating WHERE child_id = ? AND day >= ? GROUP BY day, rating",
                          (child_id, since)):
        bucket(r["day"])["ratings"][r["rating"]] = r["n"]
    # Ручная надбавка — то, что родитель поставил в Family Link мимо сайта.
    # Исполнитель замечает её и запоминает в строке выдачи дня.
    for r in conn.execute("SELECT day, manual_minutes FROM delivery WHERE child_id = ? AND day >= ?",
                          (child_id, since)):
        bucket(r["day"])["manual"] = int(r["manual_minutes"] or 0)

    summary = [PilotDay(day=d, **days[d]) for d in sorted(days, reverse=True)]

    items = []
    for r in conn.execute(
        "SELECT a.day, t.title, t.skill, at.hints_used, at.id AS attempt_id, "
        "       (SELECT 1 FROM reward rw WHERE rw.child_id = a.child_id AND rw.task_id = a.task_id AND rw.day = a.day) AS done, "
        "       (SELECT rating FROM rating rt WHERE rt.attempt_id = at.id) AS rating, "
        "       (SELECT comment FROM rating rt WHERE rt.attempt_id = at.id) AS comment "
        "  FROM attempt at JOIN assignment a ON a.id = at.assignment_id JOIN task t ON t.id = a.task_id "
        " WHERE a.child_id = ? AND a.day >= ? ORDER BY a.day DESC, at.id DESC", (child_id, since)):
        opened = json.loads(r["hints_used"] or "{}")
        items.append(PilotItem(day=r["day"], title=r["title"], skill=r["skill"] or "",
                               finished=bool(r["done"]), hints=sum(int(v) for v in opened.values()),
                               rating=RATING_LABELS.get(r["rating"] or "", ""),
                               comment=r["comment"] or ""))
    return summary, items


# --------------------------------------------------------------------------
# Утренний чек-лист
# --------------------------------------------------------------------------

SETTING_CHECKLIST = "checklist_items"      # пункты через перевод строки
CHECKLIST_DEFAULT = ("Почистил зубы", "Заправил кровать")


def checklist_items(conn: sqlite3.Connection) -> list[str]:
    raw = bank.get_setting(conn, SETTING_CHECKLIST)
    items = [line.strip() for line in raw.splitlines() if line.strip()]
    return items or list(CHECKLIST_DEFAULT)


def checklist_state(conn: sqlite3.Connection, child_id: int, day: str) -> dict | None:
    """Что отмечено сегодня, или None — чек-лист ещё не проходили."""
    row = conn.execute("SELECT items FROM checklist WHERE child_id = ? AND day = ?",
                       (child_id, day)).fetchone()
    return json.loads(row["items"]) if row else None


def complete_checklist(conn: sqlite3.Connection, *, child_id: int, day: str,
                       checked: list[str], at: str) -> dict:
    """
    Записывает утренний чек-лист. Неотмеченные пункты тоже сохраняются —
    как false: родителю важно видеть, что пропущено, а не только что сделано.
    """
    items = {name: (name in checked) for name in checklist_items(conn)}
    with conn:
        conn.execute(
            "INSERT INTO checklist (child_id, day, items, done_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (child_id, day) DO UPDATE SET items = excluded.items, done_at = excluded.done_at",
            (child_id, day, json.dumps(items, ensure_ascii=False), at),
        )
    return items


def checklist_history(conn: sqlite3.Connection, child_id: int, *, since: str) -> list[dict]:
    return [
        {"day": r["day"], "at": r["done_at"], "items": json.loads(r["items"])}
        for r in conn.execute("SELECT day, done_at, items FROM checklist "
                              " WHERE child_id = ? AND day >= ? ORDER BY day DESC", (child_id, since))
    ]


# --------------------------------------------------------------------------
# Календарь: дата словами и правила домашки по дням недели
# --------------------------------------------------------------------------

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


def human_date(day: str) -> str:
    """'2026-09-11' -> 'Пятница, 11 сентября'. Без локали: её в контейнере нет."""
    d = date.fromisoformat(day)
    return f"{WEEKDAYS[d.weekday()].capitalize()}, {d.day} {MONTHS[d.month - 1]}"


@dataclass(frozen=True)
class HomeworkPolicy:
    """
    Что можно ответить на вопрос про домашку в этот день недели.

    «Не задавали» — правда не каждый день. В будни с понедельника по четверг
    задают почти всегда, поэтому такой ответ принимается только со снимком
    дневника: пусть покажет страницу, где на сегодня пусто. В пятницу и
    субботу действительно могут не задать — верим на слово. В воскресенье
    делается домашка на понедельник, и варианта «не задавали» нет вовсе.
    """
    nothing_allowed: bool       # показывать ли ответ «не задавали»
    nothing_needs_photo: bool   # требовать ли для него снимок дневника
    for_monday: bool            # воскресенье: домашка на понедельник


def homework_policy(day: str) -> HomeworkPolicy:
    weekday = date.fromisoformat(day).weekday()
    if weekday <= 3:                     # пн–чт
        return HomeworkPolicy(nothing_allowed=True, nothing_needs_photo=True, for_monday=False)
    if weekday <= 5:                     # пт, сб
        return HomeworkPolicy(nothing_allowed=True, nothing_needs_photo=False, for_monday=False)
    return HomeworkPolicy(nothing_allowed=False, nothing_needs_photo=False, for_monday=True)


# --------------------------------------------------------------------------
# День
# --------------------------------------------------------------------------


VPR = "vpr"
MISSIONS = "missions"

# Сколько заданий каждого раздела назначается на день.
#
# Не весь каталог. По методике длинный список превращается в перечень
# обязательств и позволяет выбирать привычное, обходя незнакомое. Плюс
# арифметика: пять заданий — это 50 минут, и вместе с базой и домашкой (15 +
# 45) они ровно влезают в максимум 120. Показывать двадцать значило бы, что
# большинство из них уже ничего не стоят — а ребёнок этого не видит.
PER_DAY = {
    MISSIONS: 2,   # ежедневная практика способа
    VPR: 3,        # подготовка к работе; за шесть дней обойдёт все 18
}


def _assign_section(conn: sqlite3.Connection, child_id: int, day: str,
                    section: str, limit: int) -> None:
    """
    Назначает на день не больше limit заданий раздела.

    Выбор детерминированный: он не меняется при обновлении страницы, но
    сдвигается каждый день. Случайность здесь была бы хуже — сын видел бы
    разное на двух вкладках, а часть каталога могла бы не попасться месяцами.
    """
    already = conn.execute(
        "SELECT COUNT(*) AS c FROM assignment a JOIN task t ON t.id = a.task_id "
        " WHERE a.child_id = ? AND a.day = ? AND t.section = ?",
        (child_id, day, section),
    ).fetchone()["c"]
    if already >= limit:
        return

    rows = conn.execute(
        "SELECT id, skill FROM task WHERE active = 1 AND section = ? ORDER BY id",
        (section,),
    ).fetchall()
    if not rows:
        return

    # Ротация идёт по НАВЫКАМ, а не по заданиям. У навыка может быть несколько
    # заданий-вариантов с разными условиями; в день назначается навык, а из
    # его вариантов берётся тот, что ребёнок видел давнее всего. Так повтор
    # навыка через несколько дней — это новое условие, а не то же самое
    # задание с уже известными ответами. Задание без навыка — само себе навык.
    groups: dict[str, list[int]] = {}
    for r in rows:
        groups.setdefault(r["skill"] or f"#{r['id']}", []).append(r["id"])
    skills = list(groups)

    # Окно сдвигается на ЦЕЛЫЙ день навыков: соседние дни не пересекаются,
    # каталог обходится за ceil(размер / limit) дней и идёт по кругу.
    shift = (date.fromisoformat(day).toordinal() * limit) % len(skills)
    chosen_skills = [skills[(shift + i) % len(skills)] for i in range(min(limit, len(skills)))]

    last_seen = {r["task_id"]: r["last"] for r in conn.execute(
        "SELECT task_id, MAX(day) AS last FROM assignment WHERE child_id = ? GROUP BY task_id",
        (child_id,),
    ).fetchall()}
    chosen = []
    for skill in chosen_skills:
        variants = groups[skill]
        # Никогда не виденное — первым; иначе самое давнее. При равенстве — по id.
        chosen.append(min(variants, key=lambda t: (last_seen.get(t, ""), t)))

    with conn:
        for task_id in chosen:
            conn.execute(
                "INSERT INTO assignment (child_id, task_id, day) VALUES (?, ?, ?) "
                "ON CONFLICT (child_id, task_id, day) DO NOTHING",
                (child_id, task_id, day),
            )


def ensure_day(conn: sqlite3.Connection, child_id: int, day: str) -> None:
    """
    Собирает день: по нескольку заданий из каждого раздела.

    Разделы живут отдельно сознательно. Миссии — ежедневная практика способа,
    разборы ВПР — подготовка к конкретной работе. Смешать их в один список
    значило бы позволить проходить только знакомое.

    Разделы, которых нет в PER_DAY, назначаются целиком: это запас на случай
    появления нового раздела, о котором эта функция ещё не знает.
    """
    sections = [r["section"] for r in conn.execute(
        "SELECT DISTINCT section FROM task WHERE active = 1"
    ).fetchall()]

    for section in sections:
        limit = PER_DAY.get(section)
        if limit is None:
            with conn:
                conn.execute(
                    "INSERT INTO assignment (child_id, task_id, day) "
                    "SELECT ?, id, ? FROM task WHERE active = 1 AND section = ? "
                    "ON CONFLICT (child_id, task_id, day) DO NOTHING",
                    (child_id, day, section),
                )
        else:
            _assign_section(conn, child_id, day, section, limit)


@dataclass(frozen=True)
class DayTask:
    assignment_id: int
    task_id: int
    kind: str
    title: str
    reward_minutes: int
    state: str
    attempt_id: int | None
    video_file: str = ""
    section: str = "vpr"
    cards_total: int = 0
    feedback: str = ""      # что сказать по завершении миссии, из методики


@dataclass(frozen=True)
class DayView:
    day: str
    balance: bank.Balance
    items: list[DayTask]

    def by_section(self, section: str) -> list[DayTask]:
        return [i for i in self.items if i.section == section]

    @property
    def available_minutes(self) -> int:
        """Сколько ещё можно заработать сегодня, с учётом дневного максимума."""
        b = self.balance
        room = b.daily_max - (b.base + b.homework_bonus + b.earned)
        pending = sum(i.reward_minutes for i in self.items if i.state != EARNED)
        return max(0, min(room, pending))


def day_view(conn: sqlite3.Connection, child_id: int, day: str) -> DayView:
    ensure_day(conn, child_id, day)

    rows = conn.execute(
        """
        SELECT a.id AS assignment_id, t.id AS task_id, t.kind, t.title,
               t.reward_minutes, t.version, t.video_file, t.section, t.payload,
               (SELECT r.id FROM reward r
                 WHERE r.child_id = a.child_id AND r.task_id = t.id
                   AND r.task_version = t.version AND r.day = a.day) AS reward_id,
               (SELECT at.id FROM attempt at
                 WHERE at.assignment_id = a.id AND at.finished_at IS NULL
                 ORDER BY at.id DESC LIMIT 1) AS open_attempt
          FROM assignment a
          JOIN task t ON t.id = a.task_id
         WHERE a.child_id = ? AND a.day = ? AND t.active = 1
         ORDER BY t.id
        """,
        (child_id, day),
    ).fetchall()

    items = []
    for r in rows:
        if r["reward_id"] is not None:
            state = EARNED
        elif r["open_attempt"] is not None:
            state = IN_PROGRESS
        else:
            state = AVAILABLE
        items.append(DayTask(
            assignment_id=r["assignment_id"], task_id=r["task_id"], kind=r["kind"],
            title=r["title"], reward_minutes=r["reward_minutes"], state=state,
            attempt_id=r["open_attempt"], video_file=r["video_file"] or "",
            section=r["section"] or VPR,
            cards_total=len(tasks.mission_cards(json.loads(r["payload"] or "{}")))
            if r["kind"] == tasks.MISSION else 0,
            feedback=json.loads(r["payload"] or "{}").get("feedback", "")
            if r["kind"] == tasks.MISSION else "",
        ))

    return DayView(day=day, balance=bank.balance(conn, child_id, day), items=items)


# --------------------------------------------------------------------------
# Попытка
# --------------------------------------------------------------------------


def start_attempt(conn: sqlite3.Connection, *, child_id: int, assignment_id: int, at: str) -> int:
    """
    Начинает попытку: генерирует вопросы и сохраняет их вместе с ответами.

    Повторная попытка получает новые вопросы — иначе неудача превращается в
    подбор, а не в повторение материала.
    """
    row = conn.execute(
        "SELECT a.id, a.day, t.id AS task_id, t.kind, t.payload, t.version "
        "  FROM assignment a JOIN task t ON t.id = a.task_id "
        " WHERE a.id = ? AND a.child_id = ?",
        (assignment_id, child_id),
    ).fetchone()
    if row is None:
        raise ServiceError("Задание не найдено")

    already = conn.execute(
        "SELECT 1 FROM reward WHERE child_id = ? AND task_id = ? AND task_version = ? AND day = ?",
        (child_id, row["task_id"], row["version"], row["day"]),
    ).fetchone()
    if already:
        raise ServiceError("За это задание сегодня уже начислено время")

    payload = json.loads(row["payload"] or "{}")

    if row["kind"] == tasks.MISSION:
        # Для миссии сохраняем структуру карточек целиком: попытка должна
        # оставаться проходимой, даже если каталог потом поправят.
        cards = tasks.mission_cards(payload)
        if not cards:
            raise ServiceError("У этой миссии не получилось ни одной карточки")
        stored = {"cards": [
            {"title": c.title, "intro": c.intro, "hints": c.hints,
             "pass_ratio": c.pass_ratio, "questions": tasks.dump(c.questions)}
            for c in cards
        ]}
    else:
        questions = tasks.generate(row["kind"], payload, seed=secrets.randbelow(2 ** 31))
        if not questions:
            raise ServiceError("Для этого задания не удалось составить вопросы")
        stored = tasks.dump(questions)

    with conn:
        # Незакрытые попытки закрываем: открытая должна быть одна.
        conn.execute(
            "UPDATE attempt SET finished_at = ?, passed = 0 "
            " WHERE assignment_id = ? AND finished_at IS NULL",
            (at, assignment_id),
        )
        cur = conn.execute(
            "INSERT INTO attempt (assignment_id, started_at, questions) VALUES (?, ?, ?)",
            (assignment_id, at, json.dumps(stored, ensure_ascii=False)),
        )
    return int(cur.lastrowid)


def _attempt_row(conn: sqlite3.Connection, *, child_id: int, attempt_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT at.*, a.child_id, a.day, t.id AS task_id, t.kind, t.title, t.section, "
        "       t.version, t.reward_minutes, t.payload "
        "  FROM attempt at "
        "  JOIN assignment a ON a.id = at.assignment_id "
        "  JOIN task t ON t.id = a.task_id "
        " WHERE at.id = ? AND a.child_id = ?",
        (attempt_id, child_id),
    ).fetchone()
    if row is None:
        raise ServiceError("Попытка не найдена")
    return row


def attempt_questions(conn: sqlite3.Connection, *, child_id: int, attempt_id: int) -> list[dict]:
    """Вопросы попытки в том виде, в каком их можно отдать браузеру."""
    row = _attempt_row(conn, child_id=child_id, attempt_id=attempt_id)
    questions = tasks.load(json.loads(row["questions"]))
    return [q.for_child(i) for i, q in enumerate(questions, start=1)]


@dataclass(frozen=True)
class SubmitResult:
    judgement: tasks.Judgement
    granted: bool          # начислено именно этой отправкой
    minutes: int
    balance: bank.Balance


def submit(conn: sqlite3.Connection, *, child_id: int, attempt_id: int,
           answers: dict, at: str) -> SubmitResult:
    """
    Принимает ответы, оценивает и при успехе начисляет награду.

    Награда берётся из каталога, а не из запроса. Повторная отправка той же
    попытки не создаёт вторую награду: за это отвечает ключ идемпотентности в
    банке, а не проверка здесь.
    """
    row = _attempt_row(conn, child_id=child_id, attempt_id=attempt_id)
    questions = tasks.load(json.loads(row["questions"]))
    payload = json.loads(row["payload"] or "{}")
    ratio = float(payload.get("pass_ratio", tasks.DEFAULT_PASS_RATIO))

    judgement = tasks.judge(questions, answers, pass_ratio=ratio)

    with conn:
        conn.execute(
            "UPDATE attempt SET finished_at = ?, answers = ?, correct = ?, total = ?, passed = ? "
            " WHERE id = ?",
            (at, json.dumps(answers, ensure_ascii=False), judgement.correct,
             judgement.total, int(judgement.passed), attempt_id),
        )

    if not judgement.passed:
        return SubmitResult(judgement=judgement, granted=False, minutes=0,
                            balance=bank.balance(conn, child_id, row["day"]))

    result = bank.grant(
        conn,
        child_id=child_id,
        task_id=row["task_id"],
        task_version=row["version"],
        day=row["day"],
        minutes=row["reward_minutes"],
        attempt_id=attempt_id,
        at=at,
    )
    return SubmitResult(judgement=judgement, granted=result.granted,
                        minutes=result.minutes, balance=result.balance)


# --------------------------------------------------------------------------
# Миссии: карточки по одной
# --------------------------------------------------------------------------
#
# Отдельный путь, а не расширение обычной попытки. Причина в правиле методики:
# карточки нельзя показывать вместе, потому что вторая объясняет способ решения
# первой, а третья проверяет перенос. Значит попытка живёт во времени и имеет
# состояние — номер карточки и число открытых подсказок.
#
# Награда даётся за миссию целиком. За отдельную карточку минуты не начисляются:
# иначе выгодно проходить только первую.


def _cards_of(row: sqlite3.Row) -> list[tasks.Card]:
    """Карточки, зафиксированные при старте попытки."""
    stored = json.loads(row["questions"])
    return tasks.mission_cards(stored if isinstance(stored, dict) else {"cards": stored})


@dataclass(frozen=True)
class MissionState:
    attempt_id: int
    stage: int                 # номер текущей карточки, с нуля
    total: int                 # сколько карточек всего
    card: dict                 # текущая карточка в виде для браузера
    previous: tuple            # пройденные карточки: заголовок, условие, вопросы —
                               # чтобы перечитать, если следующая на них ссылается
    finished: bool
    reward_minutes: int
    title: str


def mission_state(conn: sqlite3.Connection, *, child_id: int, attempt_id: int) -> MissionState:
    row = _attempt_row(conn, child_id=child_id, attempt_id=attempt_id)
    cards = _cards_of(row)
    stage = int(row["stage"] or 0)
    opened = json.loads(row["hints_used"] or "{}")

    finished = stage >= len(cards)
    index = min(stage, len(cards) - 1) if cards else 0
    card = cards[index].for_child(index + 1, int(opened.get(str(index), 0))) if cards else {}
    previous = tuple(
        {"n": i + 1, "title": c.title, "intro": c.intro,
         "questions": [q.for_child(k) for k, q in enumerate(c.questions, start=1)]}
        for i, c in enumerate(cards[:index])
    )

    return MissionState(
        attempt_id=attempt_id, stage=stage, total=len(cards), card=card,
        previous=previous, finished=finished, reward_minutes=row["reward_minutes"], title=row["title"],
    )


def reveal_hint(conn: sqlite3.Connection, *, child_id: int, attempt_id: int) -> MissionState:
    """
    Открывает следующую подсказку текущей карточки.

    Подсказки бесплатны и не влияют на награду: по методике их можно попросить
    сразу. Смысл платы за помощь был бы в том, чтобы ребёнок не просил её —
    а нам нужно обратное.
    """
    row = _attempt_row(conn, child_id=child_id, attempt_id=attempt_id)
    cards = _cards_of(row)
    stage = int(row["stage"] or 0)
    if stage >= len(cards):
        return mission_state(conn, child_id=child_id, attempt_id=attempt_id)

    opened = json.loads(row["hints_used"] or "{}")
    key = str(stage)
    opened[key] = min(int(opened.get(key, 0)) + 1, len(cards[stage].hints))

    with conn:
        conn.execute("UPDATE attempt SET hints_used = ? WHERE id = ?",
                     (json.dumps(opened, ensure_ascii=False), attempt_id))
    return mission_state(conn, child_id=child_id, attempt_id=attempt_id)


@dataclass(frozen=True)
class CardResult:
    judgement: tasks.Judgement
    advanced: bool             # карточка засчитана, перешли к следующей
    mission_done: bool
    granted: bool
    minutes: int
    balance: bank.Balance


def submit_card(conn: sqlite3.Connection, *, child_id: int, attempt_id: int,
                answers: dict, at: str) -> CardResult:
    """
    Принимает ответы на текущую карточку.

    Не сошлось — остаёмся на той же карточке: можно открыть подсказку и
    попробовать снова. Сошлось — переходим к следующей, а на последней
    начисляем награду за миссию.
    """
    row = _attempt_row(conn, child_id=child_id, attempt_id=attempt_id)
    cards = _cards_of(row)
    stage = int(row["stage"] or 0)

    if not cards:
        raise ServiceError("У миссии нет карточек")
    if stage >= len(cards):
        raise ServiceError("Миссия уже завершена")

    card = cards[stage]
    judgement = tasks.judge(card.questions, answers, pass_ratio=card.pass_ratio)

    if not judgement.passed:
        return CardResult(judgement=judgement, advanced=False, mission_done=False,
                          granted=False, minutes=0,
                          balance=bank.balance(conn, child_id, row["day"]))

    next_stage = stage + 1
    done = next_stage >= len(cards)

    with conn:
        conn.execute(
            "UPDATE attempt SET stage = ?, finished_at = ?, passed = ?, "
            "       correct = ?, total = ? WHERE id = ?",
            (next_stage, at if done else None, 1 if done else None,
             judgement.correct, judgement.total, attempt_id),
        )

    if not done:
        return CardResult(judgement=judgement, advanced=True, mission_done=False,
                          granted=False, minutes=0,
                          balance=bank.balance(conn, child_id, row["day"]))

    result = bank.grant(
        conn,
        child_id=child_id,
        task_id=row["task_id"],
        task_version=row["version"],
        day=row["day"],
        minutes=row["reward_minutes"],
        attempt_id=attempt_id,
        at=at,
    )
    return CardResult(judgement=judgement, advanced=True, mission_done=True,
                      granted=result.granted, minutes=result.minutes,
                      balance=result.balance)
