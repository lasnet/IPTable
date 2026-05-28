from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import CustomField, IPAddress, Project
from app.services.custom_fields import make_custom_field_key, next_available_key
from app.services.network import address_sort_key, iter_project_addresses, normalize_cidr, reserved_project_addresses


def create_project_with_addresses(
    db: Session,
    *,
    folder_id: int,
    name: str,
    cidr: str,
    description: str,
    max_addresses: int,
) -> Project:
    normalized_cidr = normalize_cidr(cidr, max_addresses=max_addresses)
    project = Project(folder_id=folder_id, name=name.strip(), cidr=normalized_cidr, description=description.strip())
    db.add(project)
    db.flush()

    db.add_all(
        IPAddress(project_id=project.id, ordinal=ordinal, address=address)
        for ordinal, address in iter_project_addresses(normalized_cidr)
    )
    db.commit()
    db.refresh(project)
    return project


def is_ip_record_empty(ip_record: IPAddress) -> bool:
    base_values = [ip_record.hostname, ip_record.os, ip_record.asset_type, ip_record.comment]
    tags = ip_record.tags or []
    custom_values = ip_record.custom_values or {}
    return (
        not any(value.strip() for value in base_values)
        and not any(str(value).strip() for value in tags)
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
