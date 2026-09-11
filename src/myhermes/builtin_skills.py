"""Install public authoring instructions from wheel data at managed boundaries.

The caller holds both the skills state lock and the managed-session lock. Builtins
have reserved names, no account profiles, and never enter a personal/company sync.
"""

import hashlib
from importlib.resources import files
import os
from pathlib import Path
import re
import stat
import uuid

from . import __version__
from .errors import CompanionError
from .files import atomic_bytes, private_dir, safe_path

NAMES = ("myhermes-skills", "myhermes-connections")
MAX_BYTES = 32768


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _content(name, text):
    if name not in NAMES or not isinstance(text, str):
        raise CompanionError("builtin_state_invalid", "Invalid bundled skill journal.")
    raw = text.encode("utf-8")
    if len(raw) > MAX_BYTES or "\x00" in text or not text.startswith("---\nname: " + name + "\n"):
        raise CompanionError("builtin_state_invalid", "Invalid bundled skill content.")
    return raw


def bundled_skills():
    return {
        name: _content(name, files("myhermes").joinpath("data", "hermes-skills", name, "SKILL.md").read_text("utf-8"))
        for name in NAMES
    }


def _directory(target):
    safe_path(target)
    if target.exists() and (not target.is_dir() or target.stat().st_uid != os.getuid()):
        raise CompanionError("builtin_skill_modified", "A reserved builtin skill path is not an owned directory.", 6)


def _current(target):
    _directory(target)
    if not target.exists() or not list(target.iterdir()):
        return None
    if {entry.name for entry in target.iterdir()} != {"SKILL.md"}:
        raise CompanionError("builtin_skill_modified", "A reserved builtin directory contains owner modifications.", 6)
    path = safe_path(target / "SKILL.md")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise CompanionError("builtin_skill_modified", "A reserved builtin file is not an owned ordinary file.", 6)
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise CompanionError("builtin_skill_modified", "A reserved builtin file was modified.", 6)
    return _digest(raw)


def _fsync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _recover(state, home):
    journal = state.get("builtin_journal")
    if journal is None:
        return
    expected_keys = {"name", "previous_sha256", "target_sha256", "content", "version", "backup_id"}
    if not isinstance(journal, dict) or set(journal) != expected_keys:
        raise CompanionError("builtin_state_invalid", "Invalid bundled skill recovery metadata.")
    name = journal["name"]
    raw = _content(name, journal["content"])
    if _digest(raw) != journal["target_sha256"] or not re.fullmatch(r"\d+\.\d+\.\d+", journal["version"]):
        raise CompanionError("builtin_state_invalid", "Bundled skill recovery integrity check failed.")
    previous = journal["previous_sha256"]
    if previous is not None and (not isinstance(previous, str) or not re.fullmatch(r"[a-f0-9]{64}", previous)):
        raise CompanionError("builtin_state_invalid", "Invalid prior bundled skill hash.")
    target = safe_path(Path(home) / "skills" / name)
    _directory(target)
    try:
        current = _current(target)
    except CompanionError:
        if not journal["backup_id"]:
            raise
        current = "modified"
    if current != journal["target_sha256"]:
        if journal["backup_id"] is not None:
            if str(uuid.UUID(journal["backup_id"])) != journal["backup_id"]:
                raise CompanionError("builtin_state_invalid", "Invalid bundled skill backup identifier.")
            parent = private_dir(Path(home) / ".myhermes-builtin-backups" / journal["backup_id"])
            backup = safe_path(parent / name)
            if target.exists():
                if backup.exists():
                    raise CompanionError(
                        "builtin_recovery_conflict",
                        "Both builtin trees changed; rerun bootstrap --restore to preserve them.",
                        6,
                    )
                os.rename(target, backup)
                _fsync(target.parent)
                _fsync(parent)
        elif current != previous:
            raise CompanionError(
                "builtin_skill_modified",
                "Builtin instructions changed; bootstrap --restore preserves them before restoring the package version.",
                6,
            )
        atomic_bytes(target / "SKILL.md", raw)
    state.transaction(
        values=[
            ("builtin:" + name, {"sha256": journal["target_sha256"], "version": journal["version"]}),
            ("builtin_journal", None),
        ]
    )


def activate_builtin_skills(state, home, *, restore=False):
    """Reserved installed assets only; explicit restore retains whole modified trees."""
    pending = state.get("builtin_journal")
    if pending is not None and restore:
        pending["backup_id"] = str(uuid.uuid4())
        state.put("builtin_journal", pending)
    _recover(state, home)
    preserved = []
    for name, raw in bundled_skills().items():
        target = safe_path(Path(home) / "skills" / name)
        _directory(target)
        installed = state.get("builtin:" + name)
        if installed is not None and (
            not isinstance(installed, dict)
            or set(installed) != {"sha256", "version"}
            or not isinstance(installed["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", installed["sha256"])
        ):
            raise CompanionError("builtin_state_invalid", "Invalid installed builtin metadata.")
        try:
            current = _current(target)
        except CompanionError:
            if not restore:
                raise
            current = "modified"
        desired = _digest(raw)
        if installed is not None and current == desired:
            continue
        collision = installed is None and target.exists()
        changed = installed is not None and current != installed["sha256"]
        if (collision or changed) and not restore:
            raise CompanionError(
                "builtin_skill_modified",
                "Reserved builtin instructions contain local changes. Use bootstrap --restore to preserve and restore them.",
                6,
            )
        backup_id = str(uuid.uuid4()) if restore and target.exists() else None
        if backup_id:
            preserved.append(backup_id)
        state.put(
            "builtin_journal",
            {
                "name": name,
                "previous_sha256": current if current != "modified" else None,
                "target_sha256": desired,
                "content": raw.decode("utf-8"),
                "version": __version__,
                "backup_id": backup_id,
            },
        )
        _recover(state, home)
    result = {"installed": list(NAMES), "version": __version__}
    if preserved:
        result["preserved_ids"] = preserved
    return result
