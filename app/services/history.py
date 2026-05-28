from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import IPAddress, IPAddressHistory, User


@dataclass(frozen=True)
class FieldChange:
    field_name: str
    field_label: str
    old_value: str
    new_value: str


def _normalize_history_value(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def build_field_change(field_name: str, field_label: str, old_value: object, new_value: object) -> FieldChange | None:
    old_text = _normalize_history_value(old_value)
    new_text = _normalize_history_value(new_value)
    if old_text == new_text:
        return None

    return FieldChange(
        field_name=field_name,
        field_label=field_label,
        old_value=old_text,
        new_value=new_text,
    )


def record_ip_address_history(
    db: Session,
    *,
    ip_record: IPAddress,
    user: User | None,
    changes: list[FieldChange],
    username: str | None = None,
) -> int:
    if not changes:
        return 0

    actor_name = username or (user.username if user is not None else "api")
    db.add_all(
        IPAddressHistory(
            project_id=ip_record.project_id,
            ip_address_id=ip_record.id,
            user_id=user.id if user is not None else None,
            username=actor_name,
            address=ip_record.address,
            field_name=change.field_name,
            field_label=change.field_label,
            old_value=change.old_value,
            new_value=change.new_value,
        )
        for change in changes
    )
    return len(changes)
