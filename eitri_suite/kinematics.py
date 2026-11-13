"""Mechanism <-> CAD placement: where every body is in the assembly, which body drives which joint, and what the arm carries.

The mechanism kernel (eitri.mechanism) moves LINKS; the CAD model has BODIES; COUPLING links={link: [bodies]} says which body rides on
which link. Everything here poses the bodies with the kernel's own Mechanism.transforms (rotations about each joint's ZERO-pose
world point), the same transforms the Assembly view must apply, so what is checked is what is drawn.

THE ACTUATORS
  A joint's Actuator (a servo, a motor, a linear actuator) is a body in the CAD model; nothing else in the kernel places it. Every
  actuator is bound to the joint it drives (actuator_bindings): by COUPLING actuators={joint: body}, else the actuator body named
  after the joint ("elbow_servo" drives "elbow"), else the actuator body nearest the joint point. An actuator body is one the CAD
  file's HARDWARE maps to a catalog servo or motor, or one whose name ends in servo, motor, actuator, stepper or solenoid. The body
  rides on the link COUPLING lists it under (one of the joint's two links), else on the joint's parent link: effective_links adds it
  there, and that is the map the Assembly view poses with, so the body follows its link through every pose. Nothing is moved: the
  CAD model says where the body is, and the check below says whether that is at its joint.

THE CHECKS (category "coupling", on a mechanism build and on a CAD or harness build of a project whose mechanism couples to it)
  actuators       every actuator is at the joint it drives: a revolute actuator's output face holds the joint's zero-pose point
                  (within SHAFT_TOL_MM) and the joint axis is normal to that face (within AXIS_TOL_DEG), so the shaft is the axis; a
                  prismatic actuator sits within DRIVE_REACH_MM of the stroke axis (it drives through a horn or a link). A body that
                  is not, a joint whose actuator has no body, or an actuator body that drives no joint, is named.
  attached        every body is fixed to the base or attached to a link of the kinematic chain, at the zero pose, at home and at
                  every step of the firmware SEQUENCE (the poses the Assembly view shows), and bodies of every domain count: the CAD
                  parts, the harness connectors and splices, and the firmware's board at their anchors. A CAD body's link is the one
                  COUPLING lists it under or the actuator binding gives it; a body under no link is taken as fixed to the base. On
                  each link the bodies must form one connected piece (bodies within ATTACH_TOL_MM of each other) that reaches the
                  link's attachment point: the point of the joint that moves it, or, for the base, the table (the lowest base
                  body); and at every keyframe each body still touches the machine. A body that does not is floating; the check
                  names it, its link, the pose, and its distance from the nearest attachment point. A body listed under a link the
                  mechanism does not have, or under two links, never moves with the chain and fails too. A harness component is
                  attached when its anchor sits on its part (within ATTACH_TOL_MM) and that part is attached; a component whose
                  anchor names no part and sits on none floats.
  interference    at the zero pose no two bodies overlap by more than OVERLAP_TOL_MM3, whatever their links (a board sunk into the
                  floor it sits on, two bodies modelled in one place). The only overlaps allowed are between bodies on the two links
                  of one joint (a horn seated in its arm): the self-collision sweep grades those as the joint moves.
  payload         the object the arm picks is modelled only when COUPLING payload= declares it (the person asked for the object);
                  otherwise the payload is the mass m.payload() gives the torque checks and no body is drawn or swept. A declared
                  payload rests at the pick target, is attached to the end-effector frame when the plan grabs it (it keeps the offset
                  it had at that moment), moves with it, and is released where the plan releases it. While carried it is swept
                  along the planned motion (the firmware SEQUENCE, else home -> pick -> place -> home from the mechanism's IK)
                  against every body of every link and the base, except the fingers (COUPLING payload fingers=[...], else the bodies
                  of the end-effector link). An overlap fails, naming the body and the joint configuration.

    COUPLING = dict(cad="model.py", links={...},
                    actuators={"elbow": "elbow_servo"},                                      # optional: joint -> its actuator's body
                    payload=dict(name="bottle cap", shape="cylinder", size_mm=[30, 12],      # only when the object is to be modelled
                                 at="pick", fingers=["finger_l", "finger_r"]))               # at: a target name or a world point [x, y, z]

The grab/release step names follow the Assembly view's Run (static/js/asmrun.js): a step named pick/grab/take/close (not above/over/
lift) grabs on arrival, one named place/drop/put/open/release (not above/over) releases.
"""
from __future__ import annotations

import re
import time
from typing import Any

import numpy as np

from eitri import measure
from eitri.harness.checks import HCheck

