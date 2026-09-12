# -*- coding: utf-8 -*-
"""
Веб-интерфейс.

Тонкий слой: только принять запрос, позвать сценарий и нарисовать ответ. Вся
логика — в bank.py и service.py, и там же она покрыта тестами.

Три правила этого слоя:

1. Браузеру не уходит ничего лишнего. Правильные ответы, суммы наград и
   идентификаторы Family Link остаются на сервере.

2. Состояние выдачи показывается честно. Пока Family Link не подтвердил
   квоту, на экране «синхронизируется», а не «готово». Один обман — и доверие
   к системе кончится.

3. Кто вошёл, определяет сервер по подписанной куке, а не поле в запросе.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import auth, bank, config, db, service, tasks

HERE = Path(__file__).resolve().parent
COOKIE = "uroki"
COOKIE_MAX_AGE = auth.COOKIE_MAX_AGE

cfg = config.load()
app = FastAPI(title="Учебный банк времени", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

templates = Jinja2Templates(directory=HERE / "templates")

# Защита кода входа от перебора. Один объект на процесс: uvicorn запущен в
# один рабочий процесс, так что счётчики общие для всех запросов.
guard = auth.LoginGuard()


class GuardedFiles:
    """
    Раздача файлов только вошедшему.

    Сайт выходит в интернет, и ролики без этой проверки открывались бы всем по
    прямой ссылке. Проверяем ту же подписанную куку, что и страницы; сам
    файл отдаёт StaticFiles — с поддержкой перемотки, которая нужна плееру.
    """

    def __init__(self, directory: Path) -> None:
        self.files = StaticFiles(directory=directory)

    async def __call__(self, scope, receive, send) -> None:
        cookies = {}
        for name, value in scope.get("headers", ()):
            if name == b"cookie":
                for part in value.decode("latin-1").split(";"):
                    k, _, v = part.strip().partition("=")
                    cookies[k] = v
        if auth.verify(cookies.get(COOKIE), cfg.app_secret) is None:
            response = RedirectResponse("/", status_code=303)
            await response(scope, receive, send)
            return
        await self.files(scope, receive, send)


# Ролики раздаём сами: домен уже разрешён в Chrome у ребёнка, и подключать
# YouTube или облако значило бы открыть ещё один домен безлимитно.
MEDIA = HERE.parent / "media"
MEDIA.mkdir(exist_ok=True)
app.mount("/media", GuardedFiles(MEDIA), name="media")


# --------------------------------------------------------------------------
# База и время
# --------------------------------------------------------------------------


def get_conn():
    conn = db.open_db(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def now_iso() -> str:
    return bank.now(cfg.timezone).isoformat(timespec="seconds")


def today() -> str:
    return bank.today(cfg.timezone)


# --------------------------------------------------------------------------
# Вход
# --------------------------------------------------------------------------


def read_cookie(request: Request) -> int | None:
    return auth.verify(request.cookies.get(COOKIE), cfg.app_secret)


def current_child(request: Request) -> int:
    child_id = read_cookie(request)
    if child_id is None:
        raise HTTPException(status_code=401)
    return child_id


# --------------------------------------------------------------------------
# Страницы ребёнка
# --------------------------------------------------------------------------


def render(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name=name, context=context)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    child_id = read_cookie(request)
    if child_id is None:
        return render(request, "login.html", error=None)

    day = today()
    # Утренний чек-лист — один раз в день, до заданий. Ритуал входа, не сделка.
    if service.checklist_state(conn, child_id, day) is None:
        return RedirectResponse("/checklist", status_code=303)

    view = service.day_view(conn, child_id, day)
    # Ответил «нет» на вопрос про домашку: ничего не записываем, только
    # напоминаем, что она стоит минут. Флаг живёт в адресе, а не в базе.
    nudge = request.query_params.get("dz") == "net"
    morning = request.query_params.get("utro") == "1"
    delivery = conn.execute(
        "SELECT status, target_minutes, applied_minutes FROM delivery "
        " WHERE child_id = ? AND day = ?",
        (child_id, day),
    ).fetchone()

    return render(request, "day.html", view=view, delivery=delivery, nudge=nudge, morning=morning,
                  title_date=service.human_date(day), policy=service.homework_policy(day),
                  homework_state=bank.homework_state(conn, child_id, day),
                  child=conn.execute("SELECT name FROM child WHERE id = ?", (child_id,)).fetchone())


@app.get("/checklist", response_class=HTMLResponse)
def checklist_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    child_id = current_child(request)
    day = today()
    return render(request, "checklist.html", items=service.checklist_items(conn),
                  state=service.checklist_state(conn, child_id, day) or {},
                  title_date=service.human_date(day))


@app.post("/checklist")
async def checklist_submit(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    child_id = current_child(request)
    form = await request.form()
    checked = [str(v) for v in form.getlist("item")]
    items = service.complete_checklist(conn, child_id=child_id, day=today(), checked=checked, at=now_iso())
    # Всё отмечено — главная встречает итогом утра. Флаг в адресе, не в базе.
    return RedirectResponse("/?utro=1" if items and all(items.values()) else "/", status_code=303)


def source_of(request: Request) -> str:
    """
    Откуда пришёл запрос — для счётчика неудачных входов.

    Из интернета сайт виден через VPS, и до нас все запросы доходят с одного
    адреса туннеля. Настоящий адрес посетителя Caddy кладёт в X-Forwarded-For.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    return first or (request.client.host if request.client else "?")


