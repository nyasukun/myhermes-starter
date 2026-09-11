"""Strict allowlist, no-follow reads, Hermes-compatible locks and atomic writes."""

from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from .errors import CompanionError

LIMITS = {"SOUL.md": 65_536, "memories/MEMORY.md": 2200, "memories/USER.md": 1375}
MAX_BYTES = 262_144


def safe_path(root: Path, relative: str = "") -> Path:
    root = Path(os.path.abspath(root))
    if relative and relative not in LIMITS:
        raise CompanionError("path_rejected", "Only the published personality file allowlist is accepted.")
    result = root / relative
    for part in [*reversed(result.parents), result]:
        if part.is_symlink():
            raise CompanionError("symlink_rejected", "Symlink paths are not allowed for synchronized files or state.")
    return result


def private_dir(path: Path) -> Path:
    path = safe_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or path.stat().st_uid != os.getuid():
        raise CompanionError("unsafe_directory", "Local state and home must be directories owned by this user.")
    # These are explicitly chosen application directories, not arbitrary parent folders.
    os.chmod(path, 0o700)
    return path


def validate_content(path: str, content: str | None) -> None:
    if path not in LIMITS or (content is not None and not isinstance(content, str)):
        raise CompanionError("schema_rejected", "Invalid personality path or content type.")
    try:
        byte_length = len(content.encode("utf-8")) if content is not None else 0
    except UnicodeEncodeError:
        raise CompanionError("encoding_rejected", "Personality files must use valid UTF-8.") from None
    if content is not None and ("\x00" in content or len(content) > LIMITS[path] or byte_length > MAX_BYTES):
        raise CompanionError(
            "capacity_exceeded",
            "Personality file exceeds supported upstream capacity or contains a NUL byte; nothing was truncated.",
        )


def read_file(root: Path, relative: str) -> str | None:
    target = safe_path(root, relative)
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CompanionError("file_rejected", "Only regular files with a single link may synchronize.")
        raw = stream.read(MAX_BYTES + 1)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CompanionError("encoding_rejected", "Personality files must use UTF-8.") from None
    validate_content(relative, content)
    return content


def snapshot(root: Path) -> dict[str, str | None]:
    return {path: read_file(root, path) for path in LIMITS}


def atomic_bytes(path: Path, data: bytes) -> None:
    path = safe_path(path)
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".myhermes-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path)
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, data: object) -> None:
    atomic_bytes(path, (json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n").encode())


@contextmanager
def _export_directory(path: Path):
    """Create private missing directories without chmod of existing user directories."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            created = False
            try:
                following = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
                following = os.open(component, flags, dir_fd=descriptor)
                created = True
            if created:
                os.fchmod(following, 0o700)
                os.fsync(descriptor)
            os.close(descriptor)
            descriptor = following
        if os.fstat(descriptor).st_uid != os.getuid():
            raise CompanionError("unsafe_directory", "The export directory must be owned by this user.")
        yield descriptor
    finally:
        os.close(descriptor)


def export_json(path: Path, data: object) -> None:
    """Atomically export a private file while preserving an existing directory's mode."""
    path = safe_path(path)
    raw = (json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n").encode()
    try:
        with _export_directory(path.parent) as directory:

            def existing_file():
                try:
                    info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    return None
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                    raise CompanionError(
                        "file_rejected", "Export destinations must be owned regular files with one link."
                    )
                return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size

            expected = existing_file()
            temporary = ".myhermes-export-" + uuid.uuid4().hex
            descriptor = os.open(
                temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                if existing_file() != expected:
                    raise CompanionError(
                        "concurrent_edit", "The export destination changed; its contents were preserved.", 6
                    )
                os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
    except OSError:
        raise CompanionError("export_failed", "The private export could not be written safely.") from None


def atomic_content(root: Path, relative: str, content: str | None) -> None:
    validate_content(relative, content)
    path = safe_path(root, relative)
    if content is None:
        path.unlink(missing_ok=True)
        if path.parent.exists():
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    else:
        atomic_bytes(path, content.encode("utf-8"))


@contextmanager
def file_lock(path: Path):
    path = safe_path(path)
    private_dir(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
            raise CompanionError("unsafe_lock", "Lock must be a regular file with one link.")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CompanionError(
                "home_busy", "This home has an active managed session or writer; retry after it finishes.", 7
            ) from None
        yield
    finally:
        os.close(fd)


@contextmanager
def memory_locks(home: Path):
    with ExitStack() as stack:
        for name in ("memories/MEMORY.md.lock", "memories/USER.md.lock"):
            stack.enter_context(file_lock(home / name))
        yield