ATTACH_TOL_MM = 0.5            # bodies closer than this are attached to each other (modelled faces that touch, within rounding)
SHAFT_TOL_MM = 2.0             # a revolute actuator's output face holds its joint point within this (a horn's thickness)
AXIS_TOL_DEG = 5.0             # the joint axis is normal to that face within this: the shaft is the axis
DRIVE_REACH_MM = 30.0          # a prismatic actuator sits within this of the stroke axis it drives (through a horn or a link)
ATTACH_BUDGET_S = 20.0         # the keyframe sweep of the attachment check stops here and says so
OVERLAP_TOL_MM3 = 1.0          # the same tolerance as the self-collision check (eitri_suite.coupling)
ACTUATOR_CATEGORIES = {"servo", "motor", "actuator", "stepper", "linear"}
ACTUATOR_NAME = re.compile(r"(?:^|[_\-\s])(servo|motor|actuator|stepper|solenoid)$", re.I)
SAMPLES_PER_MOVE = 12          # payload sweep: poses per move between two steps (joint-space interpolation, as the view plays it)
PAYLOAD_BUDGET_S = 30.0

GRAB = re.compile(r"pick|grab|take|close", re.I)
NOT_GRAB = re.compile(r"above|over|lift", re.I)
RELEASE = re.compile(r"place|drop|put|open|release", re.I)
NOT_RELEASE = re.compile(r"above|over", re.I)


def _check(name: str, severity: str, measured: str, expected: str = "", refs=()) -> HCheck:
    return HCheck(name=name, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category="coupling", refs=list(dict.fromkeys(refs)))


# ---------------------------------------------------------------------------- posing bodies with the kernel's transforms

def location(M: np.ndarray):
    """A 4x4 rigid transform as a build123d Location."""
    import build123d as bd
    from OCP.gp import gp_Trsf
    t = gp_Trsf()
    t.SetValues(*[float(M[i, j]) for i in range(3) for j in range(4)])
    return bd.Location(t)


def moved(shape, M: np.ndarray):
    if np.allclose(M, np.eye(4), atol=1e-12):
        return shape
    return shape.moved(location(M))


