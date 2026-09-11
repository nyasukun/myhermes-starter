"""M4 install/repair recovery against real local Git, without dependency/network calls."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from myhermes.errors import CompanionError
from myhermes import runtime
from myhermes.state import State


class RuntimeInstallAcceptance(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(
            os.path.realpath(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="myhermes-install-test-")))
        )
        self.source = self.root / "official-fixture"
        self.source.mkdir()
        for command in (
            ["git", "init", "-q", str(self.source)],
            ["git", "-C", str(self.source), "config", "user.name", "Synthetic test"],
            ["git", "-C", str(self.source), "config", "user.email", "fixture@example.invalid"],
        ):
            subprocess.run(command, check=True, capture_output=True)
        (self.source / "README.md").write_text("Synthetic upstream fixture\n")
        subprocess.run(["git", "-C", str(self.source), "add", "README.md"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.source), "commit", "-qm", "fixture"], check=True, capture_output=True)
        self.commit = runtime.checked(["git", "rev-parse", "HEAD"], cwd=self.source)
        self.stack.enter_context(patch.object(runtime, "UPSTREAM_COMMIT", self.commit))
        self.stack.enter_context(patch.object(runtime, "UPSTREAM_URL", str(self.source)))
        self.stack.enter_context(patch.object(runtime, "operating_system", return_value="macos"))
        self.destination = self.root / "parent/runtime"
        self.parent = self.destination.parent
        self.parent.mkdir(mode=0o755)
        self.parent.chmod(0o755)
        self.pip_failure = False
        self.clone_failure = False
        self.after_checkout = None
        self.python_version = "3.11"
        actual_checked = runtime.checked

        def fixture_command(command, *, cwd=None):
            if command[0] == "git":
                if self.clone_failure and "clone" in command:
                    Path(command[-1]).mkdir()
                    (Path(command[-1]) / "incomplete").write_text("Interrupted transfer")
                    raise CompanionError("runtime_command_failed", "Synthetic clone interruption", 3)
                result = actual_checked(command, cwd=cwd)
                if "checkout" in command and self.after_checkout is not None:
                    self.after_checkout()
                return result
            if "-c" in command:
                if str(command[0]).endswith("/.venv/bin/python"):
                    return json.dumps(
                        {
                            "version": self.python_version,
                            "prefix": str(Path(command[0]).parent.parent),
                            "base_prefix": "/synthetic/base",
                            "pip": True,
                        }
                    )
                return self.python_version
            if "venv" in command:
                target = Path(command[-1])
                (target / "bin").mkdir(parents=True)
                (target / "bin/python").write_text("Synthetic executable placeholder")
                return ""
            if "pip" in command:
                if self.pip_failure:
                    raise CompanionError("runtime_command_failed", "Synthetic install interruption", 3)
                (Path(cwd) / ".venv/bin/hermes").write_text("Synthetic console entrypoint")
                return ""
            self.fail("Unexpected installer command")

        self.stack.enter_context(patch.object(runtime, "checked", side_effect=fixture_command))

    def test_first_install_preserves_parent_and_publishes_verified_source_before_venv(self):
        sibling = self.parent / "owner-content"
        sibling.write_bytes(b"Owner content must remain intact")
        result = runtime.install_runtime(self.destination, "fixture-python")
        self.assertEqual(result["status"], "installed")
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(sibling.read_bytes(), b"Owner content must remain intact")
        self.assertEqual((self.destination / "README.md").read_text(), "Synthetic upstream fixture\n")
        self.assertTrue((self.destination / ".venv/bin/hermes").exists())
        self.assertEqual(list(self.parent.glob(".myhermes-install-*")), [])

    def test_failed_clone_has_no_final_path_and_can_retry(self):
        self.clone_failure = True
        with self.assertRaises(CompanionError):
            runtime.install_runtime(self.destination, "fixture-python")
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.parent.iterdir()), [])
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o755)
        self.clone_failure = False
        self.assertEqual(runtime.install_runtime(self.destination, "fixture-python")["status"], "installed")

    def test_interrupted_dependency_install_keeps_pin_and_repairs_without_recloning(self):
        self.pip_failure = True
        with self.assertRaises(CompanionError):
            runtime.install_runtime(self.destination, "fixture-python")
        self.assertTrue((self.destination / ".git").is_dir())
        local = self.destination / "retained-local-file"
        local.write_text("Untracked owner file")
        self.pip_failure = False
        self.clone_failure = True
        self.assertEqual(runtime.install_runtime(self.destination, "fixture-python")["status"], "installed")
        self.assertEqual(local.read_text(), "Untracked owner file")

    def test_existing_modified_or_unrelated_checkout_is_not_overwritten(self):
        runtime.install_runtime(self.destination, "fixture-python")
        readme = self.destination / "README.md"
        readme.write_text("Owner source modification")
        with self.assertRaises(CompanionError) as raised:
            runtime.install_runtime(self.destination, "fixture-python")
        self.assertEqual(raised.exception.code, "upstream_modified")
        self.assertEqual(readme.read_text(), "Owner source modification")
        readme.write_text("Synthetic upstream fixture\n")
        with patch.object(runtime, "UPSTREAM_COMMIT", "f" * 40):
            with self.assertRaises(CompanionError) as raised:
                runtime.install_runtime(self.destination, "fixture-python")
        self.assertEqual(raised.exception.code, "upstream_mismatch")
        self.assertTrue((self.destination / ".venv/bin/hermes").is_file())

    def test_destination_appearing_during_clone_is_preserved(self):
        def owner_creates_destination():
            self.destination.mkdir()
            (self.destination / "owner-file").write_text("Do not replace")

        self.after_checkout = owner_creates_destination
        with self.assertRaises(CompanionError) as raised:
            runtime.install_runtime(self.destination, "fixture-python")
        self.assertEqual(raised.exception.code, "runtime_destination_changed")
        self.assertEqual((self.destination / "owner-file").read_text(), "Do not replace")
        self.assertFalse((self.destination / ".git").exists())
        self.assertEqual(list(self.parent.glob(".myhermes-install-*")), [])

    def test_missing_parents_private_and_symlink_destination_rejected(self):
        destination = self.root / "new-parent/nested/runtime"
        runtime.install_runtime(destination, "fixture-python")
        self.assertEqual((self.root / "new-parent").stat().st_mode & 0o777, 0o700)
        self.assertEqual(destination.parent.stat().st_mode & 0o777, 0o700)
        alias = self.root / "symlink-parent"
        alias.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(CompanionError) as raised:
            runtime.install_runtime(alias / "uncreated", "fixture-python")
        self.assertEqual(raised.exception.code, "symlink_rejected")

    def test_unsupported_python_and_dry_run_do_not_create_installation(self):
        self.python_version = "3.14"
        with self.assertRaises(CompanionError) as raised:
            runtime.install_runtime(self.destination, "fixture-python")
        self.assertEqual(raised.exception.code, "python_incompatible")
        self.assertFalse(self.destination.exists())
        self.assertEqual(runtime.install_runtime(self.destination, "fixture-python", dry_run=True)["status"], "dry_run")
        self.assertFalse(self.destination.exists())

    def test_upgrade_backup_contains_only_personality_and_leaves_outbox(self):
        home = self.root / "home"
        (home / "memories").mkdir(parents=True)
        (home / "SOUL.md").write_text("Synthetic personality")
        (home / "memories/MEMORY.md").write_text("Synthetic memory")
        (home / "state.db").write_bytes(b"Synthetic session database")
        (home / ".env").write_text("SYNTHETIC_SECRET=not-a-real-key")
        state = State(self.root / "state")
        self.addCleanup(state.close)
        queued = state.queue([{"path": "SOUL.md", "content": "Synthetic pending edit"}])
        backup = runtime.backup_personality(state, home)
        value = json.loads((state.directory / "backups" / (backup + ".json")).read_text())
        self.assertEqual(set(value["files"]), {"SOUL.md", "memories/MEMORY.md", "memories/USER.md"})
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(value))
        self.assertEqual(state.items("pending"), [queued])


@unittest.skipUnless(
    (3, 11) <= sys.version_info[:2] <= (3, 13), "Real venv repair requires upstream-supported Python 3.11–3.13"
)
class RuntimePartialVenvAcceptance(unittest.TestCase):
    """Real venv/ensurepip; only the subsequent dependency installation is a fixture."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(
            self.stack.enter_context(tempfile.TemporaryDirectory(prefix="myhermes-partial-venv-"))
        ).resolve()
        self.checkout = self.root / "runtime"
        self.checkout.mkdir()
        self.stack.enter_context(
            patch.dict(os.environ, {"PATH": os.defpath, "HOME": str(self.root), "GIT_CONFIG_NOSYSTEM": "1"}, clear=True)
        )
        self.actual_checked = runtime.checked
        for command in (
            ["git", "init", "-q", str(self.checkout)],
            ["git", "-C", str(self.checkout), "config", "user.name", "Synthetic fixture"],
            ["git", "-C", str(self.checkout), "config", "user.email", "fixture@example.invalid"],
            ["git", "-C", str(self.checkout), "config", "commit.gpgsign", "false"],
        ):
            self.actual_checked(command)
        (self.checkout / "README.md").write_text("Synthetic upstream fixture\n")
        self.actual_checked(["git", "add", "README.md"], cwd=self.checkout)
        self.actual_checked(["git", "commit", "-qm", "fixture"], cwd=self.checkout)
        commit = self.actual_checked(["git", "rev-parse", "HEAD"], cwd=self.checkout)
        self.stack.enter_context(patch.object(runtime, "UPSTREAM_COMMIT", commit))
        self.python = self.checkout / ".venv/bin/python"
        self.calls = []

        def command(args, *, cwd=None):
            self.calls.append(args)
            if "pip" in args and "install" in args:
                # A missing pip must still fail here; never replace its actual
                # bootstrap with a mock or perform an external dependency install.
                self.actual_checked([str(self.python), "-I", "-m", "pip", "--version"])
                (self.checkout / ".venv/bin/hermes").write_text("Synthetic console entrypoint\n")
                return ""
            return self.actual_checked(args, cwd=cwd)

        self.stack.enter_context(patch.object(runtime, "checked", side_effect=command))

    def without_pip(self):
        self.actual_checked([sys.executable, "-m", "venv", "--without-pip", str(self.checkout / ".venv")])

    def test_partial_venv_recovers_pip_without_recreation_or_owner_file_changes(self):
        self.without_pip()
        config = self.checkout / ".venv/pyvenv.cfg"
        original_config = config.read_bytes()
        retained = self.checkout / ".venv/owner-note.txt"
        retained.write_bytes(b"Synthetic retained owner file")
        self.assertEqual(
            self.actual_checked(
                [str(self.python), "-I", "-c", "import importlib.util; print(importlib.util.find_spec('pip') is None)"]
            ),
            "True",
        )
        for _ in range(2):
            self.assertEqual(runtime.install_runtime(self.checkout, sys.executable)["status"], "installed")
        self.assertEqual(sum("ensurepip" in call for call in self.calls), 1)
        self.assertFalse(any("venv" in call for call in self.calls))
        self.assertEqual(config.read_bytes(), original_config)
        self.assertEqual(retained.read_bytes(), b"Synthetic retained owner file")
        self.assertEqual(
            self.actual_checked(["git", "status", "--porcelain", "--untracked-files=no"], cwd=self.checkout), ""
        )

    def test_different_selected_python_version_rejects_before_pip_bootstrap(self):
        self.without_pip()
        previous = runtime.checked
        version = "3.12" if sys.version_info.minor != 12 else "3.11"

        def different(args, *, cwd=None):
            if args[0] == sys.executable and "-c" in args:
                return version
            return previous(args, cwd=cwd)

        with patch.object(runtime, "checked", side_effect=different), self.assertRaises(CompanionError) as raised:
            runtime.install_runtime(self.checkout, sys.executable)
        self.assertEqual(raised.exception.code, "runtime_venv_mismatch")
        self.assertFalse(any("ensurepip" in call or "pip" in call for call in self.calls))

    def test_interpreter_outside_target_venv_rejects_without_touching_base_python(self):
        self.python.parent.mkdir(parents=True)
        self.python.symlink_to(sys.executable)
        with self.assertRaises(CompanionError) as raised:
            runtime.install_runtime(self.checkout, sys.executable)
        self.assertEqual(raised.exception.code, "runtime_venv_mismatch")
        self.assertFalse(any("ensurepip" in call or "pip" in call for call in self.calls))
        self.assertFalse((self.checkout / ".venv/bin/hermes").exists())


if __name__ == "__main__":
    unittest.main()
