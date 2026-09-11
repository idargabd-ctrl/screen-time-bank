#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Выкладывает проект на сервер: перенос файлов, пересборка, перезапуск, каталог.

Запускается на РАБОЧЕМ ноутбуке из корня проекта:

    python tools/deploy.py              всё целиком
    python tools/deploy.py --no-build   только файлы и каталог, без пересборки
    python tools/deploy.py --check      ничего не менять, показать состояние

Зачем отдельный скрипт. Код приложения лежит ВНУТРИ образа: в Dockerfile он
копируется, а не монтируется. Значит `docker compose up -d` после правки кода
поднимает контейнер со старой версией — и это уже случилось один раз, когда
маршрут раздачи видео не появился, хотя файл на сервере был правильный.
Симптом при этом выглядит как ошибка в коде, а не как забытая пересборка.

Нужен только ssh с настроенным алиасом agent. Сторонних библиотек нет.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = "agent"
REMOTE = "child-mind"

# Сервисы, собираемые из Dockerfile. Их код живёт в образе, и после правки
# каждый надо пересобрать и пересоздать — иначе контейнер молча работает на
# старой версии.
APP_SERVICES = ("web", "worker")

# Чем проверять, что образ свежий: по файлу, который сервис реально исполняет.
APP_CHECKS = (
    ("web", "uroki-web", "web.py"),
    ("worker", "uroki-worker", "worker.py"),
)

# Что НЕ переносим. Секреты, база, видео и снимки живут только на сервере:
# перенос затёр бы их локальными или пустыми версиями.
EXCLUDES = [".git", "data", "snapshots", ".env", "__pycache__", "appdata", "media"]


def say(*parts: str) -> None:
    print(*parts, flush=True)


def remote(command: str, quiet: bool = False) -> int:
    result = subprocess.run(["ssh", HOST, command], capture_output=quiet, text=True)
    return result.returncode


def remote_out(command: str) -> str:
    result = subprocess.run(["ssh", HOST, command], capture_output=True, text=True)
    return (result.stdout or "").strip()


def sync() -> bool:
    """Переносит рабочее дерево на сервер одним потоком через tar."""
    excludes = " ".join(f"--exclude={e}" for e in EXCLUDES)
    pipeline = (
        f'tar {excludes} -czf - . | '
        f'ssh {HOST} "mkdir -p {REMOTE} && tar xzf - -C {REMOTE}"'
    )
    return subprocess.run(pipeline, shell=True, cwd=ROOT).returncode == 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Выкладка проекта на сервер.")
    parser.add_argument("--no-build", action="store_true",
                        help="не пересобирать образ (только если код не менялся)")
    parser.add_argument("--check", action="store_true",
                        help="ничего не менять, показать состояние сервера")
    args = parser.parse_args()

    if args.check:
        say("=== контейнеры ===")
        remote(f"cd {REMOTE} && docker compose ps --format 'table {{{{.Service}}}}\\t{{{{.Status}}}}'")
        say("")
        say("=== код в образах совпадает с сервером? ===")
        stale = []
        for service, container, probe in APP_CHECKS:
            on_host = remote_out(f"md5sum {REMOTE}/app/{probe} | cut -d' ' -f1")
            in_image = remote_out(f"docker exec {container} md5sum /app/app/{probe} | cut -d' ' -f1")
            fresh = bool(on_host) and on_host == in_image
            say(f"  {service:8} {'свежий' if fresh else 'ОТСТАЁТ'}")
            if not fresh:
                stale.append(service)
        if stale:
            say(f"  Нужна выкладка: {', '.join(stale)}")
        return 0

    say("1. Переношу файлы")
    if not sync():
        say("\nПеренос не удался. Проверьте: ssh agent\n")
        return 1

    if args.no_build:
        say("2. Пересборку пропускаю по ключу --no-build")
    else:
        say("2. Пересобираю образ")
        # Оба сервиса приложения собираются из одного Dockerfile, и оба копируют
        # код внутрь. Пересобирать только web — значит оставить worker на старом
        # коде и не заметить: он не падает, он просто делает не то. Так и вышло
        # в первый раз.
        if remote(f"cd {REMOTE} && docker compose build -q {' '.join(APP_SERVICES)}") != 0:
            say("\nСборка не удалась.\n")
            return 1

        say("3. Перезапускаю")
        if remote(f"cd {REMOTE} && docker compose up -d {' '.join(APP_SERVICES)}") != 0:
            say("\nЗапуск не удался.\n")
            return 1

    say("4. Загружаю каталог заданий")
    if remote(f"cd {REMOTE} && python3 tools/load_content.py") != 0:
        say("\nКаталог не загрузился — смотрите сообщение выше.\n")
        return 1

    say("")
    say("=== состояние ===")
    remote(f"cd {REMOTE} && docker compose ps --format 'table {{{{.Service}}}}\\t{{{{.Status}}}}'")

    stale = []
    for service, container, probe in APP_CHECKS:
        on_host = remote_out(f"md5sum {REMOTE}/app/{probe} | cut -d' ' -f1")
        in_image = remote_out(f"docker exec {container} md5sum /app/app/{probe} | cut -d' ' -f1")
        mark = "совпадает" if on_host and on_host == in_image else "ОТСТАЁТ"
        say(f"  {service:8} {probe:12} {mark}")
        if mark != "совпадает":
            stale.append(service)
    say("")
    if not stale:
        say("Код во всех контейнерах совпадает с файлами на сервере.")
    else:
        say(f"ВНИМАНИЕ: отстаёт код в {', '.join(stale)}. Запустите без --no-build.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
