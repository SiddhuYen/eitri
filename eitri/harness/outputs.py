"""What a harness build hands to the shop: the BOM, the cut list and a WireViz-compatible YAML export.

The YAML is interop only (open it in WireViz if you like); nothing here depends on WireViz.
"""
from __future__ import annotations

import csv
import io
from collections import OrderedDict

from .catalog import BOARD_TYPES, color_name, gauge_awg, housing_pn, terminal_pn
from .model import Harness, Link, PinRef, SpliceRef

SERVICE_LOOP = 0.10          # extra wire bought on top of the cut lengths


# --------------------------------------------------------------------------- BOM


def bom(h: Harness) -> list[dict]:
    """Rows: item, category, description, qty, unit, part_number, notes."""
    rows: list[dict] = []

    def add(category: str, description: str, qty, unit: str = "pc", part_number: str = "", notes: str = "") -> None:
        rows.append({"category": category, "description": description, "qty": qty, "unit": unit, "part_number": part_number, "notes": notes})

    # connectors, grouped by what they are
    groups: OrderedDict[tuple, list] = OrderedDict()
    for c in h.connectors.values():
        pn = c.part_number or (housing_pn(c.spec, c.pins, c.gender) if c.spec else "")
        groups.setdefault((c.type, c.pins, c.gender, pn), []).append(c)
    for (typ, pins, gender, pn), cs in groups.items():
        spec = cs[0].spec
        desc = (spec.family if spec and spec.rating_a is None and spec.key in BOARD_TYPES else
                f"{typ} connector, {pins} pos" if pins > 1 else f"{typ} terminal")
        if gender:
            desc += f", {gender}"
        add("connector", desc, len(cs), part_number=pn, notes=", ".join(c.name for c in cs))

    # crimp terminals: one per populated pin of a crimp connector (a ring/spade connector is its own terminal)
    terminals: OrderedDict[str, OrderedDict] = OrderedDict()
    solder: dict[str, int] = {}
    shield_pins = {p for cab in h.cables.values() for p in cab.shield_ends}
    for c in h.connectors.values():
        populated = [p for p in c.pin_names if h.links_at(PinRef(c.name, p)) or PinRef(c.name, p) in shield_pins]
        if not populated:
            continue
        if c.termination == "crimp":                 # the terminal follows the wire where the family has one per size
            for p in populated:
                links = h.links_at(PinRef(c.name, p))
                awg = gauge_awg(links[0].gauge) if links else None
                pn = c.terminal_pn or (terminal_pn(c.spec, c.gender, awg) if c.spec else "") or "crimp contact (match the housing)"
                per = terminals.setdefault(pn, OrderedDict())
                per[c.name] = per.get(c.name, 0) + 1
        elif c.termination == "solder":
            solder[c.type] = solder.get(c.type, 0) + len(populated)
    for pn, items in terminals.items():
        add("terminal", "crimp terminal", sum(items.values()), part_number=pn, notes="; ".join(f"{c} x{n}" for c, n in items.items()))
    for typ, n in solder.items():
        add("termination", f"solder cups, {typ} (solder + heat shrink)", n)

    # wire: by gauge / colour, the total of the cut lengths + a service loop
    cable_links = {l.id for l in h.links if l.cable}
    wires: OrderedDict[tuple, list[Link]] = OrderedDict()
    for l in h.links:
        if l.id not in cable_links:
            wires.setdefault((l.gauge.key, l.color, l.insulation), []).append(l)
    for (_k, color, insulation), ls in wires.items():
        total = sum(l.length_mm for l in ls)
        add("wire", f"{ls[0].gauge.label} {color_name(color)} ({color}){' ' + insulation if insulation else ''} hookup wire",
            round(total * (1 + SERVICE_LOOP) / 1000, 3), "m", notes=f"{len(ls)} wire(s), {total / 1000:.3f} m cut + {int(SERVICE_LOOP * 100)}% service loop")
    for cab in h.cables.values():
        cores = [l for l in h.links if l.cable == cab.name]
        gauges = sorted({l.gauge.label for l in cores})
        add("cable", f"{len(cab.wires)}-core cable {'/'.join(gauges)}, {color_name(cab.jacket_color)} jacket{', shielded' if cab.shield else ''} ({cab.name})",
            round(cab.length_mm * (1 + SERVICE_LOOP) / 1000, 3), "m", part_number=cab.part_number,
            notes=f"{cab.length_mm / 1000:.3f} m cut + {int(SERVICE_LOOP * 100)}% service loop")

    # splices
    sp: OrderedDict[tuple, list] = OrderedDict()
    for s in h.splices.values():
        sp.setdefault((s.kind, len(h.links_at(s.ref))), []).append(s)
    for (kind, n), ss in sp.items():
        add("splice", f"{kind} splice, {n} wires", len(ss), notes=", ".join(s.name for s in ss))

    # labels: one marker per wire end
    if h.links:
        add("label", "wire marker (heat-shrink or flag label), one per wire end", 2 * len(h.links), notes="W-numbers as in the cut list")
    for i, r in enumerate(rows, 1):
        r["item"] = i
    return rows


