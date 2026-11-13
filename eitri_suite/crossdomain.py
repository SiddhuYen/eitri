"""Cross-domain consistency checks: CAD <-> harness and harness <-> firmware.

They run, as ordinary checks of a build report, when the other artifact exists in the project folder (the suite extension calls
`project_checks`). The CAD side declares what it needs to know in a module-level INTEGRATION dict:

    INTEGRATION = dict(
        harness="wiring.py",                    # the harness model file in the same folder (default: the one harness file there)
        routes=[dict(name="servo leads", wires=["SH_SIG", "SH_V", ...],            # harness wire ids
                     waypoints=[[x, y, z], ...],                                    # the path through the model, mm
                     through=[dict(part="turret", at=[x, y, z], label="turret cable hole")],   # a pass-through hole, by a point on its axis
                     service_loop_pct=10, clearance_mm=1.0)],
        mating=[dict(connector="PCA_CH0", part="pwm_board", at=[x, y, z], normal=[0, 0, 1], approach_mm=20)],
    )

THE CHECKS
  route fit      each pass-through hole is found in the CAD model (measure.holes) and its measured diameter must be at least the
                 routed bundle's diameter plus the clearance; the wires must be as long as the route plus the service loop.
  mating face    a connector mated to a CAD-mounted component needs a free corridor in front of the component's mating face:
                 a box the connector's footprint wide and `approach_mm` long (default 20 mm: the plug body plus room for fingers)
                 must not intersect any other part; the free length found is reported.
  pinout         the firmware model (eitri.firmware) declares pins; its module-level COUPLING maps each pin's net to the harness connector
                 pin that carries it (COUPLING = dict(harness="wiring.py", pins={"PAN": "UNO.D9"})). Each mapped pin must exist in the
                 harness, be wired, and be the pin the firmware drives (a board connector's pins are named like the board's: D9, A4).

PHYSICAL CONSTANTS AND ESTIMATES (all flagged ESTIMATE in the check text)
  Wire outside diameter = conductor diameter (AWG formula, eitri.harness.catalog.awg_diameter_mm) + 2 x insulation wall; the wall is
  ESTIMATED at 0.30 mm up to 16 AWG and 0.50 mm above, typical of PVC/silicone hookup wire (thin-wall UL1007 is thinner, silicone thicker).
  Bundle diameter = sqrt(sum(OD^2) / fill), fill = 0.70: the area of a loosely packed round bundle is its wires' area over the fill
  factor (circle packing: hexagonal 0.907 is the limit, random loose bundles are 0.6-0.8). ESTIMATE; a tight pull needs more.
"""
from __future__ import annotations

import glob
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

from eitri import measure
from eitri.harness.catalog import awg_diameter_mm
from eitri.harness.checks import HCheck
from eitri.harness.model import Harness, PinRef, SpliceRef
from .projectfiles import model_files  # noqa: F401  (re-exported: where a project's CAD and harness model files are found)

FILL = 0.70
WALL_THIN_MM, WALL_THICK_MM = 0.30, 0.50
FINGER_MM = 10.0                 # the default approach depth is twice this: 20 mm


def _check(category: str, name: str, severity: str, measured: str, expected: str = "", refs=()) -> HCheck:
    return HCheck(name=name, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category=category, refs=list(dict.fromkeys(refs)))


# ---------------------------------------------------------------------------- finding the artifacts

def declared_counts(integration: dict | None) -> dict[str, dict[str, int]]:
    """What an INTEGRATION declaration contains, for the project guard."""
    if not isinstance(integration, dict):
        return {}
    return {"integration": {"routes": len(integration.get("routes") or []), "mating": len(integration.get("mating") or [])}}


def declared_pinout(coupling: dict | None) -> dict[str, dict[str, int]]:
    """What a firmware file's COUPLING pinout contains, for the project guard."""
    if not isinstance(coupling, dict):
        return {}
    return {"pinout": {"pins": len(coupling.get("pins") or {})}}


def wire_od_mm(gauge) -> float:
    """Outside diameter of insulated hookup wire, mm (ESTIMATE: the insulation wall is assumed, see the module docstring)."""
    d = awg_diameter_mm(gauge.awg) if gauge.awg is not None else math.sqrt(4 * gauge.mm2 / math.pi)
    return d + 2 * (WALL_THIN_MM if gauge.equiv_awg >= 16 else WALL_THICK_MM)


