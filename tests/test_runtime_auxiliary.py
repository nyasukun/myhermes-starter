"""Pinned core auxiliary routing must use the managed provider without network."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from myhermes.runtime import _managed_config


class RuntimeAuxiliaryAcceptance(unittest.TestCase):
    def test_managed_core_and_shipped_plugin_tasks_have_the_same_explicit_route(self):
        managed = _managed_config("http://127.0.0.1:9/llm/v1")
        for task in ("side_question", "kanban_estimator", "call"):
            with self.subTest(task=task):
                self.assertEqual(managed["auxiliary"].get(task), managed["auxiliary"]["title_generation"])
        self.assertNotIn("synthetic_plugin_task", managed["auxiliary"])

    @unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected pinned Hermes checkout")
    def test_pinned_core_clients_ignore_owner_direct_routes_without_network(self):
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        with tempfile.TemporaryDirectory(prefix="myhermes-auxiliary-test-") as temporary:
            root = Path(temporary).resolve()
            home, overlay = root / "home", root / "managed"
            home.mkdir(mode=0o700)
            overlay.mkdir(mode=0o700)
            managed = _managed_config("http://127.0.0.1:9/llm/v1")
            (overlay / "config.yaml").write_text(json.dumps(managed))
            token = uuid.uuid4().hex
            script = r"""
import ast,json,os,socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

network_attempts=[]
def forbidden(*args, **kwargs):
    network_attempts.append(True)
    raise AssertionError("Network is forbidden in the routing probe")

with patch.object(socket.socket,"connect",forbidden), patch.object(socket.socket,"connect_ex",forbidden), patch.object(socket,"getaddrinfo",forbidden):
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from agent.side_question import SIDE_QUESTION_TASK, _side_question_task_config
    shipped=set()
    for path in ("plugins/kanban/dashboard/plugin_api.py","plugins/teams_pipeline/pipeline.py"):
        tree=ast.parse(Path(path).read_text())
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Name) or node.func.id not in {"call_llm","async_call_llm"}: continue
            for keyword in node.keywords:
                if keyword.arg=="task" and isinstance(keyword.value,ast.Constant): shipped.add(keyword.value.value)
    assert shipped=={"kanban_estimator","call"}
    tasks=sorted({key for key,value in DEFAULT_CONFIG["auxiliary"].items() if isinstance(value,dict)} | {SIDE_QUESTION_TASK} | shipped)
    direct={"provider":"custom","model":"synthetic-direct-model","base_url":"https://direct.example.invalid/v1","api_key":"synthetic-never-used-key","api_mode":"chat_completions"}
    owner={"auxiliary":{task:dict(direct) for task in tasks}}
    owner["auxiliary"]["synthetic_plugin_task"]=dict(direct)
    original=json.dumps(owner)
    path=Path(os.environ["HERMES_HOME"])/"config.yaml"
    path.write_text(original)
    from hermes_cli.config import load_config_readonly
    from agent.auxiliary_client import get_text_auxiliary_client
    config=load_config_readonly()
    results=[]
    for task in tasks:
        client,model=get_text_auxiliary_client(task)
        results.append({"task":task,"managed_endpoint":str(client.base_url).rstrip("/")=="http://127.0.0.1:9/llm/v1","managed_model":model=="economy","managed_key":client.api_key==os.environ["MYHERMES_SESSION_TOKEN"]})
        client.close()
    assert path.read_text()==original
    assert _side_question_task_config()["provider"]==config["auxiliary"]["side_question"]["provider"]
    assert config["auxiliary"]["synthetic_plugin_task"]==direct
    from agent.background_review import _resolve_review_runtime
    parent_runtime={"provider":"myhermes","model":"economy","base_url":"http://127.0.0.1:9/llm/v1","api_key":os.environ["MYHERMES_SESSION_TOKEN"],"api_mode":"chat_completions"}
    parent=SimpleNamespace(provider="myhermes",model="economy",_current_main_runtime=lambda:dict(parent_runtime))
    fork=_resolve_review_runtime(parent,_side_question_task_config())
    fork_managed=all(fork.get(key)==value for key,value in parent_runtime.items())
    assert not network_attempts
    print(json.dumps({"results":results,"plugin_config_preserved":True,"fork_managed":fork_managed}))
"""
            result = subprocess.run(
                [str(upstream / ".venv/bin/python"), "-c", script],
                cwd=upstream,
                env={
                    "PATH": os.defpath,
                    "HOME": str(root),
                    "HERMES_HOME": str(home),
                    "HERMES_MANAGED_DIR": str(overlay),
                    "HERMES_INFERENCE_PROVIDER": "myhermes",
                    "MYHERMES_SESSION_TOKEN": token,
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, "Pinned auxiliary routing probe failed; raw output suppressed")
            self.assertFalse(token in result.stdout + result.stderr, "Probe output must not contain a credential")
            self.assertFalse(
                "synthetic-never-used-key" in result.stdout + result.stderr,
                "Probe output must not contain a credential",
            )
            output = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(output["fork_managed"])
            rows = output["results"]
            self.assertEqual(len(rows), 21)
            for row in rows:
                with self.subTest(task=row["task"]):
                    self.assertTrue(row["managed_endpoint"])
                    self.assertTrue(row["managed_model"])
                    self.assertTrue(row["managed_key"])


if __name__ == "__main__":
    unittest.main()
