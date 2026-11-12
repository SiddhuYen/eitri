"""The machine's step sequence (Circuit.step): what each step commands of the valves, what ends it, the cylinders' positions after
it, and an ESTIMATED time per step and per cycle. The checks on it are made in checks.py (_sequence_checks); the step diagram and
table are drawn here.

The valve a move needs: the valve that feeds the cylinder's port (find_path) in one position and not another. Its command:
- solenoid valve, spring return (and a 3/2): the position the spring holds is "coil off"; the other is "coil 14 on", and it stays on
  for as long as the cylinder must stay there (the valve springs back the moment the coil is off);
- double solenoid (returns="solenoid" / "detent"): a pulse on coil 14 or 12 (SMC M-E03-3-02 p.2: at least 0.1 s; "energize the
  double solenoid valve until the cylinder is exhausted completely" when piping makes 0.1 s too short: the pulse lasts the step);
- 5/3 spring centred: coil 12 or 14 held on; both off is the centre (it stops the cylinder where it is).
A manual, pilot, lever or roller valve cannot be commanded by the controller.

Cycle time ESTIMATE, per step: the valve's response time (datasheet, at 0.5 MPa) + stroke / mean speed + the ending sensor's switch-on
time + the step's dwell. The mean speed is the required speed when a speed controller on the stroke's path is set to it, else the
speed the open fill and exhaust paths pass (ISO 6358, checks.py). NOT included: the time the chamber takes to build pressure before the
piston starts, the acceleration of the load, the controller's scan and output delays, end-of-stroke cushioning. Real strokes are slower:
treat the figure as a lower bound.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Circuit, Cylinder, SeqStep, Valve

UNKNOWN_SENSOR_MS = 1.3       # ASSUMPTION for a sensor whose datasheet gives no switch-on time: Festo SMT-8M-A's 1.3 ms (574334)
ASSUMPTIONS = (
    "valve response from the datasheet (SMC SY catalogue p.2, at 0.5 MPa, JIS B 8375-1981)",
    "mean speed = the set speed where a speed controller throttles the stroke, else the ISO 6358 open-path estimate",
    "not included: pressure build-up before the piston moves, load acceleration, controller scan and output delays, cushioning",
    f"sensor switch-on time from its datasheet (Festo SMT-8M-A 1.3 ms); a sensor without one: {UNKNOWN_SENSOR_MS:g} ms (ASSUMPTION)",
)


@dataclass
class Move:
    cylinder: str
    motion: str                       # extend | retract
    valve: str = ""
    position: int | None = None
    command: str = ""                 # "V1 14 on", "V2 12 pulse" ...
    problem: str = ""                 # why the valves cannot give it ("" when they can)
    already: bool = False             # the cylinder is there already
    time_s: float | None = None
    how: str = ""


@dataclass
class End:
    sensor: str
    ok: bool
    severity: str                     # pass | warn | fail
    why: str


@dataclass
class StepPlan:
    step: SeqStep
    index: int
    moves: list[Move] = field(default_factory=list)
    ends: list[End] = field(default_factory=list)
    signals: dict[str, str] = field(default_factory=dict)     # signal -> on | pulse | off (every coil, during the step)
    state_after: dict[str, str] = field(default_factory=dict)
    time_s: float | None = None
    time_how: str = ""


@dataclass
class SeqPlan:
    steps: list[StepPlan]
    start: dict[str, str]
    end: dict[str, str]
    cycle_s: float | None
    unknown: list[str]                # what the estimate could not time


def _motion_ok(c: Circuit, cyl: Cylinder, positions: dict[str, int], motion: str) -> bool:
    from .checks import find_path
    if motion == "extend":
        return find_path(c, cyl.port("CAP"), "supply", positions).found and (cyl.acting == "single" or find_path(c, cyl.port("ROD"), "exhaust", positions).found)
    if cyl.acting == "single":
        return find_path(c, cyl.port("CAP"), "exhaust", positions).found
    return find_path(c, cyl.port("ROD"), "supply", positions).found and find_path(c, cyl.port("CAP"), "exhaust", positions).found


def controlling_valve(c: Circuit, cyl: Cylinder, motion: str) -> tuple[Valve | None, list[int]]:
    """The valve whose position decides the motion, and the positions that give it."""
    from .failsafe import _valves_of
    for v in _valves_of(c, cyl):
        ok = [i for i in range(len(v.positions)) if _motion_ok(c, cyl, {v.name: i}, motion)]
        if ok and len(ok) < len(v.positions):
            return v, ok
    return None, []


def coil_for(v: Valve, i: int) -> tuple[str | None, str]:
    """(pilot, mode) to put a valve in position i: pilot "14" / "12" or None (all coils off), mode "on" (held) or "pulse"."""
    n = len(v.positions)
    if v.cls == "5/3":
        return ({0: "12", n - 1: "14"}.get(i), "on")
    if v.returns == "spring":
        return (None if i == v.rest_position else "14", "on")
    return ("14" if i == n - 1 else "12", "pulse")


def _stroke_time(cyl: Cylinder, motion: str, info) -> tuple[float | None, str]:
    from .checks import _throttled
    si = next((s for s in (info.strokes if info else []) if s.name == motion), None)
    req = cyl.speed_mm_s if motion == "extend" else (cyl.speed_retract_mm_s or cyl.speed_mm_s)
    if si is not None and req and (_throttled(si.exhaust) or _throttled(si.fill)):
        return cyl.stroke_mm / req, f"{cyl.stroke_mm:g} mm at the set {req:g} mm/s"
    if si is not None and si.speed_available:
        return cyl.stroke_mm / si.speed_available, f"{cyl.stroke_mm:g} mm at {si.speed_available:.0f} mm/s (open paths, ISO 6358 estimate)"
    if req:
        return cyl.stroke_mm / req, f"{cyl.stroke_mm:g} mm at the required {req:g} mm/s"
    return None, "no speed known"


def plan(c: Circuit, infos=None) -> SeqPlan | None:
    if not c.sequence:
        return None
    infos = {i.name: i for i in (infos or [])}
    state = {cy.name: c.start_state.get(cy.name, "retracted") for cy in c.cylinders}
    start = dict(state)
    held: dict[str, str] = {}                       # signal -> "on" for coils held across steps
    valves = [v for v in c.components.values() if isinstance(v, Valve)]
    out: list[StepPlan] = []
    unknown: list[str] = []
    total = 0.0
    for k, st in enumerate(c.sequence, 1):
        sp = StepPlan(st, k)
        want: dict[str, int] = {}
        pulses: dict[str, str] = {}
        for cyl_name, motion in st.moves:
            cyl = c.components[cyl_name]
            mv = Move(cyl_name, motion)
            target = "extended" if motion == "extend" else "retracted"
            mv.already = state[cyl_name] == target
            v, positions = controlling_valve(c, cyl, motion)
            if v is None:
                mv.problem = f"no valve position {motion}s {cyl_name} (no valve switches its supply)"
            elif v.actuation != "solenoid":
                mv.valve = v.name
                mv.problem = f"{v.name} is {v.actuation}-actuated: the controller cannot switch it"
            else:
                mv.valve = v.name
                pick = positions[0]
                if v.name in want and want[v.name] not in positions:
                    mv.problem = f"{v.name} must be in position {want[v.name] + 1} for another move of this step and position {pick + 1} for this one"
                else:
                    pick = want.get(v.name, pick)
                    want[v.name] = pick
                    pilot, mode = coil_for(v, pick)
                    coils = {p: s for p, s, _t in v.coils}
                    if pilot is not None and pilot not in coils:
                        mv.problem = f"{v.name} has no coil {pilot} to reach position {pick + 1}"
                    else:
                        for p, sig in coils.items():
                            if mode == "on":
                                held[sig] = "on" if p == pilot else "off"
                            elif p == pilot:
                                pulses[sig] = "pulse"
                        mv.command = f"{v.name}: " + (f"coil {pilot} {mode}" if pilot else "all coils off")
            info = infos.get(cyl_name)
            t, how = _stroke_time(cyl, motion, info)
            if t is None:
                unknown.append(f"{st.name}: {cyl_name} {motion} ({how})")
            else:
                resp = (c.components[mv.valve].response_ms if mv.valve else None)
                if resp is None and mv.valve:
                    unknown.append(f"{mv.valve} response time")
                mv.time_s = (0.0 if mv.already else t) + (resp or 0.0) / 1000.0
                mv.how = ("already there; " if mv.already else how) + (f" + {resp:g} ms valve response" if resp else "")
            sp.moves.append(mv)
            state[cyl_name] = target
        # what ends the step
        moved = {m.cylinder: m.motion for m in sp.moves}
        sensor_ms = 0.0
        for name in st.until:
            s = c.sensors.get(name)
            if s is None:
                sp.ends.append(End(name, False, "fail", f"{name} is not a sensor of this circuit (sensors: {', '.join(c.sensors) or 'none'})"))
                continue
            sensor_ms = max(sensor_ms, s.response_ms if s.response_ms is not None else UNKNOWN_SENSOR_MS)
            if s.cylinder in moved:
                target = "extended" if moved[s.cylinder] == "extend" else "retracted"
                if s.position == target:
                    sp.ends.append(End(name, True, "pass", f"{name} sees {s.cylinder} {s.position}, where this step moves it"))
                else:
                    sp.ends.append(End(name, False, "fail", f"{name} is at {s.cylinder}'s {s.position} end; this step moves {s.cylinder} to {target}: "
                                                            "the step would end before it starts, or never"))
            elif state.get(s.cylinder) == s.position:
                sp.ends.append(End(name, True, "warn", f"{name} sees {s.cylinder} {s.position}, which no move of this step changes: it is true "
                                                        "already and ends the step at once"))
            else:
                sp.ends.append(End(name, False, "fail", f"{name} waits for {s.cylinder} {s.position}, which nothing makes true in this step"))
        unconfirmed = [cy for cy in moved if not any(c.sensors.get(n) is not None and c.sensors[n].cylinder == cy for n in st.until)]
        for cy in unconfirmed:
            if st.until:
                sp.ends.append(End(cy, True, "warn", f"{cy}'s move is not confirmed by a sensor in until=: the next step may start before it arrives"))
        if not st.until and moved:
            sp.ends.append(End("", False, "fail", f"the step moves {', '.join(moved)} but ends on no sensor (until=)"))
        if not st.until and not moved and st.dwell_s <= 0:
            sp.ends.append(End("", False, "fail", "the step has no move, no sensor and no dwell: nothing ends it"))
        sp.signals = {sig: ("pulse" if sig in pulses else held.get(sig, "off"))
                      for v in valves for _p, sig, _t in v.coils}
        # time
        times = [m.time_s for m in sp.moves if m.time_s is not None]
        if sp.moves and len(times) < len(sp.moves):
            sp.time_s = None
        else:
            sp.time_s = (max(times) if times else 0.0) + (sensor_ms / 1000.0 if st.until else 0.0) + st.dwell_s
            parts = [f"{max(times):.3f} s slowest move" if times else ""] + ([f"{sensor_ms:g} ms sensor"] if st.until else []) + \
                    ([f"{st.dwell_s:g} s dwell"] if st.dwell_s else [])
            sp.time_how = " + ".join(p for p in parts if p)
        sp.state_after = dict(state)
        if sp.time_s is not None and total is not None:
            total += sp.time_s
        else:
            total = None
        out.append(sp)
    return SeqPlan(out, start, dict(state), total, unknown)


# --------------------------------------------------------------------------- outputs: table and step diagram


def sequence_markdown(c: Circuit, p: SeqPlan | None) -> str:
    if p is None:
        return f"# Sequence: {c.name}\n\nNo sequence declared (c.step(...)).\n"
    rows = ["| step | moves | coils (on / pulse) | ends on | est. time (s) | cumulative (s) |", "|---|---|---|---|---|---|"]
    cum = 0.0
    for sp in p.steps:
        moves = ", ".join(f"{m.cylinder}{'+' if m.motion == 'extend' else '-'}" for m in sp.moves) or (f"wait {sp.step.dwell_s:g} s" if sp.step.dwell_s else "-")
        coils = ", ".join(f"{s} {v}" for s, v in sp.signals.items() if v != "off") or "none"
        ends = ", ".join(sp.step.until) or ("time" if sp.step.dwell_s else "-")
        if sp.time_s is not None:
            cum += sp.time_s
        rows.append(f"| {sp.index} {sp.step.name} | {moves} | {coils} | {ends} | {'' if sp.time_s is None else f'{sp.time_s:.3f}'} | "
                    f"{'' if sp.time_s is None else f'{cum:.3f}'} |")
    out = f"# Sequence: {c.name}\n\n" + "\n".join(rows) + "\n\n"
    out += (f"**Estimated cycle time: {p.cycle_s:.2f} s** (an ESTIMATE: a lower bound).\n\n" if p.cycle_s is not None
            else "Estimated cycle time: not known (" + "; ".join(p.unknown) + ").\n\n")
    out += "Assumptions:\n" + "\n".join(f"- {a}" for a in ASSUMPTIONS) + "\n"
    out += "\nStart: " + ", ".join(f"{k} {v}" for k, v in p.start.items()) + ". End: " + ", ".join(f"{k} {v}" for k, v in p.end.items()) + ".\n"
    return out


def step_diagram_scene(c: Circuit, p: SeqPlan, tags: dict[str, str] | None = None):
    """The displacement-step diagram (VDI 3260 style function chart): one row per cylinder, 0 retracted / 1 extended, the steps
    on the x axis; a row per coil signal underneath (on / pulse); the sensors that end each step at the step lines."""
    from ..diagram import INK, MUTED, Scene, text_w
    tags = tags or {}
    cyls = [cy.name for cy in c.cylinders]
    sigs = list(dict.fromkeys(s for sp in p.steps for s in sp.signals))
    n = len(p.steps)
    left, colw, rowh, sigh = 170.0, max(70.0, min(120.0, 720.0 / max(n, 1))), 52.0, 22.0
    top = 70.0
    W = left + colw * n + 40
    H = top + rowh * len(cyls) + 30 + sigh * len(sigs) + 70
    sc = Scene(W, H)
    it = sc.items
    it.append({"t": "text", "x": 20, "y": 26, "s": f"Displacement-step diagram: {c.name}", "size": 14, "weight": "700", "fill": INK, "g": "title"})
    tot = f"estimated cycle {p.cycle_s:.2f} s (ESTIMATE, a lower bound: see the sequence table)" if p.cycle_s is not None else "cycle time not known"
    it.append({"t": "text", "x": 20, "y": 44, "s": tot, "size": 9, "fill": MUTED, "g": "title"})
    bottom = top + rowh * len(cyls) + 30 + sigh * len(sigs)
    for k in range(n + 1):
        x = left + colw * k
        it.append({"t": "line", "x1": x, "y1": top - 12, "x2": x, "y2": bottom, "stroke": "#c5ccd6", "sw": 0.8, "g": "grid"})
        it.append({"t": "text", "x": x, "y": top - 16, "s": str(k + 1 if k < n else 1), "size": 9, "weight": "700", "fill": INK, "anchor": "middle", "g": "grid"})
    for k, sp in enumerate(p.steps):
        x = left + colw * (k + 0.5)
        it.append({"t": "text", "x": x, "y": bottom + 16, "s": sp.step.name[:16], "size": 8, "fill": INK, "anchor": "middle", "g": "grid"})
        if sp.time_s is not None:
            it.append({"t": "text", "x": x, "y": bottom + 28, "s": f"{sp.time_s:.2f} s", "size": 8, "fill": MUTED, "anchor": "middle", "g": "grid"})
    for r, name in enumerate(cyls):
        y0 = top + rowh * r + 10
        y1, y_ext = y0 + rowh - 22, y0
        it.append({"t": "text", "x": 20, "y": (y0 + y1) / 2 + 4, "s": f"{tags.get(name, '')} {name}".strip(), "size": 10, "weight": "700", "fill": INK, "g": "rows"})
        it.append({"t": "text", "x": left - 8, "y": y_ext + 4, "s": "1", "size": 8, "fill": MUTED, "anchor": "end", "g": "rows"})
        it.append({"t": "text", "x": left - 8, "y": y1 + 4, "s": "0", "size": 8, "fill": MUTED, "anchor": "end", "g": "rows"})
        level = lambda s: y_ext if s == "extended" else y1
        pts = [(left, level(p.start.get(name, "retracted")))]
        prev = p.start.get(name, "retracted")
        for k, sp in enumerate(p.steps):
            now = sp.state_after.get(name, prev)
            x0, x1 = left + colw * k, left + colw * (k + 1)
            if now != prev:
                pts += [(x0, level(prev)), (x1, level(now))]
            else:
                pts.append((x1, level(now)))
            prev = now
        it.append({"t": "polyline", "pts": pts, "stroke": "#1f5fbf", "sw": 2.2, "fill": "none", "g": f"cyl:{name}"})
        for k, sp in enumerate(p.steps):                            # the sensor that ends the step, at the step line
            for e in sp.ends:
                s = c.sensors.get(e.sensor)
                if s is not None and s.cylinder == name:
                    x, y = left + colw * (k + 1), level(s.position)
                    col = "#b3261e" if e.severity == "fail" else INK
                    it.append({"t": "circle", "cx": x, "cy": y, "r": 3.2, "fill": col, "stroke": col, "sw": 1, "g": "sensor"})
                    lab = f"{tags.get(e.sensor, e.sensor)}"
                    it.append({"t": "text", "x": x + 5, "y": y - 5 if s.position == "extended" else y + 12, "s": lab, "size": 8, "fill": col, "g": "sensor"})
    ys = top + rowh * len(cyls) + 30
    it.append({"t": "text", "x": 20, "y": ys - 8, "s": "coils", "size": 9, "weight": "700", "fill": MUTED, "g": "rows"})
    for r, sig in enumerate(sigs):
        y = ys + sigh * r
        it.append({"t": "text", "x": 20, "y": y + 13, "s": sig, "size": 8.5, "fill": INK, "g": "rows"})
        for k, sp in enumerate(p.steps):
            v = sp.signals.get(sig, "off")
            if v == "off":
                continue
            x0 = left + colw * k + 2
            w = colw - 4 if v == "on" else min(colw - 4, 18)
            it.append({"t": "rect", "x": x0, "y": y + 4, "w": w, "h": 12, "rx": 0, "fill": "#1f5fbf" if v == "on" else "#7aa6e0",
                       "stroke": "#1f5fbf", "sw": 0.8, "g": "coil"})
    it.append({"t": "text", "x": 20, "y": H - 14, "s": "1 = extended, 0 = retracted; filled bar = coil on, short bar = pulse; dots: the sensor that ends the step.",
               "size": 7.5, "fill": MUTED, "g": "notes"})
    sc.width = max(W, 40 + text_w(tot, 9))
    return sc


def write_sequence(c: Circuit, p: SeqPlan | None, out_dir, stem: str, tags: dict[str, str] | None = None) -> dict[str, str]:
    from pathlib import Path
    from ..diagram import scene_to_pdf, scene_to_svg
    out = {}
    md = Path(out_dir) / f"{stem}.sequence.md"
    md.write_text(sequence_markdown(c, p), encoding="utf-8")
    out["sequence_md"] = str(md)
    if p is not None:
        sc = step_diagram_scene(c, p, tags)
        svg = Path(out_dir) / f"{stem}.sequence.svg"
        svg.write_text(scene_to_svg(sc), encoding="utf-8")
        pdf = Path(out_dir) / f"{stem}.sequence.pdf"
        scene_to_pdf(sc, pdf)
        out.update(sequence_svg=str(svg), sequence_pdf=str(pdf))
    return out
