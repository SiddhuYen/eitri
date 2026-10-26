"""The pneumatic schematic: ISO 1219-style symbols (supply, FRL / regulator, valves as position boxes with arrows, cylinders,
speed controllers, silencers, tees) on rows from the supply at the bottom to the cylinders at the top, tubes as orthogonal
lines. Drawn with eitri.diagram; components carry data-kind="component" and tubes data-kind="tube" for the editor."""
from __future__ import annotations

from ..diagram import FAIL, FONT, INK, MUTED, WARN, Scene, _wrap, footer_from, place_labels, scene_to_png, scene_to_svg, text_w   # noqa: F401
from ..diagram import status_from as _status_from
from ..route import Router
from .model import Circuit, Cylinder, FlowControl, Node, PortRef, Regulator, Silencer, Supply, Valve

MARGIN = 28
ROW_GAP = 110
TOP = 86
SPACING = 170
LEVEL = {"supply": 0, "regulator": 1, "frl": 1, "node": 1.3, "valve": 2, "flow_control": 3, "cylinder": 4, "silencer": 2}
STUB = 14
VBOX = 44.0                 # one valve position's square: wide enough for P between the exhausts R and S and their silencers
LINE = "#2b3a4a"
TUBE = "#1f5fbf"


def _size(comp) -> tuple[float, float]:
    k = comp.kind
    if k == "cylinder":
        return 96.0, 28.0
    if k == "valve":
        return VBOX * len(comp.positions) + 36.0, 32.0
    if k == "frl":
        return (76.0 if comp.lubricator else 52.0), 30.0
    if k == "regulator":
        return 30.0, 30.0
    if k == "supply":
        return 26.0, 26.0
    if k == "flow_control":
        return 30.0, 30.0
    if k == "silencer":
        return 14.0, 16.0
    return 8.0, 8.0


def status_from(report) -> dict[str, str]:
    """What to colour: the failing and warning checks' parts, except the "flow data estimated" notice, which names nearly every part
    (it is a data-quality note, not a limit being approached; it stays in the checks and the footer)."""
    class _R:
        checks = [c for c in report.checks if c.category != "flow data"]
    return _status_from(_R)


def _extent(comp) -> float:
    """The width a component takes in its row: its symbol (a valve with its actuator and return) or its title, whichever is wider."""
    w, _h = _size(comp)
    if comp.kind == "valve":
        w += 40
    title = comp.name if comp.kind == "node" else f"{comp.name} · {_short(comp)}"
    return max(w, text_w(title, FONT - 1) + 8 if comp.kind not in ("node", "silencer") else w)


def _body(comp, cx: float, cy: float) -> tuple[float, float, float, float]:
    """The box a component's symbol covers (what tubes route around)."""
    w, h = _size(comp)
    if comp.kind == "valve":
        n = len(comp.positions)
        return (cx - n * VBOX / 2 - 18, cy - h / 2, n * VBOX + 36, h)
    if comp.kind == "frl":
        return (cx - w / 2 - 2, cy - h / 2 - 10, w + 4, h + 10)
    if comp.kind == "regulator":
        return (cx - w / 2, cy - h / 2 - 10, w, h + 10)
    return (cx - w / 2, cy - h / 2, w, h)


