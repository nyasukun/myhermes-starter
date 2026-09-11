"""Bounded fresh terminal lines with scoped mode and handled-signal restoration."""

import os
import signal
import termios
import threading

from .errors import CompanionError

_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def read_terminal_line(stream, prompt, *, hidden=False, limit=32):
    """Return one complete UTF-8 line, or None for EOF, excess length or bad UTF-8.

    Read the descriptor directly: a TextIOWrapper's decoded read-ahead buffer
    cannot be discarded by tcflush and must not carry a pasted authorization.
    """
    if threading.current_thread() is not threading.main_thread():
        raise CompanionError("terminal_required", "Native confirmation must run on the CLI's main thread.", 3)
    fd = stream.fileno()
    previous = termios.tcgetattr(fd)
    attributes = list(previous)
    attributes[0] = (attributes[0] | termios.ICRNL) & ~(termios.IGNCR | termios.INLCR)
    attributes[3] |= termios.ICANON | termios.ISIG
    if hidden:
        attributes[3] &= ~(termios.ECHO | termios.ECHONL)
    handlers = {}

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    try:
        for kind in _SIGNALS:
            handlers[kind] = signal.getsignal(kind)
            signal.signal(kind, interrupt)
        termios.tcsetattr(fd, termios.TCSAFLUSH, attributes)
        stream.write(prompt)
        stream.flush()
        line = bytearray()
        while len(line) <= limit:
            value = os.read(fd, 1)
            if not value:
                return None
            if value == b"\n":
                try:
                    return line.decode("utf-8")
                except UnicodeDecodeError:
                    return None
            line.extend(value)
        return None
    finally:
        # Block a second handled signal only during the restoration critical
        # section. A pending signal is delivered after original state is back.
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, _SIGNALS)
        try:
            try:
                termios.tcsetattr(fd, termios.TCSAFLUSH, previous)
                if hidden:
                    stream.write("\n")
                    stream.flush()
            finally:
                for kind, handler in handlers.items():
                    signal.signal(kind, handler)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
