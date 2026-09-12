# -*- coding: utf-8 -*-
"""
Тесты банка времени.

Проверяется то, что руками воспроизвести дорого или невозможно: одновременная
отправка из двух вкладок, переход суток, перезапуск сервера. Именно эти случаи
в чеклисте Этапа 1 стоят строками «повтор теста, две вкладки» и «полночь».
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import bank, db  # noqa: E402

AT = "2026-09-10T18:00:00+03:00"
DAY = "2026-09-10"
NEXT_DAY = "2026-09-11"


@pytest.fixture()
def conn(tmp_path):
    c = db.open_db(tmp_path / "test.sqlite3")
    c.execute(
        "INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1, 'сын', 'C', 'D')"
    )
    c.execute(
        "INSERT INTO task (id, kind, version, title, reward_minutes) "
        "VALUES (1, 'math', 1, 'Примеры', 30)"
    )
    c.execute(
        "INSERT INTO task (id, kind, version, title, reward_minutes) "
        "VALUES (2, 'words', 1, 'Слова', 20)"
    )
    # Ворота домашней работы по умолчанию закрыты, а эти тесты про арифметику
    # банка. Отмечаем ДЗ, чтобы проверять расчёт, а не ворота: им отведён
    # отдельный раздел ниже. Минуты за саму домашку здесь обнулены по той же
    # причине: тесты про арифметику наград, а не про цену тетради.
    bank.set_setting(c, bank.SETTING_HOMEWORK_MINUTES, "0")
    bank.mark_homework(c, child_id=1, day=DAY, at=AT)
    yield c
    c.close()


def give(conn, task_id=1, version=1, day=DAY, minutes=30):
    return bank.grant(
        conn,
        child_id=1,
        task_id=task_id,
        task_version=version,
        day=day,
        minutes=minutes,
        attempt_id=None,
        at=AT,
    )


# --------------------------------------------------------------------------
# Правило квоты
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base, earned, cap, expected",
    [
        (15, 0, 90, 15),      # ничего не заработал — остаётся база
        (15, 30, 90, 45),     # база плюс награда
        (15, 100, 90, 90),    # упёрлись в дневной максимум
        (15, 0, 10, 10),      # максимум ниже базы — максимум главнее
        (0, 0, 90, 0),
    ],
)
def test_quota_rule(base, earned, cap, expected):
    assert bank.quota_for(base, earned, cap) == expected


def test_quota_never_negative():
    assert bank.quota_for(-5, -5, 90) == 0


# --------------------------------------------------------------------------
# Идемпотентность — сердце всей затеи
# --------------------------------------------------------------------------


def test_second_grant_of_same_task_adds_nothing(conn):
    first = give(conn)
    second = give(conn)

    assert first.granted is True
    assert second.granted is False
    assert second.minutes == 0
    assert second.balance.earned == 30, "повтор не должен удваивать награду"
    assert conn.execute("SELECT COUNT(*) c FROM reward").fetchone()["c"] == 1


def test_repeat_cannot_be_bypassed_by_changing_minutes(conn):
    """Даже если во втором вызове другая сумма — награда уже выдана."""
    give(conn, minutes=30)
    again = give(conn, minutes=999)

    assert again.granted is False
    assert again.balance.earned == 30


def test_different_tasks_add_up(conn):
    give(conn, task_id=1, minutes=30)
    result = give(conn, task_id=2, minutes=20)

    assert result.balance.earned == 50
    assert result.balance.target == 15 + 50


def test_new_version_of_task_is_a_new_reward(conn):
    """Родитель исправил задание и поднял версию — это осознанная перевыдача."""
    give(conn, task_id=1, version=1, minutes=30)
    result = give(conn, task_id=1, version=2, minutes=30)

    assert result.granted is True
    assert result.balance.earned == 60


# --------------------------------------------------------------------------
# Сутки
# --------------------------------------------------------------------------


def test_yesterday_rewards_do_not_count_today(conn):
    give(conn, day=DAY, minutes=30)
    today = bank.balance(conn, 1, NEXT_DAY)

    assert today.earned == 0
    assert today.target == 15, "новый день начинается с базовой квоты"


def test_same_task_can_be_earned_again_next_day(conn):
    give(conn, day=DAY)
    result = give(conn, day=NEXT_DAY)

    assert result.granted is True
    assert result.balance.earned == 30


def test_today_uses_family_timezone():
    assert len(bank.today("Europe/Moscow")) == 10


# --------------------------------------------------------------------------
# Перезапуск сервера
# --------------------------------------------------------------------------


def test_balance_survives_restart(tmp_path):
    path = tmp_path / "restart.sqlite3"

    first = db.open_db(path)
    first.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'с','C','D')")
    first.execute("INSERT INTO task (id, kind, version, title, reward_minutes) "
                  "VALUES (1,'math',1,'t',30)")
    bank.mark_homework(first, child_id=1, day=DAY, at=AT)
    give(first, minutes=30)
    first.close()

    # Как будто процесс перезапустили: в памяти не осталось ничего.
    second = db.open_db(path)
    after = bank.balance(second, 1, DAY)
    second.close()

    assert after.earned == 30
    assert after.homework_bonus == 45, "цена домашки тоже читается из базы, не из памяти"
    assert after.target == 90, "15 + 45 + 30"



# --------------------------------------------------------------------------
# Цель выдачи
# --------------------------------------------------------------------------


def test_grant_queues_delivery(conn):
    give(conn, minutes=30)
    row = conn.execute("SELECT * FROM delivery WHERE child_id = 1 AND day = ?", (DAY,)).fetchone()

    assert row["target_minutes"] == 45
    assert row["status"] == "pending"


def test_confirmed_delivery_is_not_disturbed_by_an_unchanged_target(conn):
    give(conn, minutes=30)
    conn.execute("UPDATE delivery SET status='confirmed', applied_minutes=45 WHERE child_id=1")

    # Повторное начисление того же задания цель не меняет.
    give(conn, minutes=30)

    row = conn.execute("SELECT * FROM delivery WHERE child_id = 1").fetchone()
    assert row["status"] == "confirmed", "цель та же — статус сбрасывать нельзя"


def test_changed_target_returns_delivery_to_pending(conn):
    give(conn, task_id=1, minutes=30)
    conn.execute("UPDATE delivery SET status='confirmed', tries=3, applied_minutes=45")

    give(conn, task_id=2, minutes=20)

    row = conn.execute("SELECT * FROM delivery WHERE child_id = 1").fetchone()
    assert row["target_minutes"] == 65
    assert row["status"] == "pending"
    assert row["tries"] == 0, "счётчик попыток относится к конкретной цели"


def test_sync_target_follows_a_lowered_daily_max(conn):
    give(conn, minutes=30)
    bank.set_setting(conn, bank.SETTING_DAILY_MAX, "20")

    bal = bank.sync_target(conn, child_id=1, day=DAY, at=AT)
    row = conn.execute("SELECT * FROM delivery WHERE child_id = 1").fetchone()

    assert bal.target == 20
    assert row["target_minutes"] == 20


def test_cap_is_visible(conn):
    bank.set_setting(conn, bank.SETTING_DAILY_MAX, "40")
    result = give(conn, minutes=30)

    assert result.balance.target == 40
    assert result.balance.capped is True, "сайт должен честно сказать, что упёрлись в максимум"


# --------------------------------------------------------------------------
# Ворота домашней работы
# --------------------------------------------------------------------------


@pytest.fixture()
def gated(tmp_path):
    """Та же база, но домашка НЕ отмечена: ворота закрыты."""
    c = db.open_db(tmp_path / "gate.sqlite3")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'сын','C','D')")
    c.execute("INSERT INTO task (id, kind, version, title, reward_minutes) "
              "VALUES (1,'math',1,'Примеры',30)")
    # Здесь проверяются именно ворота, без цены домашки — она ниже отдельно.
    bank.set_setting(c, bank.SETTING_HOMEWORK_MINUTES, "0")
    yield c
    c.close()


def test_without_homework_only_the_base_is_given(gated):
    result = give(gated, minutes=30)

    assert result.granted is True, "награда начисляется — ворота не мешают заработать"
    assert result.balance.earned == 30, "в журнале она есть"
    assert result.balance.target == 15, "но в квоту не попадает"
    assert result.balance.locked == 30, "и это видно отдельным числом"


def test_marking_homework_releases_what_was_earned(gated):
    give(gated, minutes=30)
    bal = bank.mark_homework(gated, child_id=1, day=DAY, at=AT)

    assert bal.target == 45
    assert bal.locked == 0


def test_order_does_not_matter(gated):
    """Сделал тетрадь раньше сайта — результат тот же."""
    bank.mark_homework(gated, child_id=1, day=DAY, at=AT)
    result = give(gated, minutes=30)

    assert result.balance.target == 45


def test_nothing_assigned_also_opens_the_gate(gated):
    """Выходной без домашки не должен запирать день."""
    give(gated, minutes=30)
    bal = bank.mark_homework(gated, child_id=1, day=DAY, at=AT, marked_as="nothing_assigned")

    assert bal.target == 45


def test_revoking_locks_the_time_again_without_erasing_the_reward(gated):
    give(gated, minutes=30)
    bank.mark_homework(gated, child_id=1, day=DAY, at=AT)

    bal = bank.revoke_homework(gated, child_id=1, day=DAY, at=AT, reason="тетрадь пустая")

    assert bal.target == 15, "время снова заперто"
    assert bal.earned == 30, "награда честно заработана и остаётся в журнале"
    assert bal.locked == 30


def test_marking_again_after_a_revocation_works(gated):
    give(gated, minutes=30)
    bank.mark_homework(gated, child_id=1, day=DAY, at=AT)
    bank.revoke_homework(gated, child_id=1, day=DAY, at=AT)

    bal = bank.mark_homework(gated, child_id=1, day=DAY, at=AT)
    assert bal.target == 45


def test_revocation_is_recorded_rather_than_deleted(gated):
    bank.mark_homework(gated, child_id=1, day=DAY, at=AT)
    bank.revoke_homework(gated, child_id=1, day=DAY, at=AT, reason="проверил, не сделано")

    row = gated.execute("SELECT * FROM homework WHERE child_id = 1 AND day = ?", (DAY,)).fetchone()
    assert row["revoked_at"] is not None
    assert row["revoked_reason"] == "проверил, не сделано"


def test_marking_twice_is_harmless(gated):
    bank.mark_homework(gated, child_id=1, day=DAY, at=AT)
    bank.mark_homework(gated, child_id=1, day=DAY, at=AT)

    count = gated.execute("SELECT COUNT(*) c FROM homework WHERE child_id = 1").fetchone()["c"]
    assert count == 1


def test_gate_can_be_switched_off_for_the_holidays(gated):
    bank.set_setting(gated, bank.SETTING_REQUIRE_HOMEWORK, "0")
    result = give(gated, minutes=30)

    assert result.balance.target == 45
    assert result.balance.gate_open is True
    assert result.balance.locked == 0


def test_homework_of_another_day_does_not_open_today(gated):
    bank.mark_homework(gated, child_id=1, day=DAY, at=AT)
    give(gated, day=NEXT_DAY, minutes=30)

    tomorrow = bank.balance(gated, 1, NEXT_DAY)
    assert tomorrow.target == 15, "домашку надо отмечать каждый день заново"


def test_gate_change_updates_the_delivery_target(gated):
    give(gated, minutes=30)
    before = gated.execute("SELECT target_minutes FROM delivery WHERE child_id = 1").fetchone()
    bank.mark_homework(gated, child_id=1, day=DAY, at=AT)
    after = gated.execute("SELECT target_minutes FROM delivery WHERE child_id = 1").fetchone()

    assert before["target_minutes"] == 15
    assert after["target_minutes"] == 45, "отметка ДЗ обязана дойти до выдачи"


# --------------------------------------------------------------------------
# Домашка стоит минут сама по себе
# --------------------------------------------------------------------------


@pytest.fixture()
def paid(tmp_path):
    """База с ценой домашки по умолчанию (45) и закрытыми воротами."""
    c = db.open_db(tmp_path / "paid.sqlite3")
    c.execute("INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1,'сын','C','D')")
    c.execute("INSERT INTO task (id, kind, version, title, reward_minutes) "
              "VALUES (1,'math',1,'Примеры',30)")
    yield c
    c.close()


def test_homework_done_is_worth_45_on_its_own(paid):
    """Отметил «сделал» — сразу 15 + 45 = 60, без единого задания на сайте."""
    bal = bank.mark_homework(paid, child_id=1, day=DAY, at=AT)

    assert bal.homework_bonus == 45
    assert bal.earned == 0
    assert bal.target == 60


def test_homework_bonus_is_not_a_reward_row(paid):
    """Цена домашки считается из отметки, а не из журнала наград."""
    bank.mark_homework(paid, child_id=1, day=DAY, at=AT)
    count = paid.execute("SELECT COUNT(*) c FROM reward").fetchone()["c"]
    assert count == 0


def test_nothing_assigned_opens_the_gate_but_pays_nothing(paid):
    give(paid, minutes=30)
    bal = bank.mark_homework(paid, child_id=1, day=DAY, at=AT, marked_as="nothing_assigned")

    assert bal.homework_bonus == 0
    assert bal.target == 45, "15 базовых + 30 за задания, домашки не было — платить не за что"


def test_tasks_stack_on_top_of_homework_up_to_the_cap(paid):
    bank.mark_homework(paid, child_id=1, day=DAY, at=AT)
    result = give(paid, minutes=30)

    assert result.balance.target == 90, "15 + 45 + 30"
    assert result.balance.capped is False

    paid.execute("INSERT INTO task (id, kind, version, title, reward_minutes) "
                 "VALUES (2,'words',1,'Слова',40)")
    result = bank.grant(paid, child_id=1, task_id=2, task_version=1, day=DAY,
                        minutes=40, attempt_id=None, at=AT)
    assert result.balance.target == 120, "дальше максимума не растёт"
    assert result.balance.capped is True


def test_a_full_day_fits_the_maximum(paid):
    """База, домашка и все пять заданий дня (50 мин) влезают в 120."""
    bank.mark_homework(paid, child_id=1, day=DAY, at=AT)
    result = give(paid, minutes=50)
    assert result.balance.target == 110
    assert result.balance.capped is False


def test_revoking_homework_takes_its_minutes_back(paid):
    give(paid, minutes=30)
    bank.mark_homework(paid, child_id=1, day=DAY, at=AT)
    bal = bank.revoke_homework(paid, child_id=1, day=DAY, at=AT, reason="тетрадь пустая")

    assert bal.homework_bonus == 0
    assert bal.target == 15, "и цена домашки, и заработанное заперты"
    assert bal.earned == 30, "награды за задания в журнале целы"


def test_homework_price_is_a_setting(paid):
    bank.set_setting(paid, bank.SETTING_HOMEWORK_MINUTES, "20")
    bal = bank.mark_homework(paid, child_id=1, day=DAY, at=AT)
    assert bal.target == 35
    assert bal.homework_minutes == 20


def test_homework_photo_is_kept_with_the_mark(paid):
    bank.mark_homework(paid, child_id=1, day=DAY, at=AT, photo_file="2026-09-10-1-180000.jpg")
    row = paid.execute("SELECT photo_file, marked_as FROM homework WHERE child_id = 1").fetchone()
    assert row["photo_file"] == "2026-09-10-1-180000.jpg"

    # Отозвали, отметили заново с другим снимком — хранится новый.
    bank.revoke_homework(paid, child_id=1, day=DAY, at=AT)
    bank.mark_homework(paid, child_id=1, day=DAY, at=AT, photo_file="2026-09-10-1-190000.jpg")
    row = paid.execute("SELECT photo_file, revoked_at FROM homework WHERE child_id = 1").fetchone()
    assert row["photo_file"] == "2026-09-10-1-190000.jpg"
    assert row["revoked_at"] is None


def test_photo_column_is_added_to_an_old_base(tmp_path):
    """База, созданная до появления снимков, получает колонку при открытии."""
    path = tmp_path / "old.sqlite3"
    c = db.connect(path)
    c.executescript(db.SCHEMA.replace(
        "    photo_file     TEXT    NOT NULL DEFAULT '',   -- снимок тетради, имя файла в appdata/homework\n", ""))
    assert "photo_file" not in {r["name"] for r in c.execute("PRAGMA table_info(homework)")}
    c.close()

    c = db.open_db(path)
    assert "photo_file" in {r["name"] for r in c.execute("PRAGMA table_info(homework)")}
    c.close()


# --------------------------------------------------------------------------
# Минуты от родителя
# --------------------------------------------------------------------------


def test_parent_grant_enters_the_quota_without_waiting_for_homework(paid):
    """Решение родителя выполняется сразу: ворота домашки на него не действуют."""
    bank.grant(paid, child_id=1, task_id=1, task_version=1, day=DAY, minutes=30, attempt_id=None, at=AT)
    before = bank.balance(paid, 1, DAY)
    assert before.target == 15 and before.locked == 30

    bal = bank.parent_grant(paid, child_id=1, day=DAY, minutes=20, reason="камера не открылась", at=AT)
    assert bal.granted == 20
    assert bal.target == 35, "база 15 + 20 от папы; заработанные 30 ждут домашку"
    row = paid.execute("SELECT target_minutes, status FROM delivery WHERE day = ?", (DAY,)).fetchone()
    assert (row["target_minutes"], row["status"]) == (35, "pending")

    bank.mark_homework(paid, child_id=1, day=DAY, at=AT)
    assert bank.balance(paid, 1, DAY).target == 15 + 45 + 30 + 20


def test_parent_grant_is_capped_by_the_daily_maximum_and_validated(paid):
    bank.parent_grant(paid, child_id=1, day=DAY, minutes=60, reason="", at=AT)
    bank.parent_grant(paid, child_id=1, day=DAY, minutes=60, reason="", at=AT)
    bal = bank.balance(paid, 1, DAY)
    assert bal.granted == 120 and bal.target == 120 and bal.capped
    for bad in (0, 3, 7, 125, -5):
        with pytest.raises(ValueError):
            bank.parent_grant(paid, child_id=1, day=DAY, minutes=bad, reason="", at=AT)
    assert bank.balance(paid, 1, DAY).granted == 120, "неверные суммы не записываются"
    # соседний день не затронут
    assert bank.balance(paid, 1, NEXT_DAY).granted == 0