def _ports(comp, cx: float, cy: float) -> dict[str, tuple[float, float, str]]:
    """Port positions (x, y, exit direction) on a component drawn at its centre."""
    w, h = _size(comp)
    k = comp.kind
    if k == "cylinder":
        p = {"CAP": (cx - w / 2 + 12, cy + h / 2, "down")}
        if comp.acting == "double":
            p["ROD"] = (cx + w / 2 - 14, cy + h / 2, "down")
        return p
    if k == "valve":
        n = len(comp.positions)
        box = VBOX
        x0 = cx - n * box / 2 + comp.rest_position * box        # the rest position's box carries the ports
        out = {"P": (x0 + box / 2, cy + h / 2, "down")}
        if "A" in comp.ports:
            out["A"] = (x0 + box * 0.25, cy - h / 2, "up")
        if "B" in comp.ports:
            out["B"] = (x0 + box * 0.75, cy - h / 2, "up")
        if "R" in comp.ports:
            out["R"] = (x0 + box * 0.2, cy + h / 2, "down")
        if "S" in comp.ports:
            out["S"] = (x0 + box * 0.8, cy + h / 2, "down")
        if comp.cls == "3/2":
            out["A"] = (x0 + box / 2, cy - h / 2, "up")
        return out
    if k in ("frl", "regulator"):
        return {"IN": (cx - w / 2, cy, "left"), "OUT": (cx + w / 2, cy, "right")}
    if k == "supply":
        return {"OUT": (cx, cy - w / 2, "up")}
    if k == "flow_control":
        return {"A": (cx, cy + h / 2, "down"), "B": (cx, cy - h / 2, "up")}
    if k == "silencer":
        return {"IN": (cx, cy - h / 2, "up")}
    return {"N": (cx, cy, "up")}


def _route(p1, p2) -> list[tuple[float, float]]:
    (x1, y1, d1), (x2, y2, d2) = p1, p2
    step = {"up": (0, -STUB), "down": (0, STUB), "left": (-STUB, 0), "right": (STUB, 0)}
    a = (x1 + step[d1][0], y1 + step[d1][1])
    b = (x2 + step[d2][0], y2 + step[d2][1])
    v1, v2 = d1 in ("up", "down"), d2 in ("up", "down")
    if abs(a[0] - b[0]) < 0.5 or abs(a[1] - b[1]) < 0.5:
        pts = [(x1, y1), a, b, (x2, y2)]
    elif v1 and v2:
        my = (a[1] + b[1]) / 2
        pts = [(x1, y1), a, (a[0], my), (b[0], my), b, (x2, y2)]
    elif not v1 and not v2:
        mx = (a[0] + b[0]) / 2
        pts = [(x1, y1), a, (mx, a[1]), (mx, b[1]), b, (x2, y2)]
    elif v1:
        pts = [(x1, y1), a, (a[0], b[1]), b, (x2, y2)]
    else:
        pts = [(x1, y1), a, (b[0], a[1]), b, (x2, y2)]
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) > 0.1 or abs(p[1] - out[-1][1]) > 0.1:
            out.append(p)
    return out


def layout(c: Circuit) -> dict[str, tuple[float, float]]:
    """Component centres: rows by kind (supply at the bottom, cylinders at the top), x from the cylinders down by connectivity,
    the editor's LAYOUT hints on top."""
    comps = [x for x in c.components.values() if x.kind != "silencer"]
    nbrs: dict[str, set[str]] = {x.name: set() for x in comps}
    for t in c.tubes:
        nbrs[t.a.component].add(t.b.component)
        nbrs[t.b.component].add(t.a.component)
    lvl = {x.name: LEVEL.get(x.kind, 2) for x in comps}
    for _ in range(3):
        for x in comps:
            if x.kind == "node":
                ls = [lvl[n] for n in nbrs[x.name]]
                if ls and max(ls) > min(ls):                      # between its neighbours, nearer the lower row: clear of the
                    lvl[x.name] = min(ls) + 0.45 * (max(ls) - min(ls))   # silencers and stubs hanging under the upper one
                elif ls:
                    lvl[x.name] = ls[0]
    xs: dict[str, float] = {}
    cyls = [x for x in comps if x.kind == "cylinder"]
    for i, x in enumerate(cyls):
        xs[x.name] = MARGIN + 80 + i * SPACING
    for level in sorted({lvl[x.name] for x in comps}, reverse=True):
        row = [x for x in comps if lvl[x.name] == level and x.name not in xs]
        for x in row:
            above = [xs[n] for n in nbrs[x.name] if n in xs and lvl[n] > level]
            xs[x.name] = sum(above) / len(above) if above else None
        free = [x for x in row if xs[x.name] is None]
        for i, x in enumerate(free):
            xs[x.name] = MARGIN + 60 + i * SPACING
        # keep a minimum spacing within the row
        ordered = sorted([x for x in comps if lvl[x.name] == level], key=lambda x: xs[x.name])
        for i in range(1, len(ordered)):
            need = (_extent(ordered[i - 1]) + _extent(ordered[i])) / 2 + 30
            if xs[ordered[i].name] - xs[ordered[i - 1].name] < need:
                xs[ordered[i].name] = xs[ordered[i - 1].name] + need
    # under the valves there is more to fit (their P stubs, the silencers on their exhausts, a manifold): 40 px more room there
    row_y = lambda lv: TOP + (4 - lv) * ROW_GAP + 40.0 * min(1.0, max(0.0, LEVEL["valve"] - lv))
    pos = {x.name: (xs[x.name], row_y(lvl[x.name])) for x in comps}
    # a supply feeding a side port (an FRL's IN) stands to that side, so its tube comes in at the port
    for t in c.tubes:
        for sup_end, other in ((t.a, t.b), (t.b, t.a)):
            sup, oth = c.components.get(sup_end.component), c.components.get(other.component)
            if sup is None or oth is None or sup.kind != "supply" or oth.name not in pos:
                continue
            ox, oy = pos[oth.name]
            px, _py, d = _ports(oth, ox, oy).get(other.port, (ox, oy, "up"))
            if d == "left":
                pos[sup.name] = (px - 52, pos[sup.name][1])
            elif d == "right":
                pos[sup.name] = (px + 52, pos[sup.name][1])
    if pos:                                                           # keep everything right of the margin
        shift = max(0.0, MARGIN + 30 - min(x - _extent(c.components[n]) / 2 for n, (x, _y) in pos.items()))
        pos = {n: (x + shift, y) for n, (x, y) in pos.items()}
    for name, (hx, hy) in c.layout.items():
        if name in pos:
            pos[name] = (hx, hy)
    return pos


