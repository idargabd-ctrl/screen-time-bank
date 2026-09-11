#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fl.py — ручной стенд выдачи экранного времени. Этап 1.

Зачем он нужен. Прежде чем писать сайт с уроками, надо убедиться, что вся
цепочка вообще работает: наша команда -> Home Assistant -> Family Link ->
реальный планшет. Этот скрипт делает ровно то, что потом будет делать сервер,
но по вашей команде и с подробным выводом.

Сторонних библиотек не требуется, нужен только Python 3.9 или новее.

Команды (запускать из корня проекта):

    python tools/fl.py check
        Проверить, что мы вообще достучались до Home Assistant.

    python tools/fl.py discover
        Найти детей и устройства, показать их идентификаторы.
        Эти идентификаторы надо вписать в .env.

    python tools/fl.py snapshot
        Сохранить текущие настройки в файл. Сделайте ДО экспериментов:
        это ваша точка отката.

    python tools/fl.py show
        Показать текущее состояние: лимиты, экранное время, режимы.

    python tools/fl.py set-limit --minutes 20
        Поставить дневную квоту на СЕГОДНЯ (по умолчанию — на планшет).

    python tools/fl.py app --package com.android.chrome --mode unlimited
        Вывести приложение из общего лимита (или наоборот, вернуть в него).

    python tools/fl.py watch --minutes 10
        Следить за состоянием и печатать, что и когда изменилось.
        Главный инструмент этапа 1: показывает, через сколько секунд
        изменение реально доехало до Google.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
SNAPSHOT_DIR = ROOT / "snapshots"

# Как долго ждём ответа. Установка лимита у Google занимает около 10 секунд,
# поэтому запас большой.
TIMEOUT_READ = 30
TIMEOUT_WRITE = 90

# Режимы приложения задаются разными командами интеграции, а не одним числом.
# В её документации сказано, что set_app_daily_limit со значением -1 означает
# «следует общему лимиту устройства». Проверено на живом аккаунте: приложение,
# бывшее безлимитным, от -1 попадает в ЗАБЛОКИРОВАННЫЕ. Поэтому:
#   unlimited -> set_app_daily_limit -2
#   follow    -> unblock_app
#   blocked   -> block_app
#   N минут   -> set_app_daily_limit N


# --------------------------------------------------------------------------
# Настройки и связь с Home Assistant
# --------------------------------------------------------------------------


