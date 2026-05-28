import secrets
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models import CustomField, Folder, IPAddress, Project
from app.services.assets import normalize_tags, tags_to_text
from app.services.history import FieldChange, build_field_change, record_ip_address_history
from app.services.ping import enqueue_project_ping

api_router = APIRouter(prefix="/api/v1", tags=["integrations"])


class ProjectSummary(BaseModel):
    id: int
    folder_id: int
    name: str
    cidr: str
    description: str


class FolderSummary(BaseModel):
    id: int
    name: str
    projects: list[ProjectSummary]


class AddressOut(BaseModel):
    id: int
    ordinal: int
    address: str
    hostname: str
    os: str
    type: str
    comment: str
    tags: list[str]
    custom_values: dict[str, Any]
    ping_status: str
    last_checked_at: datetime | None


class AddressPage(BaseModel):
    project: ProjectSummary
    page: int
    per_page: int
    total: int
    items: list[AddressOut]


class AddressUpdate(BaseModel):
    hostname: str | None = Field(default=None, max_length=255)
    os: str | None = Field(default=None, max_length=120)
    type: str | None = Field(default=None, max_length=120)
    comment: str | None = Field(default=None, max_length=4000)
    tags: list[str] | str | None = None
    custom_values: dict[str, str] | None = None


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def require_api_token(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected_token = settings.integration_api_token.strip()
    if not expected_token:
        raise HTTPException(status_code=404, detail="API is disabled")

    supplied_token = (x_api_key or "").strip() or _extract_bearer_token(authorization)
    if not supplied_token or not secrets.compare_digest(supplied_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _project_summary(project: Project) -> ProjectSummary:
    return ProjectSummary(
        id=project.id,
        folder_id=project.folder_id,
        name=project.name,
        cidr=project.cidr,
        description=project.description,
    )


def _ping_status(ip_record: IPAddress) -> str:
    if ip_record.is_reachable is True:
        return "OK"
    if ip_record.is_reachable is False:
        return "NO"
    return "NoTest"


def _address_out(ip_record: IPAddress) -> AddressOut:
    return AddressOut(
        id=ip_record.id,
        ordinal=ip_record.ordinal,
        address=ip_record.address,
        hostname=ip_record.hostname,
        os=ip_record.os,
        type=ip_record.asset_type,
        comment=ip_record.comment,
        tags=normalize_tags(ip_record.tags),
        custom_values=ip_record.custom_values or {},
        ping_status=_ping_status(ip_record),
        last_checked_at=ip_record.last_checked_at,
    )


def _filled_value(column):
    return func.length(func.trim(func.coalesce(column, ""))) > 0


def _filled_condition(custom_fields: list[CustomField]):
    conditions = [
        _filled_value(IPAddress.hostname),
        _filled_value(IPAddress.os),
        _filled_value(IPAddress.asset_type),
        _filled_value(IPAddress.comment),
        func.length(func.coalesce(cast(IPAddress.tags, String), "")) > 2,
    ]
    for field in custom_fields:
        conditions.append(_filled_value(IPAddress.custom_values[field.key].as_string()))
    return or_(*conditions)


@api_router.get("/folders", response_model=list[FolderSummary], dependencies=[Depends(require_api_token)])
def list_folders(db: Annotated[Session, Depends(get_db)]) -> list[FolderSummary]:
    folders = db.scalars(
        select(Folder)
        .options(selectinload(Folder.projects))
        .order_by(Folder.name.asc())
    ).all()
    return [
        FolderSummary(
            id=folder.id,
            name=folder.name,
            projects=[_project_summary(project) for project in sorted(folder.projects, key=lambda item: item.name)],
        )
        for folder in folders
    ]


@api_router.get("/projects", response_model=list[ProjectSummary], dependencies=[Depends(require_api_token)])
def list_projects(
    db: Annotated[Session, Depends(get_db)],
    folder_id: Annotated[int | None, Query()] = None,
) -> list[ProjectSummary]:
    query = select(Project).order_by(Project.name.asc())
    if folder_id is not None:
        query = query.where(Project.folder_id == folder_id)
    return [_project_summary(project) for project in db.scalars(query).all()]


@api_router.get(
    "/projects/{project_id}/addresses",
    response_model=AddressPage,
    dependencies=[Depends(require_api_token)],
)
def list_project_addresses(
    project_id: int,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=500)] = 100,
    hide_empty: bool = False,
) -> AddressPage:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    custom_fields = db.scalars(
        select(CustomField).where(CustomField.project_id == project.id).order_by(CustomField.position.asc())
    ).all()
    query = select(IPAddress).where(IPAddress.project_id == project.id).order_by(IPAddress.ordinal.asc())
    count_query = select(func.count(IPAddress.id)).where(IPAddress.project_id == project.id)
    if hide_empty:
        condition = _filled_condition(list(custom_fields))
        query = query.where(condition)
        count_query = count_query.where(condition)

    total = db.scalar(count_query) or 0
    items = db.scalars(query.offset((page - 1) * per_page).limit(per_page)).all()
    return AddressPage(
        project=_project_summary(project),
        page=page,
        per_page=per_page,
        total=total,
        items=[_address_out(item) for item in items],
    )


