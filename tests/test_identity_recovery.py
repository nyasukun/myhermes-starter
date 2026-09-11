"""Same-home identity replacement acceptance using synthetic keys and local SQLite."""

import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from cryptography.hazmat.primitives.asymmetric import ec

from myhermes import cli, identity_recovery as recovery
from myhermes.errors import CompanionError, OfflineError
from myhermes.files import atomic_content, atomic_json, file_lock, snapshot
from myhermes.local_config import config_read, marker_read
from myhermes.state import State
from myhermes.skill_state import SkillState
from myhermes.connection_store import ConnectionStore
from myhermes.telemetry import Telemetry
from myhermes.telemetry_state import TelemetryState


class ReplacementPeer:
    def __init__(self, person):
        self.person = person
        self.installation = str(uuid.uuid4())
        self.keys = []
        self.requests = []
        self.pending = False
        self.offline = False
        self.me_person = None

    def enroll(self, candidate, directory, args):
        self.keys.append(candidate["key_id"])
        if self.pending:
            self.pending = False
            return {"status": "pending"}
        candidate.update(person_id=self.person, installation_id=self.installation)
        atomic_json(directory / "config.json", candidate)
        return {"status": "enrolled", "installation_id": self.installation}

    def api(self, candidate):
        self.requests.append(("identity", candidate["key_id"], candidate["installation_id"]))
        return self

    def request(self, method, path, payload=None):
        self.requests.append((method, path, payload))
        if self.offline:
            raise OfflineError()
        if (method, path, payload) != ("GET", "/v1/me", None):
            raise AssertionError("Recovery must not request or send user content")
        return 200, {"person_id": self.me_person or self.person, "installation_id": self.installation, "role": "member"}


class IdentityRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="myhermes-identity-")
        self.root = Path(self.temp.name).resolve()
        self.directory, self.home = self.root / "state", self.root / "home"
        self.directory.mkdir(mode=0o700)
        self.home.mkdir(mode=0o700)
        self.config = {
            "schema_version": "1",
            "server": "https://identity.example",
            "hermes_home": str(self.home),
            "upstream": str(self.root / "runtime"),
            "key_id": str(uuid.uuid4()),
            "os": "macos",
            "label": "fictional environment",
            "agent_instance_id": str(uuid.uuid4()),
            "allow_local_http": False,
            "installation_id": str(uuid.uuid4()),
            "person_id": str(uuid.uuid4()),
        }
        atomic_json(self.directory / "config.json", self.config)
        self.marker = {"key_id": self.config["key_id"], "state_directory": str(self.directory)}
        atomic_json(self.home / ".myhermes-installation.json", self.marker)
        atomic_content(self.home, "memories/MEMORY.md", "synthetic private local memory")
        self.state = State(self.directory)
        self.state.put("revision", 9)
        self.state.put("audit_fixture", {"unseen_remote": "kept"})
        self.state.queue([{"path": "SOUL.md", "content": "Synthetic unacknowledged update"}], base_revision=4)
        conflict = self.state.queue(
            [{"path": "memories/USER.md", "content": "Synthetic retained conflict"}], base_revision=3
        )
        self.state.mark(conflict["update_id"], "conflict", {"revision": 9, "conflict_paths": ["memories/USER.md"]})
        self.state.close()
        skills = SkillState(self.directory)
        skills.put("audit_fixture", {"pending": "private skill candidate"})
        with skills.db:
            for status in ("pending", "conflict"):
                update_id = str(uuid.uuid4())
                skills.db.execute(
                    "INSERT INTO skill_outbox(update_id,payload,status) VALUES (?,?,?)",
                    (
                        update_id,
                        json.dumps(
                            {
                                "schema_version": "1",
                                "update_id": update_id,
                                "base_revision": 2,
                                "skill_id": "retained-" + status,
                                "package": None,
                            }
                        ),
                        status,
                    ),
                )
        skills.close()
        connection = ConnectionStore(self.directory)
        with connection.db:
            connection.db.execute(
                "INSERT INTO connector_bindings VALUES (?,?,?,?)",
                (str(uuid.uuid4()), str(uuid.uuid4()), "12345", 1),
            )
            connection.db.execute(
                "INSERT INTO connector_pending(request_id,kind,phase,data) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), "authorize", "credential_pending", '{"synthetic":"old request"}'),
            )
        connection.close()
        telemetry = Telemetry(self.config, self.directory, None, ec.generate_private_key(ec.SECP256R1()))
        telemetry.record("runtime_start", {"outcome": "ok"})
        telemetry.close()
        self.original_files = snapshot(self.home)
        self.preserved = {name: (self.directory / name).read_bytes() for name in ("state.sqlite3", "skills.sqlite3")}
        self.peer = ReplacementPeer(self.config["person_id"])

    def tearDown(self):
        self.temp.cleanup()

    def args(self, *extra):
        return cli.parser().parse_args(["--state-dir", str(self.directory), "re-enroll", *extra])

    def run_recovery(self, *extra):
        with (
            patch.object(recovery, "_confirm"),
            patch.object(cli, "enroll", self.peer.enroll),
            patch.object(cli, "owner_api", self.peer.api),
        ):
            return cli.execute(self.args(*extra))

    def assert_original(self):
        self.assertEqual(config_read(self.directory), self.config)
        self.assertEqual(marker_read(self.home / ".myhermes-installation.json"), self.marker)
        self.assert_preserved()

    def assert_preserved(self):
        self.assertEqual(snapshot(self.home), self.original_files)
        for name, value in self.preserved.items():
            self.assertEqual((self.directory / name).read_bytes(), value)

    def assert_completed(self, result):
        self.assertEqual(result["status"], "completed")
        current = config_read(self.directory)
        self.assertEqual(current["person_id"], self.config["person_id"])
        self.assertEqual(current["installation_id"], self.peer.installation)
        self.assertNotEqual(current["key_id"], self.config["key_id"])
        self.assertEqual(marker_read(self.home / ".myhermes-installation.json")["key_id"], current["key_id"])
        self.assert_preserved()
        archive = self.directory / "identity-recovery" / result["operation_id"] / "archive"
        self.assertEqual(archive.stat().st_mode & 0o777, 0o700)
        for name in recovery.DATABASES:
            self.assertFalse((self.directory / name).exists())
            self.assertEqual((archive / name).stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(archive / "connections.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM connector_pending").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM connector_bindings").fetchone()[0], 1)
        with sqlite3.connect(archive / "telemetry.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM telemetry_outbox").fetchone()[0], 1)
            self.assertEqual(
                json.loads(
                    db.execute("SELECT value FROM telemetry_metadata WHERE key='installation_id'").fetchone()[0]
                ),
                self.config["installation_id"],
            )

    def test_dry_run_does_not_create_lock_key_or_network(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with (
            patch.object(recovery, "_confirm", side_effect=AssertionError),
            patch.object(cli, "enroll", side_effect=AssertionError),
            patch.object(cli, "owner_api", side_effect=AssertionError),
        ):
            result = cli.execute(self.args("--dry-run"))
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(
            before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        )

    def test_same_owner_preserves_content_and_archives_old_identity_queues(self):
        result = self.run_recovery()
        self.assert_completed(result)
        telemetry = TelemetryState(self.directory, self.peer.installation)
        self.assertEqual(telemetry.db.execute("SELECT COUNT(*) FROM telemetry_outbox").fetchone()[0], 0)
        telemetry.close()
        connection = ConnectionStore(self.directory)
        self.assertEqual(connection.db.execute("SELECT COUNT(*) FROM connector_pending").fetchone()[0], 0)
        self.assertEqual(connection.db.execute("SELECT COUNT(*) FROM connector_bindings").fetchone()[0], 0)
        connection.close()
        self.assertEqual([r for r in self.peer.requests if r[0] != "identity"], [("GET", "/v1/me", None)])

    def test_real_operation_requires_native_terminal_before_candidate(self):
        with patch("builtins.open", side_effect=OSError), self.assertRaises(CompanionError) as caught:
            cli.execute(self.args())
        self.assertEqual(caught.exception.code, "terminal_required")
        self.assertFalse((self.directory / recovery.JOURNAL).exists())
        self.assert_original()

    def test_pending_retry_keeps_one_candidate_key(self):
        self.peer.pending = True
        first = self.run_recovery()
        self.assertEqual(first["status"], "pending")
        self.assert_original()
        second = self.run_recovery()
        self.assert_completed(second)
        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertEqual(len(set(self.peer.keys)), 1)

    def test_wrong_owner_never_requests_owner_api_and_can_explicitly_restart(self):
        self.peer.person = str(uuid.uuid4())
        with self.assertRaises(CompanionError) as caught:
            self.run_recovery()
        self.assertEqual(caught.exception.code, "identity_owner_mismatch")
        self.assertEqual(self.peer.requests, [])
        self.assert_original()
        with self.assertRaises(CompanionError):
            self.run_recovery()
        with self.assertRaises(CompanionError):
            self.run_recovery("--new")
        cancelled = self.run_recovery("--cancel")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assert_original()
        old_key = self.peer.keys[0]
        self.peer.person = self.config["person_id"]
        result = self.run_recovery("--new")
        self.assert_completed(result)
        self.assertNotEqual(old_key, self.peer.keys[-1])

    def test_candidate_me_mismatch_and_offline_never_switch_or_archive(self):
        self.peer.me_person = str(uuid.uuid4())
        with self.assertRaises(CompanionError) as caught:
            self.run_recovery()
        self.assertEqual(caught.exception.code, "identity_owner_unverified")
        self.assert_original()
        self.peer.me_person = None
        self.peer.offline = True
        with self.assertRaises(OfflineError):
            self.run_recovery()
        self.assert_original()
        self.peer.offline = False
        self.assert_completed(self.run_recovery())
        self.assertEqual(len(set(self.peer.keys)), 1)

    def test_repeated_completed_receipt_requires_explicit_new(self):
        first = self.run_recovery()
        requests = copy.deepcopy(self.peer.requests)
        with patch.object(recovery, "_confirm", side_effect=AssertionError):
            self.assertEqual(self.run_recovery(), first)
        self.assertEqual(self.peer.requests, requests)

    def test_pending_gates_every_normal_command_before_network(self):
        self.peer.pending = True
        self.run_recovery()
        for command in (["inspect"], ["sync"], ["connections", "pending"], ["monitoring", "flush"], ["skills", "list"]):
            with (
                self.subTest(command=command),
                patch.object(cli, "owner_api", side_effect=AssertionError),
                self.assertRaises(CompanionError) as caught,
            ):
                cli.execute(cli.parser().parse_args(["--state-dir", str(self.directory), *command]))
            self.assertEqual(caught.exception.code, "identity_recovery_pending")

    def test_active_normal_commands_and_legacy_home_locks_prevent_replacement(self):
        with recovery.identity_command(self.directory):
            with recovery.identity_command(self.directory):
                pass  # Shared locks keep helper CLI concurrency available.
            with self.assertRaises(CompanionError) as caught:
                self.run_recovery()
            self.assertEqual(caught.exception.code, "identity_busy")
        with file_lock(self.home / ".myhermes-session.lock"), self.assertRaises(CompanionError) as caught:
            self.run_recovery()
        self.assertEqual(caught.exception.code, "home_busy")
        self.assertFalse((self.directory / recovery.JOURNAL).exists())

    def test_after_each_atomic_switch_boundary_retries_the_same_candidate(self):
        for boundary in ("archive", "marker", "config", "result", "completed"):
            with self.subTest(boundary=boundary):
                self.tearDown()
                self.setUp()
                original_atomic = recovery.atomic_json
                original_rename = recovery.os.rename
                injected = False

                def write(path, value):
                    nonlocal injected
                    original_atomic(path, value)
                    matches = (
                        boundary == "marker"
                        and path == self.home / ".myhermes-installation.json"
                        or boundary == "config"
                        and path == self.directory / "config.json"
                        or boundary == "result"
                        and path.name == "result.json"
                        or boundary == "completed"
                        and path == self.directory / recovery.JOURNAL
                        and value["stage"] == "completed"
                    )
                    if matches and not injected:
                        injected = True
                        raise OSError("synthetic crash")

                def rename(source, target):
                    nonlocal injected
                    original_rename(source, target)
                    if boundary == "archive" and not injected:
                        injected = True
                        raise OSError("synthetic crash")

                with (
                    patch.object(recovery, "atomic_json", write),
                    patch.object(recovery.os, "rename", rename),
                    self.assertRaises(OSError),
                ):
                    self.run_recovery()
                self.assertTrue(injected)
                self.assert_preserved()
                self.assert_completed(self.run_recovery())
                self.assertEqual(len(set(self.peer.keys)), 1)

    def test_unsafe_archive_inputs_and_tampered_journal_fail_closed(self):
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                name = self.directory / "connections.sqlite3-wal"
                if kind == "symlink":
                    name.symlink_to(self.directory / "state.sqlite3")
                elif kind == "hardlink":
                    os.link(self.directory / "state.sqlite3", name)
                else:
                    os.mkfifo(name)
                try:
                    with self.assertRaises(CompanionError):
                        self.run_recovery()
                    self.assert_original()
                finally:
                    name.unlink()
        atomic_json(self.directory / recovery.JOURNAL, {"stage": "completed", "unexpected": "synthetic"})
        with self.assertRaises(CompanionError):
            self.run_recovery()

    def test_uncheckpointable_live_writer_stops_before_archive(self):
        database = sqlite3.connect(self.directory / "connections.sqlite3")
        try:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("BEGIN IMMEDIATE")
            database.execute("UPDATE connector_bindings SET grant_revision=2")
            with self.assertRaises(CompanionError):
                self.run_recovery()
            self.assert_original()
        finally:
            database.rollback()
            database.close()
        self.assert_completed(self.run_recovery())

    def test_missing_candidate_code_has_a_safe_explicit_cancel_path(self):
        with (
            patch.object(recovery, "_confirm"),
            patch.object(cli, "enroll", side_effect=CompanionError("enrollment_code_missing", "fixture")),
            self.assertRaises(CompanionError) as caught,
        ):
            cli.execute(self.args())
        self.assertIn("--cancel", caught.exception.message)
        self.assertEqual(self.run_recovery("--cancel")["status"], "cancelled")
        self.assert_original()
        self.assert_completed(self.run_recovery("--new"))


if __name__ == "__main__":
    unittest.main()
