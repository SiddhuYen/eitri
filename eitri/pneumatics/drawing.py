"""The circuit diagram a customer receives: the ISO 1219-1 style schematic with a device tag on every component, the position
sensors on their cylinders, the coil signals on the valves, a parts table keyed by tag and a title block. SVG and PDF.

Designations. ISO 1219-2:2012 asks for a component identification code; current manufacturer references use the EN 81346-2 class
codes for it. We follow the Festo Didactic workbook TP 101 "Pneumatics, basic level" (order no. 541088, revision level 04/2016,
p. XXIII "Reference designations of the devices": "in compliance with EN 81346-2:2010-05 ... all reference designations begin
with a dash ... If several devices within a circuit have the same code, consecutive numbers are assigned"; "Cylinders: -MM1,
-MM2 ... Valves: -QM1, -QM2, -KH1, -KH2, -RM1, -RZ1 ... Sensors: -BG1, -BG2 ... Accessories: -AZ1, -AZ2, -XM1, -XM2, -PG1"; its
equipment lists name "-AZ1 On-off valve with filter regulating valve" and "-XM1 Distributor block").

What we follow:  -MM cylinders, -QM directional valves, -BG position sensors, -AZ the air-preparation (filter-regulator) unit,
                 -XM a tee / distributor; numbered consecutively per code; every tag starts with a dash; ports keep the ISO 5599
                 numbers' letters (P, A, B, R, S) and coils their pilot numbers 14 / 12.
What we don't:   the workbook does not define a class code for one-way flow controls; we use -RZ (as on the workbook's own cover
                 circuit, where -RZ1 / -RZ2 sit in the cylinder lines), which is an inference. Silencers get no tag of their own:
                 they are named by the valve port they are screwed into (-QM1:R), the EN 81346 port-designation style. The air
                 supply is the drawing's interface, not a device: it is labelled AIR without a tag. No function (=) or location (+)
                 aspects, no IEC 81346-1 multi-level structure; the older ISO 1219-2:1995 numbering (1A1, 1V1, 0Z1) is not used.
"""
from __future__ import annotations

import datetime as _dt
from collections import OrderedDict

from ..diagram import INK, MUTED, Scene, scene_to_pdf, scene_to_svg, text_w
from .model import Circuit

CODE = {"cylinder": "MM", "valve": "QM", "frl": "AZ", "regulator": "AZ", "node": "XM", "flow_control": "RZ"}
DESIGNATION_NOTE = ("Designations: EN 81346-2 class codes as used by Festo Didactic (TP 101 workbook 541088, p. XXIII): -MM cylinder, "
                    "-QM directional valve, -BG sensor, -AZ air preparation, -XM distributor; -RZ flow control (inferred); silencers by valve port.")
LINE = "#2b3a4a"


def tags(c: Circuit) -> dict[str, str]:
    """{component or sensor name: tag}. Cylinders in name order; each cylinder's valves, flow controls and sensors follow it, so a
    chain's devices number together; shared parts (air preparation, tees) in name order."""
    from .checks import find_path
    out: dict[str, str] = {}
    n: dict[str, int] = {}

    def give(name, code):
        if name in out:
            return
        n[code] = n.get(code, 0) + 1
        out[name] = f"-{code}{n[code]}"

    for cyl in sorted(c.cylinders, key=lambda x: x.name):
        give(cyl.name, "MM")
    for cyl in sorted(c.cylinders, key=lambda x: x.name):
        chain = []
        for port in cyl.ports:
            for goal in ("supply", "exhaust"):
                r = find_path(c, cyl.port(port), goal)
                chain += [s.name for s in r.steps if s.kind == "component" and s.name not in chain]
        for name in chain:
            k = c.components[name].kind
            if k in ("valve", "flow_control"):
                give(name, CODE[k])
        for s in sorted(c.sensors.values(), key=lambda s: (s.cylinder != cyl.name, s.position != "retracted", s.name)):
            if s.cylinder == cyl.name:
                give(s.name, "BG")
    for comp in sorted(c.components.values(), key=lambda x: x.name):
        if comp.kind in CODE:
            give(comp.name, CODE[comp.kind])
    for port, sref in c.attached.items():                      # silencers: by the port they sit on
        if port.component in out:
            out[sref.component] = f"{out[port.component]}:{port.port}"
    return out