@api_router.patch(
    "/projects/{project_id}/addresses/{ip_id}",
    response_model=AddressOut,
    dependencies=[Depends(require_api_token)],
)
def update_project_address(
    project_id: int,
    ip_id: int,
    payload: AddressUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> AddressOut:
    ip_record = db.scalar(select(IPAddress).where(IPAddress.project_id == project_id, IPAddress.id == ip_id))
    if ip_record is None:
        raise HTTPException(status_code=404, detail="IP address not found")

    changes: list[FieldChange] = []
    updates = {
        "hostname": payload.hostname,
        "os": payload.os,
        "asset_type": payload.type,
        "comment": payload.comment,
    }
    labels = {
        "hostname": "Hostname",
        "os": "OS",
        "asset_type": "Type",
        "comment": "Comment",
    }
    for field_name, value in updates.items():
        if value is None:
            continue
        clean_value = value.strip()
        change = build_field_change(field_name, labels[field_name], getattr(ip_record, field_name), clean_value)
        if change is not None:
            changes.append(change)
        setattr(ip_record, field_name, clean_value)

    if payload.tags is not None:
        new_tags = normalize_tags(payload.tags)
        change = build_field_change("tags", "Tags", tags_to_text(ip_record.tags), tags_to_text(new_tags))
        if change is not None:
            changes.append(change)
        ip_record.tags = new_tags

    if payload.custom_values is not None:
        custom_fields = db.scalars(
            select(CustomField).where(CustomField.project_id == project_id).order_by(CustomField.position.asc())
        ).all()
        allowed_keys = {field.key: field.name for field in custom_fields}
        custom_values = dict(ip_record.custom_values or {})
        for key, value in payload.custom_values.items():
            if key not in allowed_keys:
                raise HTTPException(status_code=400, detail=f"Unknown custom field: {key}")
            clean_value = str(value).strip()[:1000]
            change = build_field_change(f"custom.{key}", allowed_keys[key], custom_values.get(key, ""), clean_value)
            if change is not None:
                changes.append(change)
            custom_values[key] = clean_value
        ip_record.custom_values = custom_values

    record_ip_address_history(db, ip_record=ip_record, user=None, changes=changes, username="api")
    db.commit()
    db.refresh(ip_record)
    return _address_out(ip_record)


@api_router.post(
    "/projects/{project_id}/ping",
    dependencies=[Depends(require_api_token)],
)
def enqueue_ping(project_id: int, db: Annotated[Session, Depends(get_db)]) -> dict[str, bool | int]:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    job = enqueue_project_ping(db, project_id, reason="api")
    return {"queued": job is not None, "project_id": project_id}
