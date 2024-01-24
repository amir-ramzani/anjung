"""ULID identifiers (LLD section 1)."""

import re

from ulid import ULID

ID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"
_ID_RE = re.compile(ID_PATTERN)


def new_id() -> str:
    return str(ULID())


def is_id(value: object) -> bool:
    return isinstance(value, str) and _ID_RE.match(value) is not None
