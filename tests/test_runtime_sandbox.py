"""Managed sandbox policy, fail-closed admission and opt-in pinned Docker execution."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from myhermes.errors import CompanionError
from myhermes.sandbox import DOCKER_IMAGE, _verify_workspace_bind, sandbox_preflight
import test_runtime_relay as runtime_fixture


class SandboxPreflightAcceptance(unittest.TestCase):
    def test_no_cli_no_daemon_wrong_engine_and_timeout_fail_without_output(self):
        with (
            patch("myhermes.sandbox.shutil.which", return_value=None),
            patch.object(Path, "is_file", return_value=False),
        ):
            with self.assertRaises(CompanionError) as caught:
                sandbox_preflight({})
            self.assertEqual(caught.exception.code, "runtime_sandbox_unavailable")
        failures = (
            subprocess.CompletedProcess([], 1, "SYNTHETIC_PRIVATE_DOCKER_ERROR"),
            subprocess.CompletedProcess([], 0, "windows"),
            subprocess.TimeoutExpired(["docker"], 15),
            OSError("SYNTHETIC_PRIVATE_DOCKER_ERROR"),
        )
        for failure in failures:
            kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with (
                self.subTest(failure=type(failure).__name__),
                patch("myhermes.sandbox.shutil.which", return_value="/fixture/docker"),
                patch("myhermes.sandbox.subprocess.run", **kwargs) as run,
            ):
                with self.assertRaises(CompanionError) as caught:
                    sandbox_preflight({})
                self.assertEqual(caught.exception.code, "runtime_sandbox_unavailable")
                self.assertNotIn("SYNTHETIC_PRIVATE_DOCKER_ERROR", str(caught.exception))
                self.assertEqual(run.call_args.args[0], ["/fixture/docker", "info", "--format", "{{.OSType}}"])
                self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_raw_passthrough_and_merged_environment_are_rejected_before_docker_access(self):
        for key in ("env_passthrough", "credential_files", "docker_env"):
            config = {"terminal": {key: ["SYNTHETIC_PRIVATE_SETTING"]}}
            with self.subTest(key=key), patch("myhermes.sandbox.subprocess.run") as run:
                with self.assertRaises(CompanionError) as caught:
                    sandbox_preflight(config)
                self.assertEqual(caught.exception.code, "runtime_sandbox_passthrough_unsupported")
                self.assertNotIn("SYNTHETIC_PRIVATE_SETTING", str(caught.exception))
                run.assert_not_called()
                self.assertEqual(config["terminal"][key], ["SYNTHETIC_PRIVATE_SETTING"])

    def test_linux_daemon_and_local_socket_accept_without_pulling_any_container(self):
        with (
            patch("myhermes.sandbox.shutil.which", return_value="/fixture/docker"),
            patch(
                "myhermes.sandbox.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, "linux\n"),
                    subprocess.CompletedProcess([], 0, "unix:///fixture/docker.sock\n"),
                ],
            ) as run,
            patch.dict(os.environ, {"HERMES_DOCKER_BINARY": "/untrusted/override"}),
        ):
            self.assertEqual(sandbox_preflight({}), "/fixture/docker")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][0], "/fixture/docker")

    def test_remote_daemon_is_rejected_before_bind_or_container_launch(self):
        for endpoint, host in (("ssh://example.invalid", ""), ("unix:///local.sock", "tcp://example.invalid:2376")):
            with (
                self.subTest(endpoint=endpoint),
                patch("myhermes.sandbox.shutil.which", return_value="/fixture/docker"),
                patch.dict(os.environ, {"DOCKER_HOST": host}),
                patch(
                    "myhermes.sandbox.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, "linux"),
                        subprocess.CompletedProcess([], 0, endpoint),
                    ],
                ) as run,
            ):
                with self.assertRaises(CompanionError) as caught:
                    sandbox_preflight({}, Path("/synthetic/not-mounted"))
                self.assertEqual(caught.exception.code, "runtime_sandbox_remote_unsupported")
                self.assertEqual(run.call_count, 2)

    def test_missing_image_and_unshared_mount_fail_closed_and_leave_no_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch("myhermes.sandbox.subprocess.run", return_value=subprocess.CompletedProcess([], 1)) as run:
                with self.assertRaises(CompanionError) as caught:
                    _verify_workspace_bind("/fixture/docker", directory)
                self.assertEqual(caught.exception.code, "runtime_sandbox_image_missing")
                self.assertIn("docker pull " + DOCKER_IMAGE, str(caught.exception))
                run.assert_called_once()
            with patch(
                "myhermes.sandbox.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], 0, "WRONG_VM_MARKER"),
                    subprocess.CompletedProcess([], 0),
                ],
            ) as run:
                with self.assertRaises(CompanionError) as caught:
                    _verify_workspace_bind("/fixture/docker", directory)
                self.assertEqual(caught.exception.code, "runtime_sandbox_mount_unavailable")
                command = run.call_args_list[1].args[0]
                self.assertIn("--network=none", command)
                self.assertIn("--read-only", command)
                self.assertIn("--rm", command)
                self.assertIn("--pull=never", command)
                name = command[command.index("--name") + 1]
                self.assertTrue(name.startswith("myhermes-bind-check-"))
                self.assertEqual(run.call_args.args[0], ["/fixture/docker", "rm", "--force", name])
                self.assertEqual(list(directory.iterdir()), [])

    def test_arbitrary_external_skill_root_is_rejected_before_docker_access(self):
        for field in ("external_dirs", "trusted_project_dirs"):
            config = {"skills": {field: ["/synthetic/private/home"]}}
            with self.subTest(field=field), patch("myhermes.sandbox.subprocess.run") as run:
                with self.assertRaises(CompanionError) as caught:
                    sandbox_preflight(config)
            self.assertEqual(caught.exception.code, "runtime_sandbox_external_skills_unsupported")
            self.assertNotIn("/synthetic/private/home", str(caught.exception))
            self.assertEqual(config["skills"][field], ["/synthetic/private/home"])
            run.assert_not_called()

    def test_automatic_mount_roots_cannot_follow_symlinks_to_owner_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            home, outside = Path(temporary).resolve() / "home", Path(temporary).resolve() / "unrelated"
            home.mkdir()
            outside.mkdir()
            for relative in ("skills", "cache", "cache/documents", "document_cache", "images", "attachments"):
                root = home / relative
                root.parent.mkdir(exist_ok=True)
                root.symlink_to(outside, target_is_directory=True)
                try:
                    with self.subTest(path=relative), patch("myhermes.sandbox.subprocess.run") as run:
                        with self.assertRaises(CompanionError) as caught:
                            sandbox_preflight({}, home / "myhermes-sandboxes")
                    self.assertEqual(caught.exception.code, "symlink_rejected")
                    run.assert_not_called()
                finally:
                    root.unlink()

    def test_timed_out_probe_removes_only_its_generated_container_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "myhermes.sandbox.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess([], 0),
                    subprocess.TimeoutExpired(["docker"], 30),
                    subprocess.CompletedProcess([], 0),
                ],
            ) as run:
                with self.assertRaises(CompanionError) as caught:
                    _verify_workspace_bind("/fixture/docker", Path(temporary))
                self.assertEqual(caught.exception.code, "runtime_sandbox_mount_unavailable")
                command = run.call_args_list[1].args[0]
                name = command[command.index("--name") + 1]
                self.assertEqual(run.call_args.args[0], ["/fixture/docker", "rm", "--force", name])
                self.assertEqual(list(Path(temporary).iterdir()), [])


class RuntimeSandboxAcceptance(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_fixture.RuntimeRelayAcceptance()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_managed_overlay_and_environment_override_host_access_without_changing_owner_yaml(self):
        f = self.fixture
        original = (
            b"# preserve owner settings\nterminal:\n  backend: local\n  cwd: /host/private\n"
            b"  docker_volumes: ['/host:/host']\n  docker_mount_cwd_to_workspace: true\n"
            b"  docker_extra_args: ['--privileged']\n  docker_forward_env: ['PRIVATE_TOKEN']\n"
            b"  docker_persist_across_processes: true\n  docker_snap_compat: true\n"
        )
        (f.home / "config.yaml").write_bytes(original)
        inherited = {
            "TERMINAL_ENV": "local",
            "terminal_env": "local",
            "_HERMES_FORCE_TERMINAL_ENV": "local",
            "TERMINAL_DOCKER_EXTRA_ARGS": '["--privileged"]',
            "TERMINAL_SANDBOX_DIR": "/host/private",
            "_HERMES_FORCE_HERMES_DOCKER_BINARY": "/untrusted/helper",
        }
        with patch.dict(os.environ, inherited), f.session() as (_, env):
            terminal = json.loads((Path(env["HERMES_MANAGED_DIR"]) / "config.yaml").read_text())["terminal"]
            self.assertEqual(terminal["backend"], "docker")
            self.assertEqual(env["TERMINAL_ENV"], "docker")
            self.assertEqual(env["TERMINAL_CWD"], "/workspace")
            self.assertEqual(terminal["docker_image"], DOCKER_IMAGE)
            self.assertEqual(terminal["sandbox_dir"], str(f.home / "myhermes-sandboxes"))
            for key in ("docker_volumes", "docker_extra_args", "docker_forward_env"):
                self.assertEqual(terminal[key], [])
                self.assertEqual(env["TERMINAL_" + key.upper()], "[]")
            for key in ("docker_mount_cwd_to_workspace", "docker_persist_across_processes", "docker_snap_compat"):
                self.assertIs(terminal[key], False)
            for key in ("terminal_env", "_HERMES_FORCE_TERMINAL_ENV", "_HERMES_FORCE_HERMES_DOCKER_BINARY"):
                self.assertNotIn(key, env)
            self.assertEqual(env["HERMES_DOCKER_BINARY"], "/fixture/docker")
            self.assertEqual((f.home / "myhermes-sandboxes").stat().st_mode & 0o777, 0o700)
        self.assertEqual((f.home / "config.yaml").read_bytes(), original)

    def test_failed_sandbox_admission_never_opens_relay_or_yields_a_host_command(self):
        f = self.fixture
        with (
            patch(
                "myhermes.runtime.sandbox_preflight",
                side_effect=CompanionError("runtime_sandbox_unavailable", "Docker unavailable", 3),
            ),
            patch("myhermes.runtime.RelayBridge") as bridge,
        ):
            with self.assertRaises(CompanionError):
                with f.session():
                    self.fail("Sandbox failure must never yield a runtime")
            bridge.assert_not_called()

    @unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected pinned Hermes checkout")
    def test_pinned_upstream_resolves_managed_backend_and_never_falls_back_when_docker_fails(self):
        f = self.fixture
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        (f.home / "config.yaml").write_text("terminal:\n  backend: local\n  docker_extra_args: ['--privileged']\n")
        with f.session() as (_, environment):
            script = """
