"""Handled termination must reap only the managed child before session finalization."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from myhermes.errors import CompanionError
from myhermes.files import file_lock

LAUNCHER = r'''
import json,os,signal,socket,sys,time
from pathlib import Path
from unittest.mock import patch
from myhermes import runtime
from myhermes.state import State
from test_session_sync import SessionSyncAcceptance

# Prove bootstrap and managed relay startup do not ask the OS resolver to
# reverse-resolve numeric loopback. A slow lookup used to hide every signal
# scenario behind its fixture.json readiness timeout on macOS CI.
def unexpected_reverse_lookup(*_args, **_kwargs):
    raise AssertionError('Numeric-loopback startup unexpectedly used reverse DNS')
socket.getfqdn=unexpected_reverse_lookup

outer=Path(sys.argv[1]);mode=sys.argv[2]
t=SessionSyncAcceptance();t.setUp()
(outer/'fixture.json').write_text(json.dumps({'home':str(t.home),'root':str(t.root)}))
runtime._MANAGED_TERMINATION_GRACE_SECONDS=0.4
original={kind:signal.getsignal(kind) for kind in (signal.SIGTERM,signal.SIGHUP)}
child_code=r"""
import json,os,signal,time
from pathlib import Path
outer=Path(__import__('sys').argv[1]);mode=__import__('sys').argv[2]
home=Path(os.environ['HERMES_HOME'])
def stop(kind,frame):
    (outer/'stopping').write_text('yes')
    time.sleep(0.18)
    (home/'memories/MEMORY.md').write_text('Synthetic stopped memory')
    raise SystemExit(0)
for kind in (signal.SIGTERM,signal.SIGHUP):signal.signal(kind,signal.SIG_IGN if mode=='ignore' else stop)
(home/'memories/MEMORY.md').write_text('Synthetic running memory')
(outer/'ready.json').write_text(json.dumps({'pid':os.getpid()}))
while True:time.sleep(0.05)
"""
t.child.write_text('#!'+sys.executable+'\n'+child_code.replace("__import__('sys').argv[1]",repr(str(outer))).replace("__import__('sys').argv[2]",repr(mode)))
t.child.chmod(0o700)
spawned=[];popen=__import__('subprocess').Popen
def spawn(*args,**kwargs):
    if mode=='before_spawn':os.kill(os.getpid(),signal.SIGTERM)
    child=popen(*args,**kwargs);spawned.append(child)
    if mode=='after_spawn':os.kill(os.getpid(),signal.SIGHUP)
    return child
try:
    with patch('myhermes.runtime.subprocess.Popen',side_effect=spawn):
        code,result=t.command('start')
    assert code==130 and result['runtime_interrupted']
    assert result['persona_after_session']['status']=='synchronized'
    assert 'skills_after_session' in result
    assert all(signal.getsignal(kind)==handler for kind,handler in original.items())
    assert len(spawned)==1
    try:os.waitpid(spawned[0].pid,os.WNOHANG)
    except ChildProcessError:reaped=True
    else:reaped=False
    assert reaped
    state=State(t.directory)
    try:assert state.get('session_checkpoint') is None
    finally:state.close()
    if mode in ('graceful','ignore'):
        expected='Synthetic running memory' if mode=='ignore' else 'Synthetic stopped memory'
        assert t.persona.current['files']['memories/MEMORY.md']['content']==expected
    (outer/'completed.json').write_text(json.dumps({'exit':code,'reaped':reaped,'handlers_restored':True,'checkpoint_completed':True}))
    until=time.monotonic()+10
    while not (outer/'release').exists() and time.monotonic()<until:time.sleep(0.02)
finally:t.doCleanups()
'''


class RuntimeSignalAcceptance(unittest.TestCase):
    def wait_for(self, path, parent, *, timeout=10):
        until = time.monotonic() + timeout
        while not path.exists() and parent.poll() is None and time.monotonic() < until:
            time.sleep(0.01)
        self.assertTrue(
            path.exists(),
            f"Synthetic signal fixture did not reach {path.name}; launcher exit status: {parent.poll()}",
        )

    def scenario(self, mode, kind=signal.SIGTERM):
        with tempfile.TemporaryDirectory(prefix="myhermes-signal-test-") as temporary:
            outer = Path(temporary).resolve()
            launcher = outer / "launcher.py"
            launcher.write_text(LAUNCHER)
            project = Path(__file__).resolve().parent.parent
            environment = {
                "PATH": os.defpath,
                "HOME": str(outer),
                "PYTHONPATH": str(project / "src") + os.pathsep + str(project / "tests"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            parent = subprocess.Popen(
                [sys.executable, str(launcher), str(outer), mode],
                cwd=project,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                self.wait_for(outer / "fixture.json", parent)
                home = Path(json.loads((outer / "fixture.json").read_text())["home"])
                if mode in ("graceful", "ignore"):
                    self.wait_for(outer / "ready.json", parent)
                    os.kill(parent.pid, kind)
                    if mode == "graceful":
                        self.wait_for(outer / "stopping", parent)
                    with self.assertRaises(CompanionError) as held:
                        with file_lock(home / ".myhermes-session.lock"):
                            pass
                    self.assertEqual(held.exception.exit_code, 7)
                    if mode == "ignore":
                        began = time.monotonic()
                        for _ in range(30):
                            if (outer / "completed.json").exists():
                                break
                            child_pid = json.loads((outer / "ready.json").read_text())["pid"]
                            try:
                                os.kill(child_pid, 0)
                            except ProcessLookupError:
                                break
                            os.kill(parent.pid, kind)
                            time.sleep(0.08)
                        self.wait_for(outer / "completed.json", parent, timeout=2)
                        self.assertLess(time.monotonic() - began, 1.5)
                self.wait_for(outer / "completed.json", parent)
                completed = json.loads((outer / "completed.json").read_text())
                self.assertEqual(
                    completed, {"exit": 130, "reaped": True, "handlers_restored": True, "checkpoint_completed": True}
                )
                with file_lock(home / ".myhermes-session.lock"):
                    pass
                (outer / "release").write_text("yes")
                parent.wait(timeout=5)
                self.assertEqual(parent.returncode, 0)
            finally:
                # Only the fixture's newly created process group, never a host
                # service or the test runner's group. Also cleans the pre-fix orphan.
                try:
                    os.killpg(parent.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                parent.wait(timeout=5)
                fixture = outer / "fixture.json"
                if fixture.exists():
                    import shutil

                    shutil.rmtree(json.loads(fixture.read_text())["root"], ignore_errors=True)

    def test_sigterm_and_sighup_reap_child_before_releasing_lock_and_checkpoint(self):
        for kind in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=kind):
                self.scenario("graceful", kind)

    def test_ignored_repeated_signal_has_bounded_escalation_without_extending_grace(self):
        self.scenario("ignore")

    def test_termination_during_spawn_is_delivered_after_child_becomes_available(self):
        for mode in ("before_spawn", "after_spawn"):
            with self.subTest(mode=mode):
                self.scenario(mode)


if __name__ == "__main__":
    unittest.main()
