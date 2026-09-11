"""Shared checkout admission: real OS processes/CLI and synthetic runtime seams."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import unittest
import unicodedata
from unittest.mock import patch

from myhermes import cli
from myhermes.errors import CompanionError
from myhermes.runtime_lock import DIRECTORY, runtime_target_lock

_CHILD = r"""
import json, socket, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from myhermes import cli
from myhermes.errors import CompanionError
from myhermes.runtime_lock import runtime_target_lock

def deny(*args, **kwargs):
    raise AssertionError("Network/native credentials forbidden")
socket.socket.connect = socket.socket.connect_ex = socket.getaddrinfo = deny

def entered(*args, **kwargs):
    print("ENTERED", flush=True)
    if sys.argv[5] == "hold":
        assert sys.stdin.buffer.read(1) == b"x"
    return {"status": "stopped" if sys.argv[4] == "start" else "installed"}

try:
    if sys.argv[2] == "lock":
        with runtime_target_lock(Path(sys.argv[3]), writer=sys.argv[4] == "write"):
            entered()
    else:
        args = cli.parser().parse_args(["--state-dir", sys.argv[3], sys.argv[4]] + (["--offline"] if sys.argv[4] == "start" else []))
        with patch.object(cli, "install_runtime", entered), patch.object(cli, "upgrade_runtime", entered), patch.object(cli, "start_runtime", entered), patch.object(cli, "owner_api", return_value=None), patch.object(cli, "boundary_sync", return_value={"status":"offline_cached"}), patch.object(cli.Synchronizer, "flush_session", return_value={"status":"offline_cached"}), patch.object(cli, "SecureKeyStore", deny):
            cli.execute(args)
except CompanionError as error:
    print(json.dumps({"error": error.code}), flush=True)
