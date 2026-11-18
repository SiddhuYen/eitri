"""The real verifier: compile the sketch with arduino-cli, headless. Never flashes anything.

arduino-cli is a system dependency (like Chromium is for PDF drawings): `EITRI_ARDUINO_CLI` names the binary, else `arduino-cli` on
PATH, else ~/.local/bin, /opt/homebrew/bin or /usr/local/bin (`eitri setup firmware` installs it). Its own directories follow its usual ARDUINO_DIRECTORIES_DATA / _DOWNLOADS / _USER environment variables. Without the binary, or
without the board's core, compile() returns a result whose `available` is False and whose message says how to install it; the
checks turn that into a failed check, never a crashed build.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

TIMEOUT_S = 300.0
CLI_PLACES = ("/opt/homebrew/bin/arduino-cli", "/usr/local/bin/arduino-cli")   # Homebrew's, besides ~/.local/bin
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


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def cli_env() -> dict[str, str]:
    """The environment for arduino-cli: ours, with TMPDIR set when it is unset or empty. Without it `arduino-cli compile`
    exits 1 with no compiler error at all ("Error during build: exit status 1"; seen 2026-11-18 in a stripped agent
    environment). A normal login always has TMPDIR; agents and CI often do not."""
    env = dict(os.environ)
    if not env.get("TMPDIR"):
        env["TMPDIR"] = tempfile.gettempdir()
    return env


def _plain_output(log: str, keep: int = 5) -> str:
    """arduino-cli's output without colour codes and without its "Used platform" / "Used library" table: the last lines."""
    lines, in_table = [], False
    for ln in _ANSI.sub("", log).splitlines():
        if re.match(r"\s*Used (platform|library)\b", ln):
            in_table = True
            continue
        if in_table and re.match(r"\s*\S+\s+\S+\s+(/|[A-Za-z]:\\)", ln):     # a row: id, version, path
            continue
        in_table = False
        if ln.strip():
            lines.append(ln.strip())
    text = " / ".join(lines[-keep:])
    return text[-600:] if text else "(no output)"


def find_cli() -> str | None:
    env = os.environ.get("EITRI_ARDUINO_CLI")
    if env:
        return env if Path(env).is_file() and os.access(env, os.X_OK) else None
    found = shutil.which("arduino-cli")
    if found:
        return found
    # where `eitri setup firmware` (and Homebrew) put it, for an eitri started without them on PATH
    for cand in [Path.home() / ".local" / "bin" / "arduino-cli"] + [Path(c) for c in CLI_PLACES]:
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


# measured 2026-11-18 on a clean HOME (reports/2026-11-18-agent-setup-firmware.md): arduino-cli + arduino:avr + Servo is about
# 65 MB to download and 450 MB on disk, 1 to 2 minutes; the ESP32 core is about 1.2 GB to download (Espressif's index, 3.3.13)
SETUP_WHAT = {"avr": "arduino-cli and the Arduino AVR board core, about 65 MB to download, 1 to 2 minutes",
              "esp32": "arduino-cli and the ESP32 board core, about 1.2 GB to download, often over 10 minutes"}


def _eitri() -> str:
    """`eitri`, or its full path when it is not on PATH (the plugin's server may run ~/.local/bin/eitri from an agent whose
    PATH lacks it): the command in the message must run as written in the agent's shell."""
    if shutil.which("eitri"):
        return "eitri"
    for cand in (Path(sys.executable).parent / "eitri", Path.home() / ".local" / "bin" / "eitri"):
        if cand.is_file() and os.access(cand, os.X_OK):
            return shlex.quote(str(cand))
    return "eitri"


def setup_command(core: str) -> str:
    return f"{_eitri()} setup firmware -y" + (" --esp32" if core.startswith("esp32") else "")


