#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Переносит видеофайлы уроков на сервер.

Запускается на РАБОЧЕМ ноутбуке, где лежат скачанные ролики, а не на сервере:

    python tools/upload_media.py "D:/Видео/ВПР"            показать, что будет
    python tools/upload_media.py "D:/Видео/ВПР" --apply     скопировать

Имена файлов приводятся к латинице без пробелов. Причина не в аккуратности:
имя попадает в адрес вида /media/имя.mp4, а кириллица и пробелы в адресах
кодируются по-разному разными браузерами, и на планшете это выливается в
«видео не найдено» без внятной причины.

В конце печатается таблица «файл на сервере -> исходное имя». По ней в
content/*.json проставляется поле video_file у нужного задания.

Нужен только ssh, он есть в Windows из коробки. Сторонних библиотек нет.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HOST = "agent"                       # алиас из ~/.ssh/config
REMOTE_DIR = "child-mind/media"
EXTENSIONS = {".mp4", ".m4v", ".webm", ".mkv"}

# Кириллица в имени файла ломает адрес, поэтому раскладываем в латиницу.
TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(name: str) -> str:
    text = name.lower()
    out = []
    for ch in text:
        if ch in TRANSLIT:
            out.append(TRANSLIT[ch])
        elif ch.isalnum():
            out.append(ch)
        else:
            out.append("-")
    slug = re.sub(r"-{2,}", "-", "".join(out)).strip("-")
    return slug or "video"


def unique(slug: str, taken: set[str]) -> str:
    if slug not in taken:
        return slug
    n = 2
    while f"{slug}-{n}" in taken:
        n += 1
    return f"{slug}-{n}"


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Перенос видеоуроков на сервер.")
    parser.add_argument("folder", help="папка со скачанными роликами")
    parser.add_argument("--apply", action="store_true", help="действительно скопировать")
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"\nНе папка: {folder}\n", file=sys.stderr)
        return 1

    files = sorted(p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in EXTENSIONS)
    if not files:
        print(f"\nВ {folder} нет видеофайлов ({', '.join(sorted(EXTENSIONS))})\n",
              file=sys.stderr)
        return 1

    taken: set[str] = set()
    plan: list[tuple[Path, str]] = []
    for path in files:
        target = unique(slugify(path.stem) + path.suffix.lower(), taken)
        taken.add(target)
        plan.append((path, target))

    total_mb = sum(p.stat().st_size for p, _ in plan) / 1024 / 1024
    print(f"Файлов: {len(plan)}, объём {total_mb:.0f} МБ")
    print(f"Куда: {HOST}:{REMOTE_DIR}/")
    print()

    if not args.apply:
        for path, target in plan:
            print(f"  {target}")
            print(f"      <- {path.name}")
        print()
        print("Это пробный запуск. Чтобы скопировать, добавьте --apply")
        return 0

    if run(["ssh", HOST, f"mkdir -p {REMOTE_DIR}"]).returncode != 0:
        print("\nНе удалось создать папку на сервере. Проверьте: ssh agent\n",
              file=sys.stderr)
        return 1

    ok = 0
    for i, (path, target) in enumerate(plan, start=1):
        size_mb = path.stat().st_size / 1024 / 1024
        print(f"  [{i}/{len(plan)}] {target}  ({size_mb:.1f} МБ)", flush=True)
        result = run(["scp", "-q", str(path), f"{HOST}:{REMOTE_DIR}/{target}"])
        if result.returncode != 0:
            print(f"      ОШИБКА: {result.stderr.strip()[:200]}", file=sys.stderr)
            continue
        ok += 1

    print()
    print(f"Скопировано: {ok} из {len(plan)}")
    print()
    print("=" * 70)
    print("Проставьте эти имена в content/*.json, поле video_file:")
    print("=" * 70)
    for path, target in plan:
        print(f'  "video_file": "{target}",')
        print(f"      {path.name}")
    return 0 if ok == len(plan) else 1


if __name__ == "__main__":
    sys.exit(main())
