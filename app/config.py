# -*- coding: utf-8 -*-
"""
Настройки приложения.

Читаются из того же .env, что и стенд: одна конфигурация на весь проект,
чтобы не было двух источников правды про child_id и адрес Home Assistant.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

# Значения по умолчанию. Живые настройки банка лежат в базе и меняются из
# родительской панели — здесь только то, что нужно до открытия базы.
DEFAULT_BASE_MINUTES = 15
DEFAULT_DAILY_MAX_MINUTES = 195


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Формат простой: КЛЮЧ=значение, # — комментарий."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Config:
    timezone: str
    ha_url: str
    ha_token: str
    fl_child_id: str
    fl_device_id: str
    db_path: Path
    app_secret: str
    child_name: str

    @property
    def ready_for_delivery(self) -> bool:
        """Можно ли вообще выдавать время, или не хватает настроек."""
        return bool(self.ha_url and self.ha_token and self.fl_child_id and self.fl_device_id)


def load(env: dict[str, str] | None = None) -> Config:
    # Переменные окружения важнее .env: в контейнере настройки приходят так.
    file_values = read_env_file()
    src = {**file_values, **(env if env is not None else os.environ)}

    return Config(
        timezone=src.get("TZ") or "Europe/Moscow",
        ha_url=(src.get("HA_URL") or "").rstrip("/"),
        ha_token=src.get("HA_TOKEN") or "",
        fl_child_id=src.get("FL_CHILD_ID") or "",
        fl_device_id=src.get("FL_TABLET_DEVICE_ID") or "",
        db_path=Path(src.get("DB_PATH") or (ROOT / "appdata" / "bank.sqlite3")),
        # Подписывает куку входа. Смена значения разлогинивает всех — это
        # нормально, но не должно происходить само, поэтому значения по
        # умолчанию нет.
        app_secret=src.get("APP_SECRET") or "",
        # Как сайт обращается к ребёнку. Задаётся один раз при создании базы.
        child_name=src.get("CHILD_NAME") or "Ученик",
    )