def _decorate(c: Circuit, tg: dict[str, str]):
    def deco(sc: Scene, pos: dict) -> None:
        items = sc.items
        for s in c.sensors.values():
            if s.cylinder not in pos:
                continue
            cx, cy = pos[s.cylinder]
            x = cx - 30 if s.position == "retracted" else cx + 26
            top = cy - 14
            g = f"sensor:{s.name}"
            items.append({"t": "line", "x1": x, "y1": top, "x2": x, "y2": top - 10, "stroke": LINE, "sw": 1.0, "dash": "2 2", "g": g, "cls": "sym"})
            items.append({"t": "rect", "x": x - 6, "y": top - 18, "w": 12, "h": 8, "rx": 0, "fill": "#ffffff", "stroke": LINE, "sw": 1.2, "g": g, "cls": "sym"})
            items.append({"t": "line", "x1": x - 3, "y1": top - 14, "x2": x + 3, "y2": top - 14, "stroke": LINE, "sw": 1.0, "g": g, "cls": "sym"})
            label = f"{tg.get(s.name, s.name)} {s.signal}"
            items.append({"t": "text", "x": x, "y": top - 22, "s": label, "size": 8, "fill": INK, "anchor": "middle", "g": g, "cls": "label",
                          "bg": "#ffffff", "cands": [(x, top - 22, "middle"), (x, top - 32, "middle"), (x - 8, top - 22, "end"), (x + 8, top - 22, "start"),
                                                     (x, top - 42, "middle")]})
        for v in c.components.values():
            if v.kind != "valve" or v.name not in pos or not v.coils:
                continue
            cx, cy = pos[v.name]
            n = len(v.positions)
            left, right = cx - n * 22 - 18, cx + n * 22 + 18
            for pilot, signal, terminal in v.coils:
                x, anchor = (left - 4, "end") if pilot == "14" else (right + 4, "start")
                label = f"{pilot}: {signal}" + (f" ({terminal})" if terminal else "")
                items.append({"t": "text", "x": x, "y": cy + 4, "s": label, "size": 8, "fill": INK, "anchor": anchor, "g": f"coil:{v.name}:{pilot}",
                              "cls": "label", "bg": "#ffffff", "cands": [(x, cy + 4, anchor), (x, cy + 26, anchor), (x, cy - 20, anchor)]})
    return deco


def _row_text(items, x, y, cells, widths, size=8.5, bold=False, g="table"):
    for (cell, w) in zip(cells, widths):
        s = str(cell)
        while s and text_w(s, size) > w - 6:
            s = s[:-2] + "…" if len(s) > 2 else ""
        items.append({"t": "text", "x": x + 3, "y": y, "s": s, "size": size, "weight": "700" if bold else None, "fill": INK, "g": g})
        x += w


def parts_rows(c: Circuit, tg: dict[str, str]) -> list[list[str]]:
    from .outputs import _desc
    rows = []
    for comp in c.components.values():
        if comp.kind == "supply":
            continue
        rows.append([tg.get(comp.name, ""), comp.name, _desc(comp), comp.part_number])
    for s in c.sensors.values():
        rows.append([tg.get(s.name, ""), s.name, f"position sensor {s.output}, on {tg.get(s.cylinder, s.cylinder)} {s.position}, signal {s.signal}", s.part_number])
    key = lambda r: (r[0] == "", r[0].split(":")[0].lstrip("-").rstrip("0123456789"), int("".join(ch for ch in r[0].split(":")[0] if ch.isdigit()) or 0), r[0])
    return sorted(rows, key=key)


