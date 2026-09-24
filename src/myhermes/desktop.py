"""Prepare and launch the native macOS Desktop under the companion lifecycle."""

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

from .desktop_adapter import adapted_source, source_hashes
from .errors import CompanionError
from .files import atomic_bytes, atomic_json, private_dir, safe_path


def _macos():
    if platform.system() != "Darwin":
        raise CompanionError(
            "desktop_os_unsupported", "Managed Desktop currently supports macOS. Use start for TUI.", 3
        )


def desktop_root(upstream):
    return safe_path(Path(upstream) / ".myhermes-desktop" / "source")


def _policy_bytes():
    return Path(__file__).with_name("desktop_policy.py").read_bytes()


def _source_bytes(root, name):
    # Only public source at the verified pin; never owner configuration.
    result = subprocess.run(
        ["git", "show", "HEAD:" + name], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True
    )
    return result.stdout


def _verify_source(root):
    from .runtime import UPSTREAM_COMMIT, checked

    if checked(["git", "rev-parse", "HEAD"], cwd=root) != UPSTREAM_COMMIT:
        raise CompanionError("desktop_source_mismatch", "Prepare Desktop for the supported pinned runtime.", 3)
    changed = checked(["git", "diff", "--no-ext-diff", "--name-only", "HEAD"], cwd=root).splitlines()
    if set(changed) != set(source_hashes()):
        raise CompanionError("desktop_source_modified", "Managed Desktop source has unexpected changes.", 3)
    extras = set(checked(["git", "ls-files", "--others", "--exclude-standard"], cwd=root).splitlines())
    if extras - {"myhermes_desktop_policy.py", ".myhermes-build.json"}:
        raise CompanionError("desktop_source_modified", "Managed Desktop source contains unexpected extra files.", 3)
    for name in source_hashes():
        if safe_path(root / name).read_bytes() != adapted_source(name, _source_bytes(root, name)):
            raise CompanionError("desktop_source_modified", "Managed Desktop source has unexpected changes.", 3)
    if safe_path(root / "myhermes_desktop_policy.py").read_bytes() != _policy_bytes():
        raise CompanionError("desktop_source_modified", "Managed Desktop policy differs from this companion.", 3)


def _bundle(root):
    matches = list((root / "apps/desktop/release").glob("mac*/Hermes.app"))
    if len(matches) != 1:
        raise CompanionError("desktop_missing", "Run myhermes prepare-desktop before myhermes desktop.", 3)
    return safe_path(matches[0])


def _artifact_hashes(bundle):
    resources = bundle / "Contents/Resources"
    files = [bundle / "Contents/MacOS/Hermes", resources / "app.asar"]
    dist = resources / "app.asar.unpacked/dist"
    if not (dist / "electron-main.mjs").is_file():
        raise CompanionError("desktop_missing", "Desktop build is incomplete. Run prepare-desktop again.", 3)
    files.extend(sorted(path for path in dist.rglob("*") if path.is_file()))
    return {str(path.relative_to(bundle)): hashlib.sha256(safe_path(path).read_bytes()).hexdigest() for path in files}


def verify_desktop(upstream):
    _macos()
    root = desktop_root(upstream)
    if not (root / ".myhermes-build.json").is_file():
        raise CompanionError("desktop_missing", "Run myhermes prepare-desktop before myhermes desktop.", 3)
    _verify_source(root)
    bundle = _bundle(root)
    marker = json.loads(safe_path(root / ".myhermes-build.json").read_text())
    if marker != {"schema_version": "1", "artifacts": _artifact_hashes(bundle)}:
        raise CompanionError("desktop_build_modified", "Desktop build changed. Run prepare-desktop again.", 3)
    return root, bundle / "Contents/MacOS/Hermes"


