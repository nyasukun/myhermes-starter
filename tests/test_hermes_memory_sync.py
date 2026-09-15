"""Opt-in official memory tool execution, real managed exit, and cross-home sync.

Acceptance: the pinned memory tool schema reaches the synthetic provider; two
real tool dispatches persist MEMORY/USER and report success; managed start's
post-session sync publishes them; a fresh second environment pulls exact bytes.
Only native-terminal I/O and the remote persona contract peer are fixture seams.
"""

from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from myhermes import cli
from myhermes.relay_bridge import RelayBridge
from myhermes.state import State
from myhermes.sync import Synchronizer
from test_companion import FakeAPI
from test_relay_bridge import company

MEMORY = "The fictional project uses a blue notebook for examples."
USER = "The fictional owner prefers concise example summaries."


@unittest.skipUnless(
    os.getenv("MYHERMES_TEST_UPSTREAM") and os.getenv("MYHERMES_TEST_DOCKER") == "1",
    "Requires the pinned checkout and explicit local Docker execution opt-in",
)
class HermesMemorySyncAcceptance(unittest.TestCase):
    def test_official_memory_tools_persist_and_sync_at_managed_session_exit(self):
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        requests, tool_results, runtime_output, events = [], [], [], []

        def completion(request):
            requests.append(request)
            tools = {tool["function"]["name"]: tool["function"] for tool in request.get("tools", [])}
            self.assertIn("memory", tools)
            self.assertEqual(set(tools["memory"]["parameters"]["properties"]["target"]["enum"]), {"memory", "user"})
            results = [message for message in request["messages"] if message["role"] == "tool"]
            if results:
                tool_results.extend(results)
                return {"role": "assistant", "content": "MEMORY_TOOL_READY."}, "stop"
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "index": index,
                        "id": "fixture_memory_" + target,
                        "type": "function",
                        "function": {
                            "name": "memory",
                            "arguments": json.dumps({"target": target, "action": "add", "content": content}),
                        },
                    }
                    for index, (target, content) in enumerate((("memory", MEMORY), ("user", USER)))
                ],
            }, "tool_calls"

        with ExitStack() as stack:
            root = Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(prefix=".myhermes-memory-sync-", dir=Path(__file__).parent.parent)
                )
            ).resolve()
            directory, home = root / "state", root / "home"
            argv = ["--state-dir", str(directory)]
            cli.execute(
                cli.parser().parse_args(
                    [
                        *argv,
                        "setup",
                        "--server",
                        "https://fixture.invalid",
                        "--hermes-home",
                        str(home),
                        "--upstream",
                        str(upstream),
                    ]
                )
            )
            persona = FakeAPI()
            peer = stack.enter_context(company(completion_text="MEMORY_TOOL_READY.", completion_factory=completion))
            api = peer["api"]
            original_request = api.request

            def request(method, path, payload=None):
                if path == "/v1/sync/ack":
                    return 200, {
                        "status": "accepted",
                        "source": "client_reported",
                        "applied_revision": payload["revision"],
                        "received_at": "2026-09-11T00:00:00.000Z",
                    }
                if path.startswith("/v1/sync"):
                    return persona.request(method, path, payload)
                if path.startswith("/v1/skills/"):
                    return 200, {"revision": 0, "skills": []} if path.endswith("personal") else {"skills": []}
                return original_request(method, path, payload)

            api.request = request
            stack.enter_context(patch("myhermes.cli.owner_api", return_value=api))
            stack.enter_context(
                patch(
                    "myhermes.telemetry_cli.CommandMonitoring.record",
                    side_effect=lambda kind, attrs: events.append((kind, attrs)),
                )
            )
            stack.enter_context(
                patch("myhermes.telemetry_cli.CommandMonitoring.finish", return_value={"status": "synthetic"})
            )

            def bridge(api, **kwargs):
                kwargs["allow_local_http"] = True
                return RelayBridge(api, **kwargs)

            stack.enter_context(patch("myhermes.runtime.RelayBridge", side_effect=bridge))
            stack.enter_context(
                patch(
                    "myhermes.runtime.open",
                    side_effect=lambda *_, **_kw: tempfile.TemporaryFile(mode="w+"),
                    create=True,
                )
            )
            run = subprocess.run

            def actual_oneshot(command, *args, **kwargs):
                if command == [str(upstream / ".venv/bin/hermes")]:
                    command = [
                        *command,
                        "chat",
                        "--query-file",
                        "-",
                        "--oneshot",
                        "-Q",
                        "-t",
                        "memory",
                        "--reasoning",
                        "none",
                        "--max-turns",
                        "4",
                        "--run-budget",
                        "60",
                        "--ignore-rules",
                    ]
                    kwargs.pop("stdin", None)
                    kwargs.update(
                        input="Save the two fictional preferences with the memory tool, then reply MEMORY_TOOL_READY.\n",
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=90,
                        cwd=root,
                    )
                    result = run(command, *args, **kwargs)
                    self.assertNotIn(kwargs["env"]["AUXILIARY_MYHERMES_API_KEY"], result.stdout + result.stderr)
                    runtime_output.append(result)
                    return result
                return run(command, *args, **kwargs)

            stack.enter_context(patch("myhermes.runtime._run_managed_child", side_effect=actual_oneshot))
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli.main([*argv, "start"])
            self.assertEqual(code, 0, "Official managed memory session failed; raw conversation suppressed")
            result = json.loads(output.getvalue())
            self.assertEqual(result["persona_after_session"]["status"], "synchronized")
            self.assertEqual(result["runtime_exit_code"], 0)
            self.assertEqual(len(runtime_output), 1)
            self.assertIn("MEMORY_TOOL_READY.", runtime_output[0].stdout)
            self.assertNotIn(MEMORY, output.getvalue())
            self.assertNotIn(USER, output.getvalue())
            self.assertEqual(len(tool_results), 2)
            for message in tool_results:
                self.assertTrue(json.loads(message["content"])["success"])
            self.assertEqual((home / "memories/MEMORY.md").read_text(), MEMORY)
            self.assertEqual((home / "memories/USER.md").read_text(), USER)
            second_home = root / "second-home"
            second_home.mkdir(mode=0o700)
            state = State(root / "second-state")
            try:
                Synchronizer(state, second_home, persona).run()
            finally:
                state.close()
            for name in ("MEMORY.md", "USER.md"):
                self.assertEqual(
                    (second_home / "memories" / name).read_bytes(), (home / "memories" / name).read_bytes()
                )
            self.assertEqual(peer["errors"], [])
            self.assertTrue(all(count == 1 for count in peer["executions"].values()))
            self.assertTrue(any(kind == "runtime_stop" and attrs["outcome"] == "ok" for kind, attrs in events))
            # The published generic categories currently map memory to "other".
            # This test verifies real hook delivery without inventing a new field/category.
            tool_events = [attrs for kind, attrs in events if kind == "tool"]
            self.assertGreaterEqual(len(tool_events), 2)
            for attrs in tool_events:
                self.assertEqual(set(attrs), {"tool_kind", "duration_ms", "outcome"})
                self.assertEqual(attrs["tool_kind"], "other")
                self.assertEqual(attrs["outcome"], "ok")
                self.assertIsInstance(attrs["duration_ms"], int)


if __name__ == "__main__":
    unittest.main()