import os
from unittest.mock import patch
from hermes_cli.config import load_config_readonly, apply_terminal_config_to_env
from tools import terminal_tool as terminal
from tools.terminal_tool_backends import _create_environment
from tools.environments.base import EnvironmentConnectionError
from tools.environments import docker
assert load_config_readonly()['terminal']['backend'] == 'docker'
apply_terminal_config_to_env()
config = terminal._get_env_config()
assert config['env_type'] == 'docker' and config['cwd'] == '/workspace'
assert config['docker_extra_args'] == [] and config['docker_volumes'] == []
assert not config['docker_mount_cwd_to_workspace'] and not config['docker_persist_across_processes']
with patch.object(docker, '_ensure_docker_available', side_effect=EnvironmentConnectionError('synthetic down')), \
     patch('tools.terminal_tool_backends._LocalEnvironment', side_effect=AssertionError('host fallback')):
    try:
        _create_environment('docker', config['docker_image'], '/workspace', 10,
                            container_config=config, task_id='synthetic-offline')
    except EnvironmentConnectionError:
        pass
    else:
        raise AssertionError('Docker failure did not propagate')
print('PINNED_SANDBOX_POLICY_READY')
"""
            result = subprocess.run(
                [str(upstream / ".venv/bin/python"), "-c", script],
                cwd=upstream,
                env=environment,
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.assertEqual(result.returncode, 0, "Pinned sandbox policy probe failed; raw output suppressed")
            self.assertEqual(result.stdout.strip(), "PINNED_SANDBOX_POLICY_READY")

    @unittest.skipUnless(
        os.getenv("MYHERMES_TEST_UPSTREAM") and os.getenv("MYHERMES_TEST_DOCKER") == "1",
        "Requires the pinned checkout plus explicit local Docker execution opt-in",
    )
    def test_actual_docker_terminal_code_and_file_tools_share_persistent_isolated_workspace(self):
        f = self.fixture
        # macOS Docker VMs share the user home; /private/tmp is not necessarily
        # shared (Colima can expose an unrelated VM-local /private/tmp).
        shared = self.enterContext(
            tempfile.TemporaryDirectory(prefix=".sandbox-test-", dir=Path(__file__).parent.parent)
        )
        f.root = Path(shared)
        f.home = f.root / "home"
        f.home.mkdir(mode=0o700)
        f.config["hermes_home"] = str(f.home)
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        canary = f.root / "host-only-canary"
        canary.write_text("SYNTHETIC_HOST_ONLY")
        script = Path(__file__).parent / "fixtures" / "pinned_sandbox.py"
        with (
            patch("myhermes.runtime.sandbox_preflight", side_effect=sandbox_preflight),
            f.session() as (_, environment),
        ):
            result = subprocess.run(
                [str(upstream / ".venv/bin/python"), str(script), str(canary)],
                cwd=upstream,
                env={**environment, "PYTHONPATH": str(upstream)},
                capture_output=True,
                text=True,
                timeout=180,
            )
            self.assertEqual(result.returncode, 0, "Actual Docker probe failed; raw output suppressed")
            self.assertIn("DOCKER_SANDBOX_READY", result.stdout)
            self.assertNotIn(environment["AUXILIARY_MYHERMES_API_KEY"], result.stdout + result.stderr)
            self.assertEqual(canary.read_text(), "SYNTHETIC_HOST_ONLY")
            roots = list((f.home / "myhermes-sandboxes" / "docker").glob("*/workspace"))
            self.assertEqual(len(roots), 1)
            self.assertEqual((roots[0] / "code-result.txt").read_text(), "SYNTHETIC_CODE_OK")
            self.assertEqual((roots[0] / "file-result.txt").read_text(), "SYNTHETIC_FILE_OK")


if __name__ == "__main__":
    unittest.main()
