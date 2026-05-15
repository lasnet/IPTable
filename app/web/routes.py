import time
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models import CustomField, Folder, IPAddress, PingSchedule, Project, User
from app.services.auth import authenticate_user, create_user, hash_password
from app.services.csv_io import (
    CSVImportError,
    build_zip_archive,
    csv_bytes,
    parse_assets_csv,
    render_project_csv,
    safe_export_name,
)
from app.services.inventory import create_custom_field, create_project_with_addresses, is_ip_record_empty
from app.services.network import NetworkValidationError
from app.services.ping import (
    PING_SCHEDULE_FOLDER,
    PING_SCHEDULE_PROJECT,
    enqueue_project_ping,
    ensure_project_ping_schedule,
    set_folder_ping_schedule,
    set_project_ping_schedule,
)

router = APIRouter()
SESSION_LAST_ACTIVITY_KEY = "last_activity_at"


def _templates(request: Request):
    return request.app.state.templates


def _clean_text(value: str, max_len: int) -> str:
    return value.strip()[:max_len]


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _redirect_error(path: str, message: str) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    return _redirect(f"{path}{separator}{urlencode({'error': message})}")


def _redirect_message(path: str, message: str) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    return _redirect(f"{path}{separator}{urlencode({'message': message})}")


def _load_sidebar_folders(db: Session) -> list[Folder]:
    return db.scalars(
        select(Folder)
        .options(selectinload(Folder.projects))
        .order_by(Folder.name.asc())
    ).all()


def _load_folder_schedules(db: Session) -> dict[int, PingSchedule]:
    schedules = db.scalars(
        select(PingSchedule).where(
            PingSchedule.scope == PING_SCHEDULE_FOLDER,
            PingSchedule.folder_id.is_not(None),
        )
    ).all()
    return {schedule.folder_id: schedule for schedule in schedules if schedule.folder_id is not None}


def _schedule_interval_minutes(schedule: PingSchedule | None, settings: Settings) -> int:
    interval_seconds = schedule.interval_seconds if schedule is not None else settings.ping_interval_seconds
    return max(1, round(interval_seconds / 60))


def _default_ping_interval_minutes() -> int:
    return _schedule_interval_minutes(None, get_settings())


def _login_redirect(message: str | None = None) -> HTTPException:
    location = "/login"
    if message:
        location = f"{location}?{urlencode({'error': message})}"
    return HTTPException(status_code=303, headers={"Location": location})


def require_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise _login_redirect()

    now = int(time.time())
    last_activity = request.session.get(SESSION_LAST_ACTIVITY_KEY)
    try:
        last_activity_at = int(last_activity)
    except (TypeError, ValueError):
        last_activity_at = now

    if now - last_activity_at > settings.session_idle_timeout_seconds:
        request.session.clear()
        raise _login_redirect("Сессия истекла из-за бездействия. Войдите снова.")

    try:
        user_pk = int(user_id)
    except (TypeError, ValueError):
        request.session.clear()
        raise _login_redirect()

    user = db.get(User, user_pk)
    if user is None or not user.is_active:
        request.session.clear()
        raise _login_redirect()

    request.session[SESSION_LAST_ACTIVITY_KEY] = now
    return user


def require_admin(current_user: Annotated[User, Depends(require_user)]) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def _checked(value: str | None) -> bool:
    return value == "on"


def _can_import_csv(user: User) -> bool:
    return user.is_admin or (user.can_create and user.can_edit)


def _csv_response(filename: str, content: bytes) -> Response:
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _zip_response(filename: str, content: bytes) -> Response:
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    error: Annotated[str | None, Query(max_length=240)] = None,
) -> Response:
    if request.session.get("user_id"):
        return _redirect("/")

    return _templates(request).TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": error},
    )


