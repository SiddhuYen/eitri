"""The circuit's BOM (components with part numbers, tube by bore with a service loop, fittings, silencers) and the cylinder table."""
from __future__ import annotations

import csv
import io
from collections import OrderedDict

from .model import Circuit
from .report import CircuitReport

SERVICE_LOOP = 0.10


def _md_table(headers, rows) -> str:
    esc = lambda x: str(x).replace("|", "\\|")
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _desc(comp) -> str:
    k = comp.kind
    if k == "cylinder":
        return f"cylinder Ø{comp.bore_mm:g} x {comp.stroke_mm:g} mm, {comp.acting}-acting, rod Ø{comp.rod_mm:g}"
    if k == "valve":
        return f"{comp.cls} valve, {comp.actuation} actuated, {comp.returns} return" + (f", C {comp.C:g} L/(s·bar)" if comp.C else "")
    if k == "frl":
        return f"filter-regulator{'-lubricator' if comp.lubricator else ''} unit, set {comp.set_bar:g} bar" + (f", C {comp.C:g}" if comp.C else "")
    if k == "regulator":
        return f"pressure regulator, set {comp.set_bar:g} bar" + (f", C {comp.C:g}" if comp.C else "")
    if k == "supply":
        return f"air supply {comp.pressure_bar:g} bar" + (f", {comp.flow_l_min:g} L/min" if comp.flow_l_min else "")
    if k == "flow_control":
        return f"speed controller (throttle-check), meter-{comp.meter}" + (f", C {comp.C:g}" if comp.C else "")
    if k == "silencer":
        return "exhaust silencer"
    if k == "node":
        return "tee / manifold junction"
    return k


def bom(c: Circuit) -> list[dict]:
    rows: list[dict] = []

    def add(category, description, qty, unit="pc", part_number="", notes=""):
        rows.append({"category": category, "description": description, "qty": qty, "unit": unit, "part_number": part_number, "notes": notes})

    groups: OrderedDict[tuple, list] = OrderedDict()
    for comp in c.components.values():
        if comp.kind == "supply":
            continue
        groups.setdefault((comp.kind, _desc(comp), comp.part_number), []).append(comp.name)
    for (kind, desc, pn), names in groups.items():
        add(kind, desc, len(names), part_number=pn, notes=", ".join(names))
    tubes: OrderedDict[float, list] = OrderedDict()
    for t in c.tubes:
        tubes.setdefault(t.id_mm, []).append(t)
    for bore, ts in tubes.items():
        total = sum(t.length_m for t in ts)
        add("tube", f"pneumatic tube, {bore:g} mm bore (ID)", round(total * (1 + SERVICE_LOOP), 2), "m", part_number=ts[0].part_number, notes=f"{len(ts)} run(s), {total:g} m cut + {int(SERVICE_LOOP * 100)}% service loop")
        n_fit = sum(t.fittings for t in ts)
        if n_fit:
            add("fitting", f"push-in fitting for the {bore:g} mm-bore tube (fittings are sized by the tube's OD)", n_fit, notes="one per tube end (and inline fittings as declared)")
    for i, r in enumerate(rows, 1):
        r["item"] = i
    return rows


_COLS = ["item", "category", "description", "qty", "unit", "part_number", "notes"]


def bom_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, _COLS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in _COLS})
    return buf.getvalue()


def bom_markdown(c: Circuit, rows: list[dict] | None = None) -> str:
    rows = rows if rows is not None else bom(c)
    return f"# Bill of materials: {c.name}\n\n" + _md_table(["#", "category", "description", "qty", "unit", "part number", "notes"],
                                                            [[r["item"], r["category"], r["description"], r["qty"], r["unit"], r["part_number"], r["notes"]] for r in rows]) + "\n"


def cylinder_rows(rep: CircuitReport) -> list[dict]:
    out = []
    for cy in rep.cylinders:
        for s in cy.strokes:
            out.append({"cylinder": cy.name, "stroke": s.name, "bore_mm": cy.bore_mm, "rod_mm": cy.rod_mm, "stroke_mm": cy.stroke_mm, "pressure_bar": cy.pressure_bar,
                        "load_N": s.load_N, "usable_N": None if s.usable_N is None else round(s.usable_N, 1), "load_ratio": None if s.load_ratio is None else round(s.load_ratio, 3),
                        "speed_required_mm_s": s.speed_required, "speed_available_mm_s": None if s.speed_available is None else round(s.speed_available, 1),
                        "flow_required_l_s": None if s.flow_required_l_s is None else round(s.flow_required_l_s, 3), "air_per_minute_l": round(cy.air_per_minute_l, 2), "status": s.status})
    return out


_CYL_COLS = ["cylinder", "stroke", "bore_mm", "rod_mm", "stroke_mm", "pressure_bar", "load_N", "usable_N", "load_ratio", "speed_required_mm_s", "speed_available_mm_s", "flow_required_l_s", "air_per_minute_l", "status"]


def cylinders_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, _CYL_COLS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in _CYL_COLS})
    return buf.getvalue()


def cylinders_markdown(c: Circuit, rep: CircuitReport) -> str:
    rows = [[r["cylinder"], r["stroke"], f"Ø{r['bore_mm']:g}/{r['rod_mm']:g} x {r['stroke_mm']:g}", "" if r["pressure_bar"] is None else f"{r['pressure_bar']:g}", f"{r['load_N']:g}",
             "" if r["usable_N"] is None else f"{r['usable_N']:g}", "" if r["load_ratio"] is None else f"{r['load_ratio']:.2f}",
             ("" if r["speed_required_mm_s"] is None else f"{r['speed_required_mm_s']:g}") + " / " + ("" if r["speed_available_mm_s"] is None else f"{r['speed_available_mm_s']:g}"),
             f"{r['air_per_minute_l']:g}", r["status"]] for r in cylinder_rows(rep)]
    out = f"# Cylinders: {c.name}\n\n" + _md_table(["cylinder", "stroke", "size (mm)", "bar", "load (N)", "usable (N)", "load ratio", "speed req / avail (mm/s)", "air (L/min)", "status"], rows) + "\n"
    for s in rep.supplies:
        out += f"\nSupply {s.name}: {s.pressure_bar:g} bar, {s.demand_l_min:g} L/min drawn" + (f" of {s.capacity_l_min:g} L/min" if s.capacity_l_min else "") + ".\n"
    return out
