"""What the agent's `inspect` tool has in scope for a mechanism project (plain functions that print).

    m                 the built Mechanism (also `parts`); p is PARAMS
    joints()          every moving joint: limits, actuator, required vs available torque/force and speed
    links()           every link: length, mass, centre of mass, what moves it
    fk(**q)           joint and tip positions at a pose (angles in degrees / slides in mm; missing joints at home)
    torque(**q)       the static holding load of every joint at a pose
    reach(target)     how close the tip gets to a target point or a named target
    masses()          the mass rollup
    report()          the full text report again
"""
from __future__ import annotations

import numpy as np

from .checks import joint_loads, mass_rollup, sample_poses, solve_ik
from .model import Mechanism


def mechanism_scope(m: Mechanism, p=None) -> dict:
    import eitri.mechanism as M
    from .report import analyze, summarize

    def joints():
        rep = analyze(m)
        for j in rep.joint_info:
            act = f"{j.actuator}: stall {j.stall:.2f}, rated {j.rated:.2f} {j.load_unit}{' (assumed)' if j.rated_is_estimate else ''}" if j.stall is not None else "no actuator"
            sp = f"; speed {j.speed_available:.0f}" + (f" vs {j.speed_required:g} required" if j.speed_required else "") if j.speed_available is not None else ""
            print(f"{j.name} ({j.type}): {j.limit[0]:g}..{j.limit[1]:g} {j.unit}; needs {j.required:.2f} {j.load_unit} at {j.worst_pose}; {act}"
                  + (f"; margin {j.margin_stall:.2f}x stall" if j.margin_stall is not None else "") + sp + f"; moves {j.moving_mass_g:g} g; {j.status}")

    def links():
        for l in m.links.values():
            by = "fixed (ground)" if l.fixed else next((f"moved by {j.name}" for j in m.joints if j.child == l.name), "not attached")
            print(f"{l.name}: {l.length_mm:g} mm, {l.mass_g:g} g, cg ({l.cg[0]:g}, {l.cg[1]:g}, {l.cg[2]:g}), {by}")

    def fk(**q):
        pts = m.points(q)
        for name, pt in pts["joints"].items():
            print(f"joint {name}: ({pt[0]:.1f}, {pt[1]:.1f}, {pt[2]:.1f})")
        t = pts["tip"]
        print(f"tip: ({t[0]:.1f}, {t[1]:.1f}, {t[2]:.1f})")
        return pts

    def torque(**q):
        loads = joint_loads(m, q)
        for name, v in loads.items():
            j = m.get_joint(name)
            print(f"{name}: {v:.3f} {'kg·cm' if j.type == 'revolute' else 'N'}")
        return loads

    def reach(target):
        if isinstance(target, str):
            t = next((t for t in m.targets if t.name == target), None)
            if t is None:
                print(f"no target {target!r}")
                return None
            at = t.at
        else:
            at = np.array([float(x) for x in target])
        d, pose = solve_ik(m, at, sample_poses(m))
        print(f"closest {d:.2f} mm at " + ", ".join(f"{k} {v:.1f}" for k, v in pose.items()))
        return d

    def masses():
        r = mass_rollup(m)
        for k, v in r["links_g"].items():
            print(f"{k}: {v:g} g")
        print(f"payload {r['payload_g']:g} g; total {r['total_g']:g} g; moving per joint: " + ", ".join(f"{k} {v:g} g" for k, v in r["moving_g"].items()))
        return r

    def report():
        print(summarize(analyze(m)))

    return {"m": m, "parts": m, "p": p, "joints": joints, "links": links, "fk": fk, "torque": torque, "reach": reach, "masses": masses,
            "report": report, "joint_loads": joint_loads, "solve_ik": solve_ik, "sample_poses": sample_poses, "np": np,
            **{k: getattr(M, k) for k in M.__all__}}
