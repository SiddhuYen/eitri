"""Direct edits of a circuit from the browser editor, kept in the model file's EDITS list and applied after build(p):

    set_part     {"part": "R1", "ohm": 330}            a part's numbers (ohm, power_W, vf, if_max, if_rated, color, volts, current_A,
                                                        farad, v_rated, address, part_number); "rename": a new name
    set_net      {"net": "N3", "rename": "LED_A"}       rename a net; I2C settings: speed_hz, capacitance_pF, internal_pullups (on an SDA net)
    connect      {"pin": "R1.2", "net": "LED_A"}         move a pin onto a net (a new net when it does not exist)
    disconnect   {"pin": "R1.2"}                         take a pin off its net
    set_gpio     {"pin": "UNO.D2", "mode": "in", "pullup": true}
    add_part     {"kind": "resistor", "name": "R9", "ohm": 10000} (resistor, led, diode, capacitor, switch: their constructor's arguments)
    remove_part  {"part": "R9"}
"""
from __future__ import annotations

from .model import Circuit, CircuitError, PinRef, PinSpec

ELECTRONICS_OPS = frozenset({"set_part", "set_net", "connect", "disconnect", "set_gpio", "add_part", "remove_part"})
PART_FIELDS = {"resistor": ("ohm", "power_W"), "led": ("vf", "if_max", "if_rated", "color"), "capacitor": ("farad", "v_rated"),
               "supply": ("volts", "current_A"), "motor": ("volts", "current_A"), "solenoid": ("volts", "current_A"), "load": ("current_A",),
               "servo": ("current_A",), "module": ("address",)}
ADDABLE = ("resistor", "led", "diode", "capacitor", "switch")


def target_of(e: dict) -> str:
    return str(e.get("part") or e.get("net") or e.get("pin") or e.get("name") or "")


def describe(e: dict) -> str:
    op = e.get("op")
    if op == "set_part":
        return f"{e.get('part')}: " + ", ".join(f"{k} = {v}" for k, v in e.items() if k not in ("op", "part", "by"))
    if op == "set_net":
        return f"net {e.get('net')}: " + ", ".join(f"{k} = {v}" for k, v in e.items() if k not in ("op", "net", "by"))
    if op == "connect":
        return f"{e.get('pin')} -> net {e.get('net')}"
    if op == "disconnect":
        return f"disconnect {e.get('pin')}"
    if op == "set_gpio":
        return f"{e.get('pin')} mode {e.get('mode', 'io')}" + (" with pull-up" if e.get("pullup") else "")
    if op == "add_part":
        return f"add {e.get('kind')} {e.get('name')}"
    if op == "remove_part":
        return f"remove {e.get('part')}"
    return str(e)


def merge_edit(prev: dict, new: dict) -> dict | None:
    if prev.get("op") != new.get("op") or prev.get("suppressed") or new.get("suppressed"):
        return None
    if new["op"] == "set_part" and prev.get("part") == new.get("part") and "rename" not in prev:
        return {**prev, **{k: v for k, v in new.items() if k != "by"}}
    if new["op"] == "set_net" and prev.get("net") == new.get("net") and "rename" not in prev:
        return {**prev, **{k: v for k, v in new.items() if k != "by"}}
    if new["op"] in ("connect", "set_gpio") and prev.get("pin") == new.get("pin"):
        return {**prev, **{k: v for k, v in new.items() if k != "by"}}
    return None


def _set_part(c: Circuit, e: dict) -> str:
    p = c.part(str(e["part"]))
    allowed = PART_FIELDS.get(p.kind, ()) + ("part_number",)
    done = []
    for k, v in e.items():
        if k in ("op", "part", "by", "rename", "suppressed"):
            continue
        if k not in allowed:
            raise CircuitError(f"{p.name} ({p.kind}): {k!r} is not editable; editable: {', '.join(allowed)}")
        if k == "part_number":
            p.part_number = str(v)
        elif k == "color":
            from .catalog import led_spec
            spec, srcs = led_spec(v)
            p.spec.update(spec)
            p.srcs.update(srcs)
            p.spec["generic"] = True
            p.spec.pop("catalog_part", None)
            p.catalog = ""
        elif k == "address":
            a = int(v, 0) if isinstance(v, str) else int(v)
            if p.spec.get("addresses") and a not in p.spec["addresses"]:
                raise CircuitError(f"{p.name} can be at {', '.join(hex(x) for x in p.spec['addresses'][:8])}..., not {hex(a)}")
            p.spec["address"] = a
            p.srcs["address"] = "model"
        else:
            x = float(v)
            if not x > 0:
                raise CircuitError(f"{p.name}: {k} must be more than 0")
            p.spec[k] = x
            p.srcs[k] = "model"
            if p.kind == "led" and k in ("vf", "if_max"):
                p.spec["generic"] = not p.spec.get("catalog_part") and not ("model" == p.srcs.get("vf") == p.srcs.get("if_max"))
            _sync_pins(p, k, x)
        done.append(f"{k} {v}")
    if e.get("rename"):
        new = str(e["rename"])
        rename_part(c, p.name, new)
        done.append(f"renamed {new}")
    return f"{p.name}: " + ", ".join(done)


