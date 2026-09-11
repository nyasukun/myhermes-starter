"""The pinned terminal must not inherit or snapshot the local relay bearer."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from myhermes.runtime import _managed_config


@unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected pinned Hermes checkout")
class RuntimeCredentialScrubAcceptance(unittest.TestCase):
    def test_official_terminal_snapshot_and_forced_child_env_exclude_bearer_but_keep_state(self):
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        with tempfile.TemporaryDirectory(prefix="myhermes-terminal-scrub-") as temporary:
            root = Path(temporary).resolve()
            home, overlay, state = root / "home", root / "managed", root / "state"
            for path in (home, overlay, state):
                path.mkdir(mode=0o700)
            managed = _managed_config("http://127.0.0.1:9/llm/v1")
            (overlay / "config.yaml").write_text(json.dumps(managed))
            key = managed["providers"]["myhermes"]["key_env"]
            token = "SYNTHETIC_LOCAL_RELAY_BEARER_CANARY"
            script = r"""
import json, os, socket
from pathlib import Path
from unittest.mock import patch

network=[]
def forbidden(*args, **kwargs):
    network.append(True)
    raise AssertionError("No network is permitted in the terminal probe")

with patch.object(socket.socket,"connect",forbidden), patch.object(socket.socket,"connect_ex",forbidden), patch.object(socket,"getaddrinfo",forbidden):
    from hermes_cli.config import load_config_readonly
    from tools.environments.local import LocalEnvironment, _make_run_env, _sanitize_subprocess_env, hermes_subprocess_env, build_subprocess_env
    key=load_config_readonly()["providers"]["myhermes"]["key_env"]
    token=os.environ[key]
    forced="_HERMES_FORCE_"+key
    children=[
        _make_run_env({}),
        _make_run_env({forced:token}),
        _sanitize_subprocess_env(os.environ,{forced:token}),
        hermes_subprocess_env(inherit_credentials=False),
        hermes_subprocess_env(inherit_credentials=True),
        build_subprocess_env(extra={forced:token}),
    ]
    assert all(token not in child.values() and key not in child and forced not in child for child in children), "Bearer reached a spawned-child environment"
    assert all(child.get("MYHERMES_STATE_DIR")==os.environ["MYHERMES_STATE_DIR"] for child in children)
    original=LocalEnvironment._run_bash
    def nonlogin(self, command, *, login=False, timeout=120, stdin_data=None):
        # Keep official scrub/snapshot/execute; skip actual owner login rc files.
        return original(self,command,login=False,timeout=timeout,stdin_data=stdin_data)
    with patch.object(LocalEnvironment,"_run_bash",nonlogin):
        terminal=LocalEnvironment(cwd=os.environ["MYHERMES_STATE_DIR"],timeout=10)
        try:
            assert terminal._snapshot_ready
            snapshot=Path(terminal._snapshot_path)
            assert token not in snapshot.read_text(), "Bearer persisted in official shell snapshot"
            assert snapshot.stat().st_mode & 0o777 == 0o600
            command='test -n "${MYHERMES_STATE_DIR:-}" && test -z "${'+key+':-}" && test -z "${'+forced+':-}" && printf "STATE_ONLY_READY"'
            result=terminal.execute(command,timeout=10)
            assert result.get("returncode")==0 and "STATE_ONLY_READY" in result.get("output","")
            assert token not in json.dumps(result)
            assert token not in snapshot.read_text(), "Bearer persisted after a terminal call"
        finally:
            terminal.cleanup()
    assert not network
print("OFFICIAL_TERMINAL_STATE_ONLY")
"""
            result = subprocess.run(
                [str(upstream / ".venv/bin/python"), "-c", script],
                cwd=upstream,
                env={
                    "PATH": os.defpath,
                    "HERMES_HOME": str(home),
                    "HERMES_MANAGED_DIR": str(overlay),
                    "MYHERMES_STATE_DIR": str(state),
                    key: token,
                    "_HERMES_FORCE_" + key: token,
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotIn(token, result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, "Pinned terminal credential isolation failed; raw output suppressed")
            self.assertIn("OFFICIAL_TERMINAL_STATE_ONLY", result.stdout)


if __name__ == "__main__":
    unittest.main()
