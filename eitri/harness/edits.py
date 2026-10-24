"""Direct edits of a harness: the editor's inspector changes, kept in the model file's EDITS list.

The same mechanism as CAD direct edits (see eitri/edits.py): each edit is a dict with an `op`; the list sits in
the model file, build(p) runs first and the edits are applied to the Harness it returned, in order. The agent
reads them with list_edits and folds them into build(p) when convenient. An edit that no longer applies (its wire
was removed from the code) is reported as FAILED and skipped, never silently lost.
"""
from __future__ import annotations

from typing import Any

from .catalog import Gauge, normalize_color, normalize_type
from .model import Connector, Harness, HarnessError, PinRef, Wire

HARNESS_OPS = frozenset({"set_wire", "set_connector", "rename_pin", "add_pin", "remove_pin", "add_wire", "remove_wire",
                         "set_net", "nc"})

_WIRE_FIELDS = ("gauge", "color", "length_mm", "net", "current_A", "label")
_CONN_FIELDS = ("name", "type", "part_number", "gender", "notes", "rating_a")


def target_of(e: dict) -> str:
    return str(e.get("wire") or e.get("connector") or e.get("net") or e.get("pin") or e.get("name") or "")


def describe(e: dict) -> str:
    op = e.get("op")
    if op == "set_wire":
        ch = ", ".join(f"{k} = {e[k]}" for k in _WIRE_FIELDS if k in e)
        return f"wire {e.get('wire')}: {ch}"
    if op == "set_connector":
        ch = ", ".join(f"{k} = {e[k]}" for k in _CONN_FIELDS if k in e)
        return f"connector {e.get('connector')}: {ch}"
    if op == "rename_pin":
        return f"rename pin {e.get('connector')}.{e.get('pin')} to {e.get('to') or e.get('pin')}" + (f" ({e['label']})" if e.get("label") else "")
    if op == "add_pin":
        return f"add pin {e.get('pin')} to {e.get('connector')}"
    if op == "remove_pin":
        return f"remove pin {e.get('pin')} from {e.get('connector')}"
    if op == "add_wire":
        return f"add wire {e.get('a')} -> {e.get('b')} ({e.get('gauge')} {e.get('color')}, {e.get('length_mm')} mm)"
    if op == "remove_wire":
        return f"remove wire {e.get('wire')}"
    if op == "set_net":
        ch = ", ".join(f"{k} = {e[k]}" for k in ("current_A", "voltage_V", "max_drop_pct") if k in e)
        return f"net {e.get('net')}: {ch}"
    if op == "nc":
        return f"{e.get('pin')} {'not connected' if e.get('nc', True) else 'connected again'}"
    return str(e)


def merge_edit(prev: dict, new: dict) -> dict | None:
    """Fold an edit into the previous one when it adjusts the same thing (a run of inspector changes is one edit)."""
    if prev.get("op") != new.get("op") or prev.get("suppressed") or new.get("suppressed"):
        return None
    op = new.get("op")
    key = {"set_wire": "wire", "set_connector": "connector", "set_net": "net"}.get(op)
    if key and prev.get(key) == new.get(key):
        return {**prev, **{k: v for k, v in new.items() if k != "by"}}
    return None


def _pin(h: Harness, text: str) -> PinRef:
    return h._pin(text)


