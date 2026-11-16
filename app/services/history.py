import csv
import io
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.models import Folder, HistoryEntry, IPAddress, Project, User
from app.services.csv_io import safe_csv_cell
from app.services.i18n import translate


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

    project = ip_record.project
    folder = project.folder
    db.add_all(
        HistoryEntry(
            action="ip_updated",
            project_id=project.id,
            subnet_name=project.name,
            cidr=project.cidr,
            folder_id=folder.id,
            folder_name=folder.name,
            ip_address_id=ip_record.id,
            user_id=user.id if user is not None else None,
            username=username or (user.username if user is not None else "api"),
            address=ip_record.address,
            field_name=change.field_name,
            field_label=change.field_label,
            old_value=change.old_value,
            new_value=change.new_value,
        )
        for change in changes
    )
    return len(changes)


def record_inventory_history(
    db: Session,
    *,
    user: User,
    action: str,
    folder: Folder,
    project: Project | None = None,
    source_filename: str = "",
    changes: Iterable[FieldChange | None] | None = None,
) -> None:
    filename = source_filename.replace("\\", "/").rsplit("/", 1)[-1].replace("\x00", "")[:255]
    fields = (FieldChange("", "", "", ""),) if changes is None else changes
    db.add_all(
        HistoryEntry(
            action=action,
            folder_id=folder.id,
            folder_name=folder.name,
            project_id=project.id if project is not None else None,
            subnet_name=project.name if project is not None else "",
            cidr=project.cidr if project is not None else "",
            user_id=user.id,
            username=user.username,
            source_filename=filename,
            field_name=change.field_name,
            field_label=change.field_label,
            old_value=change.old_value,
            new_value=change.new_value,
        )
        for change in fields if change is not None
    )


def history_query(
    *,
    folder: Folder | None = None,
    project: Project | None = None,
    address: str = "",
    search: str = "",
    since: datetime | None = None,
) -> Select[tuple[HistoryEntry]]:
    query = select(HistoryEntry).order_by(HistoryEntry.created_at.desc(), HistoryEntry.id.desc())
    # SQLite can reuse IDs of deleted objects. Their history stays in the global view.
    if folder is not None:
        query = query.where(HistoryEntry.folder_id == folder.id, HistoryEntry.created_at >= folder.created_at)
    if project is not None:
        query = query.where(HistoryEntry.project_id == project.id, HistoryEntry.created_at >= project.created_at)
    if address:
        query = query.where(HistoryEntry.address == address)
    if search:
        query = query.where(or_(
            HistoryEntry.address.startswith(search, autoescape=True),
            HistoryEntry.cidr.startswith(search, autoescape=True),
            HistoryEntry.folder_name.icontains(search, autoescape=True),
            HistoryEntry.subnet_name.icontains(search, autoescape=True),
            HistoryEntry.username.icontains(search, autoescape=True),
            HistoryEntry.source_filename.icontains(search, autoescape=True),
        ))
    if since is not None:
        query = query.where(HistoryEntry.created_at >= since)
    return query


def render_history_csv(entries: Iterable[HistoryEntry], *, language: str) -> Iterator[bytes]:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(translate(language, key) for key in (
        "history.time_utc", "history.user", "history.event", "history.folder", "history.subnet", "project.cidr_label",
        "history.ip_address", "history.file", "history.field", "history.old_value", "history.new_value",
    ))
    yield ("\ufeff" + output.getvalue()).encode("utf-8")
    output.seek(0)
    output.truncate()
    for entry in entries:
        label = entry.field_label
        if entry.action != "ip_updated" and entry.field_name:
            label = translate(language, "history.field." + entry.field_name)
        writer.writerow(safe_csv_cell(value) for value in (
            entry.created_at.isoformat(timespec="seconds") + "Z",
            entry.username,
            translate(language, "history.action." + entry.action),
            entry.folder_name, entry.subnet_name, entry.cidr, entry.address,
            entry.source_filename, label, entry.old_value, entry.new_value,
        ))
        if output.tell() >= 65536:
            yield output.getvalue().encode("utf-8")
            output.seek(0)
            output.truncate()
    if output.tell():
        yield output.getvalue().encode("utf-8")
