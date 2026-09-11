"""Checkout-wide cooperative admission for managed dependency writers and readers."""

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import unicodedata

from .errors import CompanionError

DIRECTORY = ".myhermes-runtime-locks"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _unsafe():
    return CompanionError(
        "runtime_lock_unsafe",
        "Runtime admission requires safe owned lock files and a stable checkout parent; no runtime operation ran.",
        3,
    )


@contextmanager
def _parent(path, *, create):
    descriptor = os.open("/", _DIR_FLAGS)
    try:
        for component in path.parts[1:]:
            try:
                following = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass  # Another caller may have created the same safe parent.
                following = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        info = os.fstat(descriptor)
        # Existing user directories retain their mode. A system-owned sticky
        # temporary parent is also usable; the lock directory below is private.
        if info.st_uid not in (os.getuid(), 0) or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
            raise _unsafe()
        yield descriptor
    finally:
        os.close(descriptor)


def _identity(info):
    return info.st_dev, info.st_ino


@contextmanager
def runtime_target_lock(path: Path, *, writer: bool):
    """Hold after identity/state/home locks; never delete or replace a lock inode.

    Readers hold this through the complete managed session and its finalization.
    Writers hold it through backup, checkout verification and dependency updates.
    Dry-run callers skip this context and do not claim a live admission check.
    Direct low-level runtime callers must acquire the same context themselves.
    """
    target = Path(os.path.abspath(path))
    if target == target.parent or DIRECTORY in target.parts:
        raise _unsafe()
    # The namespace already identifies the actual parent directory. A full path
    # hash would split locks for case/Unicode aliases on macOS. Keep a stable key
    # before and after first clone; distinct case-sensitive Linux names may
    # conservatively serialize, which is preferable to admitting a writer race.
    basename = unicodedata.normalize("NFC", unicodedata.normalize("NFC", target.name).casefold())
    name = hashlib.sha256(os.fsencode(basename)).hexdigest() + ".lock"
    admitted = False
    try:
        with _parent(target.parent, create=True) as parent:
            try:
                os.mkdir(DIRECTORY, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
            directory = os.open(DIRECTORY, _DIR_FLAGS, dir_fd=parent)
            try:
                directory_info = os.fstat(directory)
                if directory_info.st_uid != os.getuid() or stat.S_IMODE(directory_info.st_mode) != 0o700:
                    raise _unsafe()
                descriptor = os.open(
                    name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory
                )
                try:
                    info = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_size != 0
                    ):
                        raise _unsafe()
                    try:
                        fcntl.flock(descriptor, (fcntl.LOCK_EX if writer else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise CompanionError(
                            "runtime_busy",
                            "This Hermes checkout has an active managed session or dependency update; retry afterward.",
                            7,
                        ) from None
                    # Detect replacement of a parent, namespace or lock during
                    # descriptor traversal/admission before permitting work.
                    with _parent(target.parent, create=False) as current:
                        if (
                            _identity(os.fstat(parent)) != _identity(os.fstat(current))
                            or _identity(directory_info)
                            != _identity(os.stat(DIRECTORY, dir_fd=current, follow_symlinks=False))
                            or _identity(info) != _identity(os.stat(name, dir_fd=directory, follow_symlinks=False))
                        ):
                            raise _unsafe()
                    try:
                        destination = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        destination = None
                    if destination is not None and (
                        not stat.S_ISDIR(destination.st_mode) or destination.st_uid != os.getuid()
                    ):
                        raise _unsafe()
                    admitted = True
                    yield
                finally:
                    os.close(descriptor)
            finally:
                os.close(directory)
    except OSError:
        if admitted:
            raise
        raise _unsafe() from None
