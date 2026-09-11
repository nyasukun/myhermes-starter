"""Environment provenance stays process-only and preserves the owner's hint."""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from myhermes.errors import CompanionError
from myhermes.runtime import _environment_hint, relay_runtime_session
import test_runtime_relay as runtime_fixture


class RuntimeEnvironmentAcceptance(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_fixture.RuntimeRelayAcceptance()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_owner_hint_precedence_exact_text_and_process_only_private_context(self):
        f = self.fixture
        original = b'# Owner comments remain\nagent:\n  environment_hint: "CONFIG_OWNER_HINT"\n'
        (f.home / "config.yaml").write_bytes(original)
        (f.home / "SOUL.md").write_text("Synthetic soul remains exact")
        (f.home / "memories").mkdir()
        (f.home / "memories/MEMORY.md").write_text("Synthetic memory remains exact")
        saved = {p: p.read_bytes() for p in (f.home / "config.yaml", f.home / "SOUL.md", f.home / "memories/MEMORY.md")}
        owner = "  OWNER_ENV_HINT\n  preserve this exact indentation  "
        events, captured = [], io.StringIO()
        with (
            patch.dict(os.environ, {"HERMES_ENVIRONMENT_HINT": owner}),
            redirect_stdout(captured),
            redirect_stderr(captured),
        ):
            with relay_runtime_session(
                f.config,
                api=f.company["api"],
                allow_local_http=True,
                on_activity=lambda kind, attrs: events.append((kind, attrs)),
            ) as (_, environment):
                hint = environment["HERMES_ENVIRONMENT_HINT"]
                token = environment["MYHERMES_SESSION_TOKEN"]
                self.assertTrue(hint.startswith(owner + "\n\n"))
                self.assertNotIn("CONFIG_OWNER_HINT", hint)
                self.assertIn("installation_id=" + f.company["installation"], hint)
                self.assertIn("not a remote terminal backend", hint)
                self.assertIn("past observations, not current execution settings", hint)
                self.assertNotIn(token, hint)
                self.assertEqual(os.environ["HERMES_ENVIRONMENT_HINT"], owner)
                for path in f.home.rglob("*"):
                    if path.is_file():
                        data = path.read_bytes()
                        self.assertNotIn(owner.encode(), data)
                        self.assertNotIn(hint.encode(), data)
                        self.assertNotIn(token.encode(), data)
            self.assertNotIn("HERMES_ENVIRONMENT_HINT", environment)
            self.assertEqual(os.environ["HERMES_ENVIRONMENT_HINT"], owner)
        self.assertEqual(captured.getvalue(), "")
        self.assertEqual(events, [])
        for path, data in saved.items():
            self.assertEqual(path.read_bytes(), data)

    def test_blank_environment_falls_back_to_exact_config_hint_and_actual_host(self):
        config = {"agent": {"environment_hint": "  CONFIG_OWNER_HINT\noriginal text  "}}
        with (
            patch.dict(os.environ, {"HERMES_ENVIRONMENT_HINT": " \n"}),
            patch("myhermes.runtime.operating_system", return_value="ubuntu"),
        ):
            hint = _environment_hint(config, self.fixture.company["installation"])
        self.assertTrue(hint.startswith(config["agent"]["environment_hint"] + "\n\n"))
        self.assertIn("Hermes host OS=ubuntu", hint)
        self.assertIn("record an observed remote backend OS/path separately", hint)
        self.assertEqual(config["agent"]["environment_hint"], "  CONFIG_OWNER_HINT\noriginal text  ")

    def test_nontext_owner_config_is_rejected_without_rendering_it(self):
        with patch.dict(os.environ, {"HERMES_ENVIRONMENT_HINT": ""}):
            with self.assertRaises(CompanionError) as caught:
                _environment_hint(
                    {"agent": {"environment_hint": {"private": "SYNTHETIC_CANARY"}}},
                    self.fixture.company["installation"],
                )
        self.assertEqual(caught.exception.code, "runtime_config_rejected")
        self.assertNotIn("SYNTHETIC_CANARY", str(caught.exception))

    @unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected pinned Hermes checkout")
    def test_pinned_prompt_builder_retains_owner_hint_and_remote_backend_authority(self):
        f = self.fixture
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        f.config["upstream"] = str(upstream)
        original = 'agent:\n  environment_hint: "CONFIG_OWNER_HINT"\n'
        (f.home / "config.yaml").write_text(original)
        for owner in ("  ENV_OWNER_HINT\nkeep this exact text  ", ""):
            with (
                self.subTest(source="environment" if owner else "config"),
                patch.dict(os.environ, {"HERMES_ENVIRONMENT_HINT": owner}),
            ):
                with f.session() as (_, environment):
                    script = (
                        "import os\nfrom unittest.mock import patch\n"
                        "from agent import prompt_builder as pb\n"
                        "from hermes_cli.config import load_config_readonly\n"
                        "hint=os.environ['HERMES_ENVIRONMENT_HINT']\n"
                        "assert load_config_readonly()['agent']['environment_hint']=='CONFIG_OWNER_HINT'\n"
                        "assert ('ENV_OWNER_HINT' in hint) != ('CONFIG_OWNER_HINT' in hint)\n"
                        "with patch.object(pb,'_tenv_read',return_value='local'),patch.object(pb,'is_wsl',return_value=False):\n"
                        " local=pb.build_environment_hints()\n"
                        " assert 'Host: ' in local and hint.strip() in local\n"
                        "with patch.object(pb,'_tenv_read',return_value='ssh'),patch.object(pb,'_probe_remote_backend',return_value='  OS: Linux synthetic\\n  Home: /remote/example\\n  Working directory: /remote/project'),patch.object(pb,'is_wsl',return_value=False):\n"
                        " remote=pb.build_environment_hints()\n"
                        " assert 'Terminal backend: ssh' in remote and 'OS: Linux synthetic' in remote\n"
                        " assert 'only the following backend state matters' in remote\n"
                        " assert 'Host: ' not in remote and hint.strip() in remote\n"
                        " assert 'verify the actual terminal backend, OS and target paths' in remote\n"
                        " assert 'source installation_id=' in remote\n"
                        "assert os.environ['MYHERMES_SESSION_TOKEN'] not in local+remote\n"
                        "print('verified environment context')\n"
                    )
                    result = subprocess.run(
                        [str(upstream / ".venv/bin/python"), "-c", script],
                        env=environment,
                        cwd=upstream,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(
                        result.returncode, 0, "Pinned environment hint probe failed; raw output suppressed"
                    )
                    self.assertEqual(result.stdout.strip(), "verified environment context")
                    self.assertNotIn(environment["HERMES_ENVIRONMENT_HINT"], result.stdout + result.stderr)
                    self.assertNotIn(environment["MYHERMES_SESSION_TOKEN"], result.stdout + result.stderr)
        self.assertEqual((f.home / "config.yaml").read_text(), original)


if __name__ == "__main__":
    unittest.main()
