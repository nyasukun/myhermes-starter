"""Exercise secure authorization prompts on an isolated, wholly synthetic PTY."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


LAUNCHER = r"""
import errno,json,os,pty,select,signal,sys,termios,threading,time
from myhermes.connection_terminal import NativeConnectionTerminal

case=sys.argv[1]
token="github_pat_"+"synthetic"*4
template={"template_id":"synthetic","version":"1.0.0",
 "auth":{"required_permissions":["metadata:read"]},
 "usage_notice":{"version":"1","text":"合成の業務利用確認"}}
read_fd,write_fd=os.pipe()
gate_read,gate_write=os.pipe()
pid,master=pty.fork()
if pid==0:
    os.close(read_fd)
    os.close(gate_write);os.read(gate_read,1);os.close(gate_read)
    terminal=None
    previous=termios.tcgetattr(0)
    handlers={kind:signal.getsignal(kind) for kind in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
    record={"ok":False}
    try:
        terminal=NativeConnectionTerminal()
        if case=='thread':
            def worker():
                try:terminal.read_token()
                except Exception as error:record['error']=getattr(error,'code','unclassified')
            thread=threading.Thread(target=worker);thread.start();thread.join(2)
            assert not thread.is_alive()
            raise SystemExit
        terminal.describe(template,{"resource":"example/repository"})
        received=terminal.read_token()
        record["token_match"]=received==token
        terminal.confirm(template,{"login":"synthetic","provider_account_id":"1"},{})
        record["ok"]=True
    except KeyboardInterrupt:
        record["interrupted"]=True
    except SystemExit:
        pass
    except Exception as error:
        record["error"]=getattr(error,"code","unclassified")
    finally:
        record["terminal_restored"]=termios.tcgetattr(0)==previous
        record["handlers_restored"]=all(signal.getsignal(kind)==handler for kind,handler in handlers.items())
        if terminal is not None:terminal.close()
    os.write(write_fd,json.dumps(record).encode())
    os.close(write_fd)
    os._exit(0)
os.close(write_fd)
os.close(gate_read)
if case=='paste-noncanonical':
    attributes=termios.tcgetattr(master);attributes[3]&=~termios.ICANON
    attributes[6][termios.VMIN]=1;attributes[6][termios.VTIME]=0
    termios.tcsetattr(master,termios.TCSANOW,attributes)
previous=termios.tcgetattr(master)
os.write(gate_write,b'g');os.close(gate_write)
os.set_blocking(master,False)
captured=bytearray();sent=False;confirmed=False;status=None;hidden=False;canonical=False;confirm_at=None
try:
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        ready,_,_=select.select([master],[],[],0.05)
        if ready:
            try:data=os.read(master,4096)
            except OSError as error:
                if error.errno!=errno.EIO:raise
                data=b''
            captured.extend(data)
            assert len(captured)<65536
            if b'Fine-grained PAT (hidden): ' in captured and not sent:
                hidden=not bool(termios.tcgetattr(master)[3] & (termios.ECHO|termios.ECHONL))
                canonical=bool(termios.tcgetattr(master)[3] & termios.ICANON)
                if case in ('interrupt','term','hup'):
                    os.kill(pid,{'interrupt':signal.SIGINT,'term':signal.SIGTERM,'hup':signal.SIGHUP}[case])
                else:
                    value=(token if case!='invalid' else 'invalid-synthetic-token')+'\r'
                    if case.startswith('paste-'):value+='yes\r'
                    os.write(master,value.encode())
                sent=True
            if b'Type yes: ' in captured and not confirmed:
                if case.startswith('paste-'):
                    if confirm_at is None:confirm_at=time.monotonic()
                else:
                    value={'cancel':'no','long-confirm':'yes'+' '*29+'NO','padded-confirm':' yes '}.get(case,'yes')
                    os.write(master,(value+'\r').encode());confirmed=True
        if confirm_at is not None and not confirmed and time.monotonic()-confirm_at>0.3:
            os.write(master,b'no\r');confirmed=True
        ended,code=os.waitpid(pid,os.WNOHANG)
        if ended:
            status=code
            while True:
                try:data=os.read(master,4096)
                except (BlockingIOError,OSError):break
                if not data:break
                captured.extend(data)
                assert len(captured)<65536
            break
    assert status is not None
    raw=os.read(read_fd,4096)
    record=json.loads(raw) if raw else {'ok':False,'terminated':True,'terminal_restored':termios.tcgetattr(master)==previous}
    record.update({"sent":sent,"confirmed":confirmed,"hidden":hidden,
      "canonical":canonical,
      "token_absent":token.encode() not in captured and b'invalid-synthetic-token' not in captured,
      "unicode_notice":"合成の業務利用確認".encode() in captured})
    print(json.dumps(record))
finally:
    if status is None:
        os.kill(pid,signal.SIGKILL);os.waitpid(pid,0)
    os.close(master);os.close(read_fd);captured.clear()
"""


class ConnectionTerminalAcceptance(unittest.TestCase):
    def scenario(self, case, *, expect_prompt=True):
        with tempfile.TemporaryDirectory(prefix="myhermes-connection-tty-") as temporary:
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
        fields = ("sent", "hidden", "token_absent", "terminal_restored") if expect_prompt else ("terminal_restored",)
        for field in fields:
            self.assertTrue(result[field], field)
        return result

    def test_hidden_token_and_unicode_confirmation_on_real_terminal(self):
        result = self.scenario("accept")
        for field in ("ok", "token_match", "confirmed", "unicode_notice"):
            self.assertTrue(result[field], field)

    def test_cancellation_restores_terminal_without_accepting_scope(self):
        result = self.scenario("cancel")
        self.assertEqual(result.get("error"), "authorization_cancelled")
        self.assertFalse(result["ok"])

    def test_invalid_credential_restores_terminal_without_confirmation(self):
        result = self.scenario("invalid")
        self.assertEqual(result.get("error"), "credential_format_rejected")
        self.assertFalse(result["confirmed"])

    def test_interrupt_restores_echo_before_exit(self):
        result = self.scenario("interrupt")
        self.assertTrue(result.get("interrupted"))
        self.assertFalse(result["confirmed"])

    def test_termination_restores_terminal_and_original_signal_handlers(self):
        for case in ("term", "hup"):
            with self.subTest(case=case):
                result = self.scenario(case)
                self.assertTrue(result.get("interrupted"))
                self.assertTrue(result.get("handlers_restored"))
                self.assertFalse(result["confirmed"])

    def test_pasted_confirmation_requires_fresh_complete_line_in_both_terminal_modes(self):
        for case in ("paste-canonical", "paste-noncanonical"):
            with self.subTest(case=case):
                result = self.scenario(case)
                self.assertTrue(result["canonical"])
                self.assertTrue(result["confirmed"])
                self.assertFalse(result["ok"])
                self.assertEqual(result.get("error"), "authorization_cancelled")

    def test_long_or_padded_yes_prefix_never_approves(self):
        for case in ("long-confirm", "padded-confirm"):
            with self.subTest(case=case):
                result = self.scenario(case)
                self.assertFalse(result["ok"])
                self.assertEqual(result.get("error"), "authorization_cancelled")

    def test_worker_thread_fails_before_hidden_input_or_signal_changes(self):
        result = self.scenario("thread", expect_prompt=False)
        self.assertEqual(result.get("error"), "terminal_required")
        self.assertFalse(result["sent"])
        self.assertTrue(result["handlers_restored"])


if __name__ == "__main__":
    unittest.main()