def link_of(links: dict[str, list[str]]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """(body -> link, body -> [links] for bodies listed more than once)."""
    of: dict[str, str] = {}
    twice: dict[str, list[str]] = {}
    for l, names in (links or {}).items():
        for n in names or []:
            n = str(n)
            if n in of and of[n] != l:
                twice.setdefault(n, [of[n]]).append(str(l))
            of.setdefault(n, str(l))
    return of, twice


def tip_frame(mech, q: dict[str, float]) -> np.ndarray:
    """The end-effector frame at pose q (world): the tip link's frame moved to the tool point."""
    W, _P = mech.zero_frames()
    tl, ta = mech.resolved_tip()
    F = mech.transforms(q)[tl] @ W[tl]
    T = np.eye(4)
    T[:3, 3] = ta
    return F @ T


def _dist(a, b) -> float:
    ba, bb = measure.bounding_box(a), measure.bounding_box(b)
    gap = np.array([max(0.0, ba.min.X - bb.max.X, bb.min.X - ba.max.X), max(0.0, ba.min.Y - bb.max.Y, bb.min.Y - ba.max.Y),
                    max(0.0, ba.min.Z - bb.max.Z, bb.min.Z - ba.max.Z)])
    g = float(np.linalg.norm(gap))
    if g > 5 * ATTACH_TOL_MM + 50:              # far apart: the box gap is a fine lower bound for the report
        return g
    try:
        return float(measure.min_distance(a, b))
    except Exception:                            # noqa: BLE001
        return g


def _point_dist(p, shape) -> float:
    import build123d as bd
    try:
        return float(measure.min_distance(bd.Vertex(*[float(x) for x in p]), shape))
    except Exception:                            # noqa: BLE001
        bb = measure.bounding_box(shape)
        lo, hi = np.array([bb.min.X, bb.min.Y, bb.min.Z]), np.array([bb.max.X, bb.max.Y, bb.max.Z])
        return float(np.linalg.norm(np.maximum(0, np.maximum(lo - p, p - hi))))


# ---------------------------------------------------------------------------- the actuators: which body drives which joint

def _word_in(word: str, name: str) -> bool:
    return re.search(r"(?:^|[_\-\s])" + re.escape(word) + r"(?:$|[_\-\s])", name, re.I) is not None


def actuator_body(name: str, hardware: dict | None) -> bool:
    """Whether a CAD part is an actuator's body: HARDWARE maps it to a catalog servo or motor, or its name ends in servo, motor,
    actuator, stepper or solenoid ("elbow_servo", "yaw motor"; not "servo_mount")."""
    ref = (hardware or {}).get(name)
    ref = ref.get("id") if isinstance(ref, dict) else ref
    if ref:
        from . import catalog
        cp = catalog.lookup(str(ref))
        if cp is not None and cp.category in ACTUATOR_CATEGORIES:
            return True
    return ACTUATOR_NAME.search(str(name)) is not None


def _axis_line(P: np.ndarray, axis: np.ndarray, half: float = 1000.0):
    import build123d as bd
    a = P - axis * half
    b = P + axis * half
    return bd.Edge.make_line(bd.Vector(*[float(x) for x in a]), bd.Vector(*[float(x) for x in b]))


def shaft_fit(joint, P: np.ndarray, shape) -> dict:
    """How an actuator body sits at its joint: {distance_mm: joint point to the body, axis_deg: the joint axis against the normal
    of the body's face nearest the point (revolute; None for prismatic), line_mm: body to the stroke axis (prismatic), ok, why}."""
    import build123d as bd
    axis = np.asarray(joint.axis, dtype=float)
    d = _point_dist(P, shape)
    out: dict[str, Any] = {"distance_mm": round(float(d), 2), "axis_deg": None, "line_mm": None, "ok": True, "why": ""}
    pt = f"({P[0]:g}, {P[1]:g}, {P[2]:g})"
    if joint.type == "prismatic":
        try:
            line = float(measure.min_distance(_axis_line(P, axis), shape))
        except Exception:                                    # noqa: BLE001
            line = d
        out["line_mm"] = round(line, 2)
        if line > DRIVE_REACH_MM:
            out["ok"] = False
            out["why"] = f"sits {line:.1f} mm from the stroke axis of joint {joint.name} through {pt}: too far to drive it"
        return out
    normal = None
    try:
        v = bd.Vertex(*[float(x) for x in P])
        face = min(shape.faces(), key=lambda f: f.distance_to(v))
        n = face.normal_at(bd.Vector(*[float(x) for x in P]))
        normal = np.array([n.X, n.Y, n.Z], dtype=float)
        normal /= max(float(np.linalg.norm(normal)), 1e-12)
    except Exception:                                        # noqa: BLE001
        normal = None
    ang = None if normal is None else float(np.degrees(np.arccos(min(1.0, abs(float(normal @ axis))))))
    out["axis_deg"] = None if ang is None else round(ang, 1)
    if d > SHAFT_TOL_MM:
        out["ok"] = False
        out["why"] = f"is {d:.1f} mm from the point of joint {joint.name} at {pt}: its output face must be centred there"
    elif ang is not None and ang > AXIS_TOL_DEG:
        nrm = ", ".join(f"{(0.0 if abs(x) < 1e-9 else x):g}" for x in np.round(normal, 3))
        ax = ", ".join(f"{(0.0 if abs(x) < 1e-9 else x):g}" for x in np.round(axis, 3))
        out["ok"] = False
        out["why"] = f"has its output face at joint {joint.name} normal to ({nrm}); the joint axis ({ax}) is {ang:.0f}° off it: the shaft must lie along the axis"
    return out


def actuator_bindings(parts, mech, coupling: dict | None, hardware: dict | None = None) -> dict:
    """Every actuator bound to the joint it drives: {joints: {joint: {part, link, source, actuator, distance_mm, axis_deg, line_mm,
    ok, why}}, unbound: [actuator bodies that drive no joint], missing: [joints whose actuator has no body]}. The link is the one
    COUPLING lists the body under (one of the joint's two links), else the joint's parent link."""
    coupling = coupling if isinstance(coupling, dict) else {}
    links = coupling.get("links") or {}
    of, _twice = link_of(links)
    by_name = {p.name: p for p in parts}
    explicit = {str(k): str(v) for k, v in (coupling.get("actuators") or {}).items()} if isinstance(coupling.get("actuators"), dict) else {}
    cands = [n for n in by_name if actuator_body(n, hardware)]
    try:
        joints = [j for j in mech.order() if j.actuator is not None]
    except Exception:                                        # noqa: BLE001
        joints = [j for j in mech.joints if j.actuator is not None]
    points = {j.name: mech.joint_at(j) for j in joints}
    assigned: dict[str, str] = {}
    out: dict[str, dict] = {}
    missing: list[str] = []
    for j in joints:
        P = points[j.name]
        part, source = None, ""
        if j.name in explicit:
            part, source = explicit[j.name], "COUPLING actuators"
            if part not in by_name:
                out[j.name] = {"part": part, "link": None, "source": source, "actuator": j.actuator.name, "distance_mm": None, "axis_deg": None, "line_mm": None,
                               "ok": False, "why": f"COUPLING actuators names {part!r} for joint {j.name}, which the CAD model does not build"}
                continue
        if part is None:
            named = [n for n in cands if n not in assigned and _word_in(j.name, n)]
            if named:
                part, source = min(named, key=lambda n: _point_dist(P, by_name[n].shape)), "named after the joint"
        if part is None:
            free = [n for n in cands if n not in assigned]
            if free:
                dist = {n: _point_dist(P, by_name[n].shape) for n in free}
                own = [n for n in free if of.get(n) in (j.parent, j.child)]
                pool = own or free
                n = min(pool, key=lambda x: dist[x])
                if dist[n] <= DRIVE_REACH_MM:
                    part, source = n, (f"listed under link {of[n]}" if n in own else "nearest to the joint")
        if part is None:
            missing.append(j.name)
            out[j.name] = {"part": None, "link": None, "source": "", "actuator": j.actuator.name, "distance_mm": None, "axis_deg": None, "line_mm": None,
                           "ok": True, "why": f"no body for the {j.actuator.name} of joint {j.name} in the CAD model"}
            continue
        assigned[part] = j.name
        fit = shaft_fit(j, P, by_name[part].shape)
        link = of.get(part) or j.parent
        rec = {"part": part, "link": link, "source": source, "actuator": j.actuator.name, **fit}
        if of.get(part) is not None and of[part] not in (j.parent, j.child):
            rec["ok"] = False
            rec["why"] = f"drives joint {j.name} but is listed under link {of[part]!r}, which is neither side of the joint ({j.parent}, {j.child})"
        out[j.name] = rec
    unbound = [n for n in cands if n not in assigned]
    return {"joints": out, "unbound": unbound, "missing": missing}


def effective_links(links: dict[str, list[str]], bindings: dict) -> dict[str, list[str]]:
    """COUPLING links with every bound actuator body that is listed under no link added to the link it rides on: the map the
    Assembly view poses the bodies with, so an actuator follows the link it is mounted on through every pose."""
    out = {str(l): [str(n) for n in (ns or [])] for l, ns in (links or {}).items()}
    of, _ = link_of(out)
    for j, b in (bindings.get("joints") or {}).items():
        if b.get("part") and b.get("link") and b["part"] not in of:
            out.setdefault(b["link"], []).append(b["part"])
            of[b["part"]] = b["link"]
    return out


def actuator_check(bindings: dict) -> HCheck:
    js = bindings.get("joints") or {}
    bad = [(j, b) for j, b in js.items() if not b["ok"]]
    problems = [f"{b['part']} {b['why']}" if b.get("part") and not b["why"].startswith("COUPLING") else b["why"] for _j, b in bad]
    refs = [b["part"] for _j, b in bad if b.get("part")] + [j for j, _b in bad]
    sev = "fail" if bad else "pass"
    notes = []
    if bindings.get("missing"):
        notes.append("no body for " + ", ".join(f"the {js[j]['actuator']} of joint {j}" for j in bindings["missing"][:4])
                     + ": model it as a box of its catalog size with its output face centred on the joint point, named after the joint (elbow_servo) or in COUPLING actuators")
        sev = sev if sev == "fail" else "warn"
    if bindings.get("unbound"):
        notes.append(", ".join(bindings["unbound"][:4]) + (" is an actuator body that drives" if len(bindings["unbound"]) == 1 else " are actuator bodies that drive") + " no joint of the mechanism")
        sev = sev if sev == "fail" else "warn"
        refs += bindings["unbound"]
    bound = [(j, b) for j, b in js.items() if b.get("part") and b["ok"]]
    if sev == "fail":
        text = "; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else "")
    elif bound:
        text = f"{len(bound)} actuator bod{'ies' if len(bound) != 1 else 'y'} at {'their' if len(bound) != 1 else 'its'} joints: " + ", ".join(
            f"{b['part']} drives {j}" + (f" (on {b['link']}, shaft along the axis within {b['axis_deg']:g}°)" if b.get("axis_deg") is not None else (f" (on {b['link']}, {b['line_mm']:g} mm from the stroke axis)" if b.get("line_mm") is not None else f" (on {b['link']})"))
            for j, b in bound)
    else:
        text = "no actuator bodies to place"
    if notes:
        text += "; " + "; ".join(notes)
    return _check("every actuator is at the joint it drives", sev, text,
                  f"each revolute actuator's output face centred on its joint point (within {SHAFT_TOL_MM:g} mm), the joint axis normal to it (within {AXIS_TOL_DEG:g}°); a prismatic one within {DRIVE_REACH_MM:g} mm of its stroke axis",
                  list(dict.fromkeys(refs)))


def keyframes(mech, sequence: dict | None = None) -> list[tuple[str, dict[str, float]]]:
    """The poses the Assembly view shows the machine in: the zero pose, home, and every step of the firmware SEQUENCE (as
    eitri_suite.assembly.sequence computes it), as (label, joint values)."""
    home = mech.home_pose()
    out = [("the zero pose", {j.name: 0.0 for j in mech.moving_joints}), ("home", dict(home))]
    for s in ((sequence or {}).get("steps") or []):
        out.append((f"step '{s.get('name')}'", {**home, **{str(k): float(v) for k, v in (s.get("joints") or {}).items()}}))
    return out


# ---------------------------------------------------------------------------- every body is attached

def _gap(ba, bb) -> float:
    g = np.array([max(0.0, ba.min.X - bb.max.X, bb.min.X - ba.max.X), max(0.0, ba.min.Y - bb.max.Y, bb.min.Y - ba.max.Y),
                  max(0.0, ba.min.Z - bb.max.Z, bb.min.Z - ba.max.Z)])
    return float(np.linalg.norm(g))


def attachment(parts, mech, links: dict[str, list[str]], poses: list[tuple[str, dict[str, float]]] | None = None,
               components: list[dict] | None = None, budget_s: float = ATTACH_BUDGET_S) -> dict:
    """Which bodies are attached: the CAD bodies link by link at the zero pose (attachment is rigid within a link), then at every
    keyframe (`poses`: [(label, q)]) whether each body still touches the machine as its link carries it, and the harness and
    firmware components (`components`: [{name, kind, part, at}]) on their parts.
    Returns {bodies: {name: {link, attached, distance_mm, to, pose?, kind}}, unknown_links, twice, implicit_base, poses_checked, truncated}."""
    t0 = time.time()
    of, twice = link_of(links)
    base = mech.base
    by_name = {p.name: p for p in parts}
    unknown = {n: l for n, l in of.items() if l not in mech.links and n in by_name}
    implicit = [p.name for p in parts if p.name not in of]
    members: dict[str, list[str]] = {}
    for p in parts:
        l = of.get(p.name, base)
        if l in mech.links:
            members.setdefault(l, []).append(p.name)
    joint_of = {j.child: j for j in mech.joints}
    out: dict[str, dict] = {}
    shapes = {p.name: p.shape for p in parts}
    boxes = {n: measure.bounding_box(s) for n, s in shapes.items()}
    floor = min((boxes[n].min.Z for n in members.get(base, [])), default=0.0)
    pair_d: dict[tuple[str, str], float] = {}

    def d(a, b):
        k = (a, b) if a < b else (b, a)
        if k not in pair_d:
            pair_d[k] = _dist(shapes[a], shapes[b])
        return pair_d[k]
    for l, names in members.items():
        if l == base:
            roots = {n: 0.0 for n in names if boxes[n].min.Z - floor <= ATTACH_TOL_MM}
            anchor_txt = f"the table (z = {floor:g})"
            root_dist = {n: boxes[n].min.Z - floor for n in names}
        else:
            j = joint_of.get(l)
            P = mech.joint_at(j) if j is not None else np.zeros(3)
            root_dist = {n: _point_dist(P, shapes[n]) for n in names}
            roots = {n: dd for n, dd in root_dist.items() if dd <= SHAFT_TOL_MM}
            anchor_txt = f"joint {j.name} at ({P[0]:g}, {P[1]:g}, {P[2]:g})" if j is not None else "its frame"
        reached = set(roots)
        frontier = list(roots)
        while frontier:
            a = frontier.pop()
            for b in names:
                if b not in reached and d(a, b) <= ATTACH_TOL_MM:
                    reached.add(b)
                    frontier.append(b)
        for n in names:
            if n in reached:
                out[n] = {"link": l, "attached": True, "distance_mm": 0.0, "to": anchor_txt, "kind": "cad"}
                continue
            cands = [(root_dist[n], anchor_txt)] + [(d(n, m), m) for m in reached]
            dist, to = min(cands, key=lambda c: c[0])
            out[n] = {"link": l, "attached": False, "distance_mm": round(float(dist), 1), "to": to, "kind": "cad"}
    for n, l in unknown.items():
        out[n] = {"link": l, "attached": False, "distance_mm": None, "to": f"link {l!r}, which the mechanism does not have", "kind": "cad"}
    # every keyframe: a body its link carries away from everything else is floating there (the base never moves: the table holds it)
    checked, truncated = 0, False
    moving = [n for n, b in out.items() if b["attached"] and b["link"] != base]
    for label, q in (poses or [])[1:] if poses else []:
        if time.time() - t0 > budget_s:
            truncated = True
            break
        checked += 1
        T = mech.transforms(q)
        posed = {n: moved(shapes[n], T.get(of.get(n, base), np.eye(4))) for n in out if out[n]["attached"]}
        pboxes = {n: measure.bounding_box(s) for n, s in posed.items()}
        for n in moving:
            if out[n].get("pose"):
                continue
            j = joint_of.get(out[n]["link"])
            P = mech.joint_at(j) if j is not None else np.zeros(3)
            if j is not None:
                P = (T.get(j.parent, np.eye(4)) @ np.append(P, 1.0))[:3]
                slide = abs(float(q.get(j.name, j.home))) if j.type == "prismatic" else 0.0    # a slider rides its guide: out by its stroke, still on it
                if _point_dist(P, posed[n]) <= SHAFT_TOL_MM + slide:
                    continue
            near = sorted((( _gap(pboxes[n], pboxes[m]), m) for m in posed if m != n), key=lambda x: x[0])
            touch, best = False, None
            for g, m in near:
                if g > ATTACH_TOL_MM:
                    best = best if best is not None else (g, m)
                    break
                dd = _dist(posed[n], posed[m])
                if dd <= ATTACH_TOL_MM:
                    touch = True
                    break
                if best is None or dd < best[0]:
                    best = (dd, m)
            if not touch:
                dist, to = best if best is not None else (float("nan"), "anything")
                out[n].update({"attached": False, "distance_mm": round(float(dist), 1), "to": to, "pose": label})
    for c in components or []:
        part, at = c.get("part"), c.get("at")
        name, kind = str(c.get("name")), str(c.get("kind") or "connector")
        if not part or part not in shapes:
            out[name] = {"link": None, "attached": False, "distance_mm": None, "to": "no part under its anchor", "kind": kind}
            continue
        dd = _point_dist(np.asarray(at, dtype=float), shapes[part]) if at is not None else 0.0
        host = out.get(part)
        ok = dd <= ATTACH_TOL_MM and bool(host and host["attached"])
        out[name] = {"link": host["link"] if host else None, "attached": ok, "distance_mm": round(float(dd), 1), "to": part, "kind": kind,
                     **({"pose": host.get("pose")} if host and host.get("pose") and dd <= ATTACH_TOL_MM else {})}
    return {"bodies": out, "unknown_links": unknown, "twice": twice, "implicit_base": implicit, "poses_checked": checked, "truncated": truncated}


def attachment_check(parts, mech, links: dict[str, list[str]], poses: list[tuple[str, dict[str, float]]] | None = None,
                     components: list[dict] | None = None) -> tuple[HCheck, dict]:
    info = attachment(parts, mech, links, poses, components)
    bad = [(n, b) for n, b in info["bodies"].items() if not b["attached"]]
    problems = []
    for n, b in bad:
        what = {"cad": "", "connector": "connector ", "splice": "splice ", "board": "board "}.get(b.get("kind"), "")
        if b["distance_mm"] is None and b["to"].startswith("link "):
            problems.append(f"{n} is listed under {b['to']}: it never moves with the chain")
        elif b["distance_mm"] is None:
            problems.append(f"{what}{n} floats: {b['to']} (give part= in its anchor, on the part it is mounted on)")
        elif b.get("kind") not in (None, "cad"):
            problems.append(f"{what}{n} is {b['distance_mm']:g} mm off its part {b['to']}" if b["distance_mm"] > ATTACH_TOL_MM else f"{what}{n} is on {b['to']}, which floats")
        elif b.get("pose"):
            problems.append(f"{n} (link {b['link']}) floats at {b['pose']}: {b['distance_mm']:g} mm from the nearest body ({b['to']})")
        else:
            problems.append(f"{n} (link {b['link']}) floats {b['distance_mm']:g} mm from the nearest attachment point ({b['to']})")
    for n, ls in info["twice"].items():
        problems.append(f"{n} is listed under {len(ls)} links ({', '.join(ls)})")
    note = f"; {len(info['implicit_base'])} bod{'ies' if len(info['implicit_base']) != 1 else 'y'} not listed in COUPLING taken as fixed to the base ({', '.join(info['implicit_base'][:6])})" if info["implicit_base"] else ""
    kinds = {}
    for b in info["bodies"].values():
        kinds[b.get("kind") or "cad"] = kinds.get(b.get("kind") or "cad", 0) + 1
    n = len(info["bodies"])
    where = f"at the zero pose" + (f", home and {info['poses_checked'] - 1} sequence step{'s' if info['poses_checked'] - 1 != 1 else ''}" if info["poses_checked"] > 1 else (", and home" if info["poses_checked"] == 1 else ""))
    domains = ", ".join(f"{k} {'CAD part' if kd == 'cad' else kd}{'s' if k != 1 else ''}" for kd, k in kinds.items())
    if problems:
        c = _check("every body is attached to the base or a link", "fail", "; ".join(problems[:6]) + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else "") + note,
                   f"each body within {ATTACH_TOL_MM:g} mm of its link's other bodies or its joint, at every pose; each connector on its part", [b for b, _ in bad] + list(info["twice"]))
    else:
        c = _check("every body is attached to the base or a link", "warn" if info["truncated"] else "pass",
                   f"{n} bodies attached {where} ({domains}): each touches its link's joint, or the table, through its link's other bodies" + note
                   + ("; the time budget ran out before every keyframe was checked" if info["truncated"] else ""), "", [])
    return c, info