def prepare_desktop(config, *, dry_run=False):
    from .runtime import UPSTREAM_COMMIT, checked, verify_runtime

    _macos()
    upstream = safe_path(Path(config["upstream"]))
    verify_runtime(upstream)
    if dry_run:
        return {
            "status": "dry_run",
            "interface": "desktop",
            "hermes_commit": UPSTREAM_COMMIT,
            "downloads": "pinned_npm_dependencies",
            "production_access": False,
        }
    npm = shutil.which("npm")
    if not npm:
        raise CompanionError("desktop_node_required", "Install Node.js 24.11 or newer in the 24.x line, then retry.", 3)
    root = desktop_root(upstream)
    if not root.exists():
        parent = private_dir(root.parent)
        with tempfile.TemporaryDirectory(prefix="prepare-", dir=parent) as temporary:
            staging = Path(temporary) / "source"
            checked(["git", "clone", "--no-hardlinks", "--no-checkout", str(upstream), str(staging)])
            checked(["git", "checkout", "--detach", UPSTREAM_COMMIT], cwd=staging)
            for name in source_hashes():
                source = safe_path(staging / name)
                atomic_bytes(source, adapted_source(name, source.read_bytes()))
            atomic_bytes(staging / "myhermes_desktop_policy.py", _policy_bytes())
            os.rename(staging, root)
    _verify_source(root)
    # Build with a temporary home and no enrollment, relay, or native-store access.
    with tempfile.TemporaryDirectory(prefix="build-home-", dir=root.parent) as temporary:
        env = {
            key: value
            for key, value in os.environ.items()
            if key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SHELL", "USER", "LOGNAME")
        }
        env.update(
            {
                "HOME": temporary,
                "HERMES_HOME": temporary,
                "CSC_IDENTITY_AUTO_DISCOVERY": "false",
                "npm_config_cache": str(private_dir(root.parent / "npm-cache")),
                "ELECTRON_CACHE": str(private_dir(root.parent / "electron-cache")),
            }
        )
        checked([npm, "ci", "--no-audit", "--no-fund"], cwd=root, env=env)
        checked([npm, "run", "pack"], cwd=root / "apps/desktop", env=env)
    _verify_source(root)
    atomic_json(root / ".myhermes-build.json", {"schema_version": "1", "artifacts": _artifact_hashes(_bundle(root))})
    return {"status": "prepared", "interface": "desktop", "hermes_commit": UPSTREAM_COMMIT, "next": "desktop"}


def desktop_environment(config, environment, root):
    home = safe_path(Path(config["hermes_home"]))
    # Clear Desktop boot/test/remote/dev-server hooks and interpreter injection.
    env = {
        key: value
        for key, value in environment.items()
        if not key.upper()
        .removeprefix("_HERMES_FORCE_")
        .startswith(("HERMES_DESKTOP_", "MYHERMES_DESKTOP_", "ELECTRON_", "NODE_", "PYTHONPATH", "PYTHONHOME"))
    }
    overlay = json.loads((Path(env["HERMES_MANAGED_DIR"]) / "config.yaml").read_text())
    env.update(
        {
            "HERMES_DESKTOP_HERMES_ROOT": str(root),
            "HERMES_DESKTOP_PYTHON": str(Path(config["upstream"]) / ".venv/bin/python"),
            "HERMES_DESKTOP_USER_DATA_DIR": str(private_dir(home / ".myhermes-desktop")),
            "HERMES_DESKTOP_APP_NAME": "MyHermes",
            "HERMES_DESKTOP_CDP_PORT": "off",
            "MYHERMES_DESKTOP_HOME": str(home),
            "MYHERMES_DESKTOP_RELAY_URL": overlay["model"]["base_url"],
            "PYTHONPATH": str(root),
        }
    )
    return env


def start_desktop(config, *, api=None, state_directory=None, on_activity=None):
    from .runtime import start_runtime

    # Reuse terminal ownership, relay, monitoring and child/session finalization.
    return start_runtime(config, api=api, state_directory=state_directory, on_activity=on_activity, desktop=True)
