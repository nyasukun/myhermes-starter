"""Opt-in actual pinned Hermes CLI with a local synthetic provider, no live account."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from myhermes.runtime import relay_runtime_session
from test_relay_bridge import company


@unittest.skipUnless(os.getenv("MYHERMES_TEST_UPSTREAM"), "Requires the explicitly selected installed Hermes checkout")
class HermesOneshotAcceptance(unittest.TestCase):
    def test_real_cli_completes_synthetic_turn_through_device_bridge(self):
        with tempfile.TemporaryDirectory(prefix="myhermes-oneshot-") as directory:
            home = Path(os.path.realpath(directory)) / "home"
            config = {"upstream": os.environ["MYHERMES_TEST_UPSTREAM"], "hermes_home": str(home)}
            events = []
            with company(completion_text="HERMES_READY.") as peer:
                with relay_runtime_session(
                    config,
                    api=peer["api"],
                    allow_local_http=True,
                    on_activity=lambda kind, attrs: events.append((kind, attrs)),
                ) as (command, environment):
                    # Prompt is a fictional constant on stdin; the bearer is only
                    # in the process environment and never a command argument.
                    result = subprocess.run(
                        [
                            str(command),
                            "chat",
                            "--query-file",
                            "-",
                            "--oneshot",
                            "-Q",
                            "-t",
                            "none",
                            "--reasoning",
                            "none",
                            "--max-turns",
                            "2",
                            "--run-budget",
                            "60",
                            "--ignore-rules",
                        ],
                        input="Reply exactly HERMES_READY.\n",
                        env=environment,
                        cwd=directory,
                        capture_output=True,
                        text=True,
                        timeout=90,
                    )
                    self.assertNotIn(environment["MYHERMES_SESSION_TOKEN"], result.stdout + result.stderr)
                    self.assertEqual(
                        result.returncode, 0, "Actual Hermes fixture turn failed; raw output intentionally suppressed"
                    )
                    self.assertIn("HERMES_READY.", result.stdout)
                self.assertEqual(peer["errors"], [])
                self.assertGreaterEqual(len(peer["executions"]), 1)
                self.assertTrue(all(count == 1 for count in peer["executions"].values()))
                self.assertTrue(any(kind == "model" and attrs["outcome"] == "ok" for kind, attrs in events))


if __name__ == "__main__":
    unittest.main()