_BOM_COLS = ["item", "category", "description", "qty", "unit", "part_number", "notes"]


def bom_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, _BOM_COLS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in _BOM_COLS})
    return buf.getvalue()


def _md_table(headers: list[str], rows: list[list]) -> str:
    esc = lambda x: str(x).replace("|", "\\|")
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def bom_markdown(h: Harness, rows: list[dict] | None = None) -> str:
    rows = rows if rows is not None else bom(h)
    table = _md_table(["#", "category", "description", "qty", "unit", "part number", "notes"],
                      [[r["item"], r["category"], r["description"], r["qty"], r["unit"], r["part_number"], r["notes"]] for r in rows])
    return f"# Bill of materials: {h.name}\n\n{table}\n"


# --------------------------------------------------------------------------- cut list


def _termination(h: Harness, e, gauge=None) -> tuple[str, float]:
    """(how this end is terminated, the insulation to strip in mm)."""
    if isinstance(e, SpliceRef):
        s = h.splices[e.splice]
        return f"{s.kind} splice {s.name}", 6.0
    c = h.connectors[e.connector]
    spec = c.spec
    strip = spec.strip_mm if spec else 5.0
    t = c.termination
    if t == "crimp":
        pn = c.terminal_pn or (terminal_pn(spec, c.gender, gauge_awg(gauge) if gauge is not None else None) if spec else "")
        return f"crimp {pn or 'contact'} into {c.name} pos {e.pin}", strip
    if t == "solder":
        return f"solder to {c.name} {e.pin} cup", strip
    if t == "ring":
        return f"{c.type.lower()} terminal on {c.name}" + (f" ({c.notes})" if c.notes else ""), strip
    if t == "spade":
        return f"{c.type.lower()} terminal on {c.name} {e.pin}", strip
    return f"bare end to {c.name} {e.pin}", strip


def cutlist(h: Harness) -> list[dict]:
    rows = []
    for l in h.links:
        ta, sa = _termination(h, l.a, l.gauge)
        tb, sb = _termination(h, l.b, l.gauge)
        rows.append({"id": l.id, "from": str(l.a), "to": str(l.b), "gauge": l.gauge.label, "color": l.color, "net": l.net or "",
                     "cut_mm": round(l.length_mm, 1), "strip_a_mm": sa, "strip_b_mm": sb, "term_a": ta, "term_b": tb,
                     "cable": f"{l.cable} core {l.core}" if l.cable else "", "label": l.label or l.id})
    return rows


_CUT_COLS = ["id", "from", "to", "gauge", "color", "net", "cut_mm", "strip_a_mm", "strip_b_mm", "term_a", "term_b", "cable", "label"]


def cutlist_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, _CUT_COLS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in _CUT_COLS})
    return buf.getvalue()


def cutlist_markdown(h: Harness, rows: list[dict] | None = None) -> str:
    rows = rows if rows is not None else cutlist(h)
    table = _md_table(["wire", "from", "to", "gauge", "colour", "net", "cut (mm)", "strip A / B (mm)", "termination A", "termination B", "cable"],
                      [[r["id"], r["from"], r["to"], r["gauge"], r["color"], r["net"], f"{r['cut_mm']:g}", f"{r['strip_a_mm']:g} / {r['strip_b_mm']:g}",
                        r["term_a"], r["term_b"], r["cable"]] for r in rows])
    return f"# Cut list: {h.name}\n\nCut length is end to end, strip lengths included.\n\n{table}\n"


# --------------------------------------------------------------------------- WireViz YAML


def _scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(round(v, 6)) if isinstance(v, float) else str(v)
    s = str(v)
    plain = s and all(ch.isalnum() or ch in " ._-/" for ch in s) and s.strip() == s and s[0] not in "-?:" \
        and s.lower() not in ("true", "false", "null", "yes", "no", "on", "off", "~") and not _looks_numeric(s)
    return s if plain else '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _looks_numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _flow(seq) -> str:
    return "[" + ", ".join(_scalar(x) for x in seq) + "]"


