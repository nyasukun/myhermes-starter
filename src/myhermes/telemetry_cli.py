"""Owner inspection and non-blocking explicit command metadata instrumentation."""

from importlib.resources import files
import json
from pathlib import Path
import sys
import threading
import time

from . import __version__
from .errors import CompanionError
from .files import export_json
from .telemetry import Telemetry
from .telemetry_contract import MANIFEST_VERSION


def manifest():
    value = json.loads(files("myhermes").joinpath("monitoring-manifest.json").read_text(encoding="utf-8"))
    if value.get("version") != MANIFEST_VERSION:
        raise CompanionError(
            "monitoring_manifest_mismatch", "The installed public monitoring contract is inconsistent."
        )
    return value


def register_monitoring_commands(sub):
    parent = sub.add_parser("monitoring", help="Inspect the public collection contract and safe local delivery state")
    commands = parent.add_subparsers(dest="monitoring_command", required=True)
    show = commands.add_parser("manifest", help="Show the installed public collection contract without network access")
    show.add_argument("--remote", action="store_true", help="Verify the authenticated server uses this exact manifest")
    inspect = commands.add_parser("inspect", help="Inspect pending and recent delivered metadata")
    inspect.add_argument("--include-wire", action="store_true", help="Include exact safe signed audit and OTLP bytes")
    inspect.add_argument("--limit", type=int, default=100)
    export = commands.add_parser("export", help="Export safe metadata and exact wire records to a new owner-local file")
    export.add_argument("--export-dir", type=Path, required=True)
    export.add_argument("--limit", type=int, default=100)
    flush = commands.add_parser("flush", help="Retry immutable pending audit and OTLP batches")
    flush.add_argument("--limit", type=int, default=100)


def monitoring_command(args, config, directory, *, owner_api):
    local = manifest()
    if args.monitoring_command == "manifest":
        if args.remote:
            _, remote = owner_api(config).request("GET", "/v1/monitoring/manifest")
            if remote != local:
                raise CompanionError(
                    "monitoring_manifest_mismatch",
                    "The server and installed public monitoring manifest differ; review the published update.",
                    5,
                )
        return {"status": "verified" if args.remote else "local", "manifest": local}
    if not 1 <= args.limit <= 1000:
        raise CompanionError("invalid_limit", "Monitoring limits must be from 1 to 1000.")
    api = owner_api(config)
    telemetry = Telemetry(config, directory, api, api.private_key, client_version=__version__)
    try:
        if args.monitoring_command == "flush":
            result = telemetry.flush(limit=args.limit)
            return {"status": "deferred" if result["deferred"] else "acknowledged", **result}
        result = telemetry.inspect(
            include_wire=args.monitoring_command == "export" or args.include_wire, limit=args.limit
        )
        if args.monitoring_command == "export":
            export_json(args.export_dir / "monitoring-records.json", result)
            return {"status": "exported", "records": len(result["records"]), "manifest_version": MANIFEST_VERSION}
        return {"status": "local", **result}
    finally:
        telemetry.close()


def command_kind(args):
    if args.command in ("sync", "resolve", "edit", "restore", "recover"):
        return "sync"
    if args.command in ("install-runtime", "upgrade"):
        return "runtime_update"
    if args.command == "enroll":
        return "enrollment"
    if args.command == "skills" and args.skill_command in (
        "sync",
        "import",
        "delete",
        "derive",
        "resolve",
        "recover",
        "restore",
    ):
        return "skill_change"
    if args.command == "connections" and args.connection_command in (
        "add",
        "resume",
        "cancel",
        "authorize",
        "test",
        "forget",
    ):
        return "connection_change"
    if args.command == "connections" and args.connection_command == "read":
        return "tool"
    return None


class CommandMonitoring:
    """Instrumentation never changes an operation's result or catches its user content."""

    def __init__(self, directory, config_loader, api_factory, *, offline=False):
        self.directory, self.config_loader, self.api_factory = directory, config_loader, api_factory
        self.offline = offline
        self.telemetry = None
        self.deferred = False
        self.last = None
        self.attempted = False
        self.lock = threading.RLock()

    def _get(self):
        if self.telemetry is None:
            config = self.config_loader(self.directory)
            if not config.get("installation_id"):
                return None
            api = self.api_factory(config)
            # Monitoring runs after the main operation; limit optional delivery latency.
            api.timeout = min(api.timeout, 5)
            self.telemetry = Telemetry(config, self.directory, api, api.private_key, client_version=__version__)
        return self.telemetry

    def record(self, kind, attrs):
        with self.lock:
            self.attempted = True
            try:
                telemetry = self._get()
                if telemetry is not None:
                    telemetry.record(kind, attrs)
            except Exception:
                self.deferred = True

    def finish(self):
        try:
            if self.telemetry is not None:
                self.last = self.telemetry.inspect(limit=1)
                if not self.offline:
                    self.last = self.telemetry.flush(limit=100)
                    self.deferred |= self.last["deferred"]
        except Exception:
            self.deferred = True
        finally:
            if self.telemetry is not None:
                try:
                    self.telemetry.close()
                except Exception:
                    self.deferred = True
        if self.deferred:
            print(
                "Monitoring metadata is deferred or unavailable. Use myhermes monitoring inspect/flush to review delivery.",
                file=sys.stderr,
            )
        result = {
            "manifest_version": MANIFEST_VERSION,
            "status": "deferred" if self.deferred else "queued" if self.offline else "acknowledged",
        }
        if self.telemetry is None:
            result["status"] = "deferred" if self.deferred else "starts_after_enrollment"
        if self.last:
            result.update(
                {key: self.last[key] for key in ("pending_events", "sent_events", "dropped_events") if key in self.last}
            )
        return result


def outcome(result):
    if result.get("status") == "conflict" or result.get("conflicts", 0):
        return "conflict"
    if result.get("runtime_exit_code", 0):
        return "failed"
    if result.get("status") in ("pending", "offline_cached"):
        return "unknown"
    return "ok"


def duration_ms(started):
    return min(86_400_000, max(0, int((time.monotonic() - started) * 1000)))
