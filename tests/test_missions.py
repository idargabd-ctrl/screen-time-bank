# -*- coding: utf-8 -*-
"""
Тесты миссий.

Главное требование методики, которое движок раньше не умел: карточки нельзя
показывать вместе. Вторая объясняет способ решения первой, третья проверяет
перенос — выложи их на одну страницу, и проверка превратится в списывание с
соседнего абзаца. Здесь это и проверяется, вместе с бесплатностью подсказок и
начислением за миссию целиком, а не за карточку.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import bank, db, service, tasks  # noqa: E402

AT = "2026-09-11T10:00:00+03:00"
DAY = "2026-09-11"

MISSION = {
    "cards": [
        {
            "title": "Вызов",
            "intro": "Набор А: 6 фонарей за 18 монет. Набор Б: 4 за 13. Нужно ровно 12.",
            "hints": ["Сравни одинаковое количество", "Сколько наборов дадут 12?"],
            "questions": [
                {"prompt": "Какой набор выгоднее? А или Б", "answer": "А", "hint": "Считай на 12 фонарей"},
                {"prompt": "Сколько монет?", "answer": "36", "hint": "Два набора А"},
            ],
        },
        {
            "title": "Ошибка персонажа",
            "intro": "Бот советует: «Бери Б, 13 меньше 18».",
            "hints": ["Что сравнил бот?"],
            "questions": [
                {"prompt": "Что не учёл бот? А, Б или В", "answer": "А", "hint": "Посмотри на количество"},
            ],
        },
        {
            "title": "Перенос",
            "intro": "Нужно ровно 24 значка. Набор К: 8 за 24 руб. Набор Л: 6 за 20 руб.",
            "hints": ["Посчитай стоимость 24 значков в каждом варианте"],
            "questions": [
                {"prompt": "Какой набор дешевле? К или Л", "answer": "К", "hint": "Три набора К"},
                {"prompt": "На сколько рублей?", "answer": "8", "hint": "72 и 80"},
            ],
        },
    ]
}


@pytest.fixture()
def conn(tmp_path):
    c = db.open_db(tmp_path / "m.sqlite3")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'сын','C','D')")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (2,'другой','C2','D2')")
    c.execute(
        "INSERT INTO task (id, slug, kind, version, title, section, payload, reward_minutes) "
        "VALUES (1, 'm1', 'mission', 1, 'Реклама набора', 'missions', ?, 10)",
        (json.dumps(MISSION, ensure_ascii=False),),
    )
    bank.set_setting(c, bank.SETTING_HOMEWORK_MINUTES, "0")
    bank.mark_homework(c, child_id=1, day=DAY, at=AT)
    yield c
    c.close()


def start(conn, child_id=1):
    view = service.day_view(conn, child_id, DAY)
    item = next(i for i in view.items if i.section == service.MISSIONS)
    return service.start_attempt(conn, child_id=child_id,
                                 assignment_id=item.assignment_id, at=AT)


def answers_for(conn, attempt_id, wrong=False):
    """Правильные ответы текущей карточки — так же, как их видит сервер."""
    row = conn.execute("SELECT questions, stage FROM attempt WHERE id = ?", (attempt_id,)).fetchone()
    cards = tasks.mission_cards(json.loads(row["questions"]))
    card = cards[row["stage"]]
    if wrong:
        return {str(i): "мимо" for i in range(1, len(card.questions) + 1)}
    return {str(i): q.answer for i, q in enumerate(card.questions, start=1)}


def pass_card(conn, attempt_id):
    return service.submit_card(conn, child_id=1, attempt_id=attempt_id,
                               answers=answers_for(conn, attempt_id), at=AT)


# --------------------------------------------------------------------------
# Карточки по одной
# --------------------------------------------------------------------------


def test_only_the_current_card_is_given_out(conn):
    attempt = start(conn)
    state = service.mission_state(conn, child_id=1, attempt_id=attempt)

    assert state.stage == 0
    assert state.total == 3
    assert state.card["title"] == "Вызов"
    assert len(state.card["questions"]) == 2


def test_later_cards_are_not_in_what_goes_to_the_browser(conn):
    """
    Самое важное: третья карточка не должна быть видна с первой.

    Иначе ребёнок читает условие переноса раньше, чем решил вызов, и
    проверка навыка превращается в чтение.
    """
    attempt = start(conn)
    state = service.mission_state(conn, child_id=1, attempt_id=attempt)
    sent = json.dumps(state.card, ensure_ascii=False)

    assert "значка" not in sent, "условие третьей карточки не должно уходить"
    assert "Бери Б" not in sent, "условие второй карточки не должно уходить"


def test_answers_never_go_to_the_browser(conn):
    attempt = start(conn)
    state = service.mission_state(conn, child_id=1, attempt_id=attempt)
    sent = json.dumps(state.card, ensure_ascii=False)

    assert "answer" not in sent
    assert '"36"' not in sent


def test_cards_advance_one_by_one(conn):
    attempt = start(conn)

    first = pass_card(conn, attempt)
    assert first.advanced is True and first.mission_done is False
    assert service.mission_state(conn, child_id=1, attempt_id=attempt).card["title"] == "Ошибка персонажа"

    second = pass_card(conn, attempt)
    assert second.mission_done is False
    assert service.mission_state(conn, child_id=1, attempt_id=attempt).card["title"] == "Перенос"


# --------------------------------------------------------------------------
# Награда за миссию целиком
# --------------------------------------------------------------------------


def test_no_minutes_for_a_single_card(conn):
    """Иначе выгодно проходить только первую карточку."""
    attempt = start(conn)
    result = pass_card(conn, attempt)

    assert result.granted is False
    assert result.balance.earned == 0


def test_finishing_the_mission_pays_once(conn):
    attempt = start(conn)
    pass_card(conn, attempt)
    pass_card(conn, attempt)
    last = pass_card(conn, attempt)

    assert last.mission_done is True
    assert last.granted is True
    assert last.minutes == 10
    assert last.balance.earned == 10
    assert last.balance.target == 25   # база 15 плюс 10


def test_a_finished_mission_cannot_be_submitted_again(conn):
    attempt = start(conn)
    for _ in range(3):
        pass_card(conn, attempt)

    with pytest.raises(service.ServiceError):
        service.submit_card(conn, child_id=1, attempt_id=attempt, answers={}, at=AT)


def test_second_attempt_after_success_is_refused(conn):
    attempt = start(conn)
    for _ in range(3):
        pass_card(conn, attempt)

    with pytest.raises(service.ServiceError):
        start(conn)


# --------------------------------------------------------------------------
# Неудача
# --------------------------------------------------------------------------


def test_a_wrong_card_does_not_advance(conn):
    attempt = start(conn)
    result = service.submit_card(conn, child_id=1, attempt_id=attempt,
                                 answers=answers_for(conn, attempt, wrong=True), at=AT)

    assert result.judgement.passed is False
    assert result.advanced is False
    assert service.mission_state(conn, child_id=1, attempt_id=attempt).stage == 0


def test_one_wrong_answer_out_of_two_is_not_enough(conn):
    """
    У миссий порог полный, а не 0,8.

    При двух вопросах порог 0,8 означал бы «оба верных», только неявно.
    Лучше сказать это прямо.
    """
    attempt = start(conn)
    right = answers_for(conn, attempt)
    right["2"] = "мимо"
    result = service.submit_card(conn, child_id=1, attempt_id=attempt, answers=right, at=AT)

    assert result.judgement.passed is False


def test_a_card_can_be_retried_after_failing(conn):
    attempt = start(conn)
    service.submit_card(conn, child_id=1, attempt_id=attempt,
                        answers=answers_for(conn, attempt, wrong=True), at=AT)
    again = pass_card(conn, attempt)

    assert again.advanced is True


def test_empty_form_does_not_advance(conn):
    attempt = start(conn)
    result = service.submit_card(conn, child_id=1, attempt_id=attempt, answers={}, at=AT)
    assert result.advanced is False


# --------------------------------------------------------------------------
# Подсказки
# --------------------------------------------------------------------------


def test_hints_are_closed_until_asked(conn):
    attempt = start(conn)
    state = service.mission_state(conn, child_id=1, attempt_id=attempt)

    assert state.card["hints"] == []
    assert state.card["hints_total"] == 2, "их наличие видно, содержание — нет"


def test_asking_for_a_hint_opens_exactly_one(conn):
    attempt = start(conn)
    state = service.reveal_hint(conn, child_id=1, attempt_id=attempt)

    assert len(state.card["hints"]) == 1
    assert state.card["hints"][0].startswith("Сравни")


def test_hints_do_not_reduce_the_reward(conn):
    """
    По методике подсказку можно просить сразу, и она не стоит минут.

    Плата за помощь имела бы смысл, чтобы ребёнок её не просил, — а нам нужно
    ровно обратное.
    """
    attempt = start(conn)
    service.reveal_hint(conn, child_id=1, attempt_id=attempt)
    service.reveal_hint(conn, child_id=1, attempt_id=attempt)
    for _ in range(3):
        result = pass_card(conn, attempt)

    assert result.minutes == 10


def test_hints_survive_a_page_reload(conn):
    attempt = start(conn)
    service.reveal_hint(conn, child_id=1, attempt_id=attempt)
    reopened = service.mission_state(conn, child_id=1, attempt_id=attempt)

    assert len(reopened.card["hints"]) == 1


def test_asking_past_the_last_hint_is_harmless(conn):
    attempt = start(conn)
    for _ in range(5):
        state = service.reveal_hint(conn, child_id=1, attempt_id=attempt)
    assert len(state.card["hints"]) == 2


def test_hints_of_the_next_card_start_closed(conn):
    attempt = start(conn)
    service.reveal_hint(conn, child_id=1, attempt_id=attempt)
    pass_card(conn, attempt)

    state = service.mission_state(conn, child_id=1, attempt_id=attempt)
    assert state.card["hints"] == []


# --------------------------------------------------------------------------
# Назначение на день
# --------------------------------------------------------------------------


def test_only_two_missions_are_assigned_per_day(tmp_path):
    c = db.open_db(tmp_path / "many.sqlite3")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'с','C','D')")
    for i in range(1, 10):
        c.execute(
            "INSERT INTO task (id, slug, kind, version, title, section, payload, reward_minutes) "
            "VALUES (?, ?, 'mission', 1, ?, 'missions', ?, 10)",
            (i, f"m{i}", f"Миссия {i}", json.dumps(MISSION, ensure_ascii=False)),
        )

    view = service.day_view(c, 1, DAY)
    assert len(view.by_section(service.MISSIONS)) == service.PER_DAY[service.MISSIONS]
    c.close()


def test_the_same_day_always_gives_the_same_missions(tmp_path):
    """Обновление страницы не должно менять задание на день."""
    c = db.open_db(tmp_path / "stable.sqlite3")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'с','C','D')")
    for i in range(1, 8):
        c.execute(
            "INSERT INTO task (id, slug, kind, version, title, section, payload, reward_minutes) "
            "VALUES (?, ?, 'mission', 1, ?, 'missions', ?, 10)",
            (i, f"m{i}", f"Миссия {i}", json.dumps(MISSION, ensure_ascii=False)),
        )

    first = [i.task_id for i in service.day_view(c, 1, DAY).by_section(service.MISSIONS)]
    second = [i.task_id for i in service.day_view(c, 1, DAY).by_section(service.MISSIONS)]
    assert first == second
    c.close()


def test_a_different_day_shifts_the_selection(tmp_path):
    """Иначе сын видел бы одно и то же, а новое ждало бы в конце каталога."""
    c = db.open_db(tmp_path / "shift.sqlite3")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'с','C','D')")
    for i in range(1, 8):
        c.execute(
            "INSERT INTO task (id, slug, kind, version, title, section, payload, reward_minutes) "
            "VALUES (?, ?, 'mission', 1, ?, 'missions', ?, 10)",
            (i, f"m{i}", f"Миссия {i}", json.dumps(MISSION, ensure_ascii=False)),
        )

    today = [i.task_id for i in service.day_view(c, 1, "2026-09-11").by_section(service.MISSIONS)]
    tomorrow = [i.task_id for i in service.day_view(c, 1, "2026-09-12").by_section(service.MISSIONS)]
    assert not set(today) & set(tomorrow), "соседние дни не должны пересекаться"
    c.close()


def test_the_whole_catalogue_comes_round_before_anything_repeats(tmp_path):
    """Девять миссий по две в день: за пять дней покажутся все девять."""
    c = db.open_db(tmp_path / "round.sqlite3")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'с','C','D')")
    for i in range(1, 10):
        c.execute(
            "INSERT INTO task (id, slug, kind, version, title, section, payload, reward_minutes) "
            "VALUES (?, ?, 'mission', 1, ?, 'missions', ?, 10)",
            (i, f"m{i}", f"Миссия {i}", json.dumps(MISSION, ensure_ascii=False)),
        )
    seen = set()
    for d in range(11, 16):
        seen |= {i.task_id for i in service.day_view(c, 1, f"2026-09-{d}").by_section(service.MISSIONS)}
    assert seen == set(range(1, 10))
    c.close()


def test_only_a_few_vpr_walkthroughs_are_assigned_per_day(tmp_path):
    """
    Восемнадцать разборов сразу — это стена, в которой большая часть уже не
    даёт минут: в квоту сверх базы и домашки влезает 60, то есть шесть заданий.
    """
    c = db.open_db(tmp_path / "vpr.sqlite3")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'с','C','D')")
    for i in range(1, 19):
        c.execute(
            "INSERT INTO task (id, slug, kind, version, title, section, payload, reward_minutes) "
            "VALUES (?, ?, 'quiz', 1, ?, 'vpr', ?, 10)",
            (i, f"v{i}", f"Задание {i}",
             json.dumps({"questions": [{"prompt": "2+2=?", "answer": "4", "hint": "h"}]},
                        ensure_ascii=False)),
        )

    view = service.day_view(c, 1, DAY)
    assert len(view.by_section(service.VPR)) == service.PER_DAY[service.VPR]
    c.close()


def test_the_vpr_selection_also_shifts_by_day(tmp_path):
    c = db.open_db(tmp_path / "vprshift.sqlite3")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'с','C','D')")
    for i in range(1, 19):
        c.execute(
            "INSERT INTO task (id, slug, kind, version, title, section, payload, reward_minutes) "
            "VALUES (?, ?, 'quiz', 1, ?, 'vpr', ?, 10)",
            (i, f"v{i}", f"Задание {i}",
             json.dumps({"questions": [{"prompt": "2+2=?", "answer": "4", "hint": "h"}]},
                        ensure_ascii=False)),
        )

    today = [i.task_id for i in service.day_view(c, 1, "2026-09-11").by_section(service.VPR)]
    later = [i.task_id for i in service.day_view(c, 1, "2026-09-14").by_section(service.VPR)]
    assert today != later, "иначе часть каталога не попадётся месяцами"
    c.close()


def test_a_day_offers_no_more_than_the_cap_allows(tmp_path):
    """
    Сумма наград за день не должна намного превышать то, что войдёт в квоту.

    Иначе экран обещает минуты, которых банк не выдаст, и это обман.
    """
    c = db.open_db(tmp_path / "cap.sqlite3")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'с','C','D')")
    for i in range(1, 10):
        c.execute(
            "INSERT INTO task (id, slug, kind, version, title, section, payload, reward_minutes) "
            "VALUES (?, ?, 'mission', 1, ?, 'missions', ?, 10)",
            (i, f"m{i}", f"Миссия {i}", json.dumps(MISSION, ensure_ascii=False)),
        )
    for i in range(20, 38):
        c.execute(
            "INSERT INTO task (id, slug, kind, version, title, section, payload, reward_minutes) "
            "VALUES (?, ?, 'quiz', 1, ?, 'vpr', ?, 10)",
            (i, f"v{i}", f"Задание {i}",
             json.dumps({"questions": [{"prompt": "2+2=?", "answer": "4", "hint": "h"}]},
                        ensure_ascii=False)),
        )

    view = service.day_view(c, 1, DAY)
    offered = sum(i.reward_minutes for i in view.items)
    room = bank.daily_max_minutes(c) - bank.base_minutes(c)

    assert offered <= room, f"на день предложено {offered} мин, а в квоту войдёт {room}"
    c.close()


def test_vpr_tasks_stay_in_their_own_section(conn):
    conn.execute(
        "INSERT INTO task (id, slug, kind, version, title, section, payload, reward_minutes) "
        "VALUES (2, 'v1', 'quiz', 1, 'Задание 1', 'vpr', ?, 10)",
        (json.dumps({"questions": [{"prompt": "2+2=?", "answer": "4", "hint": "h"}]},
                    ensure_ascii=False),),
    )
    view = service.day_view(conn, 1, DAY)

    assert [i.title for i in view.by_section(service.VPR)] == ["Задание 1"]
    assert [i.title for i in view.by_section(service.MISSIONS)] == ["Реклама набора"]


# --------------------------------------------------------------------------
# Чужое трогать нельзя
# --------------------------------------------------------------------------


def test_another_child_cannot_read_the_card(conn):
    attempt = start(conn)
    with pytest.raises(service.ServiceError):
        service.mission_state(conn, child_id=2, attempt_id=attempt)


def test_another_child_cannot_submit_or_ask_for_hints(conn):
    attempt = start(conn)
    with pytest.raises(service.ServiceError):
        service.submit_card(conn, child_id=2, attempt_id=attempt, answers={}, at=AT)
    with pytest.raises(service.ServiceError):
        service.reveal_hint(conn, child_id=2, attempt_id=attempt)