# ---------------------------------------------------------------------------- interference at rest

def interference_check(parts, mech, links: dict[str, list[str]]) -> tuple[HCheck, list[dict]]:
    """Overlapping bodies at the zero pose, any links, except bodies on the two links of one joint."""
    of, _ = link_of(links)
    base = mech.base
    adjacent = {frozenset((j.parent, j.child)) for j in mech.joints}
    plist = list(parts)
    boxes = [measure.bounding_box(p.shape) for p in plist]
    found = []
    for i in range(len(plist)):
        for k in range(i + 1, len(plist)):
            a, b, ba, bb = plist[i], plist[k], boxes[i], boxes[k]
            if (ba.max.X < bb.min.X or bb.max.X < ba.min.X or ba.max.Y < bb.min.Y or bb.max.Y < ba.min.Y or ba.max.Z < bb.min.Z or bb.max.Z < ba.min.Z):
                continue
            la, lb = of.get(a.name, base), of.get(b.name, base)
            if la != lb and frozenset((la, lb)) in adjacent:
                continue
            try:
                v = float(measure.overlap_volume(a.shape, b.shape))
            except Exception:                                    # noqa: BLE001
                continue
            if v > OVERLAP_TOL_MM3:
                found.append({"a": a.name, "b": b.name, "overlap_mm3": round(v, 2), "links": [la, lb]})
    found.sort(key=lambda f: -f["overlap_mm3"])
    if found:
        top = found[0]
        return _check("bodies do not interfere at rest", "fail",
                      "; ".join(f"{f['a']} and {f['b']} overlap by {f['overlap_mm3']:g} mm^3" for f in found[:4]) + (f" (+{len(found) - 4} more)" if len(found) > 4 else "")
                      + ": move one so it rests on its mount (a board on its standoffs) instead of inside the other",
                      "no overlap between bodies, except a horn seated across a joint", [top["a"], top["b"]]), found
    return _check("bodies do not interfere at rest", "pass", f"no overlap between {len(plist)} bodies at the zero pose (bodies across a joint are graded by the self-collision sweep)", "", []), []


