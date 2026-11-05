"""The circuit's shop outputs: the bill of materials, the netlist and the sources the checks used (Markdown and CSV)."""
from __future__ import annotations

import csv
import io

from .model import Circuit, PinRef

BOM_COLS = ["qty", "refs", "kind", "value", "part_number", "description"]
NET_COLS = ["net", "kind", "volts", "pins"]


def bom(c: Circuit) -> list[dict]:
    groups: dict[tuple, list] = {}
    for p in c.parts.values():
        key = (p.kind, p.value_text, p.part_number or p.catalog)
        groups.setdefault(key, []).append(p)
    rows = []
    for (kind, value, pn), ps in groups.items():
        rows.append({"qty": len(ps), "refs": " ".join(p.name for p in ps), "kind": kind, "value": value, "part_number": pn,
                     "description": ps[0].title if ps[0].title != kind else ""})
    order = ["board", "supply", "regulator", "module", "servo", "motor", "solenoid", "relay", "mosfet", "bjt", "diode", "led", "resistor",
             "capacitor", "switch", "load"]
    rows.sort(key=lambda r: (order.index(r["kind"]) if r["kind"] in order else 99, r["refs"]))
    return rows


def _csv(rows: list[dict], cols: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, cols, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    return buf.getvalue()


def bom_csv(rows: list[dict]) -> str:
    return _csv(rows, BOM_COLS)


def bom_markdown(c: Circuit, rows: list[dict]) -> str:
    esc = lambda x: str(x).replace("|", "\\|")
    head = "| qty | refs | kind | value | part number | description |\n|---|---|---|---|---|---|\n"
    body = "\n".join(f"| {r['qty']} | {esc(r['refs'])} | {r['kind']} | {esc(r['value'])} | {esc(r['part_number'])} | {esc(r['description'])} |" for r in rows)
    return f"# Bill of materials: {c.name}\n\n{head}{body}\n\n{sum(r['qty'] for r in rows)} parts. No prices: buy to the part numbers.\n"


def netlist_rows(c: Circuit) -> list[dict]:
    an = getattr(c, "_analysis", None)
    rows = []
    for n in c.nets.values():
        info = an.nets.get(n.name) if an else None
        rows.append({"net": n.name, "kind": info.kind if info else "", "volts": info.volts if info and info.volts is not None else "",
                     "pins": " ".join(str(r) for r in n.pins)})
    return rows


def netlist_csv(c: Circuit) -> str:
    return _csv(netlist_rows(c), NET_COLS)


def netlist_markdown(c: Circuit) -> str:
    rows = netlist_rows(c)
    head = "| net | kind | V | pins |\n|---|---|---|---|\n"
    body = "\n".join(f"| {r['net']} | {r['kind']} | {r['volts']} | {r['pins']} |" for r in rows)
    open_ = [str(PinRef(p.name, n)) for p in c.parts.values() for n in p.pins if PinRef(p.name, n) not in c._net_of]
    return f"# Netlist: {c.name}\n\n{head}{body}\n\nNot connected: {', '.join(open_) or 'none'}\n"


def sources_markdown(c: Circuit, ids: list[str]) -> str:
    from .sources import sources_table
    return (f"# Sources of the numbers the checks used: {c.name}\n\nEvery limit a check compared against, with where it comes from. Values the model file "
            f"gave are its own source. ASSUMPTION marks a value no document could be fetched for.\n\n{sources_table(ids)}\n")
