"""Local metadata-only connector journal and native per-installation credential store."""

import hmac
import json
import sqlite3
import uuid
from pathlib import Path

from .auth import SecureKeyStore
from .connection_schema import request_id
from .errors import CompanionError
from .files import private_dir, safe_path
from .github import validate_pat


class GitHubCredentialStore:
    SERVICE = "io.myhermes.github.v1"

    def __init__(self, installation_id: str, *, backend=None):
        self.installation_id = request_id(installation_id)
        self.backend = backend if backend is not None else SecureKeyStore().backend

    def _key(self, credential_id):
        return self.installation_id + ":" + request_id(credential_id)

    def load(self, credential_id):
        try:
            token = self.backend.get_password(self.SERVICE, self._key(credential_id))
        except Exception:
            raise CompanionError(
                "credential_unavailable",
                "The native store is unavailable or locked; no plaintext fallback is supported.",
                3,
            ) from None
        if token is not None:
            validate_pat(token)
        return token

    def save(self, credential_id, token):
        validate_pat(token)
        try:
            self.backend.set_password(self.SERVICE, self._key(credential_id), token)
            saved = self.backend.get_password(self.SERVICE, self._key(credential_id))
            if not isinstance(saved, str) or not hmac.compare_digest(saved, token):
                raise ValueError("Credential readback failed")
        except Exception:
            raise CompanionError(
                "credential_unavailable",
                "The credential could not be verified in the native store; no ready binding was reported.",
                3,
            ) from None

    def delete(self, credential_id):
        try:
            if self.backend.get_password(self.SERVICE, self._key(credential_id)) is not None:
                self.backend.delete_password(self.SERVICE, self._key(credential_id))
        except Exception:
            raise CompanionError(
                "credential_unavailable", "The local credential could not be removed from the native store.", 3
            ) from None


