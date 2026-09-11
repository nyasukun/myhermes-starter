"""Actual start/child/provider lifecycle must durably capture session personality writes."""

from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from myhermes.cli import main
from myhermes.errors import OfflineError
from myhermes.files import atomic_content
from myhermes.relay_bridge import RelayBridge
from myhermes.state import State
from myhermes.sync import Synchronizer
from test_companion import FakeAPI
from test_relay_bridge import company


class SessionSyncAcceptance(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.directory, self.home, self.upstream = (self.root / value for value in ("state", "home", "upstream"))
        self.argv = ["--state-dir", str(self.directory)]
        self.assertEqual(
            self.command(
                "setup",
                "--server",
                "https://fixture.invalid",
                "--hermes-home",
                str(self.home),
                "--upstream",
                str(self.upstream),
            )[0],
            0,
        )
        self.upstream.mkdir()
        self.persona = FakeAPI()
        self.skill_calls = []
        self.peer = self.stack.enter_context(company(completion_text="SYNTHETIC_SESSION_MEMORY"))
        api = self.peer["api"]
        original = api.request

        def request(method, path, payload=None):
            if path == "/v1/sync/ack":
                return 200, {
                    "status": "accepted",
                    "source": "client_reported",
                    "applied_revision": payload["revision"],
                    "received_at": "2026-09-11T00:00:00.000Z",
                }
            if path.startswith("/v1/sync"):
                return self.persona.request(method, path, payload)
            if path.startswith("/v1/skills/"):
                self.skill_calls.append(path)
                return 200, {"revision": 0, "skills": []} if path.endswith("personal") else {"skills": []}
            return original(method, path, payload)

        api.request = request
        self.api = api
        self.stack.enter_context(patch("myhermes.cli.owner_api", return_value=api))
        self.stack.enter_context(patch("myhermes.telemetry_cli.CommandMonitoring.record"))
        self.stack.enter_context(
            patch("myhermes.telemetry_cli.CommandMonitoring.finish", return_value={"status": "synthetic"})
        )
        self.child = self.upstream / "synthetic-hermes"
        self.stack.enter_context(patch("myhermes.runtime.verify_runtime", return_value=self.child))

        def bridge(api, **kwargs):
            kwargs["allow_local_http"] = True
            return RelayBridge(api, **kwargs)

        self.stack.enter_context(patch("myhermes.runtime.RelayBridge", side_effect=bridge))
        # Only synthetic conversation uses this substitute owner terminal.
        self.stack.enter_context(
            patch("myhermes.runtime.open", side_effect=lambda *_, **_kw: tempfile.TemporaryFile(mode="w+"), create=True)
        )
        self.write_child()

    def command(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([*self.argv, *args])
        self.assertNotIn("SYNTHETIC_SESSION_MEMORY", output.getvalue())
        return code, json.loads(output.getvalue())

    def write_child(self, *, code=0, content="SYNTHETIC_SESSION_MEMORY"):
        self.child.write_text(
            "#!" + sys.executable + "\n"
            "import json,os,pathlib,urllib.request\n"
            "home=pathlib.Path(os.environ['HERMES_HOME'])\n"
            "config=json.loads((pathlib.Path(os.environ['HERMES_MANAGED_DIR'])/'config.yaml').read_text())\n"
            "url=config['model']['base_url']+'/chat/completions'\n"
            "request=urllib.request.Request(url,json.dumps({'model':'economy','messages':[{'role':'user','content':'synthetic'}]}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['AUXILIARY_MYHERMES_API_KEY']})\n"
            "with urllib.request.urlopen(request) as response: value=json.load(response)\n"
            "assert value['choices'][0]['message']['content']=='SYNTHETIC_SESSION_MEMORY'\n"
            "(home/'memories').mkdir(exist_ok=True)\n"
            + "(home/'memories/MEMORY.md').write_text("
            + repr(content)
            + ")\n"
            + "(home/'SOUL.md').write_text('Synthetic session soul')\n"
            + "raise SystemExit("
            + str(code)
            + ")\n"
        )
        self.child.chmod(0o700)

    def test_online_exit_synchronizes_persona_to_another_environment(self):
        code, result = self.command("start")
        self.assertEqual(code, 0)
        self.assertEqual(result["runtime_exit_code"], 0)
        self.assertEqual(result["persona_after_session"]["status"], "synchronized")
        other = self.root / "other-home"
        other.mkdir()
        state = State(self.root / "other-state")
        try:
            Synchronizer(state, other, self.persona).run()
            self.assertEqual((other / "memories/MEMORY.md").read_text(), "SYNTHETIC_SESSION_MEMORY")
            self.assertEqual((other / "SOUL.md").read_text(), "Synthetic session soul")
        finally:
            state.close()
        self.assertEqual(self.peer["errors"], [])

    def test_explicit_offline_exit_queues_and_preserves_existing_payload(self):
        code, result = self.command("start", "--offline")
        self.assertEqual(code, 0)
        self.assertEqual(result["persona_after_session"]["status"], "queued")
        state = State(self.directory)
        first = state.items("pending")
        self.assertEqual(len(first), 1)
        state.close()
        self.write_child(content="SYNTHETIC_SESSION_MEMORY second session")
        self.assertEqual(self.command("start", "--offline")[0], 0)
        state = State(self.directory)
        try:
            self.assertEqual(state.items("pending"), first)
            checkpoint = state.get("session_checkpoint")
            self.assertEqual(checkpoint["base_revision"], 0)
            self.assertEqual(checkpoint["files"]["memories/MEMORY.md"], "SYNTHETIC_SESSION_MEMORY second session")
        finally:
            state.close()
        # A later ordinary start flushes the original immutable update and the
        # later session intent without inventing a fresh base for unseen edits.
        self.assertEqual(self.command("start")[0], 0)
        self.assertEqual(
            self.persona.current["files"]["memories/MEMORY.md"]["content"], "SYNTHETIC_SESSION_MEMORY second session"
        )

    def test_post_session_offline_and_runtime_failure_have_separate_exit_codes(self):
        from myhermes.runtime import start_runtime

        for child_code in (0, 7):
            self.persona.before_error = False
            self.write_child(code=child_code, content="SYNTHETIC_SESSION_MEMORY " + str(child_code))

            def runtime(*args, **kwargs):
                result = start_runtime(*args, **kwargs)
                self.persona.before_error = True
                return result

            with patch("myhermes.cli.start_runtime", side_effect=runtime):
                code, result = self.command("start")
            self.assertEqual(code, 3 if child_code else 4)
            self.assertEqual(result["runtime_exit_code"], child_code)
            self.assertEqual(result["persona_after_session"]["error"], "offline")
            self.assertEqual(result["post_session_exit_code"], 4)
            self.assertIn("skills_after_session", result)
            state = State(self.directory)
            try:
                self.assertIsNotNone(state.get("session_checkpoint"))
                self.assertTrue(state.items("pending"))
            finally:
                state.close()

    def test_interrupt_still_captures_and_runs_both_finalizers(self):
        def interrupted(*_args, **_kwargs):
            atomic_content(self.home, "memories/USER.md", "Synthetic interrupted owner update")
            raise KeyboardInterrupt()

        with patch("myhermes.cli.start_runtime", side_effect=interrupted):
            code, result = self.command("start")
        self.assertEqual(code, 130)
        self.assertEqual(result["runtime_exit_code"], 130)
        self.assertIn("skills_after_session", result)
        self.assertEqual(
            self.persona.current["files"]["memories/USER.md"]["content"], "Synthetic interrupted owner update"
        )

    def test_remote_session_conflict_retains_checkpoint_and_still_finalizes_skills(self):
        import uuid
        from myhermes.runtime import start_runtime

        def concurrent(*args, **kwargs):
            result = start_runtime(*args, **kwargs)
            self.persona.request(
                "POST",
                "/v1/sync",
                {
                    "schema_version": "1",
                    "update_id": str(uuid.uuid4()),
                    "base_revision": 0,
                    "changes": [{"path": "SOUL.md", "content": "Remote concurrent soul"}],
                },
            )
            return result

        with patch("myhermes.cli.start_runtime", side_effect=concurrent):
            code, result = self.command("start")
        self.assertEqual(code, 6)
        self.assertEqual(result["runtime_exit_code"], 0)
        self.assertEqual(result["persona_after_session"]["error"], "sync_conflict")
        self.assertEqual(result["skills_after_session"]["status"], "synchronized")
        self.assertEqual((self.home / "SOUL.md").read_text(), "Synthetic session soul")
        self.assertEqual(self.persona.current["files"]["SOUL.md"]["content"], "Remote concurrent soul")
        state = State(self.directory)
        try:
            self.assertIsNotNone(state.get("session_checkpoint"))
            self.assertEqual(len(state.items("conflict")), 1)
        finally:
            state.close()

    def test_ack_delivery_failure_does_not_mislabel_synchronized_content(self):
        request = self.api.request

        def offline_ack(method, path, payload=None):
            if path == "/v1/sync/ack":
                raise OfflineError()
            return request(method, path, payload)

        self.api.request = offline_ack
        code, result = self.command("start")
        self.assertEqual(code, 0)
        self.assertEqual(result["persona_after_session"]["status"], "synchronized")
        self.assertEqual(result["persona_after_session"]["acknowledgement"], {"status": "deferred", "error": "offline"})
        self.assertEqual(self.persona.current["files"]["memories/MEMORY.md"]["content"], "SYNTHETIC_SESSION_MEMORY")
        state = State(self.directory)
        try:
            self.assertEqual(state.get("sync_ack_pending"), self.persona.current["revision"])
            self.assertIsNone(state.get("session_checkpoint"))
        finally:
            state.close()


if __name__ == "__main__":
    unittest.main()
