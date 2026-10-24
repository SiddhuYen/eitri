"""The firmware's pinout table (Markdown and CSV)."""
from __future__ import annotations

import csv
import io

from .model import Firmware
from .report import FirmwareReport

_COLS = ["pin", "number", "symbol", "direction", "role", "net", "peripheral", "pullup", "uses", "status"]


def pinout_rows(fw: Firmware, rep: FirmwareReport) -> list[dict]:
    info = {p.net: p for p in rep.pins}
    rows = []
    for d in fw.pins:
        i = info.get(d.net)
        rows.append({"pin": (i.canonical if i and i.canonical else d.pin), "number": i.number if i else None, "symbol": d.symbol, "direction": d.direction, "role": d.role,
                     "net": d.net, "peripheral": d.peripheral, "pullup": d.pullup, "uses": "; ".join(i.uses) if i else "", "status": i.status if i else ""})
    return rows


def pinout_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, _COLS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in _COLS})
    return buf.getvalue()


def pinout_markdown(fw: Firmware, rep: FirmwareReport) -> str:
    esc = lambda x: str(x).replace("|", "\\|")
    rows = pinout_rows(fw, rep)
    head = "| pin | # | constant | dir | role | peripheral | used in the sources | status |\n|---|---|---|---|---|---|---|---|\n"
    body = "\n".join(f"| {esc(r['pin'])} | {'' if r['number'] is None else r['number']} | {r['symbol']} | {r['direction']} | {r['role']} | {esc(r['peripheral'])} | {esc(r['uses'])} | {r['status']} |" for r in rows)
    b = fw.board
    c = rep.compile
    comp = "" if not c else ("\nCompile: " + (c["message"] if not c.get("available") else "ok" if c.get("ok") else "FAILED") + (f", flash {c['flash_pct']}%, RAM {c['ram_pct']}%" if c.get("flash_pct") is not None else "") + "\n")
    return f"# Pinout: {fw.name}\n\nBoard: {b.name + ' (' + b.fqbn + ')' if b else fw.board_key}\n\n{head}{body}\n{comp}"
