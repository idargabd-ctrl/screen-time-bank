# -*- coding: utf-8 -*-
"""
Тесты заданий.

Отдельное внимание двум вещам: правильные ответы не должны попадать в то, что
уходит браузеру, и пустая форма не должна засчитываться.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tasks  # noqa: E402


# --------------------------------------------------------------------------
# Математика
# --------------------------------------------------------------------------


def test_math_generates_requested_count():
    qs = tasks.generate(tasks.MATH, {"count": 5, "max": 20}, seed=1)
    assert len(qs) == 5


def test_math_answers_are_right():
    qs = tasks.generate(tasks.MATH, {"count": 20, "ops": ["+", "-", "*"], "max": 30}, seed=7)
    for q in qs:
        left = q.prompt.replace(" = ?", "").replace("*", "*")
        assert str(eval(left)) == q.answer, f"неверный ответ в «{q.prompt}»"


def test_subtraction_never_goes_negative():
    qs = tasks.generate(tasks.MATH, {"count": 30, "ops": ["-"], "max": 15}, seed=3)
    for q in qs:
        assert int(q.answer) >= 0


def test_division_is_exact():
    qs = tasks.generate(tasks.MATH, {"count": 15, "ops": ["/"], "min": 2, "max": 12}, seed=5)
    for q in qs:
        a, b = q.prompt.replace(" = ?", "").split(" / ")
        assert int(a) % int(b) == 0, f"не делится нацело: {q.prompt}"


def test_same_seed_gives_same_questions():
    a = tasks.generate(tasks.MATH, {"count": 5}, seed=42)
    b = tasks.generate(tasks.MATH, {"count": 5}, seed=42)
    assert [q.prompt for q in a] == [q.prompt for q in b]


def test_different_seed_gives_different_questions():
    a = tasks.generate(tasks.MATH, {"count": 5, "max": 100}, seed=1)
    b = tasks.generate(tasks.MATH, {"count": 5, "max": 100}, seed=2)
    assert [q.prompt for q in a] != [q.prompt for q in b], "повтор должен давать другие вопросы"


def test_questions_do_not_repeat_inside_one_attempt():
    qs = tasks.generate(tasks.MATH, {"count": 10, "ops": ["+"], "max": 50}, seed=11)
    assert len({q.prompt for q in qs}) == len(qs)


# --------------------------------------------------------------------------
# Слова
# --------------------------------------------------------------------------


def test_words_use_the_given_pairs():
    payload = {"pairs": [["cat", "кошка"], ["dog", "собака"]], "count": 2}
    qs = tasks.generate(tasks.WORDS, payload, seed=1)
    assert len(qs) == 2
    assert {q.answer for q in qs} == {"кошка", "собака"}


def test_words_can_go_the_other_way():
    payload = {"pairs": [["cat", "кошка"]], "count": 1, "direction": "ru-en"}
    qs = tasks.generate(tasks.WORDS, payload, seed=1)
    assert qs[0].answer == "cat"
    assert "кошка" in qs[0].prompt


def test_words_without_pairs_give_nothing():
    assert tasks.generate(tasks.WORDS, {"pairs": []}, seed=1) == []


# --------------------------------------------------------------------------
# Сравнение ответов
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given, expected",
    [
        ("  7 ", "7"),
        ("Кошка", "кошка"),
        ("ёж", "еж"),
        ("2,5", "2.5"),
        ("07", "7"),
        ("2.0", "2"),
        ("кошка.", "кошка"),
        ("две   собаки", "две собаки"),
    ],
)
def test_formatting_does_not_cost_the_child_time(given, expected):
    assert tasks.is_correct(given, expected) is True


@pytest.mark.parametrize("given", ["8", "", "кот", "не знаю", None])
def test_wrong_answers_are_wrong(given):
    assert tasks.is_correct(given or "", "7") is False


# --------------------------------------------------------------------------
# Оценка попытки
# --------------------------------------------------------------------------


def make(n=5):
    return [tasks.Question(prompt=f"{i} + 0 = ?", answer=str(i)) for i in range(1, n + 1)]


def test_all_right_passes():
    qs = make()
    j = tasks.judge(qs, {str(i): str(i) for i in range(1, 6)})
    assert (j.correct, j.total, j.passed) == (5, 5, True)


def test_four_of_five_passes_at_default_threshold():
    qs = make()
    answers = {str(i): str(i) for i in range(1, 5)}
    answers["5"] = "неверно"
    j = tasks.judge(qs, answers)
    assert (j.correct, j.passed) == (4, True)


def test_three_of_five_fails():
    qs = make()
    answers = {"1": "1", "2": "2", "3": "3", "4": "нет", "5": "нет"}
    assert tasks.judge(qs, answers).passed is False


def test_empty_form_is_not_a_pass():
    """Иначе задание сдаётся нажатием кнопки без единого ответа."""
    j = tasks.judge(make(), {})
    assert (j.correct, j.passed) == (0, False)


def test_missing_answers_count_as_wrong():
    qs = make()
    j = tasks.judge(qs, {"1": "1", "2": "2"})
    assert j.correct == 2
    assert j.total == 5


# --------------------------------------------------------------------------
# Главное: правильные ответы не уходят наружу
# --------------------------------------------------------------------------


def test_child_view_of_a_question_has_no_answer():
    q = tasks.Question(prompt="2 + 2 = ?", answer="4", hint="подумай")
    payload = json.dumps(q.for_child(1), ensure_ascii=False)
    assert "4" not in payload.replace('"n": 1', "")
    assert "answer" not in payload


def test_child_view_of_a_result_never_shows_the_right_answer():
    qs = [tasks.Question(prompt="2 + 2 = ?", answer="4", hint="посчитай снова")]
    j = tasks.judge(qs, {"1": "5"})
    payload = json.dumps(j.for_child(), ensure_ascii=False)

    assert j.passed is False
    assert '"expected"' not in payload, "правильный ответ не должен уходить браузеру"
    assert "посчитай снова" in payload, "подсказка при ошибке нужна"


def test_hint_is_not_shown_for_correct_answers():
    qs = [tasks.Question(prompt="2 + 2 = ?", answer="4", hint="подсказка")]
    view = tasks.judge(qs, {"1": "4"}).for_child()
    assert view["items"][0]["hint"] == ""


# --------------------------------------------------------------------------
# Сохранение и чтение
# --------------------------------------------------------------------------


def test_questions_survive_a_round_trip_through_the_database():
    qs = tasks.generate(tasks.MATH, {"count": 3}, seed=9)
    restored = tasks.load(json.loads(json.dumps(tasks.dump(qs))))
    assert [(q.prompt, q.answer, q.hint) for q in restored] == [(q.prompt, q.answer, q.hint) for q in qs]


def test_answer_may_list_alternatives_with_a_pipe():
    """mother — «мама» или «мать»: оба верны, требовать одно — наказывать за знание."""
    assert tasks.is_correct("Мама", "мама|мать")
    assert tasks.is_correct("мать ", "мама|мать")
    assert not tasks.is_correct("папа", "мама|мать")
    assert tasks.is_correct("7", "07|семь") and tasks.is_correct("семь", "07|семь")