def install_hint(core: str) -> str:
    """What a failed compile check says when arduino-cli or the core is missing: read by an agent and by a person."""
    esp = core.startswith("esp32")
    what = SETUP_WHAT["esp32" if esp else "avr"]
    return (f"`{setup_command(core)}` installs it ({what}; with Homebrew if the Mac has it, else Arduino's official install script into "
            f"~/.local/bin). An agent with a shell may run that command itself: first tell the person in one line what it installs, "
            f"give the command a 10-minute timeout{' (or run it in the background)' if esp else ''}, then rebuild; if it fails, show "
            f"its error and the steps by hand below. By hand: install arduino-cli (https://arduino.github.io/arduino-cli/latest/installation/: "
            f"`curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh`, or `brew install arduino-cli`), then "
            f"`arduino-cli core update-index && arduino-cli core install {core}`" + (" (add the ESP32 package index first: "
            "`arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json`)" if esp else "")
            + "; or point EITRI_ARDUINO_CLI at the binary")


CACHE_FORMAT = 2   # 2 (0.9.2): the key holds the toolchain; a format-1 entry (fqbn + files only) never matches


def _probe_json(cmd: list[str]) -> dict:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=cli_env(), check=False)
        return json.loads(p.stdout) if p.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}


def toolchain(cli: str, core: str) -> str:
    """What the compile result depends on besides the sketch: the arduino-cli binary (path and version) and the installed
    version of the board's core. `eitri setup firmware`, a new arduino-cli or a core update changes it, so a cached result
    from before no longer matches. Two quick probes (about 0.15 s each); unknown parts are '?'."""
    ver = _probe_json([cli, "version", "--json"]).get("VersionString") or "?"
    installed = "?"
    for plat in _probe_json([cli, "core", "list", "--json"]).get("platforms") or []:
        if plat.get("id") == core:
            installed = plat.get("installed_version") or "?"
    return f"{cli}|arduino-cli {ver}|{core} {installed}"


def _key(fqbn: str, sketch_dir: Path, tools: str = "") -> str:
    h = hashlib.sha1(f"eitri-compile-cache/{CACHE_FORMAT}\0{tools}\0{fqbn}".encode())
    for f in sorted(sketch_dir.iterdir()):
        if f.is_file():
            h.update(f.name.encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def _cacheable(r: CompileResult) -> bool:
    """Only a result the sketch decides: it compiled, or the compiler named an error. A failure with no compiler diagnostic
    (an environment or tool fault: no TMPDIR, a broken install) must not outlive its cause."""
    return r.available and (r.ok or bool(r.errors))


def compile_sketch(sketch_dir: Path, fqbn: str, core: str, build_dir: Path, cache_file: Path | None = None, timeout: float = TIMEOUT_S) -> CompileResult:
    cli = find_cli()
    if cli is None:
        return CompileResult(False, False, "compiler unavailable: arduino-cli was not found; " + install_hint(core))
    key = _key(fqbn, sketch_dir, toolchain(cli, core)) if cache_file is not None else ""
    if cache_file is not None and cache_file.is_file():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data.get("key") == key:
                r = CompileResult(**data["result"])
                if _cacheable(r):                 # never serve a tool failure, whoever wrote it
                    r.cached = True
                    return r
        except (OSError, ValueError, TypeError):
            pass
    cmd = [cli, "compile", "--fqbn", fqbn, "--build-path", str(build_dir), "--warnings", "default", str(sketch_dir)]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              env=cli_env())
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
    no_diagnostic = proc.returncode != 0 and not errors
    if no_diagnostic:
        # not a compile error in the sketch: say what arduino-cli printed, not "1 error(s)" made of its platform table
        r.message = f"arduino-cli failed without a compiler error (exit {proc.returncode}); output: {_plain_output(log)}"
    m, n = _SIZE.search(log), _RAM.search(log)
    if m:
        r.flash_bytes, r.flash_pct = int(m.group(1)), int(m.group(2))
    if n:
        r.ram_bytes, r.ram_pct = int(n.group(1)), int(n.group(2))
    if cache_file is not None and not _cacheable(r):
        cache_file.unlink(missing_ok=True)             # drop an older entry too: the next build compiles afresh
    elif cache_file is not None:
        try:
            d = dict(r.__dict__)
            d.pop("cached", None)
            cache_file.write_text(json.dumps({"key": key, "result": d}), encoding="utf-8")
        except OSError:
            pass
    return r
