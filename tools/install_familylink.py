#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Устанавливает интеграцию HAFamilyLink в Home Assistant.

Home Assistant не умеет ставить сторонние интеграции сам: нужно положить папку
с кодом в его конфигурацию. Обычно для этого ставят магазин дополнений HACS,
но это лишний шаг. Скрипт делает то же самое напрямую.

Файлы кладутся ЧЕРЕЗ КОНТЕЙНЕР, а не в каталог на диске. Причина: каталог
конфигурации создан Docker от имени root, и обычный пользователь в него писать
не может. Внутри контейнера Home Assistant работает от root, поэтому docker cp
проходит без sudo — достаточно членства в группе docker.

Запуск из корня проекта на сервере:
    python3 tools/install_familylink.py

Обновление на свежую версию — та же команда, старая папка заменяется.
Сторонних библиотек не нужно.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

CONTAINER = "homeassistant"
DEST = "/config/custom_components/familylink"
ZIP_URL = "https://github.com/noiwid/HAFamilyLink/archive/refs/heads/main.zip"
INNER = "custom_components/familylink/"


def die(message: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    print(f"\n{message}\n", file=sys.stderr)
    raise SystemExit(code)


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass

    if not shutil.which("docker"):
        die("Docker не найден. Установите его:  sudo bash server/install-docker.sh")

    try:
        running = docker("ps", "--filter", f"name=^{CONTAINER}$", "--format", "{{.Names}}")
    except subprocess.CalledProcessError as exc:
        if "permission denied" in (exc.stderr or "").lower():
            die(
                "Нет прав на docker.\n"
                "Вы в группе docker, но сессия старая. Переподключитесь:\n"
                "    exit\n"
                "    ssh agent"
            )
        die(f"Docker не отвечает:\n{exc.stderr}")

    if CONTAINER not in running.stdout:
        die(
            f"Контейнер {CONTAINER} не запущен. Поднимите стек:\n"
            "    cd ~/child-mind && docker compose up -d"
        )

    print("Скачиваю HAFamilyLink с GitHub...")
    try:
        with urllib.request.urlopen(ZIP_URL, timeout=120) as resp:
            blob = resp.read()
    except Exception as exc:
        die(f"Не получилось скачать: {exc}\nПроверьте сеть на сервере.")

    print(f"Скачано {len(blob) / 1024 / 1024:.1f} МБ. Распаковываю...")

    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        die("Скачался повреждённый архив. Попробуйте ещё раз.")

    names = archive.namelist()
    roots = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(roots) != 1:
        die(f"Неожиданная структура архива: {sorted(roots)}")
    prefix = f"{roots.pop()}/{INNER}"

    members = [n for n in names if n.startswith(prefix) and not n.endswith("/")]
    if not members:
        die(
            f"В архиве не найдена папка {INNER}.\n"
            "Возможно, проект переехал. Напишите мне — поправлю скрипт."
        )

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "familylink"
        staging.mkdir()

        for name in members:
            dest = staging / name[len(prefix):]
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)

        version = "неизвестна"
        manifest = staging / "manifest.json"
        if manifest.exists():
            try:
                version = json.loads(manifest.read_text(encoding="utf-8")).get("version", version)
            except Exception:
                pass

        print(f"Версия интеграции: {version}")
        print("Копирую в контейнер...")

        # Внутри контейнера всё делается от root, поэтому sudo не нужен.
        docker("exec", CONTAINER, "mkdir", "-p", "/config/custom_components")
        docker("exec", CONTAINER, "rm", "-rf", DEST)
        docker("cp", f"{staging}/.", f"{CONTAINER}:{DEST}")

    check = docker("exec", CONTAINER, "ls", f"{DEST}/manifest.json", check=False)
    if check.returncode != 0:
        die("Копирование не удалось: manifest.json в контейнере не найден.")

    print()
    print(f"Готово. Файлов: {len(members)}. Путь в контейнере: {DEST}")
    print()
    print("Дальше — перезапустите Home Assistant, чтобы он увидел интеграцию:")
    print("    cd ~/child-mind && docker compose restart homeassistant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
