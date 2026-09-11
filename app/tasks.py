# -*- coding: utf-8 -*-
"""
Задания: генерация и проверка ответов.

Два принципа, из которых следует вся конструкция.

**Вопросы фиксируются при выдаче.** Набор генерируется один раз, в начале
попытки, и сохраняется в базе вместе с правильными ответами. Проверяется ответ
именно по тому набору, который ребёнок видел, а не по свежесгенерированному.

**Правильные ответы не покидают сервер.** Браузеру уходят только формулировки.
Иначе всё задание решается через просмотр кода страницы, и банк времени
превращается в кнопку «выдать минуты».

Типы заданий подобраны так, что проверка детерминированная: у каждого вопроса
есть точный правильный ответ, сравнение делает обычный код. ИИ в этот путь не
входит вовсе — ни по стоимости, ни по надёжности.
"""

from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass, field

MATH = "math"
WORDS = "words"
QUIZ = "quiz"
MISSION = "mission"
KINDS = (MATH, WORDS, QUIZ, MISSION)

DEFAULT_COUNT = 5
DEFAULT_PASS_RATIO = 0.8


@dataclass
class Question:
    prompt: str          # что видит ребёнок
    answer: str          # правильный ответ, наружу не отдаётся
    hint: str = ""       # подсказка при ошибке, без готового ответа

    def for_child(self, index: int) -> dict:
        return {"n": index, "prompt": self.prompt}


# --------------------------------------------------------------------------
# Генерация
# --------------------------------------------------------------------------


def _math_questions(payload: dict, rnd: random.Random) -> list[Question]:
    ops = payload.get("ops") or ["+", "-"]
    lo = int(payload.get("min", 2))
    hi = int(payload.get("max", 20))
    count = int(payload.get("count", DEFAULT_COUNT))

    out: list[Question] = []
    seen: set[str] = set()
    # Ограничение попыток: при узком диапазоне уникальных примеров может просто
    # не хватить, и бесконечный цикл здесь был бы обиднее повтора.
    for _ in range(count * 40):
        if len(out) >= count:
            break
        op = rnd.choice(ops)
        a, b = rnd.randint(lo, hi), rnd.randint(lo, hi)

        if op == "-":
            a, b = max(a, b), min(a, b)   # без отрицательных результатов
            value = a - b
        elif op == "*":
            value = a * b
        elif op == "/":
            value = rnd.randint(lo, hi)
            a, b = value * b, b           # делится нацело
        else:
            op = "+"
            value = a + b

        prompt = f"{a} {op} {b} = ?"
        if prompt in seen:
            continue
        seen.add(prompt)
        out.append(Question(prompt=prompt, answer=str(value),
                            hint="Посчитай ещё раз, не торопись."))
    return out


