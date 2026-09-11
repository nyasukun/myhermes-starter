"""Untrusted enrollment IDs and non-live candidate states must fail before publication/API."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from cryptography.hazmat.primitives.asymmetric import ec

from myhermes import cli, identity_recovery as recovery
from myhermes.errors import CompanionError
from myhermes.files import atomic_json
from myhermes.local_config import config_read
import test_identity_recovery as identity_fixture


class EnrollmentIdentityValidation(unittest.TestCase):
    def complete(self, directory, response):
        class Terminal(io.StringIO):
            def close(self):
                pass

        pending_id = str(uuid.uuid4())
        original = {
            "server": "https://fixture.invalid",
            "label": "synthetic",
            "os": "macos",
            "key_id": str(uuid.uuid4()),
        }
        with (
            patch("myhermes.cli.SecureKeyStore") as store,
            patch("myhermes.cli.API") as api,
            patch("builtins.open", return_value=Terminal()),
        ):
            store.return_value.create.return_value = ec.generate_private_key(ec.SECP256R1())
            api.return_value._request.side_effect = [
                (
                    201,
                    {
                        "enrollment_id": pending_id,
                        "user_code": "1234567890ABCDEF",
                        "verification_uri": "https://fixture.invalid/?enrollment_id=" + pending_id,
                        "expires_at": time.time() + 600,
                    },
                ),
                (200, response),
            ]
            return cli.enroll(original, directory, SimpleNamespace(dry_run=False, no_browser=True, wait_seconds=0))

    def test_uppercase_server_ids_are_canonical_before_config_publication(self):
        installation, person = str(uuid.uuid4()), str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            receipt = self.complete(directory, {"installation_id": installation.upper(), "person_id": person.upper()})
            self.assertEqual(receipt["installation_id"], installation)
            result = json.loads((directory / "config.json").read_text())
            self.assertEqual((result["installation_id"], result["person_id"]), (installation, person))

    def test_bad_server_person_or_installation_keeps_pending_state_unpoisoned(self):
        for field in ("person_id", "installation_id"):
            for value in ({"body": "SYNTHETIC_PRIVATE_CANARY"}, [str(uuid.uuid4())], "not-an-id", None, "a" * 65536):
                with self.subTest(field=field, kind=type(value).__name__), tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary).resolve()
                    response = {"installation_id": str(uuid.uuid4()), "person_id": str(uuid.uuid4()), field: value}
                    with self.assertRaises(CompanionError) as caught:
                        self.complete(directory, response)
                    self.assertEqual(caught.exception.code, "schema_rejected")
                    saved = json.loads((directory / "config.json").read_text())
                    self.assertIn("enrollment", saved)
                    self.assertNotIn("person_id", saved)
                    self.assertNotIn("installation_id", saved)
                    self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", json.dumps(saved))


class CandidateStateValidation(unittest.TestCase):
    def setUp(self):
        self.fixture = identity_fixture.IdentityRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_legacy_uppercase_owner_can_cancel_pending_recovery_and_complete(self):
        f = self.fixture
        f.config["installation_id"] = f.config["installation_id"].upper()
        f.config["person_id"] = f.config["person_id"].upper()
        atomic_json(f.directory / "config.json", f.config)
        f.peer = identity_fixture.ReplacementPeer(f.config["person_id"].lower())
        f.peer.pending = True
        self.assertEqual(f.run_recovery()["status"], "pending")
        self.assertEqual(f.run_recovery("--cancel")["status"], "cancelled")
        self.assertEqual(f.run_recovery("--new")["status"], "completed")
        self.assertEqual(config_read(f.directory)["person_id"], f.config["person_id"].lower())

    def test_case_variant_of_original_installation_is_not_a_replacement(self):
        f = self.fixture
        f.peer.installation = f.config["installation_id"].upper()
        with self.assertRaises(CompanionError) as caught:
            f.run_recovery()
        self.assertEqual(caught.exception.code, "identity_recovery_invalid")
        self.assertEqual(config_read(f.directory), f.config)
        self.assertEqual(f.peer.requests, [])
        self.assertEqual(f.run_recovery("--cancel")["status"], "cancelled")

    def test_candidate_state_cannot_dispatch_any_home_bound_api_or_monitoring(self):
        f = self.fixture
        f.peer.person = str(uuid.uuid4())
        with self.assertRaises(CompanionError):
            f.run_recovery()
        journal = recovery._journal(f.directory)
        candidate = f.directory / "identity-recovery" / journal["operation_id"] / "candidate"
        commands = (
            ["templates", "list"],
            ["connections", "pending"],
            ["connections", "authorize", str(uuid.uuid4())],
            ["monitoring", "flush"],
            ["skills", "list"],
            ["inspect"],
        )
        for command in commands:
            with (
                self.subTest(command=command),
                patch.object(cli, "owner_api") as api,
                patch.object(cli, "execute_connection_command", return_value={"status": "bypassed"}) as dispatch,
                patch.object(cli, "monitoring_command", return_value={"status": "bypassed"}) as monitoring,
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = cli.main(["--state-dir", str(candidate), *command])
                self.assertEqual(code, 3)
                self.assertEqual(json.loads(output.getvalue())["error"], "home_binding_mismatch")
                api.assert_not_called()
                dispatch.assert_not_called()
                monitoring.assert_not_called()
        self.assertFalse((candidate / "telemetry.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
