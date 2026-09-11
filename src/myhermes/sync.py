"""Optimistic per-user revision sync. Immutable outbox payloads survive lost responses."""

from pathlib import Path
import uuid

from .errors import CompanionError
from .files import LIMITS, export_json, memory_locks, snapshot, validate_content
from .output_metadata import metadata_page
from .sync_ack import SyncAcknowledgements


def remote_snapshot(value):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("revision"), int)
        or isinstance(value["revision"], bool)
        or value["revision"] < 0
        or not isinstance(value.get("files"), dict)
    ):
        raise CompanionError("schema_rejected", "Invalid remote snapshot.")
    files = {path: None for path in LIMITS}
    for path, entry in value["files"].items():
        if not isinstance(entry, dict) or "content" not in entry:
            raise CompanionError("schema_rejected", "Invalid remote file record.")
        validate_content(path, entry["content"])
        if (
            not isinstance(entry.get("revision"), int)
            or isinstance(entry["revision"], bool)
            or not 0 <= entry["revision"] <= value["revision"]
        ):
            raise CompanionError("schema_rejected", "Invalid remote file revision.")
        files[path] = entry["content"]
    return value["revision"], files


def residual_payload(changes, base_revision):
    if not changes:
        return []
    return [{"schema_version": "1", "update_id": str(uuid.uuid4()), "base_revision": base_revision, "changes": changes}]


