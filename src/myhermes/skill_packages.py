"""Portable, fully verified skill packages; no archive parser or script execution."""

from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata

from .errors import CompanionError

MAX_FILES = 100
MAX_FILE_BYTES = 524_288
MAX_TOTAL_BYTES = 2_097_152
_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z", re.I)
_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
_RESERVED = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)", re.I)
_FRONT = re.compile(r"\A---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _reject(code="skill_package_rejected"):
    return CompanionError(code, "Skill package or directory does not match the published safe package contract.")


def _exact(value, keys, optional=()):
    if not isinstance(value, dict) or set(value) - set(keys) - set(optional) or set(keys) - set(value):
        raise _reject()
    return value


def _string(value, maximum):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or "\0" in value:
        raise _reject()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _reject() from None
    return value


def skill_id(value):
    if not _ID.fullmatch(_string(value, 40)):
        raise _reject()
    return value


def skill_version(value):
    if not _VERSION.fullmatch(_string(value, 32)):
        raise _reject()
    return value


def skill_path(value):
    value = _string(value, 240)
    if not _PATH.fullmatch(value) or any(
        not part or part.startswith(".") or part.endswith(".") or _RESERVED.match(part) for part in value.split("/")
    ):
        raise _reject("skill_path_rejected")
    return value


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def package_digest(package):
    """Hash the canonical validated original envelope, never the projected tree."""
    return _digest(canonical_json(parse_package(package)).encode("utf-8"))


def _decode(value, extra=0):
    if not isinstance(value, str) or len(value) > ((MAX_FILE_BYTES + extra + 2) // 3) * 4:
        raise _reject("skill_encoding_rejected")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise _reject("skill_encoding_rejected") from None
    if base64.b64encode(raw).decode("ascii") != value:
        raise _reject("skill_encoding_rejected")
    return raw


def _document(raw, name):
    try:
        document = raw.decode("utf-8")
    except UnicodeError:
        raise _reject("skill_document_invalid") from None
    match = _FRONT.match(document)
    lines = re.split(r"\r?\n", match[1]) if match else []
    # YAML treats bare CR/NEL/LS/PS as new lines. They must not smuggle extra
    # fields through this stricter two-line header; ordinary Unicode body stays data.
    if (
        len(lines) != 2
        or any(re.search(r"[\r\n\u0085\u2028\u2029]", line) for line in lines)
        or lines[0] != "name: " + name
        or not lines[1].startswith("description: ")
        or "\0" in document
    ):
        raise _reject("skill_document_invalid")
    description = lines[1][13:]
    if description.startswith('"'):
        try:
            _string(json.loads(description), 500)
        except (ValueError, CompanionError):
            raise _reject("skill_document_invalid") from None
    elif (
        not description
        or not (description[0] == "_" or unicodedata.category(description[0]).startswith("L"))
        or description.strip().lower() in ("true", "false", "null", "yes", "no", "on", "off", "~")
        or ": " in description
        or re.search(r"[ \t]#", description)
    ):
        raise _reject("skill_document_invalid")
    else:
        _string(description, 500)
    return document


def _check_paths(paths):
    """Check file collisions and directory spelling, including case-sensitive hosts."""
    folded_files = {path.lower() for path in paths}
    if len(folded_files) != len(paths):
        raise _reject("skill_path_collision")
    spelling = {}
    for path in paths:
        parts = path.split("/")
        if parts[-1].lower() == "skill.md" and path != "SKILL.md":
            raise _reject("skill_document_invalid")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            folded = prefix.lower()
            if spelling.setdefault(folded, prefix) != prefix:
                raise _reject("skill_path_collision")
            if i < len(parts) and folded in folded_files:
                raise _reject("skill_path_collision")


def parse_package(value):
    """Validate every scalar/file before returning a detached JSON-compatible copy."""
    obj = _exact(
        value, ("schema_version", "skill_id", "version", "description", "files", "requires"), ("derived_from",)
    )
    if obj["schema_version"] != "1":
        raise _reject()
    name = skill_id(obj["skill_id"])
    skill_version(obj["version"])
    _string(obj["description"], 500)
    if not isinstance(obj["files"], list) or not 1 <= len(obj["files"]) <= MAX_FILES:
        raise _reject("skill_file_limit")
    total, document, paths = 0, None, []
    for entry in obj["files"]:
        file = _exact(entry, ("path", "content_base64", "sha256", "executable"))
        path = skill_path(file["path"])
        paths.append(path)
        if (
            type(file["executable"]) is not bool
            or not isinstance(file["sha256"], str)
            or not _HASH.fullmatch(file["sha256"])
        ):
            raise _reject("invalid_skill_file")
        raw = _decode(file["content_base64"])
        total += len(raw)
        if len(raw) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise _reject("skill_size_limit")
        if _digest(raw) != file["sha256"]:
            raise _reject("skill_hash_mismatch")
        if path == "SKILL.md":
            document = raw
    if paths != sorted(paths):
        raise _reject("skill_path_order")
    _check_paths(paths)
    if document is None:
        raise _reject("skill_document_invalid")
    _document(document, name)
    required = _exact(obj["requires"], ("connectors", "connections"))
    if (
        not isinstance(required["connectors"], list)
        or len(required["connectors"]) > 20
        or not isinstance(required["connections"], list)
        or len(required["connections"]) > 50
    ):
        raise _reject("skill_requirements_invalid")
    connectors = set()
    for item in required["connectors"]:
        item = _exact(item, ("connector_id", "version"))
        connector = skill_id(item["connector_id"])
        skill_version(item["version"])
        if connector in connectors:
            raise _reject("skill_requirements_invalid")
        connectors.add(connector)
    connections = set()
    for item in required["connections"]:
        if not isinstance(item, str) or not _UUID.fullmatch(item) or item in connections:
            raise _reject("skill_requirements_invalid")
        connections.add(item)
    if "derived_from" in obj:
        derived = _exact(obj["derived_from"], ("scope", "skill_id", "version", "sha256"))
        if (
            derived["scope"] not in ("company", "personal")
            or not isinstance(derived["sha256"], str)
            or not _HASH.fullmatch(derived["sha256"])
        ):
            raise _reject("skill_derivation_invalid")
        skill_id(derived["skill_id"])
        skill_version(derived["version"])
    return deepcopy(obj)


def projected_name(name, scope):
    if scope not in ("personal", "company"):
        raise _reject("skill_scope_rejected")
    return "mh-" + scope + "-" + skill_id(name)


@contextmanager
def _directory(path):
    """Pin every ancestor with no-follow dir descriptors, including the root itself."""
    absolute = Path(os.path.abspath(path))
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for part in absolute.parts[1:]:
            following = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        yield descriptor
    except OSError:
        raise _reject("skill_directory_rejected") from None
    finally:
        os.close(descriptor)


def _entry(path, raw, executable):
    return {
        "path": path,
        "content_base64": base64.b64encode(raw).decode("ascii"),
        "sha256": _digest(raw),
        "executable": executable,
    }


def _read_tree(directory, projection_extra=0):
    result, total, visited = [], 0, 0

    def walk(descriptor, prefix=""):
        nonlocal total, visited
        with os.scandir(descriptor) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            visited += 1
            if visited > 1000:
                raise _reject("skill_file_limit")
            path = skill_path(prefix + name)
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
                try:
                    current = os.fstat(child)
                    if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                        raise _reject("skill_directory_changed")
                    walk(child, path + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                file_limit = MAX_FILE_BYTES + (projection_extra if path == "SKILL.md" else 0)
                if len(result) >= MAX_FILES or info.st_size > file_limit:
                    raise _reject("skill_size_limit")
                file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
                with os.fdopen(file, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or before.st_nlink != 1
                        or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)
                    ):
                        raise _reject("skill_file_rejected")
                    raw = stream.read(file_limit + 1)
                    after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise _reject("skill_directory_changed")
                total += len(raw)
                if len(raw) > file_limit or total > MAX_TOTAL_BYTES + projection_extra:
                    raise _reject("skill_size_limit")
                result.append(_entry(path, raw, bool(before.st_mode & 0o111)))
            else:
                raise _reject("skill_file_rejected")

    with _directory(directory) as descriptor:
        walk(descriptor)
    return sorted(result, key=lambda entry: entry["path"])


def pack_directory(directory, skill_id, version, description, requires, derived_from=None, projected_scope=None):
    """Read regular files only; preserve all bytes except an explicitly projected name."""
    extra = len(projected_name(skill_id, projected_scope)) - len(skill_id) if projected_scope is not None else 0
    files = _read_tree(directory, extra)
    if projected_scope is not None:
        name = projected_name(skill_id, projected_scope)
        if Path(directory).name != name:
            raise _reject("skill_identity_mismatch")
        for index, file in enumerate(files):
            if file["path"] == "SKILL.md":
                document = _document(_decode(file["content_base64"], extra), name)
                document = document.replace("name: " + name, "name: " + skill_id, 1)
                files[index] = _entry("SKILL.md", document.encode("utf-8"), file["executable"])
    package = {
        "schema_version": "1",
        "skill_id": skill_id,
        "version": version,
        "description": description,
        "files": files,
        "requires": requires,
    }
    if derived_from is not None:
        package["derived_from"] = derived_from
    return parse_package(package)


def _clean_tree(descriptor):
    """Only used for this call's unpublished staging directory, anchored by descriptor."""
    with os.scandir(descriptor) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                _clean_tree(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=descriptor)
        else:
            os.unlink(name, dir_fd=descriptor)


def stage_directory(package, destination, scope):
    """Validate fully, then create a new private projected tree; never run its scripts.

    The caller must hold its managed-session/state lock and activate the returned
    tree separately. Destination's existing parent must have no symlink ancestors.
    """
    package = parse_package(package)
    name = projected_name(package["skill_id"], scope)
    destination = Path(os.path.abspath(destination))
    if destination.name != name:
        raise _reject("skill_identity_mismatch")
    prepared, installed_files = [], []
    for file in package["files"]:
        raw = _decode(file["content_base64"])
        if file["path"] == "SKILL.md":
            raw = (
                _document(raw, package["skill_id"])
                .replace("name: " + package["skill_id"], "name: " + name, 1)
                .encode("utf-8")
            )
        prepared.append((file["path"], raw, file["executable"]))
        installed_files.append({"path": file["path"], "sha256": _digest(raw), "executable": file["executable"]})
    result = {
        "path": destination,
        "runtime_name": name,
        "original_sha256": package_digest(package),
        "installed_sha256": _digest(canonical_json({"runtime_name": name, "files": installed_files}).encode("utf-8")),
        "installed_files": installed_files,
    }
    with _directory(destination.parent) as parent:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            raise _reject("skill_destination_exists") from None
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
        identity = os.fstat(descriptor)
        try:
            for path, raw, executable in prepared:
                current = os.dup(descriptor)
                try:
                    parts = path.split("/")
                    for component in parts[:-1]:
                        try:
                            os.mkdir(component, mode=0o700, dir_fd=current)
                        except FileExistsError:
                            pass
                        following = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
                        os.close(current)
                        current = following
                    file = os.open(
                        parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=current
                    )
                    with os.fdopen(file, "wb") as stream:
                        os.fchmod(stream.fileno(), 0o700 if executable else 0o600)
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.fsync(current)
                finally:
                    os.close(current)
            os.fsync(descriptor)
            current_identity = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (identity.st_dev, identity.st_ino) != (current_identity.st_dev, current_identity.st_ino):
                raise _reject("skill_directory_changed")
            os.fsync(parent)
        except BaseException:
            _clean_tree(descriptor)
            try:
                current_identity = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (identity.st_dev, identity.st_ino) == (current_identity.st_dev, current_identity.st_ino):
                    os.rmdir(name, dir_fd=parent)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(descriptor)
    return result
