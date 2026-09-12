# -*- coding: utf-8 -*-
"""
Генератор уроков английского: content/english-2026-09.json.

Урок — миссия из карточек на 10 минут, раздел english, назначается планом
через день (docs/plan-2026-09-14-27.md). Слова — Spotlight 3 и Spotlight 4
(часть 1, модули 1–4), по которым сын занимается в школе.

  Карточка 1 «Запомни» — пять слов с переводом и фразой; вопрос заставляет
  перепечатать первое слово, глядя на список.
  Карточка 2 «Узнай» — английское → русское, допускаются варианты через «|».
  Карточка 3 «Напиши» (со второй недели) — русское → английское.

Запуск: py -3 tools/make_english.py  — перезаписывает JSON и печатает строки
для EXPECTED в tests/test_content.py.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "content" / "english-2026-09.json"

# (english, русский с вариантами, фраза для запоминания)
WORDS = {
    "mother":    ("мама|мать", "My mother is kind. — Моя мама добрая."),
    "father":    ("папа|отец", "My father is tall. — Мой папа высокий."),
    "sister":    ("сестра", "I have a little sister. — У меня есть младшая сестра."),
    "brother":   ("брат", "My brother likes Roblox. — Мой брат любит Roblox."),
    "family":    ("семья", "One big happy family. — Одна большая счастливая семья."),
    "tall":      ("высокий", "Tom is tall. — Том высокий."),
    "short":     ("низкий|короткий|невысокий", "Ann is short. — Аня невысокая."),
    "slim":      ("стройный|худой", "She is slim. — Она стройная."),
    "funny":     ("смешной|весёлый|забавный", "He is very funny. — Он очень смешной."),
    "kind":      ("добрый", "A kind teacher. — Добрый учитель."),
    "baker":     ("пекарь", "A baker makes bread. — Пекарь печёт хлеб."),
    "waiter":    ("официант", "The waiter brings pizza. — Официант приносит пиццу."),
    "nurse":     ("медсестра", "A nurse works in a hospital. — Медсестра работает в больнице."),
    "mechanic":  ("механик", "A mechanic fixes cars. — Механик чинит машины."),
    "postman":   ("почтальон", "The postman brings letters. — Почтальон приносит письма."),
    "bakery":    ("пекарня|булочная", "Bread is from the bakery. — Хлеб из пекарни."),
    "garage":    ("гараж|автомастерская", "The car is in the garage. — Машина в гараже."),
    "post office": ("почта|почтовое отделение", "Letters go to the post office. — Письма несут на почту."),
    "cafe":      ("кафе", "We eat in a cafe. — Мы едим в кафе."),
    "hospital":  ("больница", "The nurse is at the hospital. — Медсестра в больнице."),
    "tomato":    ("помидор|томат", "A red tomato. — Красный помидор."),
    "potato":    ("картошка|картофель|картофелина", "I like potatoes. — Я люблю картошку."),
    "lemon":     ("лимон", "A yellow lemon. — Жёлтый лимон."),
    "butter":    ("масло|сливочное масло", "Bread and butter. — Хлеб с маслом."),
    "sugar":     ("сахар", "Tea with sugar. — Чай с сахаром."),
    "giraffe":   ("жираф", "A giraffe is very tall. — Жираф очень высокий."),
    "monkey":    ("обезьяна|обезьянка", "A funny monkey. — Смешная обезьяна."),
    "dolphin":   ("дельфин", "Dolphins swim fast. — Дельфины быстро плавают."),
    "crocodile": ("крокодил", "A big green crocodile. — Большой зелёный крокодил."),
    "elephant":  ("слон", "An elephant is big. — Слон большой."),
}

# день, slug, тема, новые слова, старые слова (повторение), сколько писать по-английски
LESSONS = [
    ("2026-09-14", "eng-01-family",   "Английский: семья",
     ["mother", "father", "sister", "brother", "family"], [], 0),
    ("2026-09-16", "eng-02-looks",    "Английский: какой человек",
     ["tall", "short", "slim", "funny", "kind"], [], 0),
    ("2026-09-18", "eng-03-jobs",     "Английский: профессии",
     ["baker", "waiter", "nurse", "mechanic", "postman"], [], 0),
    ("2026-09-20", "eng-04-review-1", "Английский: повторение недели",
     [], ["family", "brother", "kind", "tall", "nurse", "baker", "funny"], 0),
    ("2026-09-22", "eng-05-places",   "Английский: где работают",
     ["bakery", "garage", "post office", "cafe", "hospital"], ["waiter", "sister"], 3),
    ("2026-09-24", "eng-06-food",     "Английский: еда",
     ["tomato", "potato", "lemon", "butter", "sugar"], ["baker", "tall"], 3),
    ("2026-09-26", "eng-07-zoo",      "Английский: в зоопарке",
     ["giraffe", "monkey", "dolphin", "crocodile", "elephant"], ["potato", "hospital"], 3),
]

REWARD = 10


def first_ru(word: str) -> str:
    return WORDS[word][0].split("|")[0]


def mask(word: str) -> str:
    """Подсказка для написания: первая буква и длина — «p _ _»."""
    return " ".join([word[0]] + ["_"] * (len(word) - 1))


def lesson(day, slug, title, new, old, write_count):
    words = new + old
    listing = "; ".join(f"{w} — {first_ru(w)}" for w in words)
    phrases = "\n".join(WORDS[w][1] for w in words)
    cards = []

    # 1. Запомни. Вопрос — перепечатать первое слово: читать список придётся.
    cards.append({
        "title": "Запомни" if new else "Вспомни",
        "intro": (f"Слова на сегодня: {listing}. "
                  + ("Новые — первые пять, дальше повторение. " if new and old else "")
                  + "Прочитай фразы: " + " ".join(WORDS[w][1] for w in words)),
        "questions": [{
            "prompt": f"Перепиши по-английски первое слово из списка (оно означает «{first_ru(words[0])}»).",
            "answer": words[0],
            "hint": "Смотри в список выше: первое слово, буква в букву.",
        }],
    })

    # 2. Узнай: английское → русское.
    cards.append({
        "title": "Узнай",
        "intro": "Напиши по-русски, что значит слово. Подойдёт любое верное значение.",
        "pass_ratio": 0.8,
        "questions": [{
            "prompt": f"{w} — ?",
            "answer": WORDS[w][0],
            "hint": WORDS[w][1].split(" — ")[0] + " — вспомни эту фразу.",
        } for w in words],
    })

    # 3. Напиши: русское → английское (со второй недели).
    if write_count:
        to_write = (new[:write_count - 1] + old[:1]) if old else new[:write_count]
        cards.append({
            "title": "Напиши",
            "intro": "Теперь наоборот: напиши слово по-английски. Ошибка в букве — не сошлось, но подсказка покажет начало.",
            "pass_ratio": 0.67,
            "questions": [{
                "prompt": f"{first_ru(w)} — ? (по-английски)",
                "answer": w,
                "hint": f"Начинается так: {mask(w)}",
            } for w in to_write],
        })

    return {
        "slug": slug, "kind": "mission", "version": 1, "title": title,
        "section": "english", "reward_minutes": REWARD,
        "payload": {"skill": f"Английский: слова — {title.split(': ')[1]}", "cards": cards,
                    "feedback": "Ты узнал слова по фразам, а не зубрил список — так они и остаются в голове."},
    }


def main() -> None:
    tasks = [lesson(*row) for row in LESSONS]
    data = {
        "_comment": [
            "Уроки английского на 14–27 сентября 2026, через день. Сгенерировано",
            "tools/make_english.py — править слова там, а не здесь.",
            "Слова из Spotlight 3 и Spotlight 4 (часть 1, модули 1–4).",
        ],
        "subject": "Английский язык, 4 класс",
        "source": "Spotlight 3–4 (Быкова), словарь модулей",
        "section": "english",
        "status": "draft",
        "tasks": tasks,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(tasks)} уроков")
    print("\nEXPECTED для tests/test_content.py:")
    for t in tasks:
        answers = [q["answer"] for c in t["payload"]["cards"] for q in c["questions"]]
        print(f'    "{t["slug"]}": {json.dumps(answers, ensure_ascii=False)},')
    print("\nschedule.json, раздел english:")
    for (day, slug, *_), _ in zip(LESSONS, tasks):
        print(f'  "{day}": ["{slug}"]')


if __name__ == "__main__":
    main()
