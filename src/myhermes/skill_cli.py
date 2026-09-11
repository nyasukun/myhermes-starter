"""Owner-visible skill management; package bodies never appear in normal JSON output."""

from contextlib import ExitStack
from pathlib import Path
import uuid

from .connection_store import ConnectionStore
from .errors import CompanionError
from .files import export_json, file_lock, safe_path
from .output_metadata import metadata_page
from .skill_packages import pack_directory, package_digest, skill_id, skill_version
from .skill_state import SkillState
from .skill_sync import SkillSynchronizer, renamed_package, revision, snapshot


def register_skill_commands(sub):
    parent = sub.add_parser("skills", help="Owner skill library and received company distributions")
    commands = parent.add_subparsers(dest="skill_command", required=True)
    commands.add_parser("list", help="Local package metadata, pending updates and missing requirements")
    bootstrap = commands.add_parser("bootstrap", help="Install bundled same-context Hermes authoring skills")
    bootstrap.add_argument("--restore", action="store_true", help="Preserve modified reserved trees before restoring")
    bootstrap.add_argument("--dry-run", action="store_true")
    sync = commands.add_parser("sync")
    sync.add_argument("--dry-run", action="store_true")
    importing = commands.add_parser(
        "import", help="Explicitly import complete authored files into the personal library"
    )
    importing.add_argument("--source", type=Path, required=True)
    importing.add_argument("--skill-id", required=True)
    importing.add_argument("--version", required=True)
    importing.add_argument("--description", required=True)
    importing.add_argument("--connector", action="append", default=[], help="Pinned connector ID@version")
    importing.add_argument(
        "--connection", action="append", default=[], help="Logical connection UUID, never a credential"
    )
    importing.add_argument("--projected", action="store_true", help="Source is a managed personal runtime projection")
    importing.add_argument("--dry-run", action="store_true")
    delete = commands.add_parser("delete")
    delete.add_argument("skill_id")
    delete.add_argument("--dry-run", action="store_true")
    derive = commands.add_parser("derive", help="Explicitly copy an available skill into a distinct personal package")
    derive.add_argument("skill_id")
    derive.add_argument("--from-scope", required=True, choices=("personal", "company"))
    derive.add_argument("--as", dest="new_id", required=True)
    derive.add_argument("--version", required=True)
    derive.add_argument("--dry-run", action="store_true")
    history = commands.add_parser("history")
    history.add_argument("--before", type=int)
    history.add_argument("--revision", type=int)
    history.add_argument("--export-dir", type=Path)
    conflicts = commands.add_parser("conflicts")
    conflicts.add_argument("--before", type=int)
    conflicts.add_argument("--update-id")
    conflicts.add_argument("--export-dir", type=Path)
    rejected = commands.add_parser("rejected", help="Retained local mutations explicitly rejected before admission")
    rejected.add_argument("--update-id")
    rejected.add_argument("--export-dir", type=Path)
    resolve = commands.add_parser("resolve")
    resolve.add_argument("update_id")
    resolve.add_argument("--choice", required=True, choices=("local", "remote"))
    resolve.add_argument("--version", help="Explicit new version for a retained local package")
    resolve.add_argument("--dry-run", action="store_true")
    recover = commands.add_parser(
        "recover", help="Complete interrupted activation, preserving local trees before target choice"
    )
    recover.add_argument("--choice", choices=("target",))
    recover.add_argument("--dry-run", action="store_true")
    restore = commands.add_parser("restore", help="Preserve modified files and restore a recorded managed package")
    restore.add_argument("skill_id")
    restore.add_argument("--scope", required=True, choices=("personal", "company"))
    restore.add_argument("--dry-run", action="store_true")


def local_requirements(directory):
    def check(package):
        missing = [
            "connector:" + row["connector_id"] + "@" + row["version"]
            for row in package["requires"]["connectors"]
            if (row["connector_id"], row["version"]) != ("github", "1.0.0")
        ]
        if package["requires"]["connections"]:
            store = ConnectionStore(directory)
            try:
                missing += [
                    "connection:" + value
                    for value in package["requires"]["connections"]
                    if store.binding(value) is None
                ]
            finally:
                store.close()
        return missing

    return check