# ---------------------------------------------------------------------------- the payload, carried through the plan

def payload_spec(coupling: dict | None, mech, links: dict[str, list[str]]) -> dict:
    """The payload as the check and the Assembly view use it: {name, shape, size_mm, at, fingers, declared, tip_link, mass_g}.
    Without COUPLING payload= nothing is modelled: shape and size_mm are None, and the mass (m.payload) is all the checks use."""
    p = (coupling or {}).get("payload")
    p = p if isinstance(p, dict) else {}
    tl, _ta = mech.resolved_tip()
    mass = float(sum(pm.mass_g for pm in getattr(mech, "masses", []) if getattr(pm, "kind", "") == "payload"))
    if not p:
        return {"name": "payload", "shape": None, "size_mm": None, "at": None, "fingers": [], "declared": False, "tip_link": tl, "mass_g": mass}
    shape = str(p.get("shape") or "box").lower()
    if shape not in ("box", "cylinder"):
        shape = "box"
    size = p.get("size_mm") or ([20.0] * (3 if shape == "box" else 2))
    size = [float(x) for x in size][: (3 if shape == "box" else 2)]
    if shape == "box" and len(size) == 1:
        size = size * 3
    if shape == "cylinder" and len(size) == 1:
        size = [size[0], size[0]]
    fingers = [str(f) for f in p.get("fingers")] if p.get("fingers") is not None else [str(n) for n in (links or {}).get(tl, [])]
    return {"name": str(p.get("name") or "payload"), "shape": shape, "size_mm": size, "at": p.get("at", "pick"), "fingers": fingers,
            "declared": True, "tip_link": tl, "mass_g": mass}


