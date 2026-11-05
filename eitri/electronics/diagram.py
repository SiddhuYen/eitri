"""The schematic: parts as boxes (resistors, LEDs, diodes, switches and capacitors as their symbols) in columns from the board and
the supplies on the left to what they drive on the right; rails and grounds as flags at each pin (a named stub, a ground symbol);
every other net as orthogonal wires routed around the parts (eitri.route), named beside its wire. Labels go through the shared
placer (eitri.diagram.place_labels). Parts carry data-kind="part", nets data-kind="net", for the editor."""
from __future__ import annotations

from ..diagram import FAIL, FONT, INK, MUTED, WARN, Scene, _wrap, footer_from, place_labels, scene_to_png, scene_to_svg, status_from, text_w   # noqa: F401
from ..route import Router
from .model import Circuit, PinRef, amps_text

MARGIN = 28
TOP = 64
ROW = 18
COL_GAP = 170
V_GAP = 46
STUB = 18
SYMBOL = {"resistor", "led", "diode", "switch", "capacitor"}
WIRE = "#1f5fbf"
RAIL = "#b42318"
GND = "#344054"
TWO = {"resistor": ("1", "2"), "led": ("A", "K"), "diode": ("A", "K"), "switch": ("1", "2"), "capacitor": ("1", "2")}


def _shown_pins(c: Circuit, p) -> list[str]:
    """The pins a part shows: all of a small part's, a board's or module's connected and required ones."""
    if p.kind in ("board", "module") and len(p.pins) > 8:
        return [n for n, ps in p.pins.items() if PinRef(p.name, n) in c._net_of or ps.required]
    return list(p.pins)


def _column(c: Circuit, an) -> dict[str, int]:
    col: dict[str, int] = {}
    for p in c.parts.values():
        if p.kind in ("board", "supply", "regulator"):
            col[p.name] = 0
    if not col and c.parts:
        col[next(iter(c.parts))] = 0
    # breadth first through every net that is not a rail or a ground
    frontier = list(col)
    while frontier:
        nxt = []
        for name in frontier:
            for pin in c.parts[name].pins:
                n = c._net_of.get(PinRef(name, pin))
                if not n or an.nets[n].kind in ("rail", "gnd"):
                    continue
                for r in c.nets[n].pins:
                    if r.part not in col:
                        col[r.part] = col[name] + 1
                        nxt.append(r.part)
        frontier = nxt
    for p in c.parts.values():                       # powered only (a servo on a rail): next to the board
        col.setdefault(p.name, 1)
    return col


def _chains(c: Circuit, an, col: dict[str, int]) -> dict[str, str]:
    """Series symbols drawn on one row: a symbol whose only signal neighbour is a symbol one column back, on a net of just those
    two pins, follows it (part -> the part it follows)."""
    follow: dict[str, str] = {}
    for p in sorted(c.parts.values(), key=lambda q: col[q.name]):
        if p.kind not in SYMBOL or col[p.name] < 1:
            continue
        for pin in p.pins:
            n = c._net_of.get(PinRef(p.name, pin))
            if not n or an.nets[n].kind in ("rail", "gnd", "short", "driven"):
                continue
            pins = c.nets[n].pins
            if len(pins) != 2:
                continue
            other = next(r.part for r in pins if r.part != p.name)
            if c.parts[other].kind in SYMBOL and col[other] == col[p.name] - 1 and other not in follow.values():
                follow[p.name] = other
                break
    return follow


def _side(c: Circuit, an, p, pin: str, col: dict[str, int]) -> str:
    if p.kind in TWO:
        return "left" if pin == TWO[p.kind][0] else "right"
    n = c._net_of.get(PinRef(p.name, pin))
    if not n or an.nets[n].kind in ("rail", "gnd"):
        return "left" if col[p.name] > 0 else "right"
    others = [col[r.part] for r in c.nets[n].pins if r.part != p.name]
    if not others:
        return "right" if col[p.name] == 0 else "left"
    avg = sum(others) / len(others)
    if avg > col[p.name]:
        return "right"
    if avg < col[p.name]:
        return "left"
    return "right"


