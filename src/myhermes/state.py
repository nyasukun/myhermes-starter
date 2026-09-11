"""Durable owner-local outbox and crash recovery journal, never a shared home."""

import json
from pathlib import Path
import sqlite3
import uuid

from .errors import CompanionError
from .files import LIMITS, atomic_content, private_dir, read_file, safe_path, snapshot


class State:
    def __init__(self, directory: Path):
        self.directory = private_dir(directory)
        db_path = safe_path(directory / "state.sqlite3")
        if db_path.exists() and (not db_path.is_file() or db_path.stat().st_nlink != 1):
            raise CompanionError("unsafe_state", "State database must be an ordinary file with one link.")
        self.db = sqlite3.connect(db_path)
        db_path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS outbox (
            update_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','conflict','done','resolved')),
            response TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        """)

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value)))

    @property
    def revision(self):
        value = self.get("revision", 0)
        if type(value) is not int or not 0 <= value <= 9_007_199_254_740_991:
            raise CompanionError("state_rejected", "Local synchronization metadata is invalid; no content was output.")
        return value

    @property
    def baseline(self):
        return self.get("baseline", {path: None for path in LIMITS})

    def queue(self, changes, *, base_revision=None, resolves=None, resolution_expected=None):
        update_id = str(uuid.uuid4())
        payload = {
            "schema_version": "1",
            "update_id": update_id,
            "base_revision": self.revision if base_revision is None else base_revision,
            "changes": changes,
        }
        if resolves:
            payload["resolves_update_id"] = resolves
        with self.db:
            self.db.execute(
                "INSERT INTO outbox(update_id,payload,status) VALUES (?,?,'pending')", (update_id, json.dumps(payload))
            )
            if resolution_expected is not None:
                self.db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                    ("resolution_expected:" + update_id, json.dumps(resolution_expected)),
                )
        return payload

    def capture_session(self, files, *, changes=None):
        """Save later local intent without rewriting an immutable transmitted update."""
        checkpoint = {"base_revision": self.revision, "files": files}
        update = None
        if changes:
            update = {
                "schema_version": "1",
                "update_id": str(uuid.uuid4()),
                "base_revision": self.revision,
                "changes": changes,
            }
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('session_checkpoint',?)", (json.dumps(checkpoint),)
            )
            if update is not None:
                self.db.execute(
                    "INSERT INTO outbox(update_id,payload,status) VALUES (?,?,'pending')",
                    (update["update_id"], json.dumps(update)),
                )
        return update

    def items(self, status):
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT payload FROM outbox WHERE status=? ORDER BY created_at, rowid", (status,)
            )
        ]

    def mark(self, update_id, status, response=None):
        self.mark_many([{"update_id": update_id, "status": status, "response": response}])

    def mark_many(self, transitions):
        with self.db:
            for item in transitions:
                self.db.execute(
                    "UPDATE outbox SET status=?,response=? WHERE update_id=?",
                    (item["status"], json.dumps(item.get("response")), item["update_id"]),
                )

    def journal_apply(
        self, home: Path, expected, target, revision, baseline, *, residual_updates=None, completions=None
    ):
        # Compare before recording the transaction, then re-check every changed file.
        if snapshot(home) != expected:
            raise CompanionError(
                "concurrent_edit", "Local files changed during synchronization; changes are preserved. Retry.", 6
            )
        journal = {
            "expected": expected,
            "target": target,
            "revision": revision,
            "baseline": baseline,
            "residual_updates": residual_updates or [],
            "completions": completions or [],
        }
        self.put("apply_journal", journal)
        self.recover(home)

    def recover(self, home: Path):
        journal = self.get("apply_journal")
        if journal is None:
            return
        # Preflight all files so a post-crash user edit cannot be silently overwritten.
        for path in LIMITS:
            current = read_file(home, path)
            if current not in (journal["expected"][path], journal["target"][path]):
                raise CompanionError(
                    "recovery_conflict",
                    "A file changed after interrupted apply. Use recover --choice local or target; both candidates will be backed up before recovery.",
                    6,
                )
        for path in LIMITS:
            if read_file(home, path) != journal["target"][path]:
                atomic_content(home, path, journal["target"][path])
        with self.db:
            for update in journal.get("residual_updates", []):
                self.db.execute(
                    "INSERT OR IGNORE INTO outbox(update_id,payload,status) VALUES (?,?,'pending')",
                    (update["update_id"], json.dumps(update)),
                )
            for item in journal.get("completions", []):
                self.db.execute(
                    "UPDATE outbox SET status=?,response=? WHERE update_id=?",
                    (item["status"], json.dumps(item.get("response")), item["update_id"]),
                )
            for key, value in (
                ("revision", journal["revision"]),
                ("baseline", journal["baseline"]),
                ("apply_journal", None),
            ):
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value)))
            settled = not self.db.execute(
                "SELECT 1 FROM outbox WHERE status IN ('pending','conflict') LIMIT 1"
            ).fetchone()
            if journal["target"] == journal["baseline"] and settled:
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('session_checkpoint','null')")
                # A historical applied report is eligible only for a complete
                # snapshot, never a mixed baseline retaining unsent local edits.
                self.db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES ('sync_ack_pending',?)", (json.dumps(journal["revision"]),)
                )
