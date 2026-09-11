# -*- coding: utf-8 -*-
"""
Банк времени: правила начисления и расчёт целевой квоты.

Здесь живёт вся арифметика заработанного времени. Сознательно без веба, без
Home Assistant и без ввода-вывода наружу — только база и чистые функции.
Это самое дорогое место в проекте: ошибка здесь означает либо украденные у
ребёнка минуты, либо бесконечный источник времени.

Три правила, которые нельзя нарушать:

1. Квота на сегодня = min(дневной максимум, базовая + сумма подтверждённых
   наград за сегодня). Абсолютное значение, а не прибавка: в Family Link
   надбавка заменяет собой значение дня, поэтому операции «добавить» не
   существует.

2. Одно задание — одна награда за период. Ключ: ребёнок + задание + версия
   задания + день. Обеспечивается UNIQUE в базе, а не проверкой в коде: две
   вкладки могут проверить одновременно и обе увидеть «награды ещё нет».

3. Баланс всегда пересчитывается из журнала. В памяти процесса не хранится
   ничего: перезапуск сервера обязан давать ту же сумму.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

SETTING_BASE = "base_minutes"
SETTING_DAILY_MAX = "daily_max_minutes"
SETTING_REQUIRE_HOMEWORK = "require_homework"
SETTING_HOMEWORK_MINUTES = "homework_minutes"
SETTING_PARENT_PIN = "parent_pin_hash"   # пароль родительской страницы, pbkdf2

DEFAULTS = {
    SETTING_BASE: "15",
    # 15 базовых + 45 за домашку + 50 за задания дня = 110: всё предложенное
    # на день влезает в квоту, экран не обещает несуществующих минут.
    SETTING_DAILY_MAX: "120",
    # Домашняя работа — основа: по умолчанию ворота включены. Выключается на
    # каникулах, когда запирать день нечем.
    SETTING_REQUIRE_HOMEWORK: "1",
    # Сделанная домашка сама стоит минут — сразу, без заданий на сайте.
    # Решение владельца: тетрадь важнее сайта, и это должно быть видно в цифре.
    # «Не задавали» ворота открывает, но минут не даёт: не за что.
    SETTING_HOMEWORK_MINUTES: "45",
}

FALSE_VALUES = ("0", "false", "no", "off", "")


# --------------------------------------------------------------------------
# Сутки
# --------------------------------------------------------------------------


def now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def today(tz: str) -> str:
    """
    Сегодняшняя дата в часовом поясе семьи, строкой YYYY-MM-DD.

    Часовой пояс именно семьи, а не сервера и не UTC: «новый день» должен
    наступать тогда же, когда его видит Family Link на планшете.
    """
    return now(tz).date().isoformat()


# --------------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------------


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    if row is not None:
        return row["value"]
    return DEFAULTS.get(key, "")


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO setting (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def base_minutes(conn: sqlite3.Connection) -> int:
    return int(get_setting(conn, SETTING_BASE))


def daily_max_minutes(conn: sqlite3.Connection) -> int:
    return int(get_setting(conn, SETTING_DAILY_MAX))


def homework_required(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, SETTING_REQUIRE_HOMEWORK).strip().lower() not in FALSE_VALUES


def homework_minutes(conn: sqlite3.Connection) -> int:
    return int(get_setting(conn, SETTING_HOMEWORK_MINUTES))


# --------------------------------------------------------------------------
# Правило банка
# --------------------------------------------------------------------------


def quota_for(base: int, earned: int, daily_max: int, gate_open: bool = True,
              homework_bonus: int = 0) -> int:
    """
    Целевая квота на сегодня.

    Базовая квота входит в сумму, а не добавляется поверх максимума: дневной
    максимум ограничивает всё доступное время, включая базовое.

    Домашняя работа — и ворота, и награда. Пока ворота закрыты, заработанное
    на сайте в квоту не входит: базовые минуты есть всегда, остальное ждёт.
    Награды при этом уже лежат в журнале — ребёнок не обязан делать тетрадь
    раньше сайта, порядок его дело. Сделанная домашка сверх того добавляет
    свои минуты (homework_bonus) — они считаются из отметки, а не из журнала
    наград, и исчезают вместе с отозванной отметкой.
    """
    released = earned if gate_open else 0
    return max(0, min(daily_max, base + homework_bonus + released))


def homework_state(conn: sqlite3.Connection, child_id: int, day: str) -> str | None:
    """Непогашенная отметка за день: 'done', 'nothing_assigned' или None."""
    row = conn.execute(
        "SELECT marked_as FROM homework "
        " WHERE child_id = ? AND day = ? AND revoked_at IS NULL",
        (child_id, day),
    ).fetchone()
    return row["marked_as"] if row is not None else None


def homework_marked(conn: sqlite3.Connection, child_id: int, day: str) -> bool:
    """Есть ли за этот день непогашенная отметка о домашней работе."""
    return homework_state(conn, child_id, day) is not None


def earned_today(conn: sqlite3.Connection, child_id: int, day: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(minutes), 0) AS total FROM reward "
        "WHERE child_id = ? AND day = ?",
        (child_id, day),
    ).fetchone()
    return int(row["total"])


@dataclass(frozen=True)
class Balance:
    day: str
    base: int
    earned: int          # начислено в журнал за сегодня
    daily_max: int
    target: int              # что уйдёт в Family Link
    homework_required: bool
    homework_marked: bool
    homework_bonus: int = 0    # минуты за сделанную домашку, из отметки
    homework_minutes: int = 0  # сколько домашка стоит по настройке (для экрана)

    @property
    def gate_open(self) -> bool:
        return (not self.homework_required) or self.homework_marked

    @property
    def locked(self) -> int:
        """
        Заработано, но пока не выдано из-за незакрытой домашки.

        Сайт обязан показывать это отдельным числом: «заработано 30, откроются
        после ДЗ» честнее, чем молча показывать ноль.
        """
        return 0 if self.gate_open else self.earned

    @property
    def capped(self) -> bool:
        """Упёрлись ли в дневной максимум — сайту это надо показать честно."""
        released = self.earned if self.gate_open else 0
        return self.base + self.homework_bonus + released > self.daily_max


def balance(conn: sqlite3.Connection, child_id: int, day: str) -> Balance:
    base = base_minutes(conn)
    cap = daily_max_minutes(conn)
    earned = earned_today(conn, child_id, day)
    required = homework_required(conn)
    state = homework_state(conn, child_id, day)
    marked = state is not None
    gate = (not required) or marked
    worth = homework_minutes(conn)
    bonus = worth if state == "done" else 0
    return Balance(day=day, base=base, earned=earned, daily_max=cap,
                   target=quota_for(base, earned, cap, gate, bonus),
                   homework_required=required, homework_marked=marked,
                   homework_bonus=bonus, homework_minutes=worth)


# --------------------------------------------------------------------------
# Начисление
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GrantResult:
    granted: bool          # False = награда за это задание сегодня уже была
    minutes: int           # сколько начислено этим вызовом (0, если повтор)
    balance: Balance


def grant(
    conn: sqlite3.Connection,
    *,
    child_id: int,
    task_id: int,
    task_version: int,
    day: str,
    minutes: int,
    attempt_id: int | None,
    at: str,
) -> GrantResult:
    """
    Начисляет награду и обновляет цель выдачи. Идемпотентно.

    Повторный вызов с тем же ключом не создаёт вторую награду и не меняет
    сумму — возвращает granted=False и текущий баланс. Именно так гасятся две
    вкладки, двойная отправка формы и повтор после перезапуска.
    """
    if minutes < 0:
        raise ValueError("Награда не может быть отрицательной")

    with conn:  # одна транзакция на начисление и обновление цели
        cur = conn.execute(
            "INSERT INTO reward "
            "  (child_id, task_id, task_version, day, minutes, attempt_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (child_id, task_id, task_version, day) DO NOTHING",
            (child_id, task_id, task_version, day, minutes, attempt_id, at),
        )
        granted = cur.rowcount == 1

        bal = balance(conn, child_id, day)
        _set_target(conn, child_id=child_id, day=day, target=bal.target, at=at)

    return GrantResult(granted=granted, minutes=minutes if granted else 0, balance=bal)


def _set_target(conn: sqlite3.Connection, *, child_id: int, day: str, target: int, at: str) -> None:
    """
    Записывает желаемую квоту. Если цель не изменилась — ничего не трогаем,
    чтобы подтверждённая выдача не сбрасывалась в pending на ровном месте.
    """
    conn.execute(
        "INSERT INTO delivery (child_id, day, target_minutes, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?) "
        "ON CONFLICT (child_id, day) DO UPDATE SET "
        "  target_minutes = excluded.target_minutes, "
        "  status = CASE WHEN delivery.target_minutes = excluded.target_minutes "
        "                THEN delivery.status ELSE 'pending' END, "
        "  tries  = CASE WHEN delivery.target_minutes = excluded.target_minutes "
        "                THEN delivery.tries ELSE 0 END, "
        "  updated_at = excluded.updated_at",
        (child_id, day, target, at, at),
    )


def mark_homework(conn: sqlite3.Connection, *, child_id: int, day: str, at: str,
                  marked_as: str = "done", marked_by: str = "child",
                  photo_file: str = "") -> Balance:
    """
    Открывает ворота: заработанное за день попадает в квоту. Отметка «сделано»
    сверх того даёт свои минуты (настройка homework_minutes).

    photo_file — снимок страницы тетради. Банк его не проверяет и не требует:
    требование «без фото не считается» живёт на уровне сайта, а здесь только
    хранится имя файла, чтобы родитель потом посмотрел.

    Повторная отметка ничего не ломает — ключ (ребёнок, день) один. Ранее
    отозванную отметку эта же функция возвращает к жизни: родитель отозвал,
    разобрались, отметили снова.
    """
    if marked_as not in ("done", "nothing_assigned"):
        raise ValueError(f"Непонятная отметка: {marked_as}")

    with conn:
        conn.execute(
            "INSERT INTO homework (child_id, day, marked_at, marked_as, marked_by, photo_file) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (child_id, day) DO UPDATE SET "
            "  marked_at = excluded.marked_at, "
            "  marked_as = excluded.marked_as, "
            "  marked_by = excluded.marked_by, "
            "  photo_file = excluded.photo_file, "
            "  revoked_at = NULL, revoked_reason = NULL",
            (child_id, day, at, marked_as, marked_by, photo_file),
        )
        bal = balance(conn, child_id, day)
        _set_target(conn, child_id=child_id, day=day, target=bal.target, at=at)
    return bal


def revoke_homework(conn: sqlite3.Connection, *, child_id: int, day: str, at: str,
                    reason: str = "") -> Balance:
    """
    Гасит отметку: заработанное снова запирается.

    Нужно родителю, если отметка оказалась неправдой. Награды из журнала при
    этом не стираются — они честно заработаны на сайте, просто время за них
    не выдаётся, пока тетрадь не сделана.
    """
    with conn:
        conn.execute(
            "UPDATE homework SET revoked_at = ?, revoked_reason = ? "
            " WHERE child_id = ? AND day = ? AND revoked_at IS NULL",
            (at, reason, child_id, day),
        )
        bal = balance(conn, child_id, day)
        _set_target(conn, child_id=child_id, day=day, target=bal.target, at=at)
    return bal


def sync_target(conn: sqlite3.Connection, *, child_id: int, day: str, at: str) -> Balance:
    """
    Пересчитывает цель из журнала, ничего не начисляя.

    Нужен на старте процесса и при смене настроек: дневной максимум мог
    поменяться, и цель обязана следовать за журналом, а не за памятью.
    """
    with conn:
        bal = balance(conn, child_id, day)
        _set_target(conn, child_id=child_id, day=day, target=bal.target, at=at)
    return bal