def _sync_pins(p, k: str, x: float) -> None:
    """Numbers a pin also carries (a supply's volts, a load's current)."""
    if p.kind == "supply" and k == "volts":
        p.pins["+"] = PinSpec("+", "power_out", volts=x, src="model")
    if k == "current_A":
        for n, ps in list(p.pins.items()):
            if ps.kind == "power_in":
                p.pins[n] = PinSpec(ps.name, ps.kind, current_A=x, v_range=ps.v_range, required=ps.required, src="model")


def rename_part(c: Circuit, old: str, new: str) -> None:
    from .model import Part
    if new in c.parts:
        raise CircuitError(f"a part named {new!r} already exists")
    Part(new, "load")                                         # validates the name
    p = c.parts.pop(old)
    p.name = new
    c.parts[new] = p
    for net in c.nets.values():
        net.pins = [PinRef(new, r.pin) if r.part == old else r for r in net.pins]
    c._net_of = {(PinRef(new, r.pin) if r.part == old else r): n for r, n in c._net_of.items()}
    if old in c.layout:
        c.layout[new] = c.layout.pop(old)


def _apply_one(c: Circuit, e: dict) -> str:
    op = e["op"]
    if op == "set_part":
        return _set_part(c, e)
    if op == "set_net":
        name = str(e["net"])
        if name not in c.nets:
            raise CircuitError(f"no net {name!r}")
        if "speed_hz" in e or "capacitance_pF" in e or "internal_pullups" in e:
            bus = c.buses.get(name) or c.i2c(name)
            if "speed_hz" in e:
                bus.speed_hz = int(float(e["speed_hz"]))
            if "capacitance_pF" in e:
                bus.capacitance_pF = None if e["capacitance_pF"] in (None, "") else float(e["capacitance_pF"])
            if "internal_pullups" in e:
                v = e["internal_pullups"]
                bus.internal_pullups = v.strip().lower() not in ("false", "off", "0", "no") if isinstance(v, str) else bool(v)
        if e.get("rename"):
            c.rename_net(name, str(e["rename"]))
            name = str(e["rename"])
        return f"net {name}"
    if op == "connect":
        ref = c._pin(str(e["pin"]))
        c.disconnect(ref)
        c.connect(ref, net=str(e["net"]))
        return f"{ref} on {e['net']}"
    if op == "disconnect":
        c.disconnect(str(e["pin"]))
        return f"{e['pin']} open"
    if op == "set_gpio":
        ref = c.gpio(str(e["pin"]), str(e.get("mode", "io")), bool(e.get("pullup", False)))
        return f"{ref} {e.get('mode', 'io')}"
    if op == "add_part":
        kind = str(e.get("kind"))
        if kind not in ADDABLE:
            raise CircuitError(f"the editor adds {', '.join(ADDABLE)}; anything else goes in build()")
        args = {k: v for k, v in e.items() if k not in ("op", "kind", "name", "by", "suppressed")}
        getattr(c, kind)(str(e["name"]), **args)
        return f"added {kind} {e['name']}"
    if op == "remove_part":
        c.remove_part(str(e["part"]))
        return f"removed {e['part']}"
    raise CircuitError(f"unknown electronics edit {op!r}")


def apply_edits(c: Circuit, edits: list[dict]) -> list:
    from ..edits import EditNote
    notes = []
    for e in edits:
        op = e.get("op")
        if op not in ELECTRONICS_OPS:
            notes.append(EditNote(str(op), "", False, f"{op!r} is not an electronics edit"))
            continue
        if e.get("suppressed"):
            notes.append(EditNote(op, target_of(e), True, "suppressed"))
            continue
        try:
            notes.append(EditNote(op, target_of(e), True, _apply_one(c, e)))
        except (CircuitError, KeyError, ValueError, TypeError) as exc:
            notes.append(EditNote(op, target_of(e), False, f"{type(exc).__name__}: {exc}"))
    return notes


def describe_state(c: Circuit) -> dict:
    out: dict = {}
    for p in c.parts.values():
        out[f"part {p.name}"] = {"kind": p.kind, "value": p.value_text, **{k: v for k, v in p.spec.items() if isinstance(v, (int, float, str)) and not isinstance(v, bool)}}
    for n in c.nets.values():
        out[f"net {n.name}"] = {"pins": " ".join(sorted(str(r) for r in n.pins))}
    return out