def payload_solid(spec: dict):
    import build123d as bd
    if spec["shape"] == "cylinder":
        return bd.Cylinder(spec["size_mm"][0] / 2, spec["size_mm"][1])
    return bd.Box(*spec["size_mm"])


def plan(mech, sequence: dict | None, targets: list) -> dict | None:
    """The planned motion, as the Assembly view's Run plays it (static/js/asmrun.js planRun): {source, steps: [{name, q, grab, release}],
    pick, place} with pick / place target dicts ({name, at, tol_mm, pose}), or None when there is nothing to play."""
    home = {j.name: float(j.home) for j in mech.moving_joints}
    tl = [{"name": t.name, "at": list(map(float, t.at)), "tol_mm": float(t.tol_mm), "pose": dict(getattr(t, "pose", {}) or {})} for t in targets or []]
    named = lambda rx: next((t for t in tl if re.search(rx, t["name"], re.I)), None)
    pick = named(r"pick|grab|take") or (tl[0] if tl else None)
    place = named(r"place|drop|put") or (tl[1] if len(tl) > 1 else None)
    if sequence and sequence.get("steps") and sequence.get("joints"):
        steps = []
        for s in sequence["steps"]:
            name = str(s.get("name") or "")
            steps.append({"name": name, "q": {**home, **{k: float(v) for k, v in (s.get("joints") or {}).items()}},
                          "grab": bool(GRAB.search(name)) and not NOT_GRAB.search(name), "release": bool(RELEASE.search(name)) and not NOT_RELEASE.search(name)})
        return {"source": "firmware SEQUENCE", "steps": steps, "pick": pick, "place": place}
    if not tl:
        return None
    steps = [{"name": "home", "q": dict(home), "grab": False, "release": False}]
    if pick and pick["pose"]:
        steps.append({"name": f"reach {pick['name']}", "q": {**home, **pick["pose"]}, "grab": True, "release": False})
    if place and place["pose"]:
        steps.append({"name": f"reach {place['name']}", "q": {**home, **place["pose"]}, "grab": False, "release": True})
    steps.append({"name": "home", "q": dict(home), "grab": False, "release": False})
    return {"source": "mechanism IK", "steps": steps, "pick": pick, "place": place}


