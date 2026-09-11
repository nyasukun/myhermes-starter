"""Corrupted local metadata must not turn default CLI output into a content export."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from myhermes.cli import config_read, main
from myhermes.files import atomic_json
from myhermes.errors import CompanionError
from myhermes.output_metadata import metadata_page
from myhermes.state import State


class LocalConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / "state"
        self.home = self.root / "home"
        self.argv = ["--state-dir", str(self.directory)]
        code, _ = self.command(
            "setup",
            "--server",
            "https://fixture.invalid",
            "--hermes-home",
            str(self.home),
            "--upstream",
            str(self.root / "upstream"),
        )
        self.assertEqual(code, 0)
        self.config = json.loads((self.directory / "config.json").read_text())

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([*self.argv, *args])
        self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", output.getvalue())
        return code, json.loads(output.getvalue())

    def test_nested_config_values_and_unknown_fields_fail_without_output(self):
        for field in ("os", "installation_id", "label", "allow_local_http", "person_id", "extra"):
            with self.subTest(field=field):
                atomic_json(
                    self.directory / "config.json", {**self.config, field: {"body": "SYNTHETIC_PRIVATE_CANARY"}}
                )
                code, value = self.command("inspect")
                self.assertEqual(code, 3)
                self.assertEqual(value["error"], "setup_required")

    def test_config_schema_and_boolean_are_exact_but_legacy_defaults_remain(self):
        for field, bad in (("schema_version", 1), ("allow_local_http", 1), ("os", "windows"), ("key_id", "invalid")):
            with self.subTest(field=field):
                atomic_json(self.directory / "config.json", {**self.config, field: bad})
                self.assertEqual(self.command("inspect")[0], 3)
        legacy = {
            key: value
            for key, value in self.config.items()
            if key not in ("schema_version", "label", "agent_instance_id", "allow_local_http")
        }
        atomic_json(self.directory / "config.json", legacy)
        self.assertEqual(self.command("inspect")[0], 0)
        self.assertFalse(config_read(self.directory)["allow_local_http"])

    def test_marker_unknown_content_is_rejected(self):
        marker = self.home / ".myhermes-installation.json"
        value = json.loads(marker.read_text())
        atomic_json(marker, {**value, "extra": "SYNTHETIC_PRIVATE_CANARY"})
        code, result = self.command("inspect")
        self.assertEqual(code, 3)
        self.assertEqual(result["error"], "home_binding_mismatch")

    def test_config_and_marker_special_files_do_not_block_or_follow_links(self):
        for path in (self.directory / "config.json", self.home / ".myhermes-installation.json"):
            raw = path.read_bytes()
            for kind in ("fifo", "hardlink", "oversized", "duplicate"):
                with self.subTest(path=path.name, kind=kind):
                    path.unlink()
                    external = self.root / "external"
                    if kind == "fifo":
                        os.mkfifo(path)
                    elif kind == "hardlink":
                        external.write_bytes(raw)
                        os.link(external, path)
                    elif kind == "oversized":
                        path.write_bytes(b" " * 65537)
                    else:
                        path.write_text('{"os":"macos","os":{"body":"SYNTHETIC_PRIVATE_CANARY"}}')
                    process = subprocess.run(
                        [sys.executable, "-m", "myhermes", *self.argv, "inspect"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=5,
                    )
                    self.assertNotEqual(process.returncode, 0)
                    self.assertNotIn(b"SYNTHETIC_PRIVATE_CANARY", process.stdout + process.stderr)
                    path.unlink()
                    path.write_bytes(raw)
                    external.unlink(missing_ok=True)

    def test_corrupt_local_revision_never_enters_default_output(self):
        state = State(self.directory)
        state.put("revision", {"body": "SYNTHETIC_PRIVATE_CANARY"})
        state.close()
        code, result = self.command("inspect")
        self.assertNotEqual(code, 0)
        self.assertEqual(result["error"], "state_rejected")

    def test_history_cli_rejects_content_hidden_in_a_metadata_value(self):
        row = {
            "revision": 1,
            "update_id": str(uuid.uuid4()),
            "installation_id": None,
            "created_at": "2026-09-11T00:00:00.000Z",
            "paths": ["SOUL.md"],
        }
        with patch("myhermes.cli.owner_api") as api:
            api.return_value.request.return_value = (200, {"history": [row], "next_cursor": None})
            self.assertEqual(self.command("history")[0], 0)
            for field in row:
                with self.subTest(field=field):
                    api.return_value.request.return_value = (
                        200,
                        {
                            "history": [{**row, field: {"body": "SYNTHETIC_PRIVATE_CANARY"}}],
                            "next_cursor": None,
                        },
                    )
                    code, result = self.command("history")
                    self.assertNotEqual(code, 0)
                    self.assertEqual(result["error"], "schema_rejected")

    def test_all_history_and_conflict_projections_validate_each_value(self):
        base = {
            "revision": 1,
            "update_id": str(uuid.uuid4()),
            "installation_id": None,
            "created_at": "2026-09-11T00:00:00.000Z",
        }
        pages = {
            "persona_history": {**base, "paths": ["SOUL.md"]},
            "persona_conflicts": {**base, "base_revision": 0, "conflict_paths": ["SOUL.md"], "resolved_by": None},
            "skill_history": {**base, "skill_id": "example", "version": "1.0.0", "sha256": "a" * 64},
            "skill_conflicts": {**base, "skill_id": "example", "base_revision": 0, "resolved_by": None},
        }
        for kind, row in pages.items():
            collection = "history" if kind.endswith("history") else "conflicts"
            valid = metadata_page({collection: [row], "next_cursor": None}, kind)
            for field in valid[collection][0]:
                with self.subTest(kind=kind, field=field):
                    with self.assertRaises(CompanionError) as caught:
                        metadata_page({collection: [{**row, field: {"body": "SYNTHETIC_PRIVATE_CANARY"}}]}, kind)
                    self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", str(caught.exception))
            with self.assertRaises(CompanionError):
                metadata_page({collection: [row], "next_cursor": {"body": "SYNTHETIC_PRIVATE_CANARY"}}, kind)


if __name__ == "__main__":
    unittest.main()
