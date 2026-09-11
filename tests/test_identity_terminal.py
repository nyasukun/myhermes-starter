"""Same-home recovery confirmation uses a real, isolated controlling terminal."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


LAUNCHER = r"""
import errno,json,os,pty,select,signal,sys,termios,threading,time
from myhermes.identity_recovery import _confirm
case=sys.argv[1]
read_fd,write_fd=os.pipe()
pid,master=pty.fork()
if pid==0:
    os.close(read_fd)
    previous=termios.tcgetattr(0)
    handlers={kind:signal.getsignal(kind) for kind in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
    try:
        if case=='thread':
            result={}
            def worker():
                try:_confirm()
                except Exception as error:result['error']=getattr(error,'code','unclassified')
            thread=threading.Thread(target=worker);thread.start();thread.join(2)
            assert not thread.is_alive()
            result['ok']=False
            raise SystemExit
        _confirm(cancel=case=='cancel-candidate')
        result={'ok':True}
    except KeyboardInterrupt:
        result={'ok':False,'interrupted':True}
    except SystemExit:
        pass
    except Exception as error:
        result={'ok':False,'error':getattr(error,'code','unclassified')}
    result['terminal_restored']=termios.tcgetattr(0)==previous
    result['handlers_restored']=all(signal.getsignal(kind)==handler for kind,handler in handlers.items())
    os.write(write_fd,json.dumps(result).encode());os.close(write_fd);os._exit(0)
os.close(write_fd);os.set_blocking(master,False)
captured=bytearray();sent=False;status=None
try:
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        ready,_,_=select.select([master],[],[],0.05)
        if ready:
            try:data=os.read(master,4096)
            except OSError as error:
                if error.errno!=errno.EIO:raise
                data=b''
            captured.extend(data);assert len(captured)<65536
            if b'Type yes: ' in captured and not sent:
                if case in ('int','term','hup'):
                    os.kill(pid,{'int':signal.SIGINT,'term':signal.SIGTERM,'hup':signal.SIGHUP}[case])
                elif case!='thread':
                    value={'decline':'no','long':'yes'+' '*29+'NO','padded':' yes '}.get(case,'yes')
                    os.write(master,(value+'\r').encode())
                sent=True
        ended,code=os.waitpid(pid,os.WNOHANG)
        if ended:status=code;break
    assert status is not None
    raw=os.read(read_fd,4096)
    result=json.loads(raw) if raw else {'ok':False,'terminated':True}
    result['prompt_seen']=sent
    result['intended_action_seen']=(b'Cancel this unpublished candidate' if case=='cancel-candidate'
       else b'Replace this installation key for the SAME owner') in captured
    print(json.dumps(result))
finally:
    if status is None:os.kill(pid,signal.SIGKILL);os.waitpid(pid,0)
    os.close(master);os.close(read_fd);captured.clear()
"""


class IdentityTerminalAcceptance(unittest.TestCase):
    def scenario(self, case):
        with tempfile.TemporaryDirectory(prefix="myhermes-identity-tty-") as temporary:
            source = Path(__file__).resolve().parent.parent / "src"
            completed = subprocess.run(
                [sys.executable, "-c", LAUNCHER, case],
                cwd=temporary,
                env={"PATH": os.defpath, "HOME": temporary, "PYTHONPATH": str(source)},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
        self.assertEqual(completed.returncode, 0, "Synthetic PTY harness failed; raw output suppressed")
        return json.loads(completed.stdout)

    def test_owner_can_confirm_replace_cancel_candidate_or_decline_on_real_terminal(self):
        for case in ("replace", "cancel-candidate", "decline"):
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix="myhermes-identity-tty-") as temporary:
                source = Path(__file__).resolve().parent.parent / "src"
                completed = subprocess.run(
                    [sys.executable, "-c", LAUNCHER, case],
                    cwd=temporary,
                    env={"PATH": os.defpath, "HOME": temporary, "PYTHONPATH": str(source)},
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(completed.returncode, 0, "Synthetic PTY harness failed; raw output suppressed")
                result = json.loads(completed.stdout)
                self.assertTrue(result["prompt_seen"])
                self.assertTrue(result["intended_action_seen"])
                self.assertEqual(result["ok"], case != "decline")
                if case == "decline":
                    self.assertEqual(result.get("error"), "identity_recovery_cancelled")

    def test_full_line_rejects_long_or_padded_yes_prefix(self):
        for case in ("long", "padded"):
            with self.subTest(case=case):
                result = self.scenario(case)
                self.assertFalse(result["ok"])
                self.assertEqual(result.get("error"), "identity_recovery_cancelled")

    def test_handled_signals_restore_original_terminal_and_handlers(self):
        for case in ("int", "term", "hup"):
            with self.subTest(case=case):
                result = self.scenario(case)
                self.assertTrue(result.get("interrupted"))
                self.assertTrue(result.get("terminal_restored"))
                self.assertTrue(result.get("handlers_restored"))

    def test_non_main_thread_rejected_without_prompt(self):
        result = self.scenario("thread")
        self.assertEqual(result.get("error"), "terminal_required")
        self.assertFalse(result.get("prompt_seen"))
        self.assertTrue(result.get("terminal_restored"))
        self.assertTrue(result.get("handlers_restored"))


if __name__ == "__main__":
    unittest.main()