def behind_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


@app.post("/login")
def login(request: Request, pin: str = Form(...), conn: sqlite3.Connection = Depends(get_conn)):
    source = source_of(request)
    wait = guard.retry_after(source)
    if wait:
        minutes = max(1, (wait + 59) // 60)
        response = render(request, "login.html",
                          error=f"Слишком много попыток. Подожди {minutes} мин.")
        response.status_code = 429
        return response

    rows = conn.execute("SELECT id, pin_hash FROM child").fetchall()
    for row in rows:
        if auth.check_pin(pin.strip(), row["pin_hash"]):
            guard.succeeded(source)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                COOKIE, auth.sign(row["id"], int(time.time()), cfg.app_secret),
                max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
                # Через домен сайт открывается только по HTTPS, и кука не
                # должна утекать по незашифрованному запросу. Локально (через
                # ssh -L) HTTPS нет, и там флаг только мешал бы.
                secure=behind_https(request),
            )
            return response
    guard.failed(source)
    return render(request, "login.html", error="Не тот код. Попробуй ещё раз.")


@app.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE)
    return response


# --------------------------------------------------------------------------
# Домашняя работа: отметка с фотографией тетради
# --------------------------------------------------------------------------

# Снимки тетради. Лежат рядом с базой, в appdata: это данные, а не код и не
# видеоуроки. Сервер их не проверяет — проверяет родитель, глазами.
PHOTOS = cfg.db_path.parent / "homework"
PHOTO_MAX_BYTES = 12 * 1024 * 1024
PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
               "image/heic": "heic", "image/heif": "heif"}


def save_photo(upload: UploadFile, data: bytes, *, child_id: int, day: str) -> str:
    """Кладёт снимок на диск и возвращает имя файла. Имя строится сервером."""
    ext = PHOTO_TYPES.get((upload.content_type or "").lower())
    if ext is None:
        raise ValueError("Это не похоже на фотографию.")
    if not data:
        raise ValueError("Файл пустой.")
    if len(data) > PHOTO_MAX_BYTES:
        raise ValueError("Слишком большой файл. Сними ещё раз.")
    PHOTOS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    name = f"{day}-{child_id}-{stamp}.{ext}"
    (PHOTOS / name).write_bytes(data)
    return name


@app.get("/homework/photo", response_class=HTMLResponse)
def homework_photo(request: Request, what: str = "notebook",
                   conn: sqlite3.Connection = Depends(get_conn)):
    """
    Страница «сфотографируй»: без снимка отметка не принимается.

    what=notebook — страница тетради для «сделал»; what=diary — страница
    дневника для «не задавали» в те дни, когда на слово не верим.
    """
    child_id = current_child(request)
    day = today()
    bal = bank.balance(conn, child_id, day)
    return render(request, "homework-photo.html", balance=bal, error=None,
                  what="diary" if what == "diary" else "notebook",
                  policy=service.homework_policy(day))


