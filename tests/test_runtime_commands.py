"""Actual local installer-process termination; synthetic children, no network."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from myhermes import runtime
from myhermes.errors import CompanionError
from myhermes.files import file_lock


_CHILD = r"""
import os,pathlib,signal,subprocess,sys,time
root=pathlib.Path(sys.argv[1]); mode=sys.argv[2]
for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
 signal.signal(sig,signal.SIG_IGN)
if mode == 'grandchild':
 code="import os,pathlib,signal,sys,time; root=pathlib.Path(sys.argv[1]); [signal.signal(sig,signal.SIG_IGN) for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT)]; (root/'grandchild').write_text(str(os.getpid())); time.sleep(120)"
 subprocess.Popen([sys.executable,'-c',code,str(root)])
 while not (root/'grandchild').exists(): time.sleep(.01)
 def stop(sig,frame): raise SystemExit(0)
 for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT): signal.signal(sig,stop)
(root/'ready').write_text(str(os.getpid()))
(root/'group').write_text(str(os.getpgrp()))
print('SYNTHETIC_SUBPROCESS_PRIVATE_OUTPUT',flush=True)
print('SYNTHETIC_SUBPROCESS_PRIVATE_OUTPUT',file=sys.stderr,flush=True)
time.sleep(120)
"""

_WORKER = r"""
import json,os,pathlib,signal,subprocess,sys,time
from unittest.mock import patch
from myhermes import runtime
from myhermes.errors import CompanionError
from myhermes.files import atomic_content,file_lock
from myhermes.state import State
root=pathlib.Path(sys.argv[1]); mode=sys.argv[2]; child_code=sys.argv[3]
runtime._TERMINATION_GRACE_SECONDS=.35
runtime._COMMAND_TIMEOUT_SECONDS=.2 if mode=='timeout' else 20
home=root/'home'; atomic_content(home,'SOUL.md','Synthetic owner persona')
state=State(root/'state')
previous={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT)}
actual=subprocess.Popen
def create(*args,**kwargs):
 if mode=='before_spawn': os.kill(os.getpid(),signal.SIGTERM)
 result=actual(*args,**kwargs)
 (root/'spawned').write_text(str(result.pid))
 if mode=='after_spawn': os.kill(os.getpid(),signal.SIGTERM)
 return result
runtime.install_runtime=lambda *args,**kwargs: runtime.checked([sys.executable,'-c',child_code,str(root),mode])
with file_lock(home/'.myhermes-session.lock'):
 try:
  with patch.object(runtime.subprocess,'Popen',side_effect=create):
   runtime.upgrade_runtime(state,home,root/'unused','unused')
 except CompanionError as error:
  result={'error':error.code,'exit_code':error.exit_code,'backup_id_reported':'backup ID:' in error.message,'output_suppressed':'SYNTHETIC_SUBPROCESS_PRIVATE_OUTPUT' not in error.message}
 except BaseException as error:
  result={'unexpected':type(error).__name__}
 else: result={'unexpected':'no_error'}
 result['handlers_restored']=all(signal.getsignal(sig)==old for sig,old in previous.items())
 (root/'completed').write_text(json.dumps(result))
 while not (root/'release').exists(): time.sleep(.01)