def build_scene(c: Circuit, rep=None, status: dict[str, str] | None = None, footer: list[tuple[str, str]] | None = None, title: str | None = None) -> Scene:
    status = status or {}
    pos = layout(c)
    ports: dict[PortRef, tuple[float, float, str]] = {}
    for name, (cx, cy) in pos.items():
        for p, where in _ports(c.components[name], cx, cy).items():
            ports[PortRef(name, p)] = where
    # silencers hang under the exhaust port they are screwed onto
    leaders: list[tuple[str, list]] = []
    for port, sref in c.attached.items():
        if port in ports:
            x, y, _d = ports[port]
            off = -12.0 if port.port == "R" else 12.0 if port.port == "S" else 0.0      # exhausts either side of P: room for its tube
            sx, sy = x + off, y + 14 + _size(c.components[sref.component])[1] / 2
            pos[sref.component] = (sx, sy)
            leaders.append((sref.component, [(x, y), (x, y + 6), (sx, y + 6), (sx, y + 14)] if off else [(x, y), (x, y + 14)]))
    width = max([x + _size(c.components[n])[0] / 2 for n, (x, _y) in pos.items()] + [420.0]) + MARGIN
    height = max([y + _size(c.components[n])[1] / 2 for n, (_x, y) in pos.items()] + [200.0]) + MARGIN + 16
    sc = Scene(width, height)
    items = sc.items
    items.append({"t": "text", "x": MARGIN, "y": 22, "s": title or c.name, "size": 14, "weight": "700", "fill": INK, "g": "title"})

    # tubes first (under the symbols): routed around every symbol, entering each component at its port, never along another tube
    bodies = [_body(c.components[n], x, y) for n, (x, y) in pos.items()]
    router = Router(bodies, (8, 34, width + 60, height + 60))
    order = sorted([t for t in c.tubes if t.a in ports and t.b in ports],
                   key=lambda t: abs(ports[t.a][0] - ports[t.b][0]) + abs(ports[t.a][1] - ports[t.b][1]))
    routes: dict[str, list] = {}
    for _sname, lpts in leaders:                                     # a tube may cross a silencer's leader, never run along it
        router.take(lpts)
    silenced = {p.component for p in c.attached}

    # a tee: each of its tubes leaves on its own side, the side facing its other end where it can
    node_side: dict[tuple[str, str], str] = {}
    for comp in c.components.values():
        if comp.kind != "node" or comp.name not in pos:
            continue
        x, y = pos[comp.name]
        here = [(t, t.b if t.a.component == comp.name else t.a) for t in c.tubes if comp.name in (t.a.component, t.b.component)]
        # every way of giving the tubes distinct sides (at most 4! of them): the one closest to each tube's direction toward
        # where its other end's line starts
        import itertools, math
        want = []
        for t, other in here:
            ox, oy, od = ports[other]
            step = {"up": (0, -14), "down": (0, 14), "left": (-14, 0), "right": (14, 0)}[od]
            want.append(math.atan2(-(oy + step[1] - y), ox + step[0] - x))
        ang = {"right": 0.0, "up": math.pi / 2, "left": math.pi, "down": -math.pi / 2}
        off = lambda a, b: abs((a - b + math.pi) % (2 * math.pi) - math.pi)
        sides = list(ang)
        best = None
        for combo in itertools.permutations(sides, min(len(here), 4)):
            cost = sum(off(want[i], ang[combo[i]]) for i in range(len(combo)))
            if best is None or cost < best[0]:
                best = (cost, combo)
        for i, (t, _other) in enumerate(here):
            node_side[(t.name, comp.name)] = best[1][i] if best and i < len(best[1]) else "up"

    def end(ref, other, tube):
        """A tube's end: its port, the direction it leaves in (a tee: its own side) and how far it goes straight."""
        x, y, d = ports[ref]
        comp = c.components[ref.component]
        if comp.kind == "node":
            d = node_side.get((tube, comp.name), d)
        stub = 34.0 if (comp.kind == "valve" and ref.port == "P" and ref.component in silenced) else 10.0 if comp.kind == "node" else 14.0
        return (x, y, d, stub)

    for t in order:
        routes[t.name] = router.route(end(t.a, t.b, t.name), end(t.b, t.a, t.name)) or _route(ports[t.a], ports[t.b])
    for t in c.tubes:
        if t.name not in routes:
            continue
        pts = routes[t.name]
        st = status.get(t.name)
        col = FAIL if st == "fail" else WARN if st == "warn" else TUBE
        g = f"tube:{t.name}"
        items.append({"t": "polyline", "pts": pts, "stroke": col, "sw": 2.2 if not st else 3.2, "fill": "none", "g": g, "cls": "tube"})
        label = f"{t.name}  {t.id_mm:g} mm ID · {t.length_m:g} m"
        items.append({"t": "text", "x": pts[0][0], "y": pts[0][1], "s": label, "size": FONT - 2, "fill": col if st else MUTED, "anchor": "middle",
                      "g": g, "cls": "label", "bg": "#ffffff", "cands": _beside(pts, label, FONT - 2)})
    for sname, pts in leaders:                                       # a silencer screwed onto its exhaust port
        items.append({"t": "polyline", "pts": pts, "stroke": INK, "sw": 1.2, "fill": "none", "g": f"component:{sname}", "cls": "sym"})
    # vents: an open triangle at the port
    for v in c.vents:
        if v in ports:
            x, y, _d = ports[v]
            items.append({"t": "polyline", "pts": [(x - 5, y + 4), (x + 5, y + 4), (x, y + 12)], "closed": True, "fill": "none", "stroke": INK, "sw": 1.2, "g": f"component:{v.component}", "cls": "vent"})
    # components
    for name, (cx, cy) in pos.items():
        comp = c.components[name]
        st = status.get(name)
        col = FAIL if st == "fail" else WARN if st == "warn" else INK
        g = f"component:{name}"
        w, h = _size(comp)
        _symbol(items, comp, cx, cy, w, h, col, g)
        sc.nodes[name] = {"kind": "component", "x": cx, "y": cy, "w": w, "h": h}
        bx, by, bw, bh = _body(comp, cx, cy)
        if comp.kind != "silencer":
            label = name if comp.kind in ("node",) else f"{name} · {_short(comp)}"
            lw = text_w(label, FONT - 1)
            items.append({"t": "text", "x": cx, "y": by - 6, "s": label, "size": FONT - 1, "weight": "700", "fill": col, "anchor": "middle", "g": g, "cls": "label",
                          "bg": "#ffffff", "cands": [(cx, by - 6, "middle"), (bx - 8, cy + 4, "end"), (bx + bw + 8, cy + 4, "start"),
                                                     (cx, by + bh + 16, "middle"), (cx - lw / 2 - 4, by - 6, "middle"), (cx + lw / 2 + 4, by - 6, "middle"),
                                                     (cx, by - 20, "middle"), (bx - 8, by - 4, "end"), (bx + bw + 8, by - 4, "start")]})
            sub = _sub(comp, rep)
            if sub:
                first, *more = sub.split("\n")
                follow = [{"t": "text", "x": bx, "y": by, "s": ln, "size": FONT - 3, "fill": FAIL if st == "fail" else MUTED, "g": g, "cls": "sub",
                           "bg": "#ffffff", "dx": 0, "dy": 11 * (k + 1)} for k, ln in enumerate(more)]
                items.append({"t": "text", "x": bx, "y": by + bh + 30, "s": first, "size": FONT - 3, "fill": FAIL if st == "fail" else MUTED, "g": g, "cls": "sub",
                              "bg": "#ffffff", "follow": follow,
                              "cands": [(bx + bw + 10, cy - 2, "start"), (bx - 10, cy - 2, "end"), (cx, by - 32, "middle"), (cx, by + bh + 30, "middle"),
                                        (cx, by + bh + 44, "middle"), (cx, by + bh + 58, "middle"), (bx + bw + 10, by - 20, "start")]})
                items += follow
        for p, (px, py, _d) in _ports(comp, cx, cy).items():
            if comp.kind in ("node", "silencer"):
                continue
            items.append({"t": "text", "x": px, "y": py, "s": p, "size": FONT - 4, "fill": MUTED, "anchor": "middle", "g": g, "cls": "port",
                          "cands": _port_cands(px, py, _d, p)})
    place_labels(sc)
    sc.height = max(sc.height, max((it.get("y", 0) for it in items if it["t"] == "text"), default=0) + 12)
    if footer:
        y = sc.height + 6
        sc.footer_top = y
        items.append({"t": "line", "x1": MARGIN, "y1": y, "x2": width - MARGIN, "y2": y, "stroke": "#c5ccd6", "sw": 1, "g": "footer"})
        y += 18
        for sev, text in footer:
            colr = {"fail": FAIL, "warn": "#b25e00", "pass": "#1a7f4b", "head": INK}.get(sev, INK)
            for k, ln in enumerate(_wrap(text, int((width - 2 * MARGIN) / (FONT * 0.58)))):
                items.append({"t": "text", "x": MARGIN + (0 if k == 0 else 14), "y": y, "s": ln, "size": FONT, "weight": "700" if sev == "head" else "400", "fill": colr, "g": "footer"})
                y += 15
        sc.height = y + 8
    return sc


