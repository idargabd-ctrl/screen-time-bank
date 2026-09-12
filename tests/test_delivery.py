# -*- coding: utf-8 -*-
"""
Тесты решения о выдаче.

Главное, что проверяется: автоматика не отнимает время, выданное родителем
вручную. Это не гипотеза — на живых данных приложение хотело поставить 35
минут, а на планшете стояло 120, добавленных папой по просьбе сына. Слепая
запись означала бы наказание за родительскую щедрость.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import delivery  # noqa: E402

BASE = 15


def d(actual, target, applied=None, base=BASE, manual=0, daily_max=24 * 60):
    return delivery.decide(actual=actual, target=target, applied=applied, base=base,
                           manual=manual, daily_max=daily_max)


# --------------------------------------------------------------------------
# Обычная работа
# --------------------------------------------------------------------------


def test_first_write_of_the_day_raises_from_the_base():
    assert d(actual=15, target=35).action == delivery.WRITE


def test_nothing_to_do_when_the_value_already_matches():
    assert d(actual=35, target=35, applied=35).action == delivery.CONFIRM


def test_target_growing_during_the_day_is_written():
    """Сын прошёл ещё одно задание: 35 -> 45."""
    assert d(actual=35, target=45, applied=35).action == delivery.WRITE


def test_unreadable_state_is_not_a_reason_to_write():
    assert d(actual=None, target=35, applied=15).action == delivery.UNKNOWN


# --------------------------------------------------------------------------
# Ручное вмешательство родителя
# --------------------------------------------------------------------------


def test_manual_grant_before_our_first_write_is_an_advance():
    """
    Ровно тот случай, что случился в жизни: цель 35, на планшете 120.

    Записать 35 значило бы отнять время, которое папа выдал руками. Разница
    с базой (105) запоминается, а 120 признаётся авансом: цель 35 внутри него,
    писать нечего.
    """
    decision = d(actual=120, target=35)
    assert decision.action == delivery.CONFIRM
    assert decision.manual_changed
    assert decision.manual == 105
    assert decision.wanted == 120
    assert "120" in decision.reason


def test_earnings_catch_up_with_the_advance_instead_of_stacking():
    """Папа поставил 50 с утра, сын отметил домашку (цель 60): на планшете 60, не 95."""
    first = d(actual=50, target=15)            # утро: 15 → 50 руками
    assert first.action == delivery.CONFIRM and first.manual == 35
    later = d(actual=50, target=60, applied=50, manual=35)
    assert later.action == delivery.WRITE
    assert later.wanted == 60
    # цель ниже аванса — аванс остаётся, писать нечего
    assert d(actual=50, target=40, applied=50, manual=35).action == delivery.CONFIRM


def test_manual_change_after_our_write_is_an_advance_too():
    """Мы поставили 35, папа сделал 90, сын заработал ещё 10 (цель 45): остаётся 90."""
    decision = d(actual=90, target=45, applied=35)
    assert decision.action == delivery.CONFIRM
    assert decision.manual == 75, "аванс считается от базы: на устройстве 90 = 15 + 75"
    assert decision.wanted == 90
    assert "после нашей записи" in decision.reason


def test_daily_maximum_caps_the_advance_as_well():
    """Руками поставили 200 при максимуме 120 — сервер опускает до 120."""
    decision = d(actual=200, target=15, daily_max=120)
    assert decision.action == delivery.WRITE
    assert decision.wanted == 120
    assert "понижаем" in decision.reason


def test_manual_lowering_is_also_respected():
    """Родитель урезал время в наказание — автоматика не возвращает его."""
    decision = d(actual=5, target=35, applied=35)
    assert decision.action == delivery.CONFIRM
    assert decision.manual == -30
    # заработанное дальше идёт поверх урезанного, а не отменяет его
    assert d(actual=5, target=45, applied=5, manual=-30).wanted == 15


def test_offset_never_pushes_below_zero():
    assert d(actual=0, target=15, applied=15).wanted == 0


def test_manual_value_equal_to_target_needs_no_argument():
    """Родитель случайно выставил ровно нашу цель — писать нечего."""
    decision = d(actual=35, target=35)
    assert decision.action == delivery.CONFIRM
    assert decision.manual == 0


def test_late_google_apply_is_not_mistaken_for_a_manual_change():
    """
    Записали 45, Google применил с опозданием: при чтении было 35, строка
    осталась «не применено». Следующее чтение даёт 45 — это наша запись, а
    не рука родителя, и надбавка не должна появиться.
    """
    decision = d(actual=45, target=45, applied=35)
    assert decision.action == delivery.CONFIRM
    assert not decision.manual_changed
    assert decision.manual == 0


# --------------------------------------------------------------------------
# Понижение цели
# --------------------------------------------------------------------------


def test_revoked_homework_lowers_the_quota():
    """Отметку ДЗ отозвали: цель падает с 45 до базовых 15, и это выполняется."""
    decision = d(actual=45, target=15, applied=45)
    assert decision.action == delivery.WRITE
    assert "понижаем" in decision.reason


def test_lowered_daily_maximum_is_applied():
    assert d(actual=60, target=40, applied=60).action == delivery.WRITE


# --------------------------------------------------------------------------
# Начало дня
# --------------------------------------------------------------------------


def test_base_quota_alone_needs_no_write():
    """Утро, ничего не заработано: цель равна базе, на устройстве база."""
    assert d(actual=15, target=15).action == delivery.CONFIRM


def test_weekly_schedule_not_matching_our_base_is_reported():
    """
    База в Family Link оказалась не 15, а 30.

    Это расхождение между нашим представлением и реальностью, и молча
    перетирать его нельзя: 30 принимается как есть, разница уходит в надбавку
    и попадает в лог и на /parent.
    """
    decision = d(actual=30, target=15)
    assert decision.action == delivery.CONFIRM
    assert decision.manual_changed
    assert "30" in decision.reason


# --------------------------------------------------------------------------
# Что писать в базу после попытки
# --------------------------------------------------------------------------


def test_verified_write_is_confirmed():
    status, error = delivery.verdict_after_write(actual=45, target=45)
    assert status == delivery.CONFIRMED
    assert error == ""


def test_write_that_did_not_take_is_not_confirmed():
    status, error = delivery.verdict_after_write(actual=15, target=45)
    assert status == delivery.NOT_APPLIED
    assert "45" in error and "15" in error


def test_unreadable_state_after_write_is_unknown_not_success():
    """
    Успешный ответ Google не доказывает применение.

    В клиенте HAFamilyLink внутренняя ошибка проверки возвращает «успех»,
    поэтому единственное доказательство — перечитанное значение.
    """
    status, error = delivery.verdict_after_write(actual=None, target=45)
    assert status == delivery.STATUS_UNKNOWN
    assert status != delivery.CONFIRMED


@pytest.mark.parametrize("actual", [0, 1, 14, 16, 1000])
def test_any_other_value_after_write_is_not_applied(actual):
    status, _ = delivery.verdict_after_write(actual=actual, target=45)
    assert status == delivery.NOT_APPLIED
