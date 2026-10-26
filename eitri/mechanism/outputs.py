"""The mechanism's tables: the joint / torque summary and the mass rollup, as Markdown and CSV."""
from __future__ import annotations

import csv
import io

from .checks import mass_items
from .model import Mechanism
from .report import MechanismReport


def _md_table(headers: list[str], rows: list[list]) -> str:
    esc = lambda x: str(x).replace("|", "\\|")
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _num(v, nd=2) -> str:
    return "" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def joint_rows(rep: MechanismReport) -> list[dict]:
    rows = []
    for j in rep.joint_info:
        rows.append({"joint": j.name, "type": j.type, "limit_lo": j.limit[0], "limit_hi": j.limit[1], "unit": j.unit, "actuator": j.actuator or "",
                     "required": j.required, "load_unit": j.load_unit, "stall": j.stall, "rated": j.rated, "rated_estimate": j.rated_is_estimate,
                     "margin_stall": j.margin_stall, "margin_rated": j.margin_rated, "speed_required": j.speed_required, "speed_available": j.speed_available,
                     "moving_mass_g": j.moving_mass_g, "status": j.status, "worst_pose": "; ".join(f"{k}={v:g}" for k, v in j.worst_pose.items())})
    return rows


_JOINT_COLS = ["joint", "type", "limit_lo", "limit_hi", "unit", "actuator", "required", "load_unit", "stall", "rated", "rated_estimate", "margin_stall",
               "margin_rated", "speed_required", "speed_available", "moving_mass_g", "status", "worst_pose"]


def joints_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, _JOINT_COLS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in _JOINT_COLS})
    return buf.getvalue()


def joints_markdown(m: Mechanism, rep: MechanismReport) -> str:
    rows = [[r["joint"], r["type"], f"{r['limit_lo']:g}..{r['limit_hi']:g} {r['unit']}", r["actuator"] or "-", f"{_num(r['required'])} {r['load_unit']}",
             (_num(r["stall"], 1) + " / " + _num(r["rated"], 1) + (" (assumed)" if r["rated_estimate"] else "")) if r["stall"] is not None else "-",
             (_num(r["margin_stall"]) + "x / " + _num(r["margin_rated"]) + "x") if r["margin_stall"] is not None else "-",
             (_num(r["speed_required"], 0) if r["speed_required"] is not None else "-") + " / " + (_num(r["speed_available"], 0) if r["speed_available"] is not None else "-"),
             f"{r['moving_mass_g']:g}", r["status"]] for r in joint_rows(rep)]
    table = _md_table(["joint", "type", "limits", "actuator", "required (worst pose)", "stall / rated", "margin stall / rated", "speed req / avail", "moves (g)", "status"], rows)
    tg = [[t.name, f"({t.at[0]:g}, {t.at[1]:g}, {t.at[2]:g})", f"{t.tol_mm:g}", f"{t.distance_mm:g}", "yes" if t.reachable else "NO"] for t in rep.target_info]
    out = f"# Joints: {m.name}\n\nLoads are static holding loads at the worst sampled pose (gravity only); rated = recommended continuous.\n\n{table}\n"
    if tg:
        out += "\n## Targets\n\n" + _md_table(["target", "at (mm)", "tolerance (mm)", "closest (mm)", "reached"], tg) + "\n"
    if rep.reach:
        out += f"\nReach: tip radius up to {rep.reach.get('max_radius_mm', 0):g} mm, height {rep.reach.get('z_min_mm', 0):g}..{rep.reach.get('z_max_mm', 0):g} mm over {rep.reach.get('poses', 0)} poses.\n"
    return out


def mass_rows(m: Mechanism) -> list[dict]:
    rows = []
    for name, link, g in mass_items(m):
        kind = "link" if name in m.links else next((pm.kind for pm in m.masses if pm.name == name), "actuator")
        rows.append({"item": name, "kind": kind, "on_link": link, "mass_g": round(g, 2)})
    return rows


def mass_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, ["item", "kind", "on_link", "mass_g"], lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def mass_markdown(m: Mechanism, rep: MechanismReport) -> str:
    rows = [[r["item"], r["kind"], r["on_link"], f"{r['mass_g']:g}"] for r in mass_rows(m)]
    per = rep.masses.get("links_g", {})
    total = rep.masses.get("total_g", sum(r["mass_g"] for r in mass_rows(m)))
    out = f"# Mass rollup: {m.name}\n\n" + _md_table(["item", "kind", "on link", "mass (g)"], rows) + f"\n\nPer link (with what it carries): " + ", ".join(f"{k} {v:g} g" for k, v in per.items()) + f". Total {total:g} g.\n"
    return out
