"""Resolution ACKs may advance only the actual ancestor of a later owner edit."""

import copy
from itertools import product
from pathlib import Path
import tempfile
import unittest

from myhermes.errors import CompanionError, OfflineError
from myhermes.skill_state import SkillState
from myhermes.skill_sync import SkillSynchronizer
from test_skill_sync import SkillAPI, entry, sample


class SkillResolutionCausalityTests(unittest.TestCase):
    def run_case(self, selection, lost_ack, deletion):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            api = SkillAPI()
            a_home, b_home = root / "a-home", root / "b-home"
            a_home.mkdir()
            b_home.mkdir()
            a_state, b_state = SkillState(root / "a-state"), SkillState(root / "b-state")

            # These package-only fixtures explicitly model satisfied dependencies.
            def satisfied(package):
                return []

            a = SkillSynchronizer(a_state, a_home, api, requirement_check=satisfied)
            b = SkillSynchronizer(b_state, b_home, api, requirement_check=satisfied)
            try:
                a.import_package(sample())
                a.run()
                b.run()
                selected_remote = sample("Remote selection", "1.0.1")
                selected_remote["files"].append(entry("assets/source.bin", b"\x00remote\xff"))
                same_files_local = copy.deepcopy(selected_remote)
                if selection == "remote_requirements":
                    selected_remote["requires"]["connectors"] = [{"connector_id": "github", "version": "1.0.0"}]
                elif selection == "remote_description":
                    selected_remote["description"] = "Changed remote package metadata"
                elif selection == "remote_derivation":
                    selected_remote["derived_from"] = {
                        "scope": "company",
                        "skill_id": "source",
                        "version": "1.0.0",
                        "sha256": "a" * 64,
                    }
                elif selection == "remote_version_only":
                    selected_remote["version"] = "1.0.2"
                a.import_package(selected_remote)
                a.run()
                local = (
                    same_files_local
                    if selection
                    in (
                        "identical_remote",
                        "remote_requirements",
                        "remote_description",
                        "remote_derivation",
                        "remote_version_only",
                    )
                    else sample("Local candidate", "1.0.1")
                )
                b.import_package(local)
                with self.assertRaises(CompanionError) as conflict:
                    b.run()
                self.assertEqual(conflict.exception.code, "skill_sync_conflict")
                original = b_state.outbox("conflict")[0]
                if selection == "imported_package":
                    imported = sample("Explicit complete replacement", "1.0.2")
                    imported["files"].append(entry("assets/source.bin", b"\x00owner-package\xfe"))
                    imported["description"] = "Explicit owner package metadata"
                    imported["requires"]["connectors"] = [{"connector_id": "github", "version": "1.0.0"}]
                    imported["derived_from"] = {
                        "scope": "personal",
                        "skill_id": "source",
                        "version": "1.0.0",
                        "sha256": "b" * 64,
                    }
                    b.import_package(imported)
                choice = "remote" if selection.startswith("remote") or selection == "identical_remote" else "local"
                request = api.request
                attempts = []
                block = [True]

                def record_attempt(method, path, payload=None):
                    if method == "POST" and payload.get("resolves_update_id") == original["update_id"]:
                        attempts.append(copy.deepcopy(payload))
                        if block[0]:
                            if not lost_ack:
                                raise OfflineError()
                            api.lose_ack = True
                    return request(method, path, payload)

                api.request = record_attempt
                with self.assertRaises(OfflineError):
                    b.resolve(original["update_id"], choice, version="1.0.3" if choice == "local" else None)
                pending = b_state.outbox("pending")[0]
                immutable = b_state.db.execute(
                    "SELECT payload FROM skill_outbox WHERE update_id=?", (pending["update_id"],)
                ).fetchone()[0]
                block[0] = False
                later = None if deletion else sample("Edit made during unresolved delivery", "1.0.4")
                if later is not None:
                    later["files"].append(entry("assets/followup.bin", b"\x00next-owner-edit\xfd"))
                    b.import_package(later)
                else:
                    b.delete("demo")
                b_state.close()
                b_state = SkillState(root / "b-state")
                b = SkillSynchronizer(b_state, b_home, api, requirement_check=satisfied)
                if selection in ("remote", "remote_requirements", "remote_description", "remote_derivation"):
                    with self.assertRaises(CompanionError) as caught:
                        b.run()
                    self.assertEqual(caught.exception.code, "skill_sync_conflict")
                    self.assertEqual(api.current["demo"]["package"], selected_remote)
                    residual = b_state.outbox("conflict")[0]
                    self.assertEqual(residual["payload"]["base_revision"], original["payload"]["base_revision"])
                    self.assertEqual(residual["payload"]["package"], later)
                    self.assertEqual(b_state.get("working:demo")["package"], later)
                    # The retained followup can still be selected explicitly.
                    b.resolve(residual["update_id"], "local", version="1.0.5" if later else None)
                    expected = copy.deepcopy(later)
                    if expected:
                        expected["version"] = "1.0.5"
                    self.assertEqual(api.current["demo"]["package"], expected)
                else:
                    b.run()
                    self.assertEqual(api.current["demo"]["package"], later)
                    self.assertEqual(b_state.get("working:demo")["package"], later)
                    followup = next(row for row in b_state.outbox("done") if row["payload"]["package"] == later)
                    self.assertEqual(followup["payload"]["base_revision"], 3)
                self.assertEqual(attempts, [pending["payload"], pending["payload"]])
                self.assertEqual(
                    b_state.db.execute(
                        "SELECT payload FROM skill_outbox WHERE update_id=?", (pending["update_id"],)
                    ).fetchone()[0],
                    immutable,
                )
                self.assertEqual(b_state.request(pending["update_id"])["status"], "done")
                self.assertFalse(b_state.outbox("pending", "conflict", "rejected"))
            finally:
                a_state.close()
                b_state.close()

    def test_remote_resolution_followup_keeps_its_old_base_and_needs_explicit_choice(self):
        for lost_ack, deletion in product((False, True), repeat=2):
            with self.subTest(lost_ack=lost_ack, deletion=deletion):
                self.run_case("remote", lost_ack, deletion)

    def test_local_resolution_followup_is_a_valid_descendant_after_offline_or_lost_ack(self):
        for lost_ack, deletion in product((False, True), repeat=2):
            with self.subTest(lost_ack=lost_ack, deletion=deletion):
                self.run_case("local", lost_ack, deletion)

    def test_explicitly_imported_resolution_package_keeps_its_followup_ancestry(self):
        for lost_ack, deletion in product((False, True), repeat=2):
            with self.subTest(lost_ack=lost_ack, deletion=deletion):
                self.run_case("imported_package", lost_ack, deletion)

    def test_identical_remote_package_is_already_the_local_followup_ancestor(self):
        for lost_ack, deletion in product((False, True), repeat=2):
            with self.subTest(lost_ack=lost_ack, deletion=deletion):
                self.run_case("identical_remote", lost_ack, deletion)

    def test_only_the_version_label_is_ignored_in_ancestor_comparison(self):
        for lost_ack, deletion in product((False, True), repeat=2):
            with self.subTest(lost_ack=lost_ack, deletion=deletion):
                self.run_case("remote_version_only", lost_ack, deletion)

    def test_equal_file_bytes_with_changed_requirements_description_or_derivation_still_conflict(self):
        for selection, lost_ack, deletion in product(
            ("remote_requirements", "remote_description", "remote_derivation"), (False, True), (False, True)
        ):
            with self.subTest(selection=selection, lost_ack=lost_ack, deletion=deletion):
                self.run_case(selection, lost_ack, deletion)


if __name__ == "__main__":
    unittest.main()
