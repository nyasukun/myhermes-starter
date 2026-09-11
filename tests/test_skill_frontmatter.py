"""Header line boundaries must match the pinned Hermes YAML parser."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from myhermes.errors import CompanionError
from myhermes.skill_packages import parse_package
from test_skill_packages import package

SEPARATORS = {"bare-cr": "\r", "nel": "\u0085", "ls": "\u2028", "ps": "\u2029"}
IMPLICIT_SCALARS = ("true", "false", "null", "yes", "No", "ON", "off")
PLAIN_DESCRIPTIONS = (
    "日本語の説明",
    "Normal\tdescription",
    "Normal:description",
    "Normal:\tdescription",
    "Normal:",
    "Normal#description",
    "NaN",
    "None",
    "y",
    "n",
)
QUOTED_DESCRIPTIONS = ('"true"', '"true # description"', '"true\\t# description"')


def described(description):
    return "---\nname: demo\ndescription: " + description + "\n---\n# Synthetic heading only\n"


def injected(separator, field="required_environment_variables"):
    return (
        "---\nname: demo\ndescription: Synthetic"
        + separator
        + field
        + ":"
        + separator
        + "  - PRIVATE_FIXTURE_VALUE\n---\nSynthetic body\n"
    )


class SkillFrontmatterAcceptance(unittest.TestCase):
    def test_plain_yaml_space_and_tab_comments_cannot_change_description_types(self):
        for scalar in (*IMPLICIT_SCALARS, "Normal description"):
            for separator in (" ", "\t"):
                with self.subTest(scalar=scalar, separator=repr(separator)), self.assertRaises(CompanionError):
                    parse_package(package(described(scalar + separator + "# synthetic").encode()))

    def test_yaml_line_separators_cannot_reintroduce_prohibited_header_fields(self):
        for label, separator in SEPARATORS.items():
            for field in ("required_environment_variables", "required_credential_files"):
                with self.subTest(separator=label, field=field), self.assertRaises(CompanionError):
                    parse_package(package(injected(separator, field).encode()))

    def test_standard_line_endings_japanese_quoted_unicode_and_body_are_preserved(self):
        for ending in ("\n", "\r\n"):
            for description in (
                *PLAIN_DESCRIPTIONS,
                *QUOTED_DESCRIPTIONS,
                '"\\u0085quoted data"',
                '"\U00011f02 newer Unicode"',
            ):
                document = ending.join(("---", "name: demo", "description: " + description, "---", "Unicode body:"))
                document += "\n" + "body".join(SEPARATORS.values()) + "\n"
                value = package(document.encode())
                with self.subTest(ending=repr(ending), quoted=description.startswith('"')):
                    self.assertEqual(parse_package(value), value)

    @unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected pinned Hermes checkout")
    def test_official_parser_confirms_why_nonstandard_header_breaks_are_rejected(self):
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        documents = [injected(separator) for separator in SEPARATORS.values()]
        script = (
            "import json,sys\nfrom agent.skill_utils import parse_frontmatter\n"
            "for document in json.load(sys.stdin):\n"
            " header,_=parse_frontmatter(document)\n"
            " assert set(header)=={'name','description','required_environment_variables'}\n"
            "print('verified YAML header boundary')\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [str(upstream / ".venv/bin/python"), "-c", script],
                input=json.dumps(documents),
                capture_output=True,
                text=True,
                timeout=30,
                cwd=upstream,
                env={"PATH": os.defpath, "HERMES_HOME": temporary, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        self.assertEqual(result.returncode, 0, "Pinned YAML boundary probe failed; raw output suppressed")
        self.assertEqual(result.stdout.strip(), "verified YAML header boundary")
        for document in documents:
            with self.assertRaises(CompanionError):
                parse_package(package(document.encode()))

    @unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected pinned Hermes checkout")
    def test_official_discovery_confirms_why_plain_yaml_comments_are_rejected(self):
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        rejected = [described(scalar + "\t# synthetic") for scalar in IMPLICIT_SCALARS]
        accepted = [described(value) for value in (*PLAIN_DESCRIPTIONS, *QUOTED_DESCRIPTIONS)]
        script = """
import json,os
from pathlib import Path
from agent.skill_utils import parse_frontmatter
home=Path(os.environ['HERMES_HOME'])
(home/'skills').mkdir()
(home/'config.yaml').write_text('skills:\\n  inline_shell: false\\n')
cases=json.load(__import__('sys').stdin)
for i,case in enumerate(cases):
    document=case['document'].replace('name: demo','name: fixture-'+str(i))
    header,_=parse_frontmatter(document)
    assert set(header)=={'name','description'}
    assert isinstance(header['description'],str)==case['valid']
    directory=home/'skills'/('fixture-'+str(i));directory.mkdir()
    (directory/'SKILL.md').write_text(document)
from tools.skills_tool import skills_list,skill_view
listing=json.loads(skills_list());assert listing['success']
names={skill['name'] for skill in listing['skills']}
for i,case in enumerate(cases):
    name='fixture-'+str(i)
    assert (name in names)==case['valid']
    preview=json.loads(skill_view(name,preprocess=False))
    assert preview['success']
    assert isinstance(preview['description'],str)==case['valid']
print('verified YAML comments and official discovery')
"""
        cases = [{"document": document, "valid": False} for document in rejected]
        cases += [{"document": document, "valid": True} for document in accepted]
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [str(upstream / ".venv/bin/python"), "-c", script],
                input=json.dumps(cases),
                capture_output=True,
                text=True,
                timeout=30,
                cwd=upstream,
                env={"PATH": os.defpath, "HERMES_HOME": temporary, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        self.assertEqual(result.returncode, 0, "Pinned discovery probe failed; raw output suppressed")
        self.assertEqual(result.stdout.strip(), "verified YAML comments and official discovery")
        for document in rejected:
            with self.assertRaises(CompanionError):
                parse_package(package(document.encode()))
        for document in accepted:
            value = package(document.encode())
            self.assertEqual(parse_package(value), value)


if __name__ == "__main__":
    unittest.main()
