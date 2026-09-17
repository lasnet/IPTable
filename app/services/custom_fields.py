import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CustomField, IPAddress


CUSTOM_FIELD_TYPES = {"text", "number", "date"}
MAX_CUSTOM_FIELDS = 64


@dataclass(frozen=True, slots=True)
class CustomFieldSpec:
    field_id: int | None
    name: str
    field_type: str


class CustomFieldConfigurationError(ValueError):
    pass


class IncompatibleCustomFieldValueError(CustomFieldConfigurationError):
    def __init__(self, *, field_name: str, address: str, field_type: str) -> None:
        super().__init__(field_name, address, field_type)
        self.field_name = field_name
        self.address = address
        self.field_type = field_type


def make_custom_field_key(name: str) -> str:
    key = re.sub(r"\W+", "_", name.strip().lower(), flags=re.UNICODE).strip("_")
    return key or "field"


def next_available_key(base_key: str, existing_keys: set[str]) -> str:
    if base_key not in existing_keys:
        return base_key

    suffix = 2
    while f"{base_key}_{suffix}" in existing_keys:
        suffix += 1
    return f"{base_key}_{suffix}"


def _value_matches_type(value: object, field_type: str) -> bool:
    text = str(value or "").strip()
    if not text or field_type == "text":
        return True
    if field_type == "number":
        try:
            return Decimal(text).is_finite()
        except InvalidOperation:
            return False
    if field_type == "date":
        try:
            date.fromisoformat(text)
        except ValueError:
            return False
        return True
    return False


def replace_custom_fields(db: Session, *, project_id: int, specs: list[CustomFieldSpec]) -> list[CustomField]:
    if len(specs) > MAX_CUSTOM_FIELDS:
        raise CustomFieldConfigurationError("too_many_fields")

    existing = db.scalars(
        select(CustomField).where(CustomField.project_id == project_id).order_by(CustomField.position.asc())
    ).all()
    existing_by_id = {field.id: field for field in existing}
    submitted_ids = [spec.field_id for spec in specs if spec.field_id is not None]
    if len(submitted_ids) != len(set(submitted_ids)) or any(field_id not in existing_by_id for field_id in submitted_ids):
        raise CustomFieldConfigurationError("invalid_field_id")

    for spec in specs:
        if not spec.name.strip() or len(spec.name.strip()) > 120 or spec.field_type not in CUSTOM_FIELD_TYPES:
            raise CustomFieldConfigurationError("invalid_field")

    submitted_id_set = set(submitted_ids)
    deleted_fields = [field for field in existing if field.id not in submitted_id_set]
    changed_type_fields = [
        (existing_by_id[spec.field_id], spec)
        for spec in specs
        if spec.field_id is not None and existing_by_id[spec.field_id].field_type != spec.field_type
    ]
    ip_records: list[IPAddress] = []
    if deleted_fields or changed_type_fields:
        ip_records = db.scalars(select(IPAddress).where(IPAddress.project_id == project_id)).all()

    for field, spec in changed_type_fields:
        for ip_record in ip_records:
            value = (ip_record.custom_values or {}).get(field.key, "")
            if not _value_matches_type(value, spec.field_type):
                raise IncompatibleCustomFieldValueError(
                    field_name=field.name,
                    address=ip_record.address,
                    field_type=spec.field_type,
                )

    deleted_keys = {field.key for field in deleted_fields}
    if deleted_keys:
        for ip_record in ip_records:
            custom_values = dict(ip_record.custom_values or {})
            updated_values = {key: value for key, value in custom_values.items() if key not in deleted_keys}
            if updated_values != custom_values:
                ip_record.custom_values = updated_values
        for field in deleted_fields:
            db.delete(field)

    reserved_keys = {field.key for field in existing}
    saved_fields: list[CustomField] = []
    for position, spec in enumerate(specs, start=1):
        clean_name = spec.name.strip()
        if spec.field_id is None:
            key = next_available_key(make_custom_field_key(clean_name), reserved_keys)
            reserved_keys.add(key)
            field = CustomField(project_id=project_id, key=key)
            db.add(field)
        else:
            field = existing_by_id[spec.field_id]
        field.name = clean_name
        field.field_type = spec.field_type
        field.position = position
        saved_fields.append(field)

    db.commit()
    for field in saved_fields:
        db.refresh(field)
    return saved_fields
