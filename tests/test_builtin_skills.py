"""Bundled authoring skills must work from installed package data, without the repo."""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from myhermes.errors import CompanionError
from myhermes.skill_state import SkillState
from myhermes.builtin_skills import activate_builtin_skills, bundled_skills


class BuiltinSkillsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = SkillState(self.root / "state")
        self.addCleanup(self.state.close)

    def test_bundled_resources_install_as_same_context_reserved_runtime_skills(self):
        result = activate_builtin_skills(self.state, self.home)
        self.assertEqual(set(result["installed"]), {"myhermes-skills", "myhermes-connections"})
        for name, raw in bundled_skills().items():
            published = Path(__file__).parent.parent / "hermes-skills" / name / "SKILL.md"
            self.assertEqual(published.read_bytes(), raw)
            path = self.home / "skills" / name / "SKILL.md"
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(self.state.get("builtin:" + name)["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(activate_builtin_skills(self.state, self.home), result)
        self.assertFalse(self.state.outbox())

    def test_interrupted_explicit_restore_recovers_the_retained_owner_tree(self):
        activate_builtin_skills(self.state, self.home)
        target = self.home / "skills/myhermes-skills/SKILL.md"
        target.write_text("Owner changed instructions")
        with patch("myhermes.builtin_skills.atomic_bytes", side_effect=OSError("Synthetic disk interruption")):
            with self.assertRaises(OSError):
                activate_builtin_skills(self.state, self.home, restore=True)
        self.assertFalse(target.exists())
        activate_builtin_skills(self.state, self.home)
        backups = list((self.home / ".myhermes-builtin-backups").glob("*/myhermes-skills/SKILL.md"))
        self.assertEqual(backups[0].read_text(), "Owner changed instructions")
        self.assertEqual(target.read_bytes(), bundled_skills()["myhermes-skills"])

    def test_modified_builtin_is_preserved_and_requires_explicit_restore(self):
        activate_builtin_skills(self.state, self.home)
        target = self.home / "skills/myhermes-skills/SKILL.md"
        target.write_text("Owner modified builtin instructions")
        with self.assertRaises(CompanionError) as caught:
            activate_builtin_skills(self.state, self.home)
        self.assertEqual(caught.exception.code, "builtin_skill_modified")
        self.assertEqual(target.read_text(), "Owner modified builtin instructions")
        activate_builtin_skills(self.state, self.home, restore=True)
        backups = list((self.home / ".myhermes-builtin-backups").glob("*/myhermes-skills/SKILL.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "Owner modified builtin instructions")
        self.assertEqual(target.read_bytes(), bundled_skills()["myhermes-skills"])

    def test_unmanaged_collision_and_extra_files_are_never_silently_replaced(self):
        target = self.home / "skills/myhermes-connections"
        target.mkdir(parents=True)
        (target / "owner.txt").write_text("Owner content")
        with self.assertRaises(CompanionError):
            activate_builtin_skills(self.state, self.home)
        self.assertEqual((target / "owner.txt").read_text(), "Owner content")
        activate_builtin_skills(self.state, self.home, restore=True)
        backups = list((self.home / ".myhermes-builtin-backups").glob("*/myhermes-connections/owner.txt"))
        self.assertEqual(backups[0].read_text(), "Owner content")

    def test_crash_after_file_replace_recovers_exact_journal_without_overwrite(self):
        original = self.state.transaction
        with patch.object(self.state, "transaction", side_effect=OSError("Synthetic DB interruption")):
            with self.assertRaises(OSError):
                activate_builtin_skills(self.state, self.home)
        self.assertIsNotNone(self.state.get("builtin_journal"))
        with patch.object(self.state, "transaction", side_effect=original):
            activate_builtin_skills(self.state, self.home)
        self.assertIsNone(self.state.get("builtin_journal"))

    def test_symlink_directory_is_rejected_without_reading_its_target(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text("Owner content")
        skills = self.home / "skills"
        skills.mkdir()
        (skills / "myhermes-skills").symlink_to(outside)
        with self.assertRaises(CompanionError):
            activate_builtin_skills(self.state, self.home, restore=True)
        self.assertEqual((outside / "SKILL.md").read_text(), "Owner content")


if __name__ == "__main__":
    unittest.main()
