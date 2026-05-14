from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models import CustomField, Folder, IPAddress, Project
from app.services.inventory import create_custom_field, create_project_with_addresses, is_ip_record_empty
from app.services.network import NetworkValidationError

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _clean_text(value: str, max_len: int) -> str:
    return value.strip()[:max_len]


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _redirect_error(path: str, message: str) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    return _redirect(f"{path}{separator}{urlencode({'error': message})}")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    error: Annotated[str | None, Query(max_length=240)] = None,
) -> HTMLResponse:
    folders = db.scalars(
        select(Folder)
        .options(selectinload(Folder.projects))
        .order_by(Folder.name.asc())
    ).all()
    return _templates(request).TemplateResponse(
        request,
        "index.html",
        {"request": request, "folders": folders, "error": error},
    )


@router.post("/folders")
def create_folder(
    db: Annotated[Session, Depends(get_db)],
    name: Annotated[str, Form(min_length=1, max_length=120)],
) -> RedirectResponse:
    clean_name = _clean_text(name, 120)
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


@router.post("/projects")
def create_project(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    folder_id: Annotated[int, Form()],
    name: Annotated[str, Form(min_length=1, max_length=160)],
    cidr: Annotated[str, Form(min_length=1, max_length=64)],
    description: Annotated[str, Form(max_length=2000)] = "",
) -> RedirectResponse:
    folder = db.get(Folder, folder_id)
    if folder is None:
        return _redirect_error("/", "Папка не найдена")

    clean_name = _clean_text(name, 160)
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
            cidr=cidr,
            description=description,
            max_addresses=settings.max_project_addresses,
        )
    except NetworkValidationError as exc:
        return _redirect_error("/", str(exc))
    except IntegrityError:
        db.rollback()
        return _redirect_error("/", "Не удалось создать проект: проверьте уникальность имени")

    return _redirect(f"/projects/{project.id}")


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(
    project_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    hide_empty: bool = False,
    error: Annotated[str | None, Query(max_length=240)] = None,
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
    ip_records = db.scalars(
        select(IPAddress).where(IPAddress.project_id == project.id).order_by(IPAddress.ordinal.asc())
    ).all()

    if hide_empty:
        ip_records = [ip_record for ip_record in ip_records if not is_ip_record_empty(ip_record)]

    total_count = db.scalar(select(func.count(IPAddress.id)).where(IPAddress.project_id == project.id)) or 0
    filled_count = sum(1 for ip_record in ip_records if not is_ip_record_empty(ip_record))
    online_count = db.scalar(
        select(func.count(IPAddress.id)).where(IPAddress.project_id == project.id, IPAddress.is_reachable.is_(True))
    ) or 0

    return _templates(request).TemplateResponse(
        request,
        "project.html",
        {
            "request": request,
            "project": project,
            "custom_fields": custom_fields,
            "ip_records": ip_records,
            "hide_empty": hide_empty,
            "error": error,
            "total_count": total_count,
            "filled_count": filled_count,
            "online_count": online_count,
        },
    )


@router.post("/projects/{project_id}/fields")
def add_custom_field(
    project_id: int,
    db: Annotated[Session, Depends(get_db)],
    name: Annotated[str, Form(min_length=1, max_length=120)],
    field_type: Annotated[str, Form()] = "text",
) -> RedirectResponse:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    allowed_types = {"text", "number", "date"}
    clean_type = field_type if field_type in allowed_types else "text"
    create_custom_field(db, project_id=project_id, name=_clean_text(name, 120), field_type=clean_type)
    return _redirect(f"/projects/{project_id}")


@router.post("/projects/{project_id}/addresses/{ip_id}")
async def update_ip_address(
    project_id: int,
    ip_id: int,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
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
    q: Annotated[str, Query(max_length=120)] = "",
) -> HTMLResponse:
    query = q.strip()
    pattern = f"%{query}%"
    folders: list[Folder] = []
    projects: list[Project] = []
    ip_records: list[IPAddress] = []

    if query:
        folders = db.scalars(
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
            "folders": folders,
            "projects": projects,
            "ip_records": ip_records,
        },
    )