state.close()
"""


class RuntimeCommandTermination(unittest.TestCase):
    def wait_for(self, path, process, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            if process.poll() is not None:
                self.fail("Installer fixture exited before reaching the expected boundary")
            time.sleep(0.01)
        self.fail("Installer fixture exceeded the bounded termination deadline")

    def live_process(self, pid):
        # Test-only metadata handles orphan zombies without adding procps to a
        # minimal Ubuntu. No command-line/environment contents are read.
        if sys.platform == "linux":
            try:
                return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] != "Z"
            except FileNotFoundError:
                return False
        result = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
        return result.returncode == 0 and any(not row.strip().startswith("Z") for row in result.stdout.splitlines())

    def scenario(self, mode, kind=signal.SIGTERM):
        with tempfile.TemporaryDirectory(prefix="myhermes-command-signal-") as temporary:
            root = Path(temporary).resolve()
            env = {
                "HOME": str(root),
                "PATH": os.defpath,
                "PYTHONPATH": str(Path(runtime.__file__).resolve().parents[1]),
            }
            parent = subprocess.Popen(
                [sys.executable, "-c", _WORKER, str(root), mode, _CHILD],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            pids = []
            try:
                if mode not in ("before_spawn", "after_spawn"):
                    self.wait_for(root / "ready", parent)
                    pids.append(int((root / "ready").read_text()))
                    self.wait_for(root / "group", parent)
                    self.assertEqual(int((root / "group").read_text()), pids[0])
                    if mode == "grandchild":
                        pids.append(int((root / "grandchild").read_text()))
                    if mode != "timeout":
                        os.kill(parent.pid, kind)
                        with self.assertRaises(CompanionError) as held:
                            with file_lock(root / "home/.myhermes-session.lock"):
                                pass
                        self.assertEqual(held.exception.code, "home_busy")
                        for _ in range(4):
                            if (root / "completed").exists():
                                break
                            time.sleep(0.05)
                            os.kill(parent.pid, kind)
                self.wait_for(root / "completed", parent, timeout=2)
                result = json.loads((root / "completed").read_text())
                self.assertEqual(
                    result,
                    {
                        "error": "runtime_command_timeout" if mode == "timeout" else "runtime_command_interrupted",
                        "exit_code": 3 if mode == "timeout" else 130,
                        "backup_id_reported": True,
                        "output_suppressed": True,
                        "handlers_restored": True,
                    },
                )
                for pid in pids:
                    self.assertFalse(self.live_process(pid), "An installer writer was alive after cleanup returned")
                (root / "release").write_text("release")
                parent.wait(timeout=5)
                self.assertEqual(parent.returncode, 0)
                with file_lock(root / "home/.myhermes-session.lock"):
                    pass
            finally:
                (root / "release").write_text("release")
                if parent.poll() is None:
                    parent.kill()
                parent.wait(timeout=5)
                for name in ("spawned", "ready", "grandchild"):
                    if (root / name).exists():
                        try:
                            os.kill(int((root / name).read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_term_hup_int_stop_ignored_child_before_releasing_lock(self):
        for kind in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=kind):
                self.scenario("ignore", kind)

    def test_direct_child_exit_does_not_leave_same_group_grandchild_running(self):
        self.scenario("grandchild")

    def test_timeout_stops_group_and_returns_sanitized_backup_error(self):
        self.scenario("timeout")

    def test_signal_before_and_after_spawn_remains_bounded(self):
        for mode in ("before_spawn", "after_spawn"):
            with self.subTest(mode=mode):
                self.scenario(mode)

    def test_non_main_thread_rejected_before_subprocess_creation(self):
        output = []

        def run():
            try:
                runtime.checked([sys.executable, "-c", "pass"])
            except CompanionError as error:
                output.append(error.code)

        with patch.object(runtime.subprocess, "Popen") as popen:
            worker = threading.Thread(target=run)
            worker.start()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(output, ["runtime_main_thread_required"])
        popen.assert_not_called()

    def test_normal_output_and_failure_remain_bounded_to_checked_contract(self):
        self.assertEqual(runtime.checked([sys.executable, "-c", "print('SYNTHETIC_PIN')"]), "SYNTHETIC_PIN")
        with self.assertRaises(CompanionError) as failed:
            runtime.checked([sys.executable, "-c", "print('SYNTHETIC_PRIVATE'); raise SystemExit(7)"])
        self.assertEqual(failed.exception.code, "runtime_command_failed")
        self.assertNotIn("SYNTHETIC_PRIVATE", failed.exception.message)

    def test_failed_spawn_restores_handlers_and_suppresses_exception_text(self):
        watched = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
        previous = {kind: signal.getsignal(kind) for kind in watched}
        with patch.object(runtime.subprocess, "Popen", side_effect=OSError("SYNTHETIC_PRIVATE")):
            with self.assertRaises(CompanionError) as failed:
                runtime.checked(["synthetic-missing-executable"])
        self.assertEqual(failed.exception.code, "runtime_command_failed")
        self.assertNotIn("SYNTHETIC_PRIVATE", failed.exception.message)
        self.assertEqual({kind: signal.getsignal(kind) for kind in watched}, previous)
