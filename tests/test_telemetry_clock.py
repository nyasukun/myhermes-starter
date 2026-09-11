"""Idle chain rotation must not depend on equal client/server wall clocks."""

import unittest

from myhermes.telemetry_state import DAY_NS
from telemetry_clock_scenario import SKEW_NS, scenario


class TelemetryClockTests(unittest.TestCase):
    def test_allowed_future_skew_then_clock_correction_does_not_reuse_expired_server_head(self):
        value = scenario()
        self.assertFalse(value["same_stream"])
        self.assertEqual(value["second"]["batch"]["first_sequence"], 1)
        self.assertIsNone(value["second"]["batch"]["previous_batch_sha256"])
        self.assertEqual(value["state"]["pending_events"], 1)
        self.assertEqual(value["state"]["sent_events"], 1)
        self.assertEqual(value["state"]["dropped_events"], 0)

    def test_delivered_idle_chain_rotates_at_30_days_including_legacy_timestamp_fallback(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                value = scenario(elapsed_ns=SKEW_NS + 30 * DAY_NS, legacy=legacy)
                self.assertFalse(value["same_stream"])
                self.assertEqual(value["state"]["dropped_events"], 0)
                retained = value["state"]["records"][1]
                self.assertEqual(retained["status"], "delivered")
                for field in ("batch", "audit_jws", "traces_base64", "batch_sha256"):
                    self.assertEqual(retained[field], value["first"][field])

    def test_idle_rotation_never_rewrites_a_still_valid_pending_or_partly_acknowledged_chain(self):
        for acknowledged in ((), ("audit",), ("traces",)):
            with self.subTest(acknowledged=acknowledged):
                value = scenario(elapsed_ns=SKEW_NS + 30 * DAY_NS, acknowledged=acknowledged)
                self.assertTrue(value["same_stream"])
                self.assertEqual(value["second"]["batch"]["first_sequence"], 2)
                self.assertEqual(value["second"]["batch"]["previous_batch_sha256"], value["first"]["batch_sha256"])
                self.assertEqual(value["state"]["pending_events"], 2)
                self.assertEqual(value["state"]["dropped_events"], 0)
                retained = value["state"]["records"][1]
                for field in ("batch", "audit_jws", "traces_base64", "batch_sha256"):
                    self.assertEqual(retained[field], value["first"][field])

    def test_delivered_recent_chain_continues_before_idle_threshold(self):
        value = scenario(elapsed_ns=SKEW_NS + 30 * DAY_NS - 1)
        self.assertTrue(value["same_stream"])
        self.assertEqual(value["second"]["batch"]["first_sequence"], 2)


if __name__ == "__main__":
    unittest.main()