@router.post("/login")
def login(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    username: Annotated[str, Form(min_length=1, max_length=80)],
    password: Annotated[str, Form(min_length=1, max_length=200)],
) -> RedirectResponse:
    user = authenticate_user(db, username, password)
    if user is None:
        return _redirect_error("/login", "Неверный логин или пароль")

    request.session.clear()
    request.session["user_id"] = user.id
    request.session[SESSION_LAST_ACTIVITY_KEY] = int(time.time())
    return _redirect("/")


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return _redirect("/login")


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
    error: Annotated[str | None, Query(max_length=240)] = None,
    message: Annotated[str | None, Query(max_length=240)] = None,
) -> Response:
    folders = _load_sidebar_folders(db)
    first_project = next((project for folder in folders for project in folder.projects), None)
    if first_project is not None and error is None:
        return _redirect(f"/projects/{first_project.id}")

    return _templates(request).TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "folders": folders,
            "folder_schedules": _load_folder_schedules(db),
            "default_ping_interval_minutes": _default_ping_interval_minutes(),
            "active_project": first_project,
            "current_user": current_user,
            "error": error,
            "message": message,
        },
    )


@router.post("/folders")
def create_folder(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form(min_length=1, max_length=120)],
) -> RedirectResponse:
    if not current_user.can_create_inventory:
        return _redirect_error("/", "Недостаточно прав для создания папок")

    clean_name = _clean_text(name, 120)
    if not clean_name:
        return _redirect_error("/", "Название папки не может быть пустым")

    existing = db.scalar(select(Folder).where(func.lower(Folder.name) == clean_name.lower()))
    if existing:
        return _redirect_error("/", "Папка с таким именем уже существует")

    db.add(Folder(name=clean_name))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _redirect_error("/", "Папка с таким именем уже существует")
    return _redirect("/")


@router.post("/folders/{folder_id}/update")
def update_folder(
    folder_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form(min_length=1, max_length=120)],
) -> RedirectResponse:
    if not current_user.can_edit_inventory:
        return _redirect_error("/", "Недостаточно прав для редактирования папок")

    folder = db.get(Folder, folder_id)
    if folder is None:
        return _redirect_error("/", "Папка не найдена")

    clean_name = _clean_text(name, 120)
    if not clean_name:
        return _redirect_error("/", "Название папки не может быть пустым")

    folder.name = clean_name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _redirect_error("/", "Папка с таким именем уже существует")
    return _redirect("/")


@router.post("/folders/{folder_id}/delete")
def delete_folder(
    folder_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    if not current_user.can_delete_inventory:
        return _redirect_error("/", "Недостаточно прав для удаления папок")

    folder = db.get(Folder, folder_id)
    if folder is None:
        return _redirect_error("/", "Папка не найдена")

    db.delete(folder)
    db.commit()
    return _redirect("/")


@router.post("/projects")
async def create_project(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(require_user)],
    folder_id: Annotated[int, Form()],
    name: Annotated[str, Form(min_length=1, max_length=160)],
    cidr: Annotated[str, Form(max_length=64)] = "",
    description: Annotated[str, Form(max_length=2000)] = "",
) -> RedirectResponse:
    if not current_user.can_create_inventory:
        return _redirect_error("/", "Недостаточно прав для создания проектов")

    folder = db.get(Folder, folder_id)
    if folder is None:
        return _redirect_error("/", "Папка не найдена")

    clean_name = _clean_text(name, 160)
    if not clean_name:
        return _redirect_error("/", "Название проекта не может быть пустым")

    clean_cidr = _clean_text(cidr, 64)
    if not clean_cidr:
        return _redirect_error("/", "CIDR подсети обязателен для ручного создания проекта")

    existing = db.scalar(
        select(Project).where(Project.folder_id == folder.id, func.lower(Project.name) == clean_name.lower())
    )
    if existing:
        return _redirect_error("/", "Проект с таким именем уже существует в выбранной папке")

    try:
        project = create_project_with_addresses(
            db,
            folder_id=folder.id,
            name=clean_name,
            cidr=clean_cidr,
            description=description,
            max_addresses=settings.max_project_addresses,
        )
    except NetworkValidationError as exc:
        return _redirect_error("/", str(exc))
    except IntegrityError:
        db.rollback()
        return _redirect_error("/", "Не удалось создать проект: проверьте уникальность имени")

    ensure_project_ping_schedule(db, project.id, settings)
    enqueue_project_ping(db, project.id, reason="project-created")
    return _redirect(f"/projects/{project.id}?hide_empty=false")


