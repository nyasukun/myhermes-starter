"""Bounded local configuration: metadata schemas never accept arbitrary content."""

import json
import math
import os
from pathlib import Path
import stat
import urllib.parse
import uuid

from .api import validate_server
from .errors import CompanionError

MAX_CONFIG_BYTES = 65_536


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _read(path):
    # Open every ancestor by descriptor so a replaced parent symlink cannot
    # redirect this read between a path preflight and the actual file open.
    path = Path(os.path.abspath(path))
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:-1]:
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = following
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise ValueError()
            if info.st_size > MAX_CONFIG_BYTES:
                raise ValueError()
            raw = stream.read(MAX_CONFIG_BYTES + 1)
            if len(raw) > MAX_CONFIG_BYTES:
                raise ValueError()
    finally:
        os.close(directory)
    value = json.loads(raw, object_pairs_hook=_object)
    if not isinstance(value, dict):
        raise ValueError()
    return value


def _text(value, maximum):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError()
    value.encode("utf-8")
    return value


def canonical_identifier(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError()
    canonical = str(uuid.UUID(value))
    if canonical != value.lower():
        raise ValueError()
    return canonical


def _identifier(value):
    # Local key IDs can index a case-sensitive native credential store. Validate
    # legacy UUID casing without silently renaming that secure-store account.
    canonical_identifier(value)
    return value


def _path(value):
    _text(value, 4096)
    if not Path(value).is_absolute() or os.path.abspath(value) != value:
        raise ValueError()


def enrollment_metadata(value, server):
    if not isinstance(value, dict) or set(value) != {"enrollment_id", "verification_uri", "expires_at"}:
        raise ValueError()
    _identifier(value["enrollment_id"])
    _text(value["verification_uri"], 2048)
    origin = urllib.parse.urlsplit(server)
    location = urllib.parse.urlsplit(value["verification_uri"])
    if (
        (location.scheme, location.netloc) != (origin.scheme, origin.netloc)
        or location.path != "/"
        or location.fragment
        or urllib.parse.parse_qs(location.query) != {"enrollment_id": [value["enrollment_id"]]}
    ):
        raise ValueError()
    expiry = value["expires_at"]
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or not 0 <= expiry <= 253_402_300_799:
        raise ValueError()
    return dict(value)


def config_read(directory):
    try:
        data = _read(directory / "config.json")
        required = {"server", "hermes_home", "upstream", "key_id", "os"}
        optional = {
            "schema_version",
            "label",
            "agent_instance_id",
            "allow_local_http",
            "installation_id",
            "person_id",
            "enrollment",
        }
        if not required <= set(data) or set(data) - required - optional:
            raise ValueError()
        # Early local installations omitted these non-secret fields. Normalize
        # explicit documented defaults in memory; do not rewrite configuration.
        data.setdefault("schema_version", "1")
        data.setdefault("label", "My Hermes environment")
        data.setdefault("allow_local_http", False)
        if data["schema_version"] != "1" or type(data["allow_local_http"]) is not bool:
            raise ValueError()
        if data["os"] not in ("macos", "ubuntu"):
            raise ValueError()
        _text(data["label"], 80)
        if not data["label"].strip():
            raise ValueError()
        _text(data["server"], 2048)
        if validate_server(data["server"], data["allow_local_http"]) != data["server"]:
            raise ValueError()
        _path(data["hermes_home"])
        _path(data["upstream"])
        _identifier(data["key_id"])
        for field in ("agent_instance_id", "installation_id", "person_id"):
            if field in data:
                _identifier(data[field])
        if "enrollment" in data:
            if "installation_id" in data:
                raise ValueError()
            enrollment_metadata(data["enrollment"], data["server"])
        return data
    except (OSError, ValueError, KeyError, TypeError, CompanionError):
        raise CompanionError(
            "setup_required",
            "Local configuration is missing or invalid; restore it or set up an independent environment.",
            3,
        ) from None


def marker_read(path):
    try:
        value = _read(path)
        if set(value) != {"key_id", "state_directory"}:
            raise ValueError()
        _identifier(value["key_id"])
        _path(value["state_directory"])
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise CompanionError(
            "home_binding_mismatch", "The local home binding is missing or invalid; its files were preserved.", 3
        ) from None


def bound_config_read(directory):
    """All live CLI/API/monitoring paths require this state to own its home."""
    directory = Path(os.path.abspath(directory))
    config = config_read(directory)
    marker = marker_read(Path(config["hermes_home"]) / ".myhermes-installation.json")
    if marker != {"key_id": config["key_id"], "state_directory": str(directory)}:
        raise CompanionError(
            "home_binding_mismatch",
            "This state does not own the selected Hermes home; resume recovery from its original state.",
            3,
        )
    return config
