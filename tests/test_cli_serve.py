"""`eitri serve <new folder>` creates a blank design and serves the viewer."""
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_blank_project(tmp_path: Path):
    port = _free_port()
    folder = tmp_path / "fresh"
    proc = subprocess.Popen([sys.executable, "-m", "eitri.cli", "serve", str(folder), "--port", str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    try:
        state = None
        for _ in range(120):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/state.json", timeout=5) as r:
                    state = json.loads(r.read())
                if state.get("ok_revision"):
                    break
            except Exception:
                continue
        assert state and state["ok_revision"] >= 1 and state["parts"] == [] and (folder / "fresh.py").exists()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            assert b"<title>eitri</title>" in r.read()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
