"""Mechanism <-> CAD coupling: what only a project that holds both a mechanism model and a CAD model can check.

The public mechanism kernel (eitri.mechanism) grades a Mechanism from the masses and lengths its author declares. In a multi-domain
project the CAD model is the better source, so the mechanism file couples to it with a module-level COUPLING dict:

    COUPLING = dict(
        cad="arm.py",                                         # the CAD model file in the same folder (default: the only one)
        links={"upper": ["upper_arm", "elbow_servo"],          # link name -> the CAD parts that move with it
               "fore": ["forearm"]},
        print_fill=1.0,                                        # fraction of a printed part's solid mass (default 1.0: an upper bound)
    )

WHAT IS ADDED to the mechanism kernel's own checks (by the suite):
  link masses   each coupled link's mass and centre of mass are taken from the CAD bodies (volume x density x print_fill for a printed
                part, the catalog mass for a purchased one named in the CAD file's HARDWARE), overriding the Link's declared mass_g and cg.
                The check says which source each link used and how far the declaration was off. Actuator and point masses declared on
                the Mechanism are kept: an actuator body bound to a joint counts on its link only when its Actuator declares no mass_g.
  torque        the public kernel's own torque/force grading re-run with those masses; its checks are repeated here, named "(masses from CAD)".
  self-collision the CAD parts are posed through the mechanism's joints and checked for interference, at sampled poses and along
                each joint's range. A pair that already overlaps at the zero pose (a horn seated in a link) is allowed.
  actuators     every actuator bound to the joint it drives and checked to sit at it (eitri_suite.kinematics): its body is the one
                COUPLING actuators= names, else the actuator body named after the joint, else the nearest; it rides on the link it is
                listed under, else the joint's parent link, and links_map (what the Assembly view poses with) carries it there. An
                auto-bound body's mass counts on that link unless the Actuator declares mass_g (then the Actuator carries it).
  placement     every body attached to the base or a link at the zero pose, home and every SEQUENCE step, no interference at rest
                (any links but one joint's two), and the payload, when COUPLING payload= declares one, carried through the plan clear
                of every body but the fingers (eitri_suite.kinematics).
"""
from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any

import numpy as np

from eitri import measure
from eitri.harness.checks import HCheck

from . import catalog as parts_catalog

OVERLAP_TOL_MM3 = 1.0
TIME_BUDGET_S = 40.0
MASS_WARN_RATIO = 0.25          # a declared mass this far from the CAD mass is called out


def _check(category: str, name: str, severity: str, measured: str, expected: str = "", refs=()) -> HCheck:
    return HCheck(name=name, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category=category, refs=list(dict.fromkeys(refs)))


def declared_counts(coupling: dict | None) -> dict[str, dict[str, int]]:
    """What a COUPLING declaration contains, for the project guard."""
    if not isinstance(coupling, dict):
        return {}
    return {"coupling": {"links": len(coupling.get("links") or {})}}


def link_masses(parts, links: dict[str, list[str]], hardware: dict[str, Any] | None, print_fill: float) -> tuple[dict[str, dict], list[str]]:
    """{link: {"mass_g", "com" (world, zero pose), "sources": [...]}} from the CAD bodies, and the problems met."""
    hardware = hardware or {}
    by_name = {p.name: p for p in parts}
    out: dict[str, dict] = {}
    problems: list[str] = []
    for link, names in links.items():
        total, moment, sources = 0.0, np.zeros(3), []
        for name in names:
            p = by_name.get(name)
            if p is None:
                problems.append(f"link {link!r} lists part {name!r}, which the CAD model does not build")
                continue
            try:
                props = measure.mass_properties(p.shape)
            except Exception as exc:                                    # noqa: BLE001
                problems.append(f"{name}: mass properties failed ({type(exc).__name__}: {exc})")
                continue
            com = np.array(props["center_of_mass"], dtype=float)
            ref = hardware.get(name)
            ref = ref.get("id") if isinstance(ref, dict) else ref
            if ref:
                cp = parts_catalog.lookup(str(ref))
                if cp is None or cp.mass_g is None:
                    problems.append(f"{name}: catalog part {ref!r} is unknown or has no mass")
                    continue
                ms = cp.spec("mass_g")
                mass, src = cp.mass_g, f"{name} = catalog {cp.id} {cp.mass_g:g} g [source: {ms.cite}]"
            else:
                mat = getattr(p, "material", None)
                if mat not in measure.DENSITY:
                    problems.append(f"{name}: no material, so no mass (MATERIALS = {{{name!r}: 'pla'}}, or HARDWARE = {{{name!r}: <catalog id>}})")
                    continue
                mass = props["volume"] / 1000.0 * measure.DENSITY[mat] * print_fill
                src = f"{name} = {mat} x {props['volume']:.0f} mm^3" + (f" x fill {print_fill:g}" if print_fill != 1.0 else "")
            total += mass
            moment += mass * com
            sources.append(src)
        if total > 0:
            out[link] = {"mass_g": total, "com": moment / total, "sources": sources}
    return out, problems


