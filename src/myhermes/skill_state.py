"""Durable personal skill outbox and recoverable whole-directory activation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import uuid

from .errors import CompanionError
from .files import private_dir, safe_path
from .skill_packages import pack_directory, package_digest, projected_name, stage_directory


def package_hash(package):
    return package_digest(package) if package is not None else None


def checked_directory(path):
    path = safe_path(Path(path))
    if path.exists():
        if not path.is_dir():
            raise CompanionError("skill_directory_rejected", "The skill directory is not an ordinary directory.")
    else:
        path.mkdir(mode=0o700, parents=True)
    return path


def runtime_package(path, package, scope):
    path = safe_path(path)
    if not path.exists():
        return None
    if package is None:
        raise CompanionError(
            "skill_unmanaged_collision", "An unmanaged directory occupies this skill's runtime name.", 6
        )
    try:
        return pack_directory(
            path,
            package["skill_id"],
            package["version"],
            package["description"],
            package["requires"],
            derived_from=package.get("derived_from"),
            projected_scope=scope,
        )
    except CompanionError:
        raise CompanionError(
            "skill_runtime_modified",
            "A managed skill directory is modified or unsafe; preserve it and import or derive explicitly.",
            6,
        ) from None


class SkillState:
    def __init__(self, directory):
        self.directory = private_dir(Path(directory))
        path = safe_path(self.directory / "skills.sqlite3")
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise CompanionError("unsafe_state", "Skill state must be an ordinary database file with one link.")
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS skill_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS skill_outbox(
                update_id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL,
                expected TEXT,response TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM skill_metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO skill_metadata VALUES (?,?)", (key, json.dumps(value)))

    def records(self, prefix):
        return {
            row[0][len(prefix) :]: json.loads(row[1])
            for row in self.db.execute(
                "SELECT key,value FROM skill_metadata WHERE key LIKE ? ORDER BY key", (prefix + "%",)
            )
        }

    def outbox(self, *statuses):
        rows = self.db.execute(
            "SELECT update_id,payload,status,expected,response FROM skill_outbox ORDER BY rowid"
        ).fetchall()
        return [
            {
                "update_id": row[0],
                "payload": json.loads(row[1]),
                "status": row[2],
                "expected": json.loads(row[3]) if row[3] is not None else None,
                "response": json.loads(row[4]) if row[4] is not None else None,
            }
            for row in rows
            if not statuses or row[2] in statuses
        ]

    def request(self, update_id):
        value = next((row for row in self.outbox() if row["update_id"] == update_id), None)
        if value is None:
            raise CompanionError("skill_request_missing", "No local skill update has this ID.", 3)
        return value

    def transaction(self, *, values=(), queued=(), transitions=()):
        with self.db:
            for key, value in values:
                self.db.execute("INSERT OR REPLACE INTO skill_metadata VALUES (?,?)", (key, json.dumps(value)))
            for row in queued:
                self.db.execute(
                    "INSERT OR IGNORE INTO skill_outbox(update_id,payload,status,expected) VALUES (?,?,'pending',?)",
                    (row["payload"]["update_id"], json.dumps(row["payload"]), json.dumps(row.get("expected"))),
                )
            for row in transitions:
                self.db.execute(
                    "UPDATE skill_outbox SET status=?,response=? WHERE update_id=?",
                    (row["status"], json.dumps(row.get("response")), row["update_id"]),
                )

    def installed(self, scope, name):
        return self.get("installed:" + scope + ":" + name)

    def runtime_path(self, home, scope, name):
        return safe_path(Path(home) / "skills" / projected_name(name, scope))

    def inspect_runtime(self, home, scope, name):
        installed = self.installed(scope, name)
        metadata = installed["package"] if installed else None
        current = runtime_package(self.runtime_path(home, scope, name), metadata, scope)
        if package_hash(current) != package_hash(metadata):
            raise CompanionError(
                "skill_runtime_modified",
                "Managed skill files changed or disappeared; import/derive or restore explicitly before sync.",
                6,
            )
        return current

    def apply(self, home, scope, name, package, *, values=(), queued=(), transitions=(), accept_drift=False):
        if self.get("journal") is not None:
            self.recover(home)
        target = self.runtime_path(home, scope, name)
        installed = self.installed(scope, name)
        metadata = installed["package"] if installed else None
        expected = runtime_package(target, metadata, scope)
        if not accept_drift and package_hash(expected) != package_hash(metadata):
            raise CompanionError(
                "skill_runtime_modified", "Local managed skill edits are preserved; use explicit import or derive.", 6
            )
        if package_hash(expected) == package_hash(package):
            self.transaction(values=values, queued=queued, transitions=transitions)
            return
        checked_directory(Path(home) / "skills")
        operation_id = str(uuid.uuid4())
        staging = checked_directory(Path(home) / ".myhermes-skill-stage" / operation_id)
        checked_directory(Path(home) / ".myhermes-skill-backups" / operation_id)
        prepared = None
        if package is not None:
            prepared = stage_directory(package, staging / target.name, scope)
        journal = {
            "operation_id": operation_id,
            "scope": scope,
            "skill_id": name,
            "expected": expected,
            "target": package,
            "installed": {"package": package, "installed_sha256": prepared["installed_sha256"] if prepared else None},
            "values": list(values),
            "queued": list(queued),
            "transitions": list(transitions),
        }
        # The staging tree is complete and fsynced before the durable intent.
        self.put("journal", journal)
        self.recover(home)

    def recover(self, home):
        journal = self.get("journal")
        if journal is None:
            return
        scope, name = journal["scope"], journal["skill_id"]
        target = self.runtime_path(home, scope, name)
        staging = safe_path(Path(home) / ".myhermes-skill-stage" / journal["operation_id"] / target.name)
        backup = safe_path(Path(home) / ".myhermes-skill-backups" / journal["operation_id"] / target.name)
        expected, replacement = journal["expected"], journal["target"]
        # Recognize a completed rename without interpreting the old package's
        # frontmatter metadata as the new tree. A third state requires owner recovery.
        present = None
        for candidate in (replacement, expected):
            if candidate is None:
                continue
            try:
                current = runtime_package(target, candidate, scope)
            except CompanionError:
                continue
            if package_hash(current) == package_hash(candidate):
                present = "target" if candidate is replacement else "expected"
                break
        if target.exists() and present is None:
            raise CompanionError(
                "skill_recovery_conflict",
                "Files changed after interrupted skill activation; preserve them and recover explicitly.",
                6,
            )
        if present == "expected" and package_hash(expected) != package_hash(replacement):
            if backup.exists():
                raise CompanionError(
                    "skill_recovery_conflict", "The recovery backup already exists while original files are active.", 6
                )
            os.rename(target, backup)
            self._fsync(target.parent)
            self._fsync(backup.parent)
            present = None
        if present is None and replacement is not None:
            staged = runtime_package(staging, replacement, scope)
            if package_hash(staged) != package_hash(replacement):
                raise CompanionError(
                    "skill_recovery_conflict",
                    "The staged skill is missing or modified; recovery cannot overwrite it.",
                    6,
                )
            os.rename(staging, target)
            self._fsync(target.parent)
            self._fsync(staging.parent)
        # An absent target is already the requested removal state, including
        # when the owner moved/deleted the old tree before explicit recovery.
        # There are no present bytes to overwrite or discard in this case.
        self.transaction(
            values=[*journal["values"], ("installed:" + scope + ":" + name, journal["installed"]), ("journal", None)],
            queued=journal["queued"],
            transitions=journal["transitions"],
        )

    def recover_preserving_local(self, home):
        """Owner-selected target recovery, retaining every interrupted tree as data."""
        journal = self.get("journal")
        if journal is None:
            return {"status": "no_recovery_pending"}
        scope, name = journal["scope"], journal["skill_id"]
        runtime = self.runtime_path(home, scope, name)
        recovery_id = str(uuid.uuid4())
        preserved = checked_directory(Path(home) / ".myhermes-skill-backups" / recovery_id)
        backup = safe_path(Path(home) / ".myhermes-skill-backups" / journal["operation_id"] / runtime.name)
        staging = safe_path(Path(home) / ".myhermes-skill-stage" / journal["operation_id"] / runtime.name)
        if runtime.exists():
            # Never traverse or execute preserved files. The owner's explicit
            # choice moves the whole ordinary directory outside runtime discovery.
            if not runtime.is_dir():
                raise CompanionError("skill_recovery_conflict", "The runtime path is not an ordinary directory.", 6)
            os.rename(runtime, backup if not backup.exists() else preserved / runtime.name)
            self._fsync(runtime.parent)
            self._fsync(backup.parent)
            self._fsync(preserved)
        if staging.exists():
            os.rename(staging, preserved / (runtime.name + "-staged"))
            self._fsync(staging.parent)
        if journal["target"] is not None:
            stage_directory(journal["target"], staging, scope)
        self.recover(home)
        return {"status": "recovered", "preserved_id": recovery_id, "operation_id": journal["operation_id"]}

    def restore_runtime(self, home, scope, name, package, *, values=()):
        if self.get("journal") is not None:
            raise CompanionError(
                "skill_recovery_pending", "Complete the existing skill recovery before restoring another directory.", 6
            )
        installed = self.installed(scope, name)
        if installed is None:
            raise CompanionError("skill_unmanaged_collision", "Only a recorded managed skill can be restored.", 6)
        operation_id = str(uuid.uuid4())
        target = self.runtime_path(home, scope, name)
        staging = checked_directory(Path(home) / ".myhermes-skill-stage" / operation_id)
        checked_directory(Path(home) / ".myhermes-skill-backups" / operation_id)
        prepared = stage_directory(package, staging / target.name, scope) if package is not None else None
        self.put(
            "journal",
            {
                "operation_id": operation_id,
                "scope": scope,
                "skill_id": name,
                "expected": installed["package"],
                "target": package,
                "installed": {
                    "package": package,
                    "installed_sha256": prepared["installed_sha256"] if prepared else None,
                },
                "values": list(values),
                "queued": [],
                "transitions": [],
            },
        )
        return self.recover_preserving_local(home)

    @staticmethod
    def _fsync(path):
        descriptor = os.open(safe_path(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
