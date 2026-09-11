"""Explicit same-owner identity replacement; no content or native secrets are copied."""

from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import termios
import uuid

from .errors import CompanionError
from .files import atomic_json, file_lock, private_dir, safe_path
from .local_config import _identifier, _read, canonical_identifier, config_read, marker_read

JOURNAL = "identity-recovery.json"
DATABASES = ("connections.sqlite3", "telemetry.sqlite3")
ARCHIVE_FILES = tuple(sorted(name + suffix for name in DATABASES for suffix in ("", "-journal", "-wal", "-shm")))
STAGES = {"enrolling", "owner_mismatch", "publishing", "completed", "cancelled"}


def rejected():
    return CompanionError(
        "identity_recovery_invalid",
        "Recovery metadata or files are unsafe; existing identity and content were preserved.",
        3,
    )


def _id(value):
    return _identifier(value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _journal(directory):
    path = safe_path(directory / JOURNAL)
    if not path.exists():
        return None
    try:
        value = _read(path)
        required = {
            "schema_version",
            "operation_id",
            "stage",
            "old_key_id",
            "old_installation_id",
            "person_id",
            "candidate_key_id",
            "original_sha256",
        }
        if not required <= set(value) or set(value) - required - {"archive_files", "candidate_sha256"}:
            raise ValueError()
        if value["schema_version"] != "1" or value["stage"] not in STAGES:
            raise ValueError()
        for key in ("operation_id", "old_key_id", "old_installation_id", "person_id", "candidate_key_id"):
            _id(value[key])
        for key in ("original_sha256", "candidate_sha256"):
            if key in value and (
                not isinstance(value[key], str)
                or len(value[key]) != 64
                or any(c not in "0123456789abcdef" for c in value[key])
            ):
                raise ValueError()
        names = value.get("archive_files")
        if value["stage"] in {"publishing", "completed"}:
            if "candidate_sha256" not in value:
                raise ValueError()
            if not isinstance(names, list) or names != sorted(set(names)) or any(n not in ARCHIVE_FILES for n in names):
                raise ValueError()
        elif names is not None or "candidate_sha256" in value:
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError, OSError):
        raise rejected() from None


@contextmanager
def identity_command(directory, *, recovery=False, dry_run=False):
    """Hold through command monitoring shutdown so no old queue survives a switch."""
    directory = Path(os.path.abspath(directory))
    # A dry run never creates state/lock files. Initial setup has no identity to replace.
    if dry_run or not (directory / "config.json").exists():
        yield
        return
    path = safe_path(directory / "identity.lock")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise rejected()
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if recovery else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CompanionError(
                "identity_busy", "Finish active companion commands and Hermes before re-enrollment.", 7
            ) from None
        current = _journal(directory)
        if not recovery and current and current["stage"] not in {"completed", "cancelled"}:
            raise CompanionError(
                "identity_recovery_pending", "Resume re-enroll or explicitly cancel its unpublished candidate first.", 3
            )
        yield
    finally:
        os.close(descriptor)


def _confirm(cancel=False):
    try:
        with open("/dev/tty", "r+", encoding="utf-8", buffering=1) as terminal:
            if not terminal.isatty():
                raise OSError()
            termios.tcgetattr(terminal.fileno())
            terminal.write(
                "Cancel this unpublished candidate and keep the original identity? Type yes: "
                if cancel
                else "Replace this installation key for the SAME owner, keeping personality and skills? Type yes: "
            )
            terminal.flush()
            if terminal.readline(32).strip() != "yes":
                raise CompanionError("identity_recovery_cancelled", "No recovery operation was performed.", 3)
    except (OSError, termios.error):
        raise CompanionError(
            "terminal_required", "Run re-enroll in your own native terminal, never through a captured Codex PTY.", 3
        ) from None


def _fsync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ordinary(path):
    path = safe_path(path)
    try:
        info = path.stat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise rejected()
    return True


