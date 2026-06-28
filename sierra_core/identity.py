from __future__ import annotations
import re
from sierra_core.errors import IdentityLockError

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def titles_match(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    return bool(na) and na == nb

def assert_identity(*, supplied_title: str, stored_title: str, entity_id) -> None:
    """Row-action identity lock: refuse unless supplied title matches stored title.
    Empty/blank titles never match (they are not safe identifiers)."""
    if not titles_match(supplied_title, stored_title):
        raise IdentityLockError(
            f"identity-lock: id={entity_id} supplied title {supplied_title!r} "
            f"!= stored title {stored_title!r}; refusing row action")
