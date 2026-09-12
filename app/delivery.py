# -*- coding: utf-8 -*-
"""
Решение о выдаче: писать квоту в Family Link или не трогать.

Отдельный модуль без ввода-вывода. Здесь только чистая функция, принимающая
четыре числа и возвращающая, что делать. Так её можно проверить тестами на всех
случаях, включая те, которые руками не воспроизвести.

Почему это не «поставил и забыл». Родитель выдаёт время вручную — из
приложения Family Link, когда сын позвонил и попросил. Если автоматика будет
слепо записывать свою цель, она отнимет выданное. Живой пример: приложение
насчитало 35 минут, а на планшете стояло 120, поставленных руками. Запись 35
означала бы наказание за то, что папа добавил время.

Поэтому перед записью сверяется, совпадает ли текущее значение с тем, которое
мы ожидаем там увидеть. Не совпало — значит вмешался человек. Разница
запоминается как ручная надбавка дня — и это **аванс**, а не подарок:
планшет получает max(база + ручное, цель сервера). Папа поставил 50 с утра,
сын отметил домашку (цель 60) — на планшете 60, а не 95: заработанное
догоняет выданное вперёд, а не ложится сверху. Подарок сверх заработанного
делается через /parent — там он ложится в журнал и входит в цель.

Урезание руками (ручная надбавка отрицательная) уважается буквально: цель
минус урезанное. Дневной максимум ограничивает всё, что пишет сервер, — и
цель, и аванс.

Решение владельца 12.09 после первого живого дня: утренний ручной костыль
(камера под лимитом) сложился с домашкой в 1 ч 55 мин. Раньше автоматика при
чужой правке останавливалась до конца дня, и заработанное переставало
доезжать; аванс убирает обе беды.
"""

from __future__ import annotations

from dataclasses import dataclass

# Что делать
WRITE = "write"        # записать цель в Family Link
CONFIRM = "confirm"    # уже стоит нужное, только отметить в базе
UNKNOWN = "unknown"    # не удалось прочитать состояние

# Статусы строки выдачи в базе
PENDING = "pending"
CONFIRMED = "confirmed"
NOT_APPLIED = "not_applied"
STATUS_UNKNOWN = "unknown"
STATUS_MANUAL = "manual"   # исторический статус, новых строк с ним не появляется


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    manual: int = 0          # ручная надбавка дня после этого решения
    wanted: int = 0          # что должно стоять на устройстве: цель + надбавка
    manual_changed: bool = False


def wanted_for(*, target: int, base: int, manual: int, daily_max: int) -> int:
    """
    Что должно стоять на планшете.

    Ручная надбавка — аванс: max(база + ручное, цель). Урезание — буквально:
    цель + (отрицательное) ручное. Максимум — потолок для всего.
    """
    if manual >= 0:
        wanted = max(base + manual, target)
    else:
        wanted = target + manual
    return max(0, min(daily_max, wanted))


def decide(*, actual: int | None, target: int, applied: int | None, base: int,
           manual: int = 0, daily_max: int = 24 * 60) -> Decision:
    """
    actual  — что сейчас реально стоит в Family Link, None если не прочиталось
    target  — что мы хотим поставить (посчитано журналом)
    applied — что мы записали в прошлый раз и подтвердили, None если ещё не писали
    base    — базовая квота: единственное значение, которое ожидается в начале дня
    manual  — ручная надбавка, накопленная за день (аванс родителя)
    daily_max — дневной максимум: сервер никогда не пишет больше

    Ожидаемое значение: если мы уже писали — то, что записали; если ещё нет —
    базовая квота из недельного расписания. Расхождение с ожидаемым — чужая
    рука, разница уходит в надбавку. Проверка «уже стоит нужное» идёт первой:
    иначе запись, которую Google применил с опозданием, была бы принята за
    ручную правку и удвоена.
    """
    if actual is None:
        return Decision(UNKNOWN, "не удалось прочитать текущий лимит", manual=manual,
                        wanted=wanted_for(target=target, base=base, manual=manual, daily_max=daily_max))

    wanted = wanted_for(target=target, base=base, manual=manual, daily_max=daily_max)
    if actual == wanted:
        return Decision(CONFIRM, f"на устройстве уже {actual} мин", manual=manual, wanted=wanted)

    expected = applied if applied is not None else base
    changed = actual != expected
    note = ""
    if changed:
        delta = actual - expected
        # Подняли — аванс равен тому, что стоит на устройстве (сверх базы).
        # Урезали — разница накапливается и вычитается из цели буквально.
        manual = (actual - base) if delta > 0 else manual + delta
        wanted = wanted_for(target=target, base=base, manual=manual, daily_max=daily_max)
        who = "после нашей записи" if applied is not None else "до первой записи за день"
        sign = "+" if delta > 0 else "−"
        note = (f"лимит изменён вручную {who}: ожидали {expected}, на устройстве {actual}, "
                f"надбавка дня {sign}{abs(delta)} → {manual}; ")
        if actual == wanted:
            return Decision(CONFIRM, note + f"на устройстве уже {actual} мин",
                            manual=manual, wanted=wanted, manual_changed=True)

    if wanted < actual:
        # Цель ниже текущего значения — отзыв отметки ДЗ или снижение дневного
        # максимума родителем. Осознанное действие, выполняем.
        return Decision(WRITE, note + f"понижаем {actual} -> {wanted} мин",
                        manual=manual, wanted=wanted, manual_changed=changed)

    return Decision(WRITE, note + f"повышаем {actual} -> {wanted} мин",
                    manual=manual, wanted=wanted, manual_changed=changed)


def verdict_after_write(*, actual: int | None, target: int) -> tuple[str, str]:
    """
    Что записать в базу после попытки записи и повторного чтения.

    Ключевой момент: успешный ответ Google ничего не доказывает. В клиенте
    HAFamilyLink внутренняя ошибка проверки применения возвращает «успех»,
    поэтому единственное доказательство — перечитанное значение.
    """
    if actual is None:
        return STATUS_UNKNOWN, "записали, но состояние прочитать не удалось"
    if actual == target:
        return CONFIRMED, ""
    return NOT_APPLIED, f"записали {target}, при чтении {actual}"
