"""Upgrade a previously installed public plugin without overwriting owner edits.

The caller holds the managed home lock. The metadata journal permits recovery
after interruption between individual file replacements; no secret is recorded.
"""

import hashlib
import os
import re
import stat

from .errors import CompanionError
from .files import atomic_bytes, atomic_json, private_dir, safe_path
from .local_config import _read


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _reserved():
    return CompanionError(
        "runtime_plugin_reserved",
        "The managed connections plugin contains owner changes. Preserve its directory outside runtime discovery before restoring it.",
        3,
    )


def install_connections_plugin(home, files):
    directory = safe_path(private_dir(home / "plugins") / "myhermes-connections")
    marker = safe_path(home / ".myhermes-connections-plugin.json")
    target = {name: _hash(raw) for name, raw in files.items()}
    observed = dict.fromkeys(files)
    try:
        if directory.exists():
            if not directory.is_dir() or set(p.name for p in directory.iterdir()) - set(files):
                raise _reserved()
            for name in files:
                path = safe_path(directory / name)
                if not path.exists():
                    continue
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                        raise _reserved()
                    raw = stream.read(131073)
                    if len(raw) > 131072:
                        raise _reserved()
                    observed[name] = _hash(raw)
        if marker.exists():
            record = _read(marker)
            if set(record) != {"files"} or not isinstance(record["files"], dict) or set(record["files"]) != set(files):
                raise _reserved()
            for name, choices in record["files"].items():
                if not isinstance(choices, list) or not 1 <= len(choices) <= 2:
                    raise _reserved()
                if any(
                    value is not None and (not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value))
                    for value in choices
                ):
                    raise _reserved()
                if directory.exists() and observed[name] not in choices:
                    raise _reserved()
        elif directory.exists() and observed != target:
            raise _reserved()
        if observed == target:
            atomic_json(marker, {"files": {name: [digest] for name, digest in target.items()}})
            return
        atomic_json(
            marker, {"files": {name: list(dict.fromkeys([observed[name], digest])) for name, digest in target.items()}}
        )
        private_dir(directory)
        for name, raw in files.items():
            atomic_bytes(directory / name, raw)
        atomic_json(marker, {"files": {name: [digest] for name, digest in target.items()}})
    except (OSError, ValueError, TypeError):
        raise _reserved() from None