"""


class SharedRuntimeAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="myhermes-runtime-lock-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.target = self.root / "runtime"
        self.source = str(Path(cli.__file__).resolve().parent.parent)
        self.env = {"PATH": os.defpath, "HOME": "/nonexistent", "PYTHONDONTWRITEBYTECODE": "1"}

    def command(self, mode, path, kind, hold=False):
        return [sys.executable, "-c", _CHILD, self.source, mode, str(path), kind, "hold" if hold else "once"]

    @contextmanager
    def holder(self, mode, path, kind):
        child = subprocess.Popen(
            self.command(mode, path, kind, hold=True),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(10), "Synthetic holder did not become ready")
                self.assertEqual(child.stdout.readline(), b"ENTERED\n")
            yield child
            child.stdin.write(b"x")
            child.stdin.flush()
            output, errors = child.communicate(timeout=10)
            self.assertEqual((child.returncode, output, errors), (0, b"", b""))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()

    def attempt(self, mode, path, kind):
        child = subprocess.run(self.command(mode, path, kind), capture_output=True, env=self.env, timeout=10)
        self.assertEqual((child.returncode, child.stderr), (0, b""))
        return child.stdout.decode().strip()

    def setup_home(self, name, target=None):
        state = self.root / ("state-" + name)
        args = cli.parser().parse_args(
            [
                "--state-dir",
                str(state),
                "setup",
                "--server",
                "https://fixture.example.invalid",
                "--hermes-home",
                str(self.root / ("home-" + name)),
                "--upstream",
                str(target or self.target),
            ]
        )
        self.assertEqual(cli.execute(args)["status"], "configured")
        return state

    def test_writer_excludes_writers_readers_but_not_another_target(self):
        with self.holder("lock", self.target, "write"):
            for mode in ("read", "write"):
                self.assertEqual(json.loads(self.attempt("lock", self.target, mode)), {"error": "runtime_busy"})
            self.assertEqual(self.attempt("lock", self.root / "different", "write"), "ENTERED")
        self.assertEqual(self.attempt("lock", self.target, "write"), "ENTERED")

    def test_parallel_readers_exclude_writer_until_both_close(self):
        with self.holder("lock", self.target, "read"):
            with self.holder("lock", self.target, "read"):
                self.assertEqual(json.loads(self.attempt("lock", self.target, "write")), {"error": "runtime_busy"})
            self.assertEqual(json.loads(self.attempt("lock", self.target, "write")), {"error": "runtime_busy"})
        self.assertEqual(self.attempt("lock", self.target, "write"), "ENTERED")

    def test_case_and_unicode_aliases_share_admission_before_and_after_creation(self):
        for original, alias in (("MixedRuntime", "mixedruntime"), ("Caf\u00e9Runtime", "Cafe\u0301runtime")):
            first, second = self.root / original, self.root / alias
            for exists in (False, True):
                if exists:
                    first.mkdir()
                with self.holder("lock", first, "write"):
                    for mode in ("write", "read"):
                        self.assertEqual(json.loads(self.attempt("lock", second, mode)), {"error": "runtime_busy"})

    def test_existing_parent_alias_on_case_insensitive_filesystem_uses_same_namespace(self):
        parent = self.root / "MixedParent"
        parent.mkdir()
        alias = self.root / "mixedparent"
        if not alias.exists():
            self.skipTest("This filesystem has distinct case-sensitive parent names")
        self.assertEqual(parent.stat().st_ino, alias.stat().st_ino)
        with self.holder("lock", parent / "Runtime", "write"):
            self.assertEqual(json.loads(self.attempt("lock", alias / "runtime", "read")), {"error": "runtime_busy"})

    def test_cli_two_legal_homes_block_same_target_before_runtime_operations(self):
        first, second = self.setup_home("a"), self.setup_home("b")
        for writer in ("install-runtime", "upgrade"):
            with self.holder("cli", first, writer):
                for command in ("install-runtime", "upgrade", "start"):
                    self.assertEqual(json.loads(self.attempt("cli", second, command)), {"error": "runtime_busy"})
        self.assertEqual(self.attempt("cli", second, "start"), "ENTERED")

    def test_cli_reader_lifetime_blocks_writer_and_allows_other_home_reader(self):
        first, second = self.setup_home("a"), self.setup_home("b")
        separate = self.setup_home("c", self.root / "other")
        with self.holder("cli", first, "start"):
            self.assertEqual(self.attempt("cli", second, "start"), "ENTERED")
            self.assertEqual(json.loads(self.attempt("cli", second, "upgrade")), {"error": "runtime_busy"})
            self.assertEqual(self.attempt("cli", separate, "upgrade"), "ENTERED")

    def test_lock_persists_and_missing_checkout_is_not_created_parent_mode_preserved(self):
        self.root.chmod(0o755)
        with runtime_target_lock(self.target, writer=True):
            self.assertFalse(self.target.exists())
            files = list((self.root / DIRECTORY).iterdir())
            self.assertEqual(len(files), 1)
            inode = files[0].stat().st_ino
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)
        with runtime_target_lock(self.target, writer=False):
            self.assertEqual(files[0].stat().st_ino, inode)
        self.assertEqual(files[0].read_bytes(), b"")
        nested = self.root / "new/nested/runtime"
        with runtime_target_lock(nested, writer=True):
            self.assertFalse(nested.exists())
        self.assertEqual(nested.parent.stat().st_mode & 0o777, 0o700)

    def test_unsafe_namespace_parent_and_target_links_fail_without_following(self):
        real = self.root / "real"
        real.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(real, target_is_directory=True)
        for target in (alias / "runtime", alias):
            with self.assertRaises(CompanionError) as error:
                with runtime_target_lock(target, writer=True):
                    self.fail("unsafe target admitted")
            self.assertEqual(error.exception.code, "runtime_lock_unsafe")
        namespace = self.root / DIRECTORY
        # A target symlink probe may already have created the safe empty namespace.
        if namespace.exists():
            for path in namespace.iterdir():
                path.unlink()
            namespace.rmdir()
        namespace.symlink_to(real, target_is_directory=True)
        with self.assertRaises(CompanionError):
            with runtime_target_lock(self.target, writer=True):
                self.fail("namespace symlink admitted")
        self.assertEqual(list(real.iterdir()), [])
        namespace.unlink()
        namespace.mkdir(mode=0o755)
        with self.assertRaises(CompanionError):
            with runtime_target_lock(self.target, writer=True):
                self.fail("unprotected namespace admitted")
        self.assertEqual(namespace.stat().st_mode & 0o777, 0o755)

    def test_fifo_symlink_hardlink_nonempty_and_unprotected_lock_rejected(self):
        namespace = self.root / DIRECTORY
        namespace.mkdir(mode=0o700)
        normalized = unicodedata.normalize("NFC", self.target.name.casefold())
        lock = namespace / (hashlib.sha256(os.fsencode(normalized)).hexdigest() + ".lock")
        other = self.root / "retained"
        other.write_bytes(b"retained synthetic bytes")
        for kind in ("fifo", "symlink", "hardlink", "nonempty", "mode"):
            with self.subTest(kind=kind):
                if kind == "fifo":
                    os.mkfifo(lock, 0o600)
                elif kind == "symlink":
                    lock.symlink_to(other)
                elif kind == "hardlink":
                    os.link(other, lock)
                else:
                    lock.write_bytes(b"nonempty" if kind == "nonempty" else b"")
                    lock.chmod(0o644 if kind == "mode" else 0o600)
                with self.assertRaises(CompanionError) as error:
                    with runtime_target_lock(self.target, writer=True):
                        self.fail("unsafe lock admitted")
                self.assertEqual(error.exception.code, "runtime_lock_unsafe")
                self.assertEqual(other.read_bytes(), b"retained synthetic bytes")
                lock.unlink()

    def test_dry_run_does_not_create_target_lock_or_assert_live_admission(self):
        state = self.setup_home("a")
        with (
            patch.object(cli, "install_runtime", return_value={"status": "dry_run"}) as install,
            patch.object(cli, "upgrade_runtime", return_value={"status": "dry_run"}) as upgrade,
            patch.object(cli, "verify_runtime") as verify,
            patch.object(cli, "runtime_target_lock", side_effect=AssertionError("dry run acquired lock")),
        ):
            for command in ("install-runtime", "upgrade", "start"):
                result = cli.execute(cli.parser().parse_args(["--state-dir", str(state), command, "--dry-run"]))
                self.assertEqual(result["status"], "dry_run")
            self.assertTrue(install.called and upgrade.called and verify.called)
        self.assertFalse((self.root / DIRECTORY).exists())
        self.assertFalse(self.target.exists())

    def test_caller_error_keeps_original_meaning_and_releases_lock(self):
        with self.assertRaisesRegex(OSError, "synthetic operation"):
            with runtime_target_lock(self.target, writer=True):
                raise OSError("synthetic operation")
        self.assertEqual(self.attempt("lock", self.target, "write"), "ENTERED")


if __name__ == "__main__":
    unittest.main()