def _word_questions(payload: dict, rnd: random.Random) -> list[Question]:
    pairs = payload.get("pairs") or []
    count = int(payload.get("count", DEFAULT_COUNT))
    direction = payload.get("direction", "en-ru")

    usable = [p for p in pairs if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not usable:
        return []

    rnd.shuffle(usable)
    out: list[Question] = []
    for pair in usable[:count]:
        src, dst = (pair[0], pair[1]) if direction == "en-ru" else (pair[1], pair[0])
        out.append(Question(
            prompt=f"{src} — ?",
            answer=str(dst),
            hint="Вспомни, где это слово встречалось.",
        ))
    return out


def _quiz_questions(payload: dict, rnd: random.Random) -> list[Question]:
    """
    Вопросы, написанные вручную.

    Порядок сохраняется и НЕ перемешивается: этот тип нужен для разбора
    конкретной задачи по шагам, где каждый следующий вопрос опирается на ответ
    предыдущего. Перемешать их — значит сломать сам смысл задания.

    Поэтому же генерация от seed не зависит: набор один и тот же при каждой
    попытке. Повтор здесь — это повторение разбора, а не новые примеры.
    """
    raw = payload.get("questions") or []
    out: list[Question] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if not prompt or not answer:
            continue
        out.append(Question(prompt=prompt, answer=answer, hint=str(item.get("hint", "")).strip()))
    return out


# --------------------------------------------------------------------------
# Миссии: карточки, которые нельзя показывать вместе
# --------------------------------------------------------------------------


@dataclass
class Card:
    """
    Одна карточка миссии.

    Карточки идут по одной, а не на одной странице. Причина не в вёрстке:
    вторая карточка часто объясняет способ решения первой, а третья проверяет
    перенос на новое условие. Показать их вместе — значит выдать ответы.
    """
    title: str
    intro: str                                  # условие карточки
    questions: list[Question] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    # У миссий вопросов мало — два-три. Общий порог 0,8 при трёх вопросах
    # означает «все правильные», только неявно, поэтому здесь порог полный
    # и задаётся явно.
    pass_ratio: float = 1.0

    def for_child(self, index: int, hints_open: int) -> dict:
        """
        Карточка в том виде, в каком её можно отдать браузеру.

        Подсказки уходят только те, которые ребёнок уже открыл: остальные
        лежат на сервере. Иначе весь разбор виден в исходном коде страницы.
        """
        return {
            "n": index,
            "title": self.title,
            "intro": self.intro,
            "questions": [q.for_child(i) for i, q in enumerate(self.questions, start=1)],
            "hints": self.hints[:max(0, hints_open)],
            "hints_total": len(self.hints),
            "hints_open": max(0, min(hints_open, len(self.hints))),
        }


def mission_cards(payload: dict | None) -> list[Card]:
    payload = payload or {}
    out: list[Card] = []
    for raw in payload.get("cards") or []:
        if not isinstance(raw, dict):
            continue
        questions = _quiz_questions(raw, random.Random(0))
        if not questions:
            continue
        out.append(Card(
            title=str(raw.get("title", "")).strip() or f"Карточка {len(out) + 1}",
            intro=str(raw.get("intro", "")).strip(),
            questions=questions,
            hints=[str(h).strip() for h in (raw.get("hints") or []) if str(h).strip()],
            pass_ratio=float(raw.get("pass_ratio", 1.0)),
        ))
    return out


def generate(kind: str, payload: dict | None = None, seed: int | None = None) -> list[Question]:
    payload = payload or {}
    rnd = random.Random(seed)

    if kind == MATH:
        return _math_questions(payload, rnd)
    if kind == WORDS:
        return _word_questions(payload, rnd)
    if kind == QUIZ:
        return _quiz_questions(payload, rnd)
    if kind == MISSION:
        # Плоский список всех вопросов миссии по порядку карточек. Нужен для
        # проверки каталога и тестов; ребёнку карточки показываются по одной
        # через mission_cards.
        return [q for card in mission_cards(payload) for q in card.questions]
    raise ValueError(f"Неизвестный тип задания: {kind}")


# --------------------------------------------------------------------------
# Проверка
# --------------------------------------------------------------------------


def normalize(text: str) -> str:
    """
    Приводит ответ к сравнимому виду.

    Цель — не придираться к оформлению: лишние пробелы, регистр, «ё» вместо «е»
    и запятая вместо точки в дробях не должны стоить ребёнку заработанного
    времени. Смысл ответа при этом не меняется.
    """
    s = (text or "").strip().lower().replace("ё", "е")
    s = s.replace(",", ".")
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" .!?")
    return s


def is_correct(given: str, expected: str) -> bool:
    g, e = normalize(given), normalize(expected)
    if g == e:
        return True
    # Числа сравниваем как числа: «07» и «7», «2.0» и «2» — один ответ.
    try:
        return abs(float(g) - float(e)) < 1e-9
    except (TypeError, ValueError):
        return False


@dataclass
class Judgement:
    correct: int
    total: int
    passed: bool
    details: list[dict] = field(default_factory=list)

    def for_child(self) -> dict:
        """
        То, что можно показать ребёнку.

        Правильные ответы не отдаём даже после неудачи: иначе повторная попытка
        превращается в переписывание. Показываем, что именно не сошлось, и
        подсказку.
        """
        return {
            "correct": self.correct,
            "total": self.total,
            "passed": self.passed,
            "items": [
                {"n": d["n"], "prompt": d["prompt"], "given": d["given"],
                 "ok": d["ok"], "hint": "" if d["ok"] else d["hint"]}
                for d in self.details
            ],
        }


def judge(questions: list[Question], answers: dict, pass_ratio: float = DEFAULT_PASS_RATIO) -> Judgement:
    """
    Сверяет ответы с сохранённым набором вопросов.

    answers — {номер вопроса: ответ}. Пропущенный вопрос считается неверным,
    а не отсутствующим: иначе задание сдаётся отправкой пустой формы.
    """
    details = []
    correct = 0
    for i, q in enumerate(questions, start=1):
        given = str(answers.get(str(i), answers.get(i, "")) or "")
        ok = is_correct(given, q.answer)
        correct += int(ok)
        details.append({"n": i, "prompt": q.prompt, "given": given,
                        "ok": ok, "hint": q.hint, "expected": q.answer})

    total = len(questions)
    # Порог именно «не меньше»: 4 из 5 при 0.8 обязаны проходить.
    passed = total > 0 and correct >= total * pass_ratio
    return Judgement(correct=correct, total=total, passed=passed, details=details)


# --------------------------------------------------------------------------
# Сохранение в базу и обратно
# --------------------------------------------------------------------------


def dump(questions: list[Question]) -> list[dict]:
    return [asdict(q) for q in questions]


def load(raw: list[dict]) -> list[Question]:
    return [Question(prompt=r["prompt"], answer=r["answer"], hint=r.get("hint", "")) for r in raw]
