"""Owner libraries: causal updates, durable projections, withdrawal and recovery."""

import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from test_skill_packages import package, entry
from myhermes.errors import CompanionError, OfflineError
from myhermes.files import file_lock
from myhermes.skill_packages import package_digest
from myhermes.skill_state import SkillState


def sample(text="Initial", version="1.0.0", name="demo"):
    value = package()
    value.update(skill_id=name, version=version)
    value["files"] = [entry("SKILL.md", f'---\nname: {name}\ndescription: "Synthetic fixture"\n---\n{text}\n'.encode())]
    return value


class SkillAPI:
    """Faithful immutable-version and conflict receipts, synthetic identities only."""

    def __init__(self):
        self.revision = 0
        self.current = {}
        self.history = {}
        self.receipts = {}
        self.conflicts = {}
        self.versions = {}
        self.company = {}
        self.offline = False
        self.lose_ack = False
        self.calls = []

    def request(self, method, path, payload=None):
        if self.offline:
            raise OfflineError()
        self.calls.append((method, path, copy.deepcopy(payload)))
        if path == "/v1/skills/personal" and method == "GET":
            return 200, {"revision": self.revision, "skills": [self.metadata(row) for row in self.current.values()]}
        if path == "/v1/skills/company":
            return 200, {
                "skills": [
                    {
                        **{
                            key: self.metadata(row)[key]
                            for key in ("skill_id", "revision", "sha256", "version", "description")
                        },
                        "publication_id": row["publication_id"],
                        "published_at": "2026-09-11T00:00:00Z",
                    }
                    for row in self.company.values()
                ]
            }
        if path.startswith("/v1/skills/company/"):
            return 200, copy.deepcopy(self.company[path.rsplit("/", 1)[1]])
        if "/conflicts/" in path:
            return 200, copy.deepcopy(self.conflicts[path.rsplit("/", 1)[1]])
        if "/history/" in path:
            return 200, copy.deepcopy(self.history[int(path.rsplit("/", 1)[1])])
        if method == "GET" and path.startswith("/v1/skills/personal/"):
            return 200, copy.deepcopy(self.current[path.rsplit("/", 1)[1]])
        if method != "POST" or path != "/v1/skills/personal":
            raise AssertionError((method, path))
        update_id, name = payload["update_id"], payload["skill_id"]
        if update_id in self.receipts:
            old, result = self.receipts[update_id]
            return (
                (200 if result["status"] == "applied" else 409, copy.deepcopy(result))
                if old == payload
                else (409, {"error": "update_id_reused"})
            )
        current = self.current.get(name, {"revision": 0, "skill_id": name, "package": None, "sha256": None})
        content = payload["package"]
        digest = package_digest(content) if content else None
        stale = current["revision"] > payload["base_revision"]
        if (
            not stale
            and content
            and (name, content["version"]) in self.versions
            and self.versions[(name, content["version"])] != digest
        ):
            return 409, {"error": "skill_version_reused"}
        resolving = payload.get("resolves_update_id")
        if resolving:
            original = self.conflicts.get(resolving)
            if original is None or original["resolved_by"]:
                return 409, {"error": "conflict_unavailable"}
            if payload["base_revision"] != self.revision or original["mutation"]["skill_id"] != name:
                return 409, {"error": "incomplete_resolution"}
        result = {
            "status": "conflict" if stale else "applied",
            "revision": self.revision if stale else self.revision + 1,
            "update_id": update_id,
            "skill_id": name,
        }
        if stale:
            self.conflicts[update_id] = {
                "mutation": copy.deepcopy(payload),
                "current": copy.deepcopy(current),
                "revision": self.revision,
                "installation_id": None,
                "created_at": "2026-09-11T00:00:00Z",
                "resolved_by": None,
                "resolved_revision": None,
            }
        else:
            self.revision += 1
            row = {"revision": self.revision, "skill_id": name, "package": copy.deepcopy(content), "sha256": digest}
            self.current[name] = row
            self.history[self.revision] = copy.deepcopy(row)
            if content:
                self.versions[(name, content["version"])] = digest
            if resolving:
                self.conflicts[resolving].update(resolved_by=update_id, resolved_revision=self.revision)
        self.receipts[update_id] = (copy.deepcopy(payload), result)
        if self.lose_ack:
            self.lose_ack = False
            raise OfflineError()
        return (409 if stale else 200), copy.deepcopy(result)

    @staticmethod
    def metadata(row):
        content = row["package"]
        return {
            "skill_id": row["skill_id"],
            "revision": row["revision"],
            "sha256": row["sha256"],
            "version": content["version"] if content else None,
            "description": content["description"] if content else None,
            "deleted": content is None,
            "installation_id": None,
            "created_at": "2026-09-11T00:00:00Z",
            "update_id": str(uuid.uuid4()),
        }

    def publish(self, value):
        self.company[value["skill_id"]] = {
            "revision": len(self.company) + 1,
            "skill_id": value["skill_id"],
            "package": copy.deepcopy(value),
            "sha256": package_digest(value),
            "publication_id": str(uuid.uuid4()),
        }


class SkillActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="myhermes-skills-", dir=os.path.realpath(tempfile.gettempdir())
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = SkillState(self.root / "state")
        self.addCleanup(self.state.close)

    def test_complete_multifile_projection_and_same_name_scopes(self):
        content = sample()
        content["files"] += [
            entry("references/guide.md", b"Synthetic guide"),
            entry("scripts/run.sh", b"#!/bin/sh\nexit 71\n", True),
        ]
        self.state.apply(self.home, "personal", "demo", content)
        self.state.apply(self.home, "company", "demo", content)
        for scope in ("personal", "company"):
            path = self.home / "skills" / ("mh-" + scope + "-demo")
            self.assertEqual((path / "references/guide.md").read_bytes(), b"Synthetic guide")
            self.assertIn("name: mh-" + scope + "-demo", (path / "SKILL.md").read_text())
            self.assertEqual(self.state.inspect_runtime(self.home, scope, "demo"), content)

    def test_crash_after_old_tree_backup_recovers_and_commits_outbox_atomically(self):
        self.state.apply(self.home, "personal", "demo", sample())
        original = os.rename
        calls = 0

        def interrupted(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Synthetic interrupted activation")
            return original(source, target)

        queued = {"payload": {"update_id": str(uuid.uuid4()), "skill_id": "demo"}}
        with patch("myhermes.skill_state.os.rename", side_effect=interrupted), self.assertRaises(OSError):
            self.state.apply(
                self.home,
                "personal",
                "demo",
                sample("Later", "1.0.1"),
                values=[("receipt", "committed")],
                queued=[queued],
            )
        self.assertIsNone(self.state.get("receipt"))
        self.assertEqual(self.state.outbox(), [])
        self.state.recover(self.home)
        self.assertEqual(self.state.get("receipt"), "committed")
        self.assertEqual(len(self.state.outbox("pending")), 1)
        self.assertIn("Later", (self.home / "skills/mh-personal-demo/SKILL.md").read_text())

    def test_managed_direct_edit_and_untracked_collision_preserved(self):
        self.state.apply(self.home, "company", "demo", sample())
        document = self.home / "skills/mh-company-demo/SKILL.md"
        document.write_text(document.read_text() + "Owner modification\n")
        with self.assertRaises(CompanionError):
            self.state.apply(self.home, "company", "demo", None)
        self.assertIn("Owner modification", document.read_text())
        unmanaged = self.home / "skills/mh-personal-demo"
        unmanaged.mkdir()
        (unmanaged / "SKILL.md").write_text("Untracked")
        with self.assertRaises(CompanionError):
            self.state.apply(self.home, "personal", "demo", sample())
        self.assertEqual((unmanaged / "SKILL.md").read_text(), "Untracked")

    def test_post_crash_edit_requires_explicit_recovery_and_is_preserved(self):
        self.state.apply(self.home, "personal", "demo", sample())
        with (
            patch.object(self.state, "transaction", side_effect=OSError("Synthetic commit crash")),
            self.assertRaises(OSError),
        ):
            self.state.apply(self.home, "personal", "demo", sample("Target", "1.0.1"))
        document = self.home / "skills/mh-personal-demo/SKILL.md"
        document.write_text("Malformed post-crash owner edit")
        with self.assertRaises(CompanionError) as caught:
            self.state.recover(self.home)
        self.assertEqual(caught.exception.code, "skill_recovery_conflict")
        result = self.state.recover_preserving_local(self.home)
        self.assertIn("Target", document.read_text())
        preserved = self.home / ".myhermes-skill-backups" / result["preserved_id"] / "mh-personal-demo/SKILL.md"
        self.assertEqual(preserved.read_text(), "Malformed post-crash owner edit")

    def test_restore_retains_malformed_modified_tree_and_reinstates_recorded_package(self):
        self.state.apply(self.home, "company", "demo", sample())
        document = self.home / "skills/mh-company-demo/SKILL.md"
        document.write_text("Owner modified this to invalid frontmatter")
        result = self.state.restore_runtime(self.home, "company", "demo", sample())
        self.assertIn("Initial", document.read_text())
        original = self.home / ".myhermes-skill-backups" / result["operation_id"] / "mh-company-demo/SKILL.md"
        self.assertEqual(original.read_text(), "Owner modified this to invalid frontmatter")

    def test_explicit_restore_to_absence_finishes_when_owner_already_moved_the_runtime_tree(self):
        self.state.apply(self.home, "personal", "demo", sample())
        target = self.home / "skills/mh-personal-demo"
        preserved = self.root / "owner-moved"
        target.rename(preserved)
        self.state.restore_runtime(self.home, "personal", "demo", None)
        self.assertIsNone(self.state.get("journal"))
        self.assertFalse(target.exists())
        self.assertTrue((preserved / "SKILL.md").exists())
        self.assertIsNone(self.state.installed("personal", "demo")["package"])


class SkillSyncAcceptance(unittest.TestCase):
    def setUp(self):
        from myhermes.skill_sync import SkillSynchronizer

        self.synchronizer_type = SkillSynchronizer
        self.temporary = tempfile.TemporaryDirectory(
            prefix="myhermes-skill-sync-", dir=os.path.realpath(tempfile.gettempdir())
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.api = SkillAPI()
        self.a = self.device("a")
        self.b = self.device("b")

    def device(self, label):
        home = self.root / label / "home"
        home.mkdir(parents=True)
        state = SkillState(self.root / label / "state")
        self.addCleanup(state.close)
        return self.synchronizer_type(state, home, self.api)

    def seed(self):
        self.a.import_package(sample())
        self.a.run()
        self.b.run()

    def test_offline_import_restart_and_lost_ack_reuse_exact_update(self):
        self.api.offline = True
        self.a.import_package(sample())
        with self.assertRaises(OfflineError):
            self.a.run()
        pending = self.a.state.outbox("pending")[0]["payload"]
        self.api.offline = False
        self.api.lose_ack = True
        with self.assertRaises(OfflineError):
            self.a.run()
        self.a.run()
        posts = [call[2] for call in self.api.calls if call[0] == "POST"]
        self.assertEqual(posts, [pending, pending])
        self.assertEqual(self.api.revision, 1)

    def test_repeated_or_unknown_delete_does_not_consume_server_revisions(self):
        self.assertFalse(self.a.delete("unknown")["queued"])
        self.a.run()
        self.assertEqual(self.api.revision, 0)
        self.seed()
        self.a.delete("demo")
        self.a.run()
        self.assertEqual(self.api.revision, 2)
        self.assertFalse(self.a.delete("demo")["queued"])
        self.a.run()
        self.assertEqual(self.api.revision, 2)

    def test_explicit_capacity_rejection_retains_candidate_without_blocking_other_skills(self):
        self.seed()
        blocked = sample("Retained new skill", name="over-capacity")
        self.a.import_package(blocked)
        rejected_id = self.a.state.outbox("pending")[0]["update_id"]
        self.a.import_package(sample("Existing ID still updates", "1.0.1"))
        request = self.api.request

        def capacity(method, path, payload=None):
            if method == "POST" and path == "/v1/skills/personal" and payload["skill_id"] == "over-capacity":
                return 429, {"error": "skill_capacity"}
            return request(method, path, payload)

        with patch.object(self.api, "request", side_effect=capacity), self.assertRaises(CompanionError) as caught:
            self.a.run()
        self.assertEqual(caught.exception.code, "skill_capacity")
        self.assertEqual(self.api.current["demo"]["package"]["version"], "1.0.1")
        self.assertEqual(self.a.state.request(rejected_id)["payload"]["package"], blocked)
        self.assertEqual(self.a.state.request(rejected_id)["status"], "rejected")
        self.assertFalse(self.a.state.outbox("pending"))
        # An explicit re-import can retry with a new ID if capacity becomes available.
        self.a.import_package(blocked)
        self.assertNotEqual(self.a.state.outbox("pending")[0]["update_id"], rejected_id)
        with patch.object(self.api, "request", side_effect=capacity), self.assertRaises(CompanionError):
            self.a.run()
        self.assertFalse(self.a.delete("over-capacity")["queued"])
        self.a.run()
        self.assertFalse(self.a.state.outbox("pending", "rejected"))
        self.assertNotIn("over-capacity", self.api.current)

    def test_delete_replays_interrupted_import_before_reading_its_causal_state(self):
        with patch("myhermes.skill_state.os.rename", side_effect=OSError("Synthetic interruption")):
            with self.assertRaises(OSError):
                self.a.import_package(sample())
        self.assertIsNotNone(self.a.state.get("journal"))
        self.a.delete("demo")
        self.a.run()
        self.assertIsNone(self.api.current["demo"]["package"])
        self.assertFalse(self.a.state.outbox("pending", "conflict"))

    def test_resolve_replays_interrupted_newer_local_import_before_selecting_content(self):
        self.seed()
        self.a.import_package(sample("Remote", "1.0.1"))
        self.b.import_package(sample("Local", "1.0.1"))
        self.a.run()
        with self.assertRaises(CompanionError):
            self.b.run()
        conflict = self.b.state.outbox("conflict")[0]["update_id"]
        newer = sample("Newer local content", "1.0.2")
        with patch("myhermes.skill_state.os.rename", side_effect=OSError("Synthetic interruption")):
            with self.assertRaises(OSError):
                self.b.import_package(newer)
        self.b.resolve(conflict, "local", version="1.0.3")
        expected = copy.deepcopy(newer)
        expected["version"] = "1.0.3"
        self.assertEqual(self.api.current["demo"]["package"], expected)
        self.assertFalse(self.b.state.outbox("pending", "conflict"))

    def test_restore_retains_unavailable_working_package_without_activating_it(self):
        self.seed()
        content = sample(version="1.0.1")
        missing_id = str(uuid.uuid4())
        content["requires"]["connections"] = [missing_id]
        self.a.import_package(content)
        self.assertFalse((self.a.home / "skills/mh-personal-demo").exists())
        result = self.a.restore_runtime("personal", "demo")
        self.assertEqual(result["missing_requirements"], ["connection:" + missing_id])
        self.assertFalse((self.a.home / "skills/mh-personal-demo").exists())
        self.assertEqual(self.a.state.get("working:demo")["package"], content)

    def test_newer_import_after_pending_ack_is_preserved_and_synced(self):
        self.a.import_package(sample())
        self.api.lose_ack = True
        with self.assertRaises(OfflineError):
            self.a.run()
        self.a.import_package(sample("Newer import", "1.0.1"))
        self.a.run()
        self.assertEqual(self.api.current["demo"]["package"], sample("Newer import", "1.0.1"))
        self.assertEqual(self.api.revision, 2)

    def test_two_environments_conflict_retains_candidates_and_explicit_new_version(self):
        self.seed()
        self.a.import_package(sample("A", "1.0.1"))
        self.b.import_package(sample("B", "1.0.1"))
        self.a.run()
        with self.assertRaises(CompanionError) as caught:
            self.b.run()
        self.assertEqual(caught.exception.code, "skill_sync_conflict")
        conflict = self.b.state.outbox("conflict")[0]
        self.b.resolve(conflict["update_id"], "local", version="1.0.2")
        self.assertEqual(self.api.current["demo"]["package"], sample("B", "1.0.2"))

    def test_unsent_other_skill_keeps_its_original_causal_base(self):
        self.a.import_package(sample(name="other"))
        self.a.run()
        self.b.run()
        self.a.import_package(sample("Unsent A", "1.0.1", "other"))
        self.b.import_package(sample("Remote B", "1.0.1", "other"))
        self.b.run()
        self.a.import_package(sample(name="demo"))
        with self.assertRaises(CompanionError):
            self.a.run()
        self.assertEqual(self.api.current["other"]["package"], sample("Remote B", "1.0.1", "other"))

    def test_tombstone_and_offline_old_update_conflict(self):
        self.seed()
        self.b.import_package(sample("Offline edit", "1.0.1"))
        self.a.delete("demo")
        self.a.run()
        with self.assertRaises(CompanionError):
            self.b.run()
        self.assertIsNone(self.api.current["demo"]["package"])

    def test_company_withdrawal_removes_only_unmodified_managed_tree(self):
        self.api.publish(sample())
        self.a.run()
        path = self.a.home / "skills/mh-company-demo"
        self.assertTrue(path.exists())
        self.api.company.clear()
        self.a.run()
        self.assertFalse(path.exists())
        self.api.publish(sample())
        self.a.run()
        document = path / "SKILL.md"
        document.write_text(document.read_text() + "Owner edit\n")
        self.api.company.clear()
        with self.assertRaises(CompanionError):
            self.a.run()
        self.assertIn("Owner edit", document.read_text())

    def test_immutable_version_failure_requires_new_import_version(self):
        self.seed()
        self.a.import_package(sample("Changed same version"))
        with self.assertRaises(CompanionError) as caught:
            self.a.run()
        self.assertEqual(caught.exception.code, "skill_version_reused")
        self.assertEqual(self.api.current["demo"]["package"], sample())
        self.a.import_package(sample("Changed same version", "1.0.1"))
        self.a.run()
        self.assertEqual(self.api.current["demo"]["package"], sample("Changed same version", "1.0.1"))

    def conflict(self):
        self.seed()
        self.a.import_package(sample("Remote A", "1.0.1"))
        self.a.run()
        self.b.import_package(sample("Local B", "1.0.1"))
        with self.assertRaises(CompanionError):
            self.b.run()
        return self.b.state.outbox("conflict")[0]["update_id"]

    def portal_resolve(self, update_id, value):
        return self.api.request(
            "POST",
            "/v1/skills/personal",
            {
                "schema_version": "1",
                "update_id": str(uuid.uuid4()),
                "base_revision": self.api.revision,
                "skill_id": "demo",
                "package": value,
                "resolves_update_id": update_id,
            },
        )

    def queue_offline_resolution(self, update_id, choice="remote", version=None):
        original = self.api.request

        def offline_post(method, path, payload=None):
            if method == "POST":
                raise OfflineError()
            return original(method, path, payload)

        with patch.object(self.api, "request", side_effect=offline_post), self.assertRaises(OfflineError):
            self.b.resolve(update_id, choice, version=version)

    def test_portal_resolution_is_adopted_without_replaying_old_candidate(self):
        update_id = self.conflict()
        self.portal_resolve(update_id, sample("Portal D", "1.0.2"))
        self.b.run()
        self.assertEqual(self.b.state.get("working:demo")["package"], sample("Portal D", "1.0.2"))
        self.assertEqual(self.api.revision, 3)

    def test_portal_resolution_retains_newer_local_import_as_causal_conflict(self):
        update_id = self.conflict()
        self.b.import_package(sample("Newer C", "1.0.3"))
        self.portal_resolve(update_id, sample("Portal D", "1.0.2"))
        with self.assertRaises(CompanionError) as caught:
            self.b.run()
        self.assertEqual(caught.exception.code, "skill_sync_conflict")
        self.assertEqual(self.api.current["demo"]["package"], sample("Portal D", "1.0.2"))
        self.assertEqual(self.b.state.get("working:demo")["package"], sample("Newer C", "1.0.3"))
        self.assertEqual(self.b.state.outbox("pending"), [])

    def test_portal_wins_pending_resolution_preserves_selection_and_can_resolve_again(self):
        update_id = self.conflict()
        self.queue_offline_resolution(update_id, "local", "1.0.2")
        self.portal_resolve(update_id, sample("Portal D", "1.0.2"))
        with self.assertRaises(CompanionError):
            self.b.run()
        self.assertEqual(self.api.current["demo"]["package"], sample("Portal D", "1.0.2"))
        self.assertEqual(self.b.state.get("working:demo")["package"], sample("Local B", "1.0.2"))
        conflict = self.b.state.outbox("conflict")[0]["update_id"]
        self.b.resolve(conflict, "local", version="1.0.3")
        self.assertEqual(self.api.current["demo"]["package"], sample("Local B", "1.0.3"))

    def test_unrelated_remote_commit_invalidates_offline_resolution_without_stranding_it(self):
        update_id = self.conflict()
        self.queue_offline_resolution(update_id)
        self.a.import_package(sample(name="other"))
        self.a.run()
        with self.assertRaises(CompanionError) as caught:
            self.b.run()
        self.assertEqual(caught.exception.code, "skill_resolution_stale")
        self.assertEqual(self.b.state.outbox("pending"), [])
        self.b.resolve(update_id, "remote")
        self.assertEqual(self.b.state.get("working:demo")["package"], sample("Remote A", "1.0.1"))

    def test_resolution_version_collision_can_be_rechosen(self):
        update_id = self.conflict()
        with self.assertRaises(CompanionError) as caught:
            self.b.resolve(update_id, "local", version="1.0.1")
        self.assertEqual(caught.exception.code, "skill_version_reused")
        self.b.resolve(update_id, "local", version="1.0.2")
        self.assertEqual(self.b.state.outbox("rejected"), [])

    def test_new_import_during_lost_resolution_ack_does_not_overwrite_selected_remote(self):
        update_id = self.conflict()
        self.api.lose_ack = True
        with self.assertRaises(OfflineError):
            self.b.resolve(update_id, "remote")
        self.b.import_package(sample("Later C", "1.0.3"))
        with self.assertRaises(CompanionError) as caught:
            self.b.run()
        self.assertEqual(caught.exception.code, "skill_sync_conflict")
        self.assertEqual(self.api.current["demo"]["package"], sample("Remote A", "1.0.1"))
        self.assertEqual(self.b.state.get("working:demo")["package"], sample("Later C", "1.0.3"))

    def test_missing_requirements_keep_package_without_runtime_activation(self):
        value = sample()
        value["requires"]["connections"] = [str(uuid.uuid4())]
        imported = self.a.import_package(value)
        self.assertEqual(len(imported["missing_requirements"]), 1)
        with self.assertRaises(CompanionError) as caught:
            self.a.run()
        self.assertEqual(caught.exception.code, "skill_requirements_missing")
        self.assertFalse((self.a.home / "skills/mh-personal-demo").exists())
        self.assertEqual(self.api.current["demo"]["package"], value)
        self.a.requirement_check = lambda package: []
        self.a.run()
        self.assertTrue((self.a.home / "skills/mh-personal-demo").is_dir())

    def test_deferred_import_does_not_modify_live_runtime_until_boundary(self):
        self.seed()
        self.a.defer_activation = True
        self.a.import_package(sample("Written during conversation", "1.0.1"))
        document = self.a.home / "skills/mh-personal-demo/SKILL.md"
        self.assertIn("Initial", document.read_text())
        self.assertEqual(self.a.status()["activation_pending"], 1)
        self.a.defer_activation = False
        self.a.run()
        self.assertIn("Written during conversation", document.read_text())
        self.assertEqual(self.a.status()["activation_pending"], 0)

    def test_delete_recovers_a_rejected_same_version_import(self):
        self.seed()
        self.a.import_package(sample("Rejected bytes"))
        with self.assertRaises(CompanionError):
            self.a.run()
        self.a.delete("demo")
        self.a.run()
        self.assertIsNone(self.api.current["demo"]["package"])
        self.assertEqual(self.a.state.outbox("rejected"), [])

    def test_deferred_projected_import_detects_another_later_direct_edit(self):
        from myhermes.skill_packages import pack_directory

        self.seed()
        directory = self.a.home / "skills/mh-personal-demo"
        document = directory / "SKILL.md"
        document.write_text(document.read_text() + "Imported edit\n")
        imported = pack_directory(
            directory,
            "demo",
            "1.0.1",
            "Synthetic fixture",
            {"connectors": [], "connections": []},
            projected_scope="personal",
        )
        self.a.defer_activation = True
        self.a.import_package(imported, accept_drift=True)
        document.write_text(document.read_text() + "Unimported later edit\n")
        self.a.defer_activation = False
        with self.assertRaises(CompanionError):
            self.a.run()
        self.assertIn("Unimported later edit", document.read_text())
        self.assertEqual(self.api.current["demo"]["package"], sample())

    def test_remote_hash_tampering_does_not_activate_or_advance_local_baseline(self):
        self.a.import_package(sample())
        self.a.run()
        original = self.api.request

        def tampered(method, path, payload=None):
            status, body = original(method, path, payload)
            if path == "/v1/skills/personal/demo":
                body["package"]["files"][0]["content_base64"] = "bm90IHRoZSBleHBlY3RlZCBieXRlcw=="
            return status, body

        with patch.object(self.api, "request", side_effect=tampered), self.assertRaises(CompanionError):
            self.b.run()
        self.assertIsNone(self.b.state.get("baseline:demo"))
        self.assertFalse((self.b.home / "skills/mh-personal-demo").exists())

    def test_dry_run_never_sends_or_activates(self):
        self.a.defer_activation = True
        self.a.import_package(sample())
        before = len(self.api.calls)
        self.a.defer_activation = False
        result = self.a.run(dry_run=True)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(len(self.api.calls), before)
        self.assertFalse((self.a.home / "skills/mh-personal-demo").exists())


class SkillCLIIntegration(unittest.TestCase):
    def test_rejected_candidates_have_metadata_listing_and_explicit_private_export(self):
        from types import SimpleNamespace
        from myhermes.skill_cli import execute_skill_command

        with tempfile.TemporaryDirectory(
            prefix="myhermes-skill-rejected-", dir=os.path.realpath(tempfile.gettempdir())
        ) as temporary:
            root = Path(temporary)
            home, directory = root / "home", root / "state"
            home.mkdir()
            state = SkillState(directory)
            update_id = str(uuid.uuid4())
            payload = {
                "schema_version": "1",
                "update_id": update_id,
                "base_revision": 0,
                "skill_id": "demo",
                "package": sample("Owner retained candidate"),
            }
            state.transaction(queued=[{"payload": payload}])
            state.transaction(
                transitions=[{"update_id": update_id, "status": "rejected", "response": {"error": "skill_capacity"}}]
            )
            state.close()
            args = SimpleNamespace(skill_command="rejected", update_id=None, export_dir=None)
            result = execute_skill_command(args, {"hermes_home": str(home)}, directory, owner_api=None)
            self.assertNotIn("package", json.dumps(result))
            self.assertEqual(result["rejected"][0]["reason"], "skill_capacity")
            args.update_id, args.export_dir = update_id, root / "private-export"
            execute_skill_command(args, {"hermes_home": str(home)}, directory, owner_api=None)
            path = args.export_dir / ("skill-rejected-" + update_id + ".json")
            self.assertEqual(json.loads(path.read_text())["payload"], payload)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_offline_boundary_deactivates_known_missing_dependency_and_preserves_modified_tree(self):
        from myhermes.skill_cli import boundary_sync
        from myhermes.skill_sync import SkillSynchronizer

        for modified in (False, True):
            with (
                self.subTest(modified=modified),
                tempfile.TemporaryDirectory(
                    prefix="myhermes-skill-dependency-", dir=os.path.realpath(tempfile.gettempdir())
                ) as temporary,
            ):
                root = Path(temporary)
                home, directory = root / "home", root / "state"
                home.mkdir()
                state = SkillState(directory)
                content = sample()
                content["requires"]["connections"] = [str(uuid.uuid4())]
                # This fixture models a previously satisfied binding, now absent
                # from the current environment's local connection store.
                SkillSynchronizer(state, home, requirement_check=lambda package: []).import_package(content)
                target = home / "skills/mh-personal-demo/SKILL.md"
                if modified:
                    target.write_text(target.read_text() + "Owner modification\n")
                before = target.read_bytes()
                state.close()
                with self.assertRaises(CompanionError) as caught:
                    boundary_sync({"hermes_home": str(home)}, directory, offline=True)
                if modified:
                    self.assertEqual(caught.exception.code, "skill_runtime_modified")
                    self.assertEqual(target.read_bytes(), before)
                else:
                    self.assertEqual(caught.exception.code, "skill_requirements_missing")
                    self.assertFalse(target.exists())
                    state = SkillState(directory)
                    self.assertEqual(state.get("working:demo")["package"], content)
                    self.assertTrue(state.get("missing:personal:demo"))
                    state.close()

    def test_same_conversation_import_queues_while_home_lock_is_held(self):
        from myhermes.cli import main
        from myhermes.skill_cli import boundary_sync

        with tempfile.TemporaryDirectory(
            prefix="myhermes-skill-cli-", dir=os.path.realpath(tempfile.gettempdir())
        ) as temporary:
            root = Path(temporary)
            state_dir, home, source = root / "state", root / "home", root / "authored"
            source.mkdir()
            (source / "SKILL.md").write_text(
                '---\nname: demo\ndescription: "Synthetic fixture"\n---\nPrivate authored body\n'
            )
            base = ["--state-dir", str(state_dir)]
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            *base,
                            "setup",
                            "--server",
                            "https://example.invalid",
                            "--hermes-home",
                            str(home),
                            "--upstream",
                            str(root / "runtime"),
                        ]
                    ),
                    0,
                )
                with file_lock(home / ".myhermes-session.lock"), file_lock(state_dir / "companion.lock"):
                    self.assertEqual(
                        main(
                            [
                                *base,
                                "skills",
                                "import",
                                "--source",
                                str(source),
                                "--skill-id",
                                "demo",
                                "--version",
                                "1.0.0",
                                "--description",
                                "Synthetic fixture",
                            ]
                        ),
                        0,
                    )
                    self.assertFalse((home / "skills/mh-personal-demo").exists())
                config = json.loads((state_dir / "config.json").read_text())
                boundary_sync(config, state_dir, offline=True)
            self.assertTrue((home / "skills/mh-personal-demo/SKILL.md").is_file())
            self.assertNotIn("Private authored body", output.getvalue())
            self.assertIn('"activation": "after_session"', output.getvalue())

    def test_company_derivation_copies_modified_resources_only_to_personal(self):
        from myhermes.cli import main
        from myhermes.skill_sync import SkillSynchronizer

        with tempfile.TemporaryDirectory(
            prefix="myhermes-skill-derive-", dir=os.path.realpath(tempfile.gettempdir())
        ) as temporary:
            root = Path(temporary)
            state_dir, home = root / "state", root / "home"
            base = ["--state-dir", str(state_dir)]
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            *base,
                            "setup",
                            "--server",
                            "https://example.invalid",
                            "--hermes-home",
                            str(home),
                            "--upstream",
                            str(root / "runtime"),
                        ]
                    ),
                    0,
                )
                state = SkillState(state_dir)
                try:
                    api = SkillAPI()
                    company = sample()
                    company["files"].append(entry("references/guide.md", b"Original company guide"))
                    api.publish(company)
                    sync = SkillSynchronizer(state, home, api)
                    sync.run()
                    guide = home / "skills/mh-company-demo/references/guide.md"
                    guide.write_text("Owner modified guide")
                    self.assertEqual(
                        main(
                            [
                                *base,
                                "skills",
                                "derive",
                                "demo",
                                "--from-scope",
                                "company",
                                "--as",
                                "my-demo",
                                "--version",
                                "1.0.0",
                            ]
                        ),
                        0,
                    )
                    derived = state.get("working:my-demo")["package"]
                    self.assertEqual(derived["derived_from"]["sha256"], package_digest(company))
                    self.assertEqual(
                        (home / "skills/mh-personal-my-demo/references/guide.md").read_text(), "Owner modified guide"
                    )
                    self.assertEqual(api.company["demo"]["package"], company)
                    self.assertEqual(api.current, {})
                    self.assertEqual(
                        main(
                            [
                                *base,
                                "skills",
                                "derive",
                                "demo",
                                "--from-scope",
                                "company",
                                "--as",
                                "my-demo",
                                "--version",
                                "1.0.1",
                            ]
                        ),
                        6,
                    )
                    self.assertEqual(state.get("working:my-demo")["package"], derived)
                    self.assertEqual(main([*base, "skills", "restore", "demo", "--scope", "company"]), 0)
                    sync.run()
                    self.assertIn("my-demo", api.current)
                    self.assertEqual(api.company["demo"]["package"], company)
                finally:
                    state.close()
            self.assertNotIn("Owner modified guide", output.getvalue())


class SkillTransportBounds(unittest.TestCase):
    def test_skill_package_and_double_candidate_bounds_are_path_scoped(self):
        from myhermes.api import API

        class Response(io.BytesIO):
            status = 200

        class Opener:
            def __init__(self, size):
                self.raw = json.dumps({"synthetic": "x" * size}).encode()

            def open(self, request, timeout):
                return Response(self.raw)

        for path, size, allowed in (
            ("/v1/skills/personal/demo", 2_800_000, True),
            ("/v1/skills/personal/demo", 3_000_000, False),
            ("/v1/skills/personal/conflicts/" + str(uuid.uuid4()), 5_800_000, True),
            ("/v1/skills/personal/conflicts/" + str(uuid.uuid4()), 6_000_000, False),
            ("/v1/sync", 2_100_000, False),
        ):
            with self.subTest(path=path, size=size):
                api = API("https://example.invalid", opener=Opener(size))
                if allowed:
                    self.assertEqual(api._request("GET", path)[0], 200)
                else:
                    with self.assertRaises(CompanionError):
                        api._request("GET", path)


if __name__ == "__main__":
    unittest.main()
