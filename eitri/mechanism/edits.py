"""Direct edits of a mechanism: the editor's inspector changes, kept in the model file's EDITS list and applied after build(p)
(the shared direct-edit path of eitri/edits.py)."""
from __future__ import annotations

from .model import Actuator, Mechanism, MechanismError, _unit, _vec

MECHANISM_OPS = frozenset({"set_joint", "set_actuator", "set_link", "set_payload", "set_target", "add_target", "remove_target", "set_speed"})
_ACT_FIELDS = ("name", "stall_kgcm", "rated_kgcm", "stall_N", "rated_N", "speed_deg_s", "speed_mm_s", "gear_ratio", "efficiency", "travel_deg", "travel_mm", "mass_g")


def target_of(e: dict) -> str:
    return str(e.get("joint") or e.get("link") or e.get("target") or e.get("name") or "")


def describe(e: dict) -> str:
    op = e.get("op")
    if op == "set_joint":
        return f"joint {e.get('joint')}: " + ", ".join(f"{k} = {e[k]}" for k in ("limit", "home", "axis", "at") if k in e)
    if op == "set_actuator":
        if e.get("remove"):
            return f"joint {e.get('joint')}: no actuator"
        return f"actuator of {e.get('joint')}: " + ", ".join(f"{k} = {e[k]}" for k in _ACT_FIELDS if k in e)
    if op == "set_link":
        return f"link {e.get('link')}: " + ", ".join(f"{k} = {e[k]}" for k in ("length_mm", "mass_g", "cg", "direction") if k in e)
    if op == "set_payload":
        return "payload: " + ", ".join(f"{k} = {e[k]}" for k in ("mass_g", "link", "at") if k in e)
    if op == "set_target":
        return f"target {e.get('target')}: " + ", ".join(f"{k} = {e[k]}" for k in ("at", "tol_mm") if k in e)
    if op == "add_target":
        return f"add target {e.get('name')} at {e.get('at')}"
    if op == "remove_target":
        return f"remove target {e.get('target')}"
    if op == "set_speed":
        return f"required speed of {e.get('joint')} = {e.get('value')}"
    return str(e)


def merge_edit(prev: dict, new: dict) -> dict | None:
    if prev.get("op") != new.get("op") or prev.get("suppressed") or new.get("suppressed"):
        return None
    key = {"set_joint": "joint", "set_actuator": "joint", "set_link": "link", "set_target": "target", "set_speed": "joint", "set_payload": None}.get(new.get("op"), "")
    if key == "":
        return None
    if key is None or prev.get(key) == new.get(key):
        return {**prev, **{k: v for k, v in new.items() if k != "by"}}
    return None


def _num(v, what: str) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise MechanismError(f"{what} must be a number, got {v!r}") from None


