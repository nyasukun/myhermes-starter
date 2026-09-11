"""M2 acceptance: real local HTTP with synthetic credentials and owner API contract peer."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from connection_fixtures import (
    CompanyFixture,
    MemoryKeyring,
    SyntheticTerminal,
    github_http_fixture,
    template,
    token_for,
)
from myhermes.connection_schema import parse_template, resources, template_response
from myhermes.connection_store import ConnectionStore, GitHubCredentialStore
from myhermes.connection_terminal import NativeConnectionTerminal, terminal_text
from myhermes.connections import Connections, connection_command, draft_input
from myhermes.errors import CompanionError, OfflineError
from myhermes.github import API_VERSION, GitHubClient, parse_resource


class ConnectionAcceptance(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(
            tempfile.TemporaryDirectory(
                prefix="myhermes-connections-", dir="/private/tmp" if Path("/private/tmp").exists() else "/tmp"
            )
        )
        self.root = Path(temporary)
        self.http = self.stack.enter_context(github_http_fixture())
        self.company = CompanyFixture()
        self.keyring = MemoryKeyring()
        self.manager = self.device("a")

    def tearDown(self):
        self.stack.close()

    def device(self, label):
        installation_id = str(uuid.uuid4())
        config = {"installation_id": installation_id, "person_id": self.company.person_id, "os": "macos"}
        store = ConnectionStore(self.root / label)
        self.stack.callback(store.close)
        return Connections(
            config,
            self.root / label,
            api=self.company.device(installation_id),
            store=store,
            credentials=GitHubCredentialStore(installation_id, backend=self.keyring),
            github_factory=self.http["client_factory"],
        )

    def draft(self, *, kind="personal", resource="example-client/project", operations=None):
        return draft_input(
            SimpleNamespace(
                template_id="github-read",
                template_version="1.0.0",
                account_kind=kind,
                display_name="Synthetic account",
                management_kind="individual" if kind == "personal" else kind,
                management_label="Synthetic owner",
                project_id="example-client",
                project_label="Example client project",
                resource=[resource],
                operation=operations,
            )
        )

    def add(self, *, manager=None, account_id=101, kind="personal", operations=None):
        manager = manager or self.manager
        terminal = SyntheticTerminal(token_for(account_id))
        result = manager.add(self.draft(kind=kind), operations=operations, terminal=terminal)
        return result["connection_id"], terminal

    def test_real_http_registration_and_explicit_content_read_without_secret_metadata(self):
        connection_id, terminal = self.add()
        result = self.manager.read(
            connection_id, "issues.read", parse_resource("example-client/project"), include_content=True
        )
        self.assertEqual(result["provider_account_id"], "101")
        self.assertIn("Synthetic issue body", result["result"]["items"][0]["body"])
        self.assertNotIn(token_for(101), json.dumps(result))
        self.assertIn("[credential redacted]", result["result"]["items"][0]["body"])
        self.assertNotIn("untrusted.example", json.dumps(result))
        self.assertEqual(terminal.confirmations, 1)
        for request in self.http["requests"]:
            self.assertEqual(request["version"], API_VERSION)
        metadata = json.dumps(self.company.requests)
        self.assertNotIn(token_for(101), metadata)
        self.assertNotIn("Synthetic issue body", metadata)
        self.assertNotIn("private@example.invalid", metadata)
        db = (self.root / "a/connections.sqlite3").read_bytes()
        self.assertNotIn(token_for(101).encode(), db)
        self.assertNotIn(b"Synthetic issue body", db)
        self.assertEqual((self.root / "a/connections.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_device_detail_rejects_another_installation_binding_before_external_use(self):
        connection_id, _ = self.add()
        other = self.device("other")
        original = self.manager.api.request
        before = len(self.http["requests"])

        def substituted(method, path, payload=None):
            status, result = original(method, path, payload)
            if path == "/v1/connections/" + connection_id:
                result["bindings"][0]["installation_id"] = other.config["installation_id"]
            return status, result

        with patch.object(self.manager.api, "request", side_effect=substituted):
            with self.assertRaises(CompanionError) as raised:
                self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
        self.assertEqual(raised.exception.code, "connection_owner_mismatch")
        self.assertEqual(len(self.http["requests"]), before)

    def test_five_account_categories_share_one_environment_with_parallel_explicit_routing(self):
        connections = [
            self.add(account_id=101 + n, kind=kind)
            for n, kind in enumerate(("company", "client", "client", "personal", "personal"))
        ]
        config = self.manager.config

        def read_one(pair):
            connection_id, _ = pair
            store = ConnectionStore(self.root / "a")
            try:
                manager = Connections(
                    config,
                    self.root / "a",
                    api=self.company.device(config["installation_id"]),
                    store=store,
                    credentials=GitHubCredentialStore(config["installation_id"], backend=self.keyring),
                    github_factory=self.http["client_factory"],
                )
                return manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=5) as executor:
            output = list(executor.map(read_one, connections))
        self.assertEqual([item["provider_account_id"] for item in output], [str(n) for n in range(101, 106)])
        self.assertEqual([item["result"]["repository_id"] for item in output], [str(n * 10) for n in range(101, 106)])
        self.assertEqual(len(self.company.installations), 1)
        self.assertEqual(len(self.manager.list()["connections"]), 5)
        self.assertTrue(all(terminal.confirmations == 1 for _, terminal in connections))
        personal = self.company.connections[connections[-1][0]]
        self.assertEqual(personal["account_kind"], "personal")
        self.assertEqual(personal["resources"][0]["owner"], "example-client")

    def test_second_environment_needs_own_credential_and_same_account_identity(self):
        connection_id, _ = self.add()
        second = self.device("b")
        before = len(self.http["requests"])
        with self.assertRaises(CompanionError) as caught:
            second.read(connection_id, "repository.read", parse_resource("example-client/project"))
        self.assertEqual(caught.exception.code, "binding_not_ready")
        self.assertEqual(len(self.http["requests"]), before)
        with self.assertRaises(CompanionError) as caught:
            second.authorize(connection_id, terminal=SyntheticTerminal(token_for(102)))
        self.assertEqual(caught.exception.code, "github_account_mismatch")
        self.assertIsNone(second.store.binding(connection_id))
        second.authorize(connection_id, terminal=SyntheticTerminal(token_for(101)))
        self.assertEqual(
            second.read(connection_id, "repository.read", parse_resource("example-client/project"))[
                "provider_account_id"
            ],
            "101",
        )
        self.assertEqual(len(self.keyring.values), 2)
        self.assertNotEqual(
            self.manager.store.binding(connection_id)["credential_id"],
            second.store.binding(connection_id)["credential_id"],
        )

    def test_owner_declines_after_identity_check_no_key_or_connection_is_saved(self):
        with self.assertRaises(CompanionError) as caught:
            self.manager.add(self.draft(), operations=None, terminal=SyntheticTerminal(token_for(101), accepted=False))
        self.assertEqual(caught.exception.code, "authorization_cancelled")
        self.assertEqual(self.keyring.values, {})
        self.assertEqual(self.company.connections, {})
        self.assertEqual(self.manager.store.pending_metadata(), [])

    def test_current_grant_limits_and_stale_binding_require_fresh_capability_test(self):
        connection_id, _ = self.add()
        grant = self.company.grants[connection_id]
        grant["operations"] = ["repository.read"]
        grant["revision"] = 2
        before = len(self.http["requests"])
        with self.assertRaises(CompanionError) as caught:
            self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
        self.assertEqual(caught.exception.code, "binding_not_ready")
        self.assertEqual(len(self.http["requests"]), before)
        self.manager.test(connection_id)
        before = len(self.http["requests"])
        with self.assertRaises(CompanionError) as caught:
            self.manager.read(connection_id, "issues.read", parse_resource("example-client/project"))
        self.assertEqual(caught.exception.code, "grant_denied")
        self.assertEqual(len(self.http["requests"]), before)
        self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
        with self.assertRaises(CompanionError) as caught:
            self.manager.read(connection_id, "repository.read", parse_resource("example-client/not-granted"))
        self.assertEqual(caught.exception.code, "grant_denied")

    def test_revocation_disabled_template_and_company_offline_prevent_provider_calls(self):
        connection_id, _ = self.add()
        before = len(self.http["requests"])
        self.company.denied = True
        with self.assertRaises(CompanionError):
            self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
        self.company.denied = False
        self.company.offline = True
        with self.assertRaises(OfflineError):
            self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
        self.company.offline = False
        self.company.enabled = False
        with self.assertRaises(CompanionError) as caught:
            self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
        self.assertEqual(caught.exception.code, "connection_disabled")
        self.company.enabled = True
        self.company.bindings[(connection_id, self.manager.config["installation_id"])]["revoked_at"] = (
            "2026-09-11T01:00:00Z"
        )
        with self.assertRaises(CompanionError) as caught:
            self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
        self.assertEqual(caught.exception.code, "binding_revoked")
        self.assertEqual(len(self.http["requests"]), before)

    def test_swapped_stored_token_cannot_use_another_account(self):
        connection_id, _ = self.add()
        local = self.manager.store.binding(connection_id)
        self.manager.credentials.save(local["credential_id"], token_for(102))
        before = len(self.http["requests"])
        with self.assertRaises(CompanionError) as caught:
            self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
        self.assertEqual(caught.exception.code, "github_account_mismatch")
        self.assertEqual([item["path"] for item in self.http["requests"][before:]], ["/user"])

    def test_lost_create_response_resumes_same_request_and_one_connection(self):
        self.company.lose_once = ("POST", "/v1/connections")
        with self.assertRaises(OfflineError):
            self.add()
        pending = self.manager.store.pending_metadata()[0]
        self.assertEqual(len(self.company.connections), 1)
        self.assertEqual(len(self.keyring.values), 1)
        restarted_store = ConnectionStore(self.root / "a")
        self.stack.callback(restarted_store.close)
        restarted = Connections(
            self.manager.config,
            self.root / "a",
            api=self.manager.api,
            store=restarted_store,
            credentials=self.manager.credentials,
            github_factory=self.http["client_factory"],
        )
        result = restarted.resume(pending["request_id"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(self.company.connections), 1)
        posts = [item[3] for item in self.company.requests if item[1:3] == ("POST", "/v1/connections")]
        self.assertEqual(posts[0], posts[1])
        self.assertEqual(restarted.store.pending_metadata(), [])

    def test_lost_binding_response_reuses_exact_payload_without_second_connection(self):
        original = self.company.request
        once = True

        def request(installation_id, method, path, payload):
            nonlocal once
            result = original(installation_id, method, path, payload)
            if once and method == "PUT" and path.endswith("/binding"):
                once = False
                raise OfflineError()
            return result

        self.company.request = request
        with self.assertRaises(OfflineError):
            self.add()
        pending = self.manager.store.pending_metadata()[0]
        result = self.manager.resume(pending["request_id"])
        self.assertEqual(result["status"], "ready")
        updates = [item[3] for item in self.company.requests if item[1] == "PUT"]
        self.assertEqual(updates[0], updates[1])
        self.assertEqual(len(self.company.connections), 1)

    def test_changed_grant_after_binding_ack_is_retested_with_new_request_id(self):
        original = self.company.request
        once = True

        def request(installation_id, method, path, payload):
            nonlocal once
            result = original(installation_id, method, path, payload)
            if once and method == "PUT":
                once = False
                connection_id = path.split("/")[3]
                self.company.grants[connection_id]["revision"] = 2
                self.company.grants[connection_id]["operations"] = ["repository.read"]
            return result

        self.company.request = request
        with self.assertRaises(CompanionError):
            self.add()
        pending = self.manager.store.pending_metadata()[0]
        result = self.manager.resume(pending["request_id"])
        self.assertEqual(result["grant_revision"], 2)
        updates = [item[3] for item in self.company.requests if item[1] == "PUT"]
        self.assertNotEqual(updates[0]["request_id"], updates[1]["request_id"])
        self.assertEqual(updates[1]["tested_capabilities"], ["repository.read"])

    def test_forgotten_credential_is_locally_unusable_even_when_company_is_offline(self):
        connection_id, _ = self.add()
        self.company.offline = True
        result = self.manager.forget(connection_id)
        self.assertEqual(result["status"], "locally_forgotten")
        self.assertEqual(self.keyring.values, {})
        self.assertIsNone(self.manager.store.binding(connection_id))
        self.company.offline = False
        self.manager.resume(result["request_id"])
        with self.assertRaises(CompanionError):
            self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))

    def test_forget_after_remote_revocation_finishes_local_cleanup_without_stranded_retry(self):
        for revoke_binding in (False, True):
            with self.subTest(binding=revoke_binding):
                connection_id, _ = self.add()
                if revoke_binding:
                    binding = self.company.bindings[(connection_id, self.manager.config["installation_id"])]
                    binding["revoked_at"] = "2026-09-11T00:00:00Z"
                else:
                    self.company.connections[connection_id]["revoked_at"] = "2026-09-11T00:00:00Z"
                result = self.manager.forget(connection_id)
                self.assertEqual(result["metadata_sync"], "not_required_disabled")
                self.assertIsNone(self.manager.store.binding(connection_id))
                self.assertEqual(self.manager.store.pending_metadata(), [])
                self.assertEqual(self.manager.forget(connection_id)["status"], "already_forgotten")

    def test_response_failures_never_echo_tokens_or_provider_body(self):
        connection_id, _ = self.add()
        for mode, code in (
            ("redirect", "github_redirect_rejected"),
            ("denied", "github_access_denied"),
            ("rate_limit", "github_rate_limited"),
            ("invalid_json", "github_response_rejected"),
        ):
            with self.subTest(mode=mode):
                self.http["mode"] = mode
                with self.assertRaises(CompanionError) as caught:
                    self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn(token_for(101), str(caught.exception))
        self.http["mode"] = "normal"
        result = self.manager.read(connection_id, "issues.read", parse_resource("example-client/project"))
        self.assertNotIn("body", result["result"]["items"][0])
        self.assertNotIn("title", result["result"]["items"][0])

    def test_forget_cancels_lost_binding_ack_and_cannot_resurrect_pending_credential(self):
        original = self.company.request

        def request(installation_id, method, path, payload):
            result = original(installation_id, method, path, payload)
            if method == "PUT" and payload["status"] == "ready":
                raise OfflineError()
            return result

        self.company.request = request
        with self.assertRaises(OfflineError):
            self.add()
        pending = self.manager.store.pending_metadata()[0]
        self.assertIsNone(self.manager.store.binding(pending["connection_id"]))
        self.manager.forget(pending["connection_id"])
        self.assertEqual(self.keyring.values, {})
        self.assertEqual(self.manager.resume(pending["request_id"])["status"], "cancelled")
        self.assertIsNone(self.manager.store.binding(pending["connection_id"]))
        self.assertEqual(self.manager.store.pending_metadata(), [])

    def test_forget_detaches_before_keyring_deletion_and_resumes_after_locked_store(self):
        connection_id, _ = self.add()
        self.keyring.unavailable = True
        with self.assertRaises(CompanionError):
            self.manager.forget(connection_id)
        self.assertIsNone(self.manager.store.binding(connection_id))
        self.assertEqual(self.manager.store.pending_metadata()[0]["kind"], "forget")
        self.keyring.unavailable = False
        terminal = SyntheticTerminal(token_for(101))
        with self.assertRaises(CompanionError) as caught:
            self.manager.authorize(connection_id, terminal=terminal)
        self.assertEqual(caught.exception.code, "connection_request_pending")
        self.assertEqual(terminal.prompts, 0)
        self.assertEqual(self.manager.forget(connection_id)["status"], "forgotten")
        self.assertEqual(self.keyring.values, {})

    def test_cancel_lost_create_ack_deletes_credential_without_recreating_connection(self):
        self.company.lose_once = ("POST", "/v1/connections")
        with self.assertRaises(OfflineError):
            self.add()
        pending = self.manager.store.pending_metadata()[0]
        before = len(self.company.requests)
        self.assertEqual(self.manager.cancel(pending["request_id"])["status"], "cancelled")
        self.assertEqual(self.manager.resume(pending["request_id"])["status"], "cancelled")
        self.assertEqual(self.keyring.values, {})
        self.assertEqual(len(self.company.requests), before)
        self.assertEqual(len(self.company.connections), 1)

    def test_replaced_credential_cleanup_is_durable_after_native_deletion_failure(self):
        connection_id, _ = self.add()
        old_id = self.manager.store.binding(connection_id)["credential_id"]
        original_delete = self.manager.credentials.delete

        def fail_cleanup(credential_id):
            if credential_id == old_id:
                raise CompanionError("credential_unavailable", "Synthetic deletion failure", 3)
            original_delete(credential_id)

        with patch.object(self.manager.credentials, "delete", side_effect=fail_cleanup):
            with self.assertRaises(CompanionError):
                self.manager.authorize(connection_id, terminal=SyntheticTerminal(token_for(101)))
        pending = self.manager.store.pending_metadata()[0]
        self.assertEqual(pending["phase"], "cleanup_pending")
        self.assertEqual(len(self.keyring.values), 2)
        self.assertNotEqual(self.manager.store.binding(connection_id)["credential_id"], old_id)
        self.manager.resume(pending["request_id"])
        self.assertEqual(len(self.keyring.values), 1)
        self.assertEqual(self.manager.store.pending_metadata(), [])

    def test_cancel_pending_capability_test_keeps_shared_active_credential(self):
        connection_id, _ = self.add()
        self.company.lose_once = ("PUT", "/v1/connections/" + connection_id + "/binding")
        with self.assertRaises(OfflineError):
            self.manager.test(connection_id)
        pending = self.manager.store.pending_metadata()[0]
        self.manager.cancel(pending["request_id"])
        self.assertEqual(len(self.keyring.values), 1)
        self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))

    def test_repository_response_must_match_the_explicit_resource(self):
        connection_id, _ = self.add()
        self.http["mode"] = "wrong_resource"
        with self.assertRaises(CompanionError) as caught:
            self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
        self.assertEqual(caught.exception.code, "github_resource_mismatch")

    def test_private_template_tampering_fails_before_token_prompt(self):
        self.company.template["service"]["base_url"] = "https://untrusted.example"
        terminal = SyntheticTerminal(token_for(101))
        with self.assertRaises(CompanionError):
            self.manager.add(self.draft(), operations=None, terminal=terminal)
        self.assertEqual(terminal.prompts, 0)
        self.assertEqual(self.http["requests"], [])
        self.assertEqual(self.keyring.values, {})

    def test_case_insensitive_grant_match_does_not_choose_another_account(self):
        connection_id, _ = self.add()
        result = self.manager.read(connection_id, "repository.read", parse_resource("EXAMPLE-CLIENT/PROJECT"))
        self.assertEqual(result["provider_account_id"], "101")

    def test_keyring_unavailable_never_uses_plaintext_or_global_github_credentials(self):
        connection_id, _ = self.add()
        self.keyring.unavailable = True
        with (
            patch.dict("os.environ", {"GH_TOKEN": "must-not-be-used", "GITHUB_TOKEN": "must-not-be-used"}),
            self.assertRaises(CompanionError) as caught,
        ):
            self.manager.read(connection_id, "repository.read", parse_resource("example-client/project"))
        self.assertEqual(caught.exception.code, "credential_unavailable")


class ConnectionValidation(unittest.TestCase):
    def test_template_unknown_executable_fields_and_fixed_endpoint_bypasses_rejected(self):
        mutations = [
            lambda t: t.update(shell="echo malicious"),
            lambda t: t["setup"].update(command="touch /tmp/would-be-malicious"),
            lambda t: t["service"].update(base_url="http://127.0.0.1:9999"),
            lambda t: t["service"].update(allowed_hosts=["api.github.com", "untrusted.example"]),
            lambda t: t["service"].update(api_version="future-version"),
            lambda t: t["auth"].update(method="oauth", client_secret="must-not-exist"),
            lambda t: t.update(connector_version="9.0.0"),
            lambda t: t["input_fields"][0].update(required=1),
            lambda t: t["resource_rules"].append({"owner": "*", "repository": "*"}),
        ]
        for mutate in mutations:
            value = template()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(CompanionError):
                parse_template(value)

    def test_template_hash_and_requested_version_are_verified(self):
        value = template()
        with self.assertRaises(CompanionError):
            template_response({"template": value, "sha256": "0" * 64}, "github-read", "1.0.0")
        from myhermes.connection_schema import sha256

        with self.assertRaises(CompanionError):
            template_response({"template": value, "sha256": sha256(value)}, "github-read", "1.0.1")

    def test_resources_reject_urls_paths_controls_duplicates_and_null(self):
        for value in (
            "https://github.com/example/repo",
            "example/../repo",
            "example/..",
            "example/repo?token=x",
            "example/repo#x",
            "example/repo%2Fsecret",
            "example/repo\n",
            "example/repo;echo",
        ):
            with self.subTest(value=value), self.assertRaises(CompanionError):
                parse_resource(value)
        with self.assertRaises(CompanionError):
            resources([None])
        with self.assertRaises(CompanionError):
            resources([parse_resource("Example/Repo"), parse_resource("example/repo")])

    def test_only_finegrained_pat_type_and_no_empty_or_header_injection(self):
        for token in (
            "",
            "ghp_" + "x" * 40,
            "github_pat_" + "x" * 20 + "\nHeader: bad",
            "Bearer github_pat_" + "x" * 20,
        ):
            with self.assertRaises(CompanionError):
                GitHubClient(token)
        self.assertNotIn(token_for(101), repr(GitHubClient(token_for(101))))

    def test_native_terminal_has_no_stdin_fallback_and_escapes_control_text(self):
        with (
            patch("builtins.open", side_effect=OSError("no controlling tty")),
            self.assertRaises(CompanionError) as caught,
        ):
            NativeConnectionTerminal()
        self.assertEqual(caught.exception.code, "terminal_required")
        escaped = terminal_text("Notice\x1b]52;clipboard\x07\u202e")
        self.assertNotIn("\x1b", escaped)
        self.assertNotIn("\u202e", escaped)

    def test_dry_run_has_no_company_or_github_requests_and_no_token_input(self):
        args = SimpleNamespace(
            command="connections",
            connection_command="add",
            dry_run=True,
            template_id="github-read",
            template_version="1.0.0",
            account_kind="personal",
            display_name="Synthetic",
            management_kind="individual",
            management_label="Owner",
            project_id=None,
            project_label=None,
            resource=["example/repo"],
            operation=None,
        )
        calls = []
        result = connection_command(
            args, {}, Path("/not-created-by-dry-run"), owner_api=lambda config: calls.append(config)
        )
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["authorization"], "not_checked")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
