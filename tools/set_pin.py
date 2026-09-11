#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Задаёт код входа ребёнку — или пароль родительской страницы.

Запуск на сервере из корня проекта:
    python3 tools/set_pin.py             код ребёнка
    python3 tools/set_pin.py --parent    пароль родителя (страница /parent)

Код спрашивается интерактивно и не отображается при вводе. В базу пишется не
он сам, а результат pbkdf2 с солью: из базы код восстановить нельзя, и это
важно, потому что базу мы будем копировать в резервные копии.

Сторонних библиотек не нужно.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import auth, bank, config, db  # noqa: E402

MIN_LENGTH = 4
PARENT_MIN_LENGTH = 8


def ask_twice(min_length: int) -> str | None:
    print(f"Не отображается при вводе. Минимум {min_length} символов.")
    print()
    first = getpass.getpass("Новый: ")
    if len(first.strip()) < min_length:
        print(f"\nСлишком коротко: нужно минимум {min_length} символов.\n", file=sys.stderr)
        return None
    second = getpass.getpass("Ещё раз: ")
    if first != second:
        print("\nНе совпали. Ничего не изменено.\n", file=sys.stderr)
        return None
    return first.strip()


def set_parent(conn) -> int:
    """Пароль родителя хранится в настройках, так же хэшем."""
    already = " (уже задан, будет заменён)" if bank.get_setting(conn, bank.SETTING_PARENT_PIN) else ""
    print(f"Пароль родительской страницы{already}.")
    password = ask_twice(PARENT_MIN_LENGTH)
    if password is None:
        return 1
    with conn:
        bank.set_setting(conn, bank.SETTING_PARENT_PIN, auth.make_pin_hash(password))
    print()
    print("Готово. Страница родителя: тот же адрес, что у сына, плюс /parent")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass

    cfg = config.load()
    conn = db.open_db(cfg.db_path)

    if "--parent" in sys.argv[1:]:
        code = set_parent(conn)
        conn.close()
        return code

    rows = conn.execute("SELECT id, name, pin_hash FROM child ORDER BY id").fetchall()
    if not rows:
        print("\nВ базе нет ребёнка. Сначала: python3 tools/load_content.py\n", file=sys.stderr)
        return 1

    if len(rows) == 1:
        child = rows[0]
    else:
        print("Кому задаём код:")
        for r in rows:
            print(f"  {r['id']}: {r['name']}")
        try:
            wanted = int(input("Номер: ").strip())
        except (ValueError, EOFError):
            print("\nОтменено.\n", file=sys.stderr)
            return 1
        child = next((r for r in rows if r["id"] == wanted), None)
        if child is None:
            print("\nТакого номера нет.\n", file=sys.stderr)
            return 1

    already = " (код уже задан, будет заменён)" if child["pin_hash"] else ""
    print(f"Ребёнок: {child['name']}{already}")
    print(f"Код не отображается при вводе. Минимум {MIN_LENGTH} символа.")
    print()

    first = getpass.getpass("Новый код: ")
    if len(first.strip()) < MIN_LENGTH:
        print(f"\nСлишком короткий код: нужно минимум {MIN_LENGTH} символа.\n", file=sys.stderr)
        return 1

    second = getpass.getpass("Ещё раз:   ")
    if first != second:
        print("\nКоды не совпали. Ничего не изменено.\n", file=sys.stderr)
        return 1

    conn.execute(
        "UPDATE child SET pin_hash = ? WHERE id = ?",
        (auth.make_pin_hash(first.strip()), child["id"]),
    )
    conn.close()

    print()
    print(f"Готово. Код для {child['name']} задан.")
    print("Проверить можно на самом сайте — введя его в поле входа.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
