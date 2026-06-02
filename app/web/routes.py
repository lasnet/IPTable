import time
from typing import Annotated
from ipaddress import ip_network
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models import CustomField, Folder, IPAddress, IPAddressHistory, PingSchedule, Project, User
from app.services.auth import authenticate_user, create_user, hash_password
from app.services.csv_io import (
    CSVImportError,
    build_zip_archive,
    csv_bytes,
    parse_assets_file,
    render_project_csv,
    render_project_xlsx,
    safe_export_name,
)
from app.services.inventory import create_custom_field, create_project_with_addresses
from app.services.history import FieldChange, build_field_change, record_ip_address_history
from app.services.i18n import translate, translate_error_message
from app.services.network import NetworkValidationError
from app.services.ping import (
    PING_SCHEDULE_FOLDER,
    PING_SCHEDULE_PROJECT,
    enqueue_project_ping,
    ensure_project_ping_schedule,
    set_folder_ping_schedule,
    set_project_ping_schedule,
)
from app.services.security import login_rate_limit_retry_after, record_login_failure, require_csrf_token, reset_login_failures

router = APIRouter(dependencies=[Depends(require_csrf_token)])
SESSION_LAST_ACTIVITY_KEY = "last_activity_at"
PROJECT_TABLE_PAGE_SIZE_OPTIONS = (25, 50, 100, 250)
PROJECT_TABLE_FALLBACK_PAGE_SIZE = 25
PING_STATUS_FILTERS = {"ok", "no", "notest"}


def _templates(request: Request):
    return request.app.state.templates


def _clean_text(value: str, max_len: int) -> str:
    return value.strip()[:max_len]


def _ui(key: str, **kwargs) -> str:
    return translate(get_settings().interface_language, key, **kwargs)


def _ui_error(message: str) -> str:
    return translate_error_message(get_settings().interface_language, message)


def _import_error_message(detail: str) -> str:
    return _ui("import.error_message", detail=detail)


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _redirect_error(path: str, message: str) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    return _redirect(f"{path}{separator}{urlencode({'error': message})}")


def _redirect_message(path: str, message: str) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    return _redirect(f"{path}{separator}{urlencode({'message': message})}")


def _load_sidebar_folders(db: Session) -> list[Folder]:
    folders = db.scalars(
        select(Folder)
        .options(selectinload(Folder.projects))
        .order_by(Folder.name.asc())
    ).all()
    for folder in folders:
        folder.projects.sort(key=_project_sidebar_sort_key)
    return folders


def _project_sidebar_sort_key(project: Project) -> tuple[int, int | str, int, str, int]:
    try:
        network = ip_network(project.cidr, strict=False)
    except ValueError:
        return (1, project.cidr, 0, project.name.lower(), project.id)
    return (0, int(network.network_address), network.prefixlen, project.name.lower(), project.id)


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


def _session_idle_expired(request: Request, settings: Settings, *, now: int | None = None) -> bool:
    if not request.session.get("user_id"):
        return False

    last_activity = request.session.get(SESSION_LAST_ACTIVITY_KEY)
    try:
        last_activity_at = int(last_activity)
    except (TypeError, ValueError):
        return True

    current_time = int(time.time()) if now is None else now
    return current_time - last_activity_at > settings.session_idle_timeout_seconds


def require_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise _login_redirect()

    now = int(time.time())
    if _session_idle_expired(request, settings, now=now):
        request.session.clear()
        raise _login_redirect(_ui("session.expired"))

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


