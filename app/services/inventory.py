from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.models import CustomField, Folder, IPAddress, Project
from app.services.custom_fields import lock_custom_field_schema, make_custom_field_key, next_available_key
from app.services.network import address_sort_key, iter_project_addresses, normalize_cidr, reserved_project_addresses

if TYPE_CHECKING:
    from app.services.csv_io import ImportedAsset


class ProjectCreationError(ValueError):
    pass


def create_project_with_addresses(
    db: Session,
    *,
    folder_id: int,
    name: str,
    cidr: str,
    description: str,
    max_addresses: int,
    imported_rows: Iterable[ImportedAsset] = (),
    commit: bool = True,
) -> Project:
    normalized_cidr = normalize_cidr(cidr, max_addresses=max_addresses)
    clean_name = name.strip()

    # Serialize creation within a folder, including when legacy duplicates already exist.
    if db.get_bind().dialect.name == "sqlite":
        locked_folder = db.scalar(
            update(Folder).where(Folder.id == folder_id).values(updated_at=Folder.updated_at).returning(Folder.id)
        )
    else:
        locked_folder = db.scalar(select(Folder.id).where(Folder.id == folder_id).with_for_update())
    if locked_folder is None:
        raise ProjectCreationError("Папка не найдена")

    existing_cidr = db.scalar(
        select(Project.cidr)
        .where(
            Project.folder_id == folder_id,
            or_(Project.cidr == normalized_cidr, func.lower(Project.name) == clean_name.lower()),
        )
        .order_by((Project.cidr == normalized_cidr).desc())
        .limit(1)
    )
    if existing_cidr == normalized_cidr:
        raise ProjectCreationError(f"Подсеть {normalized_cidr} уже существует в выбранной папке")
    if existing_cidr is not None:
        raise ProjectCreationError("Проект с таким именем уже существует в выбранной папке")

    project = Project(folder_id=folder_id, name=clean_name, cidr=normalized_cidr, description=description.strip())
    db.add(project)
    db.flush()

    assets_by_address = {row.address: row for row in imported_rows}
    for ordinal, address in iter_project_addresses(normalized_cidr):
        record = IPAddress(project_id=project.id, ordinal=ordinal, address=address)
        asset = assets_by_address.get(address)
        if asset is not None:
            record.hostname = asset.hostname
            record.os = asset.os
            record.asset_type = asset.asset_type
            record.comment = asset.comment
        db.add(record)
    if commit:
        db.commit()
        db.refresh(project)
    return project


def is_ip_record_empty(ip_record: IPAddress) -> bool:
    base_values = [ip_record.hostname, ip_record.os, ip_record.asset_type, ip_record.comment]
    custom_values = ip_record.custom_values or {}
    return (
        not any(str(value or "").strip() for value in base_values)
        and not any(str(value).strip() for value in custom_values.values())
    )


def normalize_project_address_rows(db: Session) -> None:
    projects = db.scalars(select(Project).options(selectinload(Project.ip_addresses))).all()
    changed = False

    for project in projects:
        reserved = reserved_project_addresses(project.cidr)
        for ip_record in list(project.ip_addresses):
            if ip_record.address in reserved:
                db.delete(ip_record)
                changed = True

        if changed:
            db.flush()

        usable_records = [
            ip_record
            for ip_record in project.ip_addresses
            if ip_record.address not in reserved and ip_record not in db.deleted
        ]
        ordered_records = sorted(usable_records, key=lambda item: address_sort_key(item.address))

        for index, ip_record in enumerate(ordered_records, start=1):
            if ip_record.ordinal != index:
                ip_record.ordinal = -index
                changed = True

        if changed:
            db.flush()

        for index, ip_record in enumerate(ordered_records, start=1):
            if ip_record.ordinal != index:
                ip_record.ordinal = index
                changed = True

    if changed:
        db.commit()


def create_custom_field(db: Session, *, project_id: int, name: str, field_type: str) -> CustomField:
    lock_custom_field_schema(db, project_id, exclusive=True)
    clean_name = name.strip()
    existing_keys = set(
        db.scalars(select(CustomField.key).where(CustomField.project_id == project_id)).all()
    )
    key = next_available_key(make_custom_field_key(clean_name), existing_keys)
    max_position = db.scalars(
        select(CustomField.position).where(CustomField.project_id == project_id).order_by(CustomField.position.desc())
    ).first()

    field = CustomField(
        project_id=project_id,
        name=clean_name,
        key=key,
        field_type=field_type,
        position=(max_position or 0) + 1,
    )
    db.add(field)
    db.commit()
    db.refresh(field)
    return field
