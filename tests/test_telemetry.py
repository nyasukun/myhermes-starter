"""Actual OpenTelemetry SDK and protobuf wire; only synthetic owner/device data."""

import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from cryptography.hazmat.primitives.asymmetric import ec
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from myhermes import __version__
from myhermes.errors import CompanionError
from myhermes.telemetry import AUDIT_PATH, TRACES_PATH, Telemetry
from myhermes.telemetry_contract import canonical, digest
from myhermes.telemetry_state import DAY_NS


class Reply(io.BytesIO):
    def __init__(self, data, content_type="application/json", status=200):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Type": content_type}


class Peer:
    server = "https://fixture.example"
    timeout = 1

    def __init__(self):
        self.opener = self
        self.calls = []
        self.seen = set()
        self.behavior = None
        self.proofs = 0

    def auth_headers(self, method, path):
        self.proofs += 1
        return {"Authorization": "DPoP synthetic", "DPoP": "proof-" + str(self.proofs)}

    def open(self, request, timeout):
        self.calls.append((request.full_url, request.data, dict(request.header_items())))
        if self.behavior:
            result = self.behavior(request)
            if result is not None:
                return result
        if request.full_url.endswith(AUDIT_PATH):
            encoded = request.data.decode().split(".")[1]
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            duplicate = payload["batch_id"] in self.seen
            self.seen.add(payload["batch_id"])
            return Reply(
                canonical(
                    {
                        "status": "duplicate" if duplicate else "accepted",
                        "accepted": 0 if duplicate else len(payload["events"]),
                        "batch_id": payload["batch_id"],
                        "batch_sha256": digest(payload),
                        "last_sequence": payload["events"][-1]["sequence"],
                    }
                ).encode()
            )
        if request.full_url.endswith(TRACES_PATH):
            return Reply(b"", "application/x-protobuf")
        raise AssertionError("Unexpected destination")


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name).resolve()
        self.config = {"installation_id": str(uuid.uuid4())}
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.peer = Peer()
        self.now = time.time_ns()
        self.telemetry = Telemetry(self.config, self.directory, self.peer, self.key, clock_ns=lambda: self.now)

    def tearDown(self):
        self.telemetry.close()
        self.tmp.cleanup()

    def test_actual_sdk_wire_and_signature_contain_only_the_published_metadata(self):
        event = self.telemetry.record(
            "model",
            {
                "outcome": "ok",
                "duration_ms": 8,
                "model_alias": "economy",
                "prompt_tokens": 12,
                "completion_tokens": 3,
                "request_id": str(uuid.uuid4()),
            },
        )
        inspected = self.telemetry.inspect(include_wire=True)
        item = inspected["records"][0]
        self.assertEqual(item["batch"]["events"][0], event)
        payload = item["audit_jws"].split(".")[1]
        self.assertEqual(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode(), canonical(item["batch"])
        )
        proto = ExportTraceServiceRequest.FromString(base64.b64decode(item["traces_base64"]))
        resource = proto.resource_spans[0]
        self.assertEqual(
            {entry.key: entry.value.string_value for entry in resource.resource.attributes},
            {"service.name": "myhermes", "service.version": __version__},
        )
        span = resource.scope_spans[0].spans[0]
        self.assertEqual(span.name, "myhermes.activity")
        self.assertEqual(span.trace_id.hex(), event["trace_id"])
        self.assertEqual(span.span_id.hex(), event["span_id"])
        self.assertFalse(span.events)
        self.assertFalse(span.links)
        self.assertEqual(span.status.message, "")
        self.assertEqual(span.end_time_unix_nano - span.start_time_unix_nano, 8_000_000)
        self.assertEqual((self.directory / "telemetry.sqlite3").stat().st_mode & 0o777, 0o600)
        result = self.telemetry.flush()
        self.assertFalse(result["deferred"])
        self.assertEqual(result["sent_now"], 1)
        self.assertEqual(result["pending_events"], 0)
        self.assertEqual(self.telemetry.inspect()["records"][0]["status"], "delivered")
        self.assertEqual([url for url, _, _ in self.peer.calls], [Peer.server + AUDIT_PATH, Peer.server + TRACES_PATH])

    def test_rejects_content_exceptions_unknown_fields_types_and_wrong_attribute_domains(self):
        for kind, attrs in [
            ("sync", {"outcome": "ok", "prompt": "SECRET"}),
            ("tool", {"outcome": "failed", "exception": "SECRET"}),
            ("sync", {"outcome": "ok", "cost_nano": 0}),
            ("model", {"outcome": "ok", "model_alias": "unapproved"}),
            ("sync", {"outcome": ["ok"]}),
            ("sync", {"outcome": "ok", "duration_ms": True}),
            ("tool", {"outcome": "ok", "tool_kind": "shell ls SECRET"}),
            ("free form SECRET", {"outcome": "ok"}),
        ]:
            with self.assertRaises(CompanionError):
                self.telemetry.record(kind, attrs)
        self.assertEqual(self.telemetry.inspect()["pending_events"], 0)
        self.assertNotIn(b"SECRET", (self.directory / "telemetry.sqlite3").read_bytes())

    def test_immutable_lost_audit_ack_retries_identical_signed_bytes_with_a_fresh_proof(self):
        self.telemetry.record("sync", {"outcome": "ok"})
        original = self.telemetry.inspect(include_wire=True)["records"][0]

        def lose(request):
            if request.full_url.endswith(AUDIT_PATH):
                self.peer.behavior = None
                self.peer.seen.add(original["batch"]["batch_id"])
                raise OSError("synthetic unavailable")

        self.peer.behavior = lose
        self.assertTrue(self.telemetry.flush()["deferred"])
        self.assertEqual(self.telemetry.inspect()["pending_events"], 1)
        self.telemetry.close()
        self.telemetry = Telemetry(self.config, self.directory, self.peer, self.key, clock_ns=lambda: self.now)
        self.assertFalse(self.telemetry.flush()["deferred"])
        self.assertEqual(self.peer.calls[0][1], self.peer.calls[1][1])
        self.assertNotEqual(self.peer.calls[0][2]["Dpop"], self.peer.calls[1][2]["Dpop"])

    def test_trace_ack_loss_does_not_resend_an_acknowledged_audit(self):
        self.telemetry.record("connection_change", {"outcome": "ok", "connection_id": str(uuid.uuid4())})
        failed = False

        def lose(request):
            nonlocal failed
            if request.full_url.endswith(TRACES_PATH) and not failed:
                failed = True
                raise OSError("synthetic timeout")

        self.peer.behavior = lose
        self.assertTrue(self.telemetry.flush()["deferred"])
        self.assertTrue(self.telemetry.inspect()["records"][0]["audit_sent"])
        self.assertFalse(self.telemetry.flush()["deferred"])
        self.assertEqual([url.rsplit("/", 1)[1] for url, _, _ in self.peer.calls], ["audit", "traces", "traces"])
        self.assertEqual(self.peer.calls[1][1], self.peer.calls[2][1])

    def test_invalid_ack_and_409_never_retire_the_outbox(self):
        self.telemetry.record("runtime_start", {"outcome": "ok"})
        for response in [Reply(b"{}"), Reply(b"{}", status=409), Reply(b"secret body", status=503)]:
            self.peer.behavior = lambda request, response=response: response
            self.assertTrue(self.telemetry.flush()["deferred"])
            self.assertEqual(self.telemetry.inspect()["pending_events"], 1)
        self.peer.behavior = lambda request: (
            Reply(b"partial_success", "application/x-protobuf") if request.full_url.endswith(TRACES_PATH) else None
        )
        self.assertTrue(self.telemetry.flush()["deferred"])
        self.assertEqual(self.telemetry.inspect()["pending_events"], 1)

    def test_concurrent_metadata_events_keep_unique_contiguous_sequence_and_hash_chain(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            events = list(
                pool.map(lambda _: self.telemetry.record("tool", {"outcome": "ok", "tool_kind": "read"}), range(30))
            )
        self.assertEqual(sorted(event["sequence"] for event in events), list(range(1, 31)))
        records = list(reversed(self.telemetry.inspect()["records"]))
        self.assertIsNone(records[0]["batch"]["previous_batch_sha256"])
        for previous, current in zip(records, records[1:]):
            self.assertEqual(current["batch"]["previous_batch_sha256"], previous["batch_sha256"])

    def test_queue_full_preserves_chain_and_reports_dropped_new_events_after_room_frees(self):
        with patch("myhermes.telemetry_state.MAX_QUEUE_EVENTS", 2):
            a = self.telemetry.record("sync", {"outcome": "ok"})
            self.telemetry.record("sync", {"outcome": "ok"})
            self.assertIsNone(self.telemetry.record("sync", {"outcome": "ok"}))
            self.assertEqual(self.telemetry.inspect()["dropped_events"], 1)
            self.assertFalse(self.telemetry.flush()["deferred"])
        inspected = self.telemetry.inspect()
        self.assertEqual(inspected["sequence"], 3)
        self.assertEqual(inspected["pending_events"], 0)
        self.assertEqual(inspected["unreported_dropped_events"], 0)
        delivery = next(row for row in inspected["records"] if row["batch"]["events"][0]["kind"] == "delivery")
        self.assertEqual(delivery["batch"]["events"][0]["attributes"]["dropped_count"], 1)
        self.assertEqual(a["sequence"], 1)

    def test_expiry_rotates_whole_pending_chain_and_retains_inspectable_drop_history(self):
        self.telemetry.record("sync", {"outcome": "ok"})
        old_stream = self.telemetry.inspect()["stream_id"]
        self.now += 31 * DAY_NS
        new = self.telemetry.record("sync", {"outcome": "ok"})
        self.assertEqual(new["sequence"], 1)
        inspected = self.telemetry.inspect()
        self.assertNotEqual(inspected["stream_id"], old_stream)
        self.assertEqual(inspected["dropped_events"], 1)
        self.assertEqual({row["status"] for row in inspected["records"]}, {"pending", "expired"})
        self.assertFalse(self.telemetry.flush()["deferred"])
        self.assertEqual(self.telemetry.inspect()["pending_events"], 0)

    def test_tampered_signed_or_protobuf_bytes_never_leave_the_host_or_inspection(self):
        self.telemetry.record("sync", {"outcome": "ok"})
        self.telemetry.state.db.execute("UPDATE telemetry_outbox SET traces=?", (b"SECRET",))
        with self.assertRaises(CompanionError):
            self.telemetry.flush()
        with self.assertRaises(CompanionError):
            self.telemetry.inspect(include_wire=True)
        self.assertEqual(self.peer.calls, [])

    def test_environment_resource_attributes_and_global_current_span_are_not_collected(self):
        self.telemetry.close()
        with patch.dict(
            os.environ,
            {
                "OTEL_RESOURCE_ATTRIBUTES": "secret=SECRET,service.name=evil",
                "OTEL_SERVICE_NAME": "SECRET",
                "OTEL_TRACES_SAMPLER": "always_off",
            },
        ):
            self.telemetry = Telemetry(self.config, self.directory, self.peer, self.key, clock_ns=lambda: self.now)
            self.telemetry.record("sync", {"outcome": "ok"})
        self.assertNotIn("SECRET", canonical(self.telemetry.inspect(include_wire=True)))

    def test_database_symlink_and_hardlink_are_rejected(self):
        self.telemetry.close()
        state = self.directory / "telemetry.sqlite3"
        backup = self.directory / "original.sqlite3"
        state.rename(backup)
        state.symlink_to(backup)
        with self.assertRaises(CompanionError):
            Telemetry(self.config, self.directory, self.peer, self.key)
        state.unlink()
        os.link(backup, state)
        with self.assertRaises(CompanionError):
            Telemetry(self.config, self.directory, self.peer, self.key)
        state.unlink()
        backup.rename(state)
        self.telemetry = Telemetry(self.config, self.directory, self.peer, self.key)


if __name__ == "__main__":
    unittest.main()