def _client_ip(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host


def _login_rate_limit_message(retry_after_seconds: int) -> str:
    retry_after_minutes = max(1, (retry_after_seconds + 59) // 60)
    return _ui("login.rate_limited", minutes=retry_after_minutes)


def _can_import_csv(user: User) -> bool:
    return user.is_admin or (user.can_create and user.can_edit)


def _nearest_page_size(value: int) -> int:
    return min(PROJECT_TABLE_PAGE_SIZE_OPTIONS, key=lambda option: abs(option - value))


def _normalize_project_page_size(value: int | None, settings: Settings) -> int:
    if value in PROJECT_TABLE_PAGE_SIZE_OPTIONS:
        return int(value)

    default_size = settings.project_table_default_page_size
    if default_size in PROJECT_TABLE_PAGE_SIZE_OPTIONS:
        return default_size
    return _nearest_page_size(default_size or PROJECT_TABLE_FALLBACK_PAGE_SIZE)


def _safe_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _clean_filter_value(value: str | None, max_len: int) -> str:
    return (value or "").strip()[:max_len]


def _project_table_url(
    project_id: int,
    *,
    hide_empty: bool,
    page: int,
    per_page: int,
    ping_status: str = "",
    type_filter: str = "",
    os_filter: str = "",
) -> str:
    params = {
        "hide_empty": str(hide_empty).lower(),
        "page": page,
        "per_page": per_page,
    }
    if ping_status:
        params["ping_status"] = ping_status
    if type_filter:
        params["type_filter"] = type_filter
    if os_filter:
        params["os_filter"] = os_filter
    return f"/projects/{project_id}?{urlencode(params)}"


def _filled_value(column):
    return func.length(func.trim(func.coalesce(column, ""))) > 0


def _ip_record_filled_condition(custom_fields: list[CustomField]):
    conditions = [
        _filled_value(IPAddress.hostname),
        _filled_value(IPAddress.os),
        _filled_value(IPAddress.asset_type),
        _filled_value(IPAddress.comment),
    ]
    for field in custom_fields:
        conditions.append(_filled_value(IPAddress.custom_values[field.key].as_string()))
    return or_(*conditions)


def _pagination_context(
    project_id: int,
    *,
    hide_empty: bool,
    page: int,
    per_page: int,
    total_items: int,
    ping_status: str = "",
    type_filter: str = "",
    os_filter: str = "",
) -> dict:
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    current_page = min(max(1, page), total_pages)
    start_item = ((current_page - 1) * per_page) + 1 if total_items else 0
    end_item = min(current_page * per_page, total_items)
    return {
        "current_page": current_page,
        "total_pages": total_pages,
        "per_page": per_page,
        "options": PROJECT_TABLE_PAGE_SIZE_OPTIONS,
        "total_items": total_items,
        "start_item": start_item,
        "end_item": end_item,
        "offset": (current_page - 1) * per_page,
        "has_prev": current_page > 1,
        "has_next": current_page < total_pages,
        "prev_url": _project_table_url(
            project_id,
            hide_empty=hide_empty,
            page=current_page - 1,
            per_page=per_page,
            ping_status=ping_status,
            type_filter=type_filter,
            os_filter=os_filter,
        ),
        "next_url": _project_table_url(
            project_id,
            hide_empty=hide_empty,
            page=current_page + 1,
            per_page=per_page,
            ping_status=ping_status,
            type_filter=type_filter,
            os_filter=os_filter,
        ),
    }


def _project_table_filter_conditions(*, ping_status: str, type_filter: str, os_filter: str):
    conditions = []
    if ping_status == "ok":
        conditions.append(IPAddress.is_reachable.is_(True))
    elif ping_status == "no":
        conditions.append(IPAddress.is_reachable.is_(False))
    elif ping_status == "notest":
        conditions.append(IPAddress.is_reachable.is_(None))

    if type_filter:
        conditions.append(func.lower(IPAddress.asset_type) == type_filter.lower())
    if os_filter:
        conditions.append(func.lower(IPAddress.os) == os_filter.lower())
    return conditions


def _csv_response(filename: str, content: bytes) -> Response:
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _xlsx_response(filename: str, content: bytes) -> Response:
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _zip_response(filename: str, content: bytes) -> Response:
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _project_export_file(project: Project, ip_records: list[IPAddress], export_format: str) -> tuple[str, bytes, str]:
    if export_format == "xlsx":
        filename = safe_export_name(project.cidr.replace("/", "_"), suffix=".xlsx")
        return filename, render_project_xlsx(project, ip_records), "xlsx"

    filename = safe_export_name(project.cidr.replace("/", "_"), suffix=".csv")
    return filename, csv_bytes(render_project_csv(project, ip_records)), "csv"


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    error: Annotated[str | None, Query(max_length=240)] = None,
) -> Response:
    if request.session.get("user_id"):
        if _session_idle_expired(request, settings):
            request.session.clear()
            error = error or _ui("session.expired")
        else:
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
    settings: Annotated[Settings, Depends(get_settings)],
    username: Annotated[str, Form(min_length=1, max_length=80)],
    password: Annotated[str, Form(min_length=1, max_length=200)],
) -> RedirectResponse:
    client_ip = _client_ip(request)
    retry_after = login_rate_limit_retry_after(
        db,
        client_ip,
        username,
        attempts=settings.login_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
        lockout_seconds=settings.login_rate_limit_lockout_seconds,
    )
    if retry_after is not None:
        return _redirect_error("/login", _login_rate_limit_message(retry_after))

    user = authenticate_user(db, username, password)
    if user is None:
        retry_after = record_login_failure(
            db,
            client_ip,
            username,
            attempts=settings.login_rate_limit_attempts,
            window_seconds=settings.login_rate_limit_window_seconds,
            lockout_seconds=settings.login_rate_limit_lockout_seconds,
        )
        if retry_after is not None:
            return _redirect_error("/login", _login_rate_limit_message(retry_after))
        return _redirect_error("/login", _ui("login.invalid"))

    reset_login_failures(db, client_ip, username)
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
    error_path = "/?new_folder=true"
    if not current_user.can_create_inventory:
        return _redirect_error("/", _ui("folder.create_denied"))

    clean_name = _clean_text(name, 120)
    if not clean_name:
        return _redirect_error(error_path, _ui("folder.name_empty"))

    existing = db.scalar(select(Folder).where(func.lower(Folder.name) == clean_name.lower()))
    if existing:
        return _redirect_error(error_path, _ui("folder.duplicate"))

    db.add(Folder(name=clean_name))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _redirect_error(error_path, _ui("folder.duplicate"))
    return _redirect("/")