def rkm(ohm: float) -> str:
    """A resistance in RKM code (IEC 60062): 330R, 4k7, 10k, 1M."""
    for k, u in ((1e6, "M"), (1e3, "k"), (1.0, "R")):
        if ohm >= k or u == "R":
            v = ohm / k
            whole = int(v)
            frac = round((v - whole) * 10)
            if frac == 10:
                whole, frac = whole + 1, 0
            return f"{whole}{u}{frac}" if frac and whole < 10 else f"{v:.3g}{u}" if u != "R" or v != whole else f"{whole}{u}"
    return f"{ohm:g}R"


def _plain(s: str) -> str:
    """Text the PNG's built-in font can draw: no Ω or µ."""
    return str(s).replace(" Ω", " ohm").replace("Ω", " ohm").replace("µ", "u")


def _title(p) -> str:
    if p.kind == "resistor":
        return f"{p.name} {rkm(float(p.spec['ohm']))}" + (f" {p.spec['power_W']:g}W" if p.spec.get("power_W") not in (None, 0.25) else "")
    if p.kind in ("led", "capacitor", "switch"):
        return f"{p.name} {p.value_text}"
    if p.kind == "diode":
        return f"{p.name} {p.catalog}"
    return f"{p.name} · {p.part_number or p.catalog or p.kind}" if (p.part_number or p.catalog) else f"{p.name} · {p.value_text}"


def _sub(c: Circuit, an, p) -> str:
    i = an.part_current(p.name) if an else 0
    if p.kind == "supply":
        return f"{p.spec['volts']:g} V" + (f", {p.spec['current_A']:g} A max" if p.spec.get("current_A") else "")
    if p.kind == "module" and p.spec.get("address") is not None:
        return f"I2C {hex(int(p.spec['address']))}"
    if p.kind in ("servo", "load", "motor", "solenoid") and p.spec.get("current_A"):
        return f"{amps_text(p.spec['current_A'])}"
    if p.kind == "relay":
        return f"coil {p.spec['coil_v']:g} V, {p.spec['coil_ohm']:g} Ω"
    if p.kind in ("led", "resistor") and i and i != float("inf"):
        return amps_text(i)
    return ""


