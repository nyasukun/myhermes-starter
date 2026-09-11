"""Untrusted enrollment challenges cannot become terminal control or stored state."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
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

from myhermes import cli
from myhermes.errors import CompanionError


class Terminal(io.StringIO):
    def close(self):
        pass


class EnrollmentResponseAcceptance(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            config = {
                "server": "https://fixture.invalid",
                "label": "synthetic",
                "os": "macos",
                "key_id": str(uuid.uuid4()),
            }
            enrollment_id = str(uuid.uuid4())
            challenge = {
                "enrollment_id": enrollment_id,
                "user_code": "1234567890ABCDEF",
                "verification_uri": "https://fixture.invalid/?enrollment_id=" + enrollment_id,
                "expires_at": int(time.time()) + 600,
            }
            terminal, captured = Terminal(), io.StringIO()
            with (
                patch("myhermes.cli.SecureKeyStore") as secure,
                patch("myhermes.cli.API") as api,
                patch("myhermes.cli.webbrowser.open") as browser,
                patch("builtins.open", return_value=terminal),
                redirect_stdout(captured),
                redirect_stderr(captured),
            ):
                secure.return_value.create.return_value = ec.generate_private_key(ec.SECP256R1())
                api.return_value._request.side_effect = [(201, challenge), (202, {"status": "pending"})]
                yield directory, config, challenge, terminal, captured, secure, api, browser

    def enroll(self, directory, config):
        return cli.enroll(config, directory, SimpleNamespace(dry_run=False, no_browser=False, wait_seconds=0))

    def test_bad_code_rejected_before_store_browser_terminal_or_completion(self):
        values = ["\x1b]52;c;U1lOVEhFVElD\x07", "A" * 65536, "", "abcde12345abcdef", "0123456789ABCDEG", 17]
        for index, value in enumerate(values):
            with (
                self.subTest(case=index),
                self.fixture() as (directory, config, data, terminal, captured, secure, api, browser),
            ):
                data["user_code"] = value
                with self.assertRaises(CompanionError) as caught:
                    self.enroll(directory, config)
                self.assertEqual(caught.exception.code, "schema_rejected")
                secure.return_value.backend.set_password.assert_not_called()
                browser.assert_not_called()
                self.assertEqual(api.return_value._request.call_count, 1)
                self.assertEqual(terminal.getvalue(), "")
                self.assertEqual(captured.getvalue(), "")
                self.assertNotIn("enrollment", config)
                self.assertFalse((directory / "config.json").exists())

    def test_bad_metadata_rejected_before_code_storage(self):
        mutations = [
            lambda value: value.update(expires_at=True),
            lambda value: value.update(expires_at=float("nan")),
            lambda value: value.update(expires_at=10**400),
            lambda value: value.update(extra="SYNTHETIC_NOT_METADATA"),
            lambda value: value.update(verification_uri="https://other.invalid/"),
            lambda value: value.update(verification_uri=value["verification_uri"] + "&unexpected="),
            lambda value: value.update(verification_uri=value["verification_uri"] + "&enrollment_id="),
            lambda value: value.update(verification_uri=value["verification_uri"] + "#fragment"),
        ]
        for index, mutate in enumerate(mutations):
            with (
                self.subTest(case=index),
                self.fixture() as (directory, config, data, terminal, captured, secure, api, browser),
            ):
                mutate(data)
                with self.assertRaises(CompanionError) as caught:
                    self.enroll(directory, config)
                self.assertEqual(caught.exception.code, "schema_rejected")
                secure.return_value.backend.set_password.assert_not_called()
                browser.assert_not_called()
                self.assertEqual(terminal.getvalue(), "")
                self.assertEqual(captured.getvalue(), "")
                self.assertEqual(api.return_value._request.call_count, 1)
                self.assertNotIn("enrollment", config)

    def test_bad_resumed_native_code_never_reaches_terminal(self):
        for index, value in enumerate(["\x1b[2J", "A" * 65536, {"unexpected": "value"}]):
            with (
                self.subTest(case=index),
                self.fixture() as (directory, config, data, terminal, captured, secure, api, browser),
            ):
                config["enrollment"] = {key: value for key, value in data.items() if key != "user_code"}
                secure.return_value.backend.get_password.return_value = value
                with self.assertRaises(CompanionError) as caught:
                    self.enroll(directory, config)
                self.assertEqual(caught.exception.code, "schema_rejected")
                api.return_value._request.assert_not_called()
                secure.return_value.backend.set_password.assert_not_called()
                browser.assert_not_called()
                self.assertEqual(terminal.getvalue(), "")
                self.assertEqual(captured.getvalue(), "")
                self.assertEqual(config["enrollment"]["enrollment_id"], data["enrollment_id"])

    def test_valid_pending_challenge_resumes_and_completes_without_code_disclosure(self):
        with self.fixture() as (directory, config, data, terminal, captured, secure, api, browser):
            result = self.enroll(directory, config)
            self.assertEqual(result, {"status": "pending", "retry": "enroll"})
            self.assertIn(data["user_code"], terminal.getvalue())
            self.assertEqual(browser.call_args.args, (data["verification_uri"],))
            self.assertNotIn(data["user_code"], (directory / "config.json").read_text())
            self.assertEqual(captured.getvalue(), "")
            secure.return_value.backend.get_password.return_value = data["user_code"]
            installation, person = str(uuid.uuid4()), str(uuid.uuid4())
            api.return_value._request.side_effect = [(200, {"installation_id": installation, "person_id": person})]
            result = self.enroll(directory, config)
            self.assertEqual(result, {"status": "enrolled", "installation_id": installation})
            secure.return_value.backend.delete_password.assert_called_once_with(
                secure.return_value.SERVICE, config["key_id"] + ":enrollment-code"
            )
            self.assertNotIn(data["user_code"], json.dumps(config))
            self.assertNotIn(data["user_code"], str(browser.call_args_list))
            self.assertEqual(captured.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
