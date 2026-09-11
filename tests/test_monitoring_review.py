"""Independent regression cases from the monitoring privacy/retention review."""

import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from cryptography.hazmat.primitives.asymmetric import ec
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from myhermes.errors import CompanionError
from myhermes.telemetry import Telemetry
from myhermes.telemetry_state import DAY_NS
from test_telemetry import Peer


class MonitoringReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = time.time_ns()
        self.peer = Peer()
        self.telemetry = Telemetry(
            {"installation_id": str(uuid.uuid4())},
            Path(self.tmp.name).resolve(),
            self.peer,
            ec.generate_private_key(ec.SECP256R1()),
            clock_ns=lambda: self.now,
        )
        self.telemetry.record("sync", {"outcome": "ok"})

    def tearDown(self):
        self.telemetry.close()
        self.tmp.cleanup()

    def test_unsigned_corrupt_outbox_columns_cannot_escape_inspection(self):
        db = self.telemetry.state.db
        for column, value in (
            ("status", "PRIVATE_SENTINEL"),
            ("batch_sha256", "PRIVATE_SENTINEL"),
            ("batch_sha256", "0" * 64),
            ("audit_sent", 2),
            ("traces_sent", 2),
        ):
            with self.subTest(column=column, value=value):
                original = db.execute(f"SELECT {column} FROM telemetry_outbox").fetchone()[0]
                db.execute(f"UPDATE telemetry_outbox SET {column}=?", (value,))
                try:
                    for include_wire in (False, True):
                        with self.assertRaises(CompanionError):
                            self.telemetry.inspect(include_wire=include_wire)
                finally:
                    db.execute(f"UPDATE telemetry_outbox SET {column}=?", (original,))
        self.assertEqual(self.peer.calls, [])

    def test_unsigned_corrupt_metadata_cannot_escape_inspection(self):
        db = self.telemetry.state.db
        for key in ("stream_id", "sequence", "sent_total", "dropped_total", "dropped_pending"):
            with self.subTest(key=key):
                original = db.execute("SELECT value FROM telemetry_metadata WHERE key=?", (key,)).fetchone()[0]
                db.execute("UPDATE telemetry_metadata SET value=? WHERE key=?", (json.dumps("PRIVATE_SENTINEL"), key))
                try:
                    with self.assertRaises(CompanionError):
                        self.telemetry.inspect()
                finally:
                    db.execute("UPDATE telemetry_metadata SET value=? WHERE key=?", (original, key))
        self.assertEqual(self.peer.calls, [])

    def test_delivered_chain_restarts_after_server_retention_even_without_expired_pending_events(self):
        old_stream = self.telemetry.inspect()["stream_id"]
        self.assertFalse(self.telemetry.flush()["deferred"])
        self.now += 91 * DAY_NS
        self.telemetry.record("sync", {"outcome": "ok"})
        current = self.telemetry.inspect()["records"][0]["batch"]
        self.assertNotEqual(current["stream_id"], old_stream)
        self.assertEqual(current["first_sequence"], 1)
        self.assertIsNone(current["previous_batch_sha256"])
        self.assertFalse(self.telemetry.flush()["deferred"])
        self.assertEqual(self.telemetry.inspect()["dropped_events"], 0)

    def test_known_protobuf_extension_fields_cannot_leak_before_server_validation(self):
        db = self.telemetry.state.db
        original = db.execute("SELECT traces FROM telemetry_outbox").fetchone()[0]
        # Unknown-field stripping does not remove fields known by the pinned SDK.
        # Resource.entity_refs was already added to the actual 1.44 protobuf schema.
        changed = ExportTraceServiceRequest.FromString(original)
        changed.resource_spans[0].resource.entity_refs.add(schema_url="PRIVATE_SENTINEL")
        db.execute("UPDATE telemetry_outbox SET traces=?", (changed.SerializeToString(),))
        for include_wire in (False, True):
            with self.assertRaises(CompanionError):
                self.telemetry.inspect(include_wire=include_wire)
        with self.assertRaises(CompanionError):
            self.telemetry.flush()
        self.assertEqual(self.peer.calls, [])


if __name__ == "__main__":
    unittest.main()