def build_scene(c: Circuit, rep=None, status: dict[str, str] | None = None, footer=None, title: str | None = None) -> Scene:
    from .analysis import analyze
    status = status or {}
    an = getattr(c, "_analysis", None) or analyze(c)
    col = _column(c, an)
    follow = _chains(c, an, col)
    head = {}
    for n in c.parts:
        h = n
        while h in follow:
            h = follow[h]
        head[n] = h
    for n in follow:                                  # a follower takes its chain head's column (it is drawn to its right)
        col[n] = col[head[n]]
    if col:                                           # close the columns emptied by the chains
        used = sorted(set(col.values()))
        remap = {k: i for i, k in enumerate(used)}
        col = {n: remap[k] for n, k in col.items()}
    boxes: dict[str, dict] = {}
    # sizes
    for p in c.parts.values():
        pins = _shown_pins(c, p)
        if p.kind in SYMBOL:
            w, h = (64.0, 24.0)
            left, right = [TWO[p.kind][0]], [TWO[p.kind][1]]
        else:
            left = [n for n in pins if _side(c, an, p, n, col) == "left"]
            right = [n for n in pins if n not in left]
            lw = max([text_w(n, FONT - 2) for n in left] + [0])
            rw = max([text_w(n, FONT - 2) for n in right] + [0])
            w = max(70.0, lw + rw + 34)
            h = 12.0 + ROW * max(len(left), len(right), 1)
        boxes[p.name] = {"w": w, "h": h, "left": left, "right": right}
    # columns: x by the widest box of each column; y stacked, ordered by the neighbours' rows in the column before
    ncol = max(col.values(), default=0) + 1
    def chain_order(n):
        out, cur = [], n
        while True:
            nxt = [m for m, f in follow.items() if f == cur]
            if not nxt:
                return out
            out.append(nxt[0])
            cur = nxt[0]
    unit_w = {n: boxes[n]["w"] + sum(boxes[m]["w"] + 56 for m in chain_order(n)) for n in c.parts if n not in follow}
    widths = [max([unit_w[n] for n in col if col[n] == k and n not in follow] + [60]) for k in range(ncol)]
    xs, x = [], MARGIN + 60
    for k in range(ncol):
        xs.append(x)
        x += widths[k] + COL_GAP
    ypos: dict[str, float] = {}
    for k in range(ncol):
        members = [n for n in c.parts if col[n] == k and n not in follow]
        if k == 0:
            order = {"board": 0, "supply": 1, "regulator": 2}
            members.sort(key=lambda n: order.get(c.parts[n].kind, 3))
        else:
            def bary(n):
                ys = []
                for pin in c.parts[n].pins:
                    net = c._net_of.get(PinRef(n, pin))
                    if net and an.nets[net].kind not in ("rail", "gnd"):
                        ys += [ypos[r.part] for r in c.nets[net].pins if r.part in ypos and col[r.part] < k]
                return sum(ys) / len(ys) if ys else 1e9
            members.sort(key=bary)
        y = TOP
        for n in members:
            h = max(boxes[m]["h"] for m in [n] + chain_order(n))
            ypos[n] = y
            for m in chain_order(n):
                ypos[m] = y + (boxes[n]["h"] - boxes[m]["h"]) / 2
            y += h + V_GAP + (14 if c.parts[n].kind not in SYMBOL else 10)
    pos = {}
    for n in c.parts:
        if n in follow:
            continue
        x0 = xs[col[n]] + (widths[col[n]] - unit_w[n]) / 2
        pos[n] = [x0, ypos[n]]
        cx = pos[n][0] + boxes[n]["w"] + 56
        for m in chain_order(n):
            pos[m] = [cx, ypos[m]]
            cx += boxes[m]["w"] + 56
    for n, (lx, ly) in c.layout.items():
        if n in pos:
            pos[n] = [lx, ly]
    # pin anchors
    anchors: dict[PinRef, tuple[float, float, str]] = {}
    for n, b in boxes.items():
        x0, y0 = pos[n]
        p = c.parts[n]
        if p.kind in SYMBOL:
            anchors[PinRef(n, b["left"][0])] = (x0, y0 + b["h"] / 2, "left")
            anchors[PinRef(n, b["right"][0])] = (x0 + b["w"], y0 + b["h"] / 2, "right")
            continue
        for k, pin in enumerate(b["left"]):
            anchors[PinRef(n, pin)] = (x0, y0 + 6 + ROW * k + ROW / 2, "left")
        for k, pin in enumerate(b["right"]):
            anchors[PinRef(n, pin)] = (x0 + b["w"], y0 + 6 + ROW * k + ROW / 2, "right")
    width = max([pos[n][0] + boxes[n]["w"] for n in pos] + [400.0]) + MARGIN + 90
    height = max([pos[n][1] + boxes[n]["h"] for n in pos] + [200.0]) + MARGIN + 40
    sc = Scene(width, height)
    items = sc.items
    items.append({"t": "text", "x": MARGIN, "y": 24, "s": title or c.name, "size": 14, "weight": "700", "fill": INK, "g": "title"})
    rails = sorted(n for n, i in an.nets.items() if i.kind in ("rail", "short"))
    if rails:
        items.append({"t": "text", "x": MARGIN, "y": 42, "s": "rails: " + ", ".join(f"{n} {an.nets[n].volts:g} V" for n in rails), "size": FONT - 2, "fill": MUTED, "g": "title"})

    obstacles = [(pos[n][0], pos[n][1], b["w"], b["h"]) for n, b in boxes.items()]
    # flags: rails and grounds at each pin, a stub and a name / a ground symbol
    flags = []
    gnd_nets = [n for n, i in an.nets.items() if i.kind == "gnd"]
    for ref, (ax, ay, side) in anchors.items():
        net = c._net_of.get(ref)
        if not net or an.nets[net].kind not in ("rail", "gnd", "short"):
            continue
        d = -1 if side == "left" else 1
        ex = ax + d * STUB
        st = status.get(net)
        colr = FAIL if st == "fail" else WARN if st == "warn" else (GND if an.nets[net].kind == "gnd" else RAIL)
        g = f"net:{net}"
        items.append({"t": "line", "x1": ax, "y1": ay, "x2": ex, "y2": ay, "stroke": colr, "sw": 1.6, "g": g, "cls": "wire"})
        if an.nets[net].kind == "gnd":
            items.append({"t": "line", "x1": ex, "y1": ay, "x2": ex, "y2": ay + 6, "stroke": colr, "sw": 1.6, "g": g, "cls": "wire"})
            for k, half in enumerate((7, 4.5, 2)):
                items.append({"t": "line", "x1": ex - half, "y1": ay + 6 + 3 * k, "x2": ex + half, "y2": ay + 6 + 3 * k, "stroke": colr, "sw": 1.4, "g": g, "cls": "gnd"})
            obstacles.append((ex - 8, ay - 2, 16, 16))
            if len(gnd_nets) > 1:
                flags.append((net, ex, ay, d, colr, g, "gnd"))
        else:
            items.append({"t": "line", "x1": ex, "y1": ay - 5, "x2": ex, "y2": ay + 5, "stroke": colr, "sw": 2, "g": g, "cls": "flag"})
            lab_w = text_w(net, FONT - 2) + 6
            obstacles.append((min(ex, ex + d * lab_w), ay - 8, lab_w + 4, 16))
            flags.append((net, ex, ay, d, colr, g, "rail"))
    for net, ex, ay, d, colr, g, kind in flags:
        lab = net
        anchor = "start" if d > 0 else "end"
        x0 = ex + d * 4
        if kind == "gnd":
            cands = [(ex + 10, ay + 18, "start"), (ex - 10, ay + 18, "end"), (ex, ay + 26, "middle")]
        else:
            cands = [(x0, ay + 4, anchor), (x0, ay - 7, anchor), (x0, ay + 15, anchor), (ex, ay - 9, "middle"), (ex, ay + 18, "middle")]
        items.append({"t": "text", "x": cands[0][0], "y": cands[0][1], "s": lab, "size": FONT - 2, "weight": "700", "fill": colr, "anchor": cands[0][2],
                      "g": g, "cls": "flag", "bg": "#ffffff", "cands": cands})

    # signal nets: wires from pin to pin, routed around the parts
    router = Router(obstacles, (8, 30, width + 40, height + 40))
    order = []
    for n, info in an.nets.items():
        if info.kind in ("rail", "gnd", "short"):
            continue
        pins = [r for r in c.nets[n].pins if r in anchors]
        if len(pins) < 2:
            continue
        pins.sort(key=lambda r: (anchors[r][0], anchors[r][1]))
        order.append((n, pins))
    order.sort(key=lambda t: sum(abs(anchors[a][0] - anchors[b][0]) + abs(anchors[a][1] - anchors[b][1]) for a, b in zip(t[1], t[1][1:])))
    for n, pins in order:
        st = status.get(n)
        colr = FAIL if st == "fail" else WARN if st == "warn" else WIRE
        g = f"net:{n}"
        segs = []
        for a, b in zip(pins, pins[1:]):
            pa, pb = anchors[a], anchors[b]
            pts = router.route((pa[0], pa[1], pa[2], 12.0), (pb[0], pb[1], pb[2], 12.0))
            if not pts:
                pts = [(pa[0], pa[1]), (pb[0], pb[1])]
            segs.append(pts)
            items.append({"t": "polyline", "pts": pts, "stroke": colr, "sw": 1.8 if not st else 2.8, "fill": "none", "g": g, "cls": "wire"})
        if len(pins) > 2:
            for pts in segs[1:]:
                items.append({"t": "circle", "cx": pts[0][0], "cy": pts[0][1], "r": 2.6, "fill": colr, "stroke": colr, "sw": 1, "g": g, "cls": "dot"})
        if not c.nets[n].auto:
            longest = max(segs, key=lambda s: sum(abs(q[0] - p_[0]) + abs(q[1] - p_[1]) for p_, q in zip(s, s[1:])))
            items.append({"t": "text", "x": longest[0][0], "y": longest[0][1], "s": n, "size": FONT - 2, "fill": colr if st else MUTED, "anchor": "middle",
                          "g": g, "cls": "label", "bg": "#ffffff", "cands": _beside(longest, n, FONT - 2)})

    # parts
    for n, b in boxes.items():
        p = c.parts[n]
        x0, y0 = pos[n]
        st = status.get(n)
        colr = FAIL if st == "fail" else WARN if st == "warn" else INK
        g = f"part:{n}"
        w, h = b["w"], b["h"]
        sc.nodes[n] = {"kind": "part", "x": x0, "y": y0, "w": w, "h": h}
        if p.kind in SYMBOL:
            items.append({"t": "rect", "x": x0, "y": y0, "w": w, "h": h, "rx": 3, "fill": "#ffffff", "alpha_fill": 0.0, "stroke": "none", "sw": 0, "g": g, "cls": "hit"})
            _symbol(items, p, x0, y0, w, h, colr if st else INK, g, st)
        else:
            fill = "#f4f8f4" if p.kind == "board" else "#fff8f1" if p.kind in ("supply", "regulator") else "#fbfcfd"
            items.append({"t": "rect", "x": x0, "y": y0, "w": w, "h": h, "rx": 5, "fill": fill, "stroke": colr if st else "#3a4656", "sw": 1.2 if not st else 2.2,
                          "g": g, "cls": "part"})
            for k, pin in enumerate(b["left"]):
                items.append({"t": "text", "x": x0 + 6, "y": y0 + 6 + ROW * k + ROW / 2 + 3.5, "s": pin, "size": FONT - 2, "fill": MUTED, "g": g, "cls": "pin"})
            for k, pin in enumerate(b["right"]):
                items.append({"t": "text", "x": x0 + w - 6, "y": y0 + 6 + ROW * k + ROW / 2 + 3.5, "s": pin, "size": FONT - 2, "fill": MUTED, "anchor": "end", "g": g, "cls": "pin"})
        label = _title(p)
        lw = text_w(label, FONT - 1)
        cx = x0 + w / 2
        items.append({"t": "text", "x": cx, "y": y0 - 6, "s": label, "size": FONT - 1, "weight": "700", "fill": colr, "anchor": "middle", "g": g, "cls": "label",
                      "bg": "#ffffff", "cands": [(cx, y0 - 6, "middle"), (cx, y0 - 18, "middle"), (cx, y0 + h + 14, "middle"), (x0 + w + 8, y0 + 10, "start"),
                                                 (x0 - 8, y0 + 10, "end"), (cx - lw / 2, y0 - 6, "middle"), (cx + lw / 2, y0 - 6, "middle"), (cx, y0 + h + 28, "middle")]})
        sub = _sub(c, an, p)
        if sub:
            items.append({"t": "text", "x": cx, "y": y0 + h + 13, "s": sub, "size": FONT - 3, "fill": FAIL if st == "fail" else MUTED, "anchor": "middle", "g": g,
                          "cls": "sub", "bg": "#ffffff", "cands": [(cx, y0 + h + 13, "middle"), (cx, y0 + h + 26, "middle"), (x0 + w + 8, y0 + h - 2, "start"),
                                                                    (x0 - 8, y0 + h - 2, "end"), (cx, y0 - 20, "middle"), (cx, y0 + h + 40, "middle")]})
    for it in items:
        if it["t"] == "text":
            it["s"] = _plain(it["s"])
    place_labels(sc)
    sc.height = max(sc.height, max((it.get("y", 0) for it in items if it["t"] == "text"), default=0) + 14)
    if footer:
        y = sc.height + 6
        sc.footer_top = y
        items.append({"t": "line", "x1": MARGIN, "y1": y, "x2": width - MARGIN, "y2": y, "stroke": "#c5ccd6", "sw": 1, "g": "footer"})
        y += 18
        for sev, text in footer:
            colr = {"fail": FAIL, "warn": "#b25e00", "pass": "#1a7f4b", "head": INK}.get(sev, INK)
            for k, ln in enumerate(_wrap(_plain(text), int((width - 2 * MARGIN) / (FONT * 0.58)))):
                items.append({"t": "text", "x": MARGIN + (0 if k == 0 else 14), "y": y, "s": ln, "size": FONT, "weight": "700" if sev == "head" else "400", "fill": colr, "g": "footer"})
                y += 15
        sc.height = y + 8
    return sc