def to_yaml(obj, indent: int = 0) -> str:
    """The small YAML subset WireViz files use: block mappings, block sequences, flow lists and scalars."""
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = _scalar(k)
            if isinstance(v, dict):
                lines.append(f"{pad}{key}:")
                lines.append(to_yaml(v, indent + 1))
            elif isinstance(v, (list, tuple)) and all(not isinstance(x, (dict, list, tuple)) for x in v):
                lines.append(f"{pad}{key}: {_flow(v)}")
            elif isinstance(v, (list, tuple)):
                lines.append(f"{pad}{key}:")
                lines.append(to_yaml(v, indent + 1))
            else:
                lines.append(f"{pad}{key}: {_scalar(v)}")
    else:
        for item in obj:
            if isinstance(item, dict) and len(item) == 1 and all(isinstance(x, (list, tuple)) and all(not isinstance(y, (dict, list)) for y in x) for x in item.values()):
                (k, v), = item.items()
                lines.append(f"{pad}- {_scalar(k)}: {_flow(v)}")
            elif isinstance(item, (list, tuple)):
                lines.append(f"{pad}-")
                lines.append(to_yaml(item, indent + 1))
            elif isinstance(item, dict):
                lines.append(f"{pad}-")
                lines.append(to_yaml(item, indent + 1))
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    return "\n".join(lines)


def _wv_color(code: str) -> str:
    return code.replace("/", "")


def wireviz(h: Harness) -> dict:
    """The harness as a WireViz document (connectors, cables, connections). Splices become one-pin connectors; every
    wire becomes a one-wire bundle, and each Cable one cable when its cores share two connectors."""
    connectors: dict = {}
    for c in h.connectors.values():
        d: dict = {"type": c.type + (f" {c.pins}-pin" if c.pins > 1 else "")}
        if c.gender in ("male", "female"):
            d["subtype"] = c.gender
        d["pincount"] = c.pins
        d["pins"] = list(c.pin_names)
        if c.pin_labels:
            d["pinlabels"] = [c.pin_labels.get(p, "") for p in c.pin_names]
        if c.part_number:
            d["mpn"] = c.part_number
        if c.notes:
            d["notes"] = c.notes
        connectors[c.name] = d
    for s in h.splices.values():
        connectors[s.name] = {"type": f"{s.kind} splice", "pincount": 1, "pins": [1]}
    cables: dict = {}
    connections: list = []

    def end(e):
        return (e.connector, e.pin) if isinstance(e, PinRef) else (e.splice, 1)

    done: set[str] = set()
    for cab in h.cables.values():
        cores = [l for l in h.links if l.cable == cab.name]
        if not cores:
            continue
        ca, cb = {end(l.a)[0] for l in cores}, {end(l.b)[0] for l in cores}
        if len(ca) == 1 and len(cb) == 1:
            d = {"wirecount": len(cores), "gauge": cores[0].gauge.label.replace("²", "2"), "length": round(cab.length_mm / 1000, 4),
                 "colors": [_wv_color(l.color) for l in cores], "shield": bool(cab.shield)}
            if cab.part_number:
                d["mpn"] = cab.part_number
            cables[cab.name] = d
            connections.append([{next(iter(ca)): [end(l.a)[1] for l in cores]}, {cab.name: list(range(1, len(cores) + 1))}, {next(iter(cb)): [end(l.b)[1] for l in cores]}])
            done.update(l.id for l in cores)
    for l in h.links:
        if l.id in done:
            continue
        cables[l.id] = {"category": "bundle", "wirecount": 1, "gauge": l.gauge.label.replace("²", "2"), "length": round(l.length_mm / 1000, 4),
                        "colors": [_wv_color(l.color)]}
        if l.label:
            cables[l.id]["notes"] = l.label
        (ac, ap), (bc, bp) = end(l.a), end(l.b)
        connections.append([{ac: [ap]}, {l.id: [1]}, {bc: [bp]}])
    doc: dict = {"connectors": connectors}
    if cables:
        doc["cables"] = cables
    if connections:
        doc["connections"] = connections
    return doc


def wireviz_yaml(h: Harness) -> str:
    doc = wireviz(h)
    head = f"# {h.name}: exported by eitri for WireViz (interop only; eitri does not use WireViz)\n"
    parts = [to_yaml({"connectors": doc["connectors"]})]
    if "cables" in doc:
        parts.append(to_yaml({"cables": doc["cables"]}))
    if "connections" in doc:
        lines = ["connections:"]
        for trio in doc["connections"]:
            lines.append("  -")
            for ep in trio:
                (k, v), = ep.items()
                lines.append(f"    - {_scalar(k)}: {_flow(v)}")
        parts.append("\n".join(lines))
    return head + "\n".join(parts) + "\n"
