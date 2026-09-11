# -*- coding: utf-8 -*-
"""
Переходник к Family Link через Home Assistant.

Единственное место, которое общается с Google. Всё остальное приложение о
Family Link не знает: если исполнитель придётся заменить, переписывается этот
файл, а не система.

Два правила, добытые чтением исходников интеграции, а не догадками:

**Параметр `day` не передаём.** В обработчике `familylink.set_daily_limit`
отсутствие `day` меняет квоту на сегодня, а с ним — постоянное недельное
расписание. Проверено на живом аккаунте: расписание после нашей записи
осталось нетронутым во все дни, кроме текущего.

**Успешный ответ ничего не доказывает.** В клиенте HAFamilyLink исключение
внутри проверки применения возвращает «успех». Поэтому после записи значение
всегда перечитывается, и только совпадение считается подтверждением.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

DOMAIN = "familylink"
TIMEOUT_READ = 30
TIMEOUT_WRITE = 90   # установка лимита у Google занимает около десяти секунд


class FamilyLinkError(Exception):
    """Не удалось поговорить с Home Assistant."""


class FamilyLink:
    def __init__(self, ha_url: str, ha_token: str):
        self.url = (ha_url or "").rstrip("/")
        self.token = ha_token or ""

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)

    # ----------------------------------------------------------------------

    def _request(self, method: str, path: str, payload=None, timeout: int = TIMEOUT_READ) -> str:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=body,
            method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise FamilyLinkError(f"Home Assistant ответил {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise FamilyLinkError(f"Home Assistant недоступен: {exc}") from exc

    def states(self) -> list[dict]:
        return json.loads(self._request("GET", "/api/states"))

    # ----------------------------------------------------------------------

    def read_daily_limit(self, *, device_id: str) -> int | None:
        """
        Текущий дневной лимит устройства, как его видит Google.

        Ищем не по имени сущности: оно зависит от модели планшета и сменится
        при замене устройства. Ищем по device_id в атрибутах — это то, чем мы
        сами адресуем запись.
        """
        try:
            states = self.states()
        except FamilyLinkError:
            return None

        fallback = None
        for state in states:
            attrs = state.get("attributes") or {}
            if attrs.get("device_id") != device_id:
                continue

            # Предпочтительный источник: атрибут с явным числом лимита.
            value = attrs.get("daily_limit_minutes")
            if isinstance(value, (int, float)):
                return int(value)

            # Запасной: сенсор, у которого само состояние и есть лимит.
            if state["entity_id"].endswith("_daily_limit"):
                try:
                    fallback = int(float(state["state"]))
                except (TypeError, ValueError):
                    pass

        return fallback

    def write_daily_limit(self, *, child_id: str, device_id: str, minutes: int) -> None:
        """
        Ставит квоту на СЕГОДНЯ. Абсолютное значение, не прибавка.

        Передаём и child_id, и device_id: с одним child_id вызов
        распространяется на все устройства ребёнка, а у нас в семье есть ещё
        пустой профиль-двойник.
        """
        if not 0 <= minutes <= 1440:
            raise ValueError(f"Минуты вне диапазона: {minutes}")

        self._request(
            "POST",
            f"/api/services/{DOMAIN}/set_daily_limit",
            {"child_id": child_id, "device_id": device_id, "daily_minutes": int(minutes)},
            timeout=TIMEOUT_WRITE,
        )
