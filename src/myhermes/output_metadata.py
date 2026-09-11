"""Validate values as well as projection keys before emitting history metadata."""

from datetime import datetime
import re
import uuid

from .errors import CompanionError
from .files import LIMITS
from .skill_packages import skill_id, skill_version

_FIELDS = {
    "persona_history": ("revision", "update_id", "installation_id", "created_at", "paths"),
    "persona_conflicts": ("update_id", "base_revision", "revision", "conflict_paths", "created_at", "resolved_by"),
    "skill_history": ("revision", "skill_id", "sha256", "version", "installation_id", "created_at", "update_id"),
    "skill_conflicts": (
        "update_id",
        "skill_id",
        "base_revision",
        "revision",
        "installation_id",
        "created_at",
        "resolved_by",
    ),
}


def _integer(value):
    if type(value) is not int or not 0 <= value <= 9_007_199_254_740_991:
        raise ValueError()


def _field(key, value):
    if value is None and key in ("installation_id", "resolved_by", "sha256", "version"):
        return
    if key in ("revision", "base_revision"):
        _integer(value)
    elif key in ("update_id", "installation_id", "resolved_by"):
        if not isinstance(value, str) or str(uuid.UUID(value)) != value.lower():
            raise ValueError()
    elif key == "created_at":
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z", value):
            raise ValueError()
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif key in ("paths", "conflict_paths"):
        if not isinstance(value, list) or not 1 <= len(value) <= len(LIMITS):
            raise ValueError()
        if any(not isinstance(path, str) or path not in LIMITS for path in value) or len(set(value)) != len(value):
            raise ValueError()
    elif key == "sha256":
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError()
    elif key == "skill_id":
        skill_id(value)
    elif key == "version":
        skill_version(value)


def metadata_page(value, kind):
    collection = "history" if kind.endswith("history") else "conflicts"
    try:
        if not isinstance(value, dict) or not isinstance(value.get(collection), list) or len(value[collection]) > 100:
            raise ValueError()
        cursor = value.get("next_cursor")
        if cursor is not None:
            _integer(cursor)
        rows = []
        for item in value[collection]:
            if not isinstance(item, dict):
                raise ValueError()
            row = {key: item[key] for key in _FIELDS[kind]}
            for key, entry in row.items():
                _field(key, entry)
            rows.append(row)
        return {collection: rows, "next_cursor": cursor}
    except (ValueError, TypeError, KeyError, AttributeError, CompanionError):
        raise CompanionError(
            "schema_rejected", "Invalid history or conflict metadata; no response content was output."
        ) from None
