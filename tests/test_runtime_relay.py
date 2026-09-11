"""Managed launch acceptance; synthetic config/credentials, real child process."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

from myhermes.errors import CompanionError
from myhermes.relay_bridge import RelayBridge
from myhermes.runtime import _launch_preflight, relay_runtime_session, start_runtime
from test_relay_bridge import company


class RuntimeRelayAcceptance(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = self.stack.enter_context(
            tempfile.TemporaryDirectory(
                prefix="myhermes-runtime-test-", dir="/private/tmp" if Path("/private/tmp").exists() else "/tmp"
            )
        )
        self.root = Path(root)
        self.home, self.upstream = self.root / "home", self.root / "upstream"
        self.home.mkdir()
        self.upstream.mkdir()
        self.company = self.stack.enter_context(company())
        self.config = {"upstream": str(self.upstream), "hermes_home": str(self.home)}
        self.command = self.upstream / "fixture-hermes"
        self.command.write_text("#!" + sys.executable + "\n")
        self.command.chmod(0o700)
        self.stack.enter_context(patch("myhermes.runtime.verify_runtime", return_value=self.command))

    def session(self):
        return relay_runtime_session(
            self.config, api=self.company["api"], state_directory=self.root / "state", allow_local_http=True
        )

    def test_process_only_credential_overlay_permissions_and_original_config_preserved(self):
        original = b"# Owner formatting remains intact\nmodel:\n  default: old-model\n  provider: other\nskills:\n  inline_shell: false\n"
        (self.home / "config.yaml").write_bytes(original)
        injected = {
            "OPENROUTER_API_KEY": "synthetic-company-key",
            "openrouter_api_key": "synthetic-lowercase-key",
            "OpenRouter_Api_Key": "synthetic-mixed-case-key",
            "_HERMES_FORCE_OPENROUTER_API_KEY": "synthetic-forced-provider-key",
            "_hermes_force_openrouter_api_key": "synthetic-forced-lowercase-provider-key",
            "OPENAI_BASE_URL": "https://outside.example.invalid",
            "_HERMES_FORCE_OPENAI_BASE_URL": "https://forced.example.invalid",
            "ANTHROPIC_API_KEY": "synthetic-other-key",
            "_HERMES_FORCE_ANTHROPIC_API_KEY": "synthetic-forced-other-key",
            "_HERMES_FORCE_LANGFUSE_SECRET_KEY": "synthetic-forced-tracing-key",
            "_HERMES_FORCE_HERMES_LANGFUSE_PUBLIC_KEY": "synthetic-forced-tracing-id",
            "_HERMES_FORCE_OTEL_EXPORTER_OTLP_HEADERS": "synthetic-forced-otel-header",
            "HERMES_IGNORE_USER_CONFIG": "1",
            "HERMES_PROFILE": "unwanted-profile",
            "AUXILIARY_MYHERMES_API_KEY": "synthetic-stale-token",
            "auxiliary_myhermes_api_key": "synthetic-stale-lowercase-token",
            "_HERMES_FORCE_AUXILIARY_MYHERMES_API_KEY": "synthetic-forced-stale-token",
            "MYHERMES_SESSION_TOKEN": "synthetic-legacy-stale-token",
            "_HERMES_FORCE_MYHERMES_SESSION_TOKEN": "synthetic-forced-legacy-token",
        }
        with patch.dict(os.environ, injected):
            with self.session() as (command, environment):
                self.assertEqual(command, self.command)
                self.assertEqual(environment["HERMES_HOME"], str(self.home))
                self.assertEqual(environment["MYHERMES_STATE_DIR"], str(self.root / "state"))
                self.assertEqual(environment["HERMES_STREAM_RETRIES"], "0")
                self.assertEqual(environment["HERMES_INFERENCE_PROVIDER"], "myhermes")
                self.assertNotEqual(environment["AUXILIARY_MYHERMES_API_KEY"], injected["AUXILIARY_MYHERMES_API_KEY"])
                for key in injected:
                    if key != "AUXILIARY_MYHERMES_API_KEY":
                        self.assertFalse(
                            key in environment, "Blocked inherited variable reached the managed child: " + key
                        )
                overlay = Path(environment["HERMES_MANAGED_DIR"])
                contents = (overlay / "config.yaml").read_text()
                self.assertNotIn(environment["AUXILIARY_MYHERMES_API_KEY"], contents)
                self.assertNotIn("synthetic-company-key", contents)
                self.assertEqual(overlay.stat().st_mode & 0o777, 0o700)
                self.assertEqual((overlay / "config.yaml").stat().st_mode & 0o777, 0o600)
                managed = json.loads(contents)
                self.assertEqual(managed["providers"]["myhermes"]["key_env"], "AUXILIARY_MYHERMES_API_KEY")
                self.assertEqual(managed["agent"]["api_max_retries"], 1)
                self.assertIs(managed["skills"]["inline_shell"], False)
                self.assertEqual(managed["auxiliary"]["transient_retries"], 0)
                self.assertIsNone(managed["fallback_model"])
                self.assertEqual((self.home / "config.yaml").read_bytes(), original)
                self.assertEqual(os.environ["AUXILIARY_MYHERMES_API_KEY"], "synthetic-stale-token")
                endpoint = urllib.parse.urlsplit(managed["providers"]["myhermes"]["api"])
            self.assertNotIn("AUXILIARY_MYHERMES_API_KEY", environment)
            self.assertFalse(overlay.exists())
            with self.assertRaises(OSError):
                socket.create_connection((endpoint.hostname, endpoint.port), timeout=0.1)
        self.assertEqual((self.home / "config.yaml").read_bytes(), original)

    def test_real_fake_entrypoint_reads_env_and_calls_bridge_without_argv_secret(self):
        self.command.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, pathlib, urllib.request\n"
            "config = json.loads((pathlib.Path(os.environ['HERMES_MANAGED_DIR']) / 'config.yaml').read_text())\n"
            "provider = config['providers']['myhermes']\n"
            "request = urllib.request.Request(provider['api'] + '/models', headers={'Authorization': 'Bearer ' + os.environ[provider['key_env']]})\n"
            "with urllib.request.urlopen(request) as response: result = json.load(response)\n"
            "print(json.dumps({'model': result['data'][0]['id'], 'home': os.environ['HERMES_HOME'], 'api_retry': config['agent']['api_max_retries'], 'stream_retry': os.environ['HERMES_STREAM_RETRIES']}))\n"
        )
        with self.session() as (command, environment):
            result = subprocess.run([str(command)], env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, "Synthetic child entrypoint failed")
            self.assertNotIn(environment["AUXILIARY_MYHERMES_API_KEY"], result.stdout + result.stderr)
            self.assertNotIn(environment["AUXILIARY_MYHERMES_API_KEY"], str(result.args))
            metadata = json.loads(result.stdout)
            self.assertEqual(
                metadata, {"model": "economy", "home": str(self.home), "api_retry": 1, "stream_retry": "0"}
            )
        self.assertEqual(len(self.company["calls"]), 1)

    def test_overlay_is_removed_on_child_failure(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
            with self.session() as (_, environment):
                overlay = Path(environment["HERMES_MANAGED_DIR"])
                raise RuntimeError("synthetic crash")
        self.assertFalse(overlay.exists())
        self.assertNotIn("AUXILIARY_MYHERMES_API_KEY", environment)
        self.assertEqual(list((self.home / ".myhermes-runtime").iterdir()), [])

    def test_dotenv_presence_rejected_without_reading_or_changing_it(self):
        for path in (self.home / ".env", self.home / ".op.env", self.upstream / ".env"):
            path.write_text("synthetic unreadable credential data")
            path.chmod(0)
            try:
                with self.assertRaises(CompanionError) as raised:
                    _launch_preflight(self.home, self.upstream)
                self.assertEqual(raised.exception.code, "runtime_dotenv_unsupported")
                self.assertEqual(path.stat().st_mode & 0o777, 0)
            finally:
                path.chmod(0o600)
                path.unlink()
        (self.home / ".env").symlink_to(self.root / "missing")
        with self.assertRaises(CompanionError) as raised:
            _launch_preflight(self.home, self.upstream)
        self.assertEqual(raised.exception.code, "runtime_dotenv_unsupported")

    def test_external_sources_reserved_aliases_and_invalid_yaml_rejected(self):
        path = self.home / "config.yaml"
        for content, code in (
            ("secrets:\n  fixture:\n    enabled: true\n", "runtime_external_secrets_unsupported"),
            ("providers:\n  myhermes:\n    api: https://outside.example.invalid\n", "runtime_provider_reserved"),
            ("providers:\n  other:\n    name: MYHERMES\n", "runtime_provider_reserved"),
            ("custom_providers:\n  - name: custom:myhermes\n", "runtime_provider_reserved"),
            ("broken: [", "runtime_config_rejected"),
        ):
            path.write_text(content)
            with self.assertRaises(CompanionError) as raised:
                _launch_preflight(self.home, self.upstream)
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(path.read_text(), content)

    def test_config_symlink_hardlink_fifo_rejected(self):
        path = self.home / "config.yaml"
        target = self.root / "target"
        target.write_text("{}")
        path.symlink_to(target)
        with self.assertRaises(CompanionError):
            _launch_preflight(self.home, self.upstream)
        path.unlink()
        os.link(target, path)
        with self.assertRaises(CompanionError):
            _launch_preflight(self.home, self.upstream)
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(CompanionError):
            _launch_preflight(self.home, self.upstream)

    def test_monitoring_plugin_fixed_source_opt_ins_and_modified_file_preserved(self):
        original = "plugins:\n  enabled: [owner-plugin]\n  disabled: [unrelated-plugin]\n"
        (self.home / "config.yaml").write_text(original)
        events = []
        for _ in range(2):
            with relay_runtime_session(
                self.config,
                api=self.company["api"],
                allow_local_http=True,
                on_activity=lambda kind, attrs: events.append((kind, attrs)),
            ) as (_, environment):
                overlay = json.loads((Path(environment["HERMES_MANAGED_DIR"]) / "config.yaml").read_text())
                self.assertEqual(overlay["plugins"]["enabled"], ["owner-plugin", "myhermes-monitoring"])
                self.assertEqual(environment["MYHERMES_MONITORING_URL"].split("/v1")[1], "/myhermes/tool-events")
                installed = self.home / "plugins/myhermes-monitoring/__init__.py"
                self.assertNotIn(environment["AUXILIARY_MYHERMES_API_KEY"], installed.read_text())
                self.assertEqual((self.home / "config.yaml").read_text(), original)
        installed.write_text("# preserved user edit\n")
        with self.assertRaises(CompanionError) as raised:
            with relay_runtime_session(
                self.config, api=self.company["api"], allow_local_http=True, on_activity=lambda *_: None
            ):
                self.fail("Modified plugin was activated")
        self.assertEqual(raised.exception.code, "runtime_plugin_reserved")
        self.assertEqual(installed.read_text(), "# preserved user edit\n")

    def test_start_and_stop_activity_around_actual_child_and_failure(self):
        def bridge(api, **options):
            options["allow_local_http"] = True
            return RelayBridge(api, **options)

        for exit_code in (0, 7):
            self.command.write_text("#!" + sys.executable + "\nraise SystemExit(" + str(exit_code) + ")\n")
            events = []
            with tempfile.TemporaryFile(mode="w+") as terminal:
                with (
                    patch("myhermes.runtime.open", return_value=terminal, create=True),
                    patch("myhermes.runtime.RelayBridge", side_effect=bridge),
                ):
                    result = start_runtime(
                        self.config,
                        api=self.company["api"],
                        on_activity=lambda kind, attrs: events.append((kind, attrs)),
                    )
            self.assertEqual(result["runtime_exit_code"], exit_code)
            self.assertEqual(events[0], ("runtime_start", {"outcome": "ok"}))
            self.assertEqual(events[1][0], "runtime_stop")
            self.assertEqual(events[1][1]["outcome"], "failed" if exit_code else "ok")
            self.assertGreaterEqual(events[1][1]["duration_ms"], 0)
        events = []
        with tempfile.TemporaryFile(mode="w+") as terminal:
            with (
                patch("myhermes.runtime.open", return_value=terminal, create=True),
                patch("myhermes.runtime.RelayBridge", side_effect=bridge),
                patch("myhermes.runtime._run_managed_child", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    start_runtime(
                        self.config,
                        api=self.company["api"],
                        on_activity=lambda kind, attrs: events.append((kind, attrs)),
                    )
        self.assertEqual(events[-1][1]["outcome"], "cancelled")
        self.assertEqual(list((self.home / ".myhermes-runtime").iterdir()), [])

    @unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected pinned Hermes checkout")
    def test_official_plugin_discovery_and_narrow_post_tool_hook(self):
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        self.config["upstream"] = str(upstream)
        original = "plugins:\n  enabled: []\n"
        (self.home / "config.yaml").write_text(original)
        events = []
        with relay_runtime_session(
            self.config,
            api=self.company["api"],
            allow_local_http=True,
            on_activity=lambda kind, attrs: events.append((kind, attrs)),
        ) as (_, environment):
            script = (
                "from hermes_cli.plugins import discover_plugins,has_hook,invoke_hook\n"
                "class Forbidden:\n"
                " def __str__(self): raise AssertionError('content accessed')\n"
                " def __repr__(self): raise AssertionError('content accessed')\n"
                "discover_plugins()\n"
                "assert has_hook('post_tool_call')\n"
                "invoke_hook('post_tool_call',tool_name='read_file',duration_ms=4,status='ok',args=Forbidden(),result=Forbidden(),error_message=Forbidden())\n"
                "invoke_hook('post_tool_call',tool_name='private-name-fixture',duration_ms=9,status='error',args=Forbidden(),result=Forbidden())\n"
                "print('verified narrow hook')\n"
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
                result.returncode, 0, "Pinned plugin discovery failed; raw output intentionally suppressed"
            )
            self.assertNotIn(environment["AUXILIARY_MYHERMES_API_KEY"], result.stdout + result.stderr)
            self.assertIn("verified narrow hook", result.stdout)
        self.assertEqual(
            events,
            [
                ("tool", {"tool_kind": "read", "duration_ms": 4, "outcome": "ok"}),
                ("tool", {"tool_kind": "other", "duration_ms": 9, "outcome": "failed"}),
            ],
        )
        self.assertEqual((self.home / "config.yaml").read_text(), original)

    @unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected pinned Hermes checkout")
    def test_pinned_skill_preview_never_expands_inline_shell_from_owner_config(self):
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        self.config["upstream"] = str(upstream)
        original = "# Preserve owner formatting\nskills:\n  inline_shell: true\n  template_vars: false\n"
        (self.home / "config.yaml").write_text(original)
        with self.session() as (_, environment):
            script = (
                "import json,os\n"
                "from pathlib import Path\n"
                "from hermes_cli.config import load_config_readonly\n"
                "from agent.skill_preprocessing import preprocess_skill_content\n"
                "home=Path(os.environ['HERMES_HOME'])\n"
                "content='A harmless preview: !`printf preview > inline-executed.txt`'\n"
                "rendered=preprocess_skill_content(content,home)\n"
                "config=load_config_readonly()\n"
                "print(json.dumps({'literal':rendered==content,'executed':(home/'inline-executed.txt').exists(),'inline_shell':config['skills'].get('inline_shell'),'template_vars':config['skills'].get('template_vars')}))\n"
            )
            result = subprocess.run(
                [str(upstream / ".venv/bin/python"), "-c", script],
                env=environment,
                cwd=upstream,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, "Pinned skill preview probe failed; raw output suppressed")
            self.assertNotIn(environment["AUXILIARY_MYHERMES_API_KEY"], result.stdout + result.stderr)
            self.assertEqual(
                json.loads(result.stdout.strip().splitlines()[-1]),
                {"literal": True, "executed": False, "inline_shell": False, "template_vars": False},
            )
        self.assertEqual((self.home / "config.yaml").read_text(), original)

    @unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected pinned Hermes checkout")
    def test_pinned_official_config_loader_and_provider_resolver(self):
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        self.config["upstream"] = str(upstream)
        (self.home / "config.yaml").write_text(
            "model:\n  provider: openrouter\n  default: unwanted\nagent:\n  api_max_retries: 6\n"
        )
        with self.session() as (_, environment):
            script = (
                "import json,os\n"
                "from hermes_cli.config import load_config\n"
                "from hermes_cli.runtime_provider import resolve_runtime_provider\n"
                "from agent.auxiliary_client import _transient_retry_count\n"
                "config=load_config()\n"
                "runtime=resolve_runtime_provider(requested='myhermes',target_model='economy')\n"
                "print(json.dumps({'provider':config['model']['provider'],'model':config['model']['default'],'api_mode':runtime['api_mode'],'token_matches':runtime['api_key']==os.environ['AUXILIARY_MYHERMES_API_KEY'],'base_matches':runtime['base_url']==config['providers']['myhermes']['api'],'retry':config['agent']['api_max_retries'],'aux_retry':_transient_retry_count()}))\n"
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
                result.returncode, 0, "Pinned upstream config probe failed; raw output intentionally suppressed"
            )
            self.assertNotIn(environment["AUXILIARY_MYHERMES_API_KEY"], result.stdout + result.stderr)
            self.assertEqual(
                json.loads(result.stdout.strip().splitlines()[-1]),
                {
                    "provider": "myhermes",
                    "model": "economy",
                    "api_mode": "chat_completions",
                    "token_matches": True,
                    "base_matches": True,
                    "retry": 1,
                    "aux_retry": 0,
                },
            )


if __name__ == "__main__":
    unittest.main()
