import argparse
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from myhermes.cli import execute, parser
from myhermes.errors import CompanionError
from myhermes.telemetry_cli import CommandMonitoring, command_kind, manifest, monitoring_command
from myhermes.telemetry_contract import ATTRIBUTE_FIELDS, KINDS, MANIFEST_VERSION


class TelemetryCliTests(unittest.TestCase):
    def test_offline_manifest_needs_no_setup_identity_key_or_network(self):
        args = parser().parse_args(["--state-dir", "/nonexistent/myhermes", "monitoring", "manifest"])
        with patch("myhermes.cli.owner_api", side_effect=AssertionError("No API expected")):
            result = execute(args)
        self.assertEqual(result["status"], "local")
        self.assertEqual(result["manifest"]["version"], MANIFEST_VERSION)
        self.assertEqual(set(result["manifest"]["events"]), KINDS)
        self.assertEqual(
            set(result["manifest"]["required_attributes"] + result["manifest"]["optional_attributes"]), ATTRIBUTE_FIELDS
        )

    def test_readonly_and_dry_run_commands_do_not_collect_or_transmit_events(self):
        for command in [["sync", "--dry-run"], ["inspect"], ["connections", "list"], ["skills", "history"]]:
            args = parser().parse_args(command)
            with (
                patch("myhermes.cli._execute", return_value={"status": "fixture"}),
                patch("myhermes.cli.CommandMonitoring", side_effect=AssertionError("No telemetry expected")),
            ):
                self.assertEqual(execute(args), {"status": "fixture"})

    def test_mutations_report_fixed_kind_and_never_operation_arguments_or_result_contents(self):
        args = parser().parse_args(
            [
                "connections",
                "read",
                str(uuid.uuid4()),
                "--resource",
                "SECRET/private",
                "--operation",
                "issues.read",
                "--include-content",
            ]
        )
        captured = []
        monitoring = Mock()
        monitoring.record.side_effect = lambda kind, attrs: captured.append((kind, attrs))
        monitoring.finish.return_value = {"status": "queued"}
        with (
            patch("myhermes.cli.CommandMonitoring", return_value=monitoring),
            patch("myhermes.cli._execute", return_value={"status": "ok", "result": {"body": "SECRET private body"}}),
        ):
            result = execute(args)
        self.assertEqual(captured[0][0], "tool")
        self.assertEqual(captured[0][1]["tool_kind"], "connector")
        self.assertNotIn("SECRET", str(captured))
        self.assertEqual(result["result"]["body"], "SECRET private body")

    def test_failed_operation_uses_only_fixed_outcome_and_preserves_original_exception(self):
        args = parser().parse_args(["sync"])
        monitoring = Mock()
        error = CompanionError("offline", "Synthetic private context stays in original error")
        with (
            patch("myhermes.cli.CommandMonitoring", return_value=monitoring),
            patch("myhermes.cli._execute", side_effect=error),
        ):
            with self.assertRaises(CompanionError) as caught:
                execute(args)
        self.assertIs(caught.exception, error)
        kind, attrs = monitoring.record.call_args.args
        self.assertEqual(kind, "sync")
        self.assertEqual(attrs["outcome"], "failed")
        self.assertNotIn("private", str(attrs))

    def test_monitoring_failure_never_blocks_operation_and_diagnostic_has_no_exception_data(self):
        monitor = CommandMonitoring(
            Path("/fixture"),
            lambda _: {"installation_id": str(uuid.uuid4())},
            lambda _: (_ for _ in ()).throw(RuntimeError("SECRET exception text")),
        )
        output = io.StringIO()
        with redirect_stderr(output):
            monitor.record("sync", {"outcome": "ok"})
            status = monitor.finish()
        self.assertEqual(status["status"], "deferred")
        self.assertNotIn("SECRET", output.getvalue())

    def test_remote_manifest_mismatch_is_not_blindly_printed(self):
        api = Mock()
        api.request.return_value = (200, {"secret": "SECRET"})
        args = argparse.Namespace(monitoring_command="manifest", remote=True)
        with self.assertRaises(CompanionError) as caught:
            monitoring_command(args, {}, Path("/fixture"), owner_api=lambda _: api)
        self.assertNotIn("SECRET", str(caught.exception))

    def test_export_uses_new_owner_local_file_and_contains_only_explicit_safe_wire(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(monitoring_command="export", export_dir=Path(tmp).resolve() / "export", limit=100)
            api = Mock()
            t = Mock()
            t.inspect.return_value = {
                "manifest_version": MANIFEST_VERSION,
                "records": [{"audit_jws": "synthetic-safe-signed-metadata"}],
            }
            with patch("myhermes.telemetry_cli.Telemetry", return_value=t):
                result = monitoring_command(
                    args, {"installation_id": str(uuid.uuid4())}, Path(tmp).resolve(), owner_api=lambda _: api
                )
            target = args.export_dir / "monitoring-records.json"
            self.assertTrue(target.exists())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result["records"], 1)
            t.close.assert_called_once()

    def test_all_required_lifecycle_commands_have_explicit_kind_mapping(self):
        for command, expected in [
            ("sync", "sync"),
            ("enroll", "enrollment"),
            ("upgrade", "runtime_update"),
            ("install-runtime", "runtime_update"),
        ]:
            self.assertEqual(command_kind(parser().parse_args([command])), expected)
        self.assertEqual(manifest()["transport"]["authentication"], "installation-bound DPoP")


if __name__ == "__main__":
    unittest.main()