def drawing_scene(c: Circuit, rep=None, today: str | None = None) -> Scene:
    from .diagram import build_scene
    tg = tags(c)
    labels = {n: (f"{t}  {n}" if c.components[n].kind != "supply" else n) for n, t in tg.items() if n in c.components and ":" not in t}
    labels.update({s.name: "AIR supply" for s in c.supplies})
    meta = dict(getattr(c, "meta", {}) or {})
    sc = build_scene(c, None, {}, None, title=meta.get("title") or c.name, labels=labels, decorate=_decorate(c, tg))
    items = sc.items
    from ..diagram import text_box
    left = min([text_box(it)[0] for it in items if it["t"] == "text"] + [28.0])
    if left < 24:                                             # a label placed left of the sheet: move the whole schematic right
        _shift(items, 24 - left)
        sc.width += 24 - left
    # the parts table (left) and the title block (right) under the schematic
    rows = parts_rows(c, tg)
    widths = [62, 62, 330, 190]
    tw = sum(widths)
    bw = 300
    W = max(sc.width, 28 + tw + 24 + bw + 28)
    x0, y0 = 28, sc.height + 16
    rh = 13
    items.append({"t": "text", "x": x0, "y": y0, "s": "Parts", "size": 10, "weight": "700", "fill": INK, "g": "table"})
    y = y0 + 8
    items.append({"t": "rect", "x": x0, "y": y, "w": tw, "h": rh * (len(rows) + 1) + 4, "rx": 0, "fill": "#ffffff", "alpha_fill": 0.0, "stroke": LINE, "sw": 1, "g": "table", "cls": "frame"})
    _row_text(items, x0, y + 11, ["tag", "name", "description", "part number"], widths, bold=True)
    items.append({"t": "line", "x1": x0, "y1": y + rh + 1, "x2": x0 + tw, "y2": y + rh + 1, "stroke": LINE, "sw": 0.8, "g": "table"})
    for i, r in enumerate(rows):
        _row_text(items, x0, y + 11 + rh * (i + 1), r, widths)
    table_bottom = y + rh * (len(rows) + 1) + 4
    # title block
    bx = W - 28 - bw
    n_fail = len(rep.failed) if rep else None
    checks = (f"{len(rep.checks) - len(rep.failed) - len(rep.warnings)} pass, {len(rep.warnings)} warn, {n_fail} fail" if rep else "not run")
    from .. import __version__ as _v
    fields = [("Project", meta.get("project", "")), ("Title", meta.get("title") or c.name), ("Ticket", meta.get("ticket", "")),
              ("Revision", meta.get("revision", "")), ("Date", today or _dt.date.today().isoformat()), ("Drawn by", meta.get("drawn_by") or f"eitri {_v}"),
              ("Approved by", meta.get("approved_by", "")), ("eitri checks", checks), ("Sheet", "1 / 1")]
    by = y
    fh = 17
    items.append({"t": "rect", "x": bx, "y": by, "w": bw, "h": fh * len(fields), "rx": 0, "fill": "#ffffff", "alpha_fill": 0.0, "stroke": INK, "sw": 1.4, "g": "titleblock", "cls": "frame"})
    for i, (k, v) in enumerate(fields):
        yy = by + fh * i
        if i:
            items.append({"t": "line", "x1": bx, "y1": yy, "x2": bx + bw, "y2": yy, "stroke": LINE, "sw": 0.6, "g": "titleblock"})
        items.append({"t": "text", "x": bx + 4, "y": yy + 12, "s": k, "size": 8, "fill": MUTED, "g": "titleblock"})
        items.append({"t": "text", "x": bx + 82, "y": yy + 12, "s": str(v), "size": 9.5, "weight": "700" if k in ("Title", "Ticket", "Revision") else None,
                      "fill": INK, "g": "titleblock"})
    items.append({"t": "line", "x1": bx + 78, "y1": by, "x2": bx + 78, "y2": by + fh * len(fields), "stroke": LINE, "sw": 0.6, "g": "titleblock"})
    bottom = max(table_bottom, by + fh * len(fields)) + 14
    for k, ln in enumerate(("Symbols: ISO 1219-1 style. " + DESIGNATION_NOTE,
                            "Pressures gauge; tube labels give the bore (ID) and length; signal names as in the I/O list. Generated by eitri: check against the parts' datasheets before release.")):
        items.append({"t": "text", "x": x0, "y": bottom + 11 * k, "s": ln, "size": 7.5, "fill": MUTED, "g": "notes"})
    sc.width = W
    sc.height = bottom + 30
    # a frame around the sheet
    items.insert(0, {"t": "rect", "x": 8, "y": 8, "w": W - 16, "h": sc.height - 16, "rx": 0, "fill": "#ffffff", "alpha_fill": 0.0, "stroke": INK, "sw": 1.2,
                     "g": "paper", "cls": "frame"})
    return sc


def _shift(items, dx: float) -> None:
    for it in items:
        for k in ("x", "x1", "x2", "cx"):
            if k in it:
                it[k] += dx
        if "pts" in it:
            it["pts"] = [(x + dx, y) for x, y in it["pts"]]
        for f in it.get("follow", []):
            pass                                              # follow items are also in the list: shifted there


def write_drawing(c: Circuit, rep, out_dir, stem: str) -> dict[str, str]:
    from pathlib import Path
    sc = drawing_scene(c, rep)
    svg = Path(out_dir) / f"{stem}.drawing.svg"
    svg.write_text(scene_to_svg(sc), encoding="utf-8")
    pdf = Path(out_dir) / f"{stem}.drawing.pdf"
    scene_to_pdf(sc, pdf)
    return {"drawing_svg": str(svg), "drawing_pdf": str(pdf)}
