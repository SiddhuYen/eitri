"""What the agent's `inspect` tool has in scope for a firmware project.

    fw             the built Firmware (also `parts`); p is PARAMS
    pins()         the pinout resolved on the board, with what the sources do with each pin
    board()        what the target board's pins can do
    sources()      the source files with line counts; header() the generated eitri_pins.h
    compile_log()  the last compile's output (from out/), if any
    board_info(key) the kernel's board() lookup (shadowed by the helper above)
"""
from __future__ import annotations

from pathlib import Path

from .checks import scan_calls
from .model import Firmware


def firmware_scope(fw: Firmware, p=None, out_dir: Path | None = None) -> dict:
    import eitri.firmware as F
    from .report import analyze

    def pins():
        rep = analyze(fw)
        for i in rep.pins:
            print(f"{i.symbol} = {i.canonical or i.pin}" + (f" (#{i.number})" if i.number is not None else " (not on this board)") + f": {i.direction} {i.role}"
                  + (f", {i.peripheral}" if i.peripheral else "") + f"; uses: {', '.join(i.uses) or 'none'}; {i.status}")

    def board():
        b = fw.board
        if b is None:
            print(f"unknown board {fw.board_key!r}")
            return
        print(f"{b.name} ({b.fqbn}), core {b.core}")
        for c in b.pins.values():
            caps = [k for k in ("pwm", "analog_in", "dac", "interrupt") if getattr(c, k)] + ([] if c.digital_out else ["input-only"])
            print(f"  {c.name} #{c.number}: {', '.join(caps) or 'digital'}" + (f"  FORBIDDEN: {c.forbidden}" if c.forbidden else "") + (f"  ({'; '.join(c.notes)})" if c.notes else ""))

    def sources():
        for s in fw.sources:
            print(f"{s.name}: {len(s.code.splitlines())} lines")
        for c in scan_calls(fw):
            print(f"  {c['file']}:{c['line']}: {c['fn']}({c['arg']}{', ' + c['mode'] if c['mode'] else ''})")

    def header():
        print(fw.pins_header())

    def compile_log():
        logs = sorted(Path(out_dir or "out").glob("*.compile.log"), key=lambda q: q.stat().st_mtime)
        print(logs[-1].read_text(encoding="utf-8", errors="replace")[-20000:] if logs else "no compile log yet (build first)")

    return {**{k: getattr(F, k) for k in F.__all__},                     # the kernel's names first: the helpers below win (board())
            "fw": fw, "parts": fw, "p": p, "pins": pins, "board": board, "sources": sources, "header": header, "compile_log": compile_log,
            "scan_calls": scan_calls, "board_info": F.board}