@router.post("/folders/{folder_id}/update")
def update_folder(
    folder_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form(min_length=1, max_length=120)],
) -> RedirectResponse:
    error_path = f"/?edit_folder={folder_id}"
    if not current_user.can_edit_inventory:
        return _redirect_error("/", _ui("folder.edit_denied"))

    folder = db.get(Folder, folder_id)
    if folder is None:
        return _redirect_error("/", _ui("folder.not_found"))

    clean_name = _clean_text(name, 120)
    if not clean_name:
        return _redirect_error(error_path, _ui("folder.name_empty"))

    folder.name = clean_name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _redirect_error(error_path, _ui("folder.duplicate"))
    return _redirect("/")


@router.post("/folders/{folder_id}/delete")
def delete_folder(
    folder_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    if not current_user.can_delete_inventory:
        return _redirect_error("/", _ui("folder.delete_denied"))

    folder = db.get(Folder, folder_id)
    if folder is None:
        return _redirect_error("/", _ui("folder.not_found"))

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
        return _redirect_error("/", _ui("project.create_denied"))

    folder = db.get(Folder, folder_id)
    if folder is None:
        return _redirect_error("/", _ui("folder.not_found"))
    error_path = f"/?new_project_folder={folder.id}"

    clean_name = _clean_text(name, 160)
    if not clean_name:
        return _redirect_error(error_path, _ui("project.name_empty"))

    clean_cidr = _clean_text(cidr, 64)
    if not clean_cidr:
        return _redirect_error(error_path, _ui("project.cidr_required"))

    existing = db.scalar(
        select(Project).where(Project.folder_id == folder.id, func.lower(Project.name) == clean_name.lower())
    )
    if existing:
        return _redirect_error(error_path, _ui("project.duplicate"))

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
        return _redirect_error(error_path, _ui_error(str(exc)))
    except IntegrityError:
        db.rollback()
        return _redirect_error(error_path, _ui("project.create_failed_unique"))

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
        return _redirect_error(
            error_path,
            _ui("import.denied"),
        )

    folder = db.get(Folder, folder_id)
    if folder is None:
        return _redirect_error("/", _ui("folder.not_found"))

    clean_name = _clean_text(name, 160)
    if not clean_name:
        return _redirect_error(error_path, _import_error_message(_ui("import.name_required")))

    if not csv_file.filename:
        return _redirect_error(error_path, _import_error_message(_ui("import.file_required")))

    content = await csv_file.read(settings.csv_import_max_bytes + 1)
    if len(content) > settings.csv_import_max_bytes:
        return _redirect_error(
            error_path,
            _import_error_message(_ui("import.file_too_large", max_bytes=settings.csv_import_max_bytes)),
        )

    existing = db.scalar(
        select(Project).where(Project.folder_id == folder.id, func.lower(Project.name) == clean_name.lower())
    )
    if existing:
        return _redirect_error(error_path, _import_error_message(_ui("project.duplicate")))

    try:
        import_result = parse_assets_file(
            content,
            filename=csv_file.filename,
            max_addresses=settings.max_project_addresses,
        )
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
                raise CSVImportError(
                    _ui(
                        "import.ip_outside_subnet",
                        row=row.row_number,
                        address=row.address,
                        cidr=import_result.cidr,
                    )
                )
            ip_record.hostname = row.hostname
            ip_record.os = row.os
            ip_record.asset_type = row.asset_type
            ip_record.comment = row.comment
        db.commit()
    except CSVImportError as exc:
        db.rollback()
        return _redirect_error(error_path, _import_error_message(_ui_error(str(exc))))
    except NetworkValidationError as exc:
        db.rollback()
        return _redirect_error(error_path, _import_error_message(_ui_error(str(exc))))
    except IntegrityError:
        db.rollback()
        return _redirect_error(error_path, _import_error_message(_ui("import.failed_unique")))

    ensure_project_ping_schedule(db, project.id, settings)
    enqueue_project_ping(db, project.id, reason="project-imported")

    return _redirect_message(
        f"/projects/{project.id}?hide_empty=true",
        _ui("import.success", rows=len(import_result.rows), cidr=project.cidr),
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
        return _redirect_error(f"/projects/{project_id}", _ui("project.edit_denied"))

    project = db.get(Project, project_id)
    if project is None:
        return _redirect_error("/", _ui("project.not_found"))

    clean_name = _clean_text(name, 160)
    if not clean_name:
        return _redirect_error(f"/projects/{project_id}", _ui("project.name_empty"))

    project.name = clean_name
    project.description = _clean_text(description, 2000)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _redirect_error(f"/projects/{project_id}", _ui("project.duplicate"))
    return _redirect(f"/projects/{project_id}")


@router.post("/projects/{project_id}/delete")
def delete_project(
    project_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    if not current_user.can_delete_inventory:
        return _redirect_error(f"/projects/{project_id}", _ui("project.delete_denied"))

    project = db.get(Project, project_id)
    if project is None:
        return _redirect_error("/", _ui("project.not_found"))

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
        return _redirect_error("/", _ui("project.not_found"))

    set_project_ping_schedule(
        db,
        project.id,
        enabled=_checked(enabled),
        interval_seconds=interval_minutes * 60,
    )
    if _checked(run_now):
        enqueue_project_ping(db, project.id, reason="manual")
        return _redirect_message(f"/projects/{project.id}", _ui("schedule.project_saved_queued"))

    return _redirect_message(f"/projects/{project.id}", _ui("schedule.project_saved"))


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
        return _redirect_error("/", _ui("folder.not_found"))

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
        return _redirect_message("/", _ui("schedule.folder_saved_queued"))

    return _redirect_message("/", _ui("schedule.folder_saved"))


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(
    project_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(require_user)],
    hide_empty: bool = True,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int | None, Query(ge=10, le=250)] = None,
    ping_status: Annotated[str, Query(max_length=20)] = "",
    type_filter: Annotated[str, Query(max_length=120)] = "",
    os_filter: Annotated[str, Query(max_length=120)] = "",
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
    filled_condition = _ip_record_filled_condition(list(custom_fields))
    clean_ping_status = _clean_filter_value(ping_status, 20).lower()
    if clean_ping_status not in PING_STATUS_FILTERS:
        clean_ping_status = ""
    clean_type_filter = _clean_filter_value(type_filter, 120)
    clean_os_filter = _clean_filter_value(os_filter, 120)
    filter_conditions = _project_table_filter_conditions(
        ping_status=clean_ping_status,
        type_filter=clean_type_filter,
        os_filter=clean_os_filter,
    )

    total_count = db.scalar(select(func.count(IPAddress.id)).where(IPAddress.project_id == project.id)) or 0
    filled_total = db.scalar(
        select(func.count(IPAddress.id)).where(IPAddress.project_id == project.id, filled_condition)
    ) or 0
    visible_conditions = [IPAddress.project_id == project.id, *filter_conditions]
    if hide_empty:
        visible_conditions.append(filled_condition)
    visible_count = db.scalar(select(func.count(IPAddress.id)).where(*visible_conditions)) or 0
    page_size = _normalize_project_page_size(per_page, settings)
    pagination = _pagination_context(
        project.id,
        hide_empty=hide_empty,
        page=page,
        per_page=page_size,
        total_items=visible_count,
        ping_status=clean_ping_status,
        type_filter=clean_type_filter,
        os_filter=clean_os_filter,
    )
    ip_query = select(IPAddress).where(IPAddress.project_id == project.id, *filter_conditions).order_by(IPAddress.ordinal.asc())
    if hide_empty:
        ip_query = ip_query.where(filled_condition)
    ip_records = db.scalars(ip_query.offset(pagination["offset"]).limit(page_size)).all()
    online_count = db.scalar(
        select(func.count(IPAddress.id)).where(IPAddress.project_id == project.id, IPAddress.is_reachable.is_(True))
    ) or 0
    offline_count = db.scalar(
        select(func.count(IPAddress.id)).where(IPAddress.project_id == project.id, IPAddress.is_reachable.is_(False))
    ) or 0
    folders = _load_sidebar_folders(db)
    project_schedule = db.scalar(
        select(PingSchedule).where(
            PingSchedule.scope == PING_SCHEDULE_PROJECT,
            PingSchedule.project_id == project.id,
        )
    )
    type_options = db.scalars(
        select(IPAddress.asset_type)
        .where(IPAddress.project_id == project.id, _filled_value(IPAddress.asset_type))
        .distinct()
        .order_by(IPAddress.asset_type.asc())
    ).all()
    os_options = db.scalars(
        select(IPAddress.os)
        .where(IPAddress.project_id == project.id, _filled_value(IPAddress.os))
        .distinct()
        .order_by(IPAddress.os.asc())
    ).all()

    context = {
        "request": request,
        "project": project,
        "folders": folders,
        "folder_schedules": _load_folder_schedules(db),
        "default_ping_interval_minutes": _default_ping_interval_minutes(),
        "active_project": project,
        "current_user": current_user,
        "custom_fields": custom_fields,
        "project_schedule": project_schedule,
        "project_schedule_minutes": _schedule_interval_minutes(project_schedule, settings),
        "ip_records": ip_records,
        "hide_empty": hide_empty,
        "hide_toggle_url": _project_table_url(
            project.id,
            hide_empty=not hide_empty,
            page=1,
            per_page=pagination["per_page"],
            ping_status=clean_ping_status,
            type_filter=clean_type_filter,
            os_filter=clean_os_filter,
        ),
        "pagination": pagination,
        "table_filters": {
            "ping_status": clean_ping_status,
            "type_filter": clean_type_filter,
            "os_filter": clean_os_filter,
        },
        "filter_options": {
            "ping_statuses": [
                {"value": "", "label": _ui("filter.all_statuses")},
                {"value": "notest", "label": "NoTest"},
                {"value": "ok", "label": "OK"},
                {"value": "no", "label": "NO"},
            ],
            "types": [item for item in type_options if item],
            "oses": [item for item in os_options if item],
        },
        "error": error,
        "message": message,
        "total_count": total_count,
        "filled_count": filled_total,
        "empty_count": max(total_count - filled_total, 0),
        "online_count": online_count,
        "offline_count": offline_count,
        "shown_count": len(ip_records),
        "visible_count": visible_count,
    }
    template_name = "_project_table.html" if request.headers.get("x-requested-with") == "XMLHttpRequest" else "project.html"
    return _templates(request).TemplateResponse(request, template_name, context)


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
        return _redirect_error("/admin/users", _ui("admin.username_empty"))

    existing = db.scalar(select(User).where(func.lower(User.username) == clean_username.lower()))
    if existing:
        return _redirect_error("/admin/users", _ui("admin.duplicate"))

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
        return _redirect_error("/admin/users", _ui("admin.create_failed_unique"))

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
        return _redirect_error("/admin/users", _ui("admin.user_not_found"))

    clean_username = _clean_text(username, 80)
    if not clean_username:
        return _redirect_error("/admin/users", _ui("admin.username_empty"))
    if user.is_admin and clean_username.lower() != user.username.lower():
        return _redirect_error("/admin/users", _ui("admin.username_env"))

    existing = db.scalar(
        select(User).where(func.lower(User.username) == clean_username.lower(), User.id != user.id)
    )
    if existing:
        return _redirect_error("/admin/users", _ui("admin.duplicate"))

    clean_password = password.strip()
    if clean_password and len(clean_password) < 8:
        return _redirect_error("/admin/users", _ui("admin.password_too_short"))

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
        return _redirect_error("/admin/users", _ui("admin.update_failed_unique"))

    return _redirect_message("/admin/users", _ui("admin.updated"))


@router.post("/admin/users/{user_id}/delete")
def admin_delete_user(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
) -> RedirectResponse:
    user = db.get(User, user_id)
    if user is None:
        return _redirect_error("/admin/users", _ui("admin.user_not_found"))
    if user.is_admin:
        return _redirect_error("/admin/users", _ui("admin.cannot_delete_admin"))
    if user.id == current_user.id:
        return _redirect_error("/admin/users", _ui("admin.cannot_delete_self"))

    db.delete(user)
    db.commit()
    return _redirect_message("/admin/users", _ui("admin.deleted"))


@router.post("/projects/{project_id}/export")
def export_project(
    project_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
    export_format: Annotated[str, Form()] = "csv",
    use_password: Annotated[str | None, Form()] = None,
    password: Annotated[str, Form(max_length=200)] = "",
) -> Response:
    project = db.get(Project, project_id)
    if project is None:
        return _redirect_error("/", _ui("project.not_found"))

    ip_records = db.scalars(
        select(IPAddress).where(IPAddress.project_id == project.id).order_by(IPAddress.ordinal.asc())
    ).all()
    clean_format = "xlsx" if export_format == "xlsx" else "csv"
    export_filename, export_content, clean_format = _project_export_file(project, list(ip_records), clean_format)

    if not _checked(use_password):
        if clean_format == "xlsx":
            return _xlsx_response(export_filename, export_content)
        return _csv_response(export_filename, export_content)

    clean_password = password.strip()
    if not clean_password:
        return _redirect_error(f"/projects/{project.id}?export_project=true", _ui("export.password_required_project"))

    try:
        archive = build_zip_archive({export_filename: export_content}, password=clean_password)
    except RuntimeError as exc:
        return _redirect_error(f"/projects/{project.id}?export_project=true", _ui_error(str(exc)))

    return _zip_response(safe_export_name(project.cidr.replace("/", "_"), suffix=".zip"), archive)


@router.post("/folders/{folder_id}/export")
def export_folder(
    folder_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
    export_format: Annotated[str, Form()] = "csv",
    use_password: Annotated[str | None, Form()] = None,
    password: Annotated[str, Form(max_length=200)] = "",
) -> Response:
    folder = db.scalar(
        select(Folder)
        .where(Folder.id == folder_id)
        .options(selectinload(Folder.projects).selectinload(Project.ip_addresses))
    )
    if folder is None:
        return _redirect_error("/", _ui("folder.not_found"))
    error_path = f"/?export_folder={folder.id}"
    if not folder.projects:
        return _redirect_error(error_path, _ui("export.folder_empty"))

    files: dict[str, bytes] = {}
    clean_format = "xlsx" if export_format == "xlsx" else "csv"
    for project in sorted(folder.projects, key=lambda item: item.name.lower()):
        ordered_records = sorted(project.ip_addresses, key=lambda item: item.ordinal)
        filename, content, _ = _project_export_file(project, ordered_records, clean_format)
        files[filename] = content

    clean_password = password.strip() if _checked(use_password) else ""
    if _checked(use_password) and not clean_password:
        return _redirect_error(error_path, _ui("export.password_required_folder"))

    try:
        archive = build_zip_archive(files, password=clean_password or None)
    except RuntimeError as exc:
        return _redirect_error(error_path, _ui_error(str(exc)))

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
        return _redirect_error(f"/projects/{project_id}", _ui("field.edit_denied"))

    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    clean_name = _clean_text(name, 120)
    if not clean_name:
        return _redirect_error(f"/projects/{project_id}", _ui("field.name_empty"))

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
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    ip_record = db.scalar(select(IPAddress).where(IPAddress.id == ip_id, IPAddress.project_id == project_id))
    if ip_record is None:
        raise HTTPException(status_code=404, detail="IP address not found")

    form = await request.form()
    new_hostname = _clean_text(str(form.get("hostname", "")), 255)
    new_os = _clean_text(str(form.get("os", "")), 120)
    new_asset_type = _clean_text(str(form.get("asset_type", "")), 120)
    new_comment = _clean_text(str(form.get("comment", "")), 4000)

    changes: list[FieldChange] = []
    for change in [
        build_field_change("hostname", "Hostname", ip_record.hostname, new_hostname),
        build_field_change("os", "OS", ip_record.os, new_os),
        build_field_change("asset_type", "Type", ip_record.asset_type, new_asset_type),
        build_field_change("comment", "Comment", ip_record.comment, new_comment),
    ]:
        if change is not None:
            changes.append(change)

    ip_record.hostname = new_hostname
    ip_record.os = new_os
    ip_record.asset_type = new_asset_type
    ip_record.comment = new_comment
    custom_fields = db.scalars(
        select(CustomField).where(CustomField.project_id == project_id).order_by(CustomField.position.asc())
    ).all()
    custom_values = dict(ip_record.custom_values or {})
    for field in custom_fields:
        old_value = custom_values.get(field.key, "")
        new_value = _clean_text(str(form.get(f"custom__{field.key}", "")), 1000)
        change = build_field_change(f"custom.{field.key}", field.name, old_value, new_value)
        if change is not None:
            changes.append(change)
        custom_values[field.key] = new_value
    ip_record.custom_values = custom_values
    record_ip_address_history(db, ip_record=ip_record, user=current_user, changes=changes)

    db.commit()
    hide_empty = form.get("hide_empty") == "true"
    page = _safe_positive_int(form.get("page"), 1)
    per_page = _normalize_project_page_size(_safe_positive_int(form.get("per_page"), 0), settings)
    ping_status = _clean_filter_value(str(form.get("ping_status", "")), 20).lower()
    if ping_status not in PING_STATUS_FILTERS:
        ping_status = ""
    type_filter = _clean_filter_value(str(form.get("type_filter", "")), 120)
    os_filter = _clean_filter_value(str(form.get("os_filter", "")), 120)
    return _redirect(
        f"{_project_table_url(
            project_id,
            hide_empty=hide_empty,
            page=page,
            per_page=per_page,
            ping_status=ping_status,
            type_filter=type_filter,
            os_filter=os_filter,
        )}#ip-{ip_id}"
    )


