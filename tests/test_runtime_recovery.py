"""Repair/rollback acceptance: real files/SQLite and synthetic dependency failures."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import uuid

from myhermes import runtime
from myhermes.cli import main
from myhermes.errors import CompanionError
from myhermes.files import LIMITS, atomic_content, atomic_json, file_lock, snapshot
from myhermes.state import State


class RuntimeRecoveryAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="myhermes-recovery-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(os.path.realpath(self.temporary.name))
        self.home = self.root / "home"
        self.home.mkdir()
        atomic_content(self.home, "SOUL.md", "Synthetic original personality")
        atomic_content(self.home, "memories/MEMORY.md", "Synthetic original memory")
        self.state = State(self.root / "state")
        self.addCleanup(self.state.close)
        self.state.put("revision", 4)
        self.state.put("baseline", snapshot(self.home))
        self.upstream = self.root / "upstream"

    def legacy_backup(self, value=None):
        identifier = str(uuid.uuid4())
        path = self.state.directory / "backups" / (identifier + ".json")
        atomic_json(path, value or {"revision": 1, "files": snapshot(self.home)})
        return identifier, path

    def test_failed_repair_keeps_discoverable_id_without_body_or_subprocess_output(self):
        with patch.object(
            runtime, "install_runtime", side_effect=CompanionError("runtime_command_failed", "SECRET stderr", 3)
        ):
            with self.assertRaises(CompanionError) as raised:
                runtime.upgrade_runtime(self.state, self.home, self.upstream, "synthetic-python")
        identifier = re.search(r"[0-9a-f-]{36}", raised.exception.message).group()
        self.assertNotIn("SECRET", raised.exception.message)
        listing = runtime.list_personality_backups(self.state)
        self.assertEqual(listing["backups"][0]["backup_id"], identifier)
        self.assertEqual(listing["backups"][0]["purpose"], "upgrade")
        self.assertNotIn("Synthetic original", json.dumps(listing))
        self.assertEqual(raised.exception.exit_code, 3)

    def test_restore_preserves_revision_pending_conflict_and_unmanaged_files_with_safety_backup(self):
        identifier = runtime.backup_personality(self.state, self.home)
        before = runtime.read_personality_backup(self.state, identifier)["files"]
        atomic_content(self.home, "SOUL.md", "Newer local personality")
        atomic_content(self.home, "memories/USER.md", "Newer local user")
        current = snapshot(self.home)
        pending = self.state.queue([{"path": "SOUL.md", "content": "Immutable pending candidate"}])
        conflict = self.state.queue([{"path": "SOUL.md", "content": "Immutable conflict"}])
        self.state.mark(conflict["update_id"], "conflict")
        self.state.put("revision", 9)
        baseline = self.state.baseline
        for relative in ("config.yaml", "sessions.sqlite", ".env", "skills/local/SKILL.md"):
            target = self.home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"Unmanaged synthetic owner bytes")
        result = runtime.restore_personality(self.state, self.home, identifier)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(self.state.revision, 9)
        self.assertEqual(self.state.baseline, baseline)
        self.assertEqual(self.state.items("pending"), [pending])
        self.assertEqual(self.state.items("conflict"), [conflict])
        self.assertEqual(runtime.read_personality_backup(self.state, result["safety_backup_id"])["files"], current)
        for relative in ("config.yaml", "sessions.sqlite", ".env", "skills/local/SKILL.md"):
            self.assertEqual((self.home / relative).read_bytes(), b"Unmanaged synthetic owner bytes")
        for path in (self.state.directory / "backups").iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("Unmanaged synthetic owner bytes", path.read_text())

    def test_backup_recovers_pending_apply_under_memory_locks_before_snapshot(self):
        current = snapshot(self.home)
        target = {**current, "SOUL.md": "Committed but interrupted target"}
        self.state.put("apply_journal", {"expected": current, "target": target, "baseline": target, "revision": 5})
        identifier = runtime.backup_personality(self.state, self.home)
        self.assertEqual(self.state.revision, 5)
        self.assertEqual(runtime.read_personality_backup(self.state, identifier)["files"], target)
        self.assertIsNone(self.state.get("apply_journal"))
        with file_lock(self.home / "memories/MEMORY.md.lock"):
            with self.assertRaises(CompanionError) as raised:
                runtime.backup_personality(self.state, self.home)
        self.assertEqual(raised.exception.code, "home_busy")

    def test_intervening_edit_blocks_upgrade_before_backup_or_dependency_execution(self):
        current = snapshot(self.home)
        target = {**current, "SOUL.md": "Interrupted target"}
        self.state.put("apply_journal", {"expected": current, "target": target, "baseline": target, "revision": 5})
        atomic_content(self.home, "SOUL.md", "Intervening owner edit")
        with patch.object(runtime, "install_runtime") as install:
            with self.assertRaises(CompanionError) as raised:
                runtime.upgrade_runtime(self.state, self.home, self.upstream, "synthetic-python")
        self.assertEqual(raised.exception.code, "recovery_conflict")
        install.assert_not_called()
        self.assertEqual(snapshot(self.home)["SOUL.md"], "Intervening owner edit")
        self.assertFalse((self.state.directory / "backups").exists())

    def test_interrupted_restore_remains_recoverable_and_safety_backup_discoverable(self):
        identifier = runtime.backup_personality(self.state, self.home)
        target = snapshot(self.home)
        atomic_content(self.home, "SOUL.md", "Newer soul")
        atomic_content(self.home, "memories/MEMORY.md", "Newer memory")
        current = snapshot(self.home)
        actual = atomic_content
        writes = 0

        def crash_after_write(home, path, content):
            nonlocal writes
            actual(home, path, content)
            writes += 1
            if writes == 1:
                raise OSError("Synthetic process interruption")

        with patch("myhermes.state.atomic_content", side_effect=crash_after_write):
            with self.assertRaises(OSError):
                runtime.restore_personality(self.state, self.home, identifier)
        self.assertIsNotNone(self.state.get("apply_journal"))
        safety = next(
            item for item in runtime.list_personality_backups(self.state)["backups"] if item["purpose"] == "pre_restore"
        )
        self.assertEqual(runtime.read_personality_backup(self.state, safety["backup_id"])["files"], current)
        self.state.recover(self.home)
        self.assertEqual(snapshot(self.home), target)
        self.assertEqual(self.state.revision, 4)

    def test_dry_run_restore_and_upgrade_do_not_create_backups_or_mutate_files(self):
        identifier = runtime.backup_personality(self.state, self.home)
        atomic_content(self.home, "SOUL.md", "Current local")
        files = snapshot(self.home)
        backups = set((self.state.directory / "backups").iterdir())
        self.assertEqual(
            runtime.restore_personality(self.state, self.home, identifier, dry_run=True)["status"], "dry_run"
        )
        with patch.object(runtime, "install_runtime", return_value={"status": "dry_run"}) as install:
            runtime.upgrade_runtime(self.state, self.home, self.upstream, "fixture", dry_run=True)
        install.assert_called_once_with(self.upstream, "fixture", dry_run=True)
        self.assertEqual(snapshot(self.home), files)
        self.assertEqual(set((self.state.directory / "backups").iterdir()), backups)

    def test_legacy_personality_and_interrupted_recovery_backups_remain_restorable(self):
        for extra in ({}, {"interrupted_apply": {"synthetic": "legacy recovery metadata"}}):
            identifier, _ = self.legacy_backup({"revision": 1, "files": snapshot(self.home), **extra})
            self.assertEqual(
                runtime.restore_personality(self.state, self.home, identifier, dry_run=True)["status"], "dry_run"
            )

    def test_malformed_or_unbounded_backup_rejected_without_mutation(self):
        original = snapshot(self.home)
        valid = {"revision": 1, "files": original}
        invalids = [
            [],
            {"revision": True, "files": original},
            {"revision": -1, "files": original},
            {**valid, "secret": "Unexpected field"},
            {"revision": 1, "files": {}},
            {"revision": 1, "files": {**original, "SOUL.md": 3}},
            {"revision": 1, "files": {**original, "SOUL.md": "a" * (LIMITS["SOUL.md"] + 1)}},
        ]
        for value in invalids:
            identifier, path = self.legacy_backup(valid)
            path.write_text(json.dumps(value))
            with self.subTest(type=type(value)):
                with self.assertRaises(CompanionError) as raised:
                    runtime.restore_personality(self.state, self.home, identifier)
                self.assertEqual(raised.exception.code, "backup_rejected")
        identifier, path = self.legacy_backup(valid)
        path.write_bytes(b" " * 4_194_305)
        with self.assertRaises(CompanionError):
            runtime.read_personality_backup(self.state, identifier)
        self.assertEqual(snapshot(self.home), original)
        for invalid in ("../outside", "not-a-uuid", "{" + identifier + "}"):
            with self.assertRaises(CompanionError):
                runtime.read_personality_backup(self.state, invalid)

    def test_backup_symlink_hardlink_and_fifo_rejected_without_blocking(self):
        for kind in ("symlink", "hardlink", "fifo"):
            identifier, path = self.legacy_backup()
            source = self.root / (kind + "-source")
            path.rename(source)
            if kind == "symlink":
                path.symlink_to(source)
            elif kind == "hardlink":
                os.link(source, path)
            else:
                os.mkfifo(path)
            with self.subTest(kind=kind):
                with self.assertRaises(CompanionError) as raised:
                    runtime.read_personality_backup(self.state, identifier)
                self.assertEqual(raised.exception.code, "backup_rejected")
                listing = runtime.list_personality_backups(self.state)
                entry = next(item for item in listing["backups"] if item["backup_id"] == identifier)
                self.assertEqual(entry["status"], "rejected")

    def test_listing_paginates_metadata_without_personality_or_arbitrary_files(self):
        identifiers = {runtime.backup_personality(self.state, self.home) for _ in range(3)}
        (self.state.directory / "backups" / "owner-notes.txt").write_text("Owner private text")
        first = runtime.list_personality_backups(self.state, limit=2)
        second = runtime.list_personality_backups(self.state, limit=2, before=first["next_cursor"])
        self.assertEqual({item["backup_id"] for item in first["backups"] + second["backups"]}, identifiers)
        self.assertIsNone(second["next_cursor"])
        self.assertNotIn("Synthetic original", json.dumps(first))
        self.assertNotIn("Owner private", json.dumps(first))

    def test_cli_backup_list_restore_and_failure_outputs_contain_only_metadata(self):
        key_id = str(uuid.uuid4())
        atomic_json(
            self.home / ".myhermes-installation.json", {"key_id": key_id, "state_directory": str(self.state.directory)}
        )
        atomic_json(
            self.state.directory / "config.json",
            {
                "server": "https://example.invalid",
                "hermes_home": str(self.home),
                "upstream": str(self.upstream),
                "key_id": key_id,
                "os": "macos",
                "allow_local_http": False,
            },
        )

        def command(*args):
            output = io.StringIO()
            with patch("myhermes.cli.command_kind", return_value=None), redirect_stdout(output):
                code = main(["--state-dir", str(self.state.directory), *args])
            self.assertNotIn("Synthetic original", output.getvalue())
            return code, json.loads(output.getvalue())

        code, backup = command("backup")
        self.assertEqual(code, 0)
        self.assertEqual(command("backups")[1]["backups"][0]["backup_id"], backup["backup_id"])
        self.assertEqual(command("restore", backup["backup_id"], "--dry-run")[0], 0)
        self.assertIn("safety_backup_id", command("restore", backup["backup_id"])[1])
        with patch.object(
            runtime, "install_runtime", side_effect=CompanionError("runtime_command_failed", "SECRET", 3)
        ):
            code, result = command("upgrade")
        self.assertEqual(code, 3)
        self.assertRegex(result["message"], r"backup ID: [0-9a-f-]{36}")
        self.assertNotIn("SECRET", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