def _beside(pts, label: str, size: float) -> list[tuple]:
    """Where a net's name may go: beside its own segments (the longest first), never on them, with a tether to the wire."""
    w = text_w(label, size)
    segs = sorted(zip(pts, pts[1:]), key=lambda s: -(abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1])))
    out = []
    for (x1, y1), (x2, y2) in segs:
        for f in (0.5, 0.3, 0.7, 0.15, 0.85):
            mx, my = x1 + (x2 - x1) * f, y1 + (y2 - y1) * f
            if abs(y1 - y2) < 0.5:
                if abs(x2 - x1) >= w * 0.6:
                    out += [(mx, my - 5, "middle", None, (mx, my - 5, mx, my)), (mx, my + 13, "middle", None, (mx, my + 3, mx, my))]
            else:
                out += [(mx + 6, my + 4, "start", None, (mx + 6, my, mx, my)), (mx - 6, my + 4, "end", None, (mx - 6, my, mx, my))]
    return out or [(pts[0][0] + 6, pts[0][1] - 5, "start")]


def _symbol(items, p, x0, y0, w, h, col, g, st) -> None:
    cy = y0 + h / 2
    sw = 1.6 if not st else 2.4
    k = p.kind
    lead = 14
    items.append({"t": "line", "x1": x0, "y1": cy, "x2": x0 + lead, "y2": cy, "stroke": col, "sw": sw, "g": g, "cls": "sym"})
    items.append({"t": "line", "x1": x0 + w - lead, "y1": cy, "x2": x0 + w, "y2": cy, "stroke": col, "sw": sw, "g": g, "cls": "sym"})
    a, b = x0 + lead, x0 + w - lead
    if k == "resistor":
        n = 6
        pts = [(a, cy)] + [(a + (b - a) * (i + 0.5) / n, cy + (-5 if i % 2 == 0 else 5)) for i in range(n)] + [(b, cy)]
        items.append({"t": "polyline", "pts": pts, "stroke": col, "sw": sw, "fill": "none", "g": g, "cls": "sym"})
    elif k in ("led", "diode"):
        m = (a + b) / 2
        items.append({"t": "line", "x1": a, "y1": cy, "x2": m - 7, "y2": cy, "stroke": col, "sw": sw, "g": g, "cls": "sym"})
        items.append({"t": "line", "x1": m + 7, "y1": cy, "x2": b, "y2": cy, "stroke": col, "sw": sw, "g": g, "cls": "sym"})
        items.append({"t": "polyline", "pts": [(m - 7, cy - 7), (m + 6, cy), (m - 7, cy + 7)], "closed": True, "fill": col if k == "diode" else "#ffffff",
                      "stroke": col, "sw": sw, "g": g, "cls": "sym"})
        items.append({"t": "line", "x1": m + 7, "y1": cy - 7, "x2": m + 7, "y2": cy + 7, "stroke": col, "sw": sw, "g": g, "cls": "sym"})
        if k == "led":
            for dx in (-2, 3):
                items.append({"t": "line", "x1": m + dx, "y1": cy - 8, "x2": m + dx + 4, "y2": cy - 12, "stroke": col, "sw": 1.1, "g": g, "cls": "sym"})
    elif k == "switch":
        items.append({"t": "line", "x1": a, "y1": cy, "x2": a + 4, "y2": cy, "stroke": col, "sw": sw, "g": g, "cls": "sym"})
        items.append({"t": "line", "x1": a + 4, "y1": cy, "x2": b - 4, "y2": cy - 8, "stroke": col, "sw": sw, "g": g, "cls": "sym"})
        items.append({"t": "line", "x1": b - 4, "y1": cy, "x2": b, "y2": cy, "stroke": col, "sw": sw, "g": g, "cls": "sym"})
    elif k == "capacitor":
        m = (a + b) / 2
        items.append({"t": "line", "x1": a, "y1": cy, "x2": m - 3, "y2": cy, "stroke": col, "sw": sw, "g": g, "cls": "sym"})
        items.append({"t": "line", "x1": m + 3, "y1": cy, "x2": b, "y2": cy, "stroke": col, "sw": sw, "g": g, "cls": "sym"})
        for dx in (-3, 3):
            items.append({"t": "line", "x1": m + dx, "y1": cy - 8, "x2": m + dx, "y2": cy + 8, "stroke": col, "sw": sw + 0.4, "g": g, "cls": "sym"})
