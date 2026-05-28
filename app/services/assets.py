MAX_TAGS = 20
MAX_TAG_LENGTH = 60


def normalize_tags(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).split(",")

    tags: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        tag = str(raw_item).strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            tag = tag[:MAX_TAG_LENGTH]
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) >= MAX_TAGS:
            break
    return tags


def tags_to_text(value: object) -> str:
    return ", ".join(normalize_tags(value))
