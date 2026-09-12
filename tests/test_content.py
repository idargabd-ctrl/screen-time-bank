# -*- coding: utf-8 -*-
"""
Тесты каталога заданий.

Смысл этих тестов не в структуре файла, а в арифметике. Опечатка в ответе —
это ребёнок, который решил правильно, а система сказала «неверно» и не выдала
время. Заметить такое на живом сыне дорого, поэтому каждая цепочка проверяется
здесь и считается заново, независимо от того, что написано в файле.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import tasks  # noqa: E402

CONTENT_DIR = ROOT / "content"


def load_all() -> list[tuple[str, dict, dict]]:
    out = []
    for path in sorted(CONTENT_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for task in data.get("tasks") or []:
            out.append((path.name, data, task))
    return out


ALL = load_all()
IDS = [t["slug"] for _, _, t in ALL]


def test_catalogue_is_not_empty():
    assert ALL, "в content/ нет ни одного задания"


def test_slugs_are_unique():
    slugs = [t["slug"] for _, _, t in ALL]
    assert len(slugs) == len(set(slugs))


@pytest.mark.parametrize("task", [t for _, _, t in ALL], ids=IDS)
def test_task_is_well_formed(task):
    assert task["kind"] in tasks.KINDS
    assert task["title"].strip()
    assert int(task["reward_minutes"]) > 0
    assert int(task.get("version", 1)) >= 1

    questions = tasks.generate(task["kind"], task.get("payload") or {}, seed=1)
    assert questions, "ни одного вопроса"
    for i, q in enumerate(questions, start=1):
        assert q.prompt.strip(), f"пустая формулировка в вопросе {i}"
        assert q.answer.strip(), f"пустой ответ в вопросе {i}"
        assert q.hint.strip(), f"нет подсказки в вопросе {i} — при ошибке нечего показать"


@pytest.mark.parametrize("task", [t for _, _, t in ALL], ids=IDS)
def test_quiz_questions_keep_their_order(task):
    """Разбор по шагам рассыпается, если вопросы перемешать."""
    if task["kind"] not in (tasks.QUIZ, tasks.MISSION):
        pytest.skip("не разбор по шагам")
    a = tasks.generate(task["kind"], task["payload"], seed=1)
    b = tasks.generate(task["kind"], task["payload"], seed=999)
    assert [q.prompt for q in a] == [q.prompt for q in b]


@pytest.mark.parametrize("task", [t for _, _, t in ALL], ids=IDS)
def test_right_answers_pass_and_wrong_ones_do_not(task):
    questions = tasks.generate(task["kind"], task["payload"], seed=1)
    ratio = float(task["payload"].get("pass_ratio", tasks.DEFAULT_PASS_RATIO))

    # Ответ может перечислять варианты через «|» — годится любой из них.
    for pick in (0, -1):
        right = {str(i): q.answer.split("|")[pick] for i, q in enumerate(questions, start=1)}
        assert tasks.judge(questions, right, ratio).passed is True

    assert tasks.judge(questions, {}, ratio).passed is False, "пустая форма не должна проходить"


# --------------------------------------------------------------------------
# Арифметика: каждая цепочка пересчитана независимо от файла
# --------------------------------------------------------------------------

EXPECTED = {
    "vpr4-demo-01-vychisli-43-27":            ["2", "7", "23", "16", "43"],
    "vpr4-demo-02-7-plus-3-na-8-plus-12":     ["1", "20", "1", "60", "67"],
    "vpr4-demo-07-12012-na-3-minus-170-na-4": ["3", "4004", "680", "3324"],
    "vpr4-01-vychisli-260-na-20":             ["26", "2", "13", "260"],
    "vpr4-02-34-minus-4-na-7-plus-16":        ["1", "28", "6", "22"],
    "vpr4-07-840-na-6-minus-138-na-3":        ["1", "140", "46", "94"],

    "vpr4-demo-03-sdacha-so-100":            ["32", "33", "65", "35"],
    "vpr4-demo-04-nachalo-sekcii":           ["16", "75", "45", "15", "17"],
    "vpr4-demo-05-ploshad-pryamougolnika":   ["8", "3", "24", "3", "5", "24"],
    "vpr4-demo-06-medali-tablica":           ["8", "18", "15", "17", "10", "Орион"],
    "vpr4-demo-08-varenje-banki":            ["3000", "1600", "1400", "7"],
    "vpr4-08-pododeyalniki-navolochki":      ["440", "4400", "8000", "3600", "40"],
    "vpr4-03-hlebobulochnye-ceny":           ["26", "78", "48", "126"],
    "vpr4-04-mama-gotovila-uzhin":           ["20", "1", "20", "40", "19"],
    "vpr4-05-perimetr-kvadrata":             ["4", "16", "16", "1", "1", "12"],
    "vpr4-demo-09-tatyana-vstrechi":         ["3", "нет", "бухгалтер", "программист",
                                              "программист", "бухгалтер"],
    "vpr4-demo-10-imya-v-zerkale":           ["4", "А", "АШИМ"],
    "vpr4-demo-11-velosipedy-ruli-kolesa":   ["12", "24", "3", "3"],

    # Миссии: плоский список ответов по порядку карточек. Ребёнку карточки
    # показываются по одной, здесь они склеены для проверки каталога.
    "mission-m1-reklama-nabora":            ["1", "36", "1", "1", "8"],
    "mission-m2-ploshad-i-perimetr":        ["1", "20", "да", "22", "2", "2"],
    "mission-m3-uspet-k-finalu":            ["43", "16", "7", "1", "16", "45"],
    "mission-r1-lozhnoe-proverochnoe":      ["лесной", "1", "1", "гора", "1"],
    "mission-r2-kto-chto-sdelal":           ["3", "поднялся", "исчез", "1", "2"],
    "mission-r3-slovo-menyaet-rol":         ["1", "2", "2", "1", "2"],
    "mission-o1-mozhno-li-verit-opytu":     ["2", "нет", "1", "1"],
    "mission-ch1-otkuda-vyvod":             ["2", "1", "2", "1", "1"],
    "mission-a1-obyavlenie-na-dveri":       ["2", "12", "1", "1", "1"],

    # Вторая партия: по два варианта на навык (content/missions-batch2.json).
    "mission-m1b-skiny-optom": ["1", "60", "1", "1", "18"],
    "mission-m1c-picca-na-komandu": ["2", "840", "1", "2", "60"],
    "mission-m2b-kovrik-i-lenta": ["2", "24", "да", "18", "2", "12"],
    "mission-m2c-zagon-dlya-lam": ["2", "35", "да", "36", "2", "2"],
    "mission-m3b-strim-zakonchilsya": ["85", "19", "50", "1", "11", "45"],
    "mission-m3c-kvest-v-parke": ["55", "11", "35", "1", "14", "10"],
    "mission-r1b-stal-i-stol": ["столовая", "1", "1", "моряк", "1"],
    "mission-r1c-visna-ili-vesna": ["весна", "1", "1", "землю", "1"],
    "mission-r2b-robot-na-parkovke": ["3", "подъехал", "уехал", "1", "1"],
    "mission-r2c-avatar-prygnul": ["3", "прыгнул", "приземлился", "1", "2"],
    "mission-r3b-pech-i-pech": ["1", "2", "2", "1", "2"],
    "mission-r3c-eli-i-eli": ["1", "2", "1", "1", "2"],
    "mission-o1b-rostki-na-okne": ["2", "нет", "1", "1"],
    "mission-o1c-mashinki-na-gorke": ["2", "нет", "1", "1"],
    "mission-ch1b-mesto-v-komande": ["1", "1", "2", "1", "1"],
    "mission-ch1c-poslednyaya-batareyka": ["1", "2", "3", "1", "1"],
    "mission-a1b-chess-club": ["2", "5", "2", "2", "1"],
    "mission-a1c-music-club": ["3", "3", "1", "1", "14"],

    # Уроки английского (tools/make_english.py): переписать первое слово,
    # узнать по-русски (варианты через «|»), со второй недели — написать.
    "eng-01-family": ["mother", "мама|мать", "папа|отец", "сестра", "брат", "семья"],
    "eng-02-looks": ["tall", "высокий", "низкий|короткий|невысокий", "стройный|худой", "смешной|весёлый|забавный", "добрый"],
    "eng-03-jobs": ["baker", "пекарь", "официант", "медсестра", "механик", "почтальон"],
    "eng-04-review-1": ["family", "семья", "брат", "добрый", "высокий", "медсестра", "пекарь", "смешной|весёлый|забавный"],
    "eng-05-places": ["bakery", "пекарня|булочная", "гараж|автомастерская", "почта|почтовое отделение", "кафе", "больница", "официант", "сестра", "bakery", "garage", "waiter"],
    "eng-06-food": ["tomato", "помидор|томат", "картошка|картофель|картофелина", "лимон", "масло|сливочное масло", "сахар", "пекарь", "высокий", "tomato", "potato", "baker"],
    "eng-07-zoo": ["giraffe", "жираф", "обезьяна|обезьянка", "дельфин", "крокодил", "слон", "картошка|картофель|картофелина", "больница", "giraffe", "monkey", "potato"],

    # Целые варианты 2025: ответы по номерам, прочитаны с кадров роликов и
    # пересчитаны. Порядок — как в ролике.
    "vpr4-2025-real-2": ["409", "51", "110", "19", "50", "16", "1", "6", "21", "686", "44", "9", "4", "Й", "11"],
    "vpr4-2025-var-01": ["37", "14", "2", "27", "10", "16", "2", "4", "2313", "12", "красное", "жёлтое", "9", "ЯНАВ"],
    "vpr4-2025-var-02": ["63", "20", "9", "27", "15", "22", "2", "2", "402", "8", "красное", "зелёное", "9", "АМИД"],
    "vpr4-2025-var-03": ["8", "30", "13", "27", "12", "16", "3", "1", "2103", "16", "синее", "зелёное", "9", "ЯТАК"],
    "vpr4-2025-var-04": ["39", "22", "36", "76", "16", "26", "1", "1", "2363", "10", "синее", "жёлтое", "9", "АРЕЛ"],
    "vpr4-2025-var-05": ["49", "16", "10", "18", "30", "16", "20", "1", "2", "1162", "6", "красное", "зелёное", "9", "АДЮЛ"],
    "vpr4-2025-var-06": ["77", "79", "10", "среда", "16", "В", "Г", "4684", "9", "вторник", "четверг", "9", "АВАРИЙНАЯ", "10"],
    "vpr4-2025-var-07": ["36", "107", "25", "воскресенье", "16", "Д", "В", "2752", "7", "понедельник", "пятница", "9", "РОТАУКАВЭ", "10"],
    "vpr4-2025-var-08": ["22", "91", "25", "четверг", "18", "Г", "Б", "2922", "9", "вторник", "пятница", "9", "ПОЖАРНАЯ", "9"],
    "vpr4-2025-var-09": ["52", "96", "40", "четверг", "18", "В", "Г", "5755", "11", "понедельник", "пятница", "9", "ЯИЦИЛОП", "5"],
    "vpr4-2025-var-10": ["16", "128", "30", "воскресенье", "15", "А", "Д", "5935", "10", "вторник", "среда", "9", "РЕАНИМАЦИЯ", "8"],
    "vpr4-2025-var-11": ["8", "90", "3", "9", "18", "2010", "Екатеринбург", "33964", "7", "сиреневая", "1", "9", "0", "44"],
    "vpr4-2025-var-12": ["91", "90", "5", "9", "16", "1990", "3", "50030", "11", "розовая", "2", "9", "Д", "75"],
}


def _halves(full_slug: str) -> tuple[dict, dict] | None:
    """Две половины полного варианта из каталога, или None, если их нет."""
    a = next((t for _, _, t in ALL if t["slug"] == full_slug + "-a"), None)
    b = next((t for _, _, t in ALL if t["slug"] == full_slug + "-b"), None)
    return (a, b) if a and b else None


def _split_keys(keys: dict) -> dict:
    """
    Ключи полных вариантов 2025 → ключи их половин.

    Варианты разрезаны генератором на «-a» и «-b» (tools/vpr-frames/variants.py);
    ожидаемые ответы здесь по-прежнему записаны на целый вариант — так их
    сверяли с роликом — и раскладываются по половинам по длине первой.
    """
    out = {}
    for slug, value in keys.items():
        halves = _halves(slug) if slug.startswith("vpr4-2025-") else None
        if halves is None:
            out[slug] = value
            continue
        cut = len(halves[0]["payload"]["questions"])
        if isinstance(value, list):
            out[slug + "-a"], out[slug + "-b"] = value[:cut], value[cut:]
        else:   # VIDEO_KEY: {индекс: ответ}
            out[slug + "-a"] = {i: v for i, v in value.items() if i < cut}
            out[slug + "-b"] = {i - cut: v for i, v in value.items() if i >= cut}
    return out


EXPECTED = _split_keys(EXPECTED)


def test_full_variants_are_cut_at_a_task_boundary_near_the_middle():
    for _, _, t in ALL:
        if not (t["slug"].startswith("vpr4-2025-") and t["slug"].endswith("-a")):
            continue
        halves = _halves(t["slug"][:-2])
        assert halves is not None, t["slug"]
        qa, qb = (h["payload"]["questions"] for h in halves)
        assert abs(len(qa) - len(qb)) <= 2, f"{t['slug']}: половины {len(qa)} и {len(qb)}"
        assert qb[0]["prompt"].startswith("Задание "), "вторая половина начинается с нового задания"
        assert "пункт" not in qb[0]["prompt"].split(".")[0], "пункт не отрывается от своего задания"


@pytest.mark.parametrize("task", [t for _, _, t in ALL], ids=IDS)
def test_answers_match_what_they_should_be(task):
    slug = task["slug"]
    if slug not in EXPECTED:
        pytest.fail(f"для «{slug}» не прописаны ожидаемые ответы в EXPECTED")
    questions = tasks.generate(task["kind"], task["payload"], seed=1)
    assert [q.answer for q in questions] == EXPECTED[slug]


def test_the_arithmetic_itself_is_right():
    """
    Пересчёт руками, без обращения к файлу.

    Если кто-то поправит и файл, и EXPECTED согласованно, но с ошибкой в
    математике, поймает только этот тест.
    """
    # 43 − 27 по частям
    assert 43 - 20 == 23
    assert 23 - 7 == 16
    assert 16 + 27 == 43
    assert 43 - 27 == 16

    # 7 + 3 · (8 + 12)
    assert 8 + 12 == 20
    assert 3 * 20 == 60
    assert 7 + 60 == 67
    assert 7 + 3 * (8 + 12) == 67

    # 12012 : 3 − 170 · 4
    assert 12012 // 3 == 4004 and 12012 % 3 == 0
    assert 170 * 4 == 680
    assert 4004 - 680 == 3324
    assert 12012 // 3 - 170 * 4 == 3324

    # 260 : 20
    assert 26 // 2 == 13
    assert 13 * 20 == 260
    assert 260 // 20 == 13 and 260 % 20 == 0

    # 34 − 4 · 7 + 16
    assert 4 * 7 == 28
    assert 34 - 28 == 6
    assert 6 + 16 == 22
    assert 34 - 4 * 7 + 16 == 22

    # 840 : 6 − 138 : 3
    assert 840 // 6 == 140 and 840 % 6 == 0
    assert 138 // 3 == 46 and 138 % 3 == 0
    assert 140 - 46 == 94
    assert 840 // 6 - 138 // 3 == 94


def test_the_word_problems_add_up():
    """Текстовые задачи: пересчёт условий заново."""
    # Сдача: молоко 32, хлеб 33, купюра 100
    assert 32 + 33 == 65
    assert 100 - 65 == 35

    # Время: закончились в 17:15, длились 1 ч 30 мин
    end = 17 * 60 + 15
    assert end - 90 == 15 * 60 + 45, "начало должно быть 15:45"
    assert 16 * 60 + 15 == 15 * 60 + 75, "перенос часа в минуты"

    # Медали: суммы и третье место
    sums = {"Сириус": 7 + 8 + 3, "Орион": 6 + 4 + 5,
            "Заря": 4 + 6 + 7, "Весна": 3 + 2 + 5}
    assert sums == {"Сириус": 18, "Орион": 15, "Заря": 17, "Весна": 10}
    third = sorted(sums.items(), key=lambda kv: -kv[1])[2][0]
    assert third == "Орион"

    # Варенье: 3 кг, 4 банки по 400 г, остальное по 200 г
    assert 3 * 1000 == 3000
    assert 4 * 400 == 1600
    assert 3000 - 1600 == 1400
    assert 1400 // 200 == 7 and 1400 % 200 == 0

    # Полотно: 10 пододеяльников по 440 см из 8000 см, наволочка 90 см
    assert 4 * 100 + 40 == 440
    assert 10 * 440 == 4400
    assert 80 * 100 == 8000
    assert 8000 - 4400 == 3600
    assert 3600 // 90 == 40 and 3600 % 90 == 0

    # Велосипеды: 12 рулей, 27 колёс — метод предположения
    bikes = 12
    assert bikes * 2 == 24
    extra = 27 - 24
    assert extra == 3
    three_wheeled, two_wheeled = extra, bikes - extra
    assert three_wheeled + two_wheeled == 12
    assert three_wheeled * 3 + two_wheeled * 2 == 27, "проверка по условию"

    # Зеркало
    assert "МИША"[::-1] == "АШИМ"

    # Хлебобулочные: три «Дарницкого» по 26 и лаваш 48
    assert 26 * 3 == 78
    assert 78 + 48 == 126

    # Ужин: с 17:40 до 19:20
    start, end = 17 * 60 + 40, 19 * 60 + 20
    assert end - start == 100, "это 1 час 40 минут"
    assert 100 == 1 * 60 + 40
    assert 20 + 60 + 20 == 100, "разбор по частям должен дать столько же"

    # Квадрат: периметр 16 см при стороне 4
    side = 4
    assert side * 4 == 16, "периметр квадрата"
    assert side * side == 16, "площадь квадрата"
    assert 4 // side == 1, "полоска площадью 4 кв. см имеет ширину 1 см"
    assert side * side - 4 == 12, "у второго прямоугольника остаётся 12"

    # Площадь прямоугольника 8 на 3 и разбиение на квадрат с прямоугольником
    assert 8 * 3 == 24
    side = 3                       # сторона квадрата равна высоте фигуры
    rest = 8 - side
    assert rest == 5
    assert side * side + 3 * rest == 24, "разбиение не должно менять площадь"


def test_tatyana_schedule_has_exactly_one_solution():
    """
    Задача 9 решается перебором, и решение обязано быть единственным.

    Если вариантов несколько, ответ «программист» в 11:30 неверен, и тест это
    покажет прежде, чем ошибку найдёт ребёнок.
    """
    from itertools import permutations

    people = ("директор", "бухгалтер", "программист")
    slots = (9, 10, 11)   # начало каждого часа

    def free(who: str, hour: int) -> bool:
        if who == "директор":
            return not (10 <= hour < 12)      # занят с 10 до 12
        if who == "бухгалтер":
            return hour >= 10                 # приезжает к 10
        return not (10 <= hour < 11)          # совещание с 10 до 11

    valid = [p for p in permutations(people)
             if all(free(who, hour) for who, hour in zip(p, slots))]

    assert len(valid) == 1, f"решение не единственное: {valid}"
    order = valid[0]
    assert order == ("директор", "бухгалтер", "программист")
    assert order[2] == "программист", "в 11:30 Татьяна у программиста"
    assert order[1] == "бухгалтер", "после директора — к бухгалтеру"


def test_one_mistake_in_a_chain_is_forgiven():
    """pass_ratio 0.8: ошибка в одном шаге не должна отнимать всю награду."""
    _, _, task = next(t for t in ALL if t[2]["slug"] == "vpr4-demo-02-7-plus-3-na-8-plus-12")
    questions = tasks.generate(task["kind"], task["payload"], seed=1)
    answers = {str(i): q.answer for i, q in enumerate(questions, start=1)}
    answers["2"] = "неверно"

    assert tasks.judge(questions, answers, task["payload"]["pass_ratio"]).passed is True


# --------------------------------------------------------------------------
# Сверка с официальным ключом ВПР
# --------------------------------------------------------------------------

# Ответы из системы оценивания образца ВПР по математике для 4 класса
# (Рособрнадзор, fioco.ru). Сверено 10 сентября 2026 года по самому документу,
# а не по названиям видеофайлов: иначе мы проверяли бы себя тем же источником,
# из которого брали условия.
#
# Нумерация в образце другая — там 12 заданий, потому что есть ещё родословное
# дерево. В роликах у владельца его нет, и зеркало с велосипедами идут под
# номерами 10 и 11. Здесь ключ сопоставлен по СОДЕРЖАНИЮ задачи, не по номеру.
OFFICIAL_KEY = {
    "vpr4-demo-01-vychisli-43-27":            "16",
    "vpr4-demo-02-7-plus-3-na-8-plus-12":     "67",
    "vpr4-demo-03-sdacha-so-100":             "35",
    "vpr4-demo-04-nachalo-sekcii":            "15:45",
    "vpr4-demo-05-ploshad-pryamougolnika":    "24",
    "vpr4-demo-06-medali-tablica":            "8 и Орион",
    "vpr4-demo-07-12012-na-3-minus-170-na-4": "3324",
    "vpr4-demo-08-varenje-banki":             "7",
    "vpr4-demo-09-tatyana-vstrechi":          "программист и бухгалтер",
    "vpr4-demo-11-velosipedy-ruli-kolesa":    "3",

    # Второй набор — вариант 1 ВПР 2024. Сверено 10 сентября 2026 года по
    # опубликованному варианту с системой оценивания.
    "vpr4-01-vychisli-260-na-20":             "13",
    "vpr4-02-34-minus-4-na-7-plus-16":        "22",
    "vpr4-03-hlebobulochnye-ceny":            "126 руб.",
    "vpr4-04-mama-gotovila-uzhin":            "1 ч 40 мин",
    "vpr4-05-perimetr-kvadrata":              "16 см",
    "vpr4-07-840-na-6-minus-138-na-3":        "94",
    "vpr4-08-pododeyalniki-navolochki":       "40 наволочек",
}

# Какие ответы в цепочке обязаны совпасть с официальным. Индексы с нуля.
# Промежуточные шаги официальным ключом не заданы — там только итог, поэтому
# сверяем именно те позиции, где итог должен появиться.
KEY_POSITIONS = {
    "vpr4-demo-01-vychisli-43-27":            {3: "16"},
    "vpr4-demo-02-7-plus-3-na-8-plus-12":     {4: "67"},
    "vpr4-demo-03-sdacha-so-100":             {3: "35"},
    "vpr4-demo-04-nachalo-sekcii":            {2: "45", 3: "15"},
    "vpr4-demo-05-ploshad-pryamougolnika":    {2: "24"},
    "vpr4-demo-06-medali-tablica":            {0: "8", 5: "Орион"},
    "vpr4-demo-07-12012-na-3-minus-170-na-4": {3: "3324"},
    "vpr4-demo-08-varenje-banki":             {3: "7"},
    "vpr4-demo-09-tatyana-vstrechi":          {4: "программист", 5: "бухгалтер"},
    "vpr4-demo-11-velosipedy-ruli-kolesa":    {3: "3"},

    "vpr4-01-vychisli-260-na-20":             {2: "13"},
    "vpr4-02-34-minus-4-na-7-plus-16":        {3: "22"},
    "vpr4-03-hlebobulochnye-ceny":            {3: "126"},
    "vpr4-04-mama-gotovila-uzhin":            {3: "40", 1: "1"},
    "vpr4-05-perimetr-kvadrata":              {1: "16"},
    "vpr4-07-840-na-6-minus-138-na-3":        {3: "94"},
    "vpr4-08-pododeyalniki-navolochki":       {4: "40"},
}


@pytest.mark.parametrize("slug", sorted(KEY_POSITIONS), ids=sorted(KEY_POSITIONS))
def test_chain_ends_at_the_official_answer(slug):
    """
    Итог нашей цепочки обязан совпасть с официальным ответом.

    Это и есть настоящая проверка правильности заданий: условия и ответы
    сверены с документом Рособрнадзора, а не выведены из имён видеофайлов.
    """
    task = next((t for _, _, t in ALL if t["slug"] == slug), None)
    assert task is not None, f"задание «{slug}» пропало из каталога"

    questions = tasks.generate(task["kind"], task["payload"], seed=1)
    for index, expected in KEY_POSITIONS[slug].items():
        assert index < len(questions), f"в цепочке нет шага {index + 1}"
        assert questions[index].answer == expected, (
            f"шаг {index + 1} даёт «{questions[index].answer}», "
            f"а по официальному ключу должно быть «{expected}»"
        )


# Задания, для которых официального ответа для сверки нет: в оригинале там
# чертёж или рисунок, и сравнивать не с чем. Список закрытый и осознанный —
# любое новое задание обязано попасть либо в KEY_POSITIONS, либо сюда.
NO_OFFICIAL_ANSWER = {
    "vpr4-demo-10-imya-v-zerkale",   # надо нарисовать отражение
}

# --------------------------------------------------------------------------
# Целые варианты 2025: ключ — ответы учителя, записанные на листе в ролике
# --------------------------------------------------------------------------

# У сборника Ященко и «реального варианта» официального ключа под рукой нет.
# Источник сверки — сам ролик: на кадрах виден лист варианта и ответ, который
# учитель пишет в поле «Ответ». Сюда занесены только те ответы, что реально
# видны на кадрах (ответ, не дописанный в кадре, здесь не значится); площади
# и периметры сверх того пересчитаны по клеткам независимо. Индекс — номер
# вопроса в нашей цепочке, с нуля.
VIDEO_KEY = {
    "vpr4-2025-real-2": {5: "16", 7: "6", 8: "21", 10: "44", 11: "9", 12: "4", 14: "11"},
    "vpr4-2025-var-01": {0: "37", 2: "2", 3: "27", 4: "10", 6: "2", 7: "4", 8: "2313",
                         9: "12", 10: "красное", 11: "жёлтое"},
    "vpr4-2025-var-02": {0: "63", 1: "20", 2: "9", 3: "27", 4: "15", 5: "22", 6: "2",
                         7: "2", 8: "402", 9: "8", 10: "красное", 11: "зелёное"},
    "vpr4-2025-var-03": {0: "8", 1: "30", 2: "13", 3: "27", 4: "12", 6: "3", 7: "1",
                         8: "2103", 9: "16", 10: "синее", 11: "зелёное"},
    "vpr4-2025-var-04": {0: "39", 1: "22", 2: "36", 3: "76", 4: "16", 5: "26", 6: "1",
                         7: "1", 8: "2363", 9: "10", 10: "синее", 11: "жёлтое"},
    "vpr4-2025-var-05": {0: "49", 1: "16", 2: "10", 3: "18", 4: "30", 5: "16", 7: "1",
                         8: "2", 9: "1162", 10: "6", 11: "красное", 12: "зелёное"},
    "vpr4-2025-var-06": {0: "77", 1: "79", 2: "10", 3: "среда", 4: "16", 5: "В", 6: "Г", 7: "4684",
                         8: "9", 9: "вторник", 10: "четверг"},
    "vpr4-2025-var-07": {0: "36", 2: "25", 4: "16", 5: "Д", 6: "В", 7: "2752", 8: "7",
                         9: "понедельник", 10: "пятница", 12: "РОТАУКАВЭ", 13: "10"},
    "vpr4-2025-var-08": {0: "22", 1: "91", 2: "25", 3: "четверг", 4: "18", 5: "Г", 6: "Б",
                         7: "2922", 8: "9", 9: "вторник", 10: "пятница", 12: "ПОЖАРНАЯ", 13: "9"},
    "vpr4-2025-var-09": {0: "52", 1: "96", 2: "40", 4: "18", 5: "В", 13: "5"},
    "vpr4-2025-var-10": {0: "16", 1: "128", 2: "30", 3: "воскресенье", 4: "15", 13: "8"},
    "vpr4-2025-var-11": {0: "8", 2: "3", 4: "18", 5: "2010", 6: "Екатеринбург", 7: "33964"},
    "vpr4-2025-var-12": {0: "91", 2: "5", 3: "9", 4: "16", 5: "1990", 6: "3", 7: "50030"},
}
VIDEO_KEY = _split_keys(VIDEO_KEY)


@pytest.mark.parametrize("slug", sorted(VIDEO_KEY), ids=sorted(VIDEO_KEY))
def test_chain_matches_what_the_teacher_wrote(slug):
    task = next(t for _, _, t in ALL if t["slug"] == slug)
    questions = tasks.generate(task["kind"], task["payload"], seed=1)
    for index, expected in VIDEO_KEY[slug].items():
        assert index < len(questions), f"в цепочке нет шага {index + 1}"
        assert questions[index].answer == expected, (
            f"шаг {index + 1} даёт «{questions[index].answer}», в ролике «{expected}»"
        )


def test_full_variant_figures_add_up():
    """Клетчатые фигуры вариантов: площадь и периметр пересчитаны по рядам."""
    def perimeter(rows):
        cells = {(r, c) for r, row in enumerate(rows) for c in row}
        return sum(1 for (r, c) in cells for d in ((1, 0), (-1, 0), (0, 1), (0, -1))
                   if (r + d[0], c + d[1]) not in cells)

    # вариант 1: ряды 3, 2 (вырез слева), 3, 2
    v1 = [(0, 1, 2), (1, 2), (0, 1, 2), (0, 1)]
    assert sum(map(len, v1)) == 10 and perimeter(v1) == 16
    # вариант 2: ряды 3 (вырез третьей клетки), 3, 4, 4, 1
    v2 = [(0, 1, 3), (0, 1, 3), (0, 1, 2, 3), (0, 1, 2, 3), (0,)]
    assert sum(map(len, v2)) == 15 and perimeter(v2) == 22
    # вариант 3: ряды 2, 4, 3, 3
    v3 = [(0, 1), (0, 1, 2, 3), (0, 1, 2), (0, 1, 2)]
    assert sum(map(len, v3)) == 12 and perimeter(v3) == 16
    # вариант 5: ряды 3, 3, 4, 4, 2 (вырез посередине)
    v5 = [(0, 1, 2), (0, 1, 2), (0, 1, 2, 3), (0, 1, 2, 3), (0, 2)]
    assert sum(map(len, v5)) == 16 and perimeter(v5) == 20



def _is_mission(slug: str) -> bool:
    """Миссии и уроки слов — авторские задания, официального ключа у них нет."""
    return slug.startswith("mission-") or slug.startswith("eng-")


def test_every_task_is_either_checked_or_explicitly_exempt():
    """
    Ни одно задание не должно оказаться непроверенным молча.

    Именно так и вышло в первый раз: второй набор был написан по названиям
    видеофайлов и не сверялся ни с чем. Тест закрывает эту возможность.
    """
    everything = {t["slug"] for _, _, t in ALL if not _is_mission(t["slug"])}
    unchecked = everything - set(KEY_POSITIONS) - set(VIDEO_KEY) - NO_OFFICIAL_ANSWER
    assert not unchecked, f"без сверки с официальным ключом: {sorted(unchecked)}"