@router.post("/folders/{folder_id}/projects/import")
async def import_project_csv(
    folder_id: int,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form(min_length=1, max_length=160)],
    description: Annotated[str, Form(max_length=2000)] = "",
    csv_file: UploadFile = File(...),
) -> RedirectResponse:
    error_path = f"/?new_project_folder={folder_id}"
    if not _can_import_csv(current_user):
        return _redirect_error(error_path, "Импорт CSV доступен только администратору или пользователю с правами создания и редактирования")

    folder = db.get(Folder, folder_id)
    if folder is None:
        return _redirect_error("/", "Папка не найдена")

    clean_name = _clean_text(name, 160)
    if not clean_name:
        return _redirect_error(error_path, "Название проекта обязательно для импорта")

    if not csv_file.filename:
        return _redirect_error(error_path, "Выберите CSV-файл для импорта")

    content = await csv_file.read(settings.csv_import_max_bytes + 1)
    if len(content) > settings.csv_import_max_bytes:
        return _redirect_error(error_path, f"CSV-файл слишком большой. Лимит: {settings.csv_import_max_bytes} байт")

    existing = db.scalar(
        select(Project).where(Project.folder_id == folder.id, func.lower(Project.name) == clean_name.lower())
    )
    if existing:
        return _redirect_error(error_path, "Проект с таким именем уже существует в выбранной папке")

    try:
        import_result = parse_assets_csv(content, max_addresses=settings.max_project_addresses)
        project = create_project_with_addresses(
            db,
            folder_id=folder.id,
            name=clean_name,
            cidr=import_result.cidr,
            description=description,
            max_addresses=settings.max_project_addresses,
        )
        records_by_address = {
            ip_record.address: ip_record
            for ip_record in db.scalars(select(IPAddress).where(IPAddress.project_id == project.id)).all()
        }
        for row in import_result.rows:
            ip_record = records_by_address.get(row.address)
            if ip_record is None:
                raise CSVImportError(f"IP {row.address} не входит в рассчитанную подсеть {import_result.cidr}")
            ip_record.hostname = row.hostname
            ip_record.os = row.os
            ip_record.asset_type = row.asset_type
            ip_record.comment = row.comment
        db.commit()
    except CSVImportError as exc:
        db.rollback()
        return _redirect_error(error_path, str(exc))
    except NetworkValidationError as exc:
        db.rollback()
        return _redirect_error(error_path, str(exc))
    except IntegrityError:
        db.rollback()
        return _redirect_error(error_path, "Не удалось импортировать CSV: проверьте уникальность проекта")

    ensure_project_ping_schedule(db, project.id, settings)
    enqueue_project_ping(db, project.id, reason="project-imported")

    return _redirect_message(
        f"/projects/{project.id}?hide_empty=true",
        f"CSV импортирован: {len(import_result.rows)} строк, рассчитанная подсеть {project.cidr}. Ping-проверка поставлена в очередь",
    )


@router.post("/projects/{project_id}/update")
def update_project(
    project_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form(min_length=1, max_length=160)],
    description: Annotated[str, Form(max_length=2000)] = "",
) -> RedirectResponse:
    if not current_user.can_edit_inventory:
        return _redirect_error(f"/projects/{project_id}", "Недостаточно прав для редактирования проектов")

    project = db.get(Project, project_id)
    if project is None:
        return _redirect_error("/", "Проект не найден")

    clean_name = _clean_text(name, 160)
    if not clean_name:
        return _redirect_error(f"/projects/{project_id}", "Название проекта не может быть пустым")

    project.name = clean_name
    project.description = _clean_text(description, 2000)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _redirect_error(f"/projects/{project_id}", "Проект с таким именем уже существует в выбранной папке")
    return _redirect(f"/projects/{project_id}")


