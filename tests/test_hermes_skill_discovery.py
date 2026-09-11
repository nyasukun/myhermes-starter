"""Opt-in official Hermes discovery and initial inference from installed assets."""

from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import myhermes
from myhermes.builtin_skills import activate_builtin_skills
from myhermes.runtime import relay_runtime_session
from myhermes.skill_packages import pack_directory, stage_directory
from myhermes.skill_state import SkillState
from test_relay_bridge import company


@unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected installed Hermes checkout")
class HermesSkillDiscoveryAcceptance(unittest.TestCase):
    def test_four_skills_share_one_official_runtime_and_initial_model_context(self):
        installed = os.getenv("MYHERMES_TEST_INSTALLED")
        if installed:
            self.assertTrue(Path(myhermes.__file__).is_relative_to(Path(installed)))
        upstream = Path(os.environ["MYHERMES_TEST_UPSTREAM"])
        expected = {"myhermes-skills", "myhermes-connections", "mh-personal-demo", "mh-company-demo"}
        with tempfile.TemporaryDirectory(prefix="myhermes-discovery-") as directory:
            root = Path(directory).resolve()
            home = root / "home"
            home.mkdir(mode=0o700)
            with closing(SkillState(root / "state")) as state:
                activate_builtin_skills(state, home)
            for scope in ("company", "personal"):
                source = root / scope
                source.mkdir(mode=0o700)
                (source / "SKILL.md").write_text(
                    f'---\nname: demo\ndescription: "Synthetic {scope} discovery fixture"\n---\n'
                    f"This fictional {scope} skill has no external action.\n"
                )
                (source / "references").mkdir()
                (source / "references/example.md").write_text(f"Synthetic {scope} reference.\n")
                package = pack_directory(
                    source, "demo", "1.0.0", "Synthetic discovery fixture", {"connectors": [], "connections": []}
                )
                stage_directory(package, home / "skills" / f"mh-{scope}-demo", scope)
            requests = []
            with company(completion_text="SKILLS_READY.", on_request=requests.append) as peer:
                with relay_runtime_session(
                    {"upstream": str(upstream), "hermes_home": str(home)},
                    api=peer["api"],
                    state_directory=root / "state",
                    allow_local_http=True,
                ) as (command, environment):
                    # Directly call the pinned official discovery/view implementations.
                    # No skill shell preprocessing or owner environment is used.
                    probe = (
                        "import json\nfrom tools.skills_tool import skills_list,skill_view\n"
                        "names=" + repr(sorted(expected)) + "\n"
                        "listing=json.loads(skills_list())\n"
                        "assert listing['success']\n"
                        "assert set(names)<={s['name'] for s in listing['skills']}\n"
                        "for name in names:\n"
                        " value=json.loads(skill_view(name,preprocess=False))\n"
                        " assert value['success'],name\n"
                        "for scope in ('company','personal'):\n"
                        " value=json.loads(skill_view('mh-'+scope+'-demo','references/example.md',preprocess=False))\n"
                        " assert value['success']\n"
                        " assert 'Synthetic '+scope+' reference.' in json.dumps(value)\n"
                        "print('OFFICIAL_DISCOVERY_READY')\n"
                    )
                    discovery = subprocess.run(
                        [str(upstream / ".venv/bin/python"), "-c", probe],
                        env=environment,
                        cwd=upstream,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(discovery.returncode, 0, "Official skill discovery failed; raw output suppressed")
                    self.assertIn("OFFICIAL_DISCOVERY_READY", discovery.stdout)
                    result = subprocess.run(
                        [
                            str(command),
                            "chat",
                            "--query-file",
                            "-",
                            "--oneshot",
                            "-Q",
                            "-t",
                            "skills",
                            "--reasoning",
                            "none",
                            "--max-turns",
                            "2",
                            "--run-budget",
                            "60",
                            "--ignore-rules",
                        ],
                        input="Reply exactly SKILLS_READY.\n",
                        env=environment,
                        cwd=root,
                        capture_output=True,
                        text=True,
                        timeout=90,
                    )
                    self.assertEqual(result.returncode, 0, "Official skill-enabled turn failed; raw output suppressed")
                    self.assertIn("SKILLS_READY.", result.stdout)
                    self.assertNotIn(
                        environment["MYHERMES_SESSION_TOKEN"],
                        result.stdout + result.stderr + discovery.stdout + discovery.stderr,
                    )
                self.assertEqual(peer["errors"], [])
            main = [request for request in requests if request.get("tools")]
            self.assertTrue(main, "No skill tool schema reached the actual runtime's initial inference")
            names = {tool["function"]["name"] for tool in main[0]["tools"]}
            self.assertTrue({"skills_list", "skill_view", "skill_manage"} <= names)
            context = json.dumps(main[0]["messages"], ensure_ascii=False)
            for name in expected:
                self.assertIn(name, context, "A projected skill was absent from the actual initial context")


if __name__ == "__main__":
    unittest.main()
