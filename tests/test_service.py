# -*- coding: utf-8 -*-
"""
Тесты сценариев.

Проверяется то, ради чего слой существует: браузер не может назначить себе
награду, повторная отправка не удваивает время, а чужую попытку не открыть.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import bank, db, service, tasks  # noqa: E402

AT = "2026-09-10T18:00:00+03:00"
DAY = "2026-09-10"


@pytest.fixture()
def conn(tmp_path):
    c = db.open_db(tmp_path / "svc.sqlite3")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'сын','C','D')")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (2,'другой','C2','D2')")
    c.execute(
        "INSERT INTO task (id, kind, version, title, payload, reward_minutes) "
        "VALUES (1, 'math', 1, 'Примеры', ?, 30)",
        (json.dumps({"count": 5, "ops": ["+"], "max": 10}),),
    )
    c.execute(
        "INSERT INTO task (id, kind, version, title, payload, reward_minutes) "
        "VALUES (2, 'words', 1, 'Слова', ?, 20)",
        (json.dumps({"count": 2, "pairs": [["cat", "кошка"], ["dog", "собака"]]}),),
    )
    # Эти тесты про сценарии прохождения заданий, а не про ворота домашки.
    # Ворота проверяются в test_bank.py, здесь их открываем, а цену домашки
    # обнуляем, чтобы цифры квоты были только про задания.
    bank.set_setting(c, bank.SETTING_HOMEWORK_MINUTES, "0")
    bank.mark_homework(c, child_id=1, day=DAY, at=AT)
    yield c
    c.close()


def right_answers(conn, attempt_id):
    """Правильные ответы берём из базы — так же, как их видит сервер."""
    row = conn.execute("SELECT questions FROM attempt WHERE id = ?", (attempt_id,)).fetchone()
    qs = tasks.load(json.loads(row["questions"]))
    return {str(i): q.answer for i, q in enumerate(qs, start=1)}


def pass_task(conn, assignment_id, child_id=1):
    aid = service.start_attempt(conn, child_id=child_id, assignment_id=assignment_id, at=AT)
    return service.submit(conn, child_id=child_id, attempt_id=aid,
                          answers=right_answers(conn, aid), at=AT)


# --------------------------------------------------------------------------
# День
# --------------------------------------------------------------------------


def test_day_lists_active_tasks(conn):
    view = service.day_view(conn, 1, DAY)
    assert [i.title for i in view.items] == ["Примеры", "Слова"]
    assert all(i.state == service.AVAILABLE for i in view.items)
    assert view.balance.target == 15


def test_disabled_task_is_not_offered(conn):
    conn.execute("UPDATE task SET active = 0 WHERE id = 2")
    view = service.day_view(conn, 1, DAY)
    assert [i.title for i in view.items] == ["Примеры"]


def test_day_can_be_opened_twice_without_duplicating_assignments(conn):
    service.day_view(conn, 1, DAY)
    service.day_view(conn, 1, DAY)
    count = conn.execute("SELECT COUNT(*) c FROM assignment WHERE child_id = 1").fetchone()["c"]
    assert count == 2


def test_state_becomes_in_progress_then_earned(conn):
    view = service.day_view(conn, 1, DAY)
    item = view.items[0]

    service.start_attempt(conn, child_id=1, assignment_id=item.assignment_id, at=AT)
    assert service.day_view(conn, 1, DAY).items[0].state == service.IN_PROGRESS

    pass_task(conn, item.assignment_id)
    assert service.day_view(conn, 1, DAY).items[0].state == service.EARNED


def test_available_minutes_respect_the_daily_maximum(conn):
    bank.set_setting(conn, bank.SETTING_DAILY_MAX, "30")
    view = service.day_view(conn, 1, DAY)
    # База 15, потолок 30, заданий на 50 минут — заработать можно только 15.
    assert view.available_minutes == 15


# --------------------------------------------------------------------------
# Награда назначается сервером, а не браузером
# --------------------------------------------------------------------------


def test_reward_comes_from_the_catalogue(conn):
    view = service.day_view(conn, 1, DAY)
    result = pass_task(conn, view.items[0].assignment_id)

    assert result.granted is True
    assert result.minutes == 30, "минуты берутся из каталога, а не из запроса"
    assert result.balance.target == 45


def test_resubmitting_the_same_attempt_does_not_pay_twice(conn):
    view = service.day_view(conn, 1, DAY)
    aid = service.start_attempt(conn, child_id=1, assignment_id=view.items[0].assignment_id, at=AT)
    answers = right_answers(conn, aid)

    first = service.submit(conn, child_id=1, attempt_id=aid, answers=answers, at=AT)
    second = service.submit(conn, child_id=1, attempt_id=aid, answers=answers, at=AT)

    assert first.granted is True
    assert second.granted is False
    assert second.balance.earned == 30


def test_second_attempt_after_success_is_refused(conn):
    view = service.day_view(conn, 1, DAY)
    pass_task(conn, view.items[0].assignment_id)

    with pytest.raises(service.ServiceError):
        service.start_attempt(conn, child_id=1,
                              assignment_id=view.items[0].assignment_id, at=AT)


def test_two_tasks_add_up(conn):
    view = service.day_view(conn, 1, DAY)
    pass_task(conn, view.items[0].assignment_id)
    result = pass_task(conn, view.items[1].assignment_id)

    assert result.balance.earned == 50
    assert result.balance.target == 65


# --------------------------------------------------------------------------
# Неудача и повтор
# --------------------------------------------------------------------------


def test_wrong_answers_earn_nothing(conn):
    view = service.day_view(conn, 1, DAY)
    aid = service.start_attempt(conn, child_id=1, assignment_id=view.items[0].assignment_id, at=AT)

    result = service.submit(conn, child_id=1, attempt_id=aid,
                            answers={"1": "нет", "2": "нет", "3": "нет"}, at=AT)

    assert result.judgement.passed is False
    assert result.granted is False
    assert result.balance.earned == 0
    assert result.balance.target == 15, "остаётся базовая квота"


def test_retry_gets_different_questions(conn):
    view = service.day_view(conn, 1, DAY)
    a1 = service.start_attempt(conn, child_id=1, assignment_id=view.items[0].assignment_id, at=AT)
    service.submit(conn, child_id=1, attempt_id=a1, answers={}, at=AT)

    a2 = service.start_attempt(conn, child_id=1, assignment_id=view.items[0].assignment_id, at=AT)

    q1 = [q["prompt"] for q in service.attempt_questions(conn, child_id=1, attempt_id=a1)]
    q2 = [q["prompt"] for q in service.attempt_questions(conn, child_id=1, attempt_id=a2)]
    assert q1 != q2, "повтор не должен быть подбором к тем же вопросам"


def test_retry_after_failure_can_still_earn(conn):
    view = service.day_view(conn, 1, DAY)
    a1 = service.start_attempt(conn, child_id=1, assignment_id=view.items[0].assignment_id, at=AT)
    service.submit(conn, child_id=1, attempt_id=a1, answers={}, at=AT)

    result = pass_task(conn, view.items[0].assignment_id)
    assert result.granted is True
    assert result.balance.earned == 30


def test_starting_a_new_attempt_closes_the_previous_one(conn):
    view = service.day_view(conn, 1, DAY)
    a1 = service.start_attempt(conn, child_id=1, assignment_id=view.items[0].assignment_id, at=AT)
    service.start_attempt(conn, child_id=1, assignment_id=view.items[0].assignment_id, at=AT)

    row = conn.execute("SELECT finished_at FROM attempt WHERE id = ?", (a1,)).fetchone()
    assert row["finished_at"] is not None


# --------------------------------------------------------------------------
# Чужое трогать нельзя
# --------------------------------------------------------------------------


def test_another_child_cannot_open_someone_elses_attempt(conn):
    view = service.day_view(conn, 1, DAY)
    aid = service.start_attempt(conn, child_id=1, assignment_id=view.items[0].assignment_id, at=AT)

    with pytest.raises(service.ServiceError):
        service.attempt_questions(conn, child_id=2, attempt_id=aid)

    with pytest.raises(service.ServiceError):
        service.submit(conn, child_id=2, attempt_id=aid, answers={}, at=AT)


def test_another_child_cannot_start_someone_elses_assignment(conn):
    view = service.day_view(conn, 1, DAY)
    with pytest.raises(service.ServiceError):
        service.start_attempt(conn, child_id=2,
                              assignment_id=view.items[0].assignment_id, at=AT)


def test_unknown_attempt_is_refused(conn):
    with pytest.raises(service.ServiceError):
        service.submit(conn, child_id=1, attempt_id=999, answers={}, at=AT)


# --------------------------------------------------------------------------
# Вопросы, уходящие браузеру
# --------------------------------------------------------------------------


def test_questions_sent_to_the_browser_carry_no_answers(conn):
    view = service.day_view(conn, 1, DAY)
    aid = service.start_attempt(conn, child_id=1, assignment_id=view.items[0].assignment_id, at=AT)

    sent = json.dumps(service.attempt_questions(conn, child_id=1, attempt_id=aid),
                      ensure_ascii=False)
    stored = right_answers(conn, aid)

    assert "answer" not in sent
    for number, answer in stored.items():
        assert f'"{answer}"' not in sent, "ответ не должен уходить браузеру"


# --------------------------------------------------------------------------
# Календарь домашки
# --------------------------------------------------------------------------


def test_human_date_in_russian_without_locale():
    assert service.human_date("2026-09-11") == "Пятница, 11 сентября"
    assert service.human_date("2026-01-05") == "Понедельник, 5 января"


def test_nothing_assigned_needs_the_diary_on_school_days():
    for day in ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"):   # пн–чт
        p = service.homework_policy(day)
        assert p.nothing_allowed and p.nothing_needs_photo and not p.for_monday, day


def test_nothing_assigned_is_taken_on_trust_on_friday_and_saturday():
    for day in ("2026-09-11", "2026-09-12"):
        p = service.homework_policy(day)
        assert p.nothing_allowed and not p.nothing_needs_photo, day


def test_sunday_is_for_mondays_homework():
    p = service.homework_policy("2026-09-13")
    assert not p.nothing_allowed
    assert p.for_monday


# --------------------------------------------------------------------------
# Пилот: оценка ребёнка и сводка
# --------------------------------------------------------------------------


def test_rating_is_voluntary_replaceable_and_only_for_own_attempts(conn):
    view = service.day_view(conn, 1, DAY)
    item = view.items[0]
    attempt_id = service.start_attempt(conn, child_id=1, assignment_id=item.assignment_id, at=AT)

    service.rate_attempt(conn, child_id=1, attempt_id=attempt_id, rating="ok", at=AT)
    service.rate_attempt(conn, child_id=1, attempt_id=attempt_id, rating="fun", at=AT)
    rows = conn.execute("SELECT rating FROM rating").fetchall()
    assert [r["rating"] for r in rows] == ["fun"], "повторная оценка заменяет, а не добавляет"

    conn.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (9,'другой','C9','D9')")
    with pytest.raises(service.ServiceError):
        service.rate_attempt(conn, child_id=9, attempt_id=attempt_id, rating="ok", at=AT)
    with pytest.raises(service.ServiceError):
        service.rate_attempt(conn, child_id=1, attempt_id=attempt_id, rating="great", at=AT)


def test_pilot_summary_counts_offered_started_finished_and_ratings(conn):
    view = service.day_view(conn, 1, DAY)
    item = view.items[0]
    attempt_id = service.start_attempt(conn, child_id=1, assignment_id=item.assignment_id, at=AT)
    service.submit(conn, child_id=1, attempt_id=attempt_id, answers=right_answers(conn, attempt_id), at=AT)
    service.rate_attempt(conn, child_id=1, attempt_id=attempt_id, rating="boring", at=AT)

    days, items = service.pilot_summary(conn, 1, since=DAY)
    assert len(days) == 1 and days[0].day == DAY
    assert days[0].offered == len(view.items)
    assert days[0].started == 1 and days[0].finished == 1
    assert days[0].ratings == {"boring": 1, "ok": 0, "fun": 0}
    assert items[0].finished is True and items[0].rating == "скучно"


def test_rating_comment_is_kept_trimmed_and_replaced(conn):
    view = service.day_view(conn, 1, DAY)
    attempt_id = service.start_attempt(conn, child_id=1, assignment_id=view.items[0].assignment_id, at=AT)
    service.rate_attempt(conn, child_id=1, attempt_id=attempt_id, rating="ok", at=AT,
                         comment="  слишком   длинно\nчитать ")
    assert conn.execute("SELECT comment FROM rating").fetchone()["comment"] == "слишком длинно читать"
    service.rate_attempt(conn, child_id=1, attempt_id=attempt_id, rating="ok", at=AT, comment="x" * 900)
    assert len(conn.execute("SELECT comment FROM rating").fetchone()["comment"]) == 500
    _, items = service.pilot_summary(conn, 1, since=DAY)
    assert items[0].comment.startswith("xxx")


def test_pilot_summary_shows_the_parents_manual_minutes(conn):
    """Ручная надбавка дня приходит из строки выдачи — её пишет исполнитель."""
    view = service.day_view(conn, 1, DAY)
    attempt_id = service.start_attempt(conn, child_id=1, assignment_id=view.items[0].assignment_id, at=AT)
    service.submit(conn, child_id=1, attempt_id=attempt_id, answers=right_answers(conn, attempt_id), at=AT)
    conn.execute("UPDATE delivery SET manual_minutes = 35 WHERE child_id = 1 AND day = ?", (DAY,))
    days, _ = service.pilot_summary(conn, 1, since=DAY)
    assert days[0].manual == 35


def test_checklist_is_once_a_day_and_keeps_unchecked_items(conn):
    assert service.checklist_state(conn, 1, DAY) is None
    assert service.checklist_items(conn) == list(service.CHECKLIST_DEFAULT)
    bank.set_setting(conn, service.SETTING_CHECKLIST, "Зубы\n\n Кровать \nПортфель")
    assert service.checklist_items(conn) == ["Зубы", "Кровать", "Портфель"]

    items = service.complete_checklist(conn, child_id=1, day=DAY, checked=["Зубы", "Портфель", "чужое"], at=AT)
    assert items == {"Зубы": True, "Кровать": False, "Портфель": True}
    assert service.checklist_state(conn, 1, DAY) == items
    # повтор в тот же день заменяет запись, а не добавляет вторую
    service.complete_checklist(conn, child_id=1, day=DAY, checked=["Кровать"], at=AT)
    assert conn.execute("SELECT COUNT(*) FROM checklist").fetchone()[0] == 1
    history = service.checklist_history(conn, 1, since=DAY)
    assert history[0]["items"]["Кровать"] is True and history[0]["items"]["Зубы"] is False


def test_mission_state_exposes_previous_cards_without_answers(conn):
    """Следующая карточка ссылается на условие предыдущей — его можно перечитать."""
    import json as _json
    conn.execute(
        "INSERT INTO task (id, kind, version, title, payload, reward_minutes, section, active) "
        "VALUES (77, 'mission', 1, 'Миссия', ?, 10, 'missions', 1)",
        (_json.dumps({"cards": [
            {"title": "Вызов", "intro": "У Артёма 3 коробки.", "questions": [{"prompt": "Сколько коробок?", "answer": "3"}]},
            {"title": "Смысл", "intro": "", "questions": [{"prompt": "А если ещё две?", "answer": "5"}]},
        ]}),),
    )
    conn.execute("INSERT INTO assignment (child_id, task_id, day) VALUES (1, 77, ?)", (DAY,))
    aid = conn.execute("SELECT id FROM assignment WHERE task_id = 77").fetchone()["id"]
    attempt_id = service.start_attempt(conn, child_id=1, assignment_id=aid, at=AT)
    state = service.mission_state(conn, child_id=1, attempt_id=attempt_id)
    assert state.previous == ()
    service.submit_card(conn, child_id=1, attempt_id=attempt_id, answers={"1": "3"}, at=AT)
    state = service.mission_state(conn, child_id=1, attempt_id=attempt_id)
    assert len(state.previous) == 1
    prev = state.previous[0]
    assert prev["intro"] == "У Артёма 3 коробки." and prev["questions"][0]["prompt"] == "Сколько коробок?"
    assert "3" not in _json.dumps(prev["questions"], ensure_ascii=False).replace("Сколько", "")
    assert "answer" not in _json.dumps(prev)


# --------------------------------------------------------------------------
# «Хочу ещё минут»: сложные задания по просьбе
# --------------------------------------------------------------------------


def _add_extra(conn, task_id, title):
    conn.execute(
        "INSERT INTO task (id, kind, version, title, payload, reward_minutes, section, active) "
        "VALUES (?, 'math', 1, ?, ?, 25, 'extra', 1)",
        (task_id, title, json.dumps({"count": 3, "ops": ["+"], "max": 10})),
    )


def test_extra_tasks_are_not_offered_by_themselves(conn):
    _add_extra(conn, 50, "Сложное")
    view = service.day_view(conn, 1, DAY)
    assert view.by_section(service.EXTRA) == []
    assert service.can_request_extra(view) is False, "обычные ещё не зачтены"


def test_extra_is_given_only_after_everything_open_is_earned_and_at_most_three(conn):
    for tid in (50, 51, 52, 53):
        _add_extra(conn, tid, f"Сложное {tid}")
    with pytest.raises(service.ServiceError):
        service.request_extra(conn, child_id=1, day=DAY)

    for item in service.day_view(conn, 1, DAY).items:
        pass_task(conn, item.assignment_id)
    assert service.can_request_extra(service.day_view(conn, 1, DAY))
    assert service.extra_left(conn, 1, DAY) == 3

    first = service.request_extra(conn, child_id=1, day=DAY)
    view = service.day_view(conn, 1, DAY)
    extra = view.by_section(service.EXTRA)
    assert [i.assignment_id for i in extra] == [first] and extra[0].reward_minutes == 25
    # пока сложное не зачтено — второе не дают
    with pytest.raises(service.ServiceError):
        service.request_extra(conn, child_id=1, day=DAY)
    pass_task(conn, first)
    assert service.extra_left(conn, 1, DAY) == 2

    second = service.request_extra(conn, child_id=1, day=DAY); pass_task(conn, second)
    third = service.request_extra(conn, child_id=1, day=DAY); pass_task(conn, third)
    assert service.extra_left(conn, 1, DAY) == 0
    with pytest.raises(service.ServiceError, match="всё"):
        service.request_extra(conn, child_id=1, day=DAY)
    # три разных задания, никакого повтора в один день
    ids = {i.task_id for i in service.day_view(conn, 1, DAY).by_section(service.EXTRA)}
    assert len(ids) == 3
    bal = bank.balance(conn, 1, DAY)
    assert bal.earned == 30 + 20 + 75 and bal.target == 15 + 125


def test_extra_picks_the_least_recently_seen_task(conn):
    _add_extra(conn, 50, "A"); _add_extra(conn, 51, "B")
    conn.execute("INSERT INTO assignment (child_id, task_id, day) VALUES (1, 50, '2026-09-01')")
    for item in service.day_view(conn, 1, DAY).items:
        pass_task(conn, item.assignment_id)
    aid = service.request_extra(conn, child_id=1, day=DAY)
    task_id = conn.execute("SELECT task_id FROM assignment WHERE id = ?", (aid,)).fetchone()["task_id"]
    assert task_id == 51, "никогда не виденное — первым"
