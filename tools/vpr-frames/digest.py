"""Выжимка OCR-текста варианта: только строки, нужные для составления вопросов."""
import re, sys

sys.stdout.reconfigure(encoding="utf-8")
v = sys.argv[1]
chunks = open(f"frames/{v}/ocr.txt", encoding="utf-8").read().split("\n=====\n")
seen, lines = set(), []
for ln in "\n".join(chunks).splitlines():
    s = re.sub(r"\s+", " ", ln).strip()
    if len(s) < 6 or s in seen or not re.search(r"[а-яА-Я]{3}", s):
        continue
    seen.add(s); lines.append(s)

KEYS = ["выражения", "мультфильм", "часть длится", "закончить за", "день недели", "периметр",
        "Мальчики", "Девочки", "ручек", "ручка стоит", "можно сдать", "язык —", "обязательно",
        "Меня зовут", "зовут", "брат", "сестр", "дядя", "тёт", "Бабушк", "Дедушк", "родители",
        "карандаш", "надпись", "рис. 1", "капот", "Какой день", "какой день", "1) В", "2) В", "1) Какое", "2) Какое",
        "Рассмотри", "проехать", "км", "Найди", "Сколько", "сколько"]
for s in lines:
    if any(k in s for k in KEYS):
        print(s[:120])
print("--- времена:", ", ".join(sorted(set(re.findall(r"\b\d{1,2}\s?:\s?\d{2}\b", "\n".join(chunks))))))
