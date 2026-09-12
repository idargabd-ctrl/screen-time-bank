# -*- coding: utf-8 -*-
"""
Фоновый процесс выдачи времени.

Забирает цель из таблицы `delivery` и ставит её в Family Link. Это то самое
звено, которого не хватало: журнал наград велся правильно, цель считалась
правильно, а до планшета ничего не доезжало.

Запуск:
    python -m app.worker              бесконечный цикл
    python -m app.worker --once       один проход и выход

Что он делает каждый проход:

1. Пересчитывает цель из журнала. Это нужно не только после начисления: сервер
   мог быть выключен в полночь, и тогда цель за новый день ещё не создана.
2. Читает, что реально стоит в Family Link.
3. Решает, писать или не трогать (app/delivery.py — там же тесты).
4. Записав, ПЕРЕЧИТЫВАЕТ значение: успешный ответ Google ничего не доказывает.

Обрабатывается только сегодняшний день. Вчерашние строки не трогаем: их цель
уже неактуальна, а повторная запись задним числом ничего не исправит.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time

from app import bank, config, db, delivery, familylink

INTERVAL = 20            # пауза между проходами, секунды
AFTER_WRITE_PAUSE = 8    # ждём, пока Google применит запись, прежде чем читать

# Сколько неудачных ЗАПИСЕЙ терпим, прежде чем перестать долбить Google.
# Именно записей: неудачное чтение — это чаще всего обрыв сети, и оно не
# должно расходовать попытки. Первая ночь это и показала: хотспот пропал,
# шесть чтений подряд не удались, счётчик кончился — и когда сеть вернулась
# утром, worker так и стоял. Обрыв сети — повод подождать, а не сдаться.
MAX_TRIES = 6


def log(*parts) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}]", *parts, flush=True)


def children(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, fl_child_id, fl_device_id FROM child "
        " WHERE fl_child_id <> '' AND fl_device_id <> ''"
    ).fetchall()


def row_for(conn: sqlite3.Connection, child_id: int, day: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM delivery WHERE child_id = ? AND day = ?", (child_id, day)
    ).fetchone()


def save(conn: sqlite3.Connection, *, child_id: int, day: str, status: str,
         applied: int | None, error: str, bump: bool, at: str,
         manual: int | None = None) -> None:
    with conn:
        conn.execute(
            "UPDATE delivery SET status = ?, applied_minutes = ?, last_error = ?, "
            "       tries = tries + ?, updated_at = ?, "
            "       manual_minutes = COALESCE(?, manual_minutes) "
            " WHERE child_id = ? AND day = ?",
            (status, applied, error, 1 if bump else 0, at, manual, child_id, day),
        )


# Когда всё подтверждено, лимит всё равно перечитывается — но реже: так
# замечается ручная надбавка родителя, даже если сын в это время ничего не
# решает. Ключ — ребёнок, значение — время последней проверки.
_last_manual_check: dict[int, float] = {}
MANUAL_CHECK_EVERY = 300   # секунд


def handle(conn: sqlite3.Connection, fl: familylink.FamilyLink,
           child: sqlite3.Row, cfg, day: str) -> None:
    at = bank.now(cfg.timezone).isoformat(timespec="seconds")

    # Цель всегда пересчитывается из журнала: в памяти процесса ничего не живёт.
    balance = bank.sync_target(conn, child_id=child["id"], day=day, at=at)
    row = row_for(conn, child["id"], day)
    if row is None:
        return

    manual = int(row["manual_minutes"] or 0)
    wanted = delivery.wanted_for(target=row["target_minutes"], base=balance.base, manual=manual)
    settled = row["status"] == delivery.CONFIRMED and row["applied_minutes"] == wanted
    if settled:
        # Нечего выдавать. Ручную правку всё же ищем — раз в несколько минут.
        if time.monotonic() - _last_manual_check.get(child["id"], 0.0) < MANUAL_CHECK_EVERY:
            return
    _last_manual_check[child["id"]] = time.monotonic()

    if row["tries"] >= MAX_TRIES:
        return   # перестаём долбить: нужна человеческая помощь

    actual = fl.read_daily_limit(device_id=child["fl_device_id"])
    decision = delivery.decide(
        actual=actual,
        target=row["target_minutes"],
        applied=row["applied_minutes"],
        base=balance.base,
        manual=manual,
    )
    if decision.manual_changed:
        log(f"{child['name']}: РУЧНАЯ ПРАВКА — {decision.reason}")

    if decision.action == delivery.UNKNOWN:
        # Не читается — скорее всего нет сети. Попытки не расходуем, чтобы
        # после восстановления работа продолжилась сама. Пишем в лог один раз
        # при переходе в это состояние, а не каждые двадцать секунд.
        if row["status"] != delivery.STATUS_UNKNOWN:
            log(f"{child['name']}: {decision.reason} — жду, попытки не трачу")
        save(conn, child_id=child["id"], day=day, status=delivery.STATUS_UNKNOWN,
             applied=row["applied_minutes"], error=decision.reason, bump=False, at=at)
        return

    if decision.action == delivery.CONFIRM:
        if row["status"] != delivery.CONFIRMED:
            back = " (связь восстановилась)" if row["status"] == delivery.STATUS_UNKNOWN else ""
            log(f"{child['name']}: {decision.reason}{back}")
        save(conn, child_id=child["id"], day=day, status=delivery.CONFIRMED,
             applied=actual, error="", bump=False, at=at, manual=decision.manual)
        return

    # Пишем. Абсолютное значение: цель из журнала плюс ручная надбавка дня.
    if not decision.manual_changed:
        log(f"{child['name']}: {decision.reason}")
    try:
        fl.write_daily_limit(child_id=child["fl_child_id"],
                             device_id=child["fl_device_id"],
                             minutes=decision.wanted)
    except (familylink.FamilyLinkError, ValueError) as exc:
        log(f"{child['name']}: запись не удалась — {exc}")
        save(conn, child_id=child["id"], day=day, status=delivery.STATUS_UNKNOWN,
             applied=row["applied_minutes"], error=str(exc)[:300], bump=True, at=at,
             manual=decision.manual)
        return

    time.sleep(AFTER_WRITE_PAUSE)
    again = fl.read_daily_limit(device_id=child["fl_device_id"])
    status, error = delivery.verdict_after_write(actual=again, target=decision.wanted)

    if status == delivery.CONFIRMED:
        log(f"{child['name']}: подтверждено {again} мин")
        save(conn, child_id=child["id"], day=day, status=status,
             applied=again, error="", bump=False, at=at, manual=decision.manual)
    else:
        log(f"{child['name']}: {error}")
        save(conn, child_id=child["id"], day=day, status=status,
             applied=row["applied_minutes"], error=error, bump=True, at=at,
             manual=decision.manual)


def cycle(conn: sqlite3.Connection, fl: familylink.FamilyLink, cfg) -> None:
    day = bank.today(cfg.timezone)
    for child in children(conn):
        try:
            handle(conn, fl, child, cfg, day)
        except Exception as exc:                      # noqa: BLE001
            # Один сбойный ребёнок не должен останавливать процесс: иначе
            # временная ошибка превращается в остановку выдачи до перезапуска.
            log(f"{child['name']}: непредвиденная ошибка — {exc}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Выдача заработанного времени.")
    parser.add_argument("--once", action="store_true", help="один проход и выход")
    parser.add_argument("--interval", type=int, default=INTERVAL,
                        help=f"пауза между проходами, секунды (по умолчанию {INTERVAL})")
    args = parser.parse_args()

    cfg = config.load()
    fl = familylink.FamilyLink(cfg.ha_url, cfg.ha_token)

    if not fl.configured:
        log("В .env не заполнены HA_URL или HA_TOKEN — выдавать время нечем.")
        return 1

    conn = db.open_db(cfg.db_path)
    log(f"Запущен. Часовой пояс {cfg.timezone}, база {cfg.db_path}")

    if args.once:
        cycle(conn, fl, cfg)
        conn.close()
        return 0

    while True:
        cycle(conn, fl, cfg)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
