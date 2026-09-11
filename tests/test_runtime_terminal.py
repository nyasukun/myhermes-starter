"""The real terminal device must work without replacing open('/dev/tty')."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

LAUNCHER = r"""
from contextlib import contextmanager
import errno,json,os,pty,select,signal,sys,time
from pathlib import Path
from unittest.mock import patch
from myhermes.runtime import start_runtime

root=Path(sys.argv[1])
command=root/'hermes'
command.write_text('#!'+sys.executable+'\n'+
    'import os,sys\n'
    'assert all(os.isatty(fd) for fd in (0,1,2))\n'
    'print("SYNTHETIC_TTY_READY",flush=True)\n'
    'assert sys.stdin.readline().strip()=="synthetic-input"\n'
    'print("SYNTHETIC_TTY_DONE",flush=True)\n')
command.chmod(0o700)
@contextmanager
def session(*args,**kwargs):
    yield command,{'PATH':os.defpath,'HOME':str(root)}
read_fd,write_fd=os.pipe()
pid,master=pty.fork()
if pid==0:
    os.close(read_fd)
    try:
        with patch('myhermes.runtime.relay_runtime_session',side_effect=session):
            result=start_runtime({})
        record={'ok':True,'runtime_exit_code':result['runtime_exit_code']}
    except Exception as error:
        # Only a fixed public error classification, never exception text.
        record={'ok':False,'terminal_required':getattr(error,'code',None)=='terminal_required'}
    os.write(write_fd,json.dumps(record).encode())
    os.close(write_fd)
    os._exit(0)
os.close(write_fd)
os.set_blocking(master,False)
captured=bytearray();sent=False;status=None
try:
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        ready,_,_=select.select([master],[],[],0.05)
        if ready:
            try: data=os.read(master,4096)
            except OSError as error:
                if error.errno!=errno.EIO:raise
                data=b''
            captured.extend(data)
            assert len(captured)<65536
            if b'SYNTHETIC_TTY_READY' in captured and not sent:
                os.write(master,b'synthetic-input\r');sent=True
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
    record=json.loads(os.read(read_fd,4096))
    record.update({'sent':sent,'complete':b'SYNTHETIC_TTY_DONE' in captured})
    print(json.dumps(record))
finally:
    try:os.killpg(pid,signal.SIGKILL)
    except ProcessLookupError:pass
    if status is None:os.waitpid(pid,0)
    os.close(master);os.close(read_fd);captured.clear()
"""


class RuntimeTerminalAcceptance(unittest.TestCase):
    def test_real_controlling_pty_supports_managed_child_read_and_write(self):
        with tempfile.TemporaryDirectory(prefix="myhermes-terminal-") as temporary:
            root = Path(temporary).resolve()
            source = Path(__file__).resolve().parent.parent / "src"
            result = subprocess.run(
                [sys.executable, "-c", LAUNCHER, str(root)],
                cwd=root,
                env={
                    "PATH": os.defpath,
                    "HOME": str(root),
                    "PYTHONPATH": str(source),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, "Dedicated PTY harness failed; raw output suppressed")
            self.assertEqual(
                json.loads(result.stdout),
                {"ok": True, "runtime_exit_code": 0, "sent": True, "complete": True},
            )


if __name__ == "__main__":
    unittest.main()
