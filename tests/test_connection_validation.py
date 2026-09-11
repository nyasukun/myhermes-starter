"""Connection scalar validation matches the published TypeScript boundary."""

import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from connection_fixtures import template
from myhermes.connection_schema import bounded, parse_template, request_id
from myhermes.connection_store import ConnectionStore
from myhermes.connections import connection_command
from myhermes.errors import CompanionError


class ConnectionValidationAcceptance(unittest.TestCase):
    def test_nul_template_text_and_shared_metadata_scalar_are_rejected(self):
        for path in (("display_name",), ("description",), ("usage_notice", "text")):
            with self.subTest(field=path):
                value = copy.deepcopy(template())
                target = value
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = "Synthetic\0text"
                with self.assertRaises(CompanionError):
                    parse_template(value)
        with self.assertRaises(CompanionError):
            bounded("Synthetic\0metadata", 120)
        self.assertEqual(bounded("😀" * 120, 120), "😀" * 120)
        with self.assertRaises(CompanionError):
            bounded("😀" * 121, 120)

    def test_invalid_local_text_stops_before_provider_authorization_or_pending_state(self):
        args = SimpleNamespace(
            command="connections",
            connection_command="add",
            dry_run=False,
            template_id="github-read",
            template_version="1.0.0",
            account_kind="personal",
            display_name="Synthetic",
            management_kind="individual",
            management_label="Synthetic owner",
            project_id="project",
            project_label="Synthetic project",
            resource=["example/repo"],
            operation=None,
        )
        for field in ("display_name", "management_label", "project_label"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                value = copy.copy(args)
                setattr(value, field, "Synthetic\0text")
                directory = Path(temporary).resolve()
                owner_api = Mock(side_effect=AssertionError("Company API must not be reached"))
                with patch("myhermes.connection_terminal.NativeConnectionTerminal") as terminal:
                    with self.assertRaises(CompanionError) as caught:
                        connection_command(value, {}, directory, owner_api=owner_api)
                    self.assertEqual(caught.exception.code, "connection_schema_rejected")
                    terminal.return_value.read_token.assert_not_called()
                    terminal.return_value.confirm.assert_not_called()
                    owner_api.assert_not_called()
                state = ConnectionStore(directory)
                try:
                    self.assertEqual(state.pending_metadata(), [])
                finally:
                    state.close()

    def test_uuid_versions_and_rfc_variants_match_core_without_renaming_ids(self):
        base = "12345678-1234-4234-8234-123456789abc"
        accepted = [base, base.upper()]
        accepted += [base[:14] + version + base[15:] for version in "12345678"]
        accepted += [base[:19] + variant + base[20:] for variant in "89ab"]
        for value in accepted:
            self.assertEqual(request_id(value), value)
        rejected = ["00000000-0000-0000-0000-000000000000", "ffffffff-ffff-ffff-ffff-ffffffffffff"]
        rejected += [base[:14] + version + base[15:] for version in "09abcdef"]
        rejected += [base[:19] + variant + base[20:] for variant in "01234567cdef"]
        rejected += [base + suffix for suffix in ("\n", "\r", "\u2028", "\0", " ")]
        for index, value in enumerate(rejected):
            with self.subTest(case=index), self.assertRaises(CompanionError):
                request_id(value)


if __name__ == "__main__":
    unittest.main()
