"""Structured metadata-only CLI. Credentials stay in native keyring and memory."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import platform
import time
import urllib.parse
import uuid
import webbrowser

from . import __version__
from .api import API, validate_server
from .auth import SecureKeyStore, assertion, public_jwk
from .connection_cli import execute_connection_command, register_connection_commands
from .errors import CompanionError
from .local_config import (
    bound_config_read,
    canonical_identifier,
    config_read as config_read,
    enrollment_challenge,
    enrollment_code,
    marker_read,
)
from .identity_recovery import identity_command, re_enroll
from .output_metadata import metadata_page
from .files import (
    LIMITS,
    MAX_BYTES,
    atomic_content,
    atomic_json,
    export_json,
    file_lock,
    memory_locks,
    private_dir,
    safe_path,
    snapshot,
    validate_content,
)
from .runtime import (
    UPSTREAM_VERSION,
    backup_personality,
    install_runtime,
    list_personality_backups,
    operating_system,
    restore_personality,
    start_runtime,
    upgrade_runtime,
    verify_runtime,
)
from .runtime_lock import runtime_target_lock
from .state import State
from .session_sync import finish_managed_session
from .skill_cli import boundary_sync, execute_skill_command, register_skill_commands
from .sync import Synchronizer, remote_snapshot
from .sync_ack import SyncAcknowledgements, inspect_ack, manifest as sync_status_manifest
from .telemetry_cli import (
    CommandMonitoring,
    command_kind,
    duration_ms,
    manifest,
    monitoring_command,
    outcome,
    register_monitoring_commands,
)


def parser():
    value = argparse.ArgumentParser(prog="myhermes", description="Owner-scoped Hermes companion (JSON metadata output)")
    value.add_argument(
        "--state-dir",
        type=Path,
        default=Path.home() / ".local/state/myhermes",
        help="Independent state directory for this installation",
    )
    value.add_argument("--version", action="version", version=__version__)
    sub = value.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup")
    setup.add_argument("--server", required=True)
    setup.add_argument("--hermes-home", type=Path, required=True)
    setup.add_argument("--upstream", type=Path, required=True)
    setup.add_argument("--label", default="My Hermes environment")
    setup.add_argument("--allow-local-http", action="store_true", help="Explicit loopback-only development transport")
    setup.add_argument("--dry-run", action="store_true")
    enroll = sub.add_parser("enroll")
    enroll.add_argument(
        "--no-browser", action="store_true", help="Show registration instructions only on the controlling terminal"
    )
    enroll.add_argument("--wait-seconds", type=int, default=180)
    enroll.add_argument("--dry-run", action="store_true")
    replacement = sub.add_parser("re-enroll", help="Replace this installation identity for the same owner")
    replacement.add_argument("--dry-run", action="store_true")
    replacement.add_argument("--no-browser", action="store_true")
    replacement.add_argument("--wait-seconds", type=int, default=180)
    replacement_action = replacement.add_mutually_exclusive_group()
    replacement_action.add_argument(
        "--new", action="store_true", help="Explicitly start another replacement after completion/cancellation"
    )
    replacement_action.add_argument(
        "--cancel", action="store_true", help="Abandon an unpublished candidate, retaining the old identity"
    )
    for command in ("inspect", "sync"):
        sub.add_parser(command).add_argument("--dry-run", action="store_true")
    history = sub.add_parser("history")
    history.add_argument("--revision", type=int)
    history.add_argument("--before", type=int, help="History cursor returned by the prior page")
    history.add_argument("--export-dir", type=Path)
    conflicts = sub.add_parser("conflicts")
    conflicts.add_argument("--update-id")
    conflicts.add_argument("--before", type=int, help="Conflict cursor returned by the prior page")
    conflicts.add_argument("--export-dir", type=Path)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("update_id")
    resolve.add_argument("--choice", required=True, choices=("local", "remote"))
    resolve.add_argument("--dry-run", action="store_true")
    edit = sub.add_parser("edit")
    edit.add_argument("--path", required=True, choices=tuple(LIMITS))
    source = edit.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--delete", action="store_true")
    edit.add_argument("--dry-run", action="store_true")
    for name in ("install-runtime", "upgrade"):
        command = sub.add_parser(name)
        command.add_argument("--python", default="python3.11")
        command.add_argument("--dry-run", action="store_true")
    sub.add_parser("backup").add_argument("--dry-run", action="store_true")
    backups = sub.add_parser("backups")
    backups.add_argument("--limit", type=int, default=20)
    backups.add_argument("--before", help="Backup cursor from the previous page")
    rollback = sub.add_parser("restore")
    rollback.add_argument("backup_id")
    rollback.add_argument("--dry-run", action="store_true")
    recover = sub.add_parser("recover")
    recover.add_argument("--choice", required=True, choices=("local", "target"))
    recover.add_argument("--dry-run", action="store_true")
    start = sub.add_parser("start")
    start.add_argument("--offline", action="store_true")
    start.add_argument("--dry-run", action="store_true")
    register_connection_commands(sub)
    register_skill_commands(sub)
    register_monitoring_commands(sub)
    return value


def enroll(config, directory, args):
    if args.dry_run:
        return {
            "status": "dry_run",
            "credential_store": "native_os",
            "approval": "browser_owner_required",
            "monitoring": {"manifest_version": "1.0.0", "collection": "public_metadata_only_after_enrollment"},
            "sync_paths": list(LIMITS),
        }
    if config.get("installation_id"):
        return {"status": "already_enrolled", "installation_id": config["installation_id"]}
    try:
        terminal = open("/dev/tty", "w")
    except OSError:
        raise CompanionError(
            "terminal_required",
            "Run enrollment in your own interactive terminal; registration codes are never printed to captured output.",
            3,
        ) from None
    terminal.close()
    store = SecureKeyStore()
    key = store.create(config["key_id"])
    api = API(config["server"])
    enrollment = config.get("enrollment")
    if enrollment is not None and enrollment.get("expires_at", 0) <= time.time():
        config.pop("enrollment", None)
        atomic_json(directory / "config.json", config)
        enrollment = None
    if enrollment is None:
        _, enrollment = api._request(
            "POST",
            "/v1/enrollments",
            {
                "schema_version": "1",
                "label": config["label"],
                "os": config["os"],
                "arch": platform.machine(),
                "client_version": __version__,
                "public_jwk": public_jwk(key),
            },
        )
        try:
            metadata, code = enrollment_challenge(enrollment, config["server"])
        except (KeyError, TypeError, ValueError):
            raise CompanionError("schema_rejected", "Invalid enrollment response.") from None
        # The one-time registration code is also kept in native secure storage.
        try:
            store.backend.set_password(store.SERVICE, config["key_id"] + ":enrollment-code", code)
        except Exception:
            raise CompanionError(
                "secure_store_unavailable", "Unable to save the enrollment code to native secure storage.", 3
            ) from None
        config["enrollment"] = metadata
        atomic_json(directory / "config.json", config)
        enrollment = config["enrollment"]
    else:
        try:
            code = store.backend.get_password(store.SERVICE, config["key_id"] + ":enrollment-code")
        except Exception:
            code = None
        if not code:
            raise CompanionError(
                "enrollment_code_missing",
                "Enrollment code is unavailable in the secure store; restart enrollment from a new state directory.",
                3,
            )
        try:
            code = enrollment_code(code)
        except ValueError:
            raise CompanionError(
                "schema_rejected", "Invalid stored enrollment code; pending registration was preserved."
            ) from None
    expected_origin = urllib.parse.urlsplit(config["server"])
    verification = urllib.parse.urlsplit(enrollment["verification_uri"])
    if (verification.scheme, verification.netloc) != (expected_origin.scheme, expected_origin.netloc):
        raise CompanionError(
            "verification_origin_rejected", "Enrollment verification must use the configured server origin.", 5
        )
    url = enrollment["verification_uri"].split("#", 1)[0]
    # Browser launchers may put the URL in subprocess argv. Keep the one-time code
    # out of that URL and write it solely to the owner's controlling terminal.
    with open("/dev/tty", "w") as terminal:
        terminal.write("Verify this environment in your browser and enter this one-time code: " + code + "\n")
        terminal.write("Approval page: " + url + "\n")
    if not args.no_browser:
        webbrowser.open(url)
    deadline = time.monotonic() + max(0, min(args.wait_seconds, 600))
    endpoint = "/v1/enrollments/" + enrollment["enrollment_id"] + "/complete"
    while True:
        status, response = api._request(
            "POST",
            endpoint,
            {"client_assertion": assertion(key, enrollment["enrollment_id"], config["server"] + endpoint)},
        )
        if status == 200:
            try:
                installation_id = canonical_identifier(response["installation_id"])
                person_id = canonical_identifier(response["person_id"])
            except (ValueError, KeyError, TypeError):
                raise CompanionError(
                    "schema_rejected",
                    "Enrollment returned invalid identity metadata; the pending request was preserved.",
                ) from None
            config["installation_id"] = installation_id
            config["person_id"] = person_id
            config.pop("enrollment", None)
            atomic_json(directory / "config.json", config)
            try:
                store.backend.delete_password(store.SERVICE, config["key_id"] + ":enrollment-code")
            except Exception:
                pass  # Expired one-time code cannot authenticate an installation.
            return {"status": "enrolled", "installation_id": installation_id}
        if status != 202 or response.get("status") != "pending":
            raise CompanionError(
                "enrollment_rejected", "Enrollment did not complete. Verify approval and request expiration.", 5
            )
        if time.monotonic() >= deadline:
            return {"status": "pending", "retry": "enroll"}
        time.sleep(2)


def owner_api(config):
    installation_id = config.get("installation_id")
    if not installation_id:
        raise CompanionError("not_enrolled", "Enroll this environment before owner API access.", 3)
    key = SecureKeyStore().load(config["key_id"])
    if key is None:
        raise CompanionError(
            "key_missing",
            "This environment's key is missing. Use re-enroll in the original home; copied environments need independent enrollment.",
            3,
        )
    return API(config["server"], key, installation_id)


def _execute(args, on_activity=None):
    directory = Path(os.path.abspath(args.state_dir))
    if args.command == "re-enroll":
        return re_enroll(directory, args, enroll=enroll, owner_api=owner_api)
    if args.command == "monitoring" and args.monitoring_command == "manifest" and not args.remote:
        return {"status": "local", "manifest": manifest()}
    if args.command == "setup":
        server = validate_server(args.server, args.allow_local_http)
        home, upstream = safe_path(args.hermes_home), safe_path(args.upstream)
        current_os = operating_system()
        if len(args.label) > 80 or not args.label.strip():
            raise CompanionError("invalid_label", "Environment label must contain 1 to 80 characters.")
        if args.dry_run:
            return {
                "status": "dry_run",
                "os": current_os,
                "sync_paths": list(LIMITS),
                "hermes_version": UPSTREAM_VERSION,
            }
        private_dir(directory)
        with file_lock(directory / "companion.lock"):
            if (directory / "config.json").exists():
                existing = bound_config_read(directory)
                if (existing["server"], existing["hermes_home"], existing["upstream"]) != (
                    server,
                    str(home),
                    str(upstream),
                ):
                    raise CompanionError(
                        "already_configured", "Use a new state directory for a different environment or home.", 3
                    )
                return {"status": "already_configured"}
            private_dir(home)
            if directory == home or directory in home.parents or home in directory.parents:
                raise CompanionError(
                    "overlapping_state", "State and Hermes home must be independent non-nested directories."
                )
            with file_lock(home / ".myhermes-session.lock"):
                marker = home / ".myhermes-installation.json"
                if marker.exists():
                    binding = marker_read(marker)
                    if binding.get("state_directory") != str(directory):
                        raise CompanionError(
                            "home_already_bound",
                            "This home belongs to another state directory. Create an independent home.",
                            3,
                        )
                    key_id = str(uuid.UUID(binding["key_id"]))
                else:
                    key_id = str(uuid.uuid4())
                atomic_json(marker, {"key_id": key_id, "state_directory": str(directory)})
                atomic_json(
                    directory / "config.json",
                    {
                        "schema_version": "1",
                        "server": server,
                        "hermes_home": str(home),
                        "upstream": str(upstream),
                        "label": args.label,
                        "os": current_os,
                        "key_id": key_id,
                        "agent_instance_id": str(uuid.uuid4()),
                        "allow_local_http": args.allow_local_http,
                    },
                )
            return {"status": "configured", "os": current_os, "hermes_version": UPSTREAM_VERSION}
    config = bound_config_read(directory)
    if args.command == "monitoring":
        return monitoring_command(args, config, directory, owner_api=owner_api)
    if args.command in ("templates", "connections"):
        return execute_connection_command(args, config, directory, owner_api=owner_api)
    home = Path(config["hermes_home"])
    if args.command == "skills":
        return execute_skill_command(args, config, directory, owner_api=owner_api)
    with ExitStack() as stack:
        stack.enter_context(file_lock(directory / "companion.lock"))
        if args.command == "enroll":
            return enroll(config, directory, args)
        state = State(directory)
        stack.callback(state.close)
        if args.command == "inspect":
            return {
                "status": "ok",
                "version": __version__,
                "os": config["os"],
                "enrolled": bool(config.get("installation_id")),
                "installation_id": config.get("installation_id"),
                "revision": state.revision,
                "pending": len(state.items("pending")),
                "conflicts": len(state.items("conflict")),
                "recovery_pending": state.get("apply_journal") is not None,
                "session_checkpoint_pending": state.get("session_checkpoint") is not None,
                "sync_ack_pending": state.get("sync_ack_pending") is not None,
                "application_reports": {
                    "manifest": sync_status_manifest(),
                    "delivery": inspect_ack(state, config.get("installation_id")),
                },
                "sync_paths": list(LIMITS),
                "monitoring": {
                    "manifest_version": "1.0.0",
                    "collection": "public_metadata_only",
                    "inspection": "monitoring inspect",
                    "contract": "monitoring manifest",
                },
                "server": config["server"],
                "transmitted_fields": {
                    "enrollment": ["label", "os", "arch", "client_version", "public_jwk"],
                    "sync": [
                        "schema_version",
                        "update_id",
                        "base_revision",
                        "allowlisted_file_content",
                        "resolves_update_id",
                    ],
                    "telemetry": manifest()["otlp"]["span_attributes"],
                },
                "credentials": "native_os_store_only",
                "capabilities": {
                    "personality_sync": True,
                    "connectors": ["github@1.0.0"],
                    "hermes_skill_packages": True,
                    "telemetry": True,
                    "llm_relay": True,
                },
            }
        stack.enter_context(file_lock(home / ".myhermes-session.lock"))
        if args.command in ("install-runtime", "upgrade", "start") and not args.dry_run:
            stack.enter_context(runtime_target_lock(Path(config["upstream"]), writer=args.command != "start"))
        if args.command in ("install-runtime", "upgrade"):
            if args.command == "upgrade":
                return upgrade_runtime(state, home, Path(config["upstream"]), args.python, dry_run=args.dry_run)
            return install_runtime(Path(config["upstream"]), args.python, dry_run=args.dry_run)
        if args.command == "backup":
            if args.dry_run:
                snapshot(home)
                return {"status": "dry_run", "revision": state.revision, "paths": list(LIMITS)}
            return {"status": "backed_up", "backup_id": backup_personality(state, home), "revision": state.revision}
        if args.command == "backups":
            return {"status": "ok", **list_personality_backups(state, limit=args.limit, before=args.before)}
        if args.command == "recover":
            with memory_locks(home):
                journal = state.get("apply_journal")
                if journal is None:
                    return {"status": "current", "recovery_pending": False}
                if args.dry_run:
                    return {"status": "dry_run", "choice": args.choice, "revision": journal["revision"]}
                current = snapshot(home)
                backup_id = str(uuid.uuid4())
                atomic_json(
                    directory / "backups" / (backup_id + ".json"),
                    {"revision": state.revision, "files": current, "interrupted_apply": journal},
                )
                target = current if args.choice == "local" else journal["target"]
                state.journal_apply(
                    home,
                    current,
                    target,
                    journal["revision"],
                    journal["baseline"],
                    residual_updates=journal.get("residual_updates", []),
                    completions=journal.get("completions", []),
                )
                return {"status": "recovered", "backup_id": backup_id, "revision": state.revision, "next": "sync"}
        if args.command == "restore":
            return restore_personality(state, home, args.backup_id, dry_run=args.dry_run)
        if args.command == "edit":
            content = None
            if args.source:
                source = safe_path(args.source)
                fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as stream:
                    import stat

                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise CompanionError("source_rejected", "Edit source must be a regular local UTF-8 file.")
                    content = stream.read(MAX_BYTES + 1).decode("utf-8")
            validate_content(args.path, content)
            if not args.dry_run:
                with memory_locks(home):
                    state.recover(home)
                    atomic_content(home, args.path, content)
            return {
                "status": "dry_run" if args.dry_run else "edited",
                "path": args.path,
                "deleted": content is None,
                "next": "sync",
            }
        if args.command == "start" and args.dry_run:
            verify_runtime(Path(config["upstream"]), require_installed=False)
            return {"status": "dry_run", "sync_before_start": not args.offline, "hermes_version": UPSTREAM_VERSION}
        if args.command == "start" and args.offline:
            with memory_locks(home):
                state.recover(home)
            boundary_sync(config, directory, offline=True)
            api = owner_api(config)
            return finish_managed_session(
                config,
                directory,
                Synchronizer(state, home, api),
                api,
                offline=True,
                launch=start_runtime,
                skills=boundary_sync,
                on_activity=on_activity,
            )
        api = None if getattr(args, "dry_run", False) else owner_api(config)
        sync = Synchronizer(state, home, api)
        if args.command == "sync":
            result = sync.run(dry_run=args.dry_run)
            if not args.dry_run:
                result["acknowledgement"] = SyncAcknowledgements(state, api).flush()
            return result
        if args.command == "start":
            sync_result = sync.flush_session()
            if on_activity:
                on_activity("sync", {"outcome": outcome(sync_result)})
            skill_result = boundary_sync(config, directory, api=api)
            if on_activity:
                on_activity("skill_change", {"outcome": outcome(skill_result)})
            # Persona and newly imported skills finalize under the same managed
            # session lock, including nonzero child exits and interrupts.
            return finish_managed_session(
                config,
                directory,
                sync,
                api,
                offline=False,
                launch=start_runtime,
                skills=boundary_sync,
                on_activity=on_activity,
            )
        if args.command == "resolve":
            result = sync.resolve(args.update_id, args.choice, dry_run=args.dry_run)
            if not args.dry_run:
                result["acknowledgement"] = SyncAcknowledgements(state, api).flush()
            return result
        if args.command == "conflicts":
            if args.export_dir and args.update_id:
                return sync.export_conflict(args.update_id, args.export_dir)
            if args.export_dir or args.update_id:
                raise CompanionError(
                    "arguments_required", "Conflict export requires both --update-id and --export-dir."
                )
            return sync.conflicts(before=args.before)
        if args.command == "history":
            if args.revision is not None:
                if args.revision < 0 or not args.export_dir:
                    raise CompanionError(
                        "arguments_required", "Revision export requires a non-negative --revision and --export-dir."
                    )
                _, value = api.request("GET", f"/v1/sync/history/{args.revision}")
                revision, files = remote_snapshot(value)
                export_json(args.export_dir / f"revision-{revision}.json", {"revision": revision, "files": files})
                return {"status": "exported", "revision": revision}
            if args.export_dir:
                raise CompanionError("arguments_required", "Specify --revision for a private content export.")
            if args.before is not None and args.before < 0:
                raise CompanionError("invalid_cursor", "History cursor must be a non-negative integer.")
            suffix = "" if args.before is None else "?before=" + str(args.before)
            _, value = api.request("GET", "/v1/sync/history" + suffix)
            return metadata_page(value, "persona_history")
        raise CompanionError("unknown_command", "Unsupported command.")


def execute(args):
    with identity_command(
        args.state_dir, recovery=args.command == "re-enroll", dry_run=getattr(args, "dry_run", False)
    ):
        return _execute_monitored(args)


def _execute_monitored(args):
    kind = command_kind(args)
    if getattr(args, "dry_run", False) or (kind is None and args.command != "start"):
        return _execute(args)
    directory = Path(os.path.abspath(args.state_dir))
    monitoring = CommandMonitoring(directory, bound_config_read, owner_api, offline=getattr(args, "offline", False))
    started = time.monotonic()
    result = None
    try:
        result = _execute(args, on_activity=monitoring.record)
        if kind:
            attrs = {"outcome": outcome(result), "duration_ms": duration_ms(started)}
            if kind == "tool":
                attrs["tool_kind"] = "connector"
            connection_id = getattr(args, "connection_id", None)
            if kind in ("tool", "connection_change") and connection_id:
                try:
                    attrs["connection_id"] = str(uuid.UUID(connection_id))
                except (ValueError, TypeError, AttributeError):
                    pass
            monitoring.record(kind, attrs)
        return result
    except KeyboardInterrupt:
        if kind:
            monitoring.record(kind, {"outcome": "cancelled", "duration_ms": duration_ms(started)})
        raise
    except Exception:
        if kind:
            monitoring.record(kind, {"outcome": "failed", "duration_ms": duration_ms(started)})
        raise
    finally:
        status = monitoring.finish()
        if result is not None:
            result["monitoring"] = status


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        result = execute(args)
        if result.get("runtime_interrupted"):
            exit_code = 130
        elif "runtime_error" in result:
            exit_code = result["runtime_error"]["exit_code"]
        elif result.get("runtime_exit_code", 0) != 0:
            exit_code = 3
        else:
            exit_code = result.get("post_session_exit_code", 0)
        print(json.dumps({"ok": exit_code == 0, **result}, ensure_ascii=False, sort_keys=True))
        return exit_code
    except CompanionError as error:
        print(json.dumps({"ok": False, "error": error.code, "message": error.message}, sort_keys=True))
        return error.exit_code
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "interrupted",
                    "message": "Interrupted; durable queued changes remain available.",
                }
            )
        )
        return 130
    except Exception:
        # Command-line failures never dump an exception containing private material.
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "local_state_error",
                    "message": "Local input or state is invalid. No private file or server response was printed.",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