def with_cad_masses(mech, derived: dict[str, dict]):
    """A copy of the Mechanism whose coupled links carry the CAD masses and centres of mass (links in `derived`)."""
    from eitri.mechanism.model import _rot_x_to
    m = copy.deepcopy(mech)
    for name, d in derived.items():
        link = m.links.get(name)
        if link is None:
            continue
        j = next((x for x in m.joints if x.child == name), None)
        origin = m.joint_at(j) if j is not None else np.zeros(3)
        R = _rot_x_to(link.direction)[:3, :3]
        link.mass_g = float(d["mass_g"])
        link.cg = R.T @ (np.asarray(d["com"]) - origin)             # world point at the zero pose -> the link's own frame
    return m


def _mass_report(mech, derived: dict[str, dict], links: dict[str, list[str]]) -> HCheck:
    rows, off = [], []
    for name in mech.links:
        if name in derived:
            d, declared = derived[name], mech.links[name].mass_g
            rows.append(f"{name}: {d['mass_g']:.1f} g from CAD" + (f" (declared {declared:g} g)" if declared else " (none declared)"))
            if declared and abs(d["mass_g"] - declared) / max(declared, 1e-9) > MASS_WARN_RATIO:
                off.append(f"{name} declared {declared:g} g, CAD says {d['mass_g']:.1f} g")
        else:
            rows.append(f"{name}: {mech.links[name].mass_g:g} g declared" + (" (no CAD parts coupled)" if name not in links else " (CAD mass unavailable)"))
    sev = "warn" if off else "pass"
    return _check("coupling", "link masses come from the CAD model", sev, "; ".join(rows) + (f". Differences over {MASS_WARN_RATIO:.0%}: " + "; ".join(off) if off else ""),
                  "declared masses agree with the CAD bodies", list(mech.links))


def _collisions(parts, mech, links: dict[str, list[str]], poses: list[dict[str, float]], budget_s: float) -> dict:
    t0 = time.time()
    import build123d as bd
    by_name = {p.name: p for p in parts}
    members = [(n, l) for l, ns in links.items() for n in ns if n in by_name and l in mech.links]
    pairs = [(a, b) for i, (a, la) in enumerate(members) for (b, lb) in members[i + 1:] if la != lb]
    chains = {l: mech.chain(l) for l in {l for _, l in members}}
    origins = {j.name: mech.joint_at(j) for j in mech.joints}

    def posed(q: dict[str, float]) -> dict[str, Any]:
        shapes = {}
        for n, l in members:
            s = by_name[n].shape
            for j in reversed(chains.get(l, [])):
                v = float(q.get(j.name, 0.0))
                if abs(v) < 1e-9 or not j.moving:
                    continue
                o, ax = tuple(float(x) for x in origins[j.name]), tuple(float(x) for x in j.axis)
                s = s.rotate(bd.Axis(o, ax), v) if j.type == "revolute" else s.moved(bd.Location(tuple(float(x) * v for x in ax)))
            shapes[n] = s
        return shapes

    def overlaps(shapes) -> dict[tuple[str, str], float]:
        out = {}
        for a, b in pairs:
            ba, bb = measure.bounding_box(shapes[a]), measure.bounding_box(shapes[b])
            if (ba.max.X < bb.min.X or bb.max.X < ba.min.X or ba.max.Y < bb.min.Y or bb.max.Y < ba.min.Y or ba.max.Z < bb.min.Z or bb.max.Z < ba.min.Z):
                continue
            v = measure.overlap_volume(shapes[a], shapes[b])
            if v > 1e-9:
                out[(a, b)] = v
        return out

    base = overlaps(posed({}))
    found: list[dict] = []
    tested, truncated = 0, False

    def test(q):
        return [{"a": a, "b": b, "overlap_mm3": round(v, 2), "pose": {k: round(x, 1) for k, x in q.items()}}
                for (a, b), v in overlaps(posed(q)).items() if v > base.get((a, b), 0.0) + OVERLAP_TOL_MM3]

    for q in poses:
        if time.time() - t0 > budget_s * 0.6:
            truncated = True
            break
        tested += 1
        found += test(q)
    home = mech.home_pose()
    sweeps: dict[str, dict] = {}
    for j in mech.moving_joints:
        lo, hi = j.limit
        safe, hit = [home[j.name], home[j.name]], False
        for side, end in ((0, lo), (1, hi)):
            for k in range(1, 9):
                if time.time() - t0 > budget_s:
                    truncated = True
                    break
                v = home[j.name] + (end - home[j.name]) * k / 8
                hits = test({**home, j.name: v})
                if hits:
                    hit, found = True, found + hits
                    break
                safe[side] = v
        sweeps[j.name] = {"declared": [lo, hi], "safe": [round(safe[0], 1), round(safe[1], 1)], "collides": hit}
    uniq: dict[tuple, dict] = {}
    for f in found:
        key = (f["a"], f["b"])
        if key not in uniq or f["overlap_mm3"] > uniq[key]["overlap_mm3"]:
            uniq[key] = f
    return {"pairs": sorted(uniq.values(), key=lambda f: -f["overlap_mm3"]), "sweeps": sweeps, "poses_tested": tested, "truncated": truncated,
            "zero_pose_overlaps": {f"{a}/{b}": round(v, 2) for (a, b), v in base.items()}}


