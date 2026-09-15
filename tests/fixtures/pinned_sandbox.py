"""Opt-in fixture executed by the pinned upstream interpreter; no model requests."""

import json
import os
import shlex
import subprocess
import sys
from unittest.mock import patch

from tools import terminal_tool as terminal
from tools.code_execution_tool import execute_code
from tools.environments.docker import DockerEnvironment
from tools.file_tools import read_file_tool, write_file_tool


task_id = "synthetic-managed-sandbox"
canary = sys.argv[1]
container_ids = []
try:
    with patch("tools.terminal_tool_backends._LocalEnvironment", side_effect=AssertionError("host fallback")):
        config = terminal._get_env_config()
        assert config["env_type"] == "docker"
        result = json.loads(
            terminal.terminal_tool(
                "pwd; test ! -e " + shlex.quote(canary) + " && printf '\\nSYNTHETIC_HOST_HIDDEN\\n'",
                task_id=task_id,
                timeout=30,
            )
        )
        assert result.get("exit_code") == 0, "Docker terminal failed"
        assert "/workspace" in result.get("output", ""), "Wrong terminal workdir"
        assert "SYNTHETIC_HOST_HIDDEN" in result.get("output", ""), "Host file visible"
        environments = list(terminal._active_environments.values())
        assert len(environments) == 1 and isinstance(environments[0], DockerEnvironment)
        environment = environments[0]
        container_ids.append(environment._container_id)
        inspect = subprocess.run(
            [os.environ["HERMES_DOCKER_BINARY"], "inspect", environment._container_id],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        metadata = json.loads(inspect.stdout)[0]
        mounts = {entry["Destination"]: entry for entry in metadata["Mounts"]}
        assert mounts["/workspace"]["Source"].startswith(os.environ["TERMINAL_SANDBOX_DIR"] + "/docker/")
        assert mounts["/root"]["Source"].startswith(os.environ["TERMINAL_SANDBOX_DIR"] + "/docker/")
        assert not metadata["HostConfig"]["Privileged"]
        assert "no-new-privileges" in str(metadata["HostConfig"]["SecurityOpt"])
        assert metadata["HostConfig"]["NetworkMode"] != "host"
        for mount in metadata["Mounts"]:
            assert mount["Source"] not in ("/", os.path.expanduser("~"), os.environ["HERMES_HOME"])
            assert "docker.sock" not in mount["Source"]
        for entry in metadata["Config"]["Env"]:
            assert not entry.startswith("AUXILIARY_MYHERMES_API_KEY=")
        written = json.loads(write_file_tool("/workspace/file-result.txt", "SYNTHETIC_FILE_OK", task_id=task_id))
        assert not written.get("error"), "Docker file write failed"
        read = json.loads(read_file_tool("/workspace/file-result.txt", task_id=task_id))
        assert "SYNTHETIC_FILE_OK" in json.dumps(read), "Docker file read failed"
        code = (
            "import os\nfrom pathlib import Path\n"
            "assert not Path(" + repr(canary) + ").exists()\n"
            "assert 'AUXILIARY_MYHERMES_API_KEY' not in os.environ\n"
            "Path('/workspace/code-result.txt').write_text('SYNTHETIC_CODE_OK')\n"
            "print('SYNTHETIC_CODE_OK')\n"
        )
        executed = json.loads(execute_code(code, task_id=task_id, enabled_tools=[]))
        assert "SYNTHETIC_CODE_OK" in executed.get("output", ""), "Docker execute_code failed"
        assert len(terminal._active_environments) == 1, "Tools did not share one sandbox"
finally:
    terminal.cleanup_all_environments()
    assert DockerEnvironment.wait_for_all_teardowns(timeout=45), "Synthetic container cleanup timed out"
    for container_id in container_ids:
        result = subprocess.run(
            [os.environ["HERMES_DOCKER_BINARY"], "inspect", container_id],
            capture_output=True,
            timeout=15,
        )
        assert result.returncode != 0, "Synthetic container remained after cleanup"

print("DOCKER_SANDBOX_READY")
