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


def d(actual, target, applied=None, base=BASE):
    return delivery.decide(actual=actual, target=target, applied=applied, base=base)


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


def test_manual_grant_before_our_first_write_is_left_alone():
    """
    Ровно тот случай, что случился в жизни: цель 35, на планшете 120.

    Записать 35 значило бы отнять время, которое папа выдал руками.
    """
    decision = d(actual=120, target=35)
    assert decision.action == delivery.MANUAL
    assert "120" in decision.reason


def test_manual_change_after_our_write_is_left_alone():
    """Мы поставили 35, кто-то сделал 90. Не спорим."""
    decision = d(actual=90, target=45, applied=35)
    assert decision.action == delivery.MANUAL
    assert "после нашей записи" in decision.reason


def test_manual_lowering_is_also_respected():
    """Родитель урезал время в наказание — автоматика не возвращает его."""
    assert d(actual=5, target=35, applied=35).action == delivery.MANUAL


def test_manual_value_equal_to_target_needs_no_argument():
    """Родитель случайно выставил ровно нашу цель — писать нечего."""
    assert d(actual=35, target=35).action == delivery.CONFIRM


def test_it_recovers_when_the_value_returns_to_expected():
    """
    Вмешательство не блокирует навсегда.

    Родитель вернул лимит к тому, что мы записывали, — работа продолжается.
    Это важно: иначе один ручной сдвиг замораживал бы выдачу до конца дня.
    """
    assert d(actual=90, target=45, applied=35).action == delivery.MANUAL
    assert d(actual=35, target=45, applied=35).action == delivery.WRITE


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
    перетирать его нельзя: может быть, расписание поменяли осознанно.
    """
    decision = d(actual=30, target=15)
    assert decision.action == delivery.MANUAL
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