def bundle_diameter_mm(ods: list[float]) -> float:
    return math.sqrt(sum(o * o for o in ods) / FILL) if ods else 0.0


# ---------------------------------------------------------------------------- CAD <-> harness

def _find_hole(shape, at: np.ndarray, tol: float = 3.0):
    best = None
    for h in measure.holes(shape):
        a = np.array([h.axis.X, h.axis.Y, h.axis.Z])
        s = np.array([h.start.X, h.start.Y, h.start.Z])
        d = float(np.linalg.norm(np.cross(at - s, a)))             # distance from the point to the hole's axis line
        along = float((at - s) @ a)
        lo, hi = sorted((0.0, float(h.depth)))
        slack = max(0.0, lo - along, along - hi)                   # the point may be a little past either end of the hole
        score = d + slack
        if score <= tol and (best is None or score < best[0]):
            best = (score, h)
    return None if best is None else best[1]


def route_checks(parts, harness: Harness, routes: list[dict]) -> list[HCheck]:
    out: list[HCheck] = []
    by_name = {p.name: p for p in parts}
    for r in routes:
        rname = str(r.get("name") or "route")
        ids = [str(w) for w in r.get("wires") or []]
        missing = [w for w in ids if not any(l.id == w for l in harness.links)]
        if missing or not ids:
            out.append(_check("integration", f"route '{rname}' wires exist", "fail",
                              f"the harness has no wire {', '.join(missing)}" if missing else "the route lists no wires", "wire ids of the harness (connect(..., name=...))", [rname]))
            continue
        links = [harness.link(w) for w in ids]
        ods = [wire_od_mm(l.gauge) for l in links]
        bundle = bundle_diameter_mm(ods)
        clearance = float(r.get("clearance_mm", 1.0))
        need = bundle + clearance
        est = f"bundle of {len(ids)} wire(s) {bundle:.1f} mm (ESTIMATE: wire OD {min(ods):.1f}-{max(ods):.1f} mm, fill {FILL:g}) + {clearance:g} mm clearance = {need:.1f} mm"
        for t in r.get("through") or []:
            label = str(t.get("label") or f"{t.get('part')} pass-through")
            part = by_name.get(str(t.get("part")))
            at = np.array(t.get("at") or [0, 0, 0], dtype=float)
            name = f"route '{rname}' fits {label}"
            if part is None:
                out.append(_check("integration", name, "fail", f"part {t.get('part')!r} is not in the model", "", [rname]))
                continue
            if t.get("channel_mm") is not None:
                dia, what = float(t["channel_mm"]), f"declared {float(t['channel_mm']):g} mm channel"
            else:
                h = _find_hole(part.shape, at)
                if h is None:
                    out.append(_check("integration", name, "fail", f"no hole in {part.name} near ({at[0]:g}, {at[1]:g}, {at[2]:g}); the pass-through is not in the CAD model",
                                      "a hole through the part, or channel_mm= for a slot", [part.name, rname]))
                    continue
                if h.through is False:
                    out.append(_check("integration", name, "fail", f"the Ø{h.diameter:.1f} mm hole in {part.name} at ({at[0]:g}, {at[1]:g}, {at[2]:g}) is blind (depth {h.depth:.1f} mm), not a pass-through",
                                      "a through hole", [part.name, rname]))
                    continue
                dia, what = h.diameter, f"Ø{h.diameter:.1f} mm hole in {part.name} at ({at[0]:g}, {at[1]:g}, {at[2]:g})"
            ok = dia + 1e-9 >= need
            out.append(_check("integration", name, "pass" if ok else "fail",
                              (f"{what} fits: " if ok else f"{what} is too small: ") + est + (f" (margin {dia - need:.1f} mm)" if ok else f" (short by {need - dia:.1f} mm)"),
                              f"hole >= {need:.1f} mm", [part.name, rname] + ids))
        wp = [np.array(p, dtype=float) for p in r.get("waypoints") or []]
        if len(wp) >= 2:
            route_len = float(sum(np.linalg.norm(b - a) for a, b in zip(wp, wp[1:])))
            loop = float(r.get("service_loop_pct", 10)) / 100.0
            short = [l for l in links if l.length_mm < route_len]
            tight = [l for l in links if route_len <= l.length_mm < route_len * (1 + loop)]
            nm = f"route '{rname}' wire length"
            if short:
                out.append(_check("integration", nm, "fail",
                                  f"routed path is {route_len:.0f} mm but {', '.join(f'{l.id} is {l.length_mm:g} mm' for l in short[:5])}: too short to reach", f">= {route_len:.0f} mm", [l.id for l in short] + [rname]))
            elif tight:
                out.append(_check("integration", nm, "warn",
                                  f"routed path is {route_len:.0f} mm; {', '.join(f'{l.id} ({l.length_mm:g} mm)' for l in tight[:5])} leave under the {loop * 100:g}% service loop ({route_len * (1 + loop):.0f} mm)",
                                  f">= {route_len * (1 + loop):.0f} mm", [l.id for l in tight] + [rname]))
            else:
                out.append(_check("integration", nm, "pass", f"routed path {route_len:.0f} mm; every wire is at least {min(l.length_mm for l in links):g} mm (service loop {loop * 100:g}% = {route_len * (1 + loop):.0f} mm)", "", ids + [rname]))
    return out