def _context(directory, journal):
    operation = safe_path(directory / "identity-recovery" / journal["operation_id"])
    original = config_read(operation / "original")
    candidate = config_read(operation / "candidate")
    if any(original[k] != journal["old_" + k] for k in ("key_id", "installation_id")):
        raise rejected()
    if original.get("person_id") != journal["person_id"]:
        raise rejected()
    if _digest(original) != journal["original_sha256"] or candidate["key_id"] != journal["candidate_key_id"]:
        raise rejected()
    if journal["stage"] in {"publishing", "completed"} and (
        not candidate.get("person_id")
        or canonical_identifier(candidate["person_id"]) != canonical_identifier(original["person_id"])
        or _digest(candidate) != journal["candidate_sha256"]
    ):
        raise rejected()
    ignore = {"key_id", "agent_instance_id", "installation_id", "person_id", "enrollment"}
    if {k: v for k, v in original.items() if k not in ignore} != {
        k: v for k, v in candidate.items() if k not in ignore
    } or candidate["key_id"] == original["key_id"]:
        raise rejected()
    current = config_read(directory)
    if current not in (original, candidate):
        raise rejected()
    expected_old = {"key_id": original["key_id"], "state_directory": str(directory)}
    expected_new = {"key_id": candidate["key_id"], "state_directory": str(directory)}
    marker = marker_read(Path(original["hermes_home"]) / ".myhermes-installation.json")
    if marker not in (expected_old, expected_new):
        raise rejected()
    if journal["stage"] not in {"publishing", "completed"} and (current != original or marker != expected_old):
        raise rejected()
    return operation, original, candidate


def _checkpoint(directory):
    for name in ARCHIVE_FILES:
        _ordinary(directory / name)
    for name in DATABASES:
        path = directory / name
        if not path.exists():
            if any((directory / (name + suffix)).exists() for suffix in ("-journal", "-wal", "-shm")):
                raise rejected()
            continue
        # The exclusive identity lock excludes all current companion processes.
        # SQLite also refuses an older external writer instead of moving a live WAL.
        try:
            connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0)
            try:
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint and checkpoint[0] != 0:
                    raise rejected()
                mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
                if not mode or mode[0].lower() != "delete":
                    raise rejected()
                connection.execute("BEGIN EXCLUSIVE")
                connection.rollback()
            finally:
                connection.close()
        except sqlite3.Error:
            raise CompanionError(
                "identity_archive_busy",
                "Old connection or monitoring state could not be closed safely; retry recovery.",
                7,
            ) from None
    return [name for name in ARCHIVE_FILES if _ordinary(directory / name)]


def _archive(directory, operation, names):
    archive = private_dir(operation / "archive")
    for name in ARCHIVE_FILES:
        source, target = directory / name, archive / name
        here, there = _ordinary(source), _ordinary(target)
        if name not in names:
            if here or there:
                raise rejected()
        elif here == there:
            raise rejected()
    for name in names:
        source, target = directory / name, archive / name
        if source.exists():
            os.chmod(source, 0o600)
            os.rename(source, target)
            _fsync(directory)
            _fsync(archive)


def _receipt(journal):
    return {
        "status": journal["stage"],
        "operation_id": journal["operation_id"],
        "personality_and_skills": "preserved",
        "old_credentials": "unchanged_in_native_store",
        "connections": "reauthorization_required",
        "old_monitoring": "local_archive_only",
    }