@router.post("/projects/{project_id}/addresses/{ip_id}/clear")
async def clear_ip_address(
    project_id: int,
    ip_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(require_user)],
) -> RedirectResponse:
    ip_record = db.scalar(select(IPAddress).where(IPAddress.id == ip_id, IPAddress.project_id == project_id))
    if ip_record is None:
        raise HTTPException(status_code=404, detail="IP address not found")

    custom_fields = db.scalars(
        select(CustomField).where(CustomField.project_id == project_id).order_by(CustomField.position.asc())
    ).all()
    custom_values = dict(ip_record.custom_values or {})
    changes: list[FieldChange] = []
    for change in [
        build_field_change("hostname", "Hostname", ip_record.hostname, ""),
        build_field_change("os", "OS", ip_record.os, ""),
        build_field_change("asset_type", "Type", ip_record.asset_type, ""),
        build_field_change("comment", "Comment", ip_record.comment, ""),
    ]:
        if change is not None:
            changes.append(change)
    for field in custom_fields:
        old_value = custom_values.get(field.key, "")
        change = build_field_change(f"custom.{field.key}", field.name, old_value, "")
        if change is not None:
            changes.append(change)
        custom_values[field.key] = ""

    ip_record.hostname = ""
    ip_record.os = ""
    ip_record.asset_type = ""
    ip_record.comment = ""
    ip_record.custom_values = custom_values
    record_ip_address_history(db, ip_record=ip_record, user=current_user, changes=changes)
    db.commit()

    form = await request.form()
    hide_empty = form.get("hide_empty") == "true"
    page = _safe_positive_int(form.get("page"), 1)
    per_page = _normalize_project_page_size(_safe_positive_int(form.get("per_page"), 0), settings)
    ping_status = _clean_filter_value(str(form.get("ping_status", "")), 20).lower()
    if ping_status not in PING_STATUS_FILTERS:
        ping_status = ""
    type_filter = _clean_filter_value(str(form.get("type_filter", "")), 120)
    os_filter = _clean_filter_value(str(form.get("os_filter", "")), 120)
    return _redirect(
        f"{_project_table_url(
            project_id,
            hide_empty=hide_empty,
            page=page,
            per_page=per_page,
            ping_status=ping_status,
            type_filter=type_filter,
            os_filter=os_filter,
        )}#ip-{ip_id}"
    )