def mating_checks(parts, harness: Harness, mating: list[dict]) -> list[HCheck]:
    import build123d as bd
    out: list[HCheck] = []
    by_name = {p.name: p for p in parts}
    for m in mating:
        cname, pname = str(m.get("connector")), str(m.get("part"))
        name = f"mating face {cname} on {pname}"
        conn = harness.connectors.get(cname)
        part = by_name.get(pname)
        if conn is None or part is None:
            out.append(_check("integration", name, "fail", (f"the harness has no connector {cname!r}" if conn is None else f"the model has no part {pname!r}"), "", [cname, pname]))
            continue
        at = np.array(m.get("at") or [0, 0, 0], dtype=float)
        n = np.array(m.get("normal") or [0, 0, 1], dtype=float)
        if np.linalg.norm(n) < 1e-9:
            out.append(_check("integration", name, "fail", "the mating normal is zero", "", [cname, pname]))
            continue
        n = n / np.linalg.norm(n)
        try:
            dist = measure.min_distance(bd.Vertex(*[float(x) for x in at]), part.shape)
        except Exception:
            dist = 0.0
        if dist > 1.0:
            out.append(_check("integration", name, "fail", f"the declared face point ({at[0]:g}, {at[1]:g}, {at[2]:g}) is {dist:.1f} mm from {pname}: not on the part", "within 1 mm of the part", [cname, pname]))
            continue
        approach = float(m.get("approach_mm") or 2 * FINGER_MM)
        size = m.get("size_mm") or [(conn.pitch or 2.54) * conn.pins + 2.0, 12.0]
        plane = bd.Plane(origin=tuple(float(x) for x in at), z_dir=tuple(float(x) for x in n))
        others = [p for p in parts if p.name != pname]

        def blocked(length: float):
            box = plane * bd.Pos(0, 0, length / 2 + 0.2) * bd.Box(float(size[0]), float(size[1]), length)
            bb = measure.bounding_box(box)
            for p in others:
                bo = measure.bounding_box(p.shape)
                if (bb.max.X < bo.min.X or bo.max.X < bb.min.X or bb.max.Y < bo.min.Y or bo.max.Y < bb.min.Y or bb.max.Z < bo.min.Z or bo.max.Z < bb.min.Z):
                    continue
                if measure.overlap_volume(box, p.shape) > 1e-6:
                    return p.name
            return None
        who = blocked(approach)
        if who is None:
            out.append(_check("integration", name, "pass", f"{size[0]:g} x {size[1]:g} mm corridor {approach:g} mm deep in front of the face at ({at[0]:g}, {at[1]:g}, {at[2]:g}) is clear", f"clear for {approach:g} mm", [cname, pname]))
            continue
        lo, hi = 0.0, approach                              # the free length: bisect on the corridor depth
        for _ in range(7):
            mid = (lo + hi) / 2
            if blocked(mid) is None:
                lo = mid
            else:
                hi = mid
        out.append(_check("integration", name, "fail",
                          f"{who} blocks the {size[0]:g} x {size[1]:g} mm approach to the face at ({at[0]:g}, {at[1]:g}, {at[2]:g}): {lo:.1f} mm free, the connector needs {approach:g} mm",
                          f"{approach:g} mm of free approach", [cname, pname, who]))
    return out


# ---------------------------------------------------------------------------- harness <-> pin map <-> firmware