def _apply_one(m: Mechanism, e: dict) -> str:
    op = e["op"]
    if op == "set_joint":
        j = m.get_joint(e["joint"])
        if "limit" in e and j.moving:
            lo, hi = float(e["limit"][0]), float(e["limit"][1])
            if not lo < hi:
                raise MechanismError(f"{j.name}: the limit's low end must be below its high end")
            j.limit = (lo, hi)
            j.home = min(max(j.home, lo), hi)
        if "home" in e and j.moving:
            h = float(e["home"])
            if not j.limit[0] <= h <= j.limit[1]:
                raise MechanismError(f"{j.name}: home {h:g} is outside the limits")
            j.home = h
        if "axis" in e:
            j.axis = _unit(e["axis"], f"joint {j.name} axis")
        if "at" in e:
            j.at = None if e["at"] is None else _vec(e["at"], f"joint {j.name} at")
        return f"{j.name}: {j.limit[0]:g}..{j.limit[1]:g} {j.unit}" if j.limit else f"{j.name}: fixed"
    if op == "set_actuator":
        j = m.get_joint(e["joint"])
        if e.get("remove"):
            j.actuator = None
            return f"{j.name}: no actuator"
        base = j.actuator
        fields = {k: getattr(base, k) for k in _ACT_FIELDS} if base else {}
        for k in _ACT_FIELDS:
            if k in e:
                fields[k] = (str(e[k]) if k == "name" else (_num(e[k], f"{j.name} actuator {k}") if k not in ("gear_ratio", "efficiency", "mass_g") else float(e[k])))
        fields.setdefault("name", "actuator")
        if fields.get("gear_ratio") is None:
            fields["gear_ratio"] = 1.0
        if fields.get("efficiency") is None:
            fields["efficiency"] = 1.0
        if fields.get("mass_g") is None:
            fields["mass_g"] = 0.0
        j.actuator = Actuator(**fields)
        s = j.actuator.stall_out(j.type)
        return f"{j.name}: {j.actuator.name}" + (f", stall {s:g} at the joint" if s is not None else "")
    if op == "set_link":
        l = m.get_link(e["link"])
        if "length_mm" in e:
            v = float(e["length_mm"])
            if v < 0:
                raise MechanismError(f"{l.name}: length_mm must not be negative")
            default_cg = bool(abs(l.cg[0] - l.length_mm / 2) < 1e-9 and abs(l.cg[1]) < 1e-9 and abs(l.cg[2]) < 1e-9)
            l.length_mm = v
            if default_cg and "cg" not in e:
                l.cg = _vec((v / 2, 0, 0), "cg")
        if "mass_g" in e:
            v = float(e["mass_g"])
            if v < 0:
                raise MechanismError(f"{l.name}: mass_g must not be negative")
            l.mass_g = v
        if "cg" in e and e["cg"] is not None:
            l.cg = _vec(e["cg"], f"link {l.name} cg")
        if "direction" in e:
            l.direction = _unit(e["direction"], f"link {l.name} direction")
        return f"{l.name}: {l.length_mm:g} mm, {l.mass_g:g} g"
    if op == "set_payload":
        pl = next((pm for pm in m.masses if pm.kind == "payload"), None)
        if pl is None:
            pl = m.payload(float(e.get("mass_g", 0)))
        if "mass_g" in e:
            v = float(e["mass_g"])
            if v < 0:
                raise MechanismError("payload mass_g must not be negative")
            pl.mass_g = v
        if "link" in e:
            if e["link"] and str(e["link"]) not in m.links:
                raise MechanismError(f"payload: no link {e['link']!r}")
            pl.link = str(e["link"] or "")
        if "at" in e:
            pl.at = None if e["at"] is None else _vec(e["at"], "payload at")
        return f"payload {pl.mass_g:g} g" + (f" on {pl.link}" if pl.link else "")
    if op == "set_target":
        t = next((t for t in m.targets if t.name == e["target"]), None)
        if t is None:
            raise MechanismError(f"no target {e['target']!r}")
        if "at" in e:
            t.at = _vec(e["at"], f"target {t.name} at")
        if "tol_mm" in e:
            v = float(e["tol_mm"])
            if v <= 0:
                raise MechanismError("tol_mm must be positive")
            t.tol_mm = v
        return f"{t.name} at ({t.at[0]:g}, {t.at[1]:g}, {t.at[2]:g}) ± {t.tol_mm:g} mm"
    if op == "add_target":
        t = m.target(str(e["name"]), e["at"], float(e.get("tol_mm", 2.0)))
        return f"{t.name} at ({t.at[0]:g}, {t.at[1]:g}, {t.at[2]:g})"
    if op == "remove_target":
        before = len(m.targets)
        m.targets = [t for t in m.targets if t.name != e["target"]]
        if len(m.targets) == before:
            raise MechanismError(f"no target {e['target']!r}")
        return f"removed {e['target']}"
    if op == "set_speed":
        j = m.get_joint(e["joint"])
        if e.get("value") in (None, ""):
            m.speeds.pop(j.name, None)
            return f"{j.name}: no speed requirement"
        m.speed(j.name, deg_s=float(e["value"]) if j.type == "revolute" else None, mm_s=float(e["value"]) if j.type == "prismatic" else None)
        return f"{j.name}: {float(e['value']):g} {'°/s' if j.type == 'revolute' else 'mm/s'} required"
    raise MechanismError(f"unknown mechanism edit {op!r}")


def apply_edits(m: Mechanism, edits: list[dict]) -> list:
    from ..edits import EditNote
    notes = []
    for e in edits:
        op = e.get("op")
        if op not in MECHANISM_OPS:
            notes.append(EditNote(str(op), "", False, f"{op!r} is not a mechanism edit"))
            continue
        if e.get("suppressed"):
            notes.append(EditNote(op, target_of(e), True, "suppressed"))
            continue
        try:
            notes.append(EditNote(op, target_of(e), True, _apply_one(m, e)))
        except (MechanismError, KeyError, ValueError, TypeError, IndexError) as exc:
            notes.append(EditNote(op, target_of(e), False, f"{type(exc).__name__}: {exc}"))
    return notes


def describe_state(m: Mechanism) -> dict:
    out: dict = {}
    for name, l in m.links.items():
        out[f"link {name}"] = {"length_mm": l.length_mm, "mass_g": l.mass_g}
    for j in m.joints:
        out[f"joint {j.name}"] = {"type": j.type, "limit": list(j.limit) if j.limit else None, "actuator": j.actuator.name if j.actuator else None,
                                  "stall": j.actuator.stall_out(j.type) if j.actuator else None}
    for t in m.targets:
        out[f"target {t.name}"] = {"at": [float(x) for x in t.at], "tol_mm": t.tol_mm}
    for pm in m.masses:
        out[pm.name] = {"mass_g": pm.mass_g, "link": pm.link}
    return out
