"""A warm build worker: one long-lived subprocess that imports the CAD kernel once.

The REPL, the MCP server and the viewer each own one worker (it dies with them;
there is no global daemon, no state folder). A build request is one JSON line on
the worker's stdin; the worker runs exactly what `eitri._runner` runs and
answers with one JSON line. If the kernel crashes or a build times out, the
parent kills the worker and starts a fresh one on the next build.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


def _purge_project_modules(folder: Path) -> None:
    """Forget modules loaded from the model's folder so edits to helpers are picked up.

    Also drops the folder's __pycache__: Python validates bytecode by mtime (whole
    seconds) and size, so a helper edited within a second of the last build with
    the same length would otherwise keep running the old code.
    """
    import importlib
    import shutil

    folder_s = str(folder)
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if f and str(Path(f).resolve()).startswith(folder_s):
            del sys.modules[name]
    for cache in list(folder.glob("__pycache__")) + list(folder.glob("*/__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    importlib.invalidate_caches()


def serve() -> int:
    """Worker main loop: stdin JSON lines in, JSON lines out."""
    from . import _runner   # imports the kernel once, here
    sys.dont_write_bytecode = True
    out = sys.stdout
    sys.stdout = sys.stderr          # anything a model prints must not corrupt the protocol
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if req.get("op") == "ping":
            out.write(json.dumps({"id": req.get("id"), "ok": True}) + "\n")
            out.flush()
            continue
        argv = list(req.get("argv", []))
        model = req.get("model")
        if model:
            _purge_project_modules(Path(model).resolve().parent)
        t0 = time.time()
        try:
            code = _runner.main(argv)
            reply = {"id": req.get("id"), "code": code, "seconds": round(time.time() - t0, 2)}
        except SystemExit as exc:
            reply = {"id": req.get("id"), "code": int(exc.code or 0), "seconds": round(time.time() - t0, 2)}
        except BaseException as exc:  # noqa: BLE001 - report anything, keep serving
            reply = {"id": req.get("id"), "code": 1, "error": f"{type(exc).__name__}: {exc}",
                     "seconds": round(time.time() - t0, 2)}
        out.write(json.dumps(reply) + "\n")
        out.flush()
    return 0


class Worker:
    """Parent-side handle: starts the worker lazily, restarts it after a crash or timeout."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next = 0
        self.restarts = 0

    def _start(self) -> None:
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        self.proc = subprocess.Popen([sys.executable, "-m", "eitri._worker"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, env=env)
        self._stderr_tail: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stderr is not None
        for line in proc.stderr:
            self._stderr_tail.append(line.rstrip())
            if len(self._stderr_tail) > 400:
                del self._stderr_tail[:-400]

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc is not None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    def run(self, argv: list[str], model: str, cwd: str | None = None, timeout: float = 240.0) -> tuple[int, str, str]:
        """Run eitri._runner with argv in the warm worker. Returns (code, stdout_tail, stderr_tail).

        cwd is not changed in the worker: model paths are absolute and the runner
        resolves everything itself.
        """
        with self._lock:
            if not self.alive():
                if self.proc is not None:   # it died on its own (kernel crash)
                    self.restarts += 1
                self._start()
            assert self.proc and self.proc.stdin and self.proc.stdout
            self._next += 1
            req = {"id": self._next, "argv": argv, "model": model}
            before = len(self._stderr_tail)
            try:
                self.proc.stdin.write(json.dumps(req) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self.stop()
                return 1, "", "worker pipe broken"
            reply = self._read_reply(timeout)
            err = "\n".join(self._stderr_tail[before:])
            if reply is None:
                self.proc.kill()
                self.proc = None
                self.restarts += 1          # the next build starts a fresh worker
                return -9, "", err + "\n[worker killed: timeout or crash]"
            if reply.get("error"):
                err += "\n" + reply["error"]
            return int(reply.get("code", 1)), "", err

    def ping(self, timeout: float = 120.0) -> bool:
        """Start the worker if needed and wait until it answers: the kernel is imported then."""
        with self._lock:
            if not self.alive():
                self._start()
            assert self.proc and self.proc.stdin and self.proc.stdout
            self._next += 1
            try:
                self.proc.stdin.write(json.dumps({"op": "ping", "id": self._next}) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                return False
            reply = self._read_reply(timeout)
            return bool(reply and reply.get("ok"))

    def _read_reply(self, timeout: float) -> dict | None:
        assert self.proc and self.proc.stdout
        result: dict[str, Any] = {}

        def reader() -> None:
            try:
                line = self.proc.stdout.readline()  # type: ignore[union-attr]
                if line:
                    result["reply"] = json.loads(line)
            except Exception:
                pass

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            return None
        return result.get("reply")


if __name__ == "__main__":
    sys.exit(serve())
