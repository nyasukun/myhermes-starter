"""Crash-safe metadata-only audit/OTLP delivery, independent from persona outboxes."""

import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from .errors import CompanionError
from .files import private_dir, safe_path
from .telemetry_contract import MAX_QUEUE_EVENTS, OFFLINE_DAYS, canonical, digest, identifier, integer, validate_batch

DAY_NS = 86_400_000_000_000
IDLE_STREAM_DAYS = 30


class TelemetryState:
    def __init__(self, directory, installation_id):
        directory = private_dir(Path(directory))
        path = safe_path(directory / "telemetry.sqlite3")
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_uid != os.getuid()):
            raise CompanionError("unsafe_state", "Telemetry state must be an owner-only ordinary file with one link.")
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
        path.chmod(0o600)
        self.lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS telemetry_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS telemetry_outbox(
            cursor INTEGER PRIMARY KEY AUTOINCREMENT,batch_id TEXT UNIQUE NOT NULL,
            batch TEXT NOT NULL,batch_sha256 TEXT NOT NULL,audit_jws TEXT NOT NULL,traces BLOB NOT NULL,
            created_ns INTEGER NOT NULL,audit_sent INTEGER NOT NULL DEFAULT 0,traces_sent INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',finished_ns INTEGER
          );
        """)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                previous = self._get("installation_id")
                if previous is not None and previous != installation_id:
                    raise CompanionError(
                        "telemetry_installation_changed", "Use a fresh telemetry state directory for this installation."
                    )
                self._set("installation_id", installation_id)
                for key, default in (
                    ("stream_id", str(uuid.uuid4())),
                    ("sequence", 0),
                    ("previous_batch_sha256", None),
                    ("dropped_total", 0),
                    ("dropped_pending", 0),
                    ("sent_total", 0),
                ):
                    if self._get(key, "missing") == "missing":
                        self._set(key, default)
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self):
        with self.lock:
            self.db.close()

    def _get(self, key, default=None):
        row = self.db.execute("SELECT value FROM telemetry_metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO telemetry_metadata VALUES(?,?)", (key, canonical(value)))

    def _drop(self, count):
        self._set("dropped_total", self._get("dropped_total", 0) + count)
        self._set("dropped_pending", self._get("dropped_pending", 0) + count)

    def _validate_metadata(self):
        try:
            identifier(self._get("stream_id"))
            for key in ("sequence", "sent_total", "dropped_total", "dropped_pending"):
                integer(self._get(key))
            previous = self._get("previous_batch_sha256")
            if previous is not None and (
                not isinstance(previous, str)
                or len(previous) != 64
                or any(c not in "0123456789abcdef" for c in previous)
            ):
                raise ValueError("Invalid metadata")
            last = self._get("last_batch_ns")
            if last is not None:
                integer(last, maximum=9_223_372_036_854_775_807)
        except (CompanionError, ValueError, TypeError):
            raise CompanionError("telemetry_corrupt", "Monitoring state failed its integrity check.") from None

    def _rotate(self, now_ns):
        self._set("stream_id", str(uuid.uuid4()))
        self._set("sequence", 0)
        self._set("previous_batch_sha256", None)
        self._set("last_batch_ns", now_ns)

    def _prune(self, now_ns):
        self._validate_metadata()
        # Older state files predate the durable idle timestamp; recover it before
        # pruning the bounded receipt history.
        last = self._get("last_batch_ns")
        if last is None:
            last = self.db.execute("SELECT MAX(created_ns) FROM telemetry_outbox").fetchone()[0]
            if last is not None:
                self._set("last_batch_ns", last)
        expired = self.db.execute(
            "SELECT 1 FROM telemetry_outbox WHERE status='pending' AND created_ns<? LIMIT 1",
            (now_ns - OFFLINE_DAYS * DAY_NS,),
        ).fetchone()
        if expired:
            count = self.db.execute("SELECT COUNT(*) FROM telemetry_outbox WHERE status='pending'").fetchone()[0]
            # Removing part of a signed chain would silently make surviving batches invalid.
            self.db.execute(
                "UPDATE telemetry_outbox SET status='expired',finished_ns=? WHERE status='pending'", (now_ns,)
            )
            self._drop(count)
            self._rotate(now_ns)
        elif (
            last is not None
            and now_ns - last >= IDLE_STREAM_DAYS * DAY_NS
            and not self.db.execute("SELECT 1 FROM telemetry_outbox WHERE status='pending' LIMIT 1").fetchone()
        ):
            # Client creation and server receipt use different clocks. A prior
            # allowed +5-minute skew followed by clock correction can leave a
            # 90-day client threshold behind the server's 90-day expiry. Rotate
            # fully delivered idle streams after 30 days, with ample margin;
            # never rewrite or abandon an unexpired pending/partly ACKed batch.
            self._rotate(now_ns)
        self.db.execute(
            "DELETE FROM telemetry_outbox WHERE status!='pending' AND (finished_ns<? OR cursor NOT IN (SELECT cursor FROM telemetry_outbox WHERE status!='pending' ORDER BY cursor DESC LIMIT 1000))",
            (now_ns - 90 * DAY_NS,),
        )

    def queue(self, factory, now_ns, *, delivery=False):
        """Factory runs once, inside the same transaction as sequence/hash reservation."""
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self._prune(now_ns)
                count = self.db.execute("SELECT COUNT(*) FROM telemetry_outbox WHERE status='pending'").fetchone()[0]
                if count >= MAX_QUEUE_EVENTS:
                    if not delivery:
                        self._drop(1)
                    self.db.execute("COMMIT")
                    return None
                dropped = min(self._get("dropped_pending", 0), 100_000_000)
                if delivery and not dropped:
                    self.db.execute("COMMIT")
                    return None
                sequence = self._get("sequence") + 1
                batch, jws, traces = factory(
                    sequence, self._get("stream_id"), self._get("previous_batch_sha256"), dropped
                )
                batch = validate_batch(batch)
                batch_hash = digest(batch)
                self.db.execute(
                    "INSERT INTO telemetry_outbox(batch_id,batch,batch_sha256,audit_jws,traces,created_ns) VALUES(?,?,?,?,?,?)",
                    (batch["batch_id"], canonical(batch), batch_hash, jws, traces, now_ns),
                )
                self._set("sequence", sequence)
                self._set("previous_batch_sha256", batch_hash)
                self._set("last_batch_ns", now_ns)
                if delivery:
                    self._set("dropped_pending", self._get("dropped_pending") - dropped)
                self.db.execute("COMMIT")
                return batch
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def next(self, now_ns):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self._prune(now_ns)
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            row = self.db.execute(
                "SELECT cursor,batch,batch_sha256,audit_jws,traces,audit_sent,traces_sent FROM telemetry_outbox WHERE status='pending' ORDER BY cursor LIMIT 1"
            ).fetchone()
            if not row:
                return None
            batch = validate_batch(json.loads(row[1]))
            if digest(batch) != row[2] or row[5] not in (0, 1) or row[6] not in (0, 1):
                raise CompanionError("telemetry_corrupt", "Queued monitoring metadata failed its integrity check.")
            return {
                "cursor": row[0],
                "batch": batch,
                "batch_sha256": row[2],
                "audit_jws": row[3],
                "traces": row[4],
                "audit_sent": bool(row[5]),
                "traces_sent": bool(row[6]),
            }

    def acknowledge(self, cursor, transport, now_ns):
        if transport not in ("audit", "traces"):
            raise ValueError("Unknown metadata transport")
        column = "audit_sent" if transport == "audit" else "traces_sent"
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(
                    f"UPDATE telemetry_outbox SET {column}=1 WHERE cursor=? AND status='pending'", (cursor,)
                )
                result = self.db.execute(
                    "UPDATE telemetry_outbox SET status='delivered',finished_ns=? WHERE cursor=? AND status='pending' AND audit_sent=1 AND traces_sent=1",
                    (now_ns, cursor),
                )
                if result.rowcount:
                    self._set("sent_total", self._get("sent_total", 0) + 1)
                self._prune(now_ns)
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def inspect(self, *, include_wire=False, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Inspection limit must be from 1 to 1000")
        with self.lock:
            self._validate_metadata()
            rows = self.db.execute(
                "SELECT batch,batch_sha256,audit_jws,traces,status,audit_sent,traces_sent FROM telemetry_outbox ORDER BY cursor DESC LIMIT ?",
                (limit,),
            ).fetchall()
            records = []
            for row in rows:
                batch = validate_batch(json.loads(row[0]))
                if (
                    row[4] not in ("pending", "delivered", "expired")
                    or row[1] != digest(batch)
                    or row[5] not in (0, 1)
                    or row[6] not in (0, 1)
                ):
                    raise CompanionError("telemetry_corrupt", "Monitoring state failed its integrity check.")
                item = {
                    "batch": batch,
                    "batch_sha256": row[1],
                    "status": row[4],
                    "audit_sent": bool(row[5]),
                    "traces_sent": bool(row[6]),
                }
                if include_wire:
                    import base64

                    item.update(audit_jws=row[2], traces_base64=base64.b64encode(row[3]).decode("ascii"))
                records.append(item)
            return {
                "manifest_version": "1.0.0",
                "stream_id": self._get("stream_id"),
                "sequence": self._get("sequence"),
                "pending_events": self.db.execute(
                    "SELECT COUNT(*) FROM telemetry_outbox WHERE status='pending'"
                ).fetchone()[0],
                "sent_events": self._get("sent_total"),
                "dropped_events": self._get("dropped_total"),
                "unreported_dropped_events": self._get("dropped_pending"),
                "records": records,
            }