def _beside(pts, label: str, size: float) -> list[tuple[float, float, str]]:
    """Where a tube's label may go: beside its own segments (the longest first), never on them."""
    w = text_w(label, size)
    segs = sorted(zip(pts, pts[1:]), key=lambda s: -(abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1])))
    out = []
    for (x1, y1), (x2, y2) in segs:
        for f in (0.5, 0.3, 0.7):
            mx, my = x1 + (x2 - x1) * f, y1 + (y2 - y1) * f
            if abs(y1 - y2) < 0.5:                                   # horizontal: above, below
                if abs(x2 - x1) >= w * 0.6:
                    out += [(mx, my - 6, "middle"), (mx, my + 14, "middle")]
            else:                                                     # vertical: right, left
                out += [(mx + 7, my + 4, "start"), (mx - 7, my + 4, "end")]
    return out or [(pts[0][0] + 7, pts[0][1] + 4, "start")]


def _port_cands(px: float, py: float, d: str, name: str = "") -> list[tuple[float, float, str]]:
    """A port's name beside the point where its tube enters: off the tube's line, toward the outside of the symbol (a valve's
    exhausts R and S outward, past their silencers)."""
    if d == "down" and name == "R":
        return [(px - 22, py + 20, "end"), (px - 22, py + 30, "end"), (px - 5, py + 11, "end")]
    if d == "down" and name == "S":
        return [(px + 22, py + 20, "start"), (px + 22, py + 30, "start"), (px + 5, py + 11, "start")]
    if d == "down":
        return [(px - 5, py + 11, "end"), (px + 5, py + 11, "start"), (px - 5, py + 20, "end"), (px + 5, py + 20, "start"),
                (px - 22, py + 20, "end"), (px + 22, py + 20, "start"), (px - 22, py + 30, "end"), (px + 22, py + 30, "start")]
    if d == "up":
        return [(px - 5, py - 4, "end"), (px + 5, py - 4, "start"), (px - 5, py - 12, "end"), (px + 5, py - 12, "start")]
    if d == "left":
        return [(px - 4, py - 5, "end"), (px - 4, py + 12, "end"), (px - 18, py - 5, "end")]
    return [(px + 4, py - 5, "start"), (px + 4, py + 12, "start"), (px + 18, py - 5, "start")]


