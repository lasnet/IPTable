import re


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
