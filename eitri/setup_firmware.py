"""`eitri setup firmware`: install and configure arduino-cli for the firmware domain.

These are the README's manual steps, run for you: install arduino-cli if it is missing (Homebrew on a Mac
that has it, else Arduino's official install script into ~/.local/bin), update the board index, install the
arduino:avr core (Uno, Nano) and the Servo library, and with --esp32 add Espressif's board index and install
esp32:esp32 and ESP32Servo. Steps already done are skipped. Then a one-line test sketch is compiled for each
board family, so you know the firmware check will work.

Installing arduino-cli itself always asks first (or needs --yes). --dry-run prints every command and runs
nothing that changes the machine (it only asks the existing arduino-cli what is already installed).
eitri never uploads to a board; neither does this.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

ESP32_INDEX = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"
ARDUINO_INSTALL_SH = "https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh"

Runner = Callable[[list[str], bool], "tuple[int, str]"]


def _run(cmd: list[str], capture: bool) -> tuple[int, str]:
    """capture=True: a read-only probe, output returned. capture=False: a step, output shown as it runs."""
    try:
        if capture:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        return subprocess.call(cmd), ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"{type(exc).__name__}: {exc}"


def _ask(question: str) -> bool:
    """Ask on the terminal. Without a terminal the answer is no (use --yes)."""
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            tty.write(f"{question} [y/N] ")
            tty.flush()
            return tty.readline().strip().lower() in ("y", "yes")
    except OSError:
        return False


def _first_column(listing: str) -> set[str]:
    """IDs from `arduino-cli core list` / `lib list` text output (the first column; the header row is dropped)."""
    out = set()
    for line in listing.splitlines()[1:]:
        tok = line.split()
        if tok:
            out.add(tok[0])
    return out


def install_command() -> tuple[list[str] | None, str]:
    """How to install arduino-cli here: (command, description) or (None, why not)."""
    brew = shutil.which("brew") or next((b for b in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if Path(b).is_file()), None)
    if sys.platform == "darwin" and brew:
        return [brew, "install", "arduino-cli"], "Homebrew"
    if shutil.which("curl") and shutil.which("sh"):
        bindir = Path.home() / ".local" / "bin"
        return (["sh", "-c", f'mkdir -p "{bindir}" && curl -fsSL {ARDUINO_INSTALL_SH} | BINDIR="{bindir}" sh'],
                f"Arduino's install script, into {bindir}")
    return None, ("neither Homebrew nor curl was found; install arduino-cli by hand "
                  "(https://arduino.github.io/arduino-cli/latest/installation/) and run `eitri setup firmware` again")


def _test_compile(cli: str, fqbn: str, core: str) -> tuple[bool, str]:
    from .firmware.compile import compile_sketch
    with tempfile.TemporaryDirectory(prefix="eitri-setup-") as tmp:
        sketch = Path(tmp) / "eitri_setup_test"
        sketch.mkdir()
        (sketch / "eitri_setup_test.ino").write_text("void setup() { pinMode(2, OUTPUT); }\nvoid loop() { digitalWrite(2, HIGH); }\n",
                                                     encoding="utf-8")
        old = os.environ.get("EITRI_ARDUINO_CLI")
        os.environ["EITRI_ARDUINO_CLI"] = cli
        try:
            r = compile_sketch(sketch, fqbn, core, Path(tmp) / "build")
        finally:
            if old is None:
                os.environ.pop("EITRI_ARDUINO_CLI", None)
            else:
                os.environ["EITRI_ARDUINO_CLI"] = old
        if r.ok:
            return True, f"compiled a test sketch for {fqbn} in {r.seconds:g} s"
        return False, f"the test sketch for {fqbn} did not compile: {r.message}\n{r.log[-1500:]}"


def run_setup_firmware(esp32: bool = False, dry_run: bool = False, yes: bool = False, servo: bool = True,
                       run: Runner = _run, ask: Callable[[str], bool] = _ask, verify: bool = True) -> int:
    from .firmware.compile import find_cli

    def say(msg: str) -> None:
        print(msg, flush=True)

    def shown(cmd: list[str]) -> str:
        return " ".join(["arduino-cli" if c == cli else c for c in cmd[:1]] + cmd[1:])

    def step(cmd: list[str], why: str) -> bool:
        say(f"==> {why}: {shown(cmd)}" if not dry_run else f"would run ({why}): {shown(cmd)}")
        if dry_run:
            return True
        rc, out = run(cmd, False)
        if rc != 0:
            say(f"FAIL: `{shown(cmd)}` exited {rc}" + (f"\n{out.strip()}" if out.strip() else ""))
            return False
        return True

    say("eitri setup firmware" + (" (dry run: nothing is installed or changed)" if dry_run else ""))
    cli = find_cli()
    if cli is None and os.environ.get("EITRI_ARDUINO_CLI"):
        say(f"FAIL: EITRI_ARDUINO_CLI is set to {os.environ['EITRI_ARDUINO_CLI']}, which is not an executable file; "
            "fix it or unset it")
        return 1
    if cli is None:
        cmd, how = install_command()
        if cmd is None:
            say(f"FAIL: arduino-cli is not installed and {how}")
            return 1
        say(f"arduino-cli is not installed; installing it with {how}"
            + ("" if dry_run else f":\n    {' '.join(cmd)}"))
        if not dry_run:
            if not (yes or ask("Install arduino-cli now?")):
                say("not installed. Run that command yourself, or `eitri setup firmware --yes`, then run this again.")
                return 1
            if not step(cmd, "installing arduino-cli"):
                return 1
            cli = find_cli()
            if cli is None:
                say("FAIL: arduino-cli was installed but eitri cannot find it; set EITRI_ARDUINO_CLI to its full path")
                return 1
        else:
            step(cmd, "installing arduino-cli")
    if cli:
        rc, out = run([cli, "version"], True)
        say(f"ok   arduino-cli: {out.strip().splitlines()[0] if rc == 0 and out.strip() else '?'} ({cli})")
    cli_name = cli or "arduino-cli"

    cores: set[str] = set()
    libs: set[str] = set()
    if cli:
        rc, out = run([cli, "core", "list"], True)
        cores = _first_column(out) if rc == 0 else set()
        rc, out = run([cli, "lib", "list"], True)
        libs = _first_column(out) if rc == 0 else set()

    if esp32:
        urls = ""
        if cli:
            rc, urls = run([cli, "config", "get", "board_manager.additional_urls"], True)
        if ESP32_INDEX in urls:
            say("ok   ESP32 board index: already configured")
        else:
            # `config init` writes arduino-cli.yaml once; when the file exists it refuses, which is fine
            say(("would run" if dry_run else "==>") + " (create arduino-cli.yaml if missing): arduino-cli config init")
            if not dry_run:
                run([cli_name, "config", "init"], True)
            if not step([cli_name, "config", "add", "board_manager.additional_urls", ESP32_INDEX], "adding the ESP32 board index"):
                return 1

    if not step([cli_name, "core", "update-index"], "updating the board index"):
        return 1
    wanted = [("arduino:avr", "the Arduino AVR core (Uno, Nano)")] + ([("esp32:esp32", "the ESP32 core")] if esp32 else [])
    for core, label in wanted:
        if core in cores:
            say(f"ok   {core}: already installed")
        elif not step([cli_name, "core", "install", core], f"installing {label}"):
            return 1
    if servo:
        for lib in ["Servo"] + (["ESP32Servo"] if esp32 else []):
            if lib in libs:
                say(f"ok   library {lib}: already installed")
            elif not step([cli_name, "lib", "install", lib], f"installing the {lib} library"):
                return 1

    if dry_run:
        say("dry run done: nothing was changed. Run `eitri setup firmware` (without --dry-run) to do it.")
        return 0
    if verify and cli:
        tests = [("arduino:avr:uno", "arduino:avr")] + ([("esp32:esp32:esp32", "esp32:esp32")] if esp32 else [])
        for fqbn, core in tests:
            ok, msg = _test_compile(cli, fqbn, core)
            say(("ok   " if ok else "FAIL ") + msg)
            if not ok:
                return 1
    on_path = shutil.which("arduino-cli") == cli
    say("firmware is ready: firmware builds now compile their sketch with arduino-cli"
        + ("" if on_path or os.environ.get("EITRI_ARDUINO_CLI") else f" (eitri finds it at {cli}; add its folder to PATH to use it yourself)"))
    return 0