@router.post("/projects/{project_id}/delete")
def delete_project(
    project_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    if not current_user.can_delete_inventory:
        return _redirect_error(f"/projects/{project_id}", "Недостаточно прав для удаления проектов")

    project = db.get(Project, project_id)
    if project is None:
        return _redirect_error("/", "Проект не найден")

    db.delete(project)
    db.commit()
    return _redirect("/")


@router.post("/projects/{project_id}/schedule")
def update_project_ping_schedule(
    project_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
    interval_minutes: Annotated[int, Form(ge=5, le=10080)],
    enabled: Annotated[str | None, Form()] = None,
    run_now: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    project = db.get(Project, project_id)
    if project is None:
        return _redirect_error("/", "Проект не найден")

    set_project_ping_schedule(
        db,
        project.id,
        enabled=_checked(enabled),
        interval_seconds=interval_minutes * 60,
    )
    if _checked(run_now):
        enqueue_project_ping(db, project.id, reason="manual")
        return _redirect_message(f"/projects/{project.id}", "Расписание сохранено, ping-проверка поставлена в очередь")

    return _redirect_message(f"/projects/{project.id}", "Расписание ping-проверки сохранено")


@router.post("/folders/{folder_id}/schedule")
def update_folder_ping_schedule(
    folder_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
    interval_minutes: Annotated[int, Form(ge=5, le=10080)],
    enabled: Annotated[str | None, Form()] = None,
    run_now: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    folder = db.get(Folder, folder_id)
    if folder is None:
        return _redirect_error("/", "Папка не найдена")

    set_folder_ping_schedule(
        db,
        folder.id,
        enabled=_checked(enabled),
        interval_seconds=interval_minutes * 60,
    )
    if _checked(run_now):
        project_ids = db.scalars(
            select(Project.id).where(Project.folder_id == folder.id).order_by(Project.id.asc())
        ).all()
        for project_id in project_ids:
            enqueue_project_ping(db, project_id, reason="manual-folder", commit=False)
        db.commit()
        return _redirect_message("/", "Расписание папки сохранено, проекты поставлены в очередь ping")

    return _redirect_message("/", "Расписание ping-проверки папки сохранено")


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(
    project_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
    hide_empty: bool = True,
    error: Annotated[str | None, Query(max_length=240)] = None,
    message: Annotated[str | None, Query(max_length=240)] = None,
) -> HTMLResponse:
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.custom_fields), selectinload(Project.folder))
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    custom_fields = db.scalars(
        select(CustomField).where(CustomField.project_id == project.id).order_by(CustomField.position.asc())
    ).all()
    all_ip_records = db.scalars(
        select(IPAddress).where(IPAddress.project_id == project.id).order_by(IPAddress.ordinal.asc())
    ).all()
    ip_records = list(all_ip_records)

    if hide_empty:
        ip_records = [ip_record for ip_record in ip_records if not is_ip_record_empty(ip_record)]

    total_count = db.scalar(select(func.count(IPAddress.id)).where(IPAddress.project_id == project.id)) or 0
    filled_total = sum(1 for ip_record in all_ip_records if not is_ip_record_empty(ip_record))
    online_count = db.scalar(
        select(func.count(IPAddress.id)).where(IPAddress.project_id == project.id, IPAddress.is_reachable.is_(True))
    ) or 0
    folders = _load_sidebar_folders(db)
    project_schedule = db.scalar(
        select(PingSchedule).where(
            PingSchedule.scope == PING_SCHEDULE_PROJECT,
            PingSchedule.project_id == project.id,
        )
    )

    return _templates(request).TemplateResponse(
        request,
        "project.html",
        {
            "request": request,
            "project": project,
            "folders": folders,
            "folder_schedules": _load_folder_schedules(db),
            "default_ping_interval_minutes": _default_ping_interval_minutes(),
            "active_project": project,
            "current_user": current_user,
            "custom_fields": custom_fields,
            "project_schedule": project_schedule,
            "project_schedule_minutes": _schedule_interval_minutes(project_schedule, get_settings()),
            "ip_records": ip_records,
            "hide_empty": hide_empty,
            "error": error,
            "message": message,
            "total_count": total_count,
            "filled_count": filled_total,
            "online_count": online_count,
            "shown_count": len(ip_records),
        },
    )


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
    error: Annotated[str | None, Query(max_length=240)] = None,
    message: Annotated[str | None, Query(max_length=240)] = None,
) -> HTMLResponse:
    users = db.scalars(select(User).order_by(User.username.asc())).all()
    return _templates(request).TemplateResponse(
        request,
        "admin_users.html",
        {
            "request": request,
            "folders": _load_sidebar_folders(db),
            "folder_schedules": _load_folder_schedules(db),
            "default_ping_interval_minutes": _default_ping_interval_minutes(),
            "active_project": None,
            "current_user": current_user,
            "users": users,
            "error": error,
            "message": message,
        },
    )


