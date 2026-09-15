"""Process-only Docker defaults for managed Hermes terminal/code execution."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from .errors import CompanionError
from .files import safe_path


DOCKER_IMAGE = (
    "nikolaik/python-nodejs:python3.11-nodejs20@sha256:8f958bdc1b4a422bfafd97cab4f69836401f616ae985d4b57a53d254f5bcb038"
)
_DOCKER_LOCATIONS = (
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
)
# Pinned tools/credential_files.py auto-mount roots (current and legacy forms).
_AUTOMOUNT_ROOTS = (
    "skills",
    "cache",
    "cache/documents",
    "document_cache",
    "cache/images",
    "image_cache",
    "cache/audio",
    "audio_cache",
    "cache/videos",
    "video_cache",
    "cache/screenshots",
    "browser_screenshots",
    "cache/web",
    "web_cache",
    "cache/delegation",
    "delegation_cache",
    "cache/spillover",
    "images",
    "attachments",
)


def managed_terminal_config(sandbox_directory=None):
    """Pin isolation-sensitive leaves; the owner's YAML is never rewritten."""
    config = {
        "backend": "docker",
        "cwd": "/workspace",
        "degraded_mode": "fail",
        "docker_image": DOCKER_IMAGE,
        "docker_volumes": [],
        "docker_mount_cwd_to_workspace": False,
        "docker_extra_args": [],
        "docker_forward_env": [],
        "docker_env": {},
        "env_passthrough": [],
        "credential_files": [],
        "docker_network": True,
        "docker_snap_compat": False,
        "docker_run_as_host_user": False,
        "docker_persist_across_processes": False,
        "docker_shared_container_key": "",
        "docker_orphan_reaper": False,
        "container_persistent": True,
        "container_cpu": 2,
        "container_memory": 4096,
        "container_disk": 51200,
    }
    if sandbox_directory is not None:
        config["sandbox_dir"] = str(sandbox_directory)
    return config