def _short(comp) -> str:
    k = comp.kind
    if k == "cylinder":
        return f"D{comp.bore_mm:g}x{comp.stroke_mm:g}" + (" single" if comp.acting == "single" else "")
    if k == "valve":
        return f"{comp.cls} {comp.actuation}"
    if k == "frl":
        return f"{'FRL' if comp.lubricator else 'FR'} {comp.set_bar:g} bar"
    if k == "regulator":
        return f"{comp.set_bar:g} bar"
    if k == "supply":
        return f"{comp.pressure_bar:g} bar"
    if k == "flow_control":
        return f"meter-{comp.meter}"
    return k


def _sub(comp, rep) -> str:
    if rep is None or comp.kind != "cylinder":
        return ""
    cy = next((x for x in rep.cylinders if x.name == comp.name), None)
    if cy is None:
        return ""
    lines = []
    for s in cy.strokes:                                       # one line per stroke: force usable / load, speed possible / wanted
        bits = []
        if s.usable_N is not None:
            bits.append(f"{s.usable_N:.0f}/{s.load_N:g} N")
        if s.speed_available is not None and s.speed_required is not None:
            bits.append(f"{s.speed_available:.0f}/{s.speed_required:g} mm/s")
        if bits:
            lines.append(f"{s.name[:3]} " + " · ".join(bits))
    return "\n".join(lines)


