"""Direct edits of a circuit (the editor's inspector changes), kept in the model file's EDITS list and applied after build(p)."""
from __future__ import annotations

from .model import Circuit, PneumaticsError, Tube, _pos

PNEUMATICS_OPS = frozenset({"set_component", "set_tube", "add_tube", "remove_tube", "set_vent"})
_FIELDS = ("pressure_bar", "flow_l_min", "set_bar", "lubricator", "cls", "actuation", "returns", "normally", "center", "bore_mm", "rod_mm", "stroke_mm", "acting",
           "load_N", "load_retract_N", "speed_mm_s", "speed_retract_mm_s", "cycles_per_min", "spring_N", "friction_fraction", "meter", "C", "b", "max_bar", "part_number", "notes")
_NUMERIC = {"pressure_bar", "flow_l_min", "set_bar", "bore_mm", "rod_mm", "stroke_mm", "load_N", "load_retract_N", "speed_mm_s", "speed_retract_mm_s", "cycles_per_min",
            "spring_N", "friction_fraction", "C", "b", "max_bar"}


def target_of(e: dict) -> str:
    return str(e.get("component") or e.get("tube") or e.get("name") or e.get("port") or "")


def describe(e: dict) -> str:
    op = e.get("op")
    if op == "set_component":
        return f"{e.get('component')}: " + ", ".join(f"{k} = {e[k]}" for k in _FIELDS if k in e)
    if op == "set_tube":
        return f"tube {e.get('tube')}: " + ", ".join(f"{k} = {e[k]}" for k in ("id_mm", "length_m", "fittings", "C", "part_number") if k in e)
    if op == "add_tube":
        return f"add tube {e.get('name')} {e.get('a')} -> {e.get('b')} ({e.get('id_mm', 6)} mm, {e.get('length_m', 1)} m)"
    if op == "remove_tube":
        return f"remove tube {e.get('tube')}"
    if op == "set_vent":
        return f"{e.get('port')} {'vented' if e.get('vent', True) else 'closed'}"
    return str(e)


def merge_edit(prev: dict, new: dict) -> dict | None:
    if prev.get("op") != new.get("op") or prev.get("suppressed") or new.get("suppressed"):
        return None
    key = {"set_component": "component", "set_tube": "tube"}.get(new.get("op"))
    if key and prev.get(key) == new.get(key):
        return {**prev, **{k: v for k, v in new.items() if k != "by"}}
    return None


def _apply_one(c: Circuit, e: dict) -> str:
    op = e["op"]
    if op == "set_component":
        comp = c.get(e["component"])
        changed = []
        for k in _FIELDS:
            if k not in e or not hasattr(comp, k):
                continue
            v = e[k]
            if k in _NUMERIC:
                v = None if v in (None, "") else float(v)
            if k == "C" and v is not None:
                v = _pos(v, f"{comp.name} C")
            if k == "b" and v is not None and not 0 <= v < 1:
                raise PneumaticsError(f"{comp.name}: b must be in [0, 1)")
            if k == "max_bar" and v is not None and v <= 0:
                raise PneumaticsError(f"{comp.name}: max_bar must be positive")
            if k in ("bore_mm", "rod_mm", "stroke_mm", "set_bar", "pressure_bar") and (v is None or v <= 0):
                raise PneumaticsError(f"{comp.name}: {k} must be positive")
            setattr(comp, k, v)
            changed.append(k)
        if comp.kind == "cylinder" and comp.rod_mm >= comp.bore_mm:
            raise PneumaticsError(f"{comp.name}: rod_mm must be smaller than bore_mm")
        if comp.kind == "valve" and "cls" in e:
            from .model import VALVE_CLASSES
            if comp.cls not in VALVE_CLASSES:
                raise PneumaticsError(f"{comp.name}: class {comp.cls!r} is not one of {list(VALVE_CLASSES)}")
            comp.ports = list(VALVE_CLASSES[comp.cls]["ports"])
        if comp.kind == "cylinder" and "acting" in e:
            comp.ports = ["CAP", "ROD"] if comp.acting == "double" else ["CAP"]
        return f"{comp.name}: " + ", ".join(f"{k} = {getattr(comp, k)}" for k in changed)
    if op == "set_tube":
        t = c.get_tube(e["tube"])
        if "id_mm" in e:
            t.id_mm = _pos(e["id_mm"], f"tube {t.name} id_mm")
        if "length_m" in e:
            t.length_m = _pos(e["length_m"], f"tube {t.name} length_m")
        if "fittings" in e:
            t.fittings = max(0, int(e["fittings"]))
        if "C" in e:
            t.C = None if e["C"] in (None, "") else _pos(e["C"], f"tube {t.name} C")
        if "part_number" in e:
            t.part_number = str(e["part_number"])
        return f"{t.name}: {t.id_mm:g} mm x {t.length_m:g} m"
    if op == "add_tube":
        t = c.tube(str(e["name"]), e["a"], e["b"], float(e.get("id_mm", 6)), float(e.get("length_m", 1)))
        return f"{t.name}: {t.a} -> {t.b}"
    if op == "remove_tube":
        t = c.get_tube(e["tube"])
        c.tubes.remove(t)
        return f"removed {t.name}"
    if op == "set_vent":
        ref = c._port(e["port"])
        if e.get("vent", True):
            c.vents.add(ref)
        else:
            c.vents.discard(ref)
        return f"{ref} {'vented' if e.get('vent', True) else 'closed'}"
    raise PneumaticsError(f"unknown pneumatics edit {op!r}")


def apply_edits(c: Circuit, edits: list[dict]) -> list:
    from ..edits import EditNote
    notes = []
    for e in edits:
        op = e.get("op")
        if op not in PNEUMATICS_OPS:
            notes.append(EditNote(str(op), "", False, f"{op!r} is not a pneumatics edit"))
            continue
        if e.get("suppressed"):
            notes.append(EditNote(op, target_of(e), True, "suppressed"))
            continue
        try:
            notes.append(EditNote(op, target_of(e), True, _apply_one(c, e)))
        except (PneumaticsError, KeyError, ValueError, TypeError) as exc:
            notes.append(EditNote(op, target_of(e), False, f"{type(exc).__name__}: {exc}"))
    return notes


def describe_state(c: Circuit) -> dict:
    out: dict = {}
    for comp in c.components.values():
        d = {"kind": comp.kind}
        for k in ("pressure_bar", "set_bar", "cls", "bore_mm", "rod_mm", "stroke_mm", "load_N", "speed_mm_s", "C", "b"):
            if hasattr(comp, k):
                d[k] = getattr(comp, k)
        out[comp.name] = d
    for t in c.tubes:
        out[f"tube {t.name}"] = {"a": str(t.a), "b": str(t.b), "id_mm": t.id_mm, "length_m": t.length_m}
    return out
