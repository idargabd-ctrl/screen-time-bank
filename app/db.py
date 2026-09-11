# -*- coding: utf-8 -*-
"""
База данных.

Обычный sqlite3 из стандартной библиотеки, без ORM. Решение сознательное:
здесь хранится журнал заработанного времени, и в таком месте важнее видеть
запросы и границы транзакций глазами, чем экономить строки. Схема маленькая,
пишущих процессов два — выгоды от ORM почти нет, а неявного поведения он
добавляет много.

Режим WAL включён потому, что писать будут веб-приложение и фоновый процесс
одновременно.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
-- Ребёнок. Пока один, но зашивать это в код незачем.
CREATE TABLE IF NOT EXISTS child (
    id           INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL,
    fl_child_id  TEXT    NOT NULL,
    fl_device_id TEXT    NOT NULL,
    pin_hash     TEXT    NOT NULL DEFAULT ''
);

-- Настройки банка времени. Меняются родителем, поэтому в базе, а не в .env.
CREATE TABLE IF NOT EXISTS setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Каталог заданий. version меняется при правке содержания: он входит в ключ
-- идемпотентности, и исправленное задание можно выдать заново осознанно.
CREATE TABLE IF NOT EXISTS task (
    id             INTEGER PRIMARY KEY,
    slug           TEXT    UNIQUE,
    kind           TEXT    NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1,
    title          TEXT    NOT NULL,
    subject        TEXT    NOT NULL DEFAULT '',
    source         TEXT    NOT NULL DEFAULT '',
    -- Раздел на экране дня: vpr — разбор задания ВПР с видео, missions —
    -- короткие миссии по методике. Разделы живут отдельно и по разным
    -- правилам: у миссий карточки показываются по одной.
    section        TEXT    NOT NULL DEFAULT 'vpr',
    -- Имя файла в media/. Ролик отдаётся с нашего же домена: он уже в списке
    -- одобренных сайтов Chrome, и добавлять чужой домен не нужно.
    video_file     TEXT    NOT NULL DEFAULT '',
    payload        TEXT    NOT NULL DEFAULT '{}',
    reward_minutes INTEGER NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1
);

-- Что назначено на конкретный день.
CREATE TABLE IF NOT EXISTS assignment (
    id       INTEGER PRIMARY KEY,
    child_id INTEGER NOT NULL REFERENCES child(id),
    task_id  INTEGER NOT NULL REFERENCES task(id),
    day      TEXT    NOT NULL,
    UNIQUE (child_id, task_id, day)
);

-- Попытка выполнения. Вопросы фиксируются при выдаче, чтобы ответ проверялся
-- по тому же набору, который ребёнок видел.
CREATE TABLE IF NOT EXISTS attempt (
    id            INTEGER PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES assignment(id),
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    questions     TEXT    NOT NULL,
    answers       TEXT,
    correct       INTEGER,
    total         INTEGER,
    passed        INTEGER,
    -- Для миссий: номер карточки, на которой ребёнок сейчас. Карточки нельзя
    -- показывать вместе — следующая иногда объясняет предыдущую.
    stage         INTEGER NOT NULL DEFAULT 0,
    -- Какие подсказки уже открыты, JSON вида {"0": 2} — на карточке 0 открыто
    -- две подсказки. Нужно, чтобы после перезагрузки страницы они не закрылись.
    hints_used    TEXT    NOT NULL DEFAULT '{}'
);

-- Домашняя работа: ворота, а не задание за минуты.
--
-- Заработанные на сайте минуты копятся всегда, но в квоту попадают только
-- когда за день есть непогашенная отметка. Так домашка обязательна, но
-- ребёнок не наказан за порядок: сделал уроки на сайте до тетради — минуты
-- не пропали, просто ждут.
--
-- marked_as: done — сделал; nothing_assigned — не задавали (бывают выходные,
-- и без этого варианта ворота заперли бы день, в котором заперать нечего).
--
-- Отметка не удаляется, а гасится: revoked_at сохраняет историю, чтобы было
-- видно, что отметка была и её отозвали, а не что её не было.
CREATE TABLE IF NOT EXISTS homework (
    id             INTEGER PRIMARY KEY,
    child_id       INTEGER NOT NULL REFERENCES child(id),
    day            TEXT    NOT NULL,
    marked_at      TEXT    NOT NULL,
    marked_as      TEXT    NOT NULL DEFAULT 'done',
    marked_by      TEXT    NOT NULL DEFAULT 'child',
    revoked_at     TEXT,
    revoked_reason TEXT,
    photo_file     TEXT    NOT NULL DEFAULT '',   -- снимок тетради, имя файла в appdata/homework
    UNIQUE (child_id, day)
);

-- Журнал наград. UNIQUE — это и есть идемпотентность: две вкладки, повторная
-- отправка и перезапуск сервера не создадут вторую награду за то же задание.
CREATE TABLE IF NOT EXISTS reward (
    id           INTEGER PRIMARY KEY,
    child_id     INTEGER NOT NULL REFERENCES child(id),
    task_id      INTEGER NOT NULL REFERENCES task(id),
    task_version INTEGER NOT NULL,
    day          TEXT    NOT NULL,
    minutes      INTEGER NOT NULL,
    attempt_id   INTEGER REFERENCES attempt(id),
    created_at   TEXT    NOT NULL,
    UNIQUE (child_id, task_id, task_version, day)
);

CREATE INDEX IF NOT EXISTS reward_by_day ON reward (child_id, day);

-- Выдача. Живёт в базе, а не в памяти, чтобы пережить перезапуск.
--
-- Одна строка на ребёнка и день, а не очередь записей. Так задумано: мы ставим
-- абсолютную квоту, поэтому промежуточные значения не нужны — важна только
-- последняя цель. Строка сама по себе идемпотентна.
--
-- status: pending | confirmed | not_applied | unknown
--   pending      — цель изменилась, надо записать в Family Link
--   confirmed    — записали и перечитали, значение совпало
--   not_applied  — Google принял, но при чтении значение другое
--   unknown      — результат неизвестен: библиотека возвращает успех и при
--                  внутренней ошибке, поэтому «принято» ничего не доказывает
CREATE TABLE IF NOT EXISTS delivery (
    id              INTEGER PRIMARY KEY,
    child_id        INTEGER NOT NULL REFERENCES child(id),
    day             TEXT    NOT NULL,
    target_minutes  INTEGER NOT NULL,
    applied_minutes INTEGER,
    status          TEXT    NOT NULL DEFAULT 'pending',
    tries           INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    UNIQUE (child_id, day)
);

CREATE INDEX IF NOT EXISTS delivery_pending ON delivery (status, day);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False обязателен из-за того, как работает веб-слой:
    # зависимость, открывающая соединение, выполняется в пуле потоков, а
    # обработчик запроса — в цикле событий, и sqlite3 по умолчанию такой
    # переход запрещает.
    #
    # Безопасно потому, что соединение живёт ровно один запрос и никогда не
    # используется двумя запросами одновременно. Разделять одно соединение
    # между запросами нельзя — тогда этот флаг превратится в гонку.
    conn = sqlite3.connect(path, isolation_level=None, timeout=15,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    # Компромисс между сохранностью и износом диска на старом ноутбуке.
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# Колонки, добавленные после первого выпуска. CREATE TABLE IF NOT EXISTS
# существующую таблицу не меняет, поэтому новые поля доезжают только так.
#
# Порядок здесь не важен, повтор безвреден: перед добавлением смотрим, чего в
# таблице нет. Полноценная система миграций для одной семьи была бы дороже
# пользы, но молча расходящаяся схема — источник ошибок вида «нет такой
# колонки» через месяц.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("task", "slug", "TEXT"),
    ("task", "subject", "TEXT NOT NULL DEFAULT ''"),
    ("task", "source", "TEXT NOT NULL DEFAULT ''"),
    ("task", "video_file", "TEXT NOT NULL DEFAULT ''"),
    ("task", "section", "TEXT NOT NULL DEFAULT 'vpr'"),
    ("attempt", "stage", "INTEGER NOT NULL DEFAULT 0"),
    ("attempt", "hints_used", "TEXT NOT NULL DEFAULT '{}'"),
    ("delivery", "applied_minutes", "INTEGER"),
    ("homework", "photo_file", "TEXT NOT NULL DEFAULT ''"),
]


def migrate(conn: sqlite3.Connection) -> list[str]:
    applied: list[str] = []
    for table, column, decl in MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue          # таблицы ещё нет — её создаст SCHEMA
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        applied.append(f"{table}.{column}")
    return applied


def open_db(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    init(conn)
    migrate(conn)
    return conn
