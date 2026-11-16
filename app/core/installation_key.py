import os
from pathlib import Path
import secrets
from tempfile import NamedTemporaryFile


def validate_key(key: str) -> str:
    if len(key.strip()) < 32 or key.strip().startswith("replace-with-"):
        raise ValueError("SECRET_KEY must be at least 32 characters and must be changed from its placeholder")
    return key


def read_key(path: Path) -> str:
    return validate_key(path.read_text(encoding="utf-8").removesuffix("\n"))


def initialize_key(path: Path, previous_key: str = "") -> None:
    previous_key = validate_key(previous_key) if previous_key else ""
    if not path.exists():
        # A hard link publishes the completed 0600 file without replacing another writer's key.
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent) as key_file:
            key_file.write((previous_key or secrets.token_hex(32)) + "\n")
            key_file.flush()
            os.fsync(key_file.fileno())
            try:
                os.link(key_file.name, path)
            except FileExistsError:
                pass
    installed_key = read_key(path)
    if previous_key and not secrets.compare_digest(previous_key.encode(), installed_key.encode()):
        raise ValueError("The supplied SECRET_KEY differs from the saved installation key")