def re_enroll(directory, args, *, enroll, owner_api):
    """Caller holds exclusive identity lock. Other locks protect older launchers too."""
    directory = safe_path(Path(os.path.abspath(directory)))
    config = config_read(directory)
    if not config.get("installation_id") or not config.get("person_id"):
        raise CompanionError(
            "not_enrolled", "Use enroll for a new environment; re-enroll requires an existing owner.", 3
        )
    journal = _journal(directory)
    if args.dry_run:
        return {
            "status": "dry_run",
            "stage": journal["stage"] if journal else "not_started",
            "network_requests": False,
            "native_credentials_read": False,
            "personality_and_skills": "preserved",
            "requires_same_owner": True,
            "old_monitoring_and_connections": "local_archive_only",
            "new_operation_requires_explicit_new": bool(journal),
        }
    if journal and journal["stage"] == "completed" and not args.new:
        return _receipt(journal)
    if journal and journal["stage"] == "cancelled" and not args.new:
        return {"status": "cancelled", "operation_id": journal["operation_id"], "original_identity": "preserved"}
    if args.new and journal and journal["stage"] not in {"completed", "cancelled"}:
        raise CompanionError(
            "identity_candidate_active", "Resume or explicitly --cancel the existing candidate first.", 3
        )
    if args.cancel and (journal is None or journal["stage"] not in {"enrolling", "owner_mismatch"}):
        raise CompanionError(
            "identity_cancel_unavailable", "Only an unpublished enrollment candidate can be cancelled.", 3
        )
    _confirm(args.cancel)
    with ExitStack() as stack:
        for name in ("companion.lock", "skills.lock", "connections.lock"):
            stack.enter_context(file_lock(directory / name))
        stack.enter_context(file_lock(Path(config["hermes_home"]) / ".myhermes-session.lock"))
        if journal is None or args.new:
            marker = marker_read(Path(config["hermes_home"]) / ".myhermes-installation.json")
            if marker != {"key_id": config["key_id"], "state_directory": str(directory)}:
                raise rejected()
            operation_id = str(uuid.uuid4())
            operation = private_dir(directory / "identity-recovery" / operation_id)
            atomic_json(operation / "original" / "config.json", config)
            candidate = {k: v for k, v in config.items() if k not in {"installation_id", "person_id", "enrollment"}}
            candidate.update(key_id=str(uuid.uuid4()), agent_instance_id=str(uuid.uuid4()))
            atomic_json(operation / "candidate" / "config.json", candidate)
            journal = {
                "schema_version": "1",
                "operation_id": operation_id,
                "stage": "enrolling",
                "old_key_id": config["key_id"],
                "old_installation_id": config["installation_id"],
                "person_id": config["person_id"],
                "candidate_key_id": candidate["key_id"],
                "original_sha256": _digest(config),
            }
            atomic_json(directory / JOURNAL, journal)
        operation, original, candidate = _context(directory, journal)
        if args.cancel:
            journal["stage"] = "cancelled"
            atomic_json(operation / "result.json", journal)
            atomic_json(directory / JOURNAL, journal)
            return {"status": "cancelled", "operation_id": journal["operation_id"], "original_identity": "preserved"}
        if journal["stage"] == "owner_mismatch":
            raise CompanionError(
                "identity_owner_mismatch", "Candidate belongs to another person. Use re-enroll --cancel, then --new.", 5
            )
        if journal["stage"] == "enrolling":
            try:
                result = enroll(candidate, operation / "candidate", args)
            except CompanionError as error:
                if error.code == "enrollment_code_missing":
                    raise CompanionError(
                        "enrollment_code_missing",
                        "Candidate code is unavailable. Use re-enroll --cancel, then --new; original content remains local.",
                        3,
                    ) from None
                raise
            if result["status"] == "pending":
                return {"status": "pending", "retry": "re-enroll", "operation_id": journal["operation_id"]}
            candidate = config_read(operation / "candidate")
            if not candidate.get("person_id") or canonical_identifier(candidate["person_id"]) != canonical_identifier(
                original["person_id"]
            ):
                journal["stage"] = "owner_mismatch"
                atomic_json(directory / JOURNAL, journal)
                raise CompanionError(
                    "identity_owner_mismatch",
                    "Candidate belongs to another person. Use re-enroll --cancel, then --new.",
                    5,
                )
            if not candidate.get("installation_id") or canonical_identifier(
                candidate["installation_id"]
            ) == canonical_identifier(original["installation_id"]):
                raise rejected()
            # Resume candidates completed by an older client that preserved
            # uppercase server UUIDs, while leaving local/native key IDs intact.
            normalized = {
                **candidate,
                **{key: canonical_identifier(candidate[key]) for key in ("installation_id", "person_id")},
            }
            if normalized != candidate:
                atomic_json(operation / "candidate" / "config.json", normalized)
                candidate = normalized
            status, me = owner_api(candidate).request("GET", "/v1/me")
            try:
                verified = (
                    status == 200
                    and canonical_identifier(me.get("person_id")) == canonical_identifier(original["person_id"])
                    and canonical_identifier(me.get("installation_id")) == candidate["installation_id"]
                )
            except (ValueError, TypeError, AttributeError):
                verified = False
            if not verified:
                raise CompanionError(
                    "identity_owner_unverified", "The candidate's authenticated owner could not be verified.", 5
                )
            journal.update(
                stage="publishing", archive_files=_checkpoint(directory), candidate_sha256=_digest(candidate)
            )
            atomic_json(directory / JOURNAL, journal)
        _archive(directory, operation, journal["archive_files"])
        atomic_json(
            Path(original["hermes_home"]) / ".myhermes-installation.json",
            {"key_id": candidate["key_id"], "state_directory": str(directory)},
        )
        atomic_json(directory / "config.json", candidate)
        journal["stage"] = "completed"
        atomic_json(operation / "result.json", journal)
        atomic_json(directory / JOURNAL, journal)
        return _receipt(journal)