@app.post("/homework")
async def homework(request: Request, action: str = Form("done"),
                   photo: UploadFile | None = File(None),
                   conn: sqlite3.Connection = Depends(get_conn)):
    child_id = current_child(request)
    day = today()
    policy = service.homework_policy(day)
    if action == "no":
        return RedirectResponse("/?dz=net", status_code=303)

    if action == "nothing":
        if not policy.nothing_allowed:
            return RedirectResponse("/", status_code=303)   # кнопки и не было
        marked_as, what = "nothing_assigned", "diary"
        needs_photo = policy.nothing_needs_photo
    else:
        marked_as, what = "done", "notebook"
        needs_photo = True

    # «Сделал» принимается только со снимком страницы. Не потому что сервер
    # умеет его читать — не умеет, — а потому что снять пустую страницу это
    # уже осознанный обман, и родитель увидит его за пять секунд. Для «не
    # задавали» в будни — то же самое, только со страницей дневника.
    name = ""
    has_photo = photo is not None and bool(photo.filename)
    if needs_photo and not has_photo:
        return RedirectResponse(f"/homework/photo?what={what}", status_code=303)
    if has_photo:
        try:
            data = await photo.read()
            name = save_photo(photo, data, child_id=child_id, day=day)
        except ValueError as exc:
            bal = bank.balance(conn, child_id, day)
            return render(request, "homework-photo.html", balance=bal, error=str(exc),
                          what=what, policy=policy)

    bank.mark_homework(conn, child_id=child_id, day=day, at=now_iso(),
                       marked_as=marked_as, marked_by="child", photo_file=name)
    return RedirectResponse("/", status_code=303)


# --------------------------------------------------------------------------
# Родительская страница: снимки тетради и отзыв отметки
# --------------------------------------------------------------------------

PARENT_COOKIE = "uroki_parent"
PARENT_ID = 0   # в подписи куки вместо child_id; ребёнок с таким id не существует


def parent_logged_in(request: Request) -> bool:
    return auth.verify(request.cookies.get(PARENT_COOKIE), cfg.app_secret) == PARENT_ID


@app.get("/parent", response_class=HTMLResponse)
def parent_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    if not parent_logged_in(request):
        return render(request, "parent-login.html", error=None)
    rows = conn.execute(
        "SELECT h.*, c.name AS child_name FROM homework h JOIN child c ON c.id = h.child_id "
        " ORDER BY h.day DESC, h.child_id LIMIT 30"
    ).fetchall()
    child = conn.execute("SELECT id FROM child ORDER BY id LIMIT 1").fetchone()
    since = (date.fromisoformat(today()) - timedelta(days=13)).isoformat()
    pilot_days, pilot_items = service.pilot_summary(conn, child["id"], since=since) if child else ([], [])
    drafts = [dict(r) for r in conn.execute(
        "SELECT id, slug, title, skill, kind, payload FROM task WHERE status = 'draft' ORDER BY skill, id"
    ).fetchall()]
    # Родитель видит миссию целиком — условия, ответы, подсказки. Ребёнку
    # такое не показывается никогда; это единственная страница с ответами.
    for d in drafts:
        payload = json.loads(d.pop("payload") or "{}")
        d["cards"] = tasks.mission_cards(payload) if d["kind"] == tasks.MISSION else []
        d["questions"] = [] if d["kind"] == tasks.MISSION else tasks.generate(d["kind"], payload, seed=1)
    checklists = service.checklist_history(conn, child["id"], since=since) if child else []
    # Сегодня одним взглядом: цель сервера, что стоит на планшете, аванс.
    balance = bank.balance(conn, child["id"], today()) if child else None
    delivery_row = conn.execute("SELECT * FROM delivery WHERE child_id = ? AND day = ?",
                                (child["id"], today())).fetchone() if child else None
    grants = conn.execute("SELECT day, minutes, reason, created_at FROM parent_grant "
                          " WHERE child_id = ? AND day >= ? ORDER BY id DESC",
                          (child["id"], since)).fetchall() if child else []
    return render(request, "parent.html", rows=rows, today=today(), drafts=drafts,
                  pilot_days=pilot_days, pilot_items=pilot_items[:30], checklists=checklists,
                  manual_total=sum(d.manual for d in pilot_days), balance=balance,
                  delivery_row=delivery_row, grants=grants, child_id=child["id"] if child else 0)