def _apply_one(h: Harness, e: dict) -> str:
    op = e["op"]
    if op == "set_wire":
        l = h.link(e["wire"])
        if "gauge" in e:
            l.gauge = Gauge.parse(e["gauge"])
        if "color" in e:
            l.color = normalize_color(e["color"])
        if "length_mm" in e:
            v = float(e["length_mm"])
            if not v > 0:
                raise HarnessError("a wire length must be above 0 mm")
            l.length_mm = v
        if "net" in e:
            l.net = h._net_name(e["net"]) if e["net"] else None
        if "current_A" in e:
            l.current_A = None if e["current_A"] in (None, "") else float(e["current_A"])
        if "label" in e:
            l.label = str(e["label"])
        return f"{l.id}: {l.gauge.label} {l.color} {l.length_mm:g} mm" + (f" net {l.net}" if l.net else "")
    if op == "set_connector":
        name = e["connector"]
        if name not in h.connectors:
            raise HarnessError(f"no connector {name!r}")
        c = h.connectors[name]
        if "type" in e:
            c.type = normalize_type(e["type"])
        for k in ("part_number", "gender", "notes"):
            if k in e:
                setattr(c, k, str(e[k]))
        if "rating_a" in e:
            c.rating_a = None if e["rating_a"] in (None, "") else float(e["rating_a"])
        if e.get("name") and e["name"] != name:
            h.rename_connector(name, str(e["name"]))
        return f"{c.name}: {c.type}"
    if op == "rename_pin":
        c = h.connectors[e["connector"]]
        new = str(e.get("to") or e["pin"])
        if new != str(e["pin"]):
            h.rename_pin(e["connector"], str(e["pin"]), new)
        if "label" in e:                                   # the pin's function label ("GND", "5V")
            if e["label"]:
                c.pin_labels[new] = str(e["label"])
            else:
                c.pin_labels.pop(new, None)
        return f"{e['connector']}.{e['pin']} is now {new}" + (f" ({e['label']})" if e.get("label") else "")
    if op == "add_pin":
        h.connectors[e["connector"]].add_pin(str(e["pin"]), str(e.get("label") or ""))
        return f"{e['connector']} has {h.connectors[e['connector']].pins} pins"
    if op == "remove_pin":
        h.remove_pin(e["connector"], str(e["pin"]))
        return f"{e['connector']} has {h.connectors[e['connector']].pins} pins"
    if op == "add_wire":
        link = h.connect(e["a"], e["b"], Wire(e.get("gauge", 18), e.get("color", "BK"), float(e.get("length_mm", 100))),
                         net=e.get("net") or None, current_A=e.get("current_A"), name=e.get("name"))
        return f"{link.id}: {link.a} -> {link.b}"
    if op == "remove_wire":
        h.remove_link(e["wire"])
        return f"removed {e['wire']}"
    if op == "set_net":
        n = h.nets.get(e["net"])
        if n is None:
            n = h.net(e["net"])
        for k in ("current_A", "voltage_V"):
            if k in e:
                setattr(n, k, None if e[k] in (None, "") else float(e[k]))
        if "max_drop_pct" in e:
            n.max_drop_pct = float(e["max_drop_pct"])
        return f"{n.name}: {n.current_A} A, {n.voltage_V} V, {n.max_drop_pct}% drop"
    if op == "nc":
        ref = _pin(h, e["pin"])
        c = h.connectors[ref.connector]
        if e.get("nc", True):
            h.nc(ref)
        else:
            c.nc = [x for x in c.nc if x != ref.pin]
        return f"{ref} {'NC' if e.get('nc', True) else 'in use'}"
    raise HarnessError(f"unknown harness edit {op!r}")


def apply_edits(h: Harness, edits: list[dict]) -> list:
    """Apply the edits in order; returns one EditNote per edit."""
    from ..edits import EditNote
    notes = []
    for e in edits:
        op = e.get("op")
        if op not in HARNESS_OPS:
            notes.append(EditNote(str(op), "", False, f"{op!r} is not a harness edit"))
            continue
        if e.get("suppressed"):
            notes.append(EditNote(op, target_of(e), True, "suppressed"))
            continue
        try:
            notes.append(EditNote(op, target_of(e), True, _apply_one(h, e)))
        except (HarnessError, KeyError, ValueError, TypeError) as exc:
            notes.append(EditNote(op, target_of(e), False, f"{type(exc).__name__}: {exc}"))
    return notes


def describe_state(h: Harness) -> dict:
    """{name: what it is}: wires and connectors as plain values, to compare two revisions of a harness."""
    out: dict = {}
    for l in h.links:
        out[l.id] = {"from": str(l.a), "to": str(l.b), "gauge": l.gauge.label, "color": l.color, "length_mm": l.length_mm, "net": l.net, "current_A": h.wire_current(l)}
    for c in h.connectors.values():
        out[c.name] = {"type": c.type, "pins": list(c.pin_names), "nc": list(c.nc)}
    for n in h.nets.values():
        out[f"net {n.name}"] = {"current_A": n.current_A, "voltage_V": n.voltage_V, "max_drop_pct": n.max_drop_pct}
    return out
