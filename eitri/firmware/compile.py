"""The real verifier: compile the sketch with arduino-cli, headless. Never flashes anything.

arduino-cli is a system dependency (like Chromium is for PDF drawings): `EITRI_ARDUINO_CLI` names the binary, else `arduino-cli` on
PATH. Its own directories follow its usual ARDUINO_DIRECTORIES_DATA / _DOWNLOADS / _USER environment variables. Without the binary, or
without the board's core, compile() returns a result whose `available` is False and whose message says how to install it; the
checks turn that into a failed check, never a crashed build.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

TIMEOUT_S = 300.0
_DIAG = re.compile(r"^(?P<file>[^:\n]+?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<kind>error|warning|fatal error):\s*(?P<msg>.*)$", re.M)
_SIZE = re.compile(r"Sketch uses (\d+) bytes \((\d+)%\) of program storage space")
_RAM = re.compile(r"Global variables use (\d+) bytes \((\d+)%\) of dynamic memory")


@dataclass
class CompileResult:
    available: bool
    ok: bool
    message: str
    errors: list[dict] = field(default_factory=list)       # {file, line, col, kind, msg}
    warnings: list[dict] = field(default_factory=list)
    flash_bytes: int | None = None
    flash_pct: int | None = None
    ram_bytes: int | None = None
    ram_pct: int | None = None
    log: str = ""
    seconds: float = 0.0
    cached: bool = False
    command: list[str] = field(default_factory=list)


def find_cli() -> str | None:
    env = os.environ.get("EITRI_ARDUINO_CLI")
    if env:
        return env if Path(env).is_file() and os.access(env, os.X_OK) else None
    return shutil.which("arduino-cli")


def install_hint(core: str) -> str:
    return (f"install arduino-cli (https://arduino.github.io/arduino-cli/latest/installation/: "
            f"`curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh`, or `brew install arduino-cli`), then "
            f"`arduino-cli core update-index && arduino-cli core install {core}`" + (" (add the ESP32 package index first: "
            "`arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json`)" if core.startswith("esp32") else "")
            + "; or point EITRI_ARDUINO_CLI at the binary")


def _key(fqbn: str, sketch_dir: Path) -> str:
    h = hashlib.sha1(fqbn.encode())
    for f in sorted(sketch_dir.iterdir()):
        if f.is_file():
            h.update(f.name.encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def compile_sketch(sketch_dir: Path, fqbn: str, core: str, build_dir: Path, cache_file: Path | None = None, timeout: float = TIMEOUT_S) -> CompileResult:
    cli = find_cli()
    if cli is None:
        return CompileResult(False, False, "compiler unavailable: arduino-cli was not found; " + install_hint(core))
    key = _key(fqbn, sketch_dir)
    if cache_file is not None and cache_file.is_file():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data.get("key") == key:
                r = CompileResult(**data["result"])
                r.cached = True
                return r
        except (OSError, ValueError, TypeError):
            pass
    cmd = [cli, "compile", "--fqbn", fqbn, "--build-path", str(build_dir), "--warnings", "default", str(sketch_dir)]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return CompileResult(True, False, f"arduino-cli did not finish within {timeout:g} s", seconds=round(time.time() - t0, 1), command=cmd)
    except OSError as exc:
        return CompileResult(False, False, f"compiler unavailable: could not run {cli}: {exc}; " + install_hint(core), command=cmd)
    log = (proc.stdout or "") + (proc.stderr or "")
    seconds = round(time.time() - t0, 1)
    low = log.lower()
    if proc.returncode != 0 and ("platform not installed" in low or "unknown fqbn" in low or "invalid fqbn" in low or ("platform" in low and "not found" in low)):
        return CompileResult(False, False, f"compiler unavailable: the {core} core is not installed for arduino-cli; " + install_hint(core), log=log[-4000:], seconds=seconds, command=cmd)
    diags = [dict(file=Path(m.group("file")).name, line=int(m.group("line")), col=int(m.group("col") or 0), kind=m.group("kind"), msg=m.group("msg").strip())
             for m in _DIAG.finditer(log)]
    errors = [d for d in diags if d["kind"] != "warning"]
    warnings = [d for d in diags if d["kind"] == "warning"]
    r = CompileResult(True, proc.returncode == 0, "compiled" if proc.returncode == 0 else f"compile failed (arduino-cli exit {proc.returncode})",
                      errors=errors, warnings=warnings, log=log[-8000:], seconds=seconds, command=cmd)
    if proc.returncode != 0 and not errors:
        tail = [ln for ln in log.strip().splitlines() if ln.strip()][-3:]
        r.errors = [dict(file="", line=0, col=0, kind="error", msg=" / ".join(tail) or "arduino-cli failed without a diagnostic")]
    m, n = _SIZE.search(log), _RAM.search(log)
    if m:
        r.flash_bytes, r.flash_pct = int(m.group(1)), int(m.group(2))
    if n:
        r.ram_bytes, r.ram_pct = int(n.group(1)), int(n.group(2))
    if cache_file is not None and r.available:
        try:
            d = dict(r.__dict__)
            d.pop("cached", None)
            cache_file.write_text(json.dumps({"key": key, "result": d}), encoding="utf-8")
        except OSError:
            pass
    return r
