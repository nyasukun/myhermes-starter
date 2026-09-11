"""Synthetic acceptance checks for packages, projections and safe directory IO."""

import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from myhermes.errors import CompanionError
from myhermes.skill_packages import (
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    canonical_json,
    pack_directory,
    package_digest,
    parse_package,
    projected_name,
    stage_directory,
)


def entry(path, raw, executable=False):
    return {
        "path": path,
        "content_base64": base64.b64encode(raw).decode(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "executable": executable,
    }


def package(document=None):
    document = document or b'---\nname: demo\ndescription: "Synthetic fixture"\n---\nExample.\n'
    return {
        "schema_version": "1",
        "skill_id": "demo",
        "version": "1.2.3",
        "description": "Synthetic fixture",
        "files": [entry("SKILL.md", document)],
        "requires": {"connectors": [], "connections": []},
    }


class SkillPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="myhermes-package-test-", dir=os.path.realpath(tempfile.gettempdir())
        )
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, value=None):
        value = value or package()
        root = self.root / "source"
        root.mkdir(exist_ok=True)
        for file in value["files"]:
            path = root / file["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(file["content_base64"]))
            path.chmod(0o700 if file["executable"] else 0o600)
        return root

    def pack(self, source, **kwargs):
        return pack_directory(
            source, "demo", "1.2.3", "Synthetic fixture", {"connectors": [], "connections": []}, **kwargs
        )

    def test_parse_is_detached_and_canonical_digest_covers_exact_envelope(self):
        value = package()
        parsed = parse_package(value)
        parsed["files"][0]["sha256"] = "0" * 64
        self.assertNotEqual(parsed, value)
        self.assertEqual(package_digest(value), hashlib.sha256(canonical_json(value).encode()).hexdigest())
        reordered = dict(reversed(list(value.items())))
        self.assertEqual(package_digest(value), package_digest(reordered))

    def test_binary_script_and_reference_roundtrip_in_both_scopes_without_execution(self):
        value = package()
        value["files"] += [
            entry("assets/logo.bin", bytes(range(256))),
            entry("references/guide.md", "架空の参照資料。\n".encode()),
            entry("scripts/run.sh", b"#!/bin/sh\nexit 71\n", True),
        ]
        source = self.source(value)
        self.assertEqual(self.pack(source), value)
        for scope in ("personal", "company"):
            with self.subTest(scope=scope):
                target = self.root / projected_name("demo", scope)
                result = stage_directory(value, target, scope)
                self.assertEqual(result["path"], target)
                self.assertEqual(result["original_sha256"], package_digest(value))
                self.assertNotEqual(result["installed_sha256"], result["original_sha256"])
                self.assertEqual((target / "assets/logo.bin").read_bytes(), bytes(range(256)))
                self.assertEqual((target / "scripts/run.sh").stat().st_mode & 0o777, 0o700)
                self.assertEqual((target / "SKILL.md").stat().st_mode & 0o777, 0o600)
                self.assertIn(f"name: mh-{scope}-demo\n", (target / "SKILL.md").read_text())
                self.assertEqual(self.pack(target, projected_scope=scope), value)
                self.assertEqual(
                    sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()),
                    [f["path"] for f in value["files"]],
                )

    def test_crlf_projection_only_changes_name(self):
        value = package(b'---\r\nname: demo\r\ndescription: "Synthetic fixture"\r\n---\r\nBody\r\n')
        target = self.root / "mh-personal-demo"
        stage_directory(value, target, "personal")
        self.assertEqual(self.pack(target, projected_scope="personal"), value)

    def test_valid_pinned_requirements_and_derivation(self):
        value = package()
        value["requires"] = {
            "connectors": [{"connector_id": "github", "version": "1.0.0"}],
            "connections": ["24a7bf0a-c81e-4e80-84cb-1d50de7b8970"],
        }
        value["derived_from"] = {"scope": "company", "skill_id": "source", "version": "1.0.0", "sha256": "a" * 64}
        self.assertEqual(parse_package(value), value)

    def test_unknown_fields_wrong_scalar_types_and_bad_metadata_rejected(self):
        mutations = [
            lambda p: p.update(secret="fixture"),
            lambda p: p.update(schema_version=1),
            lambda p: p.update(skill_id="a" * 41),
            lambda p: p.update(skill_id="9demo"),
            lambda p: p.update(skill_id="has--double"),
            lambda p: p.update(version="01.2.3"),
            lambda p: p.update(description="\ud800"),
            lambda p: p.update(description="a\0b"),
            lambda p: p.update(files=[]),
            lambda p: p["files"][0].update(executable=1),
            lambda p: p["files"][0].update(mode="700"),
            lambda p: p["requires"].update(token="fixture"),
            lambda p: p["requires"].update(connections=[True]),
            lambda p: p["requires"].update(
                connectors=[{"connector_id": "github", "version": "1.0.0", "token": "fixture"}]
            ),
            lambda p: p.update(derived_from=None),
            lambda p: p.update(
                derived_from={"scope": "admin", "skill_id": "demo", "version": "1.0.0", "sha256": "0" * 64}
            ),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = package()
                mutation(value)
                with self.assertRaises(CompanionError):
                    parse_package(value)

    def test_duplicate_dependencies_and_limits(self):
        connection = "24a7bf0a-c81e-4e80-84cb-1d50de7b8970"
        requirements = [
            {"connectors": [{"connector_id": "github", "version": "1.0.0"}] * 2, "connections": []},
            {"connectors": [], "connections": [connection] * 2},
            {"connectors": [{"connector_id": "a", "version": "1.0.0"}] * 21, "connections": []},
            {"connectors": [], "connections": [connection] * 51},
        ]
        for requires in requirements:
            with self.subTest(requires=requires):
                value = package()
                value["requires"] = requires
                with self.assertRaises(CompanionError):
                    parse_package(value)

    def test_encoding_and_hash_rejections(self):
        for content in ("YQ", "YQ===", "YR==", "YQ==\n", "_w==", "é", 12):
            with self.subTest(content=content):
                value = package()
                value["files"] += [entry("asset", b"a")]
                value["files"][1]["content_base64"] = content
                with self.assertRaises(CompanionError):
                    parse_package(value)
        value = package()
        value["files"][0]["sha256"] = "0" * 64
        with self.assertRaises(CompanionError):
            parse_package(value)

    def test_paths_nested_entrypoints_and_collisions_rejected(self):
        for path in (
            "../secret",
            "/absolute",
            "a//b",
            "a/./b",
            "a/../b",
            "a\\b",
            ".env",
            "a/.env",
            "CON",
            "nul.txt",
            "a/LPT9.txt",
            "a.",
            "a b",
            "é",
            "a" * 241,
            "references/SKILL.md",
            "skill.md",
        ):
            with self.subTest(path=path):
                value = package()
                value["files"].append(entry(path, b"x"))
                value["files"].sort(key=lambda f: f["path"])
                with self.assertRaises(CompanionError):
                    parse_package(value)
        for paths in (("a", "a/b"), ("a.txt", "A.txt"), ("A/x", "a/y")):
            value = package()
            value["files"] += [entry(path, b"x") for path in paths]
            value["files"].sort(key=lambda f: f["path"])
            with self.assertRaises(CompanionError):
                parse_package(value)

    def test_unsorted_and_missing_skill_rejected(self):
        value = package()
        value["files"].insert(0, entry("z.txt", b"x"))
        with self.assertRaises(CompanionError):
            parse_package(value)
        value["files"] = [entry("readme.md", b"x")]
        with self.assertRaises(CompanionError):
            parse_package(value)

    def test_frontmatter_is_not_silently_stripped_or_coerced(self):
        documents = [
            b"no frontmatter",
            b"\xef\xbb\xbf---\nname: demo\ndescription: Fixture\n---\n",
            b"---\nname: another\ndescription: Fixture\n---\n",
            b"---\nname: demo\ndescription: Fixture\nrequired_environment_variables: [SECRET]\n---\n",
            b"---\nname: demo\ndescription: Fixture\n---\n\x00",
            b"---\nname: demo\ndescription: Fixture\n---\n\xff",
        ]
        descriptions = (
            "true",
            "true ",
            "NO",
            "123",
            "[]",
            "null",
            "~",
            "&anchor",
            "text # comment",
            "text: value",
            "'quoted'",
            "a" * 501,
        )
        documents += [("---\nname: demo\ndescription: " + desc + "\n---\n").encode() for desc in descriptions]
        for document in documents:
            with self.subTest(document=document[:100]):
                with self.assertRaises(CompanionError):
                    parse_package(package(document))
        for description in ('"true"', '"123"', "日本語の説明", "_fixture"):
            parse_package(package(("---\nname: demo\ndescription: " + description + "\n---\n").encode()))

    def test_limits_and_projected_maximum_document_roundtrip(self):
        value = package()
        value["files"] += [entry("large", b"a" * (MAX_FILE_BYTES + 1))]
        with self.assertRaises(CompanionError):
            parse_package(value)
        value = package()
        value["files"] += [entry(str(i), b"a" * MAX_FILE_BYTES) for i in range(MAX_TOTAL_BYTES // MAX_FILE_BYTES)]
        value["files"].sort(key=lambda f: f["path"])
        with self.assertRaises(CompanionError):
            parse_package(value)
        value = package()
        value["files"] += [entry("f" + str(i), b"") for i in range(100)]
        value["files"].sort(key=lambda f: f["path"])
        with self.assertRaises(CompanionError):
            parse_package(value)
        original = base64.b64decode(package()["files"][0]["content_base64"])
        value = package(original + b"a" * (MAX_FILE_BYTES - len(original)))
        destination = self.root / "mh-personal-demo"
        stage_directory(value, destination, "personal")
        self.assertEqual(self.pack(destination, projected_scope="personal"), value)

    def test_invalid_package_writes_nothing_and_existing_destination_is_preserved(self):
        target = self.root / "mh-personal-demo"
        value = package()
        value["files"][0]["sha256"] = "0" * 64
        with self.assertRaises(CompanionError):
            stage_directory(value, target, "personal")
        self.assertFalse(target.exists())
        target.mkdir()
        marker = target / "keep"
        marker.write_text("Keep unchanged")
        with self.assertRaises(CompanionError):
            stage_directory(package(), target, "personal")
        self.assertEqual(marker.read_text(), "Keep unchanged")

    def test_projection_identity_and_unknown_scope_rejected(self):
        with self.assertRaises(CompanionError):
            stage_directory(package(), self.root / "wrong-name", "personal")
        with self.assertRaises(CompanionError):
            stage_directory(package(), self.root / "mh-personal-demo", "admin")
        source = self.source()
        with self.assertRaises(CompanionError):
            self.pack(source, projected_scope="personal")
        source.rename(self.root / "mh-personal-demo")
        with self.assertRaises(CompanionError):
            self.pack(self.root / "mh-personal-demo", projected_scope="personal")

    def test_symlink_hardlink_fifo_and_ancestor_refused_without_reading(self):
        source = self.source()
        outside = self.root / "outside"
        outside.write_bytes(b"Fictional content")
        link = source / "linked"
        link.symlink_to(outside)
        with self.assertRaises(CompanionError):
            self.pack(source)
        link.unlink()
        os.link(outside, link)
        with self.assertRaises(CompanionError):
            self.pack(source)
        link.unlink()
        os.mkfifo(link)
        with self.assertRaises(CompanionError):
            self.pack(source)
        link.unlink()
        alias = self.root / "alias"
        alias.symlink_to(source, target_is_directory=True)
        with self.assertRaises(CompanionError):
            self.pack(alias)
        with self.assertRaises(CompanionError):
            stage_directory(package(), alias / "mh-personal-demo", "personal")
        self.assertEqual(outside.read_bytes(), b"Fictional content")

    def test_source_unsafe_empty_directory_and_unknown_frontmatter_rejected(self):
        source = self.source()
        hidden = source / ".hidden"
        hidden.mkdir()
        with self.assertRaises(CompanionError):
            self.pack(source)
        hidden.rmdir()
        (source / "SKILL.md").write_text("---\nname: demo\ndescription: Fixture\nmetadata: unsafe\n---\n")
        with self.assertRaises(CompanionError):
            self.pack(source)

    def test_staging_disk_failure_removes_partial_new_tree(self):
        value = package()
        value["files"].append(entry("a.txt", b"x"))
        target = self.root / "mh-personal-demo"
        original_open = os.open

        def failing_open(path, flags, *args, **kwargs):
            if path == "a.txt" and flags & os.O_WRONLY:
                raise OSError("Synthetic disk failure")
            return original_open(path, flags, *args, **kwargs)

        with patch("myhermes.skill_packages.os.open", side_effect=failing_open):
            with self.assertRaises(CompanionError):
                stage_directory(value, target, "personal")
        self.assertFalse(target.exists())

    def test_installed_hash_changes_with_content_permissions_and_scope(self):
        value = package()
        first = stage_directory(value, self.root / "mh-personal-demo", "personal")
        second = stage_directory(value, self.root / "mh-company-demo", "company")
        self.assertNotEqual(first["installed_sha256"], second["installed_sha256"])
        self.assertEqual(first["original_sha256"], second["original_sha256"])
        document = next(f for f in first["installed_files"] if f["path"] == "SKILL.md")
        self.assertEqual(document["sha256"], hashlib.sha256((first["path"] / "SKILL.md").read_bytes()).hexdigest())
        serialized = deepcopy(first)
        serialized["path"] = str(serialized["path"])
        json.dumps(serialized)


if __name__ == "__main__":
    unittest.main()
