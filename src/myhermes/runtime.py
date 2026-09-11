"""Pinned official Hermes lifecycle adapter; no shell snippets or profile switching."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import yaml

from .errors import CompanionError
from .files import (
    LIMITS,
    _export_directory,
    atomic_bytes,
    atomic_json,
    memory_locks,
    private_dir,
    safe_path,
    snapshot,
    validate_content,
)
from .relay_bridge import RelayBridge
from .local_config import canonical_identifier

UPSTREAM_URL = "https://github.com/NousResearch/hermes-agent.git"
UPSTREAM_VERSION = "v2026.9.7"
UPSTREAM_COMMIT = "2237be355906fbe6065ce1815711eee52b2d646e"
_COMMAND_TIMEOUT_SECONDS = 1200


def operating_system():
    if platform.system() == "Darwin":
        return "macos"
    if platform.system() == "Linux":
        try:
            values = dict(
                line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line
            )
            if values.get("ID", "").strip('"') == "ubuntu":
                return "ubuntu"
        except (OSError, ValueError):
            pass
    raise CompanionError("unsupported_os", "The initial runtime supports macOS and Ubuntu only.", 3)


def checked(command, *, cwd=None):
    if threading.current_thread() is not threading.main_thread():
        raise CompanionError("runtime_main_thread_required", "Runtime operations must run on the CLI's main thread.", 3)
    child, reason, deadline, requested_signal = None, None, None, None
    previous = {}
    completed, killed = False, False
    expires = time.monotonic() + _COMMAND_TIMEOUT_SECONDS

    def send_group(kind):
        nonlocal killed
        if child is not None:
            try:
                # Only this newly created session/process group, never the
                # caller's terminal or an unrelated installation's processes.
                os.killpg(child.pid, kind)
            except ProcessLookupError:
                pass
            if kind == signal.SIGKILL:
                killed = True

    def stop(kind, _frame, *, cause="interrupted"):
        nonlocal reason, deadline, requested_signal
        if reason is None:
            reason = cause
            requested_signal = kind
            deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
        send_group(kind)

    try:
        for kind in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            previous[kind] = signal.getsignal(kind)
            signal.signal(kind, stop)
        child = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        if reason is not None:
            # Remember a request during Popen creation until the owned group
            # exists. The original deadline is not restarted by this delivery.
            send_group(requested_signal)
        while True:
            if reason is None and time.monotonic() >= expires:
                stop(signal.SIGTERM, None, cause="timeout")
            if deadline is not None and time.monotonic() >= deadline and not killed:
                send_group(signal.SIGKILL)
            try:
                stdout, _stderr = child.communicate(timeout=0.1)
                completed = True
                break
            except subprocess.TimeoutExpired:
                continue
        if reason is not None:
            # A direct child can exit before its compiler/build subprocesses.
            # Stop remaining members even when they closed the capture pipes.
            if not killed:
                send_group(signal.SIGKILL)
            if reason == "timeout":
                raise CompanionError(
                    "runtime_command_timeout",
                    "Runtime operation exceeded its time limit; subprocess output was suppressed.",
                    3,
                )
            raise CompanionError(
                "runtime_command_interrupted",
                "Runtime operation was interrupted; subprocess output was suppressed.",
                130,
            )
        if child.returncode != 0:
            send_group(signal.SIGKILL)
            raise subprocess.CalledProcessError(child.returncode, command)
        return stdout.strip()
    except KeyboardInterrupt:
        # Also handle an injected interrupt or one raised by subprocess code;
        # ordinary terminal SIGINT uses the bounded handler above.
        raise CompanionError(
            "runtime_command_interrupted", "Runtime operation was interrupted; subprocess output was suppressed.", 130
        ) from None
    except (OSError, subprocess.SubprocessError):
        raise CompanionError(
            "runtime_command_failed",
            "Pinned runtime operation failed; inspect local tools and network, then retry. Subprocess output was suppressed.",
            3,
        ) from None
    finally:
        if child is not None:
            if not completed and not killed:
                send_group(signal.SIGKILL)
            while True:
                try:
                    child.wait(timeout=0.25)
                    break
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    if not killed:
                        send_group(signal.SIGKILL)
            for stream in (child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
        for kind, original in previous.items():
            signal.signal(kind, original)


def verify_runtime(path: Path, *, require_installed=True):
    path = safe_path(path)
    if checked(["git", "rev-parse", "HEAD"], cwd=path) != UPSTREAM_COMMIT:
        raise CompanionError("upstream_mismatch", "The runtime checkout is not the supported pinned Hermes commit.", 3)
    if checked(["git", "status", "--porcelain", "--untracked-files=no"], cwd=path):
        raise CompanionError(
            "upstream_modified", "Tracked upstream changes require a compatibility review before managed startup.", 3
        )
    command = path / ".venv/bin/hermes"
    if require_installed and not command.is_file():
        raise CompanionError("runtime_missing", "Install the pinned runtime environment before startup.", 3)
    return command


def _ensure_runtime_pip(path, version):
    environment = safe_path(path / ".venv")
    interpreter = environment / "bin/python"
    probe = (
        "import importlib.util,json,sys; "
        "print(json.dumps({'version':f'{sys.version_info.major}.{sys.version_info.minor}',"
        "'prefix':sys.prefix,'base_prefix':sys.base_prefix,'pip':importlib.util.find_spec('pip') is not None}))"
    )
    try:
        info = json.loads(checked([str(interpreter), "-I", "-c", probe]))
        valid = (
            isinstance(info, dict)
            and info.get("version") == version
            and isinstance(info.get("prefix"), str)
            and isinstance(info.get("base_prefix"), str)
            and info["prefix"] != info["base_prefix"]
            and Path(info["prefix"]).resolve() == environment
            and type(info.get("pip")) is bool
        )
    except (ValueError, TypeError, OSError):
        valid = False
    if not valid:
        raise CompanionError(
            "runtime_venv_mismatch",
            "The existing runtime interpreter must use this checkout's .venv and the selected Python version. "
            "Choose the matching supported --python or inspect the incomplete environment; its files were preserved.",
            3,
        )
    if not info["pip"]:
        # CPython's bundled bootstrap needs no network. Never install into a
        # base interpreter or clear an existing owner environment to repair it.
        checked([str(interpreter), "-I", "-m", "ensurepip", "--default-pip"], cwd=path)


def install_runtime(path: Path, python: str, *, dry_run=False):
    """Install/repair under the caller's exclusive runtime target admission lock."""
    operating_system()
    path = safe_path(path)
    if dry_run:
        return {"status": "dry_run", "version": UPSTREAM_VERSION, "commit": UPSTREAM_COMMIT}
    version = checked([python, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"])
    if version not in ("3.11", "3.12", "3.13"):
        raise CompanionError("python_incompatible", "Pinned Hermes requires Python 3.11, 3.12 or 3.13.", 3)
    if path.exists():
        verify_runtime(path, require_installed=False)
    else:
        # Preserve the mode of an existing parent; create missing parents 0700.
        # A failed/interrupted clone never occupies the final runtime path.
        # Publish the verified Git checkout BEFORE creating its venv, whose
        # interpreter scripts embed absolute paths and cannot be relocated.
        with _export_directory(path.parent) as parent_descriptor:
            with tempfile.TemporaryDirectory(prefix=".myhermes-install-", dir=path.parent) as temporary:
                staging = Path(temporary) / "checkout"
                checked(
                    [
                        "git",
                        "-c",
                        "credential.helper=",
                        "clone",
                        "--no-checkout",
                        "--filter=blob:none",
                        UPSTREAM_URL,
                        str(staging),
                    ]
                )
                checked(["git", "checkout", "--detach", UPSTREAM_COMMIT], cwd=staging)
                verify_runtime(staging, require_installed=False)
                if os.path.lexists(path):
                    raise CompanionError(
                        "runtime_destination_changed",
                        "The runtime destination appeared during installation; "
                        "its files were preserved. Choose another destination or inspect it before retrying.",
                        3,
                    )
                os.rename(staging, path)
                os.fsync(parent_descriptor)
    verify_runtime(path, require_installed=False)
    if not (path / ".venv/bin/python").exists():
        checked([python, "-m", "venv", str(path / ".venv")])
    _ensure_runtime_pip(path, version)
    checked([str(path / ".venv/bin/python"), "-m", "pip", "install", "-e", ".[cli]"], cwd=path)
    verify_runtime(path)
    return {"status": "installed", "version": UPSTREAM_VERSION, "commit": UPSTREAM_COMMIT}


def _write_personality_backup(state, files, purpose):
    backup_id = str(uuid.uuid4())
    atomic_json(
        state.directory / "backups" / (backup_id + ".json"),
        {
            "schema_version": "1",
            "backup_id": backup_id,
            "purpose": purpose,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "upstream_version": UPSTREAM_VERSION,
            "upstream_commit": UPSTREAM_COMMIT,
            "revision": state.revision,
            "files": files,
        },
    )
    return backup_id


def backup_personality(state, home, *, purpose="manual"):
    if purpose not in ("manual", "upgrade", "pre_restore"):
        raise ValueError("Unsupported backup purpose")
    with memory_locks(home):
        state.recover(home)
        return _write_personality_backup(state, snapshot(home), purpose)


def _backup_identifier(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise CompanionError("backup_rejected", "Backup ID must be a canonical UUID.") from None
    return value


def read_personality_backup(state, backup_id):
    """Read owner-local content only for an explicit restore; never print it."""
    backup_id = _backup_identifier(backup_id)
    try:
        path = safe_path(state.directory / "backups" / (backup_id + ".json"))
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise ValueError
            # Includes legacy recover backups containing the interrupted journal.
            raw = stream.read(4_194_305)
            if len(raw) > 4_194_304:
                raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict) or type(value.get("revision")) is not int or value["revision"] < 0:
            raise ValueError
        files = value.get("files")
        if not isinstance(files, dict) or set(files) != set(LIMITS):
            raise ValueError
        for name, content in files.items():
            validate_content(name, content)
        if "schema_version" in value:
            if set(value) != {
                "schema_version",
                "backup_id",
                "purpose",
                "created_at",
                "upstream_version",
                "upstream_commit",
                "revision",
                "files",
            }:
                raise ValueError
            if (
                value["schema_version"] != "1"
                or value["backup_id"] != backup_id
                or value["purpose"] not in ("manual", "upgrade", "pre_restore")
                or not isinstance(value["created_at"], str)
                or len(value["created_at"]) > 40
                or not isinstance(value["upstream_version"], str)
                or len(value["upstream_version"]) > 40
                or not isinstance(value["upstream_commit"], str)
                or len(value["upstream_commit"]) != 40
                or any(char not in "0123456789abcdef" for char in value["upstream_commit"])
            ):
                raise ValueError
            datetime.fromisoformat(value["created_at"])
            # Version is metadata only, but cannot become an arbitrary stdout channel.
            if not value["upstream_version"].startswith("v") or any(
                char not in "0123456789.v-" for char in value["upstream_version"]
            ):
                raise ValueError
        elif set(value) not in ({"revision", "files"}, {"revision", "files", "interrupted_apply"}):
            raise ValueError
        elif "interrupted_apply" in value and not isinstance(value["interrupted_apply"], dict):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, RecursionError, CompanionError):
        raise CompanionError(
            "backup_rejected",
            "Backup must be an owned, single-link regular JSON file below 4 MiB with valid personality content.",
        ) from None


def list_personality_backups(state, *, limit=20, before=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise CompanionError("backup_rejected", "Backup page size must be between 1 and 100.")
    if before is not None:
        _backup_identifier(before)
    directory = safe_path(state.directory / "backups")
    if not directory.exists():
        return {"backups": [], "next_cursor": None}
    if not directory.is_dir() or directory.stat().st_uid != os.getuid():
        raise CompanionError("backup_rejected", "Backup directory must be owned by this user.")
    entries = []
    for path in directory.iterdir():
        try:
            if path.suffix != ".json":
                continue
            identifier = _backup_identifier(path.stem)
            entries.append((path.lstat().st_mtime_ns, identifier))
        except (CompanionError, OSError):
            continue
    entries.sort(reverse=True)
    if before is not None:
        position = next((i for i, entry in enumerate(entries) if entry[1] == before), None)
        if position is None:
            raise CompanionError("backup_rejected", "The backup cursor no longer exists; list from the first page.")
        entries = entries[position + 1 :]
    result = []
    for _, backup_id in entries[:limit]:
        try:
            value = read_personality_backup(state, backup_id)
            result.append(
                {
                    "backup_id": backup_id,
                    "status": "available",
                    "revision": value["revision"],
                    "purpose": value.get("purpose", "recovery" if "interrupted_apply" in value else "legacy"),
                    "created_at": value.get("created_at"),
                    "upstream_version": value.get("upstream_version"),
                    "upstream_commit": value.get("upstream_commit"),
                }
            )
        except CompanionError:
            result.append({"backup_id": backup_id, "status": "rejected"})
    return {"backups": result, "next_cursor": entries[limit - 1][1] if len(entries) > limit else None}


def restore_personality(state, home, backup_id, *, dry_run=False):
    backup = read_personality_backup(state, backup_id)
    result = {"status": "dry_run" if dry_run else "restored", "backup_id": backup_id, "next": "sync"}
    if not dry_run:
        with memory_locks(home):
            state.recover(home)
            current = snapshot(home)
            result["safety_backup_id"] = _write_personality_backup(state, current, "pre_restore")
            state.journal_apply(home, current, backup["files"], state.revision, state.baseline)
    return result


def upgrade_runtime(state, home, path, python, *, dry_run=False):
    if dry_run:
        return install_runtime(path, python, dry_run=True)
    backup_id = backup_personality(state, home, purpose="upgrade")
    try:
        result = install_runtime(path, python)
    except CompanionError as error:
        raise CompanionError(
            error.code,
            f"Pinned runtime repair failed; personality backup ID: {backup_id}. "
            "Use backups to inspect metadata, then retry repair or explicitly restore the backup. "
            "Subprocess output was suppressed.",
            error.exit_code,
        ) from None
    return {**result, "backup_id": backup_id}


_AUXILIARY_TASKS = (
    "vision",
    "compression",
    "skills_hub",
    "approval",
    "review",
    "mcp",
    "title_generation",
    # Implemented by agent/side_question.py in the pin, but absent from its
    # DEFAULT_CONFIG auxiliary blocks and provider-picker list.
    "side_question",
    "memory_query_rewrite",
    "tts_audio_tags",
    "triage_specifier",
    "kanban_decomposer",
    "profile_describer",
    "goal_judge",
    "curator",
    "monitor",
    "background_review",
    "moa_reference",
    "moa_aggregator",
    # Known auxiliary names in the pinned Kanban and Teams pipeline plugins.
    "kanban_estimator",
    "call",
)


def _launch_preflight(home, upstream):
    # The pinned upstream dotenv loader has no supported skip-env switch and
    # loads these paths before its managed overlay. Inspect presence only.
    for path in (home / ".env", home / ".op.env", upstream / ".env"):
        if os.path.lexists(path):
            raise CompanionError(
                "runtime_dotenv_unsupported",
                "Managed startup requires no .env/.op.env in the Hermes home and no .env in its upstream checkout. "
                "Move those files to an owner-controlled backup and register connections through MyHermes; "
                "no file was read, changed or removed.",
                3,
            )
    path = safe_path(home / "config.yaml")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    except OSError:
        raise CompanionError("runtime_config_rejected", "Hermes config.yaml could not be read safely.", 3) from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise ValueError("Unsupported file")
            raw = stream.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("Unsupported size")
        value = yaml.safe_load(raw) or {}
        if not isinstance(value, dict):
            raise ValueError("Unsupported document")
    except (OSError, ValueError, yaml.YAMLError, RecursionError):
        raise CompanionError(
            "runtime_config_rejected", "Hermes config.yaml must be a valid, owned regular YAML mapping below 1 MiB.", 3
        ) from None
    if value.get("secrets"):
        raise CompanionError(
            "runtime_external_secrets_unsupported",
            "Managed startup cannot use Hermes external secret sources. Move the secrets configuration "
            "to an owner-controlled backup and use MyHermes connection registration; existing config was preserved.",
            3,
        )
    providers = value.get("providers") or {}
    legacy = value.get("custom_providers") or []
    if not isinstance(providers, dict) or not isinstance(legacy, list):
        raise CompanionError("runtime_config_rejected", "Hermes provider configuration has an unsupported shape.", 3)
    entries = list(providers.items()) + [("", entry) for entry in legacy]
    for key, entry in entries:
        names = [key, entry.get("name", "")] if isinstance(entry, dict) else [key]
        if any(str(name).strip().lower() in ("myhermes", "custom:myhermes") for name in names):
            raise CompanionError(
                "runtime_provider_reserved",
                "The myhermes provider name is reserved for the process-only relay. Rename that entry "
                "in the owner config before managed startup; existing config was preserved.",
                3,
            )
    return value


def _managed_config(base_url, *, enabled_plugins=None):
    # JSON is valid YAML. The file contains only routing metadata and env names,
    # never a credential value. Managed leaves override user settings upstream.
    auxiliary = {
        name: {
            "provider": "myhermes",
            "model": "economy",
            "base_url": "",
            "api_key": "",
            "api_mode": "chat_completions",
            "extra_body": None,
        }
        for name in _AUXILIARY_TASKS
    }
    auxiliary.update({"transient_retries": 0, "free_only": True})
    config = {
        "model": {
            "default": "economy",
            "provider": "myhermes",
            "base_url": base_url,
            "api_mode": "chat_completions",
            "api_key": "",
            "extra_body": None,
        },
        "providers": {
            "myhermes": {
                "name": "myhermes",
                "api": base_url,
                "transport": "chat_completions",
                # The pinned upstream strips AUXILIARY_*_API_KEY from child
                # environments, including forced passthrough and shell snapshots.
                "key_env": "AUXILIARY_MYHERMES_API_KEY",
                "enabled": True,
                "default_model": "economy",
                "models": {"economy": {"supports_vision": False}},
                "discover_models": False,
            }
        },
        "agent": {"api_max_retries": 1},
        # Viewing a distributed skill is a read. Owner config must not turn
        # inline Markdown snippets into implicit shell execution during preview.
        "skills": {"inline_shell": False},
        "auxiliary": auxiliary,
        "fallback_model": None,
        "telemetry": {"shared_metrics": {"enabled": False, "send": False}},
    }
    if enabled_plugins is not None:
        config["plugins"] = {"enabled": enabled_plugins}
    return config


def _plugin_matches(path, files):
    if not path.is_dir() or path.is_symlink() or set(item.name for item in path.iterdir()) != set(files):
        return False
    for name, raw in files.items():
        target = safe_path(path / name)
        try:
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                    return False
                if stream.read(len(raw) + 1) != raw:
                    return False
        except (OSError, CompanionError):
            return False
    return True


@contextmanager
def _monitoring_plugin(home, user_config, active):
    if not active:
        yield None
        return
    config = user_config.get("plugins") or {}
    if not isinstance(config, dict):
        raise CompanionError("runtime_plugin_rejected", "Hermes plugins configuration must be a mapping.", 3)
    enabled, disabled = config.get("enabled", []), config.get("disabled", [])
    if (
        not isinstance(enabled, list)
        or not isinstance(disabled, list)
        or any(not isinstance(name, str) for name in [*enabled, *disabled])
    ):
        raise CompanionError("runtime_plugin_rejected", "Hermes plugin enable/disable lists must contain names.", 3)
    if any(name.endswith("myhermes-monitoring") for name in disabled):
        raise CompanionError(
            "runtime_plugin_disabled",
            "The published MyHermes monitoring plugin is disabled in owner config; "
            "remove that explicit disable entry before managed startup.",
            3,
        )
    source = Path(__file__).with_name("hermes_monitoring_plugin.py").read_bytes()
    files = {
        "__init__.py": source,
        "plugin.yaml": b'{"name":"myhermes-monitoring","version":"1.0.0","description":"Published metadata-only MyHermes tool observer"}\n',
    }
    directory = safe_path(private_dir(home / "plugins") / "myhermes-monitoring")
    if directory.exists():
        if not _plugin_matches(directory, files):
            raise CompanionError(
                "runtime_plugin_reserved",
                "Existing myhermes-monitoring plugin differs from the published package. "
                "Back it up outside the plugin directory before managed startup; existing files were preserved.",
                3,
            )
    else:
        private_dir(directory)
        for name, raw in files.items():
            atomic_bytes(directory / name, raw)
    # Persist only public source. Normal shutdown leaves this exact verified
    # source installed for next startup; only the ephemeral overlay enables it.
    yield list(dict.fromkeys([*enabled, "myhermes-monitoring"]))


def _environment_hint(user_config, installation_id):
    """Compose private prompt context in memory; never persist it or emit telemetry."""
    owner_hint = os.environ.get("HERMES_ENVIRONMENT_HINT", "")
    if not owner_hint.strip():
        agent = user_config.get("agent") or {}
        if not isinstance(agent, dict):
            raise CompanionError("runtime_config_rejected", "Hermes agent configuration must be a mapping.", 3)
        owner_hint = agent.get("environment_hint", "")
        if owner_hint is None:
            owner_hint = ""
        if not isinstance(owner_hint, str):
            raise CompanionError("runtime_config_rejected", "Hermes environment_hint must be text.", 3)
    try:
        installation = canonical_identifier(installation_id)
    except ValueError:
        raise CompanionError("runtime_identity_invalid", "The current installation identifier is invalid.", 3) from None
    host_os = operating_system()
    guide = (
        "MyHermes current installation_id=" + installation + "; Hermes host OS=" + host_os + ". "
        "This identifies the companion host, not a remote terminal backend. "
        "Shared SOUL, memory and skills may describe another environment's OS or paths; "
        "treat those descriptions as past observations, not current execution settings. "
        "Before operations, verify the actual terminal backend, OS and target paths using "
        "the Hermes runtime environment context and current tool observations. "
        "When saving environment-specific memory, include source installation_id="
        + installation
        + " and host OS="
        + host_os
        + "; record an observed remote backend OS/path separately."
    )
    return owner_hint + "\n\n" + guide if owner_hint else guide


@contextmanager
def relay_runtime_session(config, *, api, state_directory=None, allow_local_http=False, on_activity=None):
    """Yield the pinned command and ephemeral relay environment without changing user config.

    The caller holds the home session lock and shared runtime target admission.
    ``allow_local_http`` is a fixture switch, never inferred from the production
    config or environment.
    """
    upstream = safe_path(Path(config["upstream"]))
    command = verify_runtime(upstream)
    home = private_dir(Path(config["hermes_home"]))
    user_config = _launch_preflight(home, upstream)
    if api is None or not api.private_key or not api.installation_id:
        raise CompanionError("not_enrolled", "Managed Hermes startup requires an enrolled installation.", 3)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper()
        .removeprefix("_HERMES_FORCE_")
        .startswith(("OPENROUTER", "OPENAI_", "ANTHROPIC_", "LANGFUSE_", "HERMES_LANGFUSE_", "OTEL_"))
        and key.upper().removeprefix("_HERMES_FORCE_")
        not in (
            "HERMES_PROFILE",
            "HERMES_HOME",
            "HERMES_MANAGED_DIR",
            "HERMES_IGNORE_USER_CONFIG",
            "HERMES_INFERENCE_PROVIDER",
            "HERMES_STREAM_RETRIES",
            "AUXILIARY_MYHERMES_API_KEY",
            "MYHERMES_SESSION_TOKEN",
            "MYHERMES_STATE_DIR",
            "MYHERMES_MONITORING_URL",
            "HERMES_ENVIRONMENT_HINT",
        )
    }
    environment.update(
        {
            "HERMES_HOME": str(home),
            "HERMES_STREAM_RETRIES": "0",
            "HERMES_INFERENCE_PROVIDER": "myhermes",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HERMES_ENVIRONMENT_HINT": _environment_hint(user_config, api.installation_id),
        }
    )
    # The companion console entrypoint must remain available to managed skills
    # when the user launched it by absolute path outside an activated venv.
    companion_bin = Path(sys.executable).parent
    if (companion_bin / "myhermes").is_file():
        environment["PATH"] = str(companion_bin) + os.pathsep + environment.get("PATH", os.defpath)
    if state_directory is not None:
        environment["MYHERMES_STATE_DIR"] = str(safe_path(Path(state_directory)))
    temporary_parent = private_dir(home / ".myhermes-runtime")
    with _monitoring_plugin(home, user_config, on_activity is not None) as enabled_plugins:
        with RelayBridge(api, allow_local_http=allow_local_http, on_activity=on_activity) as bridge:
            with tempfile.TemporaryDirectory(prefix="launch-", dir=temporary_parent) as temporary:
                overlay = Path(temporary)
                atomic_json(overlay / "config.yaml", _managed_config(bridge.base_url, enabled_plugins=enabled_plugins))
                environment["HERMES_MANAGED_DIR"] = str(overlay)
                environment["AUXILIARY_MYHERMES_API_KEY"] = bridge.session_token
                if on_activity is not None:
                    environment["MYHERMES_MONITORING_URL"] = bridge.base_url + "/myhermes/tool-events"
                try:
                    yield command, environment
                finally:
                    environment.pop("AUXILIARY_MYHERMES_API_KEY", None)
                    environment.pop("HERMES_ENVIRONMENT_HINT", None)


_TERMINATION_GRACE_SECONDS = 10


def _run_managed_child(command, *, env, stdin, stdout, stderr):
    child, requested, deadline = None, None, None
    previous = {}

    def forward(signum, _frame):
        nonlocal requested, deadline
        if requested is None:
            requested = signum
            deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
        if child is not None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    try:
        for kind in (signal.SIGTERM, signal.SIGHUP):
            original = signal.getsignal(kind)
            try:
                signal.signal(kind, forward)
            except ValueError:
                raise CompanionError(
                    "runtime_main_thread_required", "Managed startup must run on the CLI's main thread.", 3
                ) from None
            previous[kind] = original
        child = subprocess.Popen(command, env=env, stdin=stdin, stdout=stdout, stderr=stderr)
        if requested is not None:
            # A signal during Popen construction is remembered until its child
            # handle exists. Never wait or extend the deadline in the handler.
            forward(requested, None)
        while True:
            try:
                code = child.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() >= deadline:
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
        if requested is not None:
            # Use the existing cancelled-session path only after the child has
            # stopped; both persona and skill finalizers still run under lock.
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(command, code)
    finally:
        if child is not None:
            if child.poll() is None:
                try:
                    child.kill()
                except ProcessLookupError:
                    pass
            while True:
                try:
                    child.wait(timeout=0.25)
                    break
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    # Retain handlers/outer session lock until the OS confirms
                    # reaping, even if another interrupt arrives during cleanup.
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
        for kind, original in previous.items():
            signal.signal(kind, original)


def start_runtime(config, *, api=None, state_directory=None, on_activity=None):
    # One environment home; never account-kind profiles. Runtime conversation goes
    # directly to the owner's terminal, not the structured companion stdout.
    try:
        # Buffered update mode requires seeking; terminal devices are streams.
        terminal = open("/dev/tty", "r+b", buffering=0)
    except OSError:
        raise CompanionError("terminal_required", "Run myhermes start in your own interactive terminal.", 3) from None
    with terminal:
        with relay_runtime_session(config, api=api, state_directory=state_directory, on_activity=on_activity) as (
            command,
            environment,
        ):

            def record(kind, attrs):
                if on_activity is not None:
                    try:
                        on_activity(kind, attrs)
                    except Exception:
                        pass

            began, outcome = time.monotonic(), "failed"
            record("runtime_start", {"outcome": "ok"})
            try:
                result = _run_managed_child(
                    [str(command)], env=environment, stdin=terminal, stdout=terminal, stderr=terminal
                )
                outcome = "ok" if result.returncode == 0 else "failed"
            except KeyboardInterrupt:
                outcome = "cancelled"
                raise
            finally:
                record(
                    "runtime_stop",
                    {"outcome": outcome, "duration_ms": min(86_400_000, int((time.monotonic() - began) * 1000))},
                )
    return {"status": "stopped", "runtime_exit_code": result.returncode}