class ConnectionStore:
    def __init__(self, directory: Path):
        self.directory = private_dir(directory)
        path = safe_path(self.directory / "connections.sqlite3")
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise CompanionError("unsafe_state", "The connector database must be a regular file with a single link.")
        self.db = sqlite3.connect(path, timeout=10)
        path.chmod(0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS connector_pending (
                request_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                phase TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS connector_bindings (
                connection_id TEXT PRIMARY KEY,
                credential_id TEXT NOT NULL,
                provider_account_id TEXT NOT NULL,
                grant_revision INTEGER NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def create_pending(self, kind, data, *, pending_id=None, phase="authorized"):
        pending_id = pending_id or str(uuid.uuid4())
        request_id(pending_id)
        with self.db:
            self.db.execute(
                "INSERT INTO connector_pending(request_id,kind,phase,data) VALUES (?,?,?,?)",
                (pending_id, kind, phase, json.dumps(data)),
            )
        return self.pending(pending_id)

    def pending(self, pending_id):
        request_id(pending_id)
        row = self.db.execute(
            "SELECT request_id,kind,phase,data FROM connector_pending WHERE request_id=?", (pending_id,)
        ).fetchone()
        if row is None:
            raise CompanionError("connection_request_missing", "No local connector request has this ID.", 3)
        return {"request_id": row[0], "kind": row[1], "phase": row[2], "data": json.loads(row[3])}

    def update(self, pending_id, data, phase):
        with self.db:
            self.db.execute(
                "UPDATE connector_pending SET data=?,phase=?,updated_at=CURRENT_TIMESTAMP WHERE request_id=?",
                (json.dumps(data), phase, pending_id),
            )

    def finish(self, pending, connection_id, provider_account_id, grant_revision):
        data = pending["data"]
        with self.db:
            previous = self.binding(connection_id)
            data["cleanup_ids"] = (
                [previous["credential_id"]] if previous and previous["credential_id"] != data["credential_id"] else []
            )
            self.db.execute(
                "INSERT OR REPLACE INTO connector_bindings VALUES (?,?,?,?)",
                (connection_id, data["credential_id"], provider_account_id, grant_revision),
            )
            self.db.execute(
                "UPDATE connector_pending SET phase='cleanup_pending',data=?,updated_at=CURRENT_TIMESTAMP WHERE request_id=?",
                (json.dumps(data), pending["request_id"]),
            )

    def cancel_pending(self, pending_id):
        pending = self.pending(pending_id)
        if pending["phase"] in ("complete", "cancelled") or pending["kind"] in ("cancel", "forget"):
            return pending
        if pending["phase"] == "cleanup_pending":
            raise CompanionError(
                "connection_already_active", "This request is active; finish cleanup or forget the connection.", 3
            )
        data = pending["data"]
        binding = self.binding(data["connection_id"]) if data.get("connection_id") else None
        data["cleanup_ids"] = (
            [data["credential_id"]] if not binding or binding["credential_id"] != data["credential_id"] else []
        )
        data["original_kind"] = pending["kind"]
        with self.db:
            self.db.execute(
                "UPDATE connector_pending SET kind='cancel',phase='cancelling',data=?,updated_at=CURRENT_TIMESTAMP WHERE request_id=?",
                (json.dumps(data), pending_id),
            )
        return self.pending(pending_id)

    def begin_forget(self, connection_id):
        """Detach local use and all resumable activations in one durable transaction."""
        request_id(connection_id)
        rows = self.db.execute(
            "SELECT request_id,kind,phase,data FROM connector_pending WHERE phase NOT IN ('complete','cancelled')"
        ).fetchall()
        matching = [
            (row, json.loads(row[3])) for row in rows if json.loads(row[3]).get("connection_id") == connection_id
        ]
        existing = next((row for row, _ in matching if row[1] == "forget"), None)
        if existing:
            return self.pending(existing[0])
        binding = self.binding(connection_id)
        if not matching and binding is None:
            return None
        credential_ids = {binding["credential_id"]} if binding else set()
        for _, data in matching:
            if data.get("credential_id"):
                credential_ids.add(data["credential_id"])
            credential_ids.update(data.get("cleanup_ids", []))
        pending_id = str(uuid.uuid4())
        data = {"connection_id": connection_id, "credential_ids": sorted(credential_ids), "binding_payload": None}
        with self.db:
            for row, _ in matching:
                self.db.execute("UPDATE connector_pending SET phase='cancelled' WHERE request_id=?", (row[0],))
            self.db.execute("DELETE FROM connector_bindings WHERE connection_id=?", (connection_id,))
            self.db.execute(
                "INSERT INTO connector_pending(request_id,kind,phase,data) VALUES (?,'forget','cleanup_pending',?)",
                (pending_id, json.dumps(data)),
            )
        return self.pending(pending_id)

    def binding(self, connection_id):
        request_id(connection_id)
        row = self.db.execute(
            "SELECT credential_id,provider_account_id,grant_revision FROM connector_bindings WHERE connection_id=?",
            (connection_id,),
        ).fetchone()
        return {"credential_id": row[0], "provider_account_id": row[1], "grant_revision": row[2]} if row else None

    def remove_binding(self, connection_id):
        with self.db:
            self.db.execute("DELETE FROM connector_bindings WHERE connection_id=?", (connection_id,))

    def require_settled(self, connection_id):
        if any(row["connection_id"] == connection_id for row in self.pending_metadata()):
            raise CompanionError(
                "connection_request_pending",
                "Resume, cancel, or forget the existing local request before starting another authorization for this connection.",
                3,
            )

    def pending_metadata(self):
        rows = self.db.execute(
            "SELECT request_id,kind,phase,data,created_at,updated_at FROM connector_pending WHERE phase NOT IN ('complete','cancelled') ORDER BY created_at,request_id"
        ).fetchall()
        return [
            {
                "request_id": row[0],
                "kind": row[1],
                "phase": row[2],
                "connection_id": json.loads(row[3]).get("connection_id"),
                "created_at": row[4],
                "updated_at": row[5],
            }
            for row in rows
        ]