def _components(h: Harness) -> dict[Any, int]:
    adj: dict[Any, set] = {}
    for l in h.links:
        adj.setdefault(l.a, set()).add(l.b)
        adj.setdefault(l.b, set()).add(l.a)
    comp, k = {}, 0
    for n in adj:
        if n in comp:
            continue
        stack = [n]
        comp[n] = k
        while stack:
            for m in adj[stack.pop()]:
                if m not in comp:
                    comp[m] = k
                    stack.append(m)
        k += 1
    return comp


def _ref(h: Harness, text: str):
    try:
        return h._endpoint(str(text))
    except Exception:
        return None


def _components(h: Harness) -> dict[Any, int]:
    adj: dict[Any, set] = {}
    for l in h.links:
        adj.setdefault(l.a, set()).add(l.b)
        adj.setdefault(l.b, set()).add(l.a)
    comp, k = {}, 0
    for n in adj:
        if n in comp:
            continue
        stack = [n]
        comp[n] = k
        while stack:
            for m in adj[stack.pop()]:
                if m not in comp:
                    comp[m] = k
                    stack.append(m)
        k += 1
    return comp


def _ref(h: Harness, text: str):
    try:
        return h._endpoint(str(text))
    except Exception:
        return None


def firmware_pin_checks(h: Harness, fw, pins: dict[str, str]) -> list[HCheck]:
    """Harness <-> firmware: the firmware model's pinout against the harness. `pins` maps a firmware pin's net (the name its sketch uses,
    PIN_<net>) to the harness connector pin that carries it, "CONNECTOR.PIN" (a board connector's pin is named like the board's pin:
    UNO.D9). Each mapped harness pin must exist, be wired, and be the pin the firmware drives; each firmware pin must be mapped."""
    out: list[HCheck] = []
    board = fw.board
    declared = {p.net: p for p in fw.pins}
    for net, ref in pins.items():
        label = f"firmware pin {net} matches the harness"
        p = declared.get(net)
        if p is None:
            out.append(_check("pinout", label, "fail", f"COUPLING maps {net!r}, which the firmware does not declare (it declares {', '.join(sorted(declared)) or 'no pins'})", "a declared firmware pin", [str(ref)]))
            continue
        ep = _ref(h, ref)
        if ep is None:
            out.append(_check("pinout", label, "fail", f"{net} is mapped to {ref!r}, which is not in the harness", "an existing connector pin", [str(ref)]))
            continue
        if not h.links_at(ep):
            out.append(_check("pinout", label, "fail", f"{ref} is not wired to anything in the harness", "a wire on the pin", [str(ref)]))
            continue
        cap = board.resolve(p.pin) if board else None
        pin_on_connector = str(ref).split(".", 1)[-1]
        want = board.resolve(pin_on_connector) if board else None
        if cap is not None and want is not None and cap.name != want.name:
            out.append(_check("pinout", label, "fail", f"the firmware drives {cap.name} for {net}, the harness wires it to {want.name} ({ref})", "the same pin on both sides", [str(ref)]))
        else:
            out.append(_check("pinout", label, "pass", f"{net}: firmware {p.pin} <-> harness {ref}, wired", "", [str(ref)]))
    for net, p in declared.items():
        if net not in pins:
            out.append(_check("pinout", f"firmware pin {net} is in the harness", "warn", f"{p.pin} ({net}) is declared in the firmware but not mapped to a harness pin", "every firmware pin mapped in COUPLING", []))
    return out


def harness_for_firmware(module, folder: Path):
    """(path, name) of the harness a firmware file couples to: COUPLING harness=, else the project's only harness file."""
    from .projectfiles import files_by_kind
    coupling = getattr(module, "COUPLING", None)
    coupling = coupling if isinstance(coupling, dict) else {}
    har_files = files_by_kind(folder).get("harness", [])
    hname = coupling.get("harness")
    hpath = folder / hname if hname else (har_files[0] if len(har_files) == 1 else None)
    return hpath, hname


class Pair:
    """A CAD model and a harness loaded side by side for the checks between them (the other one built once, whichever side is
    being built): parts, the CAD module's INTEGRATION, the harness, and the paths. `problem` is why the other side could not be
    loaded (its own build's failure), when it could not."""

    def __init__(self):
        self.parts = self.harness = self.integration = self.cad_module = None
        self.cad_path = self.har_path = None
        self.problem: str | None = None


