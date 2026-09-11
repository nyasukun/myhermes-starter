"""Applied reports describe complete durable snapshots, independently of content delivery."""

import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from myhermes.errors import OfflineError
from myhermes.files import LIMITS, snapshot
from myhermes.state import State
from myhermes.sync import Synchronizer
from myhermes.sync_ack import SyncAcknowledgements, inspect_ack, manifest
from test_companion import FakeAPI


class AckPeer:
    def __init__(self):
        self.installation_id = str(uuid.uuid4())
        self.requests = []
        self.report = None
        self.offline = False
        self.lost = False
        self.override = None

    def request(self, method, path, payload):
        self.requests.append((method, path, payload))
        if self.offline:
            raise OfflineError()
        if self.override:
            return self.override
        duplicate = self.report == payload["revision"]
        self.report = payload["revision"]
        if self.lost:
            self.lost = False
            raise OfflineError()
        return 200, {
            "status": "duplicate" if duplicate else "accepted",
            "source": "client_reported",
            "applied_revision": self.report,
            "received_at": "2026-09-11T00:00:00.000Z",
        }


class SyncAckAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = State(self.root / "state")
        self.peer = AckPeer()

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def apply(self, revision=1):
        current = snapshot(self.home)
        target = {path: None for path in LIMITS}
        if revision:
            target["SOUL.md"] = "SYNTHETIC_PRIVATE_PERSONA"
        self.state.journal_apply(self.home, current, target, revision, target)

    def test_complete_snapshot_including_zero_reports_only_fixed_metadata(self):
        for revision in (0, 1):
            self.apply(revision)
            self.assertEqual(self.state.get("sync_ack_pending"), revision)
            result = SyncAcknowledgements(self.state, self.peer).flush()
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(
                self.peer.requests[-1], ("POST", "/v1/sync/ack", {"schema_version": "1", "revision": revision})
            )
            self.assertNotIn("SYNTHETIC_PRIVATE_PERSONA", json.dumps(self.peer.requests))
            self.assertIsNone(self.state.get("sync_ack_pending"))

    def test_lost_ack_survives_sqlite_restart_and_reuses_revision(self):
        self.apply()
        self.peer.lost = True
        self.assertEqual(SyncAcknowledgements(self.state, self.peer).flush()["status"], "deferred")
        self.state.close()
        self.state = State(self.root / "state")
        self.assertEqual(SyncAcknowledgements(self.state, self.peer).flush()["status"], "duplicate")
        self.assertEqual(self.peer.requests[0], self.peer.requests[1])

    def test_partial_apply_and_pending_or_conflict_never_report_new_baseline(self):
        current = snapshot(self.home)
        target = {**current, "SOUL.md": "Preserved local edit"}
        remote = {**current, "SOUL.md": "Remote snapshot"}
        self.state.journal_apply(self.home, current, target, 1, remote)
        self.assertIsNone(self.state.get("sync_ack_pending"))
        update = self.state.queue([{"path": "SOUL.md", "content": "Pending local"}])
        for status in ("pending", "conflict"):
            self.state.mark(update["update_id"], status)
            self.state.journal_apply(self.home, snapshot(self.home), remote, 1, remote)
            self.assertIsNone(self.state.get("sync_ack_pending"))
        self.state.mark(update["update_id"], "resolved")
        self.state.journal_apply(self.home, remote, remote, 1, remote)
        self.assertEqual(self.state.get("sync_ack_pending"), 1)

    def test_failed_pull_or_interrupted_file_apply_cannot_ack(self):
        api = FakeAPI()
        api.before_error = True
        with self.assertRaises(OfflineError):
            Synchronizer(self.state, self.home, api).run()
        self.assertIsNone(self.state.get("sync_ack_pending"))
        with patch("myhermes.state.atomic_content", side_effect=OSError("synthetic crash")):
            with self.assertRaises(OSError):
                self.apply()
        self.assertIsNone(self.state.get("sync_ack_pending"))
        self.state.close()
        self.state = State(self.root / "state")
        self.state.recover(self.home)
        self.assertEqual(self.state.get("sync_ack_pending"), 1)

    def test_stale_and_future_report_retire_without_adopting_server_revision(self):
        for response in (
            {
                "error": "sync_ack_regression",
                "source": "client_reported",
                "applied_revision": 2,
                "received_at": "2026-09-11T00:00:00.000Z",
            },
            {"error": "sync_ack_future_revision"},
        ):
            self.apply()
            self.peer.override = (409, response)
            result = SyncAcknowledgements(self.state, self.peer).flush()
            self.assertIn(result["status"], ("superseded", "rejected"))
            self.assertEqual(self.state.revision, 1)
            self.assertEqual((self.home / "SOUL.md").read_text(), "SYNTHETIC_PRIVATE_PERSONA")
            self.assertIsNone(self.state.get("sync_ack_pending"))
            self.assertEqual(SyncAcknowledgements(self.state, self.peer).flush()["status"], "none_pending")

    def test_invalid_metadata_or_network_failure_preserves_pending_and_sanitizes_output(self):
        self.apply()
        self.peer.offline = True
        self.assertEqual(
            SyncAcknowledgements(self.state, self.peer).flush(), {"status": "deferred", "error": "offline"}
        )
        self.peer.offline = False
        self.peer.override = (
            200,
            {
                "status": "accepted",
                "source": "client_reported",
                "applied_revision": {"body": "SYNTHETIC_PRIVATE_PERSONA"},
                "received_at": "2026-09-11T00:00:00.000Z",
            },
        )
        result = SyncAcknowledgements(self.state, self.peer).flush()
        self.assertEqual(result, {"status": "deferred", "error": "sync_ack_invalid"})
        self.assertEqual(self.state.get("sync_ack_pending"), 1)

    def test_older_delivery_does_not_clear_newer_report(self):
        self.apply()
        request = self.peer.request

        def advancing(method, path, payload):
            result = request(method, path, payload)
            self.apply(2)
            return result

        self.peer.request = advancing
        self.assertEqual(SyncAcknowledgements(self.state, self.peer).flush()["applied_revision"], 1)
        self.assertEqual(self.state.get("sync_ack_pending"), 2)

    def test_inspection_validates_receipts_and_separates_reenrolled_installation(self):
        self.apply()
        SyncAcknowledgements(self.state, self.peer).flush()
        self.assertEqual(inspect_ack(self.state, self.peer.installation_id)["last_receipt"]["applied_revision"], 1)
        self.assertIsNone(inspect_ack(self.state, str(uuid.uuid4()))["last_receipt"])
        self.state.put(
            "sync_ack_receipt",
            {"installation_id": self.peer.installation_id, "receipt": {"body": "SYNTHETIC_PRIVATE_PERSONA"}},
        )
        result = inspect_ack(self.state, self.peer.installation_id)
        self.assertEqual(result, {"status": "invalid", "error": "sync_ack_invalid"})

    def test_packaged_manifest_matches_public_artifact(self):
        artifact = Path(__file__).resolve().parents[1] / "monitoring/sync-status-manifest.v1.json"
        self.assertEqual(manifest(), json.loads(artifact.read_text()))


if __name__ == "__main__":
    unittest.main()
