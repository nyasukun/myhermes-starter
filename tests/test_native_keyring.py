"""Opt-in real native-store checks; operate on one new synthetic account only."""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
import platform
import unittest
from unittest.mock import patch
import uuid

from myhermes.auth import SecureKeyStore, public_jwk
from myhermes.errors import CompanionError


class NativeKeyringAcceptance(unittest.TestCase):
    @unittest.skipUnless(
        os.getenv("MYHERMES_NATIVE_KEYRING") == "1", "Explicit native credential-store opt-in required"
    )
    def test_native_create_read_delete_one_synthetic_installation(self):
        output = io.StringIO()
        account = "myhermes-acceptance-" + str(uuid.uuid4())
        store = None
        created = False
        try:
            with redirect_stdout(output), redirect_stderr(output):
                store = SecureKeyStore()
                # A write may succeed before a failed readback; cleanup is
                # required once creation is attempted, not only after return.
                created = True
                key = store.create(account)
                loaded = store.load(account)
                self.assertIsNotNone(loaded)
                self.assertEqual(public_jwk(key), public_jwk(loaded))
                store.backend.delete_password(store.SERVICE, account)
                created = False
                self.assertIsNone(store.load(account))
        except CompanionError as error:
            self.fail("Native credential round trip unavailable: " + error.code)
        finally:
            if created and store is not None:
                try:
                    if store.load(account) is not None:
                        store.backend.delete_password(store.SERVICE, account)
                except Exception:
                    self.fail("Synthetic credential cleanup needs retry for acceptance account " + account)
        self.assertEqual(output.getvalue(), "", "Native check unexpectedly wrote output; content suppressed")

    @unittest.skipUnless(
        os.getenv("MYHERMES_NATIVE_KEYRING") == "1" and platform.system() == "Linux",
        "Requires explicit native checks on Ubuntu",
    )
    def test_ubuntu_missing_session_bus_fails_without_plaintext_fallback(self):
        with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/myhermes-deliberately-missing-bus"}):
            with self.assertRaises(CompanionError) as raised:
                SecureKeyStore()
        self.assertEqual(raised.exception.code, "secure_store_unavailable")


if __name__ == "__main__":
    unittest.main()
