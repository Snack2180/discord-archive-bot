import unicodedata


def canonicalize(tag: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', tag).split()).casefold()


def resolve_alias(tag: str, aliases: dict[str, str]) -> str:
    key = canonicalize(tag)
    return {canonicalize(k): v for k, v in aliases.items()}.get(key, tag.strip())