def load_pair(model_path: Path, built: Any, kind: str, module=None) -> Pair | None:
    """The pair for a CAD or harness build, or None for another kind. The side being built is `built`; the other is loaded from
    the same folder (a CAD model's INTEGRATION names its harness; a harness finds the CAD model that declares INTEGRATION, else
    the only CAD file)."""
    from eitri.model import load_model
    from .projectfiles import files_by_kind
    folder = Path(model_path).resolve().parent
    by_kind = files_by_kind(folder)
    cad_files, har_files = by_kind.get("cad", []), by_kind.get("harness", [])
    pair = Pair()
    if kind == "cad":
        pair.parts, pair.cad_path, pair.cad_module = built, Path(model_path).resolve(), module
        pair.integration = getattr(module, "INTEGRATION", None)
        hname = pair.integration.get("harness") if isinstance(pair.integration, dict) else None
        pair.har_path = folder / hname if hname else (har_files[0] if len(har_files) == 1 else None)
    elif kind == "harness":
        pair.harness, pair.har_path = built, Path(model_path).resolve()
        for f in cad_files:                                 # the CAD model of this project: the one that declares INTEGRATION
            try:
                if "INTEGRATION" in f.read_text(encoding="utf-8"):
                    pair.cad_path = f
                    break
            except OSError:
                continue
        if pair.cad_path is None and len(cad_files) == 1:
            pair.cad_path = cad_files[0]
    else:
        return None
    try:
        if kind == "cad" and pair.har_path and pair.har_path.is_file():
            h = load_model(pair.har_path).build()
            pair.harness = h if isinstance(h, Harness) else None
        elif kind == "harness" and pair.cad_path is not None:
            lm = load_model(pair.cad_path)
            parts = lm.build()
            if not isinstance(parts, Harness):
                pair.parts, pair.cad_module = parts, lm.module
                integration = getattr(lm.module, "INTEGRATION", None)
                pair.integration = integration if isinstance(integration, dict) else None
                named = (pair.integration or {}).get("harness")
                if named and (folder / str(named)).resolve() != pair.har_path:
                    pair.integration = None                 # INTEGRATION describes another harness file: its routes and mating faces
                                                            # are that harness's checks (they run in its build and the CAD's), not this one's
    except Exception as exc:                                # the other artifact does not build: that is its own build's failure
        pair.problem = f"{type(exc).__name__}: {exc}"
    return pair


def project_checks(model_path: Path, built: Any, kind: str, module=None, pair: Pair | None = None) -> tuple[list[HCheck], dict]:
    """The cross-domain checks that apply once `built` is built: a CAD model's Parts (routes and mating faces against the harness),
    a harness (the same, from the harness side), or a Firmware (its pinout against the harness). They need the other artifact, found
    in the same folder (`pair`: already loaded by the caller). Returns (checks, data); ([], {}) when there is nothing to compare."""
    from eitri.model import load_model
    folder = Path(model_path).resolve().parent
    if kind == "firmware":
        coupling = getattr(module, "COUPLING", None)
        if not isinstance(coupling, dict) or not coupling.get("pins"):
            return [], {}
        hpath, hname = harness_for_firmware(module, folder)
        if hpath is None or not hpath.is_file():
            return [_check("pinout", "the harness for the firmware is found", "fail", f"{hname or 'no harness model file'} is not in the project", "COUPLING harness='<file>.py'")], {}
        try:
            harness = load_model(hpath).build()
        except Exception as exc:                                    # noqa: BLE001
            return [_check("pinout", "the harness builds for the pinout check", "warn", f"{type(exc).__name__}: {exc}; pinout check skipped")], {}
        if not isinstance(harness, Harness):
            return [], {}
        return firmware_pin_checks(harness, built, dict(coupling["pins"])), {"pins": len(coupling["pins"]), "harness": hpath.name}
    if pair is None:
        pair = load_pair(model_path, built, kind, module)
    if pair is None:
        return [], {}
    if pair.problem:
        return [_check("integration", "the other artifact builds for the cross-domain checks", "warn", f"{pair.problem}; cross-domain checks skipped", "", [])], {}
    checks: list[HCheck] = []
    data: dict = {}
    integration = pair.integration if isinstance(pair.integration, dict) else None
    if integration is not None and pair.parts is not None and pair.harness is not None:
        checks += route_checks(pair.parts, pair.harness, integration.get("routes") or [])
        checks += mating_checks(pair.parts, pair.harness, integration.get("mating") or [])
        data["routes"], data["mating"] = len(integration.get("routes") or []), len(integration.get("mating") or [])
    return checks, data