@router.get("/projects/{project_id}/history", response_class=HTMLResponse)
def project_history(
    project_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
    address: Annotated[str, Query(max_length=64)] = "",
) -> HTMLResponse:
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.folder))
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    clean_address = _clean_text(address, 64)
    history_conditions = [IPAddressHistory.project_id == project.id]
    if clean_address:
        history_conditions.append(IPAddressHistory.address == clean_address)
    history_items = db.scalars(
        select(IPAddressHistory)
        .where(*history_conditions)
        .order_by(IPAddressHistory.created_at.desc(), IPAddressHistory.id.desc())
        .limit(200)
    ).all()

    return _templates(request).TemplateResponse(
        request,
        "history.html",
        {
            "request": request,
            "project": project,
            "history_subtitle": f"{project.cidr} / {clean_address}" if clean_address else project.cidr,
            "history_items": history_items,
            "folders": _load_sidebar_folders(db),
            "folder_schedules": _load_folder_schedules(db),
            "default_ping_interval_minutes": _default_ping_interval_minutes(),
            "active_project": project,
            "current_user": current_user,
        },
    )


@router.get("/folders/{folder_id}/history", response_class=HTMLResponse)
def folder_history(
    folder_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_user)],
) -> HTMLResponse:
    folder = db.get(Folder, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")

    history_items = db.scalars(
        select(IPAddressHistory)
        .join(Project, IPAddressHistory.project_id == Project.id)
        .where(Project.folder_id == folder.id)
        .options(selectinload(IPAddressHistory.project))
        .order_by(IPAddressHistory.created_at.desc(), IPAddressHistory.id.desc())
        .limit(200)
    ).all()

    return _templates(request).TemplateResponse(
        request,
        "history.html",
        {
            "request": request,
            "history_title": _ui("history.title"),
            "history_subtitle": folder.name,
            "history_back_url": "/",
            "history_items": history_items,
            "show_history_project": True,
            "folders": _load_sidebar_folders(db),
            "folder_schedules": _load_folder_schedules(db),
            "default_ping_interval_minutes": _default_ping_interval_minutes(),
            "active_folder_id": folder.id,
            "current_user": current_user,
        },
    )


@router.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
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

    project_page_size = _normalize_project_page_size(None, settings)
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
            "project_page_size": project_page_size,
        },
    )
