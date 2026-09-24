"""MCP protocol and managed companion lifecycle tests with synthetic services."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from myhermes.errors import CompanionError
from myhermes.hermes_mcp import HermesClient, client_config, validate_prompt, worker_environment, write_config

HAS_MCP = importlib.util.find_spec("mcp") is not None
FIXTURE = Path(__file__).parent / "fixtures/mcp_worker.py"


class MCPConfigurationTests(unittest.TestCase):
    def test_configurations_are_valid_and_never_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            client = HermesClient(root / "State with spaces")
            claude = json.loads(client_config(client, "claude-code"))["mcpServers"]["myhermes"]
            codex = tomllib.loads(client_config(client, "codex"))["mcp_servers"]["myhermes"]
            for server in (claude, codex):
                self.assertEqual(server["command"], sys.executable)
                self.assertIn(str(client.state_dir), server["args"])
                self.assertNotIn("env", server)
            self.assertEqual(codex["tool_timeout_sec"], 480)
            target = root / "fragment.json"
            write_config(target, client_config(client, "claude-code"))
            before = target.read_bytes()
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(CompanionError):
                write_config(target, "replacement")
            self.assertEqual(target.read_bytes(), before)
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(CompanionError):
                write_config(link, "replacement")
            self.assertEqual(target.read_bytes(), before)

    def test_bounds_and_client_credential_isolation(self):
        for prompt in ("", " ", "\x00", "\ud800", "x" * 32769, None, 7):
            with self.assertRaises(CompanionError):
                validate_prompt(prompt)
        for kwargs in ({"run_budget": 0}, {"max_turns": 0}, {"max_turns": 101}):
            with self.assertRaises(CompanionError):
                HermesClient(Path("/tmp/state"), **kwargs)
        with self.assertRaises(CompanionError):
            HermesClient(Path("relative"))
        environment = {
            "HOME": "/fictional",
            "PATH": "/bin",
            "OPENAI_API_KEY": "secret",
            "CODEX_HOME": "/other",
            "ANTHROPIC_API_KEY": "secret",
            "AUXILIARY_MYHERMES_API_KEY": "secret",
            "MYHERmES_SESSION_TOKEN": "secret",
            "PYTHONPATH": "/untrusted",
            "NODE_OPTIONS": "untrusted",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(worker_environment(), {"HOME": "/fictional", "PATH": "/bin"})


@unittest.skipUnless(HAS_MCP, "Install the optional [mcp] extra")
class MCPProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    async def test_worker_receives_only_stdin_and_reports_errors_without_body(self):
        from myhermes.mcp_process import run_process

        prompt = "PRIVATE_PROMPT $(touch injection)\n`false`"
        raw = await run_process(
            [sys.executable, str(FIXTURE), "ask"], self.root, worker_environment(), prompt.encode(), 5
        )
        payload = json.loads(raw)
        self.assertEqual(payload["answer"], prompt)
        self.assertNotIn(prompt, payload["args"])
        self.assertEqual(list(self.root.iterdir()), [])
        for task, code in [("fail", "mcp_cli_failed"), ("overflow", "mcp_output_limit")]:
            with self.assertRaises(CompanionError) as error:
                await run_process(
                    [sys.executable, str(FIXTURE), "ask"], self.root, worker_environment(), task.encode(), 5
                )
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("PRIVATE_FAKE_ERROR", str(error.exception))

    async def test_timeout_and_cancellation_reap_process_group(self):
        from myhermes.mcp_process import run_process

        for prompt in ("sleep", "descendant"):
            with self.assertRaises(CompanionError) as error:
                await run_process(
                    [sys.executable, str(FIXTURE), "ask"], self.root, worker_environment(), prompt.encode(), 1
                )
            self.assertEqual(error.exception.code, "mcp_timeout")
            self.assert_stopped()
        (self.root / "fixture-pids.json").unlink()
        task = asyncio.create_task(
            run_process([sys.executable, str(FIXTURE), "ask"], self.root, worker_environment(), b"sleep", 120)
        )
        for _ in range(100):
            if (self.root / "fixture-pids.json").exists():
                break
            await asyncio.sleep(0.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_stopped()

    def assert_stopped(self):
        from test_runtime_commands import RuntimeCommandTermination

        check = RuntimeCommandTermination()
        for pid in json.loads((self.root / "fixture-pids.json").read_text()):
            self.assertFalse(check.live_process(pid))

    async def test_actual_sdk_protocol_and_fixed_arguments(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        # This entrypoint changes only the worker binary to a synthetic fixture;
        # the production FastMCP server, client and subprocess transport are used.
        bootstrap = self.root / "server.py"
        bootstrap.write_text(
            "import asyncio,sys\nfrom pathlib import Path\n"
            "from myhermes.hermes_mcp import HermesClient,serve\n"
            "class FixtureClient(HermesClient):\n"
            " def command(self, action): return [sys.executable, " + repr(str(FIXTURE)) + ", action]\n"
            "asyncio.run(serve(FixtureClient(Path(" + repr(str(self.root / "state")) + "))))\n"
        )
        with open(os.devnull, "w") as errors:
            async with stdio_client(
                StdioServerParameters(command=sys.executable, args=[str(bootstrap)]), errlog=errors
            ) as (read, write):
                async with ClientSession(read, write) as client:
                    init = await client.initialize()
                    self.assertEqual(init.serverInfo.name, "myhermes")
                    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                    self.assertEqual(set(tools), {"hermes_ask", "hermes_status"})
                    self.assertEqual(set(tools["hermes_ask"].inputSchema["properties"]), {"prompt"})
                    self.assertFalse(tools["hermes_ask"].annotations.readOnlyHint)
                    status = await client.call_tool("hermes_status", {})
                    self.assertFalse(status.isError)
                    answer = await client.call_tool("hermes_ask", {"prompt": "synthetic question"})
                    self.assertFalse(answer.isError)
                    self.assertEqual(json.loads(answer.content[0].text)["answer"], "synthetic question")
                    self.assertTrue((await client.call_tool("hermes_ask", {})).isError)
                    failure = await client.call_tool("hermes_ask", {"prompt": "fail"})
                    self.assertTrue(failure.isError)
                    self.assertNotIn("PRIVATE_FAKE_ERROR", str(failure))
                    self.assertTrue((await client.call_tool("hermes_ask", {"prompt": "invalid"})).isError)


@unittest.skipUnless(HAS_MCP, "Install the optional [mcp] extra")
class MCPManagedLifecycleTests(unittest.TestCase):
    def setUp(self):
        from test_session_sync import SessionSyncAcceptance

        self.fixture = SessionSyncAcceptance()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_actual_companion_sync_locks_and_answer_privacy(self):
        from myhermes.hermes_mcp_worker import ask
        from myhermes.files import file_lock

        f = self.fixture
        f.child.write_text(
            "#!" + sys.executable + "\n" + "import os,pathlib,sys\n"
            "prompt=sys.stdin.read()\n"
            'home=pathlib.Path(os.environ["HERMES_HOME"])\n'
            '(home/"memories").mkdir(exist_ok=True)\n'
            '(home/"memories/MEMORY.md").write_text("Synthetic MCP memory")\n'
            'print("PRIVATE_HERMES_ANSWER:"+prompt)\n'
        )
        f.child.chmod(0o700)
        events = []
        with patch("myhermes.telemetry_cli.CommandMonitoring.record", side_effect=lambda *a: events.append(a)):
            answer = ask(f.directory, "PRIVATE_CALLER_PROMPT", budget=10, max_turns=2)
        self.assertEqual(answer["status"], "completed")
        self.assertIn("PRIVATE_CALLER_PROMPT", answer["answer"])
        self.assertNotIn("PRIVATE_CALLER_PROMPT", json.dumps(events))
        self.assertNotIn("PRIVATE_HERMES_ANSWER", json.dumps(events))
        self.assertEqual((f.home / "memories/MEMORY.md").read_text(), "Synthetic MCP memory")
        self.assertIn("Synthetic MCP memory", json.dumps(f.persona.current))
        self.assertGreaterEqual(len(f.skill_calls), 4)
        with file_lock(f.home / ".myhermes-session.lock"):
            with self.assertRaises(CompanionError):
                ask(f.directory, "must not run", budget=10, max_turns=2)
        with file_lock(f.home / ".myhermes-session.lock"):
            pass

    def test_failure_still_runs_post_session_sync(self):
        from myhermes.hermes_mcp_worker import ask

        f = self.fixture
        f.child.write_text(
            "#!" + sys.executable + "\n"
            "import os,pathlib,sys\n"
            "sys.stdin.read()\n"
            'home=pathlib.Path(os.environ["HERMES_HOME"])\n'
            '(home/"SOUL.md").write_text("Synthetic changed soul")\n'
            'print("PRIVATE_UPSTREAM_ERROR",file=sys.stderr)\n'
            "sys.exit(4)\n"
        )
        f.child.chmod(0o700)
        with self.assertRaises(CompanionError) as error:
            ask(f.directory, "synthetic task", budget=10, max_turns=2)
        self.assertEqual(error.exception.code, "mcp_hermes_failed")
        self.assertNotIn("PRIVATE_UPSTREAM_ERROR", str(error.exception))
        self.assertIn("Synthetic changed soul", json.dumps(f.persona.current))
        self.assertGreaterEqual(len(f.skill_calls), 4)

    def test_deferred_application_ack_is_not_complete(self):
        from myhermes.errors import OfflineError
        from myhermes.hermes_mcp_worker import ask

        f = self.fixture
        original = f.api.request

        def request(method, path, payload=None):
            if path == "/v1/sync/ack":
                raise OfflineError()
            return original(method, path, payload)

        async def reply(*args, **kwargs):
            return b"Synthetic final answer"

        with (
            patch.object(f.api, "request", side_effect=request),
            patch("myhermes.hermes_mcp_worker.prompt_process", side_effect=reply),
        ):
            result = ask(f.directory, "synthetic", budget=10, max_turns=2)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["synchronization"]["application_ack"], "deferred")

    def test_cancelled_runtime_still_checkpoints_persona(self):
        from myhermes.hermes_mcp_worker import ask

        f = self.fixture

        async def cancel(*args, **kwargs):
            (f.home / "SOUL.md").write_text("Synthetic interrupted soul")
            raise asyncio.CancelledError()

        with patch("myhermes.hermes_mcp_worker.prompt_process", side_effect=cancel):
            with self.assertRaises(CompanionError):
                ask(f.directory, "synthetic", budget=10, max_turns=2)
        self.assertIn("Synthetic interrupted soul", json.dumps(f.persona.current))
        self.assertGreaterEqual(len(f.skill_calls), 4)