@router.post("/admin/users")
def admin_create_user(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
    username: Annotated[str, Form(min_length=3, max_length=80)],
    password: Annotated[str, Form(min_length=8, max_length=200)],
    first_name: Annotated[str, Form(max_length=120)] = "",
    last_name: Annotated[str, Form(max_length=120)] = "",
    description: Annotated[str, Form(max_length=2000)] = "",
    can_create: Annotated[str | None, Form()] = None,
    can_edit: Annotated[str | None, Form()] = None,
    can_delete: Annotated[str | None, Form()] = None,
    can_manage_columns: Annotated[str | None, Form()] = None,
    is_active: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    clean_username = _clean_text(username, 80)
    if not clean_username:
        return _redirect_error("/admin/users", "Логин не может быть пустым")

    existing = db.scalar(select(User).where(func.lower(User.username) == clean_username.lower()))
    if existing:
        return _redirect_error("/admin/users", "Пользователь с таким логином уже существует")

    try:
        create_user(
            db,
            username=clean_username,
            password=password,
            first_name=_clean_text(first_name, 120),
            last_name=_clean_text(last_name, 120),
            description=_clean_text(description, 2000),
            can_create=_checked(can_create),
            can_edit=_checked(can_edit),
            can_delete=_checked(can_delete),
            can_manage_columns=_checked(can_manage_columns),
            is_active=_checked(is_active),
        )
    except IntegrityError:
        db.rollback()
        return _redirect_error("/admin/users", "Не удалось создать пользователя: логин должен быть уникальным")

    return _redirect("/admin/users")


@router.post("/admin/users/{user_id}/update")
def admin_update_user(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
    username: Annotated[str, Form(min_length=3, max_length=80)],
    first_name: Annotated[str, Form(max_length=120)] = "",
    last_name: Annotated[str, Form(max_length=120)] = "",
    description: Annotated[str, Form(max_length=2000)] = "",
    password: Annotated[str, Form(max_length=200)] = "",
    can_create: Annotated[str | None, Form()] = None,
    can_edit: Annotated[str | None, Form()] = None,
    can_delete: Annotated[str | None, Form()] = None,
    can_manage_columns: Annotated[str | None, Form()] = None,
    is_active: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    user = db.get(User, user_id)
    if user is None:
        return _redirect_error("/admin/users", "Пользователь не найден")

    clean_username = _clean_text(username, 80)
    if not clean_username:
        return _redirect_error("/admin/users", "Логин не может быть пустым")
    if user.is_admin and clean_username.lower() != user.username.lower():
        return _redirect_error("/admin/users", "Логин администратора задается через .env")

    existing = db.scalar(
        select(User).where(func.lower(User.username) == clean_username.lower(), User.id != user.id)
    )
    if existing:
        return _redirect_error("/admin/users", "Пользователь с таким логином уже существует")

    clean_password = password.strip()
    if clean_password and len(clean_password) < 8:
        return _redirect_error("/admin/users", "Новый пароль должен быть не короче 8 символов")

    user.username = clean_username
    user.first_name = _clean_text(first_name, 120)
    user.last_name = _clean_text(last_name, 120)
    user.description = _clean_text(description, 2000)
    if user.is_admin:
        user.is_active = True
        user.can_create = True
        user.can_edit = True
        user.can_delete = True
        user.can_manage_columns = True
    else:
        user.is_active = _checked(is_active)
        user.can_create = _checked(can_create)
        user.can_edit = _checked(can_edit)
        user.can_delete = _checked(can_delete)
        user.can_manage_columns = _checked(can_manage_columns)
    if clean_password:
        user.password_hash = hash_password(clean_password)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _redirect_error("/admin/users", "Не удалось обновить пользователя: логин должен быть уникальным")

    return _redirect_message("/admin/users", "Пользователь обновлен")


@router.post("/admin/users/{user_id}/delete")
def admin_delete_user(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
) -> RedirectResponse:
    user = db.get(User, user_id)
    if user is None:
        return _redirect_error("/admin/users", "Пользователь не найден")
    if user.is_admin:
        return _redirect_error("/admin/users", "Администратора нельзя удалить")
    if user.id == current_user.id:
        return _redirect_error("/admin/users", "Нельзя удалить текущую учетную запись")

    db.delete(user)
    db.commit()
    return _redirect_message("/admin/users", "Пользователь удален")


@router.post("/projects/{project_id}/export")
def export_project(
    project_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
    use_password: Annotated[str | None, Form()] = None,
    password: Annotated[str, Form(max_length=200)] = "",
) -> Response:
    project = db.get(Project, project_id)
    if project is None:
        return _redirect_error("/", "Проект не найден")

    ip_records = db.scalars(
        select(IPAddress).where(IPAddress.project_id == project.id).order_by(IPAddress.ordinal.asc())
    ).all()
    csv_content = csv_bytes(render_project_csv(project, list(ip_records)))
    csv_filename = safe_export_name(project.cidr.replace("/", "_"), suffix=".csv")

    if not _checked(use_password):
        return _csv_response(csv_filename, csv_content)

    clean_password = password.strip()
    if not clean_password:
        return _redirect_error(f"/projects/{project.id}", "Введите пароль для защищенного экспорта")

    try:
        archive = build_zip_archive({csv_filename: csv_content}, password=clean_password)
    except RuntimeError as exc:
        return _redirect_error(f"/projects/{project.id}", str(exc))

    return _zip_response(safe_export_name(project.cidr.replace("/", "_"), suffix=".zip"), archive)


@router.post("/folders/{folder_id}/export")
def export_folder(
    folder_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
    use_password: Annotated[str | None, Form()] = None,
    password: Annotated[str, Form(max_length=200)] = "",
) -> Response:
    folder = db.scalar(
        select(Folder)
        .where(Folder.id == folder_id)
        .options(selectinload(Folder.projects).selectinload(Project.ip_addresses))
    )
    if folder is None:
        return _redirect_error("/", "Папка не найдена")
    if not folder.projects:
        return _redirect_error("/", "В папке нет проектов для экспорта")

    files: dict[str, bytes] = {}
    for project in sorted(folder.projects, key=lambda item: item.name.lower()):
        ordered_records = sorted(project.ip_addresses, key=lambda item: item.ordinal)
        filename = safe_export_name(project.cidr.replace("/", "_"), suffix=".csv")
        files[filename] = csv_bytes(render_project_csv(project, ordered_records))

    clean_password = password.strip() if _checked(use_password) else ""
    if _checked(use_password) and not clean_password:
        return _redirect_error("/", "Введите пароль для защищенного экспорта папки")

    try:
        archive = build_zip_archive(files, password=clean_password or None)
    except RuntimeError as exc:
        return _redirect_error("/", str(exc))

    return _zip_response(safe_export_name(folder.name, suffix=".zip"), archive)


@router.post("/projects/{project_id}/fields")
def add_custom_field(
    project_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form(min_length=1, max_length=120)],
    field_type: Annotated[str, Form()] = "text",
) -> RedirectResponse:
    if not current_user.can_manage_project_columns:
        return _redirect_error(f"/projects/{project_id}", "Недостаточно прав для редактирования столбцов")

    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    clean_name = _clean_text(name, 120)
    if not clean_name:
        return _redirect_error(f"/projects/{project_id}", "Название столбца не может быть пустым")

    allowed_types = {"text", "number", "date"}
    clean_type = field_type if field_type in allowed_types else "text"
    create_custom_field(db, project_id=project_id, name=clean_name, field_type=clean_type)
    return _redirect(f"/projects/{project_id}?hide_empty=false")


@router.post("/projects/{project_id}/addresses/{ip_id}")
async def update_ip_address(
    project_id: int,
    ip_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    ip_record = db.scalar(select(IPAddress).where(IPAddress.id == ip_id, IPAddress.project_id == project_id))
    if ip_record is None:
        raise HTTPException(status_code=404, detail="IP address not found")

    form = await request.form()
    ip_record.hostname = _clean_text(str(form.get("hostname", "")), 255)
    ip_record.os = _clean_text(str(form.get("os", "")), 120)
    ip_record.asset_type = _clean_text(str(form.get("asset_type", "")), 120)
    ip_record.comment = _clean_text(str(form.get("comment", "")), 4000)

    custom_fields = db.scalars(
        select(CustomField).where(CustomField.project_id == project_id).order_by(CustomField.position.asc())
    ).all()
    custom_values = dict(ip_record.custom_values or {})
    for field in custom_fields:
        custom_values[field.key] = _clean_text(str(form.get(f"custom__{field.key}", "")), 1000)
    ip_record.custom_values = custom_values

    db.commit()
    suffix = "?hide_empty=true" if form.get("hide_empty") == "true" else ""
    return _redirect(f"/projects/{project_id}{suffix}#ip-{ip_id}")


@router.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
    q: Annotated[str, Query(max_length=120)] = "",
) -> HTMLResponse:
    query = q.strip()
    pattern = f"%{query}%"
    found_folders: list[Folder] = []
    projects: list[Project] = []
    ip_records: list[IPAddress] = []

    if query:
        found_folders = db.scalars(
            select(Folder).where(Folder.name.ilike(pattern)).order_by(Folder.name.asc()).limit(25)
        ).all()
        projects = db.scalars(
            select(Project)
            .options(selectinload(Project.folder))
            .where(or_(Project.name.ilike(pattern), Project.cidr.ilike(pattern), Project.description.ilike(pattern)))
            .order_by(Project.name.asc())
            .limit(25)
        ).all()
        ip_records = db.scalars(
            select(IPAddress)
            .join(IPAddress.project)
            .options(selectinload(IPAddress.project).selectinload(Project.folder))
            .where(
                or_(
                    IPAddress.address.ilike(pattern),
                    IPAddress.hostname.ilike(pattern),
                    IPAddress.os.ilike(pattern),
                    IPAddress.asset_type.ilike(pattern),
                    IPAddress.comment.ilike(pattern),
                    cast(IPAddress.custom_values, String).ilike(pattern),
                )
            )
            .order_by(IPAddress.address.asc())
            .limit(50)
        ).all()

    return _templates(request).TemplateResponse(
        request,
        "search.html",
        {
            "request": request,
            "q": query,
            "folders": _load_sidebar_folders(db),
            "folder_schedules": _load_folder_schedules(db),
            "default_ping_interval_minutes": _default_ping_interval_minutes(),
            "active_project": None,
            "current_user": current_user,
            "found_folders": found_folders,
            "projects": projects,
            "ip_records": ip_records,
        },
    )