def boundary_sync(config, directory, *, api=None, offline=False):
    """Called with the main managed-session lock already held, before/after Hermes."""
    with file_lock(directory / "skills.lock"):
        state = SkillState(directory)
        try:
            from .builtin_skills import activate_builtin_skills

            activate_builtin_skills(state, config["hermes_home"])
            sync = SkillSynchronizer(
                state, Path(config["hermes_home"]), api, requirement_check=local_requirements(directory)
            )
            if offline:
                sync.activate_deferred()
                sync.check_runtime()
                if any(state.records("missing:").values()):
                    raise CompanionError(
                        "skill_requirements_missing",
                        "Cached packages are retained, but missing local requirements prevent activation.",
                        3,
                    )
                return {"status": "offline_cached", "authorization_current": False, **sync.status()}
            return sync.run()
        finally:
            state.close()


def execute_skill_command(args, config, directory, *, owner_api):
    home = Path(config["hermes_home"])
    command = args.skill_command
    dry_run = getattr(args, "dry_run", False)
    network = command in ("sync", "resolve", "history", "conflicts") and not dry_run
    with ExitStack() as stack:
        stack.enter_context(file_lock(directory / "skills.lock"))
        deferred = False
        # Metadata-only listing and history exports are permitted while Hermes
        # runs; every filesystem activation/import/recovery holds the session lock.
        if command not in ("list", "history", "conflicts", "rejected"):
            try:
                stack.enter_context(file_lock(home / ".myhermes-session.lock"))
            except CompanionError as error:
                if error.code != "home_busy" or command not in ("import", "delete", "derive"):
                    raise
                deferred = True
        state = SkillState(directory)
        stack.callback(state.close)
        sync = SkillSynchronizer(
            state,
            home,
            owner_api(config) if network else None,
            requirement_check=local_requirements(directory),
            defer_activation=deferred,
        )
        if command == "list":
            return {"status": "ok", **sync.status()}
        if command == "bootstrap":
            from .builtin_skills import NAMES, activate_builtin_skills

            return (
                {"status": "dry_run", "bundled_skills": list(NAMES)}
                if dry_run
                else {"status": "installed", **activate_builtin_skills(state, home, restore=args.restore)}
            )
        if command == "rejected":
            if args.update_id is not None and args.export_dir is not None:
                row = state.request(str(uuid.UUID(args.update_id)))
                if row["status"] != "rejected":
                    raise CompanionError("skill_rejection_missing", "This update is not a retained rejection.", 3)
                export_json(args.export_dir / ("skill-rejected-" + row["update_id"] + ".json"), row)
                return {"status": "exported", "update_id": row["update_id"]}
            if args.update_id is not None or args.export_dir is not None:
                raise CompanionError(
                    "arguments_required", "Rejected candidate export requires update ID and directory."
                )
            return {
                "rejected": [
                    {
                        "update_id": row["update_id"],
                        "skill_id": row["payload"]["skill_id"],
                        "base_revision": row["payload"]["base_revision"],
                        "reason": row["response"]["error"],
                    }
                    for row in state.outbox("rejected")
                ]
            }
        if command == "import":
            connectors = []
            for value in args.connector:
                parts = value.split("@")
                if len(parts) != 2:
                    raise CompanionError("skill_requirement_invalid", "Use connector ID@version.")
                connectors.append({"connector_id": skill_id(parts[0]), "version": skill_version(parts[1])})
            requires = {"connectors": connectors, "connections": [str(uuid.UUID(value)) for value in args.connection]}
            source = safe_path(args.source.absolute())
            package = pack_directory(
                source,
                args.skill_id,
                args.version,
                args.description,
                requires,
                projected_scope="personal" if args.projected else None,
            )
            if dry_run:
                return {
                    "status": "dry_run",
                    "scope": "personal",
                    "skill_id": package["skill_id"],
                    "version": package["version"],
                    "files": len(package["files"]),
                    "sha256": package_digest(package),
                }
            accept_drift = args.projected and source == state.runtime_path(home, "personal", args.skill_id)
            return sync.import_package(package, accept_drift=accept_drift)
        if command == "derive":
            name, new_id = skill_id(args.skill_id), skill_id(args.new_id)
            if name == new_id and args.from_scope == "personal":
                raise CompanionError("skill_derivation_invalid", "A personal derivative requires a distinct skill ID.")
            if (
                state.get("working:" + new_id) is not None
                or state.get("baseline:" + new_id) is not None
                or state.installed("personal", new_id) is not None
            ):
                raise CompanionError(
                    "skill_derivation_exists", "Choose an unused personal skill ID for the derivative.", 6
                )
            original = state.get("company:" + name) if args.from_scope == "company" else state.get("working:" + name)
            if original is None or original["package"] is None:
                raise CompanionError("skill_unavailable", "Sync this source package before deriving it.", 3)
            content = original["package"]
            source = state.runtime_path(home, args.from_scope, name)
            if source.exists():
                content = pack_directory(
                    source,
                    name,
                    content["version"],
                    content["description"],
                    content["requires"],
                    derived_from=content.get("derived_from"),
                    projected_scope=args.from_scope,
                )
            derived = renamed_package(
                content,
                new_id,
                args.version,
                derived_from={
                    "scope": args.from_scope,
                    "skill_id": name,
                    "version": original["package"]["version"],
                    "sha256": package_digest(original["package"]),
                },
            )
            if dry_run:
                return {"status": "dry_run", "scope": "personal", "skill_id": new_id, "sha256": package_digest(derived)}
            return sync.import_package(derived)
        if command == "delete":
            skill_id(args.skill_id)
            return {"status": "dry_run", "skill_id": args.skill_id} if dry_run else sync.delete(args.skill_id)
        if command == "sync":
            return sync.run(dry_run=dry_run)
        if command == "resolve":
            uuid.UUID(args.update_id)
            if args.version is not None:
                skill_version(args.version)
            return (
                {"status": "dry_run", "update_id": args.update_id, "choice": args.choice}
                if dry_run
                else sync.resolve(args.update_id, args.choice, version=args.version)
            )
        if command == "recover":
            if dry_run:
                return {"status": "dry_run", "recovery_pending": state.get("journal") is not None}
            if args.choice == "target":
                return state.recover_preserving_local(home)
            state.recover(home)
            return {"status": "recovered"}
        if command == "restore":
            name = skill_id(args.skill_id)
            if dry_run:
                return {"status": "dry_run", "scope": args.scope, "skill_id": name}
            return sync.restore_runtime(args.scope, name)
        if command == "history":
            if args.revision is not None:
                revision(args.revision)
                if args.export_dir is None:
                    raise CompanionError(
                        "arguments_required", "History content requires an explicit private export directory."
                    )
                value = snapshot(sync._get("/v1/skills/personal/history/" + str(args.revision)))
                export_json(args.export_dir / ("skill-revision-" + str(args.revision) + ".json"), value)
                return {"status": "exported", "revision": args.revision, "skill_id": value["skill_id"]}
            if args.export_dir:
                raise CompanionError("arguments_required", "Specify a revision for the history export.")
            suffix = "" if args.before is None else "?before=" + str(revision(args.before))
            value = sync._get("/v1/skills/personal/history" + suffix)
            return metadata_page(value, "skill_history")
        if command == "conflicts":
            if args.update_id is not None and args.export_dir is not None:
                value = sync._conflict(str(uuid.UUID(args.update_id)))
                export_json(args.export_dir / ("skill-conflict-" + args.update_id + ".json"), value)
                return {"status": "exported", "update_id": args.update_id, "skill_id": value["mutation"]["skill_id"]}
            if args.update_id is not None or args.export_dir is not None:
                raise CompanionError("arguments_required", "Conflict export requires both update ID and directory.")
            suffix = "" if args.before is None else "?before=" + str(revision(args.before))
            value = sync._get("/v1/skills/personal/conflicts" + suffix)
            return metadata_page(value, "skill_conflicts")
        raise CompanionError("unknown_command", "Unsupported skill command.")
