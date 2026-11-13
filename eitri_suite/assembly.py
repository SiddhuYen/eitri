"""The assembly of a combined project: where its harness lands on its CAD model, the wires' 3D paths, the board's pins, and the
firmware's pick-and-place sequence as joint poses. Computed in the build (so it is checked and recorded) and written as the suite
extension's data; the Assembly view in the editor draws exactly this.

HOW THE DOMAINS MEET
  anchors        the CAD model declares `anchor(name, at, z=, x=, part=)` (core 0.5.4). An anchor named exactly as a harness
                 connector or splice places it (case does not matter): a board connector (its pins are named like a board's: D9,
                 GND, 5V) gets the board's layout, so each pin has its own point; any other connector is a housing with its pins
                 along the anchor's x axis at the connector's pitch; a splice is one point. A connector with no anchor is unplaced:
                 the view never invents a position, and the check below fails.
  wire paths     a wire runs from pin point to pin point. Wires of a declared INTEGRATION route follow its waypoints; any other
                 wire leaves each pin straight along the pin's direction (LEAD_MM), then follows a Catmull-Rom spline through a
                 sagging middle, lifted over any part bounding box it would cross (CLEARANCE_MM). The page draws the same spline
                 (same control points, same sampling), so what is checked is what is shown.
  sequence       the firmware file may declare SEQUENCE = [("home", {"YAW": 90, "SHOULDER": 90}), ("pick", {...}), ...]: the servo
                 angles it writes, step by step, and COUPLING joints={"YAW": "yaw"} maps each servo to a mechanism joint
                 (joint angle = sign * (servo angle - zero); zero 90 and sign +1 unless COUPLING gives servo_zero / servo_sign).

THE CHECKS (category "assembly" on a CAD or harness build; "pinout" / "sequence" on a firmware build)
  connectors anchored   every harness connector and splice has a CAD anchor; the unplaced ones are named.
  cut lengths           every spline-routed wire's cut length covers its 3D path (fail) plus the service loop (warn). Wires of a
                        declared route are graded by the route's own check.
  pins wired            every firmware pin has a wire on the matching pin of the harness's board connector (no COUPLING needed).
  sequence              every step's servo angles are within 0-180, and within the joint's limits once mapped.
  wire paths            every wire's path reaches its two pins, is at most DETOUR_MAX x the distance between them, and a routed wire
                        spans at most SPAN_MAX_MM in free air from its bundle to its pin: at the build pose, and (a coupled mechanism)
                        at home and at every SEQUENCE step, re-routed as the view re-routes it.
  board on its mounts   a placed board whose CAD stand-in lies flat on a surface (over FLUSH_MAX of its footprint) warns.
  drawn once            data["replaces"]: a placed board's CAD stand-in, which the view hides where it draws the board.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np

from eitri.harness.checks import HCheck
from eitri.harness.model import Harness, PinRef

CLEARANCE_MM = 2.0           # a free wire keeps this far from any part's bounding box
LEAD_MM = 6.0                # a wire leaves a pin straight along the pin's direction for this long before it bends
SAG = 0.06                   # the free middle of a wire hangs down by this fraction of its span
SEGMENTS = 16                # spline samples per span; the page draws with the same number
SERVICE_LOOP_PCT = 10.0
HEADER_H = 8.5               # a female header's top above the PCB: where a jumper's plug lands
DETOUR_MAX = 3.0             # a wire path longer than this many times the distance between its ends is badly off ...
DETOUR_MIN_MM = 60.0         # ... once it is also this much longer (short jumpers loop around a header and are fine)
SPAN_MAX_MM = 90.0           # a routed wire leaving its bundle spans at most this far in free air to its pin
END_TOL_MM = 0.5             # a path's ends are its pins' points, within this
FLUSH_EPS_MM = 0.2           # a board's underside is probed this far down for what it lies on ...
FLUSH_MAX = 0.25             # ... and more than this share of its footprint in contact is flush, not on standoffs


def _check(category: str, name: str, severity: str, measured: str, expected: str = "", refs=()) -> HCheck:
    return HCheck(name=name, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category=category, refs=list(dict.fromkeys(refs)))


# ---------------------------------------------------------------------------- boards (mm; the frame's origin at the centre of the PCB's
# top face, x along the long side with the USB jack at -x, z up; pin points at the top of the headers)

def _uno() -> dict:
    """Arduino Uno R3, from the reference drawing: 2.7 x 2.1 in, headers on a 100 mil pitch, the digital header's 160 mil gap."""
    mil, cx, cy = 0.0254, 1350, 1050
    pins: dict[str, list[float]] = {}
    top = [("SCL", 740), ("SDA", 840), ("AREF", 940), ("GND2", 1040), ("D13", 1140), ("D12", 1240), ("D11", 1340), ("D10", 1440), ("D9", 1540), ("D8", 1640),
           ("D7", 1800), ("D6", 1900), ("D5", 2000), ("D4", 2100), ("D3", 2200), ("D2", 2300), ("D1", 2400), ("D0", 2500)]
    bottom = [("NC", 1100), ("IOREF", 1200), ("RESET", 1300), ("3V3", 1400), ("5V", 1500), ("GND", 1600), ("GND3", 1700), ("VIN", 1800),
              ("A0", 2000), ("A1", 2100), ("A2", 2200), ("A3", 2300), ("A4", 2400), ("A5", 2500)]
    for name, x in top:
        pins[name] = [round((x - cx) * mil, 3), round((2000 - cy) * mil, 3), HEADER_H]
    for name, x in bottom:
        pins[name] = [round((x - cx) * mil, 3), round((100 - cy) * mil, 3), HEADER_H]
    return {"key": "uno", "name": "Arduino Uno R3", "size": [68.58, 53.34, 1.6], "header_h": HEADER_H, "pins": pins,
            "aliases": {"TX": "D1", "RX": "D0", "TX1": "D1", "RX0": "D0", "3.3V": "3V3", "RST": "RESET"},
            "headers": [[(740 - cx) * mil, (2000 - cy) * mil, 10], [(1800 - cx) * mil, (2000 - cy) * mil, 8],
                        [(1100 - cx) * mil, (100 - cy) * mil, 8], [(2000 - cx) * mil, (100 - cy) * mil, 6]],         # [x of the first pin, y, pins]
            "features": {"usb": {"at": [-34.29 + 8.0, (1700 - cy) * mil, 0], "size": [16.0, 12.0, 11.0]},
                         "jack": {"at": [-34.29 + 7.0, (250 - cy) * mil, 0], "size": [14.0, 9.0, 11.0]},
                         "mcu": {"at": [(2000 - cx) * mil, (1000 - cy) * mil, 0], "size": [35.0, 10.0, 4.0]}},
            "holes": [[(550 - cx) * mil, (100 - cy) * mil], [(600 - cx) * mil, (2000 - cy) * mil], [(2600 - cx) * mil, (1400 - cy) * mil], [(2600 - cx) * mil, (300 - cy) * mil]]}