def _fmt_pose(q: dict[str, float]) -> str:
    return "{" + ", ".join(f"{k}: {v:.1f}" for k, v in q.items()) + "}"


def payload_sweep(parts, mech, links: dict[str, list[str]], spec: dict, pl: dict | None, budget_s: float = PAYLOAD_BUDGET_S) -> tuple[list[HCheck], dict]:
    """Carry the payload through the plan; the checks and what happened (for the result data)."""
    import build123d as bd
    t0 = time.time()
    if not spec.get("declared") or not spec.get("shape"):
        return [_check("the carried payload clears the arm and the base", "pass",
                       f"no payload body: the {spec.get('mass_g', 0):g} g payload rides in the torque checks as a mass (m.payload); COUPLING payload=dict(shape=..., size_mm=...) models the object when it is asked for",
                       "", [])], {"carried": False, "body": False}
    if pl is None or not any(s["grab"] for s in pl["steps"]):
        return [_check("the carried payload clears the arm and the base", "pass", "the plan never grabs: nothing is carried", "", [])], {"carried": False, "body": True}
    of, _ = link_of(links)
    base = mech.base
    by_name = {p.name: p for p in parts}
    fingers = set(spec["fingers"])
    others = [(p.name, of.get(p.name, base)) for p in parts if p.name not in fingers and of.get(p.name, base) in mech.links]
    solid = payload_solid(spec)
    at = spec["at"]
    rest = None
    if isinstance(at, (list, tuple)) and len(at) == 3:
        rest = np.array(at, dtype=float)
    elif pl.get("pick"):
        tgt = next((t for t in [pl.get("pick"), pl.get("place")] if t and t["name"] == at), None) or pl["pick"]
        rest = np.array(tgt["at"], dtype=float)
    P = None
    if rest is not None:
        P = np.eye(4)
        P[:3, 3] = rest
    events: list[dict] = []
    hits: list[dict] = []
    carried, G = False, None
    tested, truncated = 0, False
    prev = pl["steps"][0]["q"]
    names = [s["name"] for s in pl["steps"]]
    for i, step in enumerate(pl["steps"]):
        q1 = {**prev, **step["q"]}
        if carried and i > 0:
            for k in range(1, SAMPLES_PER_MOVE + 1):
                if time.time() - t0 > budget_s:
                    truncated = True
                    break
                f = k / SAMPLES_PER_MOVE
                q = {n: prev.get(n, 0.0) + (q1.get(n, 0.0) - prev.get(n, 0.0)) * f for n in q1}
                T = mech.transforms(q)
                obj = moved(solid, tip_frame(mech, q) @ G)
                ob = measure.bounding_box(obj)
                tested += 1
                for n, l in others:
                    s = moved(by_name[n].shape, T[l])
                    bb = measure.bounding_box(s)
                    if (ob.max.X < bb.min.X or bb.max.X < ob.min.X or ob.max.Y < bb.min.Y or bb.max.Y < ob.min.Y or ob.max.Z < bb.min.Z or bb.max.Z < ob.min.Z):
                        continue
                    v = float(measure.overlap_volume(obj, s))
                    if v > OVERLAP_TOL_MM3:
                        hits.append({"body": n, "overlap_mm3": round(v, 2), "pose": {a: round(b, 1) for a, b in q.items()},
                                     "move": [names[i - 1], step["name"]], "fraction": round(f, 2)})
        prev = q1
        if step["grab"] and not carried:
            F = tip_frame(mech, q1)
            if P is None:                                       # no pick target: the object appears in the gripper
                P = F.copy()
            G = np.linalg.inv(F) @ P
            carried = True
            events.append({"step": step["name"], "event": "grab", "tip_to_object_mm": round(float(np.linalg.norm(G[:3, 3])), 1), "pose": {a: round(b, 1) for a, b in q1.items()}})
        elif step["release"] and carried:
            P = tip_frame(mech, q1) @ G
            carried = False
            events.append({"step": step["name"], "event": "release", "at": [round(float(x), 2) for x in P[:3, 3]], "pose": {a: round(b, 1) for a, b in q1.items()}})
    data = {"carried": True, "body": True, "events": events, "hits": hits, "samples": tested, "truncated": truncated, "excluded": sorted(fingers)}
    checks: list[HCheck] = []
    label = spec["name"]
    if hits:
        worst: dict[str, dict] = {}
        for h in hits:
            if h["body"] not in worst or h["overlap_mm3"] > worst[h["body"]]["overlap_mm3"]:
                worst[h["body"]] = h
        top = max(worst.values(), key=lambda h: h["overlap_mm3"])
        checks.append(_check("the carried payload clears the arm and the base", "fail",
                             f"{label} and {top['body']} overlap by {top['overlap_mm3']:g} mm^3 at {_fmt_pose(top['pose'])} (moving {top['move'][0]} -> {top['move'][1]}, {top['fraction']:.0%} of the way)"
                             + (f" (+{len(worst) - 1} more bodies)" if len(worst) > 1 else ""),
                             "the carried object touches nothing but the fingers", [top["body"]]))
    else:
        checks.append(_check("the carried payload clears the arm and the base", "warn" if truncated else "pass",
                             f"{label} carried through {tested} poses of the {pl['source']}, clear of every body but the fingers ({', '.join(sorted(fingers)) or 'none'})"
                             + ("; the time budget ran out first" if truncated else ""),
                             "the carried object touches nothing but the fingers", []))
    grab = next((e for e in events if e["event"] == "grab"), None)
    pick = pl.get("pick")
    if grab is not None and pick is not None and isinstance(spec["at"], str):
        tol = max(float(pick.get("tol_mm") or 2.0), 2.0)
        sev = "pass" if grab["tip_to_object_mm"] <= tol else "fail"
        checks.append(_check("the gripper closes on the payload where it rests", sev,
                             f"at step '{grab['step']}' the tool point is {grab['tip_to_object_mm']:g} mm from the {label}'s centre at {pick['name']}",
                             f"within the target's {tol:g} mm", [pick["name"]]))
    return checks, data
