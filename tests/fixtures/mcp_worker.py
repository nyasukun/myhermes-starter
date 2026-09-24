"""Synthetic worker used by actual SDK stdio tests. Never calls a provider."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

prompt = sys.stdin.read()
if sys.argv[-1] == "status":
    print(json.dumps({"status": "ready", "enrolled": True}))
    sys.exit(0)
if prompt == "fail":
    print("PRIVATE_FAKE_ERROR", file=sys.stderr)
    print("PRIVATE_FAKE_ERROR")
    sys.exit(7)
if prompt == "overflow":
    print("x" * 1_048_577)
    sys.exit(0)
if prompt == "invalid":
    print("{bad")
    sys.exit(0)
if prompt in ("sleep", "descendant"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path("fixture-pids.json").write_text(json.dumps([os.getpid(), child.pid]))
    if prompt == "descendant":
        sys.exit(0)
    time.sleep(120)
print(
    json.dumps({"status": "completed", "answer": prompt, "environment_names": sorted(os.environ), "args": sys.argv[1:]})
)
