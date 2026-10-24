"""The harness diagram, drawn by our own layout code (no Graphviz, no WireViz).

    build_scene(harness, ...)  ->  Scene     connectors as pin tables, wires as coloured curves, splices as dots
    scene_to_svg(scene)        ->  str       the editor's interactive drawing (every element carries data-* ids)
    scene_to_png(scene, path)                the agent's review image, rasterised with Pillow (nothing else needed)

Layout: nodes are put in columns by their position along the wires (sources left, loads right), ordered inside a
column to keep wires from crossing, and placed near their neighbours. `Harness.layout` (node name -> [x, y])
overrides the automatic place of a node: these are the editor's drag positions, layout hints and not geometry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from .catalog import color_hex, stripe_hex
from .model import Harness, Link, PinRef, SpliceRef

ROW = 18
HEAD = 38
PAD = 14
FONT = 11
MARGIN = 28
GAP = 250
SPLICE_R = 6
INK = "#1b2430"
MUTED = "#6b7686"
BOX = "#fbfcfd"
HEADER = "#e4e9ef"
BORDER = "#3a4656"
FAIL = "#d92d20"
WARN = "#f08c00"


def text_w(s: str, size: float = FONT) -> float:
    return len(s) * size * 0.58


@dataclass
class Scene:
    width: float
    height: float
    items: list[dict] = field(default_factory=list)
    nodes: dict[str, dict] = field(default_factory=dict)       # name -> {kind, x, y, w, h}
    pins: dict[str, dict] = field(default_factory=dict)       # "CONN.PIN" -> {y, xl, xr}
    wires: dict[str, dict] = field(default_factory=dict)      # id -> {pts, label_at}
    footer_top: float = 0.0


# --------------------------------------------------------------------------- layout


def _node_key(e) -> str:
    return e.connector if isinstance(e, PinRef) else e.splice


def _is_return(h: Harness, l: Link) -> bool:
    n = h.net_of(l)
    return n is not None and not n.round_trip


def _layers(h: Harness, names: list[str]) -> dict[str, int]:
    """Columns from the wires' direction (source -> load); a return net is drawn the same way round, so its wires
    (written load -> source) are reversed here and the battery does not end up on the far side of the loads."""
    succ: dict[str, set[str]] = {}

    def reaches(src: str, dst: str) -> bool:
        seen, todo = set(), [src]
        while todo:
            n = todo.pop()
            if n == dst:
                return True
            if n not in seen:
                seen.add(n)
                todo += succ.get(n, ())
        return False

    edges = set()
    for l in h.links:                  # in the order written; a wire that would close a loop (a return written load -> source) is left out
        a, b = _node_key(l.a), _node_key(l.b)
        if _is_return(h, l):
            a, b = b, a
        if a != b and not reaches(b, a):
            succ.setdefault(a, set()).add(b)
            edges.add((a, b))
    layer = {n: 0 for n in names}
    for _ in range(len(names) + 1):
        changed = False
        for a, b in edges:
            if layer[b] < layer[a] + 1 and layer[a] + 1 <= len(names):
                layer[b] = layer[a] + 1
                changed = True
        if not changed:
            break
    return layer


def _conn_size(h: Harness, c, shield_pins: set) -> tuple[float, float, list[tuple[str, str]]]:
    title = f"{c.name} · {c.type}"
    sub = f"{c.pins}-pin" + (f" {c.gender}" if c.gender else "") + (f" · {c.part_number}" if c.part_number else "")
    rows = []
    for p in c.pin_names:
        label = c.pin_labels.get(p, "")
        if p in c.nc:
            label = (label + " " if label else "") + "NC"
        if PinRef(c.name, p) in shield_pins:
            label = (label + " " if label else "") + "⏚ shield"
        rows.append((p, label))
    w = max([text_w(title, FONT + 1) + 8, text_w(sub, FONT - 1)] + [text_w(f"{p}  {lab}") for p, lab in rows]) + 2 * PAD
    return max(w, 112.0), HEAD + ROW * len(rows) + 6, rows


def build_scene(h: Harness, status: dict[str, str] | None = None, footer: list[tuple[str, str]] | None = None,
                title: str | None = None) -> Scene:
    """status: {wire id / connector / splice name: "fail" | "warn"} marks what the failing checks are about.
    footer: [(severity, text)] lines drawn under the diagram (the review image)."""
    status = status or {}
    shield_pins = {p for c in h.cables.values() for p in c.shield_ends}
    names = list(h.connectors) + list(h.splices)
    sizes: dict[str, tuple[float, float]] = {}
    rows_of: dict[str, list] = {}
    for c in h.connectors.values():
        w, ht, rows = _conn_size(h, c, shield_pins)
        sizes[c.name], rows_of[c.name] = (w, ht), rows
    for s in h.splices:
        sizes[s] = (2 * SPLICE_R + 4, 2 * SPLICE_R + 4)

    layer = _layers(h, names)
    cols: dict[int, list[str]] = {}
    for n in names:
        cols.setdefault(layer[n], []).append(n)
    nbr: dict[str, set[str]] = {n: set() for n in names}
    for l in h.links:
        a, b = _node_key(l.a), _node_key(l.b)
        if a != b:
            nbr[a].add(b)
            nbr[b].add(a)
    order = {n: i for col in cols.values() for i, n in enumerate(col)}
    for _ in range(4):                                   # barycentre sweeps: fewer crossings
        for k in sorted(cols):
            col = cols[k]
            def bary(n):
                ys = [order[m] for m in nbr[n] if layer[m] != k]
                return sum(ys) / len(ys) if ys else order[n]
            col.sort(key=bary)
            for i, n in enumerate(col):
                order[n] = i

    xs: dict[int, float] = {}
    x = float(MARGIN)
    for k in sorted(cols):
        xs[k] = x
        x += max(sizes[n][0] for n in cols[k]) + (GAP if k + 1 in cols else 0)
    top = MARGIN + (22 if title is not False else 0)
    pos: dict[str, tuple[float, float]] = {}                 # top-left corner
    gap_of = lambda n: 28 if n in h.connectors else 16
    for k in sorted(cols):                                   # first: every column stacked from the top
        y = float(top)
        for n in cols[k]:
            pos[n] = (xs[k], y)
            y += sizes[n][1] + gap_of(n)

    def offset(e) -> float:                                  # an endpoint's y inside its node
        if isinstance(e, PinRef):
            return HEAD + ROW * h.connectors[e.connector].pin_names.index(e.pin) + ROW / 2
        return sizes[e.splice][1] / 2

    def wanted(n: str, side: set[int]) -> float | None:
        """Where node n wants its top edge so its wires run level, judged by neighbours in the columns `side`."""
        ys = []
        for l in h.links:
            for mine, other in ((l.a, l.b), (l.b, l.a)):
                if _node_key(mine) == n and layer[_node_key(other)] in side and _node_key(other) != n:
                    ys.append(pos[_node_key(other)][1] + offset(other) - offset(mine))
        return sum(ys) / len(ys) if ys else None

    for sweep in range(6):
        ks = sorted(cols) if sweep % 2 == 0 else sorted(cols, reverse=True)
        for k in ks:
            side = {k - 1, k + 1} if sweep >= 2 else ({k - 1} if sweep % 2 == 0 else {k + 1})
            y = float(top)
            for n in cols[k]:                                # keep the column's order, resolve overlaps downwards
                w = wanted(n, side)
                y0 = max(y, w) if w is not None else y
                pos[n] = (xs[k], y0)
                y = y0 + sizes[n][1] + gap_of(n)
    low = min((pos[n][1] for n in pos), default=top)
    if low > top:
        pos = {n: (x0, y0 - (low - top)) for n, (x0, y0) in pos.items()}
    for n, (hx, hy) in h.layout.items():                   # the editor's drag positions
        if n in pos:
            if n in h.splices:
                pos[n] = (hx - sizes[n][0] / 2, hy - sizes[n][1] / 2)
            else:
                pos[n] = (hx, hy)

    width = max(pos[n][0] + sizes[n][0] for n in pos) + MARGIN if pos else 2 * MARGIN
    height = max(pos[n][1] + sizes[n][1] for n in pos) + MARGIN if pos else 2 * MARGIN
    width = max(width, 420.0)
    scene = Scene(width, height)
    items = scene.items

    if title is not False:
        items.append({"t": "text", "x": MARGIN, "y": 22, "s": title or h.name, "size": 14, "weight": "700", "fill": INK, "g": "title"})

    # connectors and splices
    for n in names:
        x0, y0 = pos[n]
        w, ht = sizes[n]
        if n in h.connectors:
            c = h.connectors[n]
            st = status.get(n)
            g = f"conn:{n}"
            border = FAIL if st == "fail" else WARN if st == "warn" else BORDER
            items.append({"t": "rect", "x": x0, "y": y0, "w": w, "h": ht, "rx": 4, "fill": BOX, "stroke": border, "sw": 2 if st else 1.2, "g": g, "cls": "box"})
            items.append({"t": "rect", "x": x0, "y": y0, "w": w, "h": HEAD - 4, "rx": 4, "fill": HEADER, "stroke": "none", "sw": 0, "g": g, "cls": "head"})
            items.append({"t": "text", "x": x0 + PAD, "y": y0 + 16, "s": f"{c.name} · {c.type}", "size": FONT + 1, "weight": "700", "fill": INK, "g": g})
            sub = f"{c.pins}-pin" + (f" {c.gender}" if c.gender else "") + (f" · {c.part_number}" if c.part_number else "")
            items.append({"t": "text", "x": x0 + PAD, "y": y0 + 30, "s": sub, "size": FONT - 1, "fill": MUTED, "g": g})
            items.append({"t": "line", "x1": x0, "y1": y0 + HEAD - 4, "x2": x0 + w, "y2": y0 + HEAD - 4, "stroke": border, "sw": 1, "g": g})
            for i, (pin, label) in enumerate(rows_of[n]):
                ry = y0 + HEAD + ROW * i
                if i % 2:
                    items.append({"t": "rect", "x": x0 + 1, "y": ry, "w": w - 2, "h": ROW, "rx": 0, "fill": "#f1f4f7", "stroke": "none", "sw": 0, "g": g, "cls": "band"})
                items.append({"t": "text", "x": x0 + PAD, "y": ry + 13, "s": pin, "size": FONT, "weight": "700", "fill": INK, "g": g})
                if label:
                    items.append({"t": "text", "x": x0 + PAD + text_w(pin + "  "), "y": ry + 13, "s": label, "size": FONT, "fill": MUTED, "g": g})
                items.append({"t": "rect", "x": x0, "y": ry, "w": w, "h": ROW, "rx": 0, "fill": "#ffffff", "stroke": "none", "sw": 0, "g": g, "cls": "pinhit", "pin": pin, "alpha_fill": 0.0})
                scene.pins[f"{n}.{pin}"] = {"y": ry + ROW / 2, "xl": x0, "xr": x0 + w}
            scene.nodes[n] = {"kind": "connector", "x": x0, "y": y0, "w": w, "h": ht}
        else:
            st = status.get(n)
            cx, cy = x0 + w / 2, y0 + ht / 2
            col = FAIL if st == "fail" else WARN if st == "warn" else INK
            items.append({"t": "circle", "cx": cx, "cy": cy, "r": SPLICE_R, "fill": col, "stroke": "#ffffff", "sw": 1.5, "g": f"splice:{n}", "cls": "dot"})
            items.append({"t": "text", "x": cx, "y": cy - SPLICE_R - 5, "s": n, "size": FONT - 1, "weight": "700", "fill": col, "anchor": "middle", "g": f"splice:{n}"})
            scene.pins[n] = {"y": cy, "xl": cx, "xr": cx}
            scene.nodes[n] = {"kind": "splice", "x": x0, "y": y0, "w": w, "h": ht}

    # shield stubs
    for cab in h.cables.values():
        for p in cab.shield_ends:
            a = scene.pins.get(f"{p.connector}.{p.pin}")
            nd = scene.nodes.get(p.connector)
            if a and nd:
                right = layer[p.connector] == 0 and len(cols) > 1
                x1 = nd["x"] + nd["w"] if right else nd["x"]
                x2 = x1 + (22 if right else -22)
                items.append({"t": "line", "x1": x1, "y1": a["y"], "x2": x2, "y2": a["y"], "stroke": MUTED, "sw": 1.6, "dash": "3 3", "g": f"conn:{p.connector}", "cls": "shield"})

    # wires: invisible fat strokes first (a wide click target under everything), then the visible wires
    hits: list[dict] = []
    visible: list[dict] = []
    tags: list[dict] = []                                      # the labels, drawn over every wire
    labels: list[tuple[float, float, float, float]] = []      # label boxes placed so far (x, y, w, h)
    for l in sorted(h.links, key=lambda l: (layer[_node_key(l.a)], _anchor(scene, l.a, 0)[1] + _anchor(scene, l.b, 0)[1])):
        ka, kb = _node_key(l.a), _node_key(l.b)
        xa, xb = scene.nodes[ka]["x"], scene.nodes[kb]["x"]
        sa, sb = ("r", "l") if xb > xa + 1 else ("l", "r") if xb < xa - 1 else ("r", "r")
        (x1, y1), (x2, y2) = _anchor(scene, l.a, sa), _anchor(scene, l.b, sb)
        d = max(40.0, min(150.0, abs(x2 - x1) * 0.5)) if sa != sb or abs(x2 - x1) > 1 else 70.0
        c1 = (x1 + (d if sa == "r" else -d), y1)
        c2 = (x2 + (d if sb == "r" else -d), y2)
        pts = [(x1, y1), c1, c2, (x2, y2)]
        cur = h.wire_current(l)
        line1 = f"{l.id}  {l.gauge.label}  {l.color}"
        line2 = f"{l.length_mm:g} mm" + (f" · {cur:g} A" if cur is not None else "") + (f" · {l.net}" if l.net else "")
        lw = max(text_w(line1, FONT - 1), text_w(line2, FONT - 1)) + 10
        t0 = 0.5                                               # where the label would like to sit along the wire
        if isinstance(l.a, SpliceRef) != isinstance(l.b, SpliceRef):    # wires fan out of a splice: label them where they are apart
            t0 = 0.7 if isinstance(l.a, SpliceRef) else 0.3
        t, (mx, my) = t0, _bez(pts, t0)
        for cand in [t0] + sorted((0.2 + 0.05 * i for i in range(13)), key=lambda v: abs(v - t0)):      # the nearest free spot
            cx, cy = _bez(pts, cand)
            if not any(_overlap((cx - lw / 2, cy - 14, lw, 28), r) for r in labels):
                t, (mx, my) = cand, (cx, cy)
                break
        else:                                                  # no free spot along the wire: nudge it down
            for k in range(1, 8):
                if not any(_overlap((mx - lw / 2, my + 30 * k - 14, lw, 28), r) for r in labels):
                    my += 30 * k
                    break
        labels.append((mx - lw / 2, my - 14, lw, 28))
        st = status.get(l.id)
        scene.wires[l.id] = {"pts": pts, "t": t, "sides": [sa, sb], "label": [mx, my]}
        g = f"wire:{l.id}"
        base, stripe = color_hex(l.color), stripe_hex(l.color)
        if st:
            visible.append({"t": "path", "pts": pts, "stroke": FAIL if st == "fail" else WARN, "sw": 9, "alpha": 0.35, "g": g, "cls": "halo"})
        visible.append({"t": "path", "pts": pts, "stroke": "#23282f", "sw": 5.4, "g": g, "cls": "edge"})
        visible.append({"t": "path", "pts": pts, "stroke": base, "sw": 3.4, "g": g, "cls": "core"})
        if stripe:
            visible.append({"t": "path", "pts": pts, "stroke": stripe, "sw": 3.4, "dash": "7 7", "g": g, "cls": "stripe"})
        w = lw
        col = FAIL if st == "fail" else "#b25e00" if st == "warn" else INK
        tags.append({"t": "rect", "x": mx - w / 2, "y": my - 14, "w": w, "h": 28, "rx": 4, "fill": "#ffffff", "stroke": col if st else "#c5ccd6", "sw": 1.4 if st else 0.8, "alpha_fill": 0.92, "g": g, "cls": "labelbox"})
        tags.append({"t": "text", "x": mx, "y": my - 3, "s": line1, "size": FONT - 1, "weight": "700", "fill": col, "anchor": "middle", "g": g, "cls": "label"})
        tags.append({"t": "text", "x": mx, "y": my + 9, "s": line2, "size": FONT - 1, "fill": col if st else MUTED, "anchor": "middle", "g": g, "cls": "label2"})
        hits.append({"t": "path", "pts": pts, "stroke": "#000000", "sw": 14, "alpha": 0.0, "g": g, "cls": "hit"})
        scene.height = max(scene.height, my + 20)
    items.extend(hits)
    items.extend(visible)
    items.extend(tags)

    if footer:
        y = scene.height + 6
        scene.footer_top = y
        items.append({"t": "line", "x1": MARGIN, "y1": y, "x2": scene.width - MARGIN, "y2": y, "stroke": "#c5ccd6", "sw": 1, "g": "footer"})
        y += 18
        for sev, text in footer:
            col = {"fail": FAIL, "warn": "#b25e00", "pass": "#1a7f4b", "head": INK}.get(sev, INK)
            lines = _wrap(text, int((scene.width - 2 * MARGIN) / (FONT * 0.58)))
            for j, ln in enumerate(lines):
                items.append({"t": "text", "x": MARGIN + (0 if j == 0 else 14), "y": y, "s": ln, "size": FONT, "weight": "700" if sev == "head" else "400", "fill": col, "g": "footer"})
                y += 15
        scene.height = y + 8
    return scene


def _wrap(text: str, width: int) -> list[str]:
    width = max(width, 40)
    out, line = [], ""
    for word in text.split(" "):
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return (out + [line])[:4] if line else out[:4]


def _anchor(scene: Scene, e, side: str | int) -> tuple[float, float]:
    key = f"{e.connector}.{e.pin}" if isinstance(e, PinRef) else e.splice
    p = scene.pins[key]
    return (p["xr"] if side == "r" else p["xl"], p["y"])


def _overlap(a, b, pad: float = 2.0) -> bool:
    return a[0] < b[0] + b[2] + pad and b[0] < a[0] + a[2] + pad and a[1] < b[1] + b[3] + pad and b[1] < a[1] + a[3] + pad


def _bez(pts, t: float) -> tuple[float, float]:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
    u = 1 - t
    return (u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
            u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3)


def status_from(report) -> dict[str, str]:
    """{ref: "fail" | "warn"} from a HarnessReport: what the non-passing checks are about (failures win)."""
    out: dict[str, str] = {}
    for c in report.checks:
        if c.severity == "pass":
            continue
        for r in c.refs:
            if out.get(r) != "fail":
                out[r] = c.severity
    return out


def footer_from(report, limit: int = 14) -> list[tuple[str, str]]:
    n_fail, n_warn = len(report.failed), len(report.warnings)
    lines = [("head", f"Checks: {len(report.checks) - n_fail - n_warn} pass, {n_warn} warn, {n_fail} FAIL")]
    for c in report.failed + report.warnings:
        lines.append((c.severity, f"{'FAIL' if c.severity == 'fail' else 'WARN'}  {c.name}: {c.measured}"))
    if len(lines) > limit:
        lines = lines[:limit] + [("head", f"... and {len(lines) - limit} more (see the report)")]
    if n_fail == 0 and n_warn == 0:
        lines.append(("pass", "every check passes"))
    return lines


# --------------------------------------------------------------------------- SVG


def _bezier_d(pts) -> str:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
    return f"M{x0:.1f},{y0:.1f} C{x1:.1f},{y1:.1f} {x2:.1f},{y2:.1f} {x3:.1f},{y3:.1f}"


def scene_to_svg(scene: Scene, standalone: bool = True) -> str:
    out = []
    if standalone:
        out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{scene.width:.0f}" height="{scene.height:.0f}" '
                   f'viewBox="0 0 {scene.width:.0f} {scene.height:.0f}" font-family="IBM Plex Sans, Helvetica, Arial, sans-serif" class="harness">')
        out.append(f'<rect width="100%" height="100%" fill="#ffffff"/>')
    group = None

    def close():
        if group is not None:
            out.append("</g>")

    for it in scene.items:
        g = it.get("g", "")
        if g != group:
            close()
            kind, _, ident = g.partition(":")
            if ident:
                out.append(f'<g class="{kind}" data-kind="{kind}" data-id="{escape(ident)}">')
            else:
                out.append(f'<g class="{escape(g)}">')
            group = g
        out.append(_svg_item(it))
    close()
    if standalone:
        out.append("</svg>")
    return "\n".join(out)


def _svg_item(it: dict) -> str:
    cls = f' class="{it["cls"]}"' if it.get("cls") else ""
    t = it["t"]
    if t == "rect":
        fill = it["fill"]
        op = f' fill-opacity="{it["alpha_fill"]}"' if "alpha_fill" in it else ""
        pin = f' data-pin="{escape(str(it["pin"]), {chr(34): "&quot;"})}"' if "pin" in it else ""
        return (f'<rect{cls}{pin} x="{it["x"]:.1f}" y="{it["y"]:.1f}" width="{it["w"]:.1f}" height="{it["h"]:.1f}" rx="{it.get("rx", 0)}" '
                f'fill="{fill}"{op} stroke="{it["stroke"]}" stroke-width="{it["sw"]}"/>')
    if t == "text":
        anchor = f' text-anchor="{it["anchor"]}"' if it.get("anchor") else ""
        weight = f' font-weight="{it["weight"]}"' if it.get("weight") else ""
        return f'<text{cls} x="{it["x"]:.1f}" y="{it["y"]:.1f}" font-size="{it["size"]}" fill="{it["fill"]}"{anchor}{weight}>{escape(str(it["s"]))}</text>'
    if t == "line":
        dash = f' stroke-dasharray="{it["dash"]}"' if it.get("dash") else ""
        return f'<line{cls} x1="{it["x1"]:.1f}" y1="{it["y1"]:.1f}" x2="{it["x2"]:.1f}" y2="{it["y2"]:.1f}" stroke="{it["stroke"]}" stroke-width="{it["sw"]}"{dash}/>'
    if t == "circle":
        return f'<circle{cls} cx="{it["cx"]:.1f}" cy="{it["cy"]:.1f}" r="{it["r"]}" fill="{it["fill"]}" stroke="{it["stroke"]}" stroke-width="{it["sw"]}"/>'
    if t == "path":
        dash = f' stroke-dasharray="{it["dash"]}"' if it.get("dash") else ""
        alpha = f' stroke-opacity="{it["alpha"]}"' if "alpha" in it else ""
        return (f'<path{cls} d="{_bezier_d(it["pts"])}" fill="none" stroke="{it["stroke"]}" stroke-width="{it["sw"]}"{alpha}{dash} '
                f'stroke-linecap="round"/>')
    return ""


# --------------------------------------------------------------------------- PNG (Pillow)

SS = 3          # drawn at 3x, shown at 1.5x: smooth edges without anti-aliased primitives
OUT = 1.5


def _font(size: float):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                                  # Pillow < 10.1: only a bitmap font
        return ImageFont.load_default()


def _flatten(pts, n: int = 28) -> list[tuple[float, float]]:
    return [_bez(pts, i / n) for i in range(n + 1)]


def _hex_rgb(h: str, alpha: float = 1.0, bg=(255, 255, 255)):
    h = h.lstrip("#")
    c = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(round(c[i] * alpha + bg[i] * (1 - alpha)) for i in range(3))


def scene_to_png(scene: Scene, path) -> str:
    from PIL import Image, ImageDraw
    W, H = int(scene.width * SS), int(scene.height * SS)
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    fonts: dict = {}

    def font(size, bold=False):
        k = (size, bold)
        if k not in fonts:
            fonts[k] = _font(size * SS)
        return fonts[k]

    for it in scene.items:
        t = it["t"]
        if t == "rect":
            if it.get("alpha_fill", 1.0) == 0.0:
                continue
            box = [it["x"] * SS, it["y"] * SS, (it["x"] + it["w"]) * SS, (it["y"] + it["h"]) * SS]
            fill = _hex_rgb(it["fill"], it.get("alpha_fill", 1.0))
            outline = None if it["stroke"] == "none" else _hex_rgb(it["stroke"])
            if it.get("rx", 0) > 0:
                d.rounded_rectangle(box, radius=it["rx"] * SS, fill=fill, outline=outline, width=max(1, round(it["sw"] * SS)))
            else:
                d.rectangle(box, fill=fill, outline=outline, width=max(1, round(it["sw"] * SS)))
        elif t == "text":
            f = font(it["size"], it.get("weight") == "700")
            anchor = {"middle": "ms", "end": "rs"}.get(it.get("anchor"), "ls")
            d.text((it["x"] * SS, it["y"] * SS), str(it["s"]), font=f, fill=_hex_rgb(it["fill"]), anchor=anchor,
                   stroke_width=1 if it.get("weight") == "700" and SS >= 3 else 0)
        elif t == "line":
            _line(d, [(it["x1"] * SS, it["y1"] * SS), (it["x2"] * SS, it["y2"] * SS)], _hex_rgb(it["stroke"]), it["sw"] * SS, it.get("dash"))
        elif t == "circle":
            r = it["r"] * SS
            d.ellipse([it["cx"] * SS - r, it["cy"] * SS - r, it["cx"] * SS + r, it["cy"] * SS + r], fill=_hex_rgb(it["fill"]),
                      outline=_hex_rgb(it["stroke"]), width=max(1, round(it["sw"] * SS)))
        elif t == "path":
            if it.get("alpha", 1.0) == 0.0:
                continue
            col = _hex_rgb(it["stroke"], it.get("alpha", 1.0))
            _line(d, [(x * SS, y * SS) for x, y in _flatten(it["pts"])], col, it["sw"] * SS, it.get("dash"))
    img = img.resize((round(scene.width * OUT), round(scene.height * OUT)), Image.LANCZOS)
    img.save(str(path), "PNG")
    return str(path)


def _line(d, pts, color, width, dash=None) -> None:
    width = max(1, round(width))
    if not dash:
        d.line(pts, fill=color, width=width, joint="curve")
        r = width / 2
        for x, y in (pts[0], pts[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)
        return
    on, off = (float(v) * SS for v in dash.split())
    # walk the polyline, drawing the "on" stretches only
    pos, drawing, left = 0.0, True, on
    cur = pts[0]
    for nxt in pts[1:]:
        seg = math.dist(cur, nxt)
        if seg == 0:
            continue
        done = 0.0
        while done < seg:
            step = min(left, seg - done)
            a = (cur[0] + (nxt[0] - cur[0]) * done / seg, cur[1] + (nxt[1] - cur[1]) * done / seg)
            b = (cur[0] + (nxt[0] - cur[0]) * (done + step) / seg, cur[1] + (nxt[1] - cur[1]) * (done + step) / seg)
            if drawing:
                d.line([a, b], fill=color, width=width)
            done += step
            left -= step
            if left <= 1e-9:
                drawing = not drawing
                left = on if drawing else off
        cur = nxt