@app.post("/parent/grant")
def parent_grant(request: Request, child_id: int = Form(...), minutes: int = Form(...),
                 reason: str = Form(""), conn: sqlite3.Connection = Depends(get_conn)):
    """
    Минуты от родителя. Единственный законный способ добавить время мимо
    заданий: ложится в журнал, входит в цель, видно в сводке. Правка руками в
    Family Link — аванс, а не подарок (см. delivery.py).
    """
    if not parent_logged_in(request):
        raise HTTPException(status_code=401)
    try:
        bank.parent_grant(conn, child_id=child_id, day=today(), minutes=minutes,
                          reason=reason, at=now_iso())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse("/parent#today", status_code=303)


@app.post("/parent/review")
def parent_review(request: Request, task_id: int = Form(...), decision: str = Form(...),
                  conn: sqlite3.Connection = Depends(get_conn)):
    """Утвердить или отклонить черновик. В каталог попадает только утверждённое."""
    if not parent_logged_in(request):
        raise HTTPException(status_code=401)
    status = "active" if decision == "approve" else "rejected"
    with conn:
        conn.execute("UPDATE task SET status = ?, active = ? WHERE id = ? AND status = 'draft'",
                     (status, 1 if status == "active" else 0, task_id))
    return RedirectResponse("/parent#drafts", status_code=303)


@app.post("/parent/login")
def parent_login(request: Request, password: str = Form(...),
                 conn: sqlite3.Connection = Depends(get_conn)):
    source = "parent:" + source_of(request)
    if guard.retry_after(source):
        response = render(request, "parent-login.html", error="Слишком много попыток. Подождите 10 минут.")
        response.status_code = 429
        return response
    stored = bank.get_setting(conn, bank.SETTING_PARENT_PIN)
    if not stored:
        return render(request, "parent-login.html",
                      error="Пароль родителя не задан. На сервере: python3 tools/set_pin.py --parent")
    if not auth.check_pin(password.strip(), stored):
        guard.failed(source)
        return render(request, "parent-login.html", error="Не тот пароль.")
    guard.succeeded(source)
    response = RedirectResponse("/parent", status_code=303)
    response.set_cookie(PARENT_COOKIE, auth.sign(PARENT_ID, int(time.time()), cfg.app_secret),
                        max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
                        secure=behind_https(request))
    return response


@app.post("/parent/revoke")
def parent_revoke(request: Request, child_id: int = Form(...), day: str = Form(...),
                  reason: str = Form(""), conn: sqlite3.Connection = Depends(get_conn)):
    if not parent_logged_in(request):
        raise HTTPException(status_code=401)
    bank.revoke_homework(conn, child_id=child_id, day=day, at=now_iso(),
                         reason=reason.strip() or "отозвано родителем")
    return RedirectResponse("/parent", status_code=303)


@app.get("/parent/photo/{name}")
def parent_photo(request: Request, name: str):
    if not parent_logged_in(request):
        raise HTTPException(status_code=401)
    # Имя файла придумал сервер; всё, что не похоже на него, — не наш файл.
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]+-[0-9]{6}\.[a-z]+", name):
        raise HTTPException(status_code=404)
    path = PHOTOS / name
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)


def _open_attempt(conn, request, child_id: int, assignment_id: int):
    """Находит задание дня и открытую попытку, начиная новую при необходимости."""
    view = service.day_view(conn, child_id, today())
    item = next((i for i in view.items if i.assignment_id == assignment_id), None)
    if item is None:
        raise HTTPException(status_code=404)
    if item.state == service.EARNED:
        return view, item, None
    attempt_id = item.attempt_id or service.start_attempt(
        conn, child_id=child_id, assignment_id=assignment_id, at=now_iso()
    )
    return view, item, attempt_id


@app.get("/task/{assignment_id}", response_class=HTMLResponse)
def task_page(request: Request, assignment_id: int,
              conn: sqlite3.Connection = Depends(get_conn)):
    child_id = current_child(request)
    try:
        view, item, attempt_id = _open_attempt(conn, request, child_id, assignment_id)
        if attempt_id is None:
            return RedirectResponse("/", status_code=303)

        if item.kind == tasks.MISSION:
            state = service.mission_state(conn, child_id=child_id, attempt_id=attempt_id)
            if state.finished:
                return RedirectResponse("/", status_code=303)
            return render(request, "mission.html", item=item, state=state,
                          view=view, judgement=None)

        questions = service.attempt_questions(conn, child_id=child_id, attempt_id=attempt_id)
    except service.ServiceError as exc:
        return render(request, "message.html", title="Не получилось", text=str(exc))

    return render(request, "task.html", item=item, attempt_id=attempt_id,
                  questions=questions, view=view)


