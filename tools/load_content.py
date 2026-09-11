#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Загружает каталог заданий из файлов content/*.json в базу.

Запуск на сервере из корня проекта:
    python3 tools/load_content.py              все файлы из content/
    python3 tools/load_content.py --dry-run    показать, что изменится
    python3 tools/load_content.py --file content/vpr4-math.json

Повторный запуск безопасен: задания опознаются по slug и обновляются, а не
дублируются.

Про версии. Версия задания входит в ключ идемпотентности награды. Если
поправить вопросы и НЕ поднять version, ребёнок не сможет пройти исправленное
задание в тот же день — награда за него уже в журнале. Скрипт это проверяет и
громко предупреждает: молча оставить такое расхождение хуже, чем отказаться
загружать.

Сторонних библиотек не нужно.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, db, tasks  # noqa: E402

CONTENT_DIR = ROOT / "content"


def say(*parts: str) -> None:
    print(*parts)


def fail(message: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"\nОШИБКА: {message}\n", file=sys.stderr)
    raise SystemExit(1)


def validate(task: dict, where: str) -> list[str]:
    """Проверяет задание до записи в базу. Возвращает список проблем."""
    problems = []
    slug = task.get("slug") or "<без slug>"

    for field in ("slug", "kind", "title", "reward_minutes"):
        if not task.get(field):
            problems.append(f"{where}: у «{slug}» не заполнено поле {field}")

    kind = task.get("kind")
    if kind and kind not in tasks.KINDS:
        problems.append(f"{where}: у «{slug}» неизвестный тип «{kind}»")

    payload = task.get("payload") or {}
    try:
        questions = tasks.generate(kind, payload, seed=1) if kind in tasks.KINDS else []
    except Exception as exc:
        problems.append(f"{where}: у «{slug}» не удалось собрать вопросы: {exc}")
        return problems

    if not questions:
        problems.append(f"{where}: у «{slug}» не получилось ни одного вопроса")

    # Пустой ответ означает задание, которое нельзя пройти никогда.
    for i, q in enumerate(questions, start=1):
        if not q.answer.strip():
            problems.append(f"{where}: у «{slug}» пустой ответ в вопросе {i}")

    ratio = payload.get("pass_ratio")
    if ratio is not None and not (0 < float(ratio) <= 1):
        problems.append(f"{where}: у «{slug}» pass_ratio вне диапазона: {ratio}")

    return problems


def load_file(conn, path: Path, dry_run: bool) -> tuple[int, int, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    subject = data.get("subject", "")
    source = data.get("source", "")
    # Раздел задаётся на уровне файла: миссии и разборы ВПР лежат врозь.
    section = data.get("section", "vpr")
    items = data.get("tasks") or []

    problems: list[str] = []
    slugs = [t.get("slug") for t in items]
    duplicates = {s for s in slugs if s and slugs.count(s) > 1}
    if duplicates:
        problems.append(f"{path.name}: slug повторяется внутри файла: {sorted(duplicates)}")

    for task in items:
        problems.extend(validate(task, path.name))
    if problems:
        return 0, 0, problems

    added = updated = 0
    for task in items:
        payload_text = json.dumps(task.get("payload") or {}, ensure_ascii=False, sort_keys=True)
        existing = conn.execute(
            "SELECT id, version, payload, title, video_file, section FROM task WHERE slug = ?", (task["slug"],)
        ).fetchone()

        if existing is None:
            say(f"  + {task['slug']}  {task['title']}")
            if not dry_run:
                conn.execute(
                    "INSERT INTO task (slug, kind, version, title, subject, source, "
                    "                  section, video_file, payload, reward_minutes, active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (task["slug"], task["kind"], int(task.get("version", 1)), task["title"],
                     subject, source, task.get("section", section),
                     task.get("video_file", ""), payload_text,
                     int(task["reward_minutes"])),
                )
            added += 1
            continue

        new_version = int(task.get("version", 1))
        changed = existing["payload"] != payload_text or existing["title"] != task["title"]
        # Привязку ролика можно менять без поднятия версии: вопросы те же.
        video_changed = ((existing["video_file"] or "") != task.get("video_file", "")
                         or (existing["section"] or "vpr") != task.get("section", section))

        if changed and new_version == existing["version"]:
            problems.append(
                f"{path.name}: «{task['slug']}» изменился, но version остался "
                f"{new_version}. Поднимите version в файле — иначе исправленное "
                f"задание нельзя будет пройти заново в тот же день."
            )
            continue

        if not changed and new_version == existing["version"]:
            if video_changed:
                say(f"  ~ {task['slug']}  привязан ролик")
                if not dry_run:
                    conn.execute("UPDATE task SET video_file = ?, section = ? WHERE slug = ?",
                                 (task.get("video_file", ""),
                                  task.get("section", section), task["slug"]))
                updated += 1
            continue

        say(f"  ~ {task['slug']}  версия {existing['version']} -> {new_version}")
        if not dry_run:
            conn.execute(
                "UPDATE task SET kind = ?, version = ?, title = ?, subject = ?, source = ?, "
                "                section = ?, video_file = ?, payload = ?, "
                "                reward_minutes = ? WHERE slug = ?",
                (task["kind"], new_version, task["title"], subject, source,
                 task.get("section", section), task.get("video_file", ""), payload_text,
                 int(task["reward_minutes"]), task["slug"]),
            )
        updated += 1

    return added, updated, problems


