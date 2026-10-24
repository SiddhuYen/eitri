"""The harness diagram, drawn by our own layout code (no Graphviz, no WireViz).

    build_scene(harness, ...)  ->  Scene     connectors as pin tables, wires as coloured curves, splices as dots
    scene_to_svg(scene)        ->  str       the editor's interactive drawing (every element carries data-* ids)
    scene_to_png(scene, path)                the agent's review image, rasterised with Pillow (nothing else needed)

Layout: nodes are put in columns by their position along the wires (sources left, loads right), ordered inside a
column to keep wires from crossing, and placed near their neighbours. `Harness.layout` (node name -> [x, y])
overrides the automatic place of a node: these are the editor's drag positions, layout hints and not geometry.
"""
from __future__ import annotations


from ..diagram import (BORDER, BOX, FAIL, FONT, HEADER, INK, MUTED, WARN, Scene, _bez, _overlap, _wrap, footer_from,   # noqa: F401
                       scene_to_png, scene_to_svg, status_from, text_w)
from .catalog import color_hex, stripe_hex
from .model import Harness, Link, PinRef, SpliceRef

ROW = 18
HEAD = 38
PAD = 14
MARGIN = 28
GAP = 250
SPLICE_R = 6


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


def _anchor(scene: Scene, e, side: str | int) -> tuple[float, float]:
    key = f"{e.connector}.{e.pin}" if isinstance(e, PinRef) else e.splice
    p = scene.pins[key]
    return (p["xr"] if side == "r" else p["xl"], p["y"])