def _zigzag(items, x0, y0, x1, y1, n, amp, col, g):
    pts = [(x0, y0)]
    for i in range(1, n):
        t = i / n
        pts.append((x0 + (x1 - x0) * t + (amp if i % 2 else -amp) * (1 if abs(y1 - y0) > abs(x1 - x0) else 0), y0 + (y1 - y0) * t + (amp if i % 2 else -amp) * (1 if abs(x1 - x0) >= abs(y1 - y0) else 0)))
    pts.append((x1, y1))
    items.append({"t": "polyline", "pts": pts, "stroke": col, "sw": 1.2, "fill": "none", "g": g, "cls": "sym"})


def _arrow(items, x0, y0, x1, y1, col, g):
    items.append({"t": "line", "x1": x0, "y1": y0, "x2": x1, "y2": y1, "stroke": col, "sw": 1.4, "g": g, "cls": "sym"})
    import math
    ang = math.atan2(y1 - y0, x1 - x0)
    for s in (1, -1):
        items.append({"t": "line", "x1": x1, "y1": y1, "x2": x1 - 5 * math.cos(ang + s * 0.5), "y2": y1 - 5 * math.sin(ang + s * 0.5), "stroke": col, "sw": 1.4, "g": g, "cls": "sym"})


def _symbol(items, comp, cx, cy, w, h, col, g) -> None:
    k = comp.kind
    box = lambda x, y, bw, bh: items.append({"t": "rect", "x": x, "y": y, "w": bw, "h": bh, "rx": 0, "fill": "#ffffff", "stroke": col, "sw": 1.4, "g": g, "cls": "sym"})
    if k == "cylinder":
        body_w = w - 26
        x0, y0 = cx - w / 2, cy - h / 2
        box(x0, y0, body_w, h)
        px = x0 + body_w * 0.3
        items.append({"t": "rect", "x": px - 3, "y": y0 + 2, "w": 6, "h": h - 4, "rx": 0, "fill": col, "stroke": "none", "sw": 0, "g": g, "cls": "sym"})
        items.append({"t": "rect", "x": px + 3, "y": cy - 2.5, "w": x0 + w - (px + 3), "h": 5, "rx": 0, "fill": "#ffffff", "stroke": col, "sw": 1.2, "g": g, "cls": "sym"})
        if comp.acting == "single":
            _zigzag(items, px + 10, cy - 1, x0 + body_w - 3, cy - 1, 8, 6, col, g)
        return
    if k == "valve":
        n = len(comp.positions)
        bw = VBOX
        x0 = cx - n * bw / 2
        for i, pos in enumerate(comp.positions):
            bx = x0 + i * bw
            box(bx, cy - h / 2, bw, h)
            at = {"P": (bx + bw / 2, cy + h / 2 - 3), "A": (bx + bw * 0.25, cy - h / 2 + 3), "B": (bx + bw * 0.75, cy - h / 2 + 3), "R": (bx + bw * 0.2, cy + h / 2 - 3), "S": (bx + bw * 0.8, cy + h / 2 - 3)}
            if comp.cls == "3/2":
                at["A"] = (bx + bw / 2, cy - h / 2 + 3)
            used = set()
            for a, b in pos:
                if a in at and b in at:
                    _arrow(items, *at[a], *at[b], col, g)
                    used.update((a, b))
            for p in comp.ports:
                if p not in used and p in at:
                    px, py = at[p]
                    items.append({"t": "line", "x1": px - 4, "y1": py, "x2": px + 4, "y2": py, "stroke": col, "sw": 1.4, "g": g, "cls": "sym"})
        # actuation on the left, return on the right
        ax = x0 - 16
        if comp.actuation == "solenoid":
            box(ax, cy - 7, 14, 14)
            items.append({"t": "line", "x1": ax, "y1": cy + 7, "x2": ax + 14, "y2": cy - 7, "stroke": col, "sw": 1.2, "g": g, "cls": "sym"})
        elif comp.actuation == "pilot":
            items.append({"t": "polyline", "pts": [(ax, cy - 6), (ax + 12, cy), (ax, cy + 6)], "closed": True, "fill": "none", "stroke": col, "sw": 1.2, "g": g, "cls": "sym"})
        else:
            items.append({"t": "line", "x1": ax + 2, "y1": cy + 8, "x2": ax + 12, "y2": cy - 8, "stroke": col, "sw": 1.4, "g": g, "cls": "sym"})
            items.append({"t": "line", "x1": ax - 2, "y1": cy + 8, "x2": ax + 6, "y2": cy + 8, "stroke": col, "sw": 1.4, "g": g, "cls": "sym"})
        rx = x0 + n * bw
        if comp.returns == "spring":
            _zigzag(items, rx + 1, cy, rx + 16, cy, 6, 5, col, g)
        elif comp.returns == "solenoid":
            box(rx + 2, cy - 7, 14, 14)
            items.append({"t": "line", "x1": rx + 2, "y1": cy + 7, "x2": rx + 16, "y2": cy - 7, "stroke": col, "sw": 1.2, "g": g, "cls": "sym"})
        else:
            items.append({"t": "polyline", "pts": [(rx + 2, cy - 6), (rx + 8, cy), (rx + 14, cy - 6)], "fill": "none", "stroke": col, "sw": 1.2, "g": g, "cls": "sym"})
        return
    if k in ("frl", "regulator"):
        sq = 22.0
        x0 = cx - w / 2
        cells = (["filter", "regulator"] + (["lubricator"] if comp.lubricator else [])) if k == "frl" else ["regulator"]   # draw what is fitted
        if k == "frl":
            items.append({"t": "rect", "x": x0 - 2, "y": cy - h / 2, "w": w + 4, "h": h, "rx": 0, "fill": "none", "stroke": col, "sw": 0.8, "dash": "3 2", "g": g, "cls": "sym"})
        gap = (w - sq * len(cells)) / (len(cells) + 1)
        for i, cell in enumerate(cells):
            bx = x0 + gap + i * (sq + gap)
            box(bx, cy - sq / 2, sq, sq)
            if cell == "filter":
                items.append({"t": "polyline", "pts": [(bx + sq / 2, cy - sq / 2 + 2), (bx + sq - 2, cy), (bx + sq / 2, cy + sq / 2 - 2), (bx + 2, cy)], "closed": True, "fill": "none", "stroke": col, "sw": 1, "g": g, "cls": "sym"})
                items.append({"t": "line", "x1": bx + 4, "y1": cy, "x2": bx + sq - 4, "y2": cy, "stroke": col, "sw": 1, "dash": "2 2", "g": g, "cls": "sym"})
            elif cell == "regulator":
                _arrow(items, bx + 3, cy + sq / 2 - 3, bx + sq - 3, cy - sq / 2 + 3, col, g)
                _zigzag(items, bx + sq / 2, cy - sq / 2 - 1, bx + sq / 2, cy - sq / 2 - 9, 4, 3, col, g)
            else:
                items.append({"t": "circle", "cx": bx + sq / 2, "cy": cy + 3, "r": 4, "fill": "none", "stroke": col, "sw": 1, "g": g, "cls": "sym"})
            if i < len(cells) - 1:
                items.append({"t": "line", "x1": bx + sq, "y1": cy, "x2": bx + sq + gap, "y2": cy, "stroke": col, "sw": 1.2, "g": g, "cls": "sym"})
        items.append({"t": "line", "x1": cx - w / 2, "y1": cy, "x2": x0 + gap, "y2": cy, "stroke": col, "sw": 1.2, "g": g, "cls": "sym"})
        items.append({"t": "line", "x1": x0 + gap + len(cells) * sq + (len(cells) - 1) * gap, "y1": cy, "x2": cx + w / 2, "y2": cy, "stroke": col, "sw": 1.2, "g": g, "cls": "sym"})
        return
    if k == "supply":
        items.append({"t": "circle", "cx": cx, "cy": cy, "r": w / 2, "fill": "#ffffff", "stroke": col, "sw": 1.4, "g": g, "cls": "sym"})
        items.append({"t": "polyline", "pts": [(cx - 7, cy + 5), (cx + 7, cy + 5), (cx, cy - 7)], "closed": True, "fill": col, "stroke": "none", "sw": 0, "g": g, "cls": "sym"})
        return
    if k == "flow_control":
        box(cx - w / 2, cy - h / 2, w, h)
        items.append({"t": "polyline", "pts": [(cx - 9, cy - 10), (cx - 3, cy), (cx - 9, cy + 10)], "fill": "none", "stroke": col, "sw": 1.2, "g": g, "cls": "sym"})
        items.append({"t": "polyline", "pts": [(cx + 3, cy - 10), (cx - 3, cy), (cx + 3, cy + 10)], "fill": "none", "stroke": col, "sw": 1.2, "g": g, "cls": "sym"})
        items.append({"t": "circle", "cx": cx + 9, "cy": cy, "r": 3.5, "fill": "none", "stroke": col, "sw": 1, "g": g, "cls": "sym"})
        items.append({"t": "line", "x1": cx + 6, "y1": cy + 4, "x2": cx + 12, "y2": cy + 4, "stroke": col, "sw": 1, "g": g, "cls": "sym"})
        return
    if k == "silencer":
        box(cx - w / 2, cy - h / 2, w, h)
        for i in range(3):
            items.append({"t": "line", "x1": cx - w / 2 + 2, "y1": cy - h / 2 + 4 + i * 4, "x2": cx + w / 2 - 2, "y2": cy - h / 2 + 4 + i * 4, "stroke": col, "sw": 0.8, "g": g, "cls": "sym"})
        return
    items.append({"t": "circle", "cx": cx, "cy": cy, "r": 3.5, "fill": col, "stroke": "none", "sw": 0, "g": g, "cls": "sym"})