def _nano() -> dict:
    """Arduino Nano: 15 pins a side on 100 mil, the rows 600 mil apart, the USB at the -x end."""
    pins: dict[str, list[float]] = {}
    xs = [round(-17.78 + 2.54 * i, 3) for i in range(15)]
    left = ["D12", "D11", "D10", "D9", "D8", "D7", "D6", "D5", "D4", "D3", "D2", "GND", "RESET", "D0", "D1"]
    right = ["D13", "3V3", "AREF", "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "5V", "RESET2", "GND2", "VIN"]
    for i, n in enumerate(left):
        pins[n] = [xs[i], 7.62, HEADER_H]
    for i, n in enumerate(right):
        pins[n] = [xs[i], -7.62, HEADER_H]
    return {"key": "nano", "name": "Arduino Nano", "size": [43.18, 17.78, 1.6], "header_h": HEADER_H, "pins": pins,
            "aliases": {"TX": "D1", "RX": "D0", "TX1": "D1", "RX0": "D0", "3.3V": "3V3", "RST": "RESET"},
            "headers": [[xs[0], 7.62, 15], [xs[0], -7.62, 15]],
            "features": {"usb": {"at": [-21.59 + 4.0, 0, 0], "size": [8.0, 7.5, 4.0]}, "mcu": {"at": [2.0, 0, 0], "size": [7.0, 7.0, 1.0]}}, "holes": []}


BOARDS: dict[str, dict] = {"uno": _uno(), "nano": _nano()}


def board_layout(key: str | None) -> dict | None:
    """The layout for a firmware board key ("uno", "nano", "nano-every", ...), or None for a board we do not draw."""
    k = str(key or "").lower()
    if k.startswith("uno"):
        return BOARDS["uno"]
    if k.startswith("nano"):
        return BOARDS["nano"]
    return None


def board_pin(layout: dict, name: str) -> list[float] | None:
    """A pin's point in the board frame, by any of its names (D9, d9, 5V, GND, TX...)."""
    n = str(name).strip().upper()
    n = layout["aliases"].get(n, n)
    if n in layout["pins"]:
        return layout["pins"][n]
    if n.isdigit() and f"D{n}" in layout["pins"]:            # a number means a digital pin, as on the board
        return layout["pins"][f"D{n}"]
    return None


_IO_PIN = re.compile(r"^(D\d{1,2}|A\d|\d{1,2})$", re.I)


def looks_like_board(connector, layout: dict | None = None) -> bool:
    """A harness connector whose pins are named like a board's I/O pins (two or more of D9, A0, 13...; with a layout, two or more
    that the layout has). Power pins alone (GND, 5V, 3V3) do not make a board: every module has those."""
    return sum(1 for p in connector.pin_names if _IO_PIN.match(str(p)) and (layout is None or board_pin(layout, p) is not None)) >= 2


# ---------------------------------------------------------------------------- frames and placement

def _unit(v) -> np.ndarray:
    a = np.array(v, dtype=float)
    n = float(np.linalg.norm(a))
    return a / n if n > 1e-12 else np.array([0.0, 0.0, 1.0])


def frame(a: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(origin, x, y, z) of an anchor: z faces out (up when not given), x in-plane (chosen when not given), y = z × x."""
    at = np.array(a["at"], dtype=float)
    z = _unit(a.get("z") or [0, 0, 1])
    x = a.get("x")
    if x is None:
        ref = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        x = ref - z * float(ref @ z)
    x = _unit(x)
    return at, x, np.cross(z, x), z


def to_world(fr, local) -> list[float]:
    at, x, y, z = fr
    p = at + x * float(local[0]) + y * float(local[1]) + z * float(local[2])
    return [round(float(c), 3) for c in p]


def placements(anchors: list[dict], harness: Harness, board_key: str | None = None, board_connector: str | None = None) -> dict[str, Any]:
    """Where each connector and splice sits: {placed: {name: {anchor, kind, ...}}, unplaced: [names], pins: {"CONN.PIN": {at, dir}},
    board: {key, connector} | None}. The board connector (the one the firmware's COUPLING names, else the one whose pins are
    named like the board's) gets the board's pin layout; the others their pins along the anchor's x."""
    by = {str(a["name"]).lower(): a for a in anchors}
    placed: dict[str, dict] = {}
    unplaced: list[str] = []
    pins: dict[str, dict] = {}
    board = None
    for c in harness.connectors.values():
        a = by.get(c.name.lower())
        # the board: the firmware's board, or (no firmware file yet) an Uno, a Nano when the connector says so
        layout = board_layout(board_key) if board_key else (board_layout("nano") if "nano" in c.name.lower() else board_layout("uno"))
        is_board = layout is not None and board is None and looks_like_board(c, layout) and (board_connector is None or c.name == board_connector)
        if is_board:
            board = {"key": layout["key"], "connector": c.name}
        if a is None:
            unplaced.append(c.name)
            continue
        fr = frame(a)
        if is_board and layout is not None:
            placed[c.name] = {"anchor": a["name"], "kind": "board", "board": layout["key"], "part": a.get("part")}
            for p in c.pin_names:
                local = board_pin(layout, p)
                if local is not None:
                    pins[f"{c.name}.{p}"] = {"at": to_world(fr, local), "dir": [round(float(v), 4) for v in fr[3]]}
            continue
        pitch = float(c.pitch or 2.54)
        names = list(c.pin_names)
        placed[c.name] = {"anchor": a["name"], "kind": "connector", "pins": len(names), "pitch": pitch, "part": a.get("part")}
        for i, p in enumerate(names):
            pins[f"{c.name}.{p}"] = {"at": to_world(fr, ((i - (len(names) - 1) / 2) * pitch, 0, 0)), "dir": [round(float(v), 4) for v in fr[3]]}
    for s in harness.splices.values():
        a = by.get(s.name.lower())
        if a is None:
            unplaced.append(s.name)
            continue
        fr = frame(a)
        placed[s.name] = {"anchor": a["name"], "kind": "splice", "part": a.get("part")}
        pins[s.name] = {"at": to_world(fr, (0, 0, 0)), "dir": [round(float(v), 4) for v in fr[3]]}
    return {"placed": placed, "unplaced": unplaced, "pins": pins, "board": board}


# ---------------------------------------------------------------------------- wire paths

def catmull_rom(ctrl: list[np.ndarray], n: int = SEGMENTS) -> list[np.ndarray]:
    """A uniform Catmull-Rom spline through the control points, n samples per span (the page uses the very same formula)."""
    if len(ctrl) < 2:
        return list(ctrl)
    P = [ctrl[0]] + list(ctrl) + [ctrl[-1]]
    out = [ctrl[0]]
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for k in range(1, n + 1):
            t = k / n
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    return out


def polyline_length(points) -> float:
    pts = [np.array(p, dtype=float) for p in points]
    return float(sum(np.linalg.norm(b - a) for a, b in zip(pts, pts[1:])))


def _inside(p, lo, hi) -> bool:
    return bool(np.all(p >= lo) and np.all(p <= hi))


def _segment_box(a, b, lo, hi):
    """(t_in, t_out) where the segment a->b is inside the box, or None (slab test)."""
    d = b - a
    t0, t1 = 0.0, 1.0
    for i in range(3):
        if abs(d[i]) < 1e-12:
            if a[i] < lo[i] or a[i] > hi[i]:
                return None
            continue
        ta, tb = (lo[i] - a[i]) / d[i], (hi[i] - a[i]) / d[i]
        if ta > tb:
            ta, tb = tb, ta
        t0, t1 = max(t0, ta), min(t1, tb)
        if t0 > t1:
            return None
    return (t0, t1)


def route(p0, d0, p1, d1, boxes: list[tuple], clearance: float = CLEARANCE_MM) -> list[list[float]]:
    """The free path of a wire from pin point p0 (facing d0) to p1 (facing d1): a straight lead out of each pin, then a spline
    through a sagging middle, lifted over every part box the straight run would cross (boxes holding a lead point are the wire's
    own parts and are ignored). Returns the sampled polyline."""
    p0, p1 = np.array(p0, dtype=float), np.array(p1, dtype=float)
    a, b = p0 + _unit(d0) * LEAD_MM, p1 + _unit(d1) * LEAD_MM
    span = float(np.linalg.norm(b - a))
    inner: list[tuple[float, np.ndarray]] = []
    lifted = False
    for lo, hi in boxes:
        lo, hi = np.array(lo, dtype=float) - clearance, np.array(hi, dtype=float) + clearance
        if _inside(a, lo, hi) or _inside(b, lo, hi):
            continue
        hit = _segment_box(a, b, lo, hi)
        if hit is None:
            continue
        top = hi[2] + clearance
        for t in hit:
            q = a + (b - a) * t
            inner.append((t, np.array([q[0], q[1], max(q[2], top)])))
        lifted = True
    if lifted:
        inner.sort(key=lambda x: x[0])
        pts = [p for _, p in inner][:6]
    else:
        pts = [(a + b) / 2 - np.array([0.0, 0.0, SAG * span])]
    ctrl = [p0, a] + pts + [b, p1]
    return [[round(float(c), 3) for c in p] for p in catmull_rom(ctrl)]


def wire_paths(harness: Harness, pins: dict[str, dict], boxes: list[tuple], routes: list[dict] | None) -> dict[str, dict]:
    """{wire id: {points, length_mm, source}} for every wire whose ends are both placed. Wires of a declared route follow its
    waypoints (pin -> waypoints -> pin); the rest are routed free."""
    out: dict[str, dict] = {}
    in_route: dict[str, dict] = {}
    for r in routes or []:
        wps = [np.array(p, dtype=float) for p in (r.get("waypoints") or []) if len(p) == 3]
        if len(wps) >= 2:
            for w in r.get("wires") or []:
                in_route.setdefault(str(w), {"name": str(r.get("name") or "route"), "waypoints": wps})

    def key(e) -> str:
        return f"{e.connector}.{e.pin}" if isinstance(e, PinRef) else str(e.splice)

    for l in harness.links:
        pa, pb = pins.get(key(l.a)), pins.get(key(l.b))
        if pa is None or pb is None:
            continue
        r = in_route.get(l.id)
        if r is not None:
            wps = r["waypoints"]
            a, b = np.array(pa["at"]), np.array(pb["at"])
            if np.linalg.norm(a - wps[0]) > np.linalg.norm(a - wps[-1]):        # the waypoints are listed from the other end
                wps = list(reversed(wps))
            k = int(np.argmin([np.linalg.norm(w - b) for w in wps]))             # the wire leaves the bundle at the waypoint nearest its end
            pts = [[round(float(c), 3) for c in p] for p in [a, *wps[:k + 1], b]]
            out[l.id] = {"points": pts, "length_mm": round(polyline_length(pts), 1), "source": "route:" + r["name"]}
        else:
            pts = route(pa["at"], pa["dir"], pb["at"], pb["dir"], boxes)
            out[l.id] = {"points": pts, "length_mm": round(polyline_length(pts), 1), "source": "spline"}
    return out


def part_boxes(parts) -> dict[str, list[list[float]]]:
    from eitri import measure
    out = {}
    for p in parts:
        try:
            bb = measure.bounding_box(p.shape)
            out[p.name] = [[round(float(bb.min.X), 3), round(float(bb.min.Y), 3), round(float(bb.min.Z), 3)],
                           [round(float(bb.max.X), 3), round(float(bb.max.Y), 3), round(float(bb.max.Z), 3)]]
        except Exception:                                   # noqa: BLE001 - a part that cannot be measured is not an obstacle
            continue
    return out


# ---------------------------------------------------------------------------- CAD <-> harness: the assembly data and its checks

def part_under(boxes_by: dict[str, list], at) -> str | None:
    """The smallest part whose bounding box holds the point: the part an anchor without part= belongs to (the view's partAt)."""
    p = np.asarray(at, dtype=float)
    best, vol = None, float("inf")
    for name, (lo, hi) in boxes_by.items():
        lo_, hi_ = np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)
        if np.all(p >= lo_ - 1e-6) and np.all(p <= hi_ + 1e-6):
            v = float(np.prod(np.maximum(hi_ - lo_, 1e-9)))
            if v < vol:
                best, vol = name, v
    return best


def components(placed: dict, anchors: list[dict], harness: Harness) -> list[dict]:
    """The placed harness and firmware bodies for the attachment check: [{name, kind, part, at}] for every connector with a
    housing (a BARE wire end is not a body), splice and board, with the part its anchor names (or sits in) and the anchor point."""
    by = {str(a["name"]).lower(): a for a in anchors}
    out = []
    for name, pl in (placed or {}).items():
        c = harness.connectors.get(name)
        if c is not None and str(getattr(c, "type", "")).upper() == "BARE":
            continue
        a = by.get(str(pl.get("anchor")).lower())
        out.append({"name": name, "kind": pl.get("kind") or "connector", "part": pl.get("part"), "at": list(a["at"]) if a else None})
    return out


def component_check(parts, comps: list[dict]) -> HCheck:
    """Every harness component sits on a part (its anchor on the part it names, within ATTACH_TOL_MM): the attachment check of a
    project with no mechanism to move the parts (with one, eitri_suite.kinematics.attachment_check covers the components too)."""
    from .kinematics import ATTACH_TOL_MM, _point_dist
    by_name = {p.name: p for p in parts}
    problems, refs = [], []
    for c in comps:
        what = {"connector": "connector", "splice": "splice", "board": "board"}.get(c["kind"], "connector")
        if not c["part"] or c["part"] not in by_name:
            problems.append(f"{what} {c['name']} floats: no part under its anchor (give part= in the anchor, on the part it is mounted on)")
            refs.append(c["name"])
            continue
        d = _point_dist(np.asarray(c["at"], dtype=float), by_name[c["part"]].shape) if c["at"] is not None else 0.0
        if d > ATTACH_TOL_MM:
            problems.append(f"{what} {c['name']} is {d:.1f} mm off its part {c['part']}")
            refs += [c["name"], c["part"]]
    if problems:
        return _check("assembly", "every harness component sits on a part", "fail", "; ".join(problems[:6]) + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else ""),
                      f"each connector, splice and board on the part its anchor names, within {ATTACH_TOL_MM:g} mm", refs)
    return _check("assembly", "every harness component sits on a part", "pass",
                  f"{len(comps)} component{'s' if len(comps) != 1 else ''} on their parts: " + ", ".join(f"{c['name']} on {c['part']}" for c in comps[:8]) + ("…" if len(comps) > 8 else ""), "", [c["name"] for c in comps])


def run(parts, anchors: list[dict], harness: Harness, integration: dict | None, board_key: str | None, board_connector: str | None = None,
        poses: list[dict] | None = None, components_checked: bool = False) -> tuple[list[HCheck], dict]:
    """The assembly of a CAD model and a harness: placements, pin points, wire paths, and their checks. `poses` (from a coupled
    mechanism, see posed_pins): [{label, T: {part: 4x4}}], the poses the wire segments are also checked at. `components_checked`:
    the coupled attachment check covers the components, so the component check here is left out."""
    boxes_by = part_boxes(parts)
    place = placements(anchors, harness, board_key, board_connector)
    for pl in place["placed"].values():                      # an anchor without part=: the part it sits in, as the view draws it
        if not pl.get("part"):
            a = next((x for x in anchors if str(x["name"]).lower() == str(pl["anchor"]).lower()), None)
            pl["part"] = part_under(boxes_by, a["at"]) if a else None
    routes = [r for r in ((integration or {}).get("routes") or []) if isinstance(r, dict)]
    paths = wire_paths(harness, place["pins"], list(boxes_by.values()), routes)
    data = {"anchors": anchors, "placed": place["placed"], "unplaced": place["unplaced"], "pins": place["pins"], "board": place["board"],
            "paths": paths, "boxes": boxes_by, "replaces": stand_ins(parts, anchors, place, board_key),
            "routes": [{"name": str(r.get("name") or "route"), "wires": [str(w) for w in r.get("wires") or []],
                        "waypoints": [[float(c) for c in p] for p in (r.get("waypoints") or []) if len(p) == 3],
                        "through": [dict(t) for t in (r.get("through") or []) if isinstance(t, dict)]} for r in routes]}
    checks: list[HCheck] = []
    n_conn, n_spl = len(harness.connectors), len(harness.splices)
    if place["unplaced"]:
        checks.append(_check("assembly", "every harness connector has a CAD anchor", "fail",
                             f"no anchor for {', '.join(place['unplaced'])}: declare anchor(\"{place['unplaced'][0]}\", at=..., z=...) in the CAD model, named as the connector",
                             "an anchor named as each connector and splice", place["unplaced"]))
    else:
        checks.append(_check("assembly", "every harness connector has a CAD anchor", "pass",
                             f"{n_conn} connector{'s' if n_conn != 1 else ''}" + (f" and {n_spl} splice{'s' if n_spl != 1 else ''}" if n_spl else "") + " placed at anchors"
                             + (f"; {place['board']['connector']} is the {board_layout(place['board']['key'])['name']}" if place["board"] and place["board"].get("key") else ""),
                             "", list(place["placed"])))
    for name, part in data["replaces"].items():
        c = board_rest_check(parts, place["placed"][name], part, anchors)
        if c is not None:
            checks.append(c)
    if not components_checked:
        checks.append(component_check(parts, components(place["placed"], anchors, harness)))
    part_of_pin = {k: (place["placed"].get(k.split(".")[0]) or {}).get("part") for k in place["pins"]}
    checks.append(segment_check(harness, paths, place["pins"], poses=poses, boxes=boxes_by, routes=routes, part_of_pin=part_of_pin))
    loop = SERVICE_LOOP_PCT / 100.0
    free = [(l, paths[l.id]) for l in harness.links if l.id in paths and paths[l.id]["source"] == "spline"]
    short = [(l, p) for l, p in free if l.length_mm < p["length_mm"]]
    tight = [(l, p) for l, p in free if p["length_mm"] <= l.length_mm < p["length_mm"] * (1 + loop)]
    if free:
        if short:
            checks.append(_check("assembly", "wire cut lengths cover their 3D paths", "fail",
                                 "; ".join(f"{l.id}: path {p['length_mm']:.0f} mm, cut {l.length_mm:g} mm" for l, p in short[:6]) + (f" (+{len(short) - 6} more)" if len(short) > 6 else ""),
                                 "each cut length at least its path", [l.id for l, _ in short]))
        elif tight:
            checks.append(_check("assembly", "wire cut lengths cover their 3D paths", "warn",
                                 "; ".join(f"{l.id}: path {p['length_mm']:.0f} mm, cut {l.length_mm:g} mm" for l, p in tight[:6]) + f": under the {SERVICE_LOOP_PCT:g}% service loop",
                                 f"each cut length at least its path + {SERVICE_LOOP_PCT:g}%", [l.id for l, _ in tight]))
        else:
            margin = min(l.length_mm - p["length_mm"] for l, p in free)
            checks.append(_check("assembly", "wire cut lengths cover their 3D paths", "pass",
                                 f"{len(free)} wire{'s' if len(free) != 1 else ''} routed through the model; the shortest margin is {margin:.0f} mm", "", [l.id for l, _ in free]))
    return checks, data


# ---------------------------------------------------------------------------- wire segments: the path agrees with its ends

def _segment_problems(harness: Harness, paths: dict[str, dict], pins: dict[str, dict], label: str) -> list[tuple[str, str, str]]:
    """(severity, wire id, text) for each wire whose path is badly off from the distance between its two ends."""
    def key(e) -> str:
        return f"{e.connector}.{e.pin}" if isinstance(e, PinRef) else str(e.splice)
    out = []
    for l in harness.links:
        p = paths.get(l.id)
        a, b = pins.get(key(l.a)), pins.get(key(l.b))
        if p is None or a is None or b is None:
            continue
        pts = [np.array(x, dtype=float) for x in p["points"]]
        pa, pb = np.array(a["at"], dtype=float), np.array(b["at"], dtype=float)
        d = float(np.linalg.norm(pb - pa))
        L = polyline_length(pts)
        off = max(float(np.linalg.norm(pts[0] - pa)), float(np.linalg.norm(pts[-1] - pb)))
        where = f" ({label})" if label else ""
        if off > END_TOL_MM or L + END_TOL_MM < d:
            out.append(("fail", l.id, f"{l.id}: its path does not reach its pins{where}: ends {off:.1f} mm off, {L:.0f} mm long for {d:.0f} mm between the pins"))
            continue
        if L > DETOUR_MAX * d and L - d > DETOUR_MIN_MM:
            out.append(("warn", l.id, f"{l.id}: routed {L:.0f} mm for {d:.0f} mm between its ends{where}"))
        if str(p.get("source", "")).startswith("route:") and len(pts) >= 2:
            span = max(float(np.linalg.norm(pts[0] - pts[1])), float(np.linalg.norm(pts[-1] - pts[-2])))
            if span > SPAN_MAX_MM:
                out.append(("warn", l.id, f"{l.id}: leaves its bundle and spans {span:.0f} mm in free air to its pin{where}"))
    return out


def posed_paths(harness: Harness, pins: dict[str, dict], paths: dict[str, dict], part_of_pin: dict[str, str | None], T: dict[str, np.ndarray],
                boxes: dict[str, list], routes: list[dict]) -> tuple[dict, dict]:
    """(pins, paths) at a pose: each pin moved with its part's transform (T: part -> 4x4), routed wires keeping their bundle's
    waypoints and free wires routed again around the moved parts' boxes: what the Assembly view draws at that pose."""
    moved: dict[str, dict] = {}
    for k, p in pins.items():
        M = T.get(part_of_pin.get(k) or "")
        if M is None:
            moved[k] = p
            continue
        at = (M @ np.append(np.array(p["at"], dtype=float), 1.0))[:3]
        moved[k] = {"at": [round(float(c), 3) for c in at], "dir": [round(float(c), 4) for c in M[:3, :3] @ np.array(p["dir"], dtype=float)]}
    bx = []
    for n, (lo, hi) in boxes.items():
        M = T.get(n)
        if M is None:
            bx.append((lo, hi))
            continue
        c = np.array([[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]) @ M.T
        bx.append((c[:, :3].min(axis=0).tolist(), c[:, :3].max(axis=0).tolist()))

    def key(e) -> str:
        return f"{e.connector}.{e.pin}" if isinstance(e, PinRef) else str(e.splice)
    out = {}
    for l in harness.links:
        p = paths.get(l.id)
        a, b = moved.get(key(l.a)), moved.get(key(l.b))
        if p is None or a is None or b is None:
            continue
        if str(p.get("source", "")).startswith("route:"):
            pts = [a["at"], *p["points"][1:-1], b["at"]]
        else:
            pts = route(a["at"], a["dir"], b["at"], b["dir"], bx)
        out[l.id] = {"points": pts, "length_mm": round(polyline_length(pts), 1), "source": p.get("source")}
    return moved, out


def segment_check(harness: Harness, paths: dict[str, dict], pins: dict[str, dict], poses: list[dict] | None = None, boxes: dict | None = None,
                  routes: list[dict] | None = None, part_of_pin: dict[str, str | None] | None = None) -> HCheck:
    problems = _segment_problems(harness, paths, pins, "")
    for pose in poses or []:
        mp, pp = posed_paths(harness, pins, paths, pose.get("part_of_pin") or part_of_pin or {}, pose["T"], boxes or {}, routes or [])
        seen = {w for _s, w, _t in problems}
        problems += [x for x in _segment_problems(harness, pp, mp, pose["label"]) if x[1] not in seen]
    n = len(paths)
    if not problems:
        return _check("assembly", "wire paths agree with the distance between their ends", "pass",
                      f"{n} wire path{'s' if n != 1 else ''} reach their pins, none more than {DETOUR_MAX:g}x the distance between its ends, no routed span over {SPAN_MAX_MM:g} mm"
                      + (f", at the build pose and {len(poses)} more" if poses else ""), "", list(paths))
    sev = "fail" if any(s == "fail" for s, _w, _t in problems) else "warn"
    return _check("assembly", "wire paths agree with the distance between their ends", sev, "; ".join(t for _s, _w, t in problems[:6]) + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else ""),
                  f"each path from pin to pin, at most {DETOUR_MAX:g}x the distance between them, routed spans under {SPAN_MAX_MM:g} mm", [w for _s, w, _t in problems])


# ---------------------------------------------------------------------------- one thing drawn once: a CAD stand-in for a placed board

def stand_ins(parts, anchors: list[dict], place: dict, board_key: str | None) -> dict[str, str]:
    """{placed connector: CAD part} for a board the Assembly view draws (with its real pins) where the CAD model already has a body
    standing in for the same board: the anchor's part, whose bounding box is the drawn PCB's (under the anchor) within 3 mm on every
    side. The view draws the board once, in place of that body; the body stays in the model and in every check."""
    out: dict[str, str] = {}
    by_name = {p.name: p for p in parts}
    anchors_by = {str(a["name"]).lower(): a for a in anchors}
    for name, pl in (place.get("placed") or {}).items():
        if pl.get("kind") != "board" or not pl.get("part") or pl["part"] not in by_name:
            continue
        layout = BOARDS.get(pl.get("board") or "")
        a = anchors_by.get(str(pl.get("anchor")).lower())
        if layout is None or a is None:
            continue
        o, x, y, z = frame(a)
        from eitri import measure
        try:
            bb = measure.bounding_box(by_name[pl["part"]].shape)
        except Exception:                                   # noqa: BLE001
            continue
        lo, hi = np.array([bb.min.X, bb.min.Y, bb.min.Z]), np.array([bb.max.X, bb.max.Y, bb.max.Z])
        L, Wd, T = layout["size"]
        corners = np.array([o + x * cx + y * cy + z * cz for cx in (-L / 2, L / 2) for cy in (-Wd / 2, Wd / 2) for cz in (-T, 0.0)])   # the drawn PCB: under the anchor
        if np.all(np.abs(corners.min(axis=0) - lo) <= 3.0) and np.all(np.abs(corners.max(axis=0) - hi) <= 3.0):
            out[name] = pl["part"]
    return out


def board_rest_check(parts, placed: dict, part: str, anchors: list[dict]) -> HCheck | None:
    """A board resting flat on a surface (the enclosure floor, the plate) instead of on standoffs: its underside in contact over more
    than FLUSH_MAX of its footprint. (A board sunk INTO a surface is an overlap: the coupling check "bodies do not interfere at rest".)"""
    from eitri import measure
    by_name = {p.name: p for p in parts}
    a = {str(x["name"]).lower(): x for x in anchors}.get(str(placed.get("anchor")).lower())
    board = by_name.get(part)
    if a is None or board is None:
        return None
    _o, _x, _y, z = frame(a)
    try:
        foot = float(measure.bounding_box(board.shape).size.X * measure.bounding_box(board.shape).size.Y) if abs(z[2]) > 0.9 else None
        probe = board.shape.moved(__import__("build123d").Location(tuple(float(c) for c in -z * FLUSH_EPS_MM)))
    except Exception:                                       # noqa: BLE001
        return None
    worst = None
    for p in parts:
        if p.name == part:
            continue
        try:
            v = float(measure.overlap_volume(probe, p.shape)) - float(measure.overlap_volume(board.shape, p.shape))
        except Exception:                                   # noqa: BLE001
            continue
        area = v / FLUSH_EPS_MM
        if area > 1.0 and (worst is None or area > worst[1]):
            worst = (p.name, area)
    if foot is None or worst is None or worst[1] <= FLUSH_MAX * foot:
        return _check("assembly", f"{part} rests on its mounts", "pass", f"{part}'s underside touches {worst[0]} over {worst[1]:.0f} mm^2 ({worst[1] / foot:.0%} of its footprint)" if worst and foot else f"{part}'s underside is clear: it rests on its mounts", "", [part])
    return _check("assembly", f"{part} rests on its mounts", "warn",
                  f"{part} lies flat on {worst[0]}: {worst[1]:.0f} mm^2 of its underside ({worst[1] / foot:.0%} of its footprint) in contact; put it on standoffs so it clears the surface",
                  f"contact under {FLUSH_MAX:.0%} of the footprint (standoffs)", [part, worst[0]])


# ---------------------------------------------------------------------------- firmware <-> harness: the pins are wired

def pin_wiring_checks(fw, harness: Harness, coupling: dict | None) -> list[HCheck]:
    """Every firmware pin has a wire on the matching pin of the harness's board connector (named in COUPLING pins, else the one
    whose pins are named like a board's). Works without COUPLING: the pinout check needs the mapping, this one only the names."""
    board = fw.board
    layout = board_layout(fw.board_key)
    named = {str(v).split(".", 1)[0] for v in ((coupling or {}).get("pins") or {}).values() if "." in str(v)}
    conns = [c for c in harness.connectors.values() if c.name in named] or [c for c in harness.connectors.values() if looks_like_board(c, layout)]
    if not conns:
        return [_check("pinout", "every firmware pin is wired in the harness", "fail",
                       "the harness has no board connector (one whose pins are named like the board's: D9, GND, 5V)", "a board connector in the harness", [])]
    conn = conns[0]
    pins_on = {str(p).upper(): str(p) for p in conn.pin_names}
    for alias, real in (("TX", "D1"), ("RX", "D0"), ("3.3V", "3V3"), ("RST", "RESET")):
        if alias in pins_on and real not in pins_on:
            pins_on[real] = pins_on[alias]
    missing, unwired, ok = [], [], []
    for p in fw.pins:
        cap = board.resolve(p.pin) if board else None
        name = (cap.name if cap else str(p.pin)).upper()
        alt = f"D{name}" if name.isdigit() else name
        on = pins_on.get(name) or pins_on.get(alt)
        if on is None:
            missing.append(f"{p.pin} ({p.net})")
            continue
        ref = f"{conn.name}.{on}"
        if not harness.links_at(PinRef(conn.name, on)):
            unwired.append(ref + f" ({p.net})")
        else:
            ok.append(ref)
    if missing or unwired:
        bits = []
        if missing:
            bits.append(f"{conn.name} has no pin for " + ", ".join(missing))
        if unwired:
            bits.append("nothing is wired to " + ", ".join(unwired))
        return [_check("pinout", "every firmware pin is wired in the harness", "fail", "; ".join(bits), f"a wire on each firmware pin of {conn.name}",
                       [f"{conn.name}.{m.split(' ')[0]}" for m in missing] + [u.split(" ")[0] for u in unwired])]
    return [_check("pinout", "every firmware pin is wired in the harness", "pass", f"{len(ok)} pin{'s' if len(ok) != 1 else ''} of {conn.name} wired: " + ", ".join(ok), "", ok)]


# ---------------------------------------------------------------------------- the firmware's sequence as joint poses

def sequence(module, fw, mech=None) -> tuple[list[HCheck], dict | None]:
    """SEQUENCE and COUPLING joints of a firmware file -> (checks, data). data: {steps: [{name, servo: {net: deg}, joints: {joint: deg}}],
    joints: {net: {joint, zero, sign}}}, or None when the file declares no SEQUENCE."""
    seq = getattr(module, "SEQUENCE", None)
    if seq is None:
        return [], None
    coupling = getattr(module, "COUPLING", None)
    coupling = coupling if isinstance(coupling, dict) else {}
    jmap = coupling.get("joints") or {}
    zero = coupling.get("servo_zero") or {}
    sign = coupling.get("servo_sign") or {}
    servos = {p.name: p for p in fw.peripherals if p.kind == "servo"}
    problems: list[str] = []
    steps: list[dict] = []
    if not isinstance(seq, (list, tuple)) or not seq:
        return [_check("sequence", "SEQUENCE is a list of steps", "fail", "SEQUENCE must be a non-empty list of (name, {servo: angle}) steps",
                       'SEQUENCE = [("home", {"YAW": 90}), ...]')], None
    joints_info = {net: {"joint": str(j), "zero": float(zero.get(net, 90.0)), "sign": float(sign.get(net, 1.0))} for net, j in jmap.items()}
    limits = {}
    if mech is not None:
        try:
            limits = {j.name: tuple(j.limit) for j in mech.joints if j.limit}
        except Exception:                                   # noqa: BLE001
            limits = {}
    for i, step in enumerate(seq):
        if isinstance(step, dict):
            name, angles = str(step.get("name") or f"step {i + 1}"), step.get("servos") or step.get("angles") or {k: v for k, v in step.items() if k != "name"}
        elif isinstance(step, (list, tuple)) and len(step) == 2 and isinstance(step[1], dict):
            name, angles = str(step[0]), step[1]
        else:
            problems.append(f"step {i + 1} is not (name, {{servo: angle}})")
            continue
        servo_deg, joint_deg = {}, {}
        for net, v in angles.items():
            net = str(net)
            try:
                deg = float(v)
            except (TypeError, ValueError):
                problems.append(f"{name}: {net} = {v!r} is not an angle")
                continue
            if net not in servos:
                problems.append(f"{name}: {net} is not a servo of the firmware (it has {', '.join(servos) or 'none'})")
                continue
            if not 0.0 <= deg <= 180.0:
                problems.append(f"{name}: {net} {deg:g}° is outside the servo's 0-180°")
            servo_deg[net] = deg
            ji = joints_info.get(net)
            if ji is not None:
                jd = ji["sign"] * (deg - ji["zero"])
                joint_deg[ji["joint"]] = round(jd, 2)
                lim = limits.get(ji["joint"])
                if lim and not (lim[0] - 1e-9 <= jd <= lim[1] + 1e-9):
                    problems.append(f"{name}: {net} {deg:g}° puts joint {ji['joint']} at {jd:g}°, outside its {lim[0]:g}..{lim[1]:g}°")
        steps.append({"name": name, "servos": servo_deg, "joints": joint_deg})
    unmapped = [n for n in servos if n not in jmap]
    checks: list[HCheck] = []
    if problems:
        checks.append(_check("sequence", "the sequence stays within the servos and the joints", "fail", "; ".join(problems[:6]) + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else ""),
                             "every step within 0-180° and the joint limits", [s["name"] for s in steps]))
    else:
        checks.append(_check("sequence", "the sequence stays within the servos and the joints", "pass",
                             f"{len(steps)} step{'s' if len(steps) != 1 else ''} ({', '.join(s['name'] for s in steps[:8])}{'…' if len(steps) > 8 else ''})" +
                             (f"; joints checked against {len(limits)} limits" if limits else "; no mechanism to check the joint limits against"), "", [s["name"] for s in steps]))
    if unmapped and jmap:
        checks.append(_check("sequence", "every servo maps to a mechanism joint", "warn", f"COUPLING joints does not map {', '.join(unmapped)}", "joints={'NET': 'joint', ...} for each servo", unmapped))
    return checks, {"steps": steps, "joints": joints_info, "source": "firmware"}