def run(mech, coupling: dict, folder: Path, model_path: Path) -> tuple[list[HCheck], dict]:
    """The coupling checks for a built Mechanism whose model file declares `coupling`. Returns (checks, data)."""
    from eitri.mechanism.checks import run_checks, sample_poses
    from eitri.model import load_model
    from .projectfiles import files_by_kind
    if not isinstance(coupling, dict):
        return [_check("coupling", "COUPLING is a dict", "fail", f"got {type(coupling).__name__}", "COUPLING = dict(cad=..., links={...})")], {}
    unknown = [k for k in coupling if k not in ("cad", "links", "print_fill", "payload", "actuators", "base_fixing")]
    links = coupling.get("links") or {}
    if unknown or not isinstance(links, dict) or not links:
        return [_check("coupling", "COUPLING is valid", "fail", (f"unknown key(s) {unknown}; " if unknown else "") + "links must map each link to its CAD parts",
                       "COUPLING = dict(cad='arm.py', links={'upper': ['upper_arm']})")], {}
    cad_files = files_by_kind(folder).get("cad", [])
    name = coupling.get("cad")
    cad_path = (folder / name) if name else (cad_files[0] if len(cad_files) == 1 else None)
    if cad_path is None or not cad_path.is_file():
        return [_check("coupling", "the CAD model for the mechanism is found", "fail", f"{name or 'no CAD model file'} not in the project", "COUPLING cad='<file>.py'", [])], {}
    try:
        lm = load_model(cad_path)
        parts = lm.build()
    except Exception as exc:                                            # noqa: BLE001 - that file's own build reports its error
        return [_check("coupling", "the CAD model builds for the coupling checks", "warn", f"{type(exc).__name__}: {exc}; coupling checks skipped")], {}
    from . import kinematics as K
    fill = float(coupling.get("print_fill", 1.0))
    hardware = dict(getattr(lm.module, "HARDWARE", None) or {})
    bindings = K.actuator_bindings(parts, mech, coupling, hardware)
    links_eff = K.effective_links(links, bindings)
    mass_links = mass_links_for(mech, links, bindings)
    derived, problems = link_masses(parts, mass_links, hardware, fill)
    checks = [_mass_report(mech, derived, mass_links)]
    if problems:
        checks.append(_check("coupling", "every coupled CAD part has a mass", "warn", "; ".join(problems[:6]), "a material or a HARDWARE catalog id per part", []))
    data: dict = {"link_masses_g": {k: round(v["mass_g"], 2) for k, v in derived.items()}, "sources": {k: v["sources"] for k, v in derived.items()},
                  "links_map": links_eff,                     # link -> the CAD parts that ride on it, actuators included: what the Assembly view poses with
                  "actuators": bindings["joints"]}
    checks.append(K.actuator_check(bindings))
    coupled = with_cad_masses(mech, derived)
    cad_checks, _infos, tinfos, _reach, _masses = run_checks(coupled)
    try:                                                    # the mechanism domain's own sources for its torque rule (the actuator's datasheet)
        from eitri.check_sources import attach
        from eitri.mechanism.rules import RULES as MECH_RULES
        attach(cad_checks, MECH_RULES, coupled)
    except Exception:                                       # noqa: BLE001 - eitri_suite.rules gives the rule's own sources then
        pass
    for c in cad_checks:
        if c.name.startswith(("torque", "force")):
            checks.append(HCheck(name=f"{c.name} (masses from CAD)", ok=c.ok, measured=c.measured, expected=c.expected, severity=c.severity, category="coupling", refs=list(c.refs),
                                 source=list(getattr(c, "source", None) or []), not_run=list(getattr(c, "not_run", None) or [])))
    seq = firmware_sequence(folder, mech, Path(model_path).name)
    p_checks, p_data = placement_checks(parts, mech, coupling, folder, tinfos, sequence_data=seq, links=links_eff)
    checks += p_checks
    data.update(p_data)
    from . import stability
    s_check, s_data = stability.check(parts, mech, coupling, links_eff, bindings, hardware, getattr(lm.module, "PURCHASED", None), seq)
    checks.append(s_check)
    data["stability"] = s_data
    col = _collisions(parts, mech, links_eff, sample_poses(mech)[: int(getattr(mech, "poses", 24)) or 24], TIME_BUDGET_S)
    data["collisions"] = col
    if col["pairs"]:
        top = col["pairs"][0]
        checks.append(_check("coupling", "self-collision (CAD geometry)", "fail",
                             f"{top['a']} and {top['b']} overlap by {top['overlap_mm3']:g} mm^3 at {top['pose']}" + (f" (+{len(col['pairs']) - 1} more pairs)" if len(col["pairs"]) > 1 else ""),
                             "no interference between parts on different links", [top["a"], top["b"]]))
    else:
        checks.append(_check("coupling", "self-collision (CAD geometry)", "warn" if col["truncated"] else "pass",
                             f"no interference in {col['poses_tested']} poses and along every joint's range" + ("; the time budget ran out first, so not every pose was tested" if col["truncated"] else ""),
                             "no interference between parts on different links", []))
    return checks, data


