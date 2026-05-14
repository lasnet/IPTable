from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CustomField, IPAddress, Project
from app.services.custom_fields import make_custom_field_key, next_available_key
from app.services.network import iter_project_addresses, normalize_cidr


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
    custom_values = ip_record.custom_values or {}
    return not any(value.strip() for value in base_values) and not any(
        str(value).strip() for value in custom_values.values()
    )


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
