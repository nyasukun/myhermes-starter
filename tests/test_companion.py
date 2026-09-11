"""M1 acceptance tests use synthetic content only; no keyring or live accounts."""

import base64
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from myhermes.api import validate_server
from myhermes.auth import assertion, dpop, public_jwk
from myhermes.cli import main
from myhermes.errors import CompanionError, OfflineError
from myhermes.files import atomic_content, export_json, file_lock, memory_locks, read_file, snapshot, validate_content
from myhermes.state import State
from myhermes.sync import Synchronizer


class FakeAPI:
    """Deterministic contract peer. Production auth is tested against workerd separately."""

    def __init__(self):
        self.current = {"revision": 0, "files": {}}
        self.history = {0: copy.deepcopy(self.current)}
        self.receipts = {}
        self.conflict_records = {}
        self.before_error = False
        self.after_error = False
        self.history_error = False
        self.on_get = None
        self.posts = []

    def request(self, method, path, payload=None):
        if self.before_error:
            raise OfflineError()
        if method == "POST":
            self.posts.append(copy.deepcopy(payload))
            update_id = payload["update_id"]
            if update_id in self.receipts:
                return copy.deepcopy(self.receipts[update_id])
            if payload.get("resolves_update_id") and self.conflict_records[payload["resolves_update_id"]].get(
                "resolved_by"
            ):
                return 409, {"error": "conflict_unavailable"}
            if payload.get("resolves_update_id"):
                original = self.conflict_records[payload["resolves_update_id"]]
                if payload["base_revision"] != self.current["revision"] or any(
                    path not in {change["path"] for change in payload["changes"]} for path in original["conflict_paths"]
                ):
                    return 409, {"error": "incomplete_resolution"}
            conflicts = [
                change["path"]
                for change in payload["changes"]
                if self.current["files"].get(change["path"], {}).get("revision", 0) > payload["base_revision"]
            ]
            if conflicts:
                result = (
                    409,
                    {
                        "status": "conflict",
                        "revision": self.current["revision"],
                        "update_id": update_id,
                        "conflict_paths": conflicts,
                    },
                )
                self.conflict_records[update_id] = {
                    **copy.deepcopy(payload),
                    "conflict_paths": conflicts,
                    "revision": self.current["revision"],
                    "current": copy.deepcopy(self.current),
                    "created_at": "2026-09-11T00:00:00.000Z",
                    "resolved_by": None,
                }
            else:
                revision = self.current["revision"] + 1
                for change in payload["changes"]:
                    self.current["files"][change["path"]] = {
                        "content": change["content"],
                        "revision": revision,
                        "installation_id": "synthetic",
                        "updated_at": "2026-09-11T00:00:00Z",
                    }
                self.current["revision"] = revision
                self.history[revision] = copy.deepcopy(self.current)
                if payload.get("resolves_update_id"):
                    conflict = self.conflict_records[payload["resolves_update_id"]]
                    conflict["resolved_by"] = update_id
                    conflict["resolved_revision"] = revision
                result = (200, {"status": "applied", "revision": revision, "update_id": update_id})
            self.receipts[update_id] = copy.deepcopy(result)
            if self.after_error:
                raise OfflineError()
            return result
        if path.startswith("/v1/sync/history/"):
            if self.history_error:
                raise OfflineError()
            return 200, copy.deepcopy(self.history[int(path.rsplit("/", 1)[1])])
        if path == "/v1/sync/conflicts":
            return 200, {"conflicts": copy.deepcopy(list(self.conflict_records.values()))}
        if path.startswith("/v1/sync/conflicts/"):
            return 200, copy.deepcopy(self.conflict_records[path.rsplit("/", 1)[1]])
        if self.on_get:
            self.on_get()
        return 200, copy.deepcopy(self.current)


class SyncAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="myhermes-tests-", dir="/private/tmp" if Path("/private/tmp").exists() else "/tmp"
        )
        self.root = Path(self.temp.name)
        self.api = FakeAPI()
        self.states = []
        self.a = self.device("a")
        self.b = self.device("b")

    def tearDown(self):
        for state in self.states:
            state.close()
        self.temp.cleanup()

    def device(self, name):
        home = self.root / name / "home"
        home.mkdir(parents=True, exist_ok=True)
        state = State(self.root / name / "state")
        self.states.append(state)
        return Synchronizer(state, home, self.api)

    def write(self, device, content, path="SOUL.md"):
        atomic_content(device.home, path, content)

    def read(self, device, path="SOUL.md"):
        return read_file(device.home, path)

    def test_two_independent_environments_same_owner(self):
        self.write(self.a, "A synthetic personality")
        self.write(self.a, "Uses concise language", "memories/USER.md")
        self.a.run()
        self.b.run()
        self.assertEqual(snapshot(self.a.home), snapshot(self.b.home))
        self.assertNotEqual(self.a.home, self.b.home)

    def test_disjoint_stale_updates_merge_without_overwrite(self):
        self.write(self.a, "personality")
        self.a.run()
        self.write(self.b, "memory", "memories/MEMORY.md")
        self.b.run()
        self.assertEqual(self.read(self.b), "personality")
        self.a.run()
        self.assertEqual(self.read(self.a, "memories/MEMORY.md"), "memory")

    def test_same_file_conflict_retains_both_versions(self):
        self.write(self.a, "baseline")
        self.a.run()
        self.b.run()
        self.write(self.a, "remote edit")
        self.a.run()
        self.write(self.b, "local edit")
        with self.assertRaises(CompanionError) as caught:
            self.b.run()
        self.assertEqual(caught.exception.code, "sync_conflict")
        self.assertEqual(self.read(self.b), "local edit")
        self.assertEqual(self.api.current["files"]["SOUL.md"]["content"], "remote edit")
        self.assertEqual(len(self.b.state.items("conflict")), 1)

    def test_offline_outbox_survives_restart_and_lost_response(self):
        self.write(self.a, "private synthetic value")
        self.api.before_error = True
        with self.assertRaises(OfflineError):
            self.a.run()
        queued = self.a.state.items("pending")[0]
        self.api.before_error = False
        self.api.after_error = True
        restarted = self.device("a")
        with self.assertRaises(OfflineError):
            restarted.run()
        self.assertEqual(self.api.current["revision"], 1)
        self.api.after_error = False
        restarted.run()
        self.assertEqual(self.api.current["revision"], 1)
        self.assertEqual(self.api.posts[0], self.api.posts[1])
        self.assertEqual(self.api.posts[0]["update_id"], queued["update_id"])
        self.assertEqual(restarted.state.items("pending"), [])

    def test_new_edit_after_queue_is_not_lost_or_rebased_silently(self):
        self.write(self.a, "queued v1")
        self.api.before_error = True
        with self.assertRaises(OfflineError):
            self.a.run()
        self.write(self.a, "later v2")
        self.api.before_error = False
        result = self.a.run()
        self.assertEqual(self.read(self.a), "later v2")
        self.assertEqual(result["remaining_local_changes"], 1)
        self.assertEqual(self.api.current["files"]["SOUL.md"]["content"], "queued v1")
        self.a.run()
        self.assertEqual(self.api.current["files"]["SOUL.md"]["content"], "later v2")

    def test_post_queue_revert_to_original_baseline_is_preserved(self):
        self.write(self.a, "original")
        self.a.run()
        self.write(self.a, "queued change")
        self.api.after_error = True
        with self.assertRaises(OfflineError):
            self.a.run()
        self.write(self.a, "original")
        self.api.after_error = False
        self.a.run()
        self.assertEqual(self.read(self.a), "original")
        self.assertEqual(self.a.state.baseline["SOUL.md"], "queued change")
        self.a.run()
        self.assertEqual(self.api.current["files"]["SOUL.md"]["content"], "original")

    def test_post_queue_deletion_is_not_treated_as_missing_resolution_expected(self):
        self.write(self.a, "queued")
        self.api.after_error = True
        with self.assertRaises(OfflineError):
            self.a.run()
        self.write(self.a, None)
        self.api.after_error = False
        self.a.run()
        self.assertIsNone(self.read(self.a))
        self.assertEqual(self.a.state.baseline["SOUL.md"], "queued")
        self.a.run()
        self.assertIsNone(self.api.current["files"]["SOUL.md"]["content"])

    def test_ack_then_history_network_failure_retries_original_id(self):
        self.write(self.a, "v1")
        self.api.history_error = True
        with self.assertRaises(OfflineError):
            self.a.run()
        self.assertEqual(self.a.state.revision, 0)
        self.write(self.a, "v2")
        self.api.history_error = False
        self.a.run()
        self.assertEqual(self.read(self.a), "v2")
        self.assertEqual(self.api.current["revision"], 1)
        self.assertEqual(self.api.posts[0], self.api.posts[1])

    def test_tombstone_prevents_offline_resurrection(self):
        self.write(self.a, "keep")
        self.a.run()
        self.b.run()
        self.write(self.a, None)
        self.a.run()
        self.write(self.b, "stale edit")
        with self.assertRaises(CompanionError):
            self.b.run()
        self.assertIsNone(self.api.current["files"]["SOUL.md"]["content"])
        self.assertEqual(self.api.current["files"]["SOUL.md"]["revision"], 2)
        self.assertEqual(self.read(self.b), "stale edit")

    def create_conflict(self):
        self.write(self.a, "base")
        self.a.run()
        self.b.run()
        self.write(self.a, "remote")
        self.a.run()
        self.write(self.b, "local")
        with self.assertRaises(CompanionError):
            self.b.run()
        return self.b.state.items("conflict")[0]["update_id"]

    def test_explicit_remote_resolution_keeps_unrelated_local_edit(self):
        conflict = self.create_conflict()
        self.write(self.b, "independent memory", "memories/MEMORY.md")
        result = self.b.resolve(conflict, "remote")
        self.assertEqual(self.read(self.b), "remote")
        self.assertEqual(self.read(self.b, "memories/MEMORY.md"), "independent memory")
        self.assertEqual(result["remaining_local_changes"], 1)
        self.b.run()
        self.assertEqual(self.api.current["files"]["memories/MEMORY.md"]["content"], "independent memory")

    def test_explicit_local_resolution_is_revisioned(self):
        conflict = self.create_conflict()
        self.b.resolve(conflict, "local")
        self.a.run()
        self.assertEqual(self.read(self.a), "local")
        self.assertEqual(self.api.current["revision"], 3)
        self.assertIsNotNone(self.api.conflict_records[conflict]["resolved_by"])

    def test_resolution_offline_then_new_local_edit_preserved(self):
        conflict = self.create_conflict()
        original = self.api.request

        def request(method, path, payload=None):
            if method == "POST":
                raise OfflineError()
            return original(method, path, payload)

        self.api.request = request
        with self.assertRaises(OfflineError):
            self.b.resolve(conflict, "remote")
        self.write(self.b, "new edit after resolution")
        self.api.request = original
        self.b.run()
        self.assertEqual(self.read(self.b), "new edit after resolution")
        self.assertEqual(self.b.state.baseline["SOUL.md"], "remote")

    def test_remote_resolution_later_edit_retains_old_base_across_restart(self):
        for lost_ack in (False, True):
            for later_content in ("edit derived from losing local content", None):
                with self.subTest(lost_ack=lost_ack, later_content=later_content):
                    suffix = f"{lost_ack}-{later_content is None}"
                    self.api = FakeAPI()
                    self.a = self.device("resolution-a-" + suffix)
                    self.b = self.device("resolution-b-" + suffix)
                    conflict = self.create_conflict()
                    old_base = self.b.state.revision
                    original = self.api.request

                    def unavailable(method, path, payload=None):
                        if method == "POST" and not lost_ack:
                            raise OfflineError()
                        return original(method, path, payload)

                    self.api.request = unavailable
                    self.api.after_error = lost_ack
                    with self.assertRaises(OfflineError):
                        self.b.resolve(conflict, "remote")
                    selected = copy.deepcopy(self.b.state.items("pending")[0])
                    self.write(self.b, later_content)
                    self.b.state.close()
                    self.states.remove(self.b.state)
                    self.b = self.device("resolution-b-" + suffix)
                    self.api.request = original
                    self.api.after_error = False
                    self.assertEqual(self.b.state.items("pending"), [selected])
                    self.b.run()
                    self.assertEqual(self.read(self.b), later_content)
                    self.assertEqual(self.b.state.baseline["SOUL.md"], "remote")
                    self.assertEqual(self.api.posts[-1], selected)
                    residual = self.b.state.items("pending")
                    self.assertEqual(len(residual), 1)
                    self.assertEqual(residual[0]["base_revision"], old_base)
                    self.assertEqual(residual[0]["changes"], [{"path": "SOUL.md", "content": later_content}])
                    self.assertNotIn("resolves_update_id", residual[0])
                    with self.assertRaises(CompanionError) as caught:
                        self.b.run()
                    self.assertEqual(caught.exception.code, "sync_conflict")
                    self.assertEqual(self.api.current["files"]["SOUL.md"]["content"], "remote")
                    self.assertEqual(self.read(self.b), later_content)
                    # The owner can explicitly choose the later edit or deletion.
                    self.b.resolve(residual[0]["update_id"], "local")
                    self.assertEqual(self.api.current["files"]["SOUL.md"]["content"], later_content)

    def test_local_resolution_later_edit_remains_a_causal_descendant(self):
        conflict = self.create_conflict()
        self.api.after_error = True
        with self.assertRaises(OfflineError):
            self.b.resolve(conflict, "local")
        self.write(self.b, "edit derived from chosen local content")
        self.api.after_error = False
        self.b.run()
        self.assertEqual(self.b.state.items("pending"), [])
        self.b.run()
        self.assertEqual(self.b.state.items("conflict"), [])
        self.assertEqual(self.api.current["files"]["SOUL.md"]["content"], "edit derived from chosen local content")

    def test_portal_resolution_unblocks_original_environment(self):
        conflict = self.create_conflict()
        import uuid

        self.api.request(
            "POST",
            "/v1/sync",
            {
                "schema_version": "1",
                "update_id": str(uuid.uuid4()),
                "base_revision": self.api.current["revision"],
                "resolves_update_id": conflict,
                "changes": [{"path": "SOUL.md", "content": "portal decision"}],
            },
        )
        self.b.run()
        self.assertEqual(self.read(self.b), "portal decision")
        self.assertEqual(self.b.state.items("conflict"), [])

    def test_portal_resolution_preserves_later_local_edits(self):
        conflict = self.create_conflict()
        self.write(self.b, "later local edit")
        self.write(self.b, "unrelated memory", "memories/MEMORY.md")
        import uuid

        self.api.request(
            "POST",
            "/v1/sync",
            {
                "schema_version": "1",
                "update_id": str(uuid.uuid4()),
                "base_revision": self.api.current["revision"],
                "resolves_update_id": conflict,
                "changes": [{"path": "SOUL.md", "content": "portal decision"}],
            },
        )
        with self.assertRaises(CompanionError) as caught:
            self.b.run()
        self.assertEqual(caught.exception.code, "sync_conflict")
        self.assertEqual(self.read(self.b), "later local edit")
        self.assertEqual(self.read(self.b, "memories/MEMORY.md"), "unrelated memory")
        self.assertEqual(self.api.current["files"]["SOUL.md"]["content"], "portal decision")
        self.assertEqual(len(self.b.state.items("conflict")), 1)

    def test_portal_wins_offline_resolution_preserves_choice_without_deadlock(self):
        import uuid

        for choice in ("local", "remote"):
            with self.subTest(choice=choice):
                # Use fresh synthetic devices/server for each choice.
                self.api = FakeAPI()
                self.a = self.device("a-" + choice)
                self.b = self.device("b-" + choice)
                conflict = self.create_conflict()
                original = self.api.request

                def fail_post(method, path, payload=None):
                    if method == "POST":
                        raise OfflineError()
                    return original(method, path, payload)

                self.api.request = fail_post
                with self.assertRaises(OfflineError):
                    self.b.resolve(conflict, choice)
                self.api.request = original
                losing = self.b.state.items("pending")[0]
                self.api.request(
                    "POST",
                    "/v1/sync",
                    {
                        "schema_version": "1",
                        "update_id": str(uuid.uuid4()),
                        "base_revision": self.api.current["revision"],
                        "resolves_update_id": conflict,
                        "changes": [{"path": "SOUL.md", "content": "portal wins"}],
                    },
                )
                with self.assertRaises(CompanionError) as caught:
                    self.b.run()
                self.assertEqual(caught.exception.code, "sync_conflict")
                self.assertEqual(self.b.state.items("pending"), [])
                self.assertEqual(len(self.b.state.items("conflict")), 1)
                self.assertEqual(self.api.current["files"]["SOUL.md"]["content"], "portal wins")
                self.assertEqual(self.read(self.b), "local" if choice == "local" else "remote")
                self.assertNotEqual(self.b.state.items("conflict")[0]["update_id"], losing["update_id"])
                fresh_conflict = self.b.state.items("conflict")[0]["update_id"]
                self.b.resolve(fresh_conflict, "remote")
                self.assertEqual(self.read(self.b), "portal wins")

    def test_lost_resolution_ack_retries_original_id(self):
        conflict = self.create_conflict()
        self.api.after_error = True
        with self.assertRaises(OfflineError):
            self.b.resolve(conflict, "remote")
        queued = self.b.state.items("pending")[0]
        committed_revision = self.api.current["revision"]
        self.api.after_error = False
        self.b.run()
        self.assertEqual(self.api.current["revision"], committed_revision)
        self.assertEqual(self.api.posts[-1]["update_id"], queued["update_id"])
        self.assertEqual(self.b.state.items("pending"), [])
        self.assertEqual(self.b.state.items("conflict"), [])

    def test_offline_resolution_stale_base_retains_choice_and_can_resolve_again(self):
        for choice in ("local", "remote"):
            with self.subTest(choice=choice):
                self.api = FakeAPI()
                self.a = self.device("stale-a-" + choice)
                self.b = self.device("stale-b-" + choice)
                conflict = self.create_conflict()
                original = self.api.request

                def fail_resolution_post(method, path, payload=None):
                    if method == "POST" and payload.get("resolves_update_id"):
                        raise OfflineError()
                    return original(method, path, payload)

                self.api.request = fail_resolution_post
                with self.assertRaises(OfflineError):
                    self.b.resolve(conflict, choice)
                rejected = self.b.state.items("pending")[0]
                old_baseline = self.b.state.baseline
                self.write(self.b, "new local edit after queued choice")
                self.write(self.a, "unrelated remote memory", "memories/MEMORY.md")
                self.a.run()
                self.api.request = original
                server_before = copy.deepcopy(self.api.current)
                with self.assertRaises(CompanionError) as caught:
                    self.b.run()
                self.assertEqual(caught.exception.code, "resolution_stale")
                self.assertEqual(self.b.state.items("pending"), [])
                self.assertEqual(self.b.state.items("conflict")[0]["update_id"], conflict)
                self.assertIn(rejected, self.b.state.items("resolved"))
                self.assertNotIn(rejected["update_id"], self.api.receipts)
                self.assertEqual(self.b.state.baseline, old_baseline)
                self.assertEqual(self.read(self.b), "new local edit after queued choice")
                self.assertEqual(self.api.current, server_before)
                with self.assertRaises(CompanionError) as caught:
                    self.b.run()
                self.assertEqual(caught.exception.code, "sync_conflict")
                self.b.resolve(conflict, choice)
                self.assertEqual(self.b.state.items("pending"), [])
                self.assertEqual(self.b.state.items("conflict"), [])
                self.assertEqual(self.api.current["files"]["memories/MEMORY.md"]["content"], "unrelated remote memory")
                expected = "new local edit after queued choice" if choice == "local" else "remote"
                self.assertEqual(self.api.current["files"]["SOUL.md"]["content"], expected)

    def test_lost_resolution_ack_is_replayed_before_stale_base_rejection(self):
        conflict = self.create_conflict()
        self.api.after_error = True
        with self.assertRaises(OfflineError):
            self.b.resolve(conflict, "remote")
        queued = self.b.state.items("pending")[0]
        self.api.after_error = False
        self.write(self.a, "later unrelated remote memory", "memories/MEMORY.md")
        self.a.run()
        latest = self.api.current["revision"]
        self.b.run()
        self.assertEqual(self.api.current["revision"], latest)
        self.assertEqual(self.api.posts[-1]["update_id"], queued["update_id"])
        self.assertEqual(self.b.state.items("pending"), [])
        self.assertEqual(self.b.state.items("conflict"), [])
        self.b.run()
        self.assertEqual(self.read(self.b, "memories/MEMORY.md"), "later unrelated remote memory")

    def test_unsubmitted_local_edit_keeps_causal_base_after_disjoint_ack(self):
        self.write(self.a, "soul A")
        self.write(self.a, "user A", "memories/USER.md")
        self.a.run()
        self.b.run()
        self.write(self.a, "soul B")
        self.api.before_error = True
        with self.assertRaises(OfflineError):
            self.a.run()
        self.write(self.a, "user B", "memories/USER.md")
        self.api.before_error = False
        self.write(self.b, "user C", "memories/USER.md")
        self.b.run()
        self.a.run()  # SOUL update is accepted as disjoint.
        self.assertEqual(self.read(self.a, "memories/USER.md"), "user B")
        pending = self.a.state.items("pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["base_revision"], 1)
        with self.assertRaises(CompanionError) as caught:
            self.a.run()
        self.assertEqual(caught.exception.code, "sync_conflict")
        self.assertEqual(self.api.current["files"]["memories/USER.md"]["content"], "user C")
        self.assertEqual(self.read(self.a, "memories/USER.md"), "user B")

    def test_journal_recovery_atomically_persists_causal_residual(self):
        import uuid

        before = snapshot(self.a.home)
        target = {**before, "SOUL.md": "remote accepted"}
        residual = {
            "schema_version": "1",
            "update_id": str(uuid.uuid4()),
            "base_revision": 0,
            "changes": [{"path": "memories/USER.md", "content": "unseen local"}],
        }
        self.a.state.put(
            "apply_journal",
            {"expected": before, "target": target, "revision": 2, "baseline": target, "residual_updates": [residual]},
        )
        restarted = self.device("a")
        restarted.state.recover(restarted.home)
        restarted.state.recover(restarted.home)
        self.assertEqual(restarted.state.revision, 2)
        self.assertEqual(restarted.state.items("pending"), [residual])

    def test_pull_detects_intervening_local_write(self):
        self.write(self.a, "remote")
        self.a.run()
        self.api.on_get = lambda: self.write(self.b, "concurrent local")
        with self.assertRaises(CompanionError) as caught:
            self.b.run()
        self.assertEqual(caught.exception.code, "concurrent_edit")
        self.assertEqual(self.read(self.b), "concurrent local")
        self.assertEqual(self.b.state.revision, 0)

    def test_crash_mid_apply_recovers_then_commits_baseline(self):
        expected = snapshot(self.a.home)
        target = {**expected, "SOUL.md": "soul", "memories/MEMORY.md": "memory"}
        calls = 0
        original = atomic_content

        def interrupted(home, path, content):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated power loss")
            return original(home, path, content)

        with memory_locks(self.a.home):
            with patch("myhermes.state.atomic_content", side_effect=interrupted), self.assertRaises(OSError):
                self.a.state.journal_apply(self.a.home, expected, target, 7, target)
            self.assertEqual(self.a.state.revision, 0)
            self.a.state.recover(self.a.home)
        self.assertEqual(snapshot(self.a.home), target)
        self.assertEqual(self.a.state.revision, 7)
        self.assertIsNone(self.a.state.get("apply_journal"))

    def test_recovery_preserves_edit_made_after_crash(self):
        old = snapshot(self.a.home)
        target = {**old, "SOUL.md": "target"}
        self.a.state.put("apply_journal", {"expected": old, "target": target, "revision": 1, "baseline": target})
        self.write(self.a, "new local edit")
        with self.assertRaises(CompanionError) as caught:
            self.a.state.recover(self.a.home)
        self.assertEqual(caught.exception.code, "recovery_conflict")
        self.assertEqual(self.read(self.a), "new local edit")

    def test_non_allowlisted_secrets_sessions_and_skills_are_never_uploaded(self):
        (self.a.home / ".env").write_text("TOKEN=synthetic-secret")
        (self.a.home / "state.db").write_bytes(b"session content")
        (self.a.home / "work-document.md").write_text("private document")
        self.write(self.a, "synthetic soul")
        self.a.run()
        self.assertEqual([item["path"] for item in self.api.posts[0]["changes"]], ["SOUL.md"])
        self.assertNotIn("synthetic-secret", json.dumps(self.api.posts))

    def test_remote_unknown_path_rejected_without_apply(self):
        self.api.current = {"revision": 1, "files": {".env": {"content": "secret", "revision": 1}}}
        with self.assertRaises(CompanionError):
            self.a.run()
        self.assertEqual(self.a.state.revision, 0)
        self.assertFalse((self.a.home / ".env").exists())

    def test_dry_run_does_not_enqueue_or_network(self):
        self.write(self.a, "local")
        self.api.before_error = True
        result = self.a.run(dry_run=True)
        self.assertEqual(result["changed_paths"], ["SOUL.md"])
        self.assertEqual(self.a.state.items("pending"), [])

    def test_symlink_hardlink_fifo_and_capacity_rejection(self):
        secret = self.root / "outside"
        secret.write_text("synthetic secret")
        soul = self.a.home / "SOUL.md"
        soul.symlink_to(secret)
        with self.assertRaises(CompanionError):
            self.a.run()
        soul.unlink()
        os.link(secret, soul)
        with self.assertRaises(CompanionError):
            self.a.run()
        soul.unlink()
        os.mkfifo(soul)
        with self.assertRaises(CompanionError):
            self.a.run()
        soul.unlink()
        with self.assertRaises(CompanionError):
            self.write(self.a, "m" * 2201, "memories/MEMORY.md")
        self.assertEqual(self.api.posts, [])

    def test_symlink_parent_rejected(self):
        outside = self.root / "outside-dir"
        outside.mkdir()
        (self.a.home / "memories").symlink_to(outside)
        with self.assertRaises(CompanionError):
            self.a.run()

    def test_shared_session_lock_prevents_managed_writer(self):
        lock = self.a.home / ".myhermes-session.lock"
        with file_lock(lock), self.assertRaises(CompanionError) as caught:
            with file_lock(lock):
                pass
        self.assertEqual(caught.exception.code, "home_busy")

    def test_conflict_export_is_explicit_private_files(self):
        conflict = self.create_conflict()
        summary = self.b.conflicts()
        self.assertNotIn('"local"', json.dumps(summary))
        out = self.root / "export"
        self.b.export_conflict(conflict, out)
        self.assertEqual(json.loads((out / "local.json").read_text())["SOUL.md"], "local")
        self.assertEqual((out / "local.json").stat().st_mode & 0o777, 0o600)

    def test_conflict_export_preserves_existing_shared_directory_permissions(self):
        conflict = self.create_conflict()
        out = self.root / "shared-export"
        out.mkdir()
        out.chmod(0o755)
        self.b.export_conflict(conflict, out)
        self.assertEqual(out.stat().st_mode & 0o777, 0o755)
        self.assertEqual(len(list(out.iterdir())), 4)
        for file in out.iterdir():
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_export_creates_private_missing_directories_and_private_files(self):
        shared = self.root / "shared"
        shared.mkdir()
        shared.chmod(0o755)
        target = shared / "new" / "nested" / "export.json"
        export_json(target, {"synthetic": "content"})
        self.assertEqual(shared.stat().st_mode & 0o777, 0o755)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(target.parent.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        target.chmod(0o644)
        export_json(target, {"synthetic": "replacement"})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(target.read_text()), {"synthetic": "replacement"})

    def test_export_rejects_symlinks_hardlinks_and_special_files(self):
        outside = self.root / "untouched.json"
        outside.write_text("preserve synthetic original")
        out = self.root / "export-safe"
        out.mkdir()
        target = out / "export.json"
        target.symlink_to(outside)
        with self.assertRaises(CompanionError):
            export_json(target, {"synthetic": "content"})
        target.unlink()
        os.link(outside, target)
        with self.assertRaises(CompanionError):
            export_json(target, {"synthetic": "content"})
        target.unlink()
        os.mkfifo(target)
        with self.assertRaises(CompanionError):
            export_json(target, {"synthetic": "content"})
        target.unlink()
        alias = self.root / "alias"
        alias.symlink_to(out, target_is_directory=True)
        with self.assertRaises(CompanionError):
            export_json(alias / "new.json", {"synthetic": "content"})
        self.assertFalse((out / "new.json").exists())
        self.assertEqual(outside.read_text(), "preserve synthetic original")


class AuthenticationAndCLI(unittest.TestCase):
    def decode(self, token):
        return [base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)) for part in token.split(".")]

    def test_cli_history_export_preserves_existing_directory_mode(self):
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as temporary:
            root = Path(temporary)
            output = root / "shared"
            output.mkdir()
            output.chmod(0o755)
            args = ["--state-dir", str(root / "state")]
            with patch("myhermes.cli.operating_system", return_value="macos"), patch("sys.stdout", io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            *args,
                            "setup",
                            "--server",
                            "https://example.test",
                            "--hermes-home",
                            str(root / "home"),
                            "--upstream",
                            str(root / "runtime"),
                        ]
                    ),
                    0,
                )
                with patch("myhermes.cli.owner_api", return_value=FakeAPI()):
                    self.assertEqual(main([*args, "history", "--revision", "0", "--export-dir", str(output)]), 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o755)
            self.assertEqual((output / "revision-0.json").stat().st_mode & 0o777, 0o600)

    def test_standard_es256_assertion_and_bound_dpop_proof(self):
        key = ec.generate_private_key(ec.SECP256R1())
        token = assertion(key, "installation", "https://example.test/v1/auth/token")
        header, claims, signature = self.decode(token)
        self.assertEqual(json.loads(header), {"alg": "ES256", "typ": "JWT"})
        self.assertEqual(json.loads(claims)["sub"], "installation")
        key.public_key().verify(
            encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")),
            ".".join(token.split(".")[:2]).encode(),
            ec.ECDSA(hashes.SHA256()),
        )
        proof = dpop(key, "get", "https://example.test/v1/sync?x=1", "synthetic-token")
        h, c, _ = self.decode(proof)
        self.assertEqual(json.loads(h)["jwk"], public_jwk(key))
        self.assertEqual(json.loads(c)["htu"], "https://example.test/v1/sync")
        self.assertNotIn("synthetic-token", json.loads(c).values())
        self.assertNotEqual(
            json.loads(c)["jti"],
            json.loads(self.decode(dpop(key, "GET", "https://example.test/v1/sync", "synthetic-token"))[1])["jti"],
        )

    def test_enrollment_code_never_enters_json_config_or_browser_arguments(self):
        import time
        import uuid
        from types import SimpleNamespace
        from myhermes.cli import enroll

        class Terminal(io.StringIO):
            def close(self):
                pass

        terminal = Terminal()
        key = ec.generate_private_key(ec.SECP256R1())
        code = "TEST-SECRET-CODE"
        enrollment_id, installation_id, person_id = (str(uuid.uuid4()) for _ in range(3))
        with tempfile.TemporaryDirectory(dir="/private/tmp" if Path("/private/tmp").exists() else "/tmp") as temporary:
            config = {
                "server": "https://example.test",
                "label": "Synthetic test",
                "os": "macos",
                "key_id": str(uuid.uuid4()),
            }
            with (
                patch("myhermes.cli.SecureKeyStore") as store,
                patch("myhermes.cli.API") as api,
                patch("myhermes.cli.webbrowser.open") as browser,
                patch("builtins.open", return_value=terminal),
            ):
                store.return_value.create.return_value = key
                api.return_value._request.side_effect = [
                    (
                        201,
                        {
                            "enrollment_id": enrollment_id,
                            "user_code": code,
                            "verification_uri": "https://example.test/?enrollment_id=" + enrollment_id,
                            "expires_at": int(time.time()) + 600,
                        },
                    ),
                    (200, {"installation_id": installation_id, "person_id": person_id}),
                ]
                result = enroll(
                    config, Path(temporary), SimpleNamespace(dry_run=False, no_browser=False, wait_seconds=0)
                )
                self.assertEqual(result["status"], "enrolled")
                self.assertNotIn(code, json.dumps(result))
                self.assertNotIn(code, json.dumps(config))
                self.assertNotIn(code, str(browser.call_args))
                self.assertIn(code, terminal.getvalue())
            self.assertNotIn(code, (Path(temporary) / "config.json").read_text())

    def test_cli_recover_preserves_atomic_residual_and_receipt_transitions(self):
        import uuid

        for choice in ("local", "target"):
            with (
                self.subTest(choice=choice),
                tempfile.TemporaryDirectory(
                    dir="/private/tmp" if Path("/private/tmp").exists() else "/tmp"
                ) as temporary,
            ):
                root = Path(temporary)
                args = ["--state-dir", str(root / "state")]
                with patch("myhermes.cli.operating_system", return_value="macos"), patch("sys.stdout", io.StringIO()):
                    self.assertEqual(
                        main(
                            [
                                *args,
                                "setup",
                                "--server",
                                "https://example.test",
                                "--hermes-home",
                                str(root / "home"),
                                "--upstream",
                                str(root / "runtime"),
                            ]
                        ),
                        0,
                    )
                    state = State(root / "state")
                    original = state.queue([{"path": "SOUL.md", "content": "submitted"}])
                    expected = snapshot(root / "home")
                    target = {**expected, "SOUL.md": "remote accepted"}
                    residual = {
                        "schema_version": "1",
                        "update_id": str(uuid.uuid4()),
                        "base_revision": 0,
                        "changes": [{"path": "memories/USER.md", "content": "local candidate"}],
                    }
                    state.put(
                        "apply_journal",
                        {
                            "expected": expected,
                            "target": target,
                            "revision": 2,
                            "baseline": target,
                            "residual_updates": [residual],
                            "completions": [{"update_id": original["update_id"], "status": "done"}],
                        },
                    )
                    atomic_content(root / "home", "SOUL.md", "post-crash owner edit")
                    state.close()
                    self.assertEqual(main([*args, "recover", "--choice", choice]), 0)
                    state = State(root / "state")
                    self.assertEqual(state.revision, 2)
                    self.assertEqual(state.items("pending"), [residual])
                    self.assertEqual(state.items("done"), [original])
                    self.assertEqual(
                        read_file(root / "home", "SOUL.md"),
                        "post-crash owner edit" if choice == "local" else "remote accepted",
                    )
                    self.assertIsNone(state.get("apply_journal"))
                    state.close()

    def test_http_restricted_to_explicit_loopback_and_no_credentials(self):
        for value in (
            "http://example.test",
            "https://token@example.test",
            "https://example.test/path",
            "https://example.test?token=x",
        ):
            with self.assertRaises(CompanionError):
                validate_server(value, True)
        self.assertEqual(validate_server("http://127.0.0.1:8787", True), "http://127.0.0.1:8787")
        with self.assertRaises(CompanionError):
            validate_server("http://127.0.0.1:8787")

    def test_unicode_capacity_exact_content_and_invalid_surrogates(self):
        validate_content("memories/USER.md", "個" * 1375)
        with self.assertRaises(CompanionError):
            validate_content("SOUL.md", "\ud800")

    def test_secure_store_does_not_fallback(self):
        with patch("myhermes.auth.platform.system", return_value="Unsupported"):
            from myhermes.auth import SecureKeyStore

            with self.assertRaises(CompanionError) as caught:
                SecureKeyStore()
            self.assertEqual(caught.exception.code, "unsupported_os")

    def test_runtime_failure_has_nonzero_companion_exit(self):
        with (
            patch("myhermes.cli.execute", return_value={"status": "stopped", "runtime_exit_code": 1}),
            patch("sys.stdout", io.StringIO()) as output,
        ):
            self.assertEqual(main(["start"]), 3)
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_cli_dry_run_and_error_stdout_never_contains_file_content(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp" if Path("/private/tmp").exists() else "/tmp") as temporary:
            root = Path(temporary)
            base = ["--state-dir", str(root / "state")]
            output = io.StringIO()
            with patch("myhermes.cli.operating_system", return_value="macos"), patch("sys.stdout", output):
                self.assertEqual(
                    main(
                        [
                            *base,
                            "setup",
                            "--server",
                            "https://example.test",
                            "--hermes-home",
                            str(root / "home"),
                            "--upstream",
                            str(root / "upstream"),
                        ]
                    ),
                    0,
                )
                (root / "home/SOUL.md").write_text("private synthetic personality")
                self.assertEqual(main([*base, "sync", "--dry-run"]), 0)
                self.assertEqual(main([*base, "inspect"]), 0)
                self.assertEqual(main([*base, "sync"]), 3)
            self.assertNotIn("private synthetic personality", output.getvalue())
            self.assertNotIn("access_token", output.getvalue())
            inspected = [json.loads(line) for line in output.getvalue().splitlines() if '"capabilities"' in line][0]
            self.assertEqual(inspected["capabilities"]["connectors"], ["github@1.0.0"])
            self.assertTrue(inspected["capabilities"]["telemetry"])
            self.assertTrue(inspected["capabilities"]["llm_relay"])
            self.assertEqual(inspected["monitoring"]["manifest_version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
