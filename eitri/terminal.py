"""The person's own terminal (/dev/tty), for questions and one-time codes that must not go through stdin/stdout.

`open("/dev/tty", "r+")` does not work: a read-write text handle sits on io.BufferedRandom, which needs a seekable file,
and a terminal is not one, so it raises io.UnsupportedOperation (an OSError) on every real terminal. Fake terminals in
tests never showed it. TerminalIO opens the terminal twice instead, once to read and once to write.
"""
from __future__ import annotations

import os

TTY_PATH = "/dev/tty"


class TerminalIO:
    """/dev/tty as a separate reader and writer: write(), flush(), readline(), fileno(), close(), and a context manager.
    Raises OSError when there is no controlling terminal."""

    def __init__(self, path: str = TTY_PATH):
        self._r = open(path, "r", encoding="utf-8")
        try:
            self._w = open(path, "w", encoding="utf-8")
        except BaseException:
            self._r.close()
            raise

    def write(self, s: str) -> int:
        return self._w.write(s)

    def flush(self) -> None:
        self._w.flush()

    def readline(self, *args) -> str:
        self._w.flush()                                                  # the prompt is on screen before we wait
        return self._r.readline(*args)

    def fileno(self) -> int:
        return self._r.fileno()

    def close(self) -> None:
        for f in (self._w, self._r):
            try:
                f.close()
            except OSError:
                pass

    def __enter__(self) -> "TerminalIO":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def ttyname(t: TerminalIO) -> str:
    try:
        return os.ttyname(t.fileno())
    except OSError:
        return "tty"