def ensure_child(conn, cfg: config.Config, dry_run: bool) -> None:
    row = conn.execute("SELECT id, name FROM child ORDER BY id LIMIT 1").fetchone()
    if row is not None:
        say(f"Ребёнок уже есть: {row['name']} (id {row['id']})")
        return

    if not cfg.fl_child_id or not cfg.fl_device_id:
        fail(
            "В базе нет ребёнка, а в .env не заполнены FL_CHILD_ID и "
            "FL_TABLET_DEVICE_ID.\nСначала: python3 tools/fl.py discover"
        )

    say("Создаю ребёнка из .env")
    if not dry_run:
        conn.execute(
            "INSERT INTO child (id, name, fl_child_id, fl_device_id) VALUES (1, ?, ?, ?)",
            (cfg.child_name, cfg.fl_child_id, cfg.fl_device_id),
        )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Загрузка каталога заданий в базу.")
    parser.add_argument("--file", help="конкретный файл вместо всей папки content/")
    parser.add_argument("--dry-run", action="store_true", help="только показать изменения")
    args = parser.parse_args()

    files = [Path(args.file)] if args.file else sorted(CONTENT_DIR.glob("*.json"))
    if not files:
        fail(f"Не найдено ни одного файла в {CONTENT_DIR}")

    cfg = config.load()
    conn = db.open_db(cfg.db_path)
    say(f"База: {cfg.db_path}")
    say("")

    ensure_child(conn, cfg, args.dry_run)
    say("")

    total_added = total_updated = 0
    all_problems: list[str] = []
    for path in files:
        say(f"{path.name}")
        added, updated, problems = load_file(conn, path, args.dry_run)
        total_added += added
        total_updated += updated
        all_problems.extend(problems)
        if not added and not updated and not problems:
            say("  без изменений")
        say("")

    if all_problems:
        say("=" * 70)
        say("НЕ ЗАГРУЖЕНО, есть проблемы:")
        for p in all_problems:
            say(f"  - {p}")
        say("=" * 70)
        conn.close()
        return 1

    if args.dry_run:
        say(f"Пробный запуск: добавилось бы {total_added}, обновилось бы {total_updated}")
    else:
        say(f"Готово. Добавлено: {total_added}, обновлено: {total_updated}")
        total = conn.execute("SELECT COUNT(*) c FROM task WHERE active = 1").fetchone()["c"]
        minutes = conn.execute(
            "SELECT COALESCE(SUM(reward_minutes), 0) m FROM task WHERE active = 1"
        ).fetchone()["m"]
        say(f"Активных заданий в каталоге: {total}, суммарная награда: {minutes} мин")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
