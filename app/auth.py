# -*- coding: utf-8 -*-
"""
Код входа и подпись куки.

Отдельный модуль без зависимостей: им пользуется и веб-приложение, и скрипт
tools/set_pin.py, который запускается на сервере системным Python, где fastapi
не установлен.

Код входа хранится не сам, а как pbkdf2 с солью. Базу мы копируем в резервные
копии, и код из неё восстановить не должно быть возможно.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

ITERATIONS = 200_000
COOKIE_MAX_AGE = 60 * 60 * 24 * 90   # планшет ребёнка, каждый день не логинимся


# --------------------------------------------------------------------------
# Код входа
# --------------------------------------------------------------------------


def _derive(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), ITERATIONS).hex()


def make_pin_hash(pin: str) -> str:
    salt = secrets.token_hex(16)
    return f"{salt}${_derive(pin, salt)}"


def check_pin(pin: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, expected = stored.split("$", 1)
    # Сравнение с постоянным временем: иначе по задержке ответа можно подбирать.
    return hmac.compare_digest(_derive(pin, salt), expected)


# --------------------------------------------------------------------------
# Кука
# --------------------------------------------------------------------------


def sign(child_id: int, issued: int, secret: str) -> str:
    payload = f"{child_id}.{issued}"
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{mac}"


def verify(raw: str | None, secret: str, max_age: int = COOKIE_MAX_AGE) -> int | None:
    """
    Возвращает child_id, если кука подлинная и не просрочена.

    Кто вошёл, решает подпись, а не значение в запросе: иначе достаточно
    подправить куку в браузере, чтобы стать другим ребёнком.
    """
    if not secret or not raw or raw.count(".") != 2:
        return None
    child_id, issued, _ = raw.split(".")
    try:
        expected = sign(int(child_id), int(issued), secret)
        age = time.time() - int(issued)
    except ValueError:
        return None
    if not hmac.compare_digest(raw, expected):
        return None
    if age > max_age or age < -60:   # -60: небольшой запас на рассинхрон часов
        return None
    return int(child_id)


# --------------------------------------------------------------------------
# Защита от перебора кода
# --------------------------------------------------------------------------


class LoginGuard:
    """
    Считает неудачные входы и на время запрещает новые попытки.

    Код входа короткий — четыре-шесть цифр. Пока сайт жил на локальном порту,
    перебор был невозможен физически; с выходом в интернет десять тысяч
    вариантов перебираются за минуты. Поэтому два счётчика:

    - по источнику (IP-адрес): столько попыток, сколько нужно ребёнку,
      который ошибся пару раз, но мало для перебора;
    - общий на всех: чтобы перебор не размазывали по разным адресам.

    Всё живёт в памяти процесса. Перезапуск сбрасывает счётчики — это
    приемлемо, перебор перезапуском не ускорить: он не в руках атакующего.
    """

    EVERYONE = "*"

    def __init__(self, *, per_source: int = 5, total: int = 20,
                 window: int = 600, clock=time.time) -> None:
        self.per_source = per_source
        self.total = total
        self.window = window
        self._clock = clock
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        edge = self._clock() - self.window
        kept = [t for t in self._failures.get(key, ()) if t > edge]
        if kept:
            self._failures[key] = kept
        else:
            self._failures.pop(key, None)
        return kept

    def retry_after(self, source: str) -> int:
        """Сколько секунд ждать, прежде чем принимать код. 0 — можно сейчас."""
        waits = []
        for key, limit in ((source, self.per_source), (self.EVERYONE, self.total)):
            recent = self._recent(key)
            if len(recent) >= limit:
                waits.append(recent[-limit] + self.window - self._clock())
        return max(1, int(max(waits) + 0.999)) if waits else 0

    def failed(self, source: str) -> None:
        now = self._clock()
        for key in (source, self.EVERYONE):
            self._failures.setdefault(key, []).append(now)

    def succeeded(self, source: str) -> None:
        # Удачный вход снимает счётчик источника: ребёнок ошибся и вспомнил.
        # Общий счётчик не трогаем — он про всех сразу.
        self._failures.pop(source, None)
