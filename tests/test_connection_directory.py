"""Metadata reports/guides over the actual bridge; no external services or keys."""

from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import uuid

from myhermes.connection_directory import ConnectionDirectory, snapshot
from myhermes.errors import CompanionError
from myhermes import hermes_connections_plugin as plugin
from myhermes.relay_bridge import RelayBridge
from myhermes.runtime import _connections_plugin


def integration():
    return {
        "integration_id": "work-chat",
        "display_name": "Synthetic work chat",
        "service": "chat",
        "required": True,
        "enabled": True,
        "source": {"kind": "mcp", "server_name": "work_chat"},
        "guide": {
            "summary": "Use your work account.",
            "steps": ["Ask the administrator for access."],
            "url": "https://example.com/setup",
        },
        "revision": 1,
        "updated_at": "2026-09-24T01:00:00.000Z",
    }


class API:
    server = "https://app.example"
    installation_id = str(uuid.uuid4())

    def __init__(self):
        self.revision = 0
        self.sent = []
        self.reported = None

    def request(self, method, path, payload=None):
        self.sent.append((method, path, deepcopy(payload)))
        if path == "/v1/connections/report":
            if method == "GET":
                return 200, {"revision": self.revision}
            self.revision += 1
            self.reported = deepcopy(payload)
            return 200, {
                "revision": self.revision,
                "report_id": payload["report_id"],
                "received_at": "2026-09-24T01:00:00.000Z",
            }
        if path == "/v1/companion-release":
            return 200, {"revision": 0, "release": None, "updated_at": None}
        if path == "/v1/integrations":
            return 200, {"integrations": [integration()]}
        if path == "/v1/installations/" + self.installation_id + "/connections":
            return 200, {
                "installation": {
                    "installation_id": self.installation_id,
                    "person_id": "alice",
                    "label": "Synthetic",
                    "revoked_at": None,
                },
                "report": None,
                "mcp": [],
                "github": [],
                "next_cursor": None,
                "integrations": [
                    {
                        **integration(),
                        "status": "not_connected",
                        "evidence_source": "client_reported",
                        "observed_at": None,
                    }
                ],
            }
        raise AssertionError("Unexpected company endpoint")


class ConnectionDirectoryTests(unittest.TestCase):
    def test_snapshot_rejects_secrets_duplicates_and_missing_collection(self):
        for value in (
            {"collection_status": "ok", "connections": [], "token": "CANARY"},
            {"collection_status": "unavailable", "connections": [{"server_name": "work_chat", "status": "connected"}]},
            {
                "collection_status": "ok",
                "connections": [{"server_name": "work_chat", "status": "connected", "error": "CANARY"}],
            },
            {"collection_status": "ok", "connections": [{"server_name": "work_chat", "status": "connected"}] * 2},
        ):
            with self.subTest(value=value), self.assertRaises(CompanionError):
                snapshot(value)

    def test_projector_never_serializes_upstream_errors_or_configuration(self):
        module = ModuleType("tools.mcp_tool_discovery")
        module.get_mcp_status = lambda: [
            {
                "name": "work_chat",
                "status": "failed",
                "connected": False,
                "error": "SYNTHETIC-SECRET",
                "url": "https://secret.example",
                "sampling": {"body": "CANARY"},
            }
        ]
        with patch.dict(sys.modules, {"tools": ModuleType("tools"), "tools.mcp_tool_discovery": module}):
            self.assertEqual(
                plugin.collect_snapshot(),
                {"collection_status": "ok", "connections": [{"server_name": "work_chat", "status": "error"}]},
            )
            module.get_mcp_status = lambda: [{"name": "bad/name", "status": "connected"}]
            self.assertEqual(plugin.collect_snapshot(), {"collection_status": "unavailable", "connections": []})

    def test_bridge_authentication_strict_projection_and_live_guide_fetch(self):
        api = API()
        client = ConnectionDirectory(api)
        with RelayBridge(api, connection_directory=client) as bridge:
            url = bridge.base_url + "/myhermes/connections"
            payload = {"snapshot": {"collection_status": "ok", "connections": []}, "integration_id": "work-chat"}

            def request(value, token=bridge.session_token):
                try:
                    return urllib.request.urlopen(
                        urllib.request.Request(
                            url,
                            json.dumps(value).encode(),
                            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
                        ),
                        timeout=5,
                    )
                except urllib.error.HTTPError as error:
                    return error

            with request(payload, "invalid") as response:
                self.assertEqual(response.status, 401)
            with request({**payload, "command": "cat private"}) as response:
                self.assertEqual(response.status, 400)
            with request(payload) as response:
                self.assertEqual(response.status, 200)
                value = json.load(response)
                self.assertEqual(value["integrations"][0]["guide"]["steps"], ["Ask the administrator for access."])
            self.assertEqual(api.reported["connections"], [])
            self.assertNotIn("installation_id", api.reported)
            self.assertNotIn(bridge.session_token, json.dumps(api.sent))
            self.assertTrue(all(path.startswith("/v1/") for _, path, _ in api.sent))

    def test_plugin_does_not_claim_success_when_the_directory_is_unavailable(self):
        with patch.dict(
            os.environ, {"MYHERMES_CONNECTIONS_URL": "https://other.example", "AUXILIARY_MYHERMES_API_KEY": "fixture"}
        ):
            result = json.loads(plugin.connection_tool({}))
        self.assertEqual(result["error"], "connection_directory_unavailable")

    @unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires explicitly selected pinned Hermes")
    def test_actual_pinned_plugin_registration_tool_dispatch_and_status_accessor(self):
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        with ExitStack() as stack:
            root = Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(
                        prefix="myhermes-directory-", dir="/private/tmp" if Path("/private/tmp").exists() else "/tmp"
                    )
                )
            )
            home = root / "home"
            home.mkdir()
            enabled = _connections_plugin(home, {}, None)
            (home / "config.yaml").write_text(json.dumps({"plugins": {"enabled": enabled}, "mcp_servers": {}}))
            api = API()
            bridge = stack.enter_context(RelayBridge(api, connection_directory=ConnectionDirectory(api)))
            environment = {
                **os.environ,
                "HERMES_HOME": str(home),
                "HERMES_MANAGED_DIR": str(home),
                "PYTHONDONTWRITEBYTECODE": "1",
                "MYHERMES_CONNECTIONS_URL": bridge.base_url + "/myhermes/connections",
                "AUXILIARY_MYHERMES_API_KEY": bridge.session_token,
            }
            script = """
import json
from tools.mcp_tool_discovery import get_mcp_status
assert get_mcp_status(configured={}) == []
from hermes_cli.plugins import discover_plugins
discover_plugins()
from tools.registry import registry
entry = registry.get_entry('myhermes_connections')
assert entry is not None
result = json.loads(entry.handler({'integration_id':'work-chat'}))
assert result['integrations'][0]['guide']['steps'] == ['Ask the administrator for access.']
assert result['report_delivery'] == 'reported'
print('verified company guide tool')
"""
            result = subprocess.run(
                [str(upstream / ".venv/bin/python"), "-c", script],
                cwd=upstream,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, "Pinned plugin probe failed: " + result.stderr[-2000:])
            self.assertIn("verified company guide tool", result.stdout)
            self.assertNotIn(bridge.session_token, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