@app.post("/hint/{assignment_id}")
def hint(request: Request, assignment_id: int,
         conn: sqlite3.Connection = Depends(get_conn)):
    """
    Открывает следующую подсказку. Подсказки бесплатны: по методике их можно
    просить сразу, и они не уменьшают награду.
    """
    child_id = current_child(request)
    try:
        _, item, attempt_id = _open_attempt(conn, request, child_id, assignment_id)
        if attempt_id is not None and item.kind == tasks.MISSION:
            service.reveal_hint(conn, child_id=child_id, attempt_id=attempt_id)
    except service.ServiceError as exc:
        return render(request, "message.html", title="Не получилось", text=str(exc))
    return RedirectResponse(f"/task/{assignment_id}", status_code=303)


@app.post("/task/{assignment_id}", response_class=HTMLResponse)
async def task_submit(request: Request, assignment_id: int,
                      conn: sqlite3.Connection = Depends(get_conn)):
    child_id = current_child(request)
    form = await request.form()

    attempt_id = int(form.get("attempt_id") or 0)
    answers = {k[1:]: str(v) for k, v in form.items() if k.startswith("q")}
    view = service.day_view(conn, child_id, today())
    item = next((i for i in view.items if i.assignment_id == assignment_id), None)

    try:
        if item is not None and item.kind == tasks.MISSION:
            card = service.submit_card(conn, child_id=child_id, attempt_id=attempt_id,
                                       answers=answers, at=now_iso())
            if not card.advanced:
                # Остаёмся на той же карточке: можно открыть подсказку и
                # попробовать снова. Новую карточку раньше времени не даём.
                state = service.mission_state(conn, child_id=child_id, attempt_id=attempt_id)
                return render(request, "mission.html", item=item, state=state,
                              view=service.day_view(conn, child_id, today()),
                              judgement=card.judgement.for_child())
            if not card.mission_done:
                return RedirectResponse(f"/task/{assignment_id}", status_code=303)
            return render(request, "mission-done.html", result=card, item=item,
                          attempt_id=attempt_id,
                          view=service.day_view(conn, child_id, today()))

        result = service.submit(conn, child_id=child_id, attempt_id=attempt_id,
                                answers=answers, at=now_iso())
    except service.ServiceError as exc:
        return render(request, "message.html", title="Не получилось", text=str(exc))

    return render(request, "result.html", result=result, item=item,
                  attempt_id=attempt_id,
                  view=service.day_view(conn, child_id, today()),
                  child_view=result.judgement.for_child())


@app.post("/rate/{attempt_id}")
def rate(request: Request, attempt_id: int, rating: str = Form(...), comment: str = Form(""),
         conn: sqlite3.Connection = Depends(get_conn)):
    """Добровольная оценка задания. Ничего не даёт и ничего не отнимает."""
    child_id = current_child(request)
    try:
        service.rate_attempt(conn, child_id=child_id, attempt_id=attempt_id,
                             rating=rating, comment=comment, at=now_iso())
    except service.ServiceError:
        pass   # чужая или несуществующая попытка — просто на главную
    return RedirectResponse("/", status_code=303)


@app.get("/status")
def status(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    """
    Состояние выдачи для автообновления плашки на экране дня.

    Отдаёт ровно то, что можно показать: цифры и статус, без идентификаторов.
    """
    child_id = current_child(request)
    day = today()
    bal = bank.balance(conn, child_id, day)
    row = conn.execute(
        "SELECT status, target_minutes, applied_minutes FROM delivery "
        " WHERE child_id = ? AND day = ?", (child_id, day),
    ).fetchone()
    return {
        "target": bal.target,
        "earned": bal.earned,
        "locked": bal.locked,
        "homework": bal.gate_open,
        "homework_bonus": bal.homework_bonus,
        "delivery": row["status"] if row else "none",
        "applied": row["applied_minutes"] if row else None,
    }


@app.exception_handler(401)
def unauthorized(request: Request, exc):  # noqa: ANN001
    target = "/parent" if request.url.path.startswith("/parent") else "/"
    return RedirectResponse(target, status_code=303)