class Synchronizer:
    def __init__(self, state, home: Path, api):
        self.state, self.home, self.api = state, home, api

    def checkpoint_session(self):
        """Durably retain an exited session before any optional network operation."""
        with memory_locks(self.home):
            self.state.recover(self.home)
            local = snapshot(self.home)
            changes = [
                {"path": path, "content": local[path]} for path in LIMITS if local[path] != self.state.baseline[path]
            ]
            pending, conflicts = self.state.items("pending"), self.state.items("conflict")
            if changes or pending or conflicts:
                self.state.capture_session(local, changes=changes if not pending and not conflicts else None)
            else:
                self.state.put("session_checkpoint", None)
            return {
                "status": "queued" if changes or pending or conflicts else "current",
                "revision": self.state.revision,
                "pending": len(self.state.items("pending")),
                "conflicts": len(conflicts),
                "checkpoint_pending": self.state.get("session_checkpoint") is not None,
            }

    def flush_session(self, *, offline=False):
        captured = self.checkpoint_session()
        if offline:
            return {**captured, "network": "skipped"}
        # Managed session/state locks exclude another cooperating writer. Each
        # ACK uses exact revision history; an unsubmitted concurrent file keeps
        # its old causal base. Drain descendants only after that ACK is durable.
        for _ in range(100):
            result = self.run()
            with memory_locks(self.home):
                dirty = snapshot(self.home) != self.state.baseline
            if not dirty and not self.state.items("pending"):
                return {
                    "status": "synchronized",
                    "revision": self.state.revision,
                    "last_result": result["status"],
                    "checkpoint_pending": self.state.get("session_checkpoint") is not None,
                    "acknowledgement": SyncAcknowledgements(self.state, self.api).flush(),
                }
        raise CompanionError(
            "sync_pass_limit", "Session changes remain retained; run sync after other writers stop.", 6
        )

    def reconcile_portal_resolutions(self, pending):
        resolving = pending[0].get("resolves_update_id") if pending else None
        for conflict in self.state.items("conflict"):
            _, receipt = self.api.request("GET", "/v1/sync/conflicts/" + conflict["update_id"])
            if receipt.get("update_id") != conflict["update_id"]:
                raise CompanionError("schema_rejected", "Invalid conflict receipt.")
            if not receipt.get("resolved_by"):
                continue
            losing_resolution = pending[0] if conflict["update_id"] == resolving else None
            if losing_resolution and receipt["resolved_by"] == losing_resolution["update_id"]:
                continue  # Retry the exact acknowledged local resolution after a lost response.
            revision = receipt.get("resolved_revision")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < self.state.revision:
                raise CompanionError("revision_rejected", "Invalid resolved conflict revision.")
            _, value = self.api.request("GET", f"/v1/sync/history/{revision}")
            actual_revision, remote = remote_snapshot(value)
            if actual_revision != revision:
                raise CompanionError("revision_rejected", "Resolved conflict history does not match its receipt.")
            local, baseline = snapshot(self.home), self.state.baseline
            submitted = {change["path"]: change["content"] for change in conflict["changes"]}
            target = dict(local)
            for path in LIMITS:
                if (path in submitted and local[path] == submitted[path]) or (
                    path not in submitted and local[path] == baseline[path]
                ):
                    target[path] = remote[path]
            residual = [
                {"path": path, "content": local[path]}
                for path in LIMITS
                if local[path] != remote[path]
                and (
                    (path in submitted and local[path] != submitted[path])
                    or (path not in submitted and local[path] != baseline[path] and remote[path] != baseline[path])
                )
            ]
            completions = [
                {
                    "update_id": conflict["update_id"],
                    "status": "resolved",
                    "response": {"resolved_by": receipt["resolved_by"], "revision": revision},
                }
            ]
            causal_base = self.state.revision
            if losing_resolution:
                # A portal decision won before this offline resolution arrived. Keep
                # the owner's selected value (or a newer edit), but remove the now
                # invalid resolves_update_id by creating a fresh old-base candidate.
                selected = {change["path"]: change["content"] for change in losing_resolution["changes"]}
                expected = self.state.get("resolution_expected:" + losing_resolution["update_id"], {})
                target = dict(local)
                for path in LIMITS:
                    if path in selected and path in expected and local[path] == expected[path]:
                        target[path] = selected[path]
                    elif path not in selected and local[path] == baseline[path]:
                        target[path] = remote[path]
                residual = [
                    {"path": path, "content": target[path]}
                    for path in LIMITS
                    if target[path] != remote[path]
                    and (path in selected or (local[path] != baseline[path] and remote[path] != baseline[path]))
                ]
                causal_base = min(causal_base, losing_resolution["base_revision"])
                completions.append(
                    {
                        "update_id": losing_resolution["update_id"],
                        "status": "resolved",
                        "response": {"superseded_by": receipt["resolved_by"]},
                    }
                )
            self.state.journal_apply(
                self.home,
                local,
                target,
                revision,
                remote,
                residual_updates=residual_payload(residual, causal_base),
                completions=completions,
            )

    def run(self, *, dry_run=False):
        # Caller holds the managed-session and state locks. These locks match upstream
        # MemoryStore and prevent another upstream memory writer while apply runs.
        with memory_locks(self.home):
            if dry_run:
                local = snapshot(self.home)
                return {
                    "status": "dry_run",
                    "revision": self.state.revision,
                    "changed_paths": [path for path in LIMITS if local[path] != self.state.baseline[path]],
                    "pending": len(self.state.items("pending")),
                    "conflicts": len(self.state.items("conflict")),
                }
            self.state.recover(self.home)
            pending = self.state.items("pending")
            self.reconcile_portal_resolutions(pending)
            pending = self.state.items("pending")
            resolving = pending[0].get("resolves_update_id") if pending else None
            if any(item["update_id"] != resolving for item in self.state.items("conflict")):
                raise CompanionError(
                    "sync_conflict", "Retained conflicts require explicit resolution. Use conflicts and resolve.", 6
                )
            if not pending:
                local = snapshot(self.home)
                changes = [
                    {"path": path, "content": local[path]}
                    for path in LIMITS
                    if local[path] != self.state.baseline[path]
                ]
                if changes:
                    pending = [self.state.queue(changes)]
            if pending:
                # At most one logical update is sent in a pass. New edits remain dirty
                # for the next pass instead of rewriting an already transmitted update.
                update = pending[0]
                status, response = self.api.request("POST", "/v1/sync", update)
                if (
                    status == 409
                    and response.get("error") == "incomplete_resolution"
                    and update.get("resolves_update_id")
                ):
                    # The server checks an existing receipt before rejecting a stale
                    # resolution base, so this update ID was never committed. Retain
                    # its immutable payload but stop retrying it; the original conflict
                    # and all current local files remain available for a fresh choice.
                    self.state.mark(update["update_id"], "resolved", {"rejected": "incomplete_resolution"})
                    raise CompanionError(
                        "resolution_stale",
                        "The server changed before this resolution arrived. Your files and rejected choice remain retained. Inspect the conflict and resolve again against the current version.",
                        6,
                    )
                if (
                    status == 409
                    and response.get("error") == "conflict_unavailable"
                    and update.get("resolves_update_id")
                ):
                    raise CompanionError(
                        "resolution_changed",
                        "Another environment resolved this conflict during the request. Retry sync to reconcile the receipt and retain your choice.",
                        6,
                    )
                if response.get("update_id") != update["update_id"] or not isinstance(response.get("revision"), int):
                    raise CompanionError("schema_rejected", "Invalid synchronization acknowledgement.")
                if status == 409:
                    if response.get("status") != "conflict":
                        raise CompanionError("schema_rejected", "Invalid synchronization conflict acknowledgement.")
                    transitions = [{"update_id": update["update_id"], "status": "conflict", "response": response}]
                    if update.get("resolves_update_id"):
                        transitions.append(
                            {
                                "update_id": update["resolves_update_id"],
                                "status": "resolved",
                                "response": {"superseded_by": update["update_id"]},
                            }
                        )
                    self.state.mark_many(transitions)
                    raise CompanionError(
                        "sync_conflict", "Both versions are retained; inspect the conflict and choose a resolution.", 6
                    )
                if response.get("status") != "applied":
                    raise CompanionError("schema_rejected", "Invalid synchronization acknowledgement status.")
                # Read the exact committed revision: a later remote edit must not cause
                # post-queue local edits to be accepted against an unseen baseline.
                _, value = self.api.request("GET", f"/v1/sync/history/{response['revision']}")
                revision, remote = remote_snapshot(value)
                if revision != response["revision"] or revision < self.state.revision:
                    raise CompanionError("revision_rejected", "Server history did not match the acknowledged revision.")
                local, baseline = snapshot(self.home), self.state.baseline
                submitted = {change["path"]: change["content"] for change in update["changes"]}
                target = dict(local)
                resolution_expected = self.state.get("resolution_expected:" + update["update_id"], {})
                for path in LIMITS:
                    if path in submitted:
                        can_apply = local[path] == submitted[path] or (
                            path in resolution_expected and local[path] == resolution_expected[path]
                        )
                    else:
                        can_apply = local[path] == baseline[path]
                    if can_apply:
                        target[path] = remote[path]
                # An unsubmitted edit, or an edit made after choosing a different
                # remote resolution, has not observed the new value. Preserve its
                # causal base. A descendant of a chosen local value can advance
                # normally because that submitted value is its actual ancestor.
                residual = [
                    {"path": path, "content": local[path]}
                    for path in LIMITS
                    if local[path] != remote[path]
                    and (
                        (path not in submitted and local[path] != baseline[path] and remote[path] != baseline[path])
                        or (
                            path in submitted
                            and update.get("resolves_update_id")
                            and path in resolution_expected
                            and submitted[path] != resolution_expected[path]
                            and local[path] != resolution_expected[path]
                        )
                    )
                ]
                completions = [{"update_id": update["update_id"], "status": "done", "response": response}]
                if update.get("resolves_update_id"):
                    completions.append(
                        {
                            "update_id": update["resolves_update_id"],
                            "status": "resolved",
                            "response": {"resolved_by": update["update_id"]},
                        }
                    )
                self.state.journal_apply(
                    self.home,
                    local,
                    target,
                    revision,
                    remote,
                    residual_updates=residual_payload(residual, self.state.revision),
                    completions=completions,
                )
                return {
                    "status": "applied",
                    "revision": revision,
                    "update_id": update["update_id"],
                    "remaining_local_changes": sum(target[path] != remote[path] for path in LIMITS),
                }
            # Keep the initial observation: an intervening edit must fail compare/apply.
            _, value = self.api.request("GET", "/v1/sync")
            revision, remote = remote_snapshot(value)
            if revision < self.state.revision:
                raise CompanionError(
                    "revision_rejected", "Server revision moved backwards. Local files were preserved."
                )
            self.state.journal_apply(self.home, local, remote, revision, remote)
            return {"status": "current", "revision": revision}

    def conflicts(self, *, before=None):
        if before is not None and (not isinstance(before, int) or before < 0):
            raise CompanionError("invalid_cursor", "Conflict cursor must be a non-negative integer.")
        suffix = "" if before is None else "?before=" + str(before)
        _, value = self.api.request("GET", "/v1/sync/conflicts" + suffix)
        # No candidate body crosses stdout / Codex context.
        return metadata_page(value, "persona_conflicts")

    def export_conflict(self, update_id: str, directory: Path):
        uuid.UUID(update_id)
        _, item = self.api.request("GET", "/v1/sync/conflicts/" + update_id)
        if item is None:
            raise CompanionError("conflict_missing", "Conflict not found for the authenticated owner.")
        _, remote = remote_snapshot(item["current"])
        local = snapshot(self.home)
        # Explicit local export, excluded from synchronization and monitoring.
        export_json(directory / "baseline.json", self.state.baseline)
        export_json(directory / "local.json", local)
        export_json(directory / "remote.json", remote)
        export_json(directory / "submitted.json", item["changes"])
        return {"status": "exported", "update_id": update_id, "files": 4}

    def resolve(self, update_id: str, choice: str, *, dry_run=False):
        uuid.UUID(update_id)
        with memory_locks(self.home):
            self.state.recover(self.home)
            update = next((item for item in self.state.items("conflict") if item["update_id"] == update_id), None)
            if not update:
                raise CompanionError("conflict_missing", "No unresolved local conflict has this update ID.")
            if self.state.items("pending"):
                raise CompanionError(
                    "pending_update", "Retry the existing pending update before creating another resolution.", 6
                )
            if dry_run:
                return {
                    "status": "dry_run",
                    "choice": choice,
                    "update_id": update_id,
                    "changed_paths": [change["path"] for change in update["changes"]],
                }
            _, value = self.api.request("GET", "/v1/sync")
            revision, remote = remote_snapshot(value)
            local = snapshot(self.home)
            changes = [
                {"path": change["path"], "content": (local if choice == "local" else remote)[change["path"]]}
                for change in update["changes"]
            ]
            self.state.queue(changes, base_revision=revision, resolves=update_id, resolution_expected=local)
            # The original remains available locally and on the server; run() must be
            # permitted to transmit this explicit resolution while blocking other edits.
            # Original conflict stays retained until acknowledgement. The pending
            # resolution carries its expected local version in private SQLite metadata.
        return self.run()
