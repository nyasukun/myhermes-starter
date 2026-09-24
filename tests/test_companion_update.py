"""The updater uses only a selected public artifact and a dedicated environment."""

import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from myhermes import companion_update as update
from myhermes.cli import parser
from myhermes.errors import CompanionError
from myhermes.managed_plugin import install_connections_plugin


def wheel(version="0.5.0"):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr(
            f"myhermes_companion-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: myhermes-companion\nVersion: {version}\n",
        )
    return raw.getvalue()


def release(raw=None, version="0.5.0"):
    return {
        "version": version,
        "wheel_url": update.RELEASE_ROOT + f"v{version}/myhermes_companion-{version}-py3-none-any.whl",
        "sha256": hashlib.sha256(wheel(version) if raw is None else raw).hexdigest(),
    }


class Response(io.BytesIO):
    def __init__(self, raw=b"", status=200, location=None):
        super().__init__(raw)
        self.status, self.headers = status, {"Location": location} if location else {}


class UpdaterTests(unittest.TestCase):
    def test_check_and_dry_run_never_download_or_install(self):
        api = SimpleNamespace(
            request=lambda *_args: (200, {"revision": 1, "release": release(), "updated_at": "2026-09-24T00:00:00Z"})
        )
        with patch.object(update, "download_wheel", side_effect=AssertionError("unexpected download")):
            for dry_run in (False, True):
                result = update.update_companion(
                    SimpleNamespace(apply=False, dry_run=dry_run), {}, Path("/unused"), api
                )
                self.assertTrue(result["update_available"])
        with self.assertRaises(SystemExit):
            parser().parse_args(["self-update", "--dry-run", "--apply"])

    def test_hash_and_redirect_boundaries_precede_any_installation(self):
        raw = wheel()
        good = SimpleNamespace(open=lambda request, timeout: Response(raw))
        self.assertEqual(update.download_wheel(release(raw), opener=good), raw)
        bad = SimpleNamespace(open=lambda request, timeout: Response(raw + b"tamper"))
        with self.assertRaises(CompanionError):
            update.download_wheel(release(raw), opener=bad)
        for url in [
            "http://github.com/file",
            "https://evil.example/file",
            "https://token@github.com/file",
            "https://github.com:444/file",
        ]:
            with self.subTest(url=url), self.assertRaises(CompanionError):
                update.download_wheel(
                    release(raw),
                    opener=SimpleNamespace(open=lambda request, timeout: Response(status=302, location=url)),
                )
        for value in [
            {**release(raw), "wheel_url": "https://github.com/other/repo/file.whl"},
            {**release(raw), "sha256": "bad"},
        ]:
            with self.assertRaises(CompanionError):
                update.parse_release(value)

    def test_package_identity_is_checked_and_install_command_uses_verified_local_wheel(self):
        raw = wheel()
        api = SimpleNamespace(
            request=lambda *_args: (200, {"revision": 1, "release": release(raw), "updated_at": None})
        )
        with tempfile.TemporaryDirectory(prefix="myhermes-updater-") as directory:
            root = Path(directory).resolve()
            home = root / "home"
            home.mkdir()
            state = root / "state"
            state.mkdir()
            config = {"hermes_home": str(home)}
            calls = []

            def run(command):
                calls.append(command)
                return "0.5.0" if "-c" in command else ""

            with (
                patch.object(update, "_update_environment", return_value=Path("/fixture/venv/bin/python")),
                patch.object(update, "download_wheel", return_value=raw),
                patch.object(update, "_run", side_effect=run),
            ):
                result = update.update_companion(SimpleNamespace(apply=True, dry_run=False), config, state, api)
            self.assertEqual(result["status"], "updated")
            retained = Path(result["verified_wheel"])
            self.assertEqual(retained.read_bytes(), raw)
            self.assertEqual(len(calls), 3)
            self.assertIn("--dry-run", calls[0])
            self.assertIn("--no-index", calls[1])
            self.assertIn("--isolated", calls[1])
            self.assertIn(str(retained), calls[1])
            self.assertNotIn("--dry-run", calls[1])
            self.assertFalse(any(arg.startswith("https:") for command in calls for arg in command))
            retained.write_bytes(wheel("0.9.0"))
            with self.assertRaises(CompanionError):
                update.verify_wheel(retained, release(raw))

    def test_system_python_and_editable_installs_are_not_replaced(self):
        with (
            patch.object(update.sys, "prefix", "/system"),
            patch.object(update.sys, "base_prefix", "/system"),
            self.assertRaises(CompanionError),
        ):
            update._update_environment()
        dist = SimpleNamespace(read_text=lambda _: json.dumps({"dir_info": {"editable": True}}))
        with (
            patch.object(update.sys, "prefix", "/fixture/venv"),
            patch.object(update.sys, "base_prefix", "/system"),
            patch.object(update.importlib.metadata, "distribution", return_value=dist),
            self.assertRaises(CompanionError),
        ):
            update._update_environment()

    def test_plugin_upgrade_recovers_partial_writes_and_preserves_owner_changes(self):
        with tempfile.TemporaryDirectory(prefix="myhermes-plugin-update-") as temporary:
            home = Path(temporary).resolve()
            old = {"__init__.py": b"old source", "plugin.yaml": b"old manifest"}
            new = {"__init__.py": b"new source", "plugin.yaml": b"new manifest"}
            install_connections_plugin(home, old)
            from myhermes import managed_plugin

            real = managed_plugin.atomic_bytes

            def interrupted(path, raw):
                if path.name == "plugin.yaml":
                    raise OSError("synthetic interruption")
                real(path, raw)

            with (
                patch.object(managed_plugin, "atomic_bytes", side_effect=interrupted),
                self.assertRaises(CompanionError),
            ):
                install_connections_plugin(home, new)
            install_connections_plugin(home, new)
            path = home / "plugins/myhermes-connections/__init__.py"
            self.assertEqual(path.read_bytes(), new["__init__.py"])
            path.write_bytes(b"owner custom edits")
            with self.assertRaises(CompanionError):
                install_connections_plugin(home, old)
            self.assertEqual(path.read_bytes(), b"owner custom edits")


if __name__ == "__main__":
    unittest.main()