def sandbox_preflight(user_config, sandbox_directory=None):
    """Reject raw-config escape hatches; prove the local Docker bind is visible.

    The pinned upstream reads credential_files and env_passthrough from raw
    owner config, bypassing its managed overlay. Deep-merging an empty docker_env
    mapping also retains owner keys. Reject rather than pretend an empty overlay wins.
    """
    terminal = user_config.get("terminal") or {}
    if not isinstance(terminal, dict):
        raise CompanionError("runtime_config_rejected", "Hermes terminal configuration must be a mapping.", 3)
    if any(terminal.get(key) for key in ("env_passthrough", "credential_files", "docker_env")):
        raise CompanionError(
            "runtime_sandbox_passthrough_unsupported",
            "Managed sandbox startup does not allow terminal.env_passthrough, terminal.credential_files "
            "or terminal.docker_env. "
            "Back up and remove those owner settings before retrying; existing config was preserved.",
            3,
        )
    skills = user_config.get("skills") or {}
    if not isinstance(skills, dict):
        raise CompanionError("runtime_config_rejected", "Hermes skills configuration must be a mapping.", 3)
    if skills.get("external_dirs") or skills.get("trusted_project_dirs"):
        raise CompanionError(
            "runtime_sandbox_external_skills_unsupported",
            "Managed sandbox startup does not allow skills.external_dirs or skills.trusted_project_dirs because "
            "upstream can mount whole directories into Docker. Back up and remove those owner settings; use validated MyHermes packages "
            "under this Hermes home's skills directory. Existing config was preserved.",
            3,
        )
    if sandbox_directory is not None:
        # Upstream's automatic cache/skill mounts follow source-root symlinks.
        # Check names/parents only; never inspect owner cache or skill content.
        for relative in _AUTOMOUNT_ROOTS:
            safe_path(Path(sandbox_directory).parent / relative)
    # Resolve a real Docker CLI, never an inherited HERMES_DOCKER_BINARY helper.
    docker = shutil.which("docker") or next(
        (p for p in _DOCKER_LOCATIONS if Path(p).is_file() and os.access(p, os.X_OK)), None
    )
    if not docker:
        raise CompanionError(
            "runtime_sandbox_unavailable",
            "Managed Hermes requires Docker. Install and start Docker, then retry. Host execution is disabled.",
            3,
        )
    try:
        result = subprocess.run(
            [docker, "info", "--format", "{{.OSType}}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
            check=False,
        )
        available = result.returncode == 0 and result.stdout.strip() == "linux"
    except (OSError, subprocess.SubprocessError):
        available = False
    if not available:
        raise CompanionError(
            "runtime_sandbox_unavailable",
            "Docker must be running and accessible with Linux containers before managed Hermes starts. "
            "Check Docker in your native terminal and retry. Host execution is disabled; tool output was suppressed.",
            3,
        )
    # Refuse remote daemons before offering any host path for a bind mount.
    # DOCKER_CONTEXT can override DOCKER_HOST; conservatively require both an
    # explicit host (if supplied) and the selected context to use local sockets.
    host = os.environ.get("DOCKER_HOST", "")
    try:
        context = subprocess.run(
            [docker, "context", "inspect", "--format", '{{(index .Endpoints "docker").Host}}'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
            check=False,
        )
        local = context.returncode == 0 and context.stdout.strip().startswith("unix://")
    except (OSError, subprocess.SubprocessError):
        local = False
    if (host and not host.startswith("unix://")) or not local:
        raise CompanionError(
            "runtime_sandbox_remote_unsupported",
            "Managed sandbox startup requires a local Docker Unix socket context. "
            "Select your local Docker context and retry; remote Docker daemons are unsupported.",
            3,
        )
    if sandbox_directory is not None:
        _verify_workspace_bind(docker, sandbox_directory)
    return docker


def _verify_workspace_bind(docker, directory):
    # Docker's -v can silently create an empty directory inside a macOS VM for
    # an unshared host path. Check a random, nonsecret marker with --mount,
    # which also refuses a missing source, before any real workload can start.
    if any(char in str(directory) for char in (":", ",", "\n", "\r")):
        raise CompanionError(
            "runtime_sandbox_path_unsupported",
            "The Hermes home path cannot contain colons, commas or line breaks for Docker bind mounts.",
            3,
        )
    try:
        image = subprocess.run(
            [docker, "image", "inspect", DOCKER_IMAGE, "--format", "{{.Id}}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        image_ready = image.returncode == 0
    except (OSError, subprocess.SubprocessError):
        image_ready = False
    if not image_ready:
        raise CompanionError(
            "runtime_sandbox_image_missing",
            "Pull the managed public sandbox image in your native terminal, then retry: docker pull " + DOCKER_IMAGE,
            3,
        )
    with tempfile.TemporaryDirectory(prefix=".bind-check-", dir=directory) as scratch:
        marker = uuid.uuid4().hex
        container_name = "myhermes-bind-check-" + uuid.uuid4().hex
        (Path(scratch) / "ready").write_text(marker)
        try:
            result = subprocess.run(
                [
                    docker,
                    "run",
                    "--rm",
                    "--name",
                    container_name,
                    "--pull=never",
                    "--network=none",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--pids-limit=32",
                    "--mount",
                    "type=bind,source=" + scratch + ",target=/myhermes-check,readonly",
                    DOCKER_IMAGE,
                    "cat",
                    "/myhermes-check/ready",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=30,
                check=False,
            )
            visible = result.returncode == 0 and result.stdout == marker
        except (OSError, subprocess.SubprocessError):
            visible = False
        finally:
            # A timed-out docker client does not imply its container stopped.
            # Only this freshly generated name is eligible for cleanup.
            try:
                subprocess.run(
                    [docker, "rm", "--force", container_name],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError, KeyboardInterrupt):
                pass
        if not visible:
            raise CompanionError(
                "runtime_sandbox_mount_unavailable",
                "Docker could not verify the dedicated workspace bind. Make the Hermes home available to "
                "your local Docker VM (for example its shared user-home directory), then retry. "
                "Host execution is disabled; diagnostic output was suppressed.",
                3,
            )


def terminal_environment(config):
    """Set explicit environment counterparts as well as the managed YAML.

    Hermes reads terminal settings before some entrypoints apply the YAML, and
    raw-owner config controls which environment leaves its bridge replaces.
    """
    return {
        "TERMINAL_ENV" if key == "backend" else "TERMINAL_" + key.upper(): (
            json.dumps(value) if isinstance(value, (list, dict)) or value is None else str(value)
        )
        for key, value in config.items()
        if key not in ("env_passthrough", "credential_files")
    }