def load_env() -> dict[str, str]:
    """Читает .env. Формат простой: КЛЮЧ=значение, # — комментарий."""
    if not ENV_FILE.exists():
        die(
            f"Не найден файл {ENV_FILE}\n"
            "Скопируйте .env.example в .env и заполните его."
        )
    env: dict[str, str] = {}
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def die(message: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    print(f"\n{message}\n", file=sys.stderr)
    raise SystemExit(code)


class HA:
    """Тонкая обёртка над HTTP-интерфейсом Home Assistant."""

    def __init__(self, env: dict[str, str]):
        self.url = (env.get("HA_URL") or "").rstrip("/")
        self.token = env.get("HA_TOKEN") or ""
        if not self.url:
            die("В .env не заполнен HA_URL. Обычно это http://127.0.0.1:8123")
        if not self.token:
            die(
                "В .env не заполнен HA_TOKEN.\n"
                "Где взять: откройте http://127.0.0.1:8123 -> ваш профиль внизу слева\n"
                "-> вкладка «Безопасность» -> «Токены долгосрочного доступа»\n"
                "-> «Создать токен». Скопируйте его сразу: второй раз он не покажется."
            )

    def request(self, method: str, path: str, payload: Any = None, timeout: int = TIMEOUT_READ) -> str:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            if exc.code == 401:
                die("Home Assistant не принял токен. Создайте новый и впишите в .env (HA_TOKEN).")
            die(f"Home Assistant ответил ошибкой {exc.code} на {method} {path}:\n{detail}")
        except urllib.error.URLError as exc:
            die(
                f"Не удалось соединиться с Home Assistant по адресу {self.url}\n"
                f"Причина: {exc.reason}\n\n"
                "Проверьте, что контейнеры запущены:  docker compose ps\n"
                "и что в .env правильный HA_URL."
            )

    def states(self) -> list[dict]:
        return json.loads(self.request("GET", "/api/states"))

    def template(self, tpl: str) -> str:
        return self.request("POST", "/api/template", {"template": tpl})

    def call(self, domain: str, service: str, data: dict) -> str:
        return self.request(
            "POST", f"/api/services/{domain}/{service}", data, timeout=TIMEOUT_WRITE
        )


# --------------------------------------------------------------------------
# Поиск сущностей Family Link
# --------------------------------------------------------------------------


def familylink_states(ha: HA) -> list[dict]:
    """
    Возвращает только то, что создала интеграция familylink.

    Сначала спрашиваем Home Assistant напрямую. Если по какой-то причине не
    вышло — откатываемся на грубый признак: наличие child_id или device_id
    среди атрибутов.
    """
    wanted: set[str] = set()
    try:
        rendered = ha.template("{{ integration_entities('familylink') }}")
        parsed = ast.literal_eval(rendered.strip())
        if isinstance(parsed, (list, tuple)):
            wanted = {str(x) for x in parsed}
    except Exception:
        wanted = set()

    states = ha.states()
    if wanted:
        return [s for s in states if s["entity_id"] in wanted]

    return [
        s
        for s in states
        if isinstance(s.get("attributes"), dict)
        and ("child_id" in s["attributes"] or "device_id" in s["attributes"])
    ]


def collect_ids(states: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """
    Группирует сущности по child_id и device_id.

    Имя берётся из entity_id, а не из friendly_name. Причина практическая:
    friendly_name у сущности описывает саму сущность («mi pad School Time
    Active»), а не то, чьё это. В семье с двумя детскими профилями по такому
    названию невозможно понять, к кому относится идентификатор, — а ошибиться
    здесь означает отправить команду не тому ребёнку.
    """
    children: dict[str, dict] = {}
    devices: dict[str, dict] = {}

    for s in states:
        attrs = s.get("attributes") or {}
        slug = s["entity_id"].split(".", 1)[1]
        cid = attrs.get("child_id") or attrs.get("user_id")
        did = attrs.get("device_id")

        if cid:
            entry = children.setdefault(str(cid), {"names": set(), "count": 0})
            entry["count"] += 1
            # Имя профиля берём только у сущностей БЕЗ устройства: у остальных
            # в названии стоит планшет, и профиль получил бы его имя.
            if not did:
                entry["names"].add(slug)

        if did:
            entry = devices.setdefault(str(did), {"names": set(), "child": None, "count": 0})
            entry["names"].add(slug)
            entry["count"] += 1
            if cid:
                entry["child"] = str(cid)

    return children, devices


def label(slugs: set) -> str:
    """
    Общее начало у названий сущностей — это и есть имя владельца.

    Брать самое короткое название нельзя: у планшета есть и `mi_pad_ring`,
    и `mi_pad`, и «самым коротким» окажется обрубок вроде «mi».
    """
    if not slugs:
        return "?"
    items = sorted(slugs)
    first, last = items[0], items[-1]
    i = 0
    while i < min(len(first), len(last)) and first[i] == last[i]:
        i += 1
    prefix = first[:i].rstrip("_")
    if "_" in prefix and i < len(first):
        prefix = prefix.rsplit("_", 1)[0] if not first[i:].startswith("_") else prefix
    return (prefix or min(items, key=len)).replace("_", " ")


def short(value: Any, width: int = 46) -> str:
    text = str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


# --------------------------------------------------------------------------
# Команды
# --------------------------------------------------------------------------


def cmd_check(ha: HA, env: dict[str, str], args: argparse.Namespace) -> int:
    raw = ha.request("GET", "/api/config")
    cfg = json.loads(raw)
    print("Связь с Home Assistant есть.")
    print(f"  версия:        {cfg.get('version')}")
    print(f"  часовой пояс:  {cfg.get('time_zone')}")
    print(f"  адрес:         {ha.url}")

    tz_env = env.get("TZ")
    if tz_env and cfg.get("time_zone") and tz_env != cfg["time_zone"]:
        print()
        print(f"  ВНИМАНИЕ: в .env указан TZ={tz_env}, а Home Assistant живёт в {cfg['time_zone']}.")
        print("  Из-за расхождения 'новый день' может наступать не тогда, когда ожидается.")

    states = familylink_states(ha)
    print()
    if not states:
        print("Сущностей Family Link пока нет.")
        print("Это нормально, если интеграция ещё не добавлена. Порядок действий в README, шаг 5.")
        return 1
    print(f"Интеграция Family Link на месте: сущностей {len(states)}.")
    return 0


def cmd_discover(ha: HA, env: dict[str, str], args: argparse.Namespace) -> int:
    states = familylink_states(ha)
    if not states:
        die("Сущности Family Link не найдены. Сначала добавьте интеграцию — README, шаг 5.")

    children, devices = collect_ids(states)

    if not children:
        die("Детских профилей не найдено.")

    print("ДЕТСКИЕ ПРОФИЛИ")
    print()
    for cid, info in sorted(children.items(), key=lambda i: -i[1]["count"]):
        own = {d: v for d, v in devices.items() if v["child"] == cid}
        print(f"  {label(info['names'])}")
        print(f"      child_id = {cid}")
        print(f"      сущностей: {info['count']}, устройств: {len(own)}")
        if own:
            for did, dinfo in own.items():
                print(f"      └─ {label(dinfo['names'])}")
                print(f"         device_id = {did}")
        else:
            print("      └─ устройств нет")
        print()

    orphan = {d: v for d, v in devices.items() if not v["child"]}
    if orphan:
        print("  Устройства без привязки к профилю:")
        for did, dinfo in orphan.items():
            print(f"      {label(dinfo['names'])}: {did}")
        print()

    if len(children) > 1:
        print("!" * 70)
        print("ВНИМАНИЕ: профилей больше одного.")
        print("Берите тот, у которого ЕСТЬ устройства. Профиль без устройств —")
        print("обычно старый дубль: команда к нему завершится успешно и не сделает")
        print("ничего, а искать причину вы будете долго.")
        print("!" * 70)
        print()

    usable = [(c, i) for c, i in children.items() if any(v["child"] == c for v in devices.values())]
    print("Впишите в .env:")
    if len(usable) == 1:
        cid = usable[0][0]
        did = next(d for d, v in devices.items() if v["child"] == cid)
        print(f"  FL_CHILD_ID={cid}")
        print(f"  FL_TABLET_DEVICE_ID={did}")
    else:
        print("  FL_CHILD_ID=<child_id профиля, у которого есть устройства>")
        print("  FL_TABLET_DEVICE_ID=<device_id планшета>")
    print("  FL_PHONE_DEVICE_ID=<device_id телефона, если он под контролем>")
    return 0


def cmd_snapshot(ha: HA, env: dict[str, str], args: argparse.Namespace) -> int:
    states = familylink_states(ha)
    if not states:
        die("Нечего сохранять: сущности Family Link не найдены.")

    SNAPSHOT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = SNAPSHOT_DIR / f"familylink_{stamp}.json"
    path.write_text(
        json.dumps(
            {"taken_at": datetime.now().isoformat(timespec="seconds"), "states": states},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Снимок сохранён: {path}")
    print(f"Сущностей записано: {len(states)}")
    print()
    print("Это точка отката. Если после экспериментов настройки поедут — по этому файлу")
    print("видно, как было. Файл в git не попадает.")
    return 0


def cmd_show(ha: HA, env: dict[str, str], args: argparse.Namespace) -> int:
    states = familylink_states(ha)
    if not states:
        die("Сущности Family Link не найдены.")

    interesting = ("limit", "time", "screen", "bonus", "bedtime", "school", "lock", "block")
    for s in sorted(states, key=lambda x: x["entity_id"]):
        attrs = s.get("attributes") or {}
        name = attrs.get("friendly_name") or s["entity_id"]
        eid = s["entity_id"]
        if not args.all and not any(w in eid.lower() for w in interesting):
            continue
        print(f"{name}")
        print(f"    {eid} = {s['state']}")
        for key, value in attrs.items():
            if key in ("friendly_name", "icon", "attribution", "device_class", "unit_of_measurement"):
                continue
            print(f"      {key}: {short(value)}")
        print()
    if not args.all:
        print("(показано главное; полный список — с ключом --all)")
    return 0


def resolve_target(env: dict[str, str], args: argparse.Namespace) -> tuple[str, str]:
    child_id = args.child or env.get("FL_CHILD_ID") or ""
    if args.device:
        device_id = args.device
    elif args.phone:
        device_id = env.get("FL_PHONE_DEVICE_ID") or ""
    else:
        device_id = env.get("FL_TABLET_DEVICE_ID") or ""

    if not child_id:
        die("Не задан child_id. Заполните FL_CHILD_ID в .env (см. python tools/fl.py discover).")
    if not device_id:
        die(
            "Не задан device_id. Заполните FL_TABLET_DEVICE_ID в .env,\n"
            "или укажите явно: --device <id>, или --phone для телефона."
        )
    return child_id, device_id


def cmd_set_limit(ha: HA, env: dict[str, str], args: argparse.Namespace) -> int:
    child_id, device_id = resolve_target(env, args)

    if not 0 <= args.minutes <= 1440:
        die("Минуты должны быть от 0 до 1440.")

    # Ключевой момент. Параметр day НЕ передаём сознательно: с ним интеграция
    # меняет постоянное недельное расписание, а нам нужна разовая квота
    # только на сегодня.
    payload = {
        "child_id": child_id,
        "device_id": device_id,
        "daily_minutes": args.minutes,
    }

    which = "телефон" if args.phone else "планшет"
    print(f"Ставлю дневную квоту {args.minutes} мин на СЕГОДНЯ ({which}).")
    print(f"  child_id  = {child_id}")
    print(f"  device_id = {device_id}")
    print("  day       = не передаём (иначе поменяется расписание на всю неделю)")
    print()
    print("Google отвечает не мгновенно, обычно около 10 секунд. Жду...")

    started = time.time()
    ha.call("familylink", "set_daily_limit", payload)
    print(f"Команда принята за {time.time() - started:.1f} с.")
    print()
    print("ВАЖНО: принятая команда — это ещё не применённая настройка.")
    print("Проверьте двумя способами:")
    print("  1) python tools/fl.py watch --minutes 10")
    print("  2) глазами на самом устройстве — в Family Link у ребёнка")
    return 0


def cmd_app(ha: HA, env: dict[str, str], args: argparse.Namespace) -> int:
    child_id = args.child or env.get("FL_CHILD_ID") or ""
    if not child_id:
        die("Не задан child_id. Заполните FL_CHILD_ID в .env.")

    print(f"Приложение: {args.package}")

    if args.mode == "follow":
        # НЕ set_app_daily_limit со значением -1. По документации интеграции -1
        # означает «следует общему лимиту устройства», но проверено на живом
        # аккаунте: приложение, которое было безлимитным, от -1 попадает в
        # заблокированные. В общий лимит возвращает только unblock_app.
        print("Режим:      follow (следует общему дневному лимиту устройства)")
        print()
        ha.call(
            "familylink",
            "unblock_app",
            {"child_id": child_id, "package_name": args.package},
        )

    elif args.mode == "blocked":
        print("Режим:      blocked (заблокировано)")
        print()
        ha.call(
            "familylink",
            "block_app",
            {"child_id": child_id, "package_name": args.package},
        )

    else:
        if args.mode == "unlimited":
            minutes, description = -2, "безлимит: не расходует общий лимит устройства"
        else:
            try:
                minutes = int(args.mode)
            except ValueError:
                die(
                    f"Непонятный режим: {args.mode}\n"
                    "Допустимо: unlimited, follow, blocked или число минут (1-1440)."
                )
            if not 1 <= minutes <= 1440:
                die("Число минут должно быть от 1 до 1440. Для нуля используйте blocked.")
            description = f"дневной лимит {minutes} мин именно на это приложение"

        print(f"Режим:      {args.mode} -> {minutes} ({description})")
        print()
        ha.call(
            "familylink",
            "set_app_daily_limit",
            {"child_id": child_id, "package_name": args.package, "minutes": minutes},
        )

    print("Команда принята. Проверьте применение на устройстве.")
    return 0


def snapshot_map(states: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in states:
        attrs = {
            k: v
            for k, v in (s.get("attributes") or {}).items()
            if k not in ("friendly_name", "icon", "attribution")
        }
        out[s["entity_id"]] = {"state": s["state"], "attrs": attrs}
    return out


def cmd_watch(ha: HA, env: dict[str, str], args: argparse.Namespace) -> int:
    """
    Следит за состоянием и печатает только изменения.

    Это главный измерительный прибор этапа 1. Он отвечает на вопрос,
    который нельзя решить рассуждением: через сколько секунд изменение
    реально доезжает и доезжает ли вообще.
    """
    deadline = time.time() + args.minutes * 60
    previous = snapshot_map(familylink_states(ha))
    started = datetime.now()

    print(f"Слежу {args.minutes} мин, опрос каждые {args.interval} с. Прервать: Ctrl+C")
    print(f"Начало: {started.strftime('%H:%M:%S')}. Отслеживаю {len(previous)} сущностей.")
    print("-" * 72)

    changes = 0
    try:
        while time.time() < deadline:
            time.sleep(args.interval)
            current = snapshot_map(familylink_states(ha))
            now = datetime.now()
            elapsed = (now - started).total_seconds()

            for eid, cur in current.items():
                old = previous.get(eid)
                if old is None:
                    print(f"[{now:%H:%M:%S}] +{elapsed:6.0f}с  ПОЯВИЛОСЬ  {eid} = {cur['state']}")
                    changes += 1
                    continue
                if old["state"] != cur["state"]:
                    print(
                        f"[{now:%H:%M:%S}] +{elapsed:6.0f}с  {eid}\n"
                        f"                       было:  {short(old['state'])}\n"
                        f"                       стало: {short(cur['state'])}"
                    )
                    changes += 1
                for key, value in cur["attrs"].items():
                    was = old["attrs"].get(key, "<нет>")
                    if was != value:
                        print(
                            f"[{now:%H:%M:%S}] +{elapsed:6.0f}с  {eid} . {key}\n"
                            f"                       было:  {short(was)}\n"
                            f"                       стало: {short(value)}"
                        )
                        changes += 1

            previous = current
    except KeyboardInterrupt:
        print("\nОстановлено вручную.")

    print("-" * 72)
    if changes:
        print(f"Изменений замечено: {changes}.")
    else:
        print("Изменений не было.")
        print()
        print("Если вы только что ставили лимит, а изменений ноль — это важный результат,")
        print("а не поломка скрипта. Значит команда принята, но не применилась.")
        print("Запишите это в docs/etap1-checklist.md и покажите мне.")
    return 0


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fl.py",
        description="Ручной стенд выдачи экранного времени через Family Link (этап 1).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="проверить связь с Home Assistant")
    sub.add_parser("discover", help="найти детей и устройства, показать идентификаторы")
    sub.add_parser("snapshot", help="сохранить текущие настройки в файл (точка отката)")

    show = sub.add_parser("show", help="показать текущее состояние")
    show.add_argument("--all", action="store_true", help="показать вообще всё, а не только главное")

    limit = sub.add_parser("set-limit", help="поставить дневную квоту на сегодня")
    limit.add_argument("--minutes", type=int, required=True, help="минут на сегодня, 0-1440")
    limit.add_argument("--device", help="device_id явно, вместо значения из .env")
    limit.add_argument("--phone", action="store_true", help="применить к телефону, а не планшету")
    limit.add_argument("--child", help="child_id явно, вместо значения из .env")

    app = sub.add_parser("app", help="режим отдельного приложения")
    app.add_argument("--package", required=True, help="например com.android.chrome")
    app.add_argument(
        "--mode",
        required=True,
        help="unlimited | follow | blocked | число минут",
    )
    app.add_argument("--child", help="child_id явно, вместо значения из .env")

    watch = sub.add_parser("watch", help="следить за изменениями и печатать их со временем")
    watch.add_argument("--minutes", type=int, default=10, help="сколько минут следить (по умолчанию 10)")
    watch.add_argument("--interval", type=int, default=15, help="пауза между опросами, с (по умолчанию 15)")

    return p


HANDLERS = {
    "check": cmd_check,
    "discover": cmd_discover,
    "snapshot": cmd_snapshot,
    "show": cmd_show,
    "set-limit": cmd_set_limit,
    "app": cmd_app,
    "watch": cmd_watch,
}


def main() -> int:
    # Русский текст в консоли Windows иначе ломается.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass

    args = build_parser().parse_args()
    env = load_env()
    ha = HA(env)
    return HANDLERS[args.command](ha, env, args)


if __name__ == "__main__":
    sys.exit(main())
