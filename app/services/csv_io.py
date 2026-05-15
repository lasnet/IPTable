import csv
import io
import re
import zipfile
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_address

from app.models import IPAddress, Project
from app.services.network import reserved_project_addresses

CSV_COLUMNS = ["ip", "hostname", "os", "type", "comment"]
FIELD_LIMITS = {
    "hostname": 255,
    "os": 120,
    "type": 120,
    "comment": 4000,
}


class CSVImportError(ValueError):
    pass


@dataclass(frozen=True)
class ImportedAsset:
    address: str
    hostname: str
    os: str
    asset_type: str
    comment: str


@dataclass(frozen=True)
class CSVImportResult:
    cidr: str
    rows: list[ImportedAsset]


def _decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CSVImportError("Не удалось прочитать файл: используйте UTF-8 или Windows-1251")


def _smallest_network(addresses: list[IPv4Address]) -> IPv4Network:
    first = min(int(address) for address in addresses)
    last = max(int(address) for address in addresses)
    prefix = 32 - (first ^ last).bit_length()
    mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
    return IPv4Network((first & mask, prefix))


def parse_assets_csv(content: bytes, *, max_addresses: int) -> CSVImportResult:
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";")
    if reader.fieldnames is None:
        raise CSVImportError("CSV-файл пустой или не содержит заголовок")

    headers = [header.strip().lower() for header in reader.fieldnames]
    if headers != CSV_COLUMNS:
        raise CSVImportError("Неверный заголовок CSV. Ожидается: ip;hostname;os;type;comment")

    rows: list[ImportedAsset] = []
    parsed_addresses: list[IPv4Address] = []
    seen_addresses: dict[str, int] = {}

    for row_number, raw_row in enumerate(reader, start=2):
        normalized = {key: (raw_row.get(key) or "").strip() for key in CSV_COLUMNS}
        if not any(normalized.values()):
            continue

        raw_ip = normalized["ip"]
        if not raw_ip:
            raise CSVImportError(f"Строка {row_number}: поле ip обязательно")

        try:
            parsed_ip = ip_address(raw_ip)
        except ValueError as exc:
            raise CSVImportError(f"Строка {row_number}: неверный IPv4-адрес") from exc

        if not isinstance(parsed_ip, IPv4Address):
            raise CSVImportError(f"Строка {row_number}: поддерживаются только IPv4-адреса")

        address = str(parsed_ip)
        if address in seen_addresses:
            raise CSVImportError(
                f"Строка {row_number}: IP {address} уже указан в строке {seen_addresses[address]}"
            )
        seen_addresses[address] = row_number

        for field_name, limit in FIELD_LIMITS.items():
            if len(normalized[field_name]) > limit:
                raise CSVImportError(f"Строка {row_number}: поле {field_name} длиннее {limit} символов")

        rows.append(
            ImportedAsset(
                address=address,
                hostname=normalized["hostname"],
                os=normalized["os"],
                asset_type=normalized["type"],
                comment=normalized["comment"],
            )
        )
        parsed_addresses.append(parsed_ip)

        if len(rows) > max_addresses:
            raise CSVImportError(f"CSV содержит больше строк, чем разрешенный лимит проекта: {max_addresses}")

    if not rows:
        raise CSVImportError("CSV не содержит строк с IP-адресами")

    network = _smallest_network(parsed_addresses)
    if network.num_addresses > max_addresses:
        raise CSVImportError(
            f"По импортированным IP получилась подсеть {network} на {network.num_addresses} адресов, "
            f"лимит: {max_addresses}"
        )

    reserved = reserved_project_addresses(str(network))
    reserved_hits = [row.address for row in rows if row.address in reserved]
    if reserved_hits:
        sample = ", ".join(reserved_hits[:5])
        raise CSVImportError(f"CSV содержит network/broadcast адреса для подсети {network}: {sample}")

    rows.sort(key=lambda item: int(ip_address(item.address)))
    return CSVImportResult(cidr=str(network), rows=rows)


def render_project_csv(project: Project, ip_records: list[IPAddress]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for ip_record in ip_records:
        writer.writerow(
            [
                ip_record.address,
                ip_record.hostname,
                ip_record.os,
                ip_record.asset_type,
                ip_record.comment,
            ]
        )
    return output.getvalue()


def csv_bytes(content: str) -> bytes:
    return ("\ufeff" + content).encode("utf-8")


def safe_export_name(value: str, *, suffix: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._")
    return f"{clean or 'export'}{suffix}"


def build_zip_archive(files: dict[str, bytes], *, password: str | None = None) -> bytes:
    buffer = io.BytesIO()
    if password:
        try:
            import pyzipper
        except ImportError as exc:
            raise RuntimeError("Для экспорта ZIP с паролем установите зависимость pyzipper") from exc

        with pyzipper.AESZipFile(
            buffer,
            "w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as archive:
            archive.setpassword(password.encode("utf-8"))
            for filename, content in files.items():
                archive.writestr(filename, content)
    else:
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for filename, content in files.items():
                archive.writestr(filename, content)

    return buffer.getvalue()
