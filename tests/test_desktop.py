"""Managed Desktop policy, pinned adapter, and real child-to-sync boundaries."""

import ast
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from myhermes import cli, desktop_policy
from myhermes.desktop import desktop_environment
from myhermes.desktop_adapter import PYTHON_EDITS, adapted_source, source_hashes
from myhermes.errors import CompanionError
import test_session_sync


class DesktopPolicyAcceptance(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.env = {
            "HERMES_HOME": str(self.root), "MYHERMES_DESKTOP_HOME": str(self.root),
            "HERMES_MANAGED_DIR": str(self.root / "overlay"),
            "MYHERMES_DESKTOP_RELAY_URL": "http://127.0.0.1:18999/v1",
            "AUXILIARY_MYHERMES_API_KEY": "synthetic-session-key",
        }
        self.stack.enter_context(patch.dict(os.environ, self.env, clear=True))

    def test_provider_inventory_and_explicit_overrides(self):
        self.assertEqual([row["slug"] for row in desktop_policy.inventory()["providers"]], ["myhermes"])
        self.assertEqual(desktop_policy.resolve()["base_url"], self.env["MYHERMES_DESKTOP_RELAY_URL"])
        for overrides in (
            {"requested": "openrouter"}, {"requested": "custom"}, {"requested": "moa"},
            {"target_model": "other-model"}, {"explicit_base_url": "https://provider.invalid/v1"},
            {"explicit_api_key": "other-key"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                desktop_policy.resolve(**overrides)

    def test_detached_bundle_and_different_home_fail_closed(self):
        for name in self.env:
            with self.subTest(name=name), patch.dict(os.environ, {name: ""}), self.assertRaises(ValueError):
                desktop_policy.session()
        with self.assertRaises(ValueError):
            desktop_policy.guard_home(self.root / "different")
        with self.assertRaises(ValueError):
            desktop_policy.guard_profile("personal")
        desktop_policy.guard_home(self.root)

    def test_auxiliary_tasks_cannot_override_the_managed_provider(self):
        self.assertEqual(desktop_policy.auxiliary_route(None, None, None, None)[:2], ("myhermes", "economy"))
        with self.assertRaises(ValueError):
            desktop_policy.auxiliary_route("anthropic", None, None, None)

    def test_desktop_hooks_are_scrubbed_and_paths_are_bound(self):
        overlay = self.root / "overlay"
        overlay.mkdir()
        (overlay / "config.yaml").write_text('{"model":{"base_url":"http://127.0.0.1:18999/v1"}}')
        env = {**self.env, "HERMES_DESKTOP_REMOTE_URL": "https://unmanaged.invalid",
               "HERMES_DESKTOP_BOOT_FAKE": "1", "NODE_OPTIONS": "--require bad.js", "PYTHONPATH": "/unmanaged",
               "_HERMES_FORCE_MYHERMES_DESKTOP_HOME": "/unmanaged"}
        result = desktop_environment({"hermes_home": str(self.root), "upstream": str(self.root / "runtime")},
                                     env, self.root / "adapter")
        self.assertNotIn("HERMES_DESKTOP_REMOTE_URL", result)
        self.assertNotIn("HERMES_DESKTOP_BOOT_FAKE", result)
        self.assertNotIn("NODE_OPTIONS", result)
        self.assertNotIn("_HERMES_FORCE_MYHERMES_DESKTOP_HOME", result)
        self.assertEqual(result["PYTHONPATH"], str(self.root / "adapter"))
        self.assertEqual(result["MYHERMES_DESKTOP_HOME"], str(self.root))

    def test_cli_alias_uses_the_existing_managed_start_boundary(self):
        for arguments in (["desktop"], ["start", "--desktop"]):
            args = cli.parser().parse_args(arguments)
            self.assertEqual(args.command, "start")
            self.assertTrue(args.desktop)
        self.assertFalse(cli.parser().parse_args(["start"]).desktop)

    @unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected pinned source")
    def test_adapter_executes_pinned_picker_resolver_and_home_guards(self):
        # Execute the adapted public functions without importing the rest of the
        # agent or its optional network integrations. Their actual pinned bodies
        # are parsed/compiled, including the inserted entry guards.
        upstream = Path(PINNED_SOURCE)
        with patch.dict(sys.modules, {"myhermes_desktop_policy": desktop_policy}):
            for name in source_hashes():
                source = adapted_source(name, (upstream / name).read_bytes())
                if name.endswith(".py"):
                    tree = ast.parse(source)
                    compile(tree, name, "exec")
                    for node in tree.body:
                        if isinstance(node, ast.FunctionDef) and node.name in PYTHON_EDITS[name]:
                            node.decorator_list = []
                            module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
                                ast.alias(name="annotations")], level=0), node], type_ignores=[])
                            namespace = {}
                            exec(compile(ast.fix_missing_locations(module), name, "exec"), namespace)
                            if node.name == "build_models_payload":
                                self.assertEqual(namespace[node.name](None, include_unconfigured=True)["provider"],
                                                 "myhermes")
                            if node.name == "resolve_runtime_provider":
                                self.assertEqual(namespace[node.name]()["model"], "economy")
                                with self.assertRaises(ValueError):
                                    namespace[node.name](requested="openai-codex")
                with self.assertRaises(CompanionError):
                    adapted_source(name, source + b"\n")


# Capture before the per-test environment is cleared; this is public source,
# never the owner's Hermes home or config.
PINNED_SOURCE = os.getenv("MYHERMES_TEST_UPSTREAM")


