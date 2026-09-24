"""Explicit companion self-update using a company-selected, hash-pinned public wheel."""

from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.parser import BytesParser

from . import __version__
from .api import NoRedirect
from .connection_schema import exact, version
from .errors import CompanionError
from .files import atomic_bytes, file_lock, private_dir
from .runtime_lock import runtime_target_lock

MAX_WHEEL_BYTES = 50 * 1024 * 1024
RELEASE_ROOT = "https://github.com/nyasukun/myhermes-starter/releases/download/"


def parse_release(value):
    exact(value, ("version", "wheel_url", "sha256"))
    release_version = version(value["version"])
    expected = RELEASE_ROOT + "v" + release_version + "/myhermes_companion-" + release_version + "-py3-none-any.whl"
    if (
        value["wheel_url"] != expected
        or not isinstance(value["sha256"], str)
        or not re.fullmatch("[a-f0-9]{64}", value["sha256"])
    ):
        raise CompanionError("companion_release_rejected", "The release URL or checksum is invalid.", 5)
    return value


def release_status(api):
    code, value = api.request("GET", "/v1/companion-release")
    if code != 200:
        raise CompanionError("companion_release_unavailable", "The company update selection is unavailable.", 5)
    exact(value, ("revision", "release", "updated_at"))
    if type(value["revision"]) is not int or value["revision"] < 0:
        raise CompanionError("companion_release_rejected", "Invalid release revision.", 5)
    release = value["release"]
    if release is not None:
        parse_release(release)
    available = release is not None and tuple(map(int, release["version"].split("."))) > tuple(
        map(int, __version__.split("."))
    )
    return {
        "current_version": __version__,
        "update_available": available,
        "release": release,
        "command": 'myhermes --state-dir "$MYHERMES_STATE_DIR" self-update --apply' if available else None,
    }


@contextmanager
def companion_admission(*, writer=False):
    if sys.prefix == sys.base_prefix:
        if writer:
            raise CompanionError("companion_venv_required", "Use the companion's dedicated virtual environment.", 3)
        yield
        return
    with runtime_target_lock(Path(sys.prefix), writer=writer):
        yield


def _update_environment():
    if sys.prefix == sys.base_prefix:
        raise CompanionError(
            "companion_venv_required", "Self-update requires the companion's dedicated virtual environment.", 3
        )
    distribution = importlib.metadata.distribution("myhermes-companion")
    direct = distribution.read_text("direct_url.json")
    if direct and json.loads(direct).get("dir_info", {}).get("editable"):
        raise CompanionError(
            "companion_editable_install",
            "Update this editable source checkout with its development workflow; self-update supports wheel installations.",
            3,
        )
    location = Path(distribution.locate_file("")).resolve()
    if not location.is_relative_to(Path(sys.prefix).resolve()) or Path(sys.prefix).stat().st_uid != os.getuid():
        raise CompanionError(
            "companion_venv_required",
            "The installed companion must belong to this user's current virtual environment.",
            3,
        )
    return Path(sys.executable)


def download_wheel(release, *, opener=None):
    parse_release(release)
    opener = opener or urllib.request.build_opener(NoRedirect())
    url = release["wheel_url"]
    for _ in range(5):
        request = urllib.request.Request(
            url, headers={"Accept": "application/octet-stream", "User-Agent": "myhermes-updater/1"}
        )
        try:
            try:
                response = opener.open(request, timeout=30)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                if response.status in (301, 302, 303, 307, 308):
                    target = urllib.parse.urlsplit(urllib.parse.urljoin(url, response.headers.get("Location", "")))
                    if (
                        target.scheme != "https"
                        or target.hostname
                        not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
                        or target.username
                        or target.password
                        or target.fragment
                        or target.port not in (None, 443)
                    ):
                        raise ValueError()
                    url = target.geturl()
                    continue
                if response.status != 200:
                    raise ValueError()
                raw = response.read(MAX_WHEEL_BYTES + 1)
            if len(raw) > MAX_WHEEL_BYTES or hashlib.sha256(raw).hexdigest() != release["sha256"]:
                raise ValueError()
            return raw
        except (OSError, ValueError, urllib.error.URLError):
            raise CompanionError(
                "companion_download_failed",
                "The selected public wheel could not be downloaded and verified. No package was installed.",
                5,
            ) from None
    raise CompanionError("companion_download_failed", "The release exceeded the allowed redirect limit.", 5)


def verify_wheel(path, release):
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(names) != len(set(names)) or len(metadata) != 1 or archive.getinfo(metadata[0]).file_size > 65536:
                raise ValueError()
            value = BytesParser().parsebytes(archive.read(metadata[0]))
            if value["Name"] != "myhermes-companion" or value["Version"] != release["version"]:
                raise ValueError()
            # --no-index does not block direct URL dependencies in wheel metadata.
            if any("@" in requirement for requirement in value.get_all("Requires-Dist", [])):
                raise ValueError()
            if any(name.startswith("/") or ".." in name.split("/") or "\\" in name for name in names):
                raise ValueError()
    except (OSError, ValueError, zipfile.BadZipFile, KeyError):
        raise CompanionError(
            "companion_wheel_rejected", "The verified artifact is not the selected companion wheel.", 5
        ) from None


def _run(command):
    from .runtime import checked

    try:
        return checked(command, env={**os.environ, "PIP_CONFIG_FILE": os.devnull})
    except CompanionError as error:
        raise CompanionError(
            "companion_update_failed",
            "The package operation did not complete; use the retained verified wheel for repair.",
            130 if error.exit_code == 130 else 5,
        ) from None


def update_companion(args, config, directory, api):
    selected = release_status(api)
    if not args.apply:
        return {"status": "dry_run" if args.dry_run else "checked", "python": sys.executable, **selected}
    if not selected["update_available"]:
        return {"status": "current" if selected["release"] else "no_release_selected", **selected}
    python = _update_environment()
    release = selected["release"]
    with file_lock(directory / "companion.lock"), file_lock(Path(config["hermes_home"]) / ".myhermes-session.lock"):
        updates = private_dir(directory / "updates")
        raw = download_wheel(release)
        with tempfile.TemporaryDirectory(prefix="verify-", dir=updates) as temporary:
            candidate = Path(temporary) / Path(urllib.parse.urlsplit(release["wheel_url"]).path).name
            atomic_bytes(candidate, raw)
            verify_wheel(candidate, release)
        retained = private_dir(updates / release["sha256"]) / candidate.name
        atomic_bytes(retained, raw)
        command = [
            str(python),
            "-I",
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--no-cache-dir",
            str(retained),
        ]
        try:
            _run([*command, "--dry-run"])
            _run(command)
            installed = _run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "from importlib.metadata import version; print(version('myhermes-companion'))",
                ]
            )
            if installed != release["version"]:
                raise CompanionError(
                    "companion_update_unverified", "The installed version did not match the selected release.", 5
                )
        except CompanionError as error:
            raise CompanionError(
                error.code,
                error.message + f" Retained wheel: {retained}. Companion Python: {python}.",
                error.exit_code,
            ) from None
    return {
        "status": "updated",
        "previous_version": __version__,
        "version": installed,
        "restart_required": True,
        "verified_wheel": str(retained),
        "python": str(python),
    }