def firmware_sequence(folder: Path, mech, mech_name: str | None = None) -> dict | None:
    """The joint-space SEQUENCE of the project's firmware file that drives this mechanism (COUPLING mechanism=, else the only
    mechanism), as eitri_suite.assembly.sequence computes it; None when there is none."""
    from eitri.model import load_model
    from .assembly import sequence
    from .projectfiles import files_by_kind
    by_kind = files_by_kind(folder)
    for f in by_kind.get("firmware", []):
        try:
            text = f.read_text(encoding="utf-8")
            if "SEQUENCE" not in text:
                continue
            lm = load_model(f)
            fw = lm.build()
        except Exception:                                   # noqa: BLE001 - that file's own build reports its error
            continue
        c = getattr(lm.module, "COUPLING", None)
        target = (c or {}).get("mechanism") if isinstance(c, dict) else None
        if target and mech_name and target != mech_name:
            continue
        if not target and len(by_kind.get("mechanism", [])) != 1:
            continue
        _checks, seq = sequence(lm.module, fw, mech)
        if seq is not None:
            return seq
    return None


def mass_links_for(mech, links: dict, bindings: dict) -> dict[str, list[str]]:
    """The links map the masses are derived from: COUPLING links, plus each auto-bound actuator body on its link when its Actuator
    declares no mass_g (else the Actuator carries the motor's mass and the body must not count twice)."""
    out = {str(k): [str(n) for n in (v or [])] for k, v in (links or {}).items()}
    listed = {n for ns in out.values() for n in ns}
    for jn, b in (bindings.get("joints") or {}).items():
        if not b.get("part") or b["part"] in listed or not b.get("link"):
            continue
        j = next((x for x in mech.joints if x.name == jn), None)
        if j is not None and j.actuator is not None and float(j.actuator.mass_g or 0) <= 0:
            out.setdefault(b["link"], []).append(b["part"])
    return out


def placement_checks(parts, mech, coupling: dict, folder: Path, tinfos=None, sequence_data: dict | None = None, mech_name: str | None = None,
                     links: dict | None = None) -> tuple[list[HCheck], dict]:
    """Where the bodies are in the assembly (eitri_suite.kinematics): every body attached at every keyframe, no interference at rest,
    and the payload carried through the plan (the firmware SEQUENCE when there is one, else the IK targets). `links`: the effective
    map (actuators bound), else COUPLING's."""
    from . import kinematics as K
    links = links if links is not None else (coupling.get("links") or {})
    checks: list[HCheck] = []
    seq = sequence_data if sequence_data is not None else firmware_sequence(folder, mech, mech_name)
    a_check, a_info = K.attachment_check(parts, mech, links, poses=K.keyframes(mech, seq))
    i_check, i_found = K.interference_check(parts, mech, links)
    checks += [a_check, i_check]
    if tinfos is None:
        from eitri.mechanism.checks import run_checks
        tinfos = run_checks(mech)[2]
    spec = K.payload_spec(coupling, mech, links)
    pl = K.plan(mech, seq, tinfos)
    p_checks, p_data = K.payload_sweep(parts, mech, links, spec, pl)
    checks += p_checks
    return checks, {"attachment": a_info["bodies"], "interference": i_found, "payload": {**spec, **p_data, "source": pl["source"] if pl else None}}