@unittest.skipUnless(
    os.getenv("MYHERMES_TEST_UPSTREAM") and os.getenv("MYHERMES_TEST_DESKTOP_SOURCE"),
    "Requires the explicitly selected pinned interpreter and prepared adapter source",
)
class PinnedDesktopBackendAcceptance(unittest.TestCase):
    def test_real_backend_picker_provider_auxiliary_and_user_memory(self):
        source = Path(os.environ["MYHERMES_TEST_DESKTOP_SOURCE"]).resolve()
        interpreter = Path(os.environ["MYHERMES_TEST_UPSTREAM"]) / ".venv/bin/python"
        with tempfile.TemporaryDirectory(prefix="myhermes-backend-fixture-") as temporary:
            root = Path(temporary).resolve()
            home, overlay = root / "home", root / "overlay"
            home.mkdir()
            overlay.mkdir()
            from myhermes.files import atomic_json
            from myhermes.runtime import _managed_config

            atomic_json(overlay / "config.yaml", _managed_config("http://127.0.0.1:18999/v1"))
            env = {
                "HOME": str(root), "PATH": os.defpath, "HERMES_HOME": str(home),
                "MYHERMES_DESKTOP_HOME": str(home), "HERMES_MANAGED_DIR": str(overlay),
                "MYHERMES_DESKTOP_RELAY_URL": "http://127.0.0.1:18999/v1",
                "AUXILIARY_MYHERMES_API_KEY": "synthetic-key", "PYTHONPATH": str(source),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            code = r'''
import asyncio,json,os,socket
from pathlib import Path
attempts=[]
def no_network(*args,**kwargs):
    attempts.append(True)
    raise AssertionError('This fixture does not permit any network connection')
socket.socket.connect=no_network
from hermes_cli import inventory
assert Path(inventory.__file__).is_relative_to(Path(os.environ['PYTHONPATH']))
from hermes_cli.web_routers.models import get_model_options
payload=asyncio.run(get_model_options(include_unconfigured=True,refresh=True))
assert [row['slug'] for row in payload['providers']]==['myhermes']
from hermes_cli.runtime_provider import resolve_runtime_provider
assert resolve_runtime_provider()['base_url']=='http://127.0.0.1:18999/v1'
for provider in ['openrouter','openai-codex','custom','anthropic','moa']:
    try: resolve_runtime_provider(requested=provider)
    except ValueError: pass
    else: raise AssertionError('Unmanaged provider admitted')
from agent.auxiliary_client import resolve_provider_client
client,model=resolve_provider_client('myhermes')
assert model=='economy' and str(client.base_url)=='http://127.0.0.1:18999/v1/'
client.close()
from hermes_cli.web_server_config import _apply_model_assignment_sync
from fastapi import HTTPException
try: _apply_model_assignment_sync('main','openrouter','economy','','','')
except HTTPException as error: assert error.status_code==403
else: raise AssertionError('Alternate provider setting admitted')
from tools.memory_tool import memory_tool,load_on_disk_store
saved=json.loads(memory_tool(action='add',target='user',content='A fictional owner prefers concise replies.',
                             store=load_on_disk_store()))
assert saved['success']
assert 'fictional owner' in (Path(os.environ['HERMES_HOME'])/'memories/USER.md').read_text()
assert not attempts
print('PINNED_DESKTOP_BACKEND_OK')
'''
            result = subprocess.run([str(interpreter), "-c", code], cwd=source, env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, "Pinned backend fixture failed: " + result.stderr[-5000:])
            self.assertIn("PINNED_DESKTOP_BACKEND_OK", result.stdout)


class DesktopSessionAcceptance(unittest.TestCase):
    def setUp(self):
        self.fixture = test_session_sync.SessionSyncAcceptance()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.stack.enter_context(patch("myhermes.desktop.verify_desktop", return_value=(
            self.fixture.upstream, self.fixture.child
        )))

    def test_gui_quit_captures_memory_and_syncs_through_the_existing_relay(self):
        t = self.fixture
        code, result = t.command("desktop")
        self.assertEqual(code, 0)
        self.assertEqual(result["persona_after_session"]["status"], "synchronized")
        self.assertEqual(t.persona.current["files"]["memories/MEMORY.md"]["content"], "SYNTHETIC_SESSION_MEMORY")

    def test_backend_is_stopped_before_final_user_memory_snapshot(self):
        t = self.fixture
        backend = t.root / "backend.py"
        backend.write_text(
            "import os,signal,time\nfrom pathlib import Path\n"
            "root=Path(os.environ['HERMES_HOME'])\n"
            "(root/'memories').mkdir(exist_ok=True)\n"
            "def stop(*_):\n"
            " (root/'memories/USER.md').write_text('Fictional owner prefers concise replies.')\n"
            " raise SystemExit(0)\n"
            "signal.signal(signal.SIGTERM,stop)\n"
            "(root/'backend-ready').touch()\n"
            "while True: time.sleep(.01)\n"
        )
        t.child.write_text(
            "#!" + sys.executable + "\nimport os,subprocess,time\nfrom pathlib import Path\n"
            + "subprocess.Popen([" + repr(sys.executable) + "," + repr(str(backend)) + "])\n"
            "root=Path(os.environ['HERMES_HOME'])\n"
            "while not (root/'backend-ready').exists(): time.sleep(.01)\n"
        )
        code, result = t.command("start", "--desktop")
        self.assertEqual(code, 0)
        self.assertEqual(result["persona_after_session"]["status"], "synchronized")
        self.assertEqual(t.persona.current["files"]["memories/USER.md"]["content"],
                         "Fictional owner prefers concise replies.")
