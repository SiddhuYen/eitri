"""The electronics checks: what every build is graded on. Each number a check compares against has a source
(eitri.electronics.sources), named in the check's text; a value the model file gave is its own source.

    connectivity  required pins connected, no dangling nets, every current solved (series strings in closed form, branched
                  networks by nodal analysis)
    power         no shorted rails, every supply pin within its part's range, the rail budget of every source (a board's 5V / 3V3
                  pins, a supply, a regulator), the regulators' headroom and junction temperature, a board's VIN range (at a battery's
                  full charge too: an unregulated source straight on VIN or on the 5V pin), one common ground for everything that
                  exchanges signals
    current       per GPIO pin and per microcontroller against its datasheet limits, LED current, resistor power, nothing
                  without a current limit
    logic         driver high level against receiver VIH, no input above its absolute maximum, no floating input
    i2c           pull-ups present, within UM10204's Rp(min) / Rp(max), no two devices at one address; an AVR board's Wire
                  internal pull-ups (to 5 V) against the devices' absolute maximum
    inductive     a flyback diode, the right way round and rated for the coil current, on every coil a transistor or pin switches
    switching     MOSFET gate drive against the VGS its RDS(on) is specified at, its dissipation, a gate pull-down when a board pin
                  drives it (the pin floats through reset); a BJT's saturation and IC rating
    parts         standard (E24) resistor values, LED data from the part's own datasheet, capacitor voltage ratings

Every check is a Check (eitri.checks): pass, warn or fail, with `refs` naming the parts and nets it is about. A bad design is a
failed check, never an exception.
"""
from __future__ import annotations

from ..checks import Check, check, switched_off
from .. import power
from .analysis import INF, Analysis, analyze, net_high, open_drain, pullups_on, pulldowns_on, resistors_to_fixed
from .catalog import E24, nearest_e
from .model import Circuit, PinRef, amps_text, ohms
from .sources import cite

AVR_VMAX = 5.5                       # ATmega328P: any pin VCC + 0.5 V at 5 V (DS40002061B s29.1): what a 5 V-tolerant device must take

WARN_FRACTION = 0.8                  # within 20 % of a limit: a warning (as the harness grades)
I2C_MODES = ((100_000, "Standard-mode", 1000e-9, 400e-12, 0.003), (400_000, "Fast-mode", 300e-9, 400e-12, 0.003),
             (1_000_000, "Fast-mode Plus", 120e-9, 550e-12, 0.020))       # fSCL max, name, tr max, Cb max, IOL (UM10204 Tables 10, 11)
I2C_VOL = 0.4                        # UM10204 Table 10: VOL1 max at the IOL above, VDD > 2 V
I2C_CI = 10e-12                      # UM10204 Table 10: Ci, capacitance for each I/O pin, max 10 pF
I2C_WIRING = 50e-12                  # ASSUMPTION: breadboard / jumper wiring per line; no source (give capacitance_pF to override)


def _c(category, name, severity, measured, expected="", refs=()) -> Check:
    return check(category, name, severity, measured, expected, refs)


def _lvl(value: float, limit: float) -> str:
    return "fail" if value > limit + 1e-12 else "warn" if value >= WARN_FRACTION * limit else "pass"


def _src(part, key: str) -> str:
    s = part.srcs.get(key, "")
    return f" [source: {cite(s)}]" if s else ""


def _a(x: float) -> str:
    return "unlimited" if x == INF else amps_text(x)


def run_checks(c: Circuit) -> tuple[list[Check], Analysis]:
    an = analyze(c)
    checks: list[Check] = []
    for fn in (_connectivity, _shorts, _supply_ranges, _unregulated_inputs, _budgets, _regulators, _ground, _unlimited, _gpio_current, _leds,
               _resistors, _logic, _floating, _i2c, _avr_wire_pullups, _inductive, _contacts, _mosfets, _gate_pulldowns, _bjts, _parts):
        checks += fn(c, an)
    return checks, an


# --------------------------------------------------------------------------- connectivity


def _connectivity(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    open_req = [r for r in c.unconnected() if c.pin_spec(r).required]
    if open_req:
        out.append(_c("connectivity", "required pins connected", "fail",
                      f"{len(open_req)} open: " + ", ".join(map(str, open_req[:12])) + (" ..." if len(open_req) > 12 else ""),
                      "every supply, ground and passive pin on a net", [r.part for r in open_req]))
    else:
        out.append(_c("connectivity", "required pins connected", "pass", f"{len(c.parts)} parts, every required pin on a net"))
    lonely = [n for n in c.nets.values() if len(n.pins) < 2]
    if lonely:
        out.append(_c("connectivity", "no dangling nets", "warn", "net(s) with one pin: " + ", ".join(f"{n.name} ({n.pins[0]})" for n in lonely if n.pins),
                      "a net joins two or more pins", [n.name for n in lonely] + [n.pins[0].part for n in lonely if n.pins]))
    if an.branches:
        out.append(_c("connectivity", "currents solved on every net", "warn",
                      "; ".join(f"{n} joins {', '.join(ps)}" for n, ps in an.branches.items())
                      + ": nodal analysis found no consistent solution for this network (or it has too many cases); the currents through it "
                      "are not computed (pin, LED and resistor checks skip them)",
                      "every current solved", list(an.branches) + [p for ps in an.branches.values() for p in ps]))
    elif an.networks:
        nodes = {n: ps for net in an.networks for n, ps in net["nodes"].items()}
        out.append(_c("connectivity", "currents solved on every net", "pass",
                      "series strings in closed form; branch node(s) " + "; ".join(f"{n} ({', '.join(ps)})" for n, ps in nodes.items())
                      + " solved by nodal analysis (MNA, ideal-diode iteration) for every input state",
                      "every current solved", list(nodes) + sorted({p for ps in nodes.values() for p in ps})))
    return out


# --------------------------------------------------------------------------- power


def _shorts(c: Circuit, an: Analysis) -> list[Check]:
    out, bad = [], []
    for n, info in an.nets.items():
        if info.kind != "short":
            continue
        gnd = [r for r in c.nets[n].pins if c.pin_spec(r).kind == "gnd"]
        srcs = ", ".join(f"{s} ({c.pin_spec(s).volts:g} V)" for s in info.sources)
        why = f"{srcs} on the same net as ground ({', '.join(map(str, gnd[:3]))})" if gnd else f"{srcs}: two supplies drive one net"
        bad.append((n, why, [s.part for s in info.sources] + [g.part for g in gnd]))
    for n, info in an.nets.items():
        if info.kind == "rail" and len({s.part for s in info.sources}) > 1:
            bad.append((n, f"{', '.join(map(str, info.sources))} all drive it: supplies in parallel back-feed each other "
                           "(one source per rail; join only the grounds)", [s.part for s in info.sources]))
    if bad:
        out.append(_c("power", "no shorted or back-fed rails", "fail", "; ".join(f"{n}: {w}" for n, w, _ in bad), "one source per rail, never tied to ground",
                      [n for n, _, _ in bad] + [p for _, _, ps in bad for p in ps]))
    else:
        rails = [n for n, i in an.nets.items() if i.kind == "rail"]
        out.append(_c("power", "no shorted or back-fed rails", "pass", f"{len(rails)} rail(s), one source each"))
    return out


def _supply_ranges(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for p in c.parts.values():
        for pn, ps in p.pins.items():
            if ps.kind != "power_in":
                continue
            net = c._net_of.get(PinRef(p.name, pn))
            name = f"supply voltage {p.name}.{pn}"
            if net is None:
                if ps.required:
                    out.append(_c("power", name, "fail", f"{p.name}.{pn} is not connected: {p.name} is unpowered", "a rail", [p.name]))
                continue
            info = an.nets[net]
            if info.kind not in ("rail", "short"):
                out.append(_c("power", name, "fail", f"{p.name}.{pn} is on {net}, which no supply drives: {p.name} is unpowered",
                              "a rail from a supply, a board's 5V / 3V3 pin or a regulator", [p.name, net]))
                continue
            v = info.volts
            if ps.v_range is None:
                if p.kind == "load":                          # the model's own v_min / v_max feed this check: unset, it does not run
                    out.append(switched_off(_c("power", name, "warn", f"{v:g} V on {net}; {p.name} declares no supply range, so it is not graded",
                                               "v_min= and v_max= from the load's datasheet", [p.name, net]), f"{p.name}.v_min", f"{p.name}.v_max"))
                continue
            lo, hi = ps.v_range
            src = f" [source: {cite(ps.src)}]" if ps.src else ""
            if v < lo - 1e-9 or v > hi + 1e-9:
                out.append(_c("power", name, "fail", f"{v:g} V on {net}; {p.catalog or p.kind} takes {lo:g}-{hi:g} V{src}",
                              f"{lo:g}-{hi:g} V", [p.name, net]))
            else:
                out.append(_c("power", name, "pass", f"{v:g} V on {net}, within {lo:g}-{hi:g} V{src}", f"{lo:g}-{hi:g} V", [p.name, net]))
    for b in c.parts.values():
        if b.kind != "board" or b.spec.get("powered") != "vin" or "VIN" not in b.pins:
            continue
        net = c._net_of.get(PinRef(b.name, "VIN"))
        lo, hi = b.spec.get("vin_range", (None, None))
        v = an.nets[net].volts if net and an.nets[net].kind == "rail" else b.spec.get("vin_V")
        name = f"VIN {b.name} (powered on VIN)"
        if v is None:
            out.append(_c("power", name, "fail", f"{b.name} is powered on VIN, but VIN is on no rail and no vin_V was given", "a supply on VIN", [b.name]))
        elif lo is not None and not lo <= v <= hi:
            out.append(_c("power", name, "fail", f"{v:g} V on VIN; {b.title} recommends {lo:g}-{hi:g} V{_src(b, 'vin_range')}", f"{lo:g}-{hi:g} V", [b.name]))
        elif lo is not None:
            out.append(_c("power", name, "pass", f"{v:g} V, within {lo:g}-{hi:g} V{_src(b, 'vin_range')}", f"{lo:g}-{hi:g} V", [b.name]))
    return out


BATTERY_WORDS = ("batt", "lipo", "li-ion", "liion", "li-po", "18650", "21700", "lead-acid", "lead acid", " sla", "vrla", "nimh", "alkaline",
                 "accumulator", " car ", "vehicle")      # a supply named or noted like a battery, for the "model its full charge" warning


def _looks_like_battery(p) -> bool:
    text = f" {p.name} {p.part_number} {p.notes} ".lower()
    return any(w in text for w in BATTERY_WORDS)


def _supplies_on(c: Circuit, an: Analysis, net: str | None) -> list:
    """The supply parts whose "+" is on this net (a source straight on it, with no regulator between)."""
    if not net:
        return []
    return [c.parts[r.part] for r in c.nets[net].pins if c.parts[r.part].kind == "supply" and r.pin == "+"]


def _unregulated_inputs(c: Circuit, an: Analysis) -> list[Check]:
    """A battery or other unregulated source wired straight to a board: its VIN graded at the source's highest voltage (a
    battery's full charge, a vehicle's charging system), not only its nominal one; a battery on the 5V pin, which bypasses the
    board's regulator, against the most the 5 V rail may see."""
    out = []
    for b in c.parts.values():
        if b.kind != "board":
            continue
        if b.spec.get("powered") == "vin" and b.spec.get("vin_range"):
            lo, hi = b.spec["vin_range"]
            for sp in _supplies_on(c, an, c._net_of.get(PinRef(b.name, "VIN"))):
                name = f"VIN {b.name} at {sp.name}'s highest voltage"
                vmax = sp.spec.get("v_max")
                if vmax is not None:
                    src = _src(sp, "v_max")
                    if vmax > hi + 1e-9:
                        out.append(_c("power", name, "fail",
                                      f"{sp.name} reaches {vmax:g} V ({'a battery at full charge' if sp.spec.get('battery') else 'its highest voltage'}{src}), "
                                      f"straight on VIN; {b.title} recommends {lo:g}-{hi:g} V{_src(b, 'vin_range')}. Fix: a regulator or buck "
                                      f"converter between {sp.name} and VIN (its output within {lo:g}-{hi:g} V), or a lower-voltage source",
                                      f"<= {hi:g} V at the source's highest", [b.name, sp.name]))
                    else:
                        out.append(_c("power", name, "pass", f"{sp.name} reaches {vmax:g} V{src}, within {lo:g}-{hi:g} V{_src(b, 'vin_range')}",
                                      f"<= {hi:g} V", [b.name, sp.name]))
                elif sp.spec.get("battery") or _looks_like_battery(sp):
                    out.append(switched_off(_c("power", name, "warn",
                                  f"{sp.name} looks like a battery straight on VIN, modelled at {sp.spec['volts']:g} V only; a battery runs above its "
                                  f"nominal voltage at full charge or on charge (a 12 V lead-acid reaches 14.5 V [source: {cite('yuasa-np7-12')}]), "
                                  f"over {b.title}'s {lo:g}-{hi:g} V{_src(b, 'vin_range')} if it is near the top. Fix: c.battery(name, chemistry, "
                                  "cells), or v_max= on the supply, so its full-charge voltage is graded",
                                  "the source's highest voltage", [b.name, sp.name]), f"{sp.name}.v_max"))
        v5 = b.spec.get("v5_max")
        if v5 is not None and "5V" in b.pins:
            for sp in _supplies_on(c, an, c._net_of.get(PinRef(b.name, "5V"))):
                if not (sp.spec.get("battery") or sp.spec.get("v_max") is not None or _looks_like_battery(sp)):
                    continue
                vmax = sp.spec.get("v_max") or sp.spec["volts"]
                sev = "fail" if vmax > v5 + 1e-9 else "warn"
                out.append(_c("power", f"5V pin {b.name} fed by {sp.name}", sev,
                              f"{sp.name} ({vmax:g} V at its highest) is wired to {b.name}'s 5V pin, which bypasses the board's regulator; the 5 V "
                              f"rail takes at most {v5:g} V{_src(b, 'v5_max')}, and a battery's voltage falls as it discharges. Fix: feed "
                              f"VIN (c.board(..., powered=\"vin\")) or put a 5 V regulator between {sp.name} and the 5V pin",
                              f"a regulated 5 V, at most {v5:g} V", [b.name, sp.name]))
    return out


def rail_loads(c: Circuit, an: Analysis, net: str, _seen=None) -> list[tuple[str, float, list[str]]]:
    """What a rail supplies: (what, amps, refs) for every consumer on it, worst case."""
    seen = _seen or set()
    if net in seen:
        return []
    seen = seen | {net}
    items: list[tuple[str, float, list[str]]] = []
    for ref in c.nets[net].pins:
        p, ps = c.parts[ref.part], c.pin_spec(ref)
        if ps.kind == "power_in" and ps.current_A:
            items.append((f"{p.name} ({p.catalog or p.kind}) {amps_text(ps.current_A)}", float(ps.current_A), [p.name]))
        if p.kind == "regulator" and ref.pin == "IN":
            outn = c._net_of.get(PinRef(p.name, "OUT"))
            sub = sum(i for _, i, _ in rail_loads(c, an, outn, seen)) if outn else 0.0
            iq = float(p.spec.get("iq", 0.0))
            items.append((f"{p.name} (regulator: its output {amps_text(sub)} + {amps_text(iq)} quiescent)", sub + iq, [p.name]))
        if p.kind == "board" and ref.pin == "VIN" and p.spec.get("powered") == "vin":
            sub = board_draw(c, an, p.name)
            items.append((f"{p.name} through VIN (what its 5 V rail supplies, {amps_text(sub)})", sub, [p.name]))
    for s in an.strings:
        if s.flow[0] == net and s.current not in (0.0, INF):
            items.append((f"{', '.join(s.parts)} {amps_text(s.current)}", s.current, s.parts))
    return items


def board_draw(c: Circuit, an: Analysis, board: str) -> float:
    """What a board's own 5 V (or 3.3 V) supply carries for the circuit: its supply pins' loads and its I/O pins' source current."""
    b = c.parts[board]
    total = 0.0
    for pin in ("5V", "3V3"):
        n = c._net_of.get(PinRef(board, pin)) if pin in b.pins else None
        if n and an.nets[n].kind == "rail":
            total += sum(i for _, i, _ in rail_loads(c, an, n))
    total += sum(io_source(c, an, board).values())
    return total


def io_source(c: Circuit, an: Analysis, board: str) -> dict[str, float]:
    """Current each GPIO of a board sources (drives high into a string), worst case."""
    out: dict[str, float] = {}
    for n, info in an.nets.items():
        for d in info.drivers:
            if d.part != board:
                continue
            for s in an.at_net(n):
                if s.flow[0] == n and s.current not in (0.0, INF):
                    out[d.pin] = out.get(d.pin, 0.0) + s.current
    return out


def _budgets(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for n, info in an.nets.items():
        if info.kind != "rail" or len({s.part for s in info.sources}) != 1:
            continue
        src = info.sources[0]
        p = c.parts[src.part]
        items = rail_loads(c, an, n)
        draw = sum(i for _, i, _ in items)
        extra = ""
        if p.kind == "board":
            if src.pin in ("5V",) and p.spec.get("logic_rail") == "5V" or src.pin == p.spec.get("logic_rail"):
                io = sum(io_source(c, an, p.name).values())
                if io:
                    draw += io
                    extra = f" + {amps_text(io)} sourced by its I/O pins"
            if src.pin == "5V" and "3V3" in p.pins and p.spec.get("v3_from_5v"):
                n3 = c._net_of.get(PinRef(p.name, "3V3"))
                if n3 and an.nets[n3].kind == "rail":
                    i3 = sum(i for _, i, _ in rail_loads(c, an, n3))
                    if i3:
                        draw += i3
                        extra += f" + {amps_text(i3)} on its 3V3 pin (its 3.3 V regulator runs from the 5 V rail){_src(p, 'v3_from_5v')}"
        lim, basis, source = rail_limit(c, an, p, src.pin, n)
        big = [rs for _, i, rs in items if lim is None or i >= 0.05 * lim]      # what matters to the budget (not a 0.5 mA pull-up)
        refs = [n, p.name] + [r for rs in big for r in rs]
        what = "; ".join(t for t, _, _ in items[:8]) + (" ..." if len(items) > 8 else "")
        name = f"rail budget {n} ({src})"
        if not items and not extra:
            continue
        if lim is None:
            chk = _c("power", name, "warn", f"{amps_text(draw)} drawn ({what}{extra}); not graded: {basis}" + (f" [source: {source}]" if source else ""),
                     "the source's rating", refs)
            out.append(switched_off(chk, f"{p.name}.current_A") if p.kind == "supply" else chk)
            continue
        sev = _lvl(draw, lim)
        fix = (f". Fix: {power.advice(src.pin)}" if p.kind == "board" else ". Fix: a supply rated for the load, or move loads to another rail") if sev == "fail" else ""
        out.append(_c("power", name, sev, f"{amps_text(draw)} of {amps_text(lim)} ({draw / lim * 100:.0f}%), limited by {basis}: {what}{extra}{fix}"
                      + (f" [source: {source}]" if source else ""), f"<= {amps_text(lim)}" + (" (within 20% of the limit)" if sev == "warn" else ""), refs))
    return out


def rail_limit(c: Circuit, an: Analysis, p, pin: str, net: str) -> tuple[float | None, str, str]:
    """What a source pin may deliver: (amps or None, what limits it, the source text)."""
    if p.kind == "supply":
        lim = p.spec.get("current_A")
        return (lim, "the supply's rating (from the model file)", "the model file") if lim else (None, "the supply has no current_A rating", "")
    if p.kind == "regulator":
        return float(p.spec["i_max"]), f"{p.catalog}'s rated output", cite(p.srcs.get("i_max", ""))
    if p.kind == "board":
        key = p.spec.get("key")
        if key in power.BOARDS:
            vin = None
            if p.spec.get("powered") == "vin":
                vn = c._net_of.get(PinRef(p.name, "VIN"))
                vin = an.nets[vn].volts if vn and an.nets[vn].kind == "rail" else p.spec.get("vin_V")
            lim = power.supply_limit(key, pin, p.spec.get("powered", "usb"), vin, c.ambient_C)
            return lim.limit_A, lim.basis, lim.source
        rl = (p.spec.get("rail_limits") or {}).get(pin)
        if rl:
            return rl[0], rl[1], cite(rl[2])
        return None, f"no published limit for {p.title}'s {pin} pin", ""
    return None, "unknown source", ""


def _regulators(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for p in c.parts.values():
        if p.kind != "regulator":
            continue
        s = p.spec
        nin, nout = c._net_of.get(PinRef(p.name, "IN")), c._net_of.get(PinRef(p.name, "OUT"))
        if not nin or an.nets[nin].kind != "rail":
            continue                                  # an unpowered regulator: the connectivity / supply checks say so
        vin = an.nets[nin].volts
        iout = sum(i for _, i, _ in rail_loads(c, an, nout)) if nout else 0.0
        head = vin - s["vout"]
        name = f"regulator headroom {p.name} ({p.catalog})"
        if vin > s["vin_max"]:
            out.append(_c("power", name, "fail", f"{vin:g} V in; {p.catalog} takes at most {s['vin_max']:g} V{_src(p, 'vin_max')}", f"<= {s['vin_max']:g} V", [p.name, nin]))
        elif head < s["dropout"]:
            out.append(_c("power", name, "fail", f"{vin:g} V in for {s['vout']:g} V out leaves {head:.2f} V; it needs {s['dropout']:g} V (dropout, max){_src(p, 'dropout')}. "
                          "Fix: a higher input rail, or a low-dropout part", f">= {s['dropout']:g} V", [p.name, nin]))
        else:
            out.append(_c("power", name, "pass", f"{head:.2f} V above its output (dropout {s['dropout']:g} V max){_src(p, 'dropout')}", f">= {s['dropout']:g} V", [p.name]))
        pd = head * iout + vin * float(s.get("iq", 0.0))
        tj = c.ambient_C + pd * s["theta_ja"]
        budget = s["tj_max"] - c.ambient_C
        sev = _lvl(tj - c.ambient_C, budget)
        out.append(_c("power", f"regulator temperature {p.name} ({p.catalog})", sev,
                      f"{pd:.2f} W ({head:.2f} V x {amps_text(iout)}): junction {tj:.0f} C at {c.ambient_C:g} C ambient, {s['theta_ja']:g} C/W "
                      f"junction to ambient; limit {s['tj_max']:g} C{_src(p, 'theta_ja')}"
                      + (". Fix: less current, a lower input voltage, a heatsink (a lower theta), or a switching regulator" if sev == "fail" else ""),
                      f"<= {s['tj_max']:g} C" + (" (within 20% of the limit)" if sev == "warn" else ""), [p.name]))
    return out


def _ground(c: Circuit, an: Analysis) -> list[Check]:
    """Everything that exchanges a signal shares one ground."""
    gnd_of: dict[str, set[str]] = {}
    for p in c.parts.values():
        for pn, ps in p.pins.items():
            if ps.kind == "gnd":
                n = c._net_of.get(PinRef(p.name, pn))
                if n:
                    gnd_of.setdefault(p.name, set()).add(n)
    # a part whose own gnd is open but whose supply comes from a part that has one: borrow nothing; it stays without a ground
    pairs, bad = 0, []
    for n, info in an.nets.items():
        if info.kind in ("rail", "gnd", "short"):
            continue
        logic = [r for r in c.nets[n].pins if c.pin_spec(r).kind in ("io", "in", "out", "od")]
        parts = sorted({r.part for r in logic})
        for i, a in enumerate(parts):
            for b in parts[i + 1:]:
                pairs += 1
                ga, gb = gnd_of.get(a), gnd_of.get(b)
                if ga and gb and ga & gb:
                    continue
                bad.append((n, a, b, "has no ground connected" if not ga or not gb else "is on another ground net"))
    if not pairs:
        return []
    if bad:
        return [_c("power", "common ground for every signal", "fail",
                   "; ".join(f"{a} and {b} share {n} but {b if gnd_of.get(a) else a} {why}" for n, a, b, why in bad[:6])
                   + ". A signal is a voltage against ground: join the grounds (a separate supply's minus to the board's GND)",
                   "one ground net joins every part that exchanges signals", [x for n, a, b, _ in bad for x in (n, a, b)])]
    return [_c("power", "common ground for every signal", "pass", f"{pairs} signal connection(s), all on a shared ground")]


# --------------------------------------------------------------------------- current


def _unlimited(c: Circuit, an: Analysis) -> list[Check]:
    bad = [s for s in an.strings if s.current == INF]
    out = []
    for s in bad:
        ends = f"{s.flow[0]} -> {' -> '.join(s.parts)} -> {s.flow[1]}"
        hint = ""
        drv = [d for d in an.nets[s.flow[0]].drivers + an.nets[s.flow[1]].drivers if c.parts[d.part].kind == "board"]
        if drv and all(c.parts[x].kind == "switch" for x in s.parts):
            hint = f" when {drv[0]} drives the other level: if it only reads, declare it c.gpio({drv[0].part}['{drv[0].pin}'], 'in')"
        elif any(c.parts[x].kind == "led" for x in s.parts):
            hint = ": an LED needs a series resistor"
        out.append(_c("current", f"current limited: {', '.join(s.parts)}", "fail",
                      f"nothing limits the current along {ends} ({s.va:g} V to {s.vb:g} V, no resistance){hint}",
                      "a resistance in every string between two fixed voltages", s.parts + [s.a, s.b]))
    return out


def _gpio_current(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for b in c.parts.values():
        if b.kind not in ("board", "module"):
            continue
        per_pin: dict[str, tuple[float, list]] = {}
        for n, info in an.nets.items():
            for d in info.drivers:
                if d.part != b.name:
                    continue
                for s in an.at_net(n):
                    if s.current in (0.0, INF):
                        continue
                    cur, refs = per_pin.get(d.pin, (0.0, []))
                    per_pin[d.pin] = (cur + s.current, refs + s.parts + [n])
        if not per_pin:
            continue
        if b.kind == "board":
            lim, lsrc = b.spec.get("pin_limit_A"), b.srcs.get("pin_limit_A", "")
        for pin, (cur, refs) in sorted(per_pin.items()):
            if b.kind == "module":
                lim, lsrc = b.pins[pin].current_A, b.pins[pin].src
                if not lim:
                    continue
            if lim is None:
                out.append(_c("current", f"pin current {b.name}.{pin}", "warn", f"{amps_text(cur)}; no per-pin limit recorded for {b.title}", "", [b.name] + refs))
                continue
            sev = _lvl(cur, lim)
            out.append(_c("current", f"pin current {b.name}.{pin}", sev,
                          f"{amps_text(cur)} of {amps_text(lim)} ({cur / lim * 100:.0f}%) through {', '.join(dict.fromkeys(x for x in refs if x in c.parts))}"
                          + (". Fix: a larger series resistor, or a transistor to switch the load" if sev == "fail" else "") + f" [source: {cite(lsrc)}]",
                          f"<= {amps_text(lim)}" + (" (within 20% of the limit)" if sev == "warn" else ""), [b.name] + refs))
        tot_lim = b.spec.get("total_limit_A") if b.kind == "board" else None
        if tot_lim:
            total = sum(v for v, _ in per_pin.values())
            sev = _lvl(total, tot_lim)
            out.append(_c("current", f"total I/O current {b.name}", sev,
                          f"{amps_text(total)} through {len(per_pin)} pin(s) at once (worst case) of {amps_text(tot_lim)} [source: {cite(b.srcs.get('total_limit_A', ''))}]",
                          f"<= {amps_text(tot_lim)}", [b.name]))
    return out


def _leds(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for p in c.parts.values():
        if p.kind != "led":
            continue
        ss = an.through(p.name)
        if not ss:
            continue
        i = max(s.current for s in ss)
        if i == INF:
            continue                                   # the unlimited-current check says it
        name = f"LED current {p.name} ({p.spec.get('color', '')})"
        imax = float(p.spec["if_max"])
        net = next((an.networks[s.network] for s in ss if s.network is not None), None)
        others = [x for x in (net or {}).get("leds", []) if x != p.name]
        typ = max((s.typical if s.typical is not None else s.current) for s in ss)
        if i > 0 and typ <= 0 and others:
            node = next((n for s in ss for n in (s.a, s.b) if n in net["nodes"]), "the shared node")
            low = ", ".join(f"{x} ({c.parts[x].spec['vf']:g} V)" for x in others)
            out.append(_c("current", name, "warn",
                          f"current hogging: at typical Vf {p.name} ({p.spec['vf']:g} V) takes none of the current it shares at {node}: {low} hold(s) "
                          f"{node} below its forward voltage, so it stays dark (with Vf spread it could instead take up to {amps_text(i)}). "
                          "Fix: one series resistor per LED (or LEDs of one part number)",
                          "every LED lights", [p.name] + others + [node]))
            continue
        if i <= 0:
            s = ss[0]
            out.append(_c("current", name, "warn", f"no current: along {s.describe()} the LED is reversed or the supply is below its {p.spec['vf']:g} V forward voltage, so it never lights",
                          "the anode toward the higher voltage", [p.name] + s.parts))
            continue
        sev = _lvl(i, imax)
        rated = p.spec.get("if_rated")
        hog = ""
        if others:
            hog = (f"; it shares its current with {', '.join(others)}: graded on the worst case, its Vf at typical and theirs at maximum, "
                   f"where it takes the whole shared current (at typical Vf: {amps_text(typ)})")
        out.append(_c("current", name, sev, f"{amps_text(i)} of {amps_text(imax)} DC max ({i / imax * 100:.0f}%)" + (f", rated {amps_text(rated)}" if rated else "")
                      + f", Vf {p.spec['vf']:g} V" + hog + (". Fix: a larger series resistor" + (", or one resistor per LED" if others else "") if sev == "fail" else "")
                      + _src(p, "if_max"),
                      f"<= {amps_text(imax)}", [p.name] + max(ss, key=lambda s: s.current).parts))
    return out


def _resistors(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for p in c.parts.values():
        if p.kind != "resistor":
            continue
        ss = an.through(p.name)
        i = max((s.current for s in ss), default=0.0)
        if not ss or i in (0.0, INF):
            continue
        pw = i * i * float(p.spec["ohm"])
        rating = float(p.spec["power_W"])
        sev = _lvl(pw, rating)
        out.append(_c("current", f"resistor power {p.name} ({ohms(p.spec['ohm'])})", sev,
                      f"{pw * 1000:.3g} mW at {amps_text(i)} of {rating * 1000:g} mW rated ({pw / rating * 100:.0f}%)"
                      + (". Fix: a higher-wattage resistor or less current" if sev == "fail" else "") + _src(p, "power_W"),
                      f"<= {rating * 1000:g} mW" + (" (within 20% of the rating)" if sev == "warn" else ""), [p.name]))
    return out


# --------------------------------------------------------------------------- logic levels


def _logic_pins(c: Circuit, an: Analysis, net: str):
    drivers, receivers = [], []
    for r in c.nets[net].pins:
        p, ps = c.parts[r.part], c.pin_spec(r)
        if ps.kind == "io" and p.kind == "board":
            mode = p.mode(r.pin)
            if mode != "in" and "input_only" not in ps.roles:
                drivers.append(r)
            if mode != "out":
                receivers.append(r)
        elif ps.kind == "out":
            drivers.append(r)
        elif ps.kind in ("in", "od"):
            receivers.append(r)
        elif ps.kind == "io":
            drivers.append(r)
            receivers.append(r)
    return drivers, receivers


def _levels(c: Circuit, an: Analysis, r: PinRef) -> dict:
    ps = c.pin_spec(r)
    return ps.logic.at(an.vdd.get(r.part)) if ps.logic else {}


def _logic(c: Circuit, an: Analysis) -> list[Check]:
    out, ok_inputs, ok_levels = [], [], []
    for n, info in an.nets.items():
        if info.kind in ("rail", "gnd", "short"):
            continue
        drivers, receivers = _logic_pins(c, an, n)
        od = open_drain(c, n)
        high, why = net_high(c, an, n)
        # inputs above their absolute maximum (a pin's own part driving it is not a hazard to it)
        for r in dict.fromkeys(receivers + drivers):
            lv = _levels(c, an, r)
            vmax = lv.get("vmax")
            if vmax is None or high is None:
                continue
            outside = [d for d in drivers if d.part != r.part] or [x for x in c.nets[n].pins if x.part != r.part and c.parts[x.part].kind == "resistor"]
            if not outside and not od:
                continue
            src = f" [source: {cite(c.pin_spec(r).logic.src)}]"
            if high > vmax + 1e-9:
                out.append(_c("logic", f"input voltage {r} ({n})", "fail", f"{n} reaches {high:g} V ({'; '.join(why)}), above {r}'s absolute maximum {vmax:g} V{src}. "
                              "Fix: a level shifter, a divider on the line, or run both sides from the same voltage", f"<= {vmax:g} V", [r.part, n]))
            else:
                ok_inputs.append((r, n))
        # driver high level against receiver VIH (push-pull nets; an open-drain net's high level is its pull-up rail)
        if od:
            ups = pullups_on(c, an, n)
            vhigh = min((v for _, _, v, _ in ups), default=None)
            for r in receivers:
                lv = _levels(c, an, r)
                if vhigh is None or lv.get("vih") is None:
                    continue
                if vhigh < lv["vih"] - 1e-9:
                    out.append(_c("logic", f"high level {n} -> {r}", "fail", f"pulled up to {vhigh:g} V; {r} reads high only from {lv['vih']:g} V (VIH min). "
                                  "Fix: pull-ups to the higher device's supply where every device tolerates it, or a level shifter"
                                  f" [source: {cite(c.pin_spec(r).logic.src)}]", f">= {lv['vih']:g} V", [r.part, n]))
                else:
                    ok_levels.append((r, n))
            continue
        for d in drivers:
            for r in receivers:
                if d.part == r.part:
                    continue
                ld, lr = _levels(c, an, d), _levels(c, an, r)
                if lr.get("vih") is None or (ld.get("voh") is None and an.vdd.get(d.part) is None):
                    continue
                vdd_d = an.vdd.get(d.part)
                voh = ld.get("voh")
                srcs = "; ".join(x for x in (cite(c.pin_spec(d).logic.src) if c.pin_spec(d).logic else "", cite(c.pin_spec(r).logic.src)) if x)
                if vdd_d is not None and vdd_d < lr["vih"] - 1e-9:
                    out.append(_c("logic", f"high level {d} -> {r}", "fail", f"{d} runs at {vdd_d:g} V, below {r}'s VIH min {lr['vih']:g} V: a high is never read. "
                                  f"Fix: a level shifter [source: {srcs}]", f">= {lr['vih']:g} V", [d.part, r.part, n]))
                elif voh is not None and voh < lr["vih"] - 1e-9:
                    out.append(_c("logic", f"high level {d} -> {r}", "warn", f"{d} guarantees {voh:g} V high (VOH min), below {r}'s VIH min {lr['vih']:g} V: it reads "
                                  "high only while the pin is lightly loaded, with no datasheet margin. Fix: a level shifter (a buffer at the receiver's voltage)"
                                  f" [source: {srcs}]", f"VOH >= {lr['vih']:g} V", [d.part, r.part, n]))
                else:
                    ok_levels.append((r, n))
    if ok_inputs:
        out.append(_c("logic", "inputs within their absolute maximum", "pass", f"{len(ok_inputs)} input(s): " + ", ".join(f"{r} on {n}" for r, n in ok_inputs[:10])
                      + (" ..." if len(ok_inputs) > 10 else ""), "", sorted({r.part for r, _ in ok_inputs})))
    if ok_levels:
        out.append(_c("logic", "high levels read as high", "pass", f"{len(ok_levels)} receiver(s): " + ", ".join(f"{r} on {n}" for r, n in ok_levels[:10])
                      + (" ..." if len(ok_levels) > 10 else ""), "", sorted({r.part for r, _ in ok_levels})))
    return out


def _floating(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for n, info in an.nets.items():
        if info.kind in ("rail", "gnd", "short") or info.drivers:
            continue
        inputs = [r for r in c.nets[n].pins if (c.pin_spec(r).kind == "in") or
                  (c.pin_spec(r).kind == "io" and c.parts[r.part].kind == "board" and c.parts[r.part].mode(r.pin) == "in")]
        if not inputs:
            continue
        internal = [r for r in inputs if c.parts[r.part].kind == "board" and r.pin in c.parts[r.part].pullups]
        pulls = pullups_on(c, an, n) + [(x, 0, 0, "") for x, _ in pulldowns_on(c, an, n)] + resistors_to_fixed(c, an, n)
        outs = [r for r in c.nets[n].pins if c.pin_spec(r).kind in ("out", "od")]
        through = [s for s in an.strings if n in s.inner or (s.network is not None and n in (s.a, s.b))]
        if internal or pulls or outs or through:
            continue
        sw = [r.part for r in c.nets[n].pins if c.parts[r.part].kind == "switch"]
        out.append(_c("logic", f"input defined {n}", "fail",
                      f"{', '.join(map(str, inputs))} floats" + (f" while {', '.join(sw)} is open" if sw else "") + ": nothing pulls it to a level. "
                      "Fix: a pull-up resistor to the input's supply (or c.gpio(pin, 'in', pullup=True) for the board's own), or a pull-down",
                      "a pull-up, a pull-down or a driver", [r.part for r in inputs] + sw + [n]))
    return out


# --------------------------------------------------------------------------- I2C


def i2c_buses(c: Circuit) -> list[tuple[str, str | None, list[str]]]:
    """(SDA net, SCL net, devices) for every bus found from the pins' roles."""
    sda_nets: dict[str, set[str]] = {}
    scl_nets: dict[str, set[str]] = {}
    for p in c.parts.values():
        for pn, ps in p.pins.items():
            n = c._net_of.get(PinRef(p.name, pn))
            if not n:
                continue
            if "i2c_sda" in ps.roles:
                sda_nets.setdefault(n, set()).add(p.name)
            if "i2c_scl" in ps.roles:
                scl_nets.setdefault(n, set()).add(p.name)
    out = []
    for sda, devs in sda_nets.items():
        scl = max(scl_nets, key=lambda k: len(scl_nets[k] & devs), default=None)
        if len([d for d in devs]) < 2:
            continue
        out.append((sda, scl if scl and scl_nets[scl] & devs else None, sorted(devs)))
    return out


def _i2c(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for sda, scl, devs in i2c_buses(c):
        bus = c.buses.get(sda)
        speed = bus.speed_hz if bus else 100_000
        mode = next((m for m in I2C_MODES if speed <= m[0]), None)
        if mode is None:
            out.append(_c("i2c", f"I2C clock {sda}", "fail", f"{speed / 1000:g} kHz is above Fast-mode Plus (1 MHz) [source: {cite('nxp-um10204')}]", "<= 1000 kHz", [sda]))
            continue
        fmax, mname, tr, cb_max, iol = mode
        nd = len(devs)
        if bus and bus.capacitance_pF is not None:
            cb, cb_how = bus.capacitance_pF * 1e-12, f"{bus.capacitance_pF:g} pF (from the model file)"
        else:
            cb = nd * I2C_CI + I2C_WIRING
            cb_how = (f"{cb * 1e12:.0f} pF estimated: {nd} x 10 pF per device pin (UM10204 Ci max) + 50 pF of wiring (ASSUMPTION: no source; "
                      f"give c.i2c(..., capacitance_pF=...) if measured)")
        for line in (sda, scl):
            if line is None:
                continue
            ups = pullups_on(c, an, line)
            name = f"I2C pull-ups {line} ({mname}, {speed / 1000:g} kHz)"
            if not ups:
                out.append(_c("i2c", name, "fail", f"no pull-up on {line}: the open-drain lines never go high. Fix: a resistor from {line} to the "
                              f"devices' supply (4.7 kΩ suits a short Standard-mode bus) [source: {cite('nxp-um10204')}]", "a pull-up resistor per line", [line] + devs))
                continue
            rp = 1.0 / sum(1.0 / r for _, r, _, _ in ups)
            vdd = max(v for _, _, v, _ in ups)
            rmin = (vdd - I2C_VOL) / iol
            rmax = tr / (0.8473 * cb)
            parts = ", ".join(f"{w} {ohms(r)}" for w, r, _, _ in ups)
            refs = [line] + [w.split(" ")[0] for w, _, _, _ in ups]
            if rp < rmin - 1e-9:
                sev, msg = "fail", f"too strong: the devices must sink {amps_text((vdd - I2C_VOL) / rp)} to reach {I2C_VOL:g} V, over the {amps_text(iol)} {mname} guarantees. Fix: larger pull-ups, or remove a module's on-board set"
            elif rp > rmax + 1e-9:
                sev, msg = "fail", f"too weak: the rise time would exceed {tr * 1e9:g} ns. Fix: smaller pull-ups, a slower clock or less capacitance"
            else:
                sev, msg = "pass", "within the range"
            out.append(_c("i2c", name, sev, f"{ohms(round(rp, 1))} ({parts}) to {vdd:g} V; allowed {ohms(round(rmin))} to {ohms(round(rmax))} with Cb = {cb_how}: {msg} "
                          f"[source: {cite('nxp-um10204')}: Rp(min) = (VDD - VOL)/IOL, Rp(max) = tr/(0.8473 x Cb)]",
                          f"{ohms(round(rmin))} to {ohms(round(rmax))}", refs))
        if cb > cb_max:
            out.append(_c("i2c", f"I2C bus capacitance {sda}", "fail", f"{cb * 1e12:.0f} pF over {mname}'s {cb_max * 1e12:g} pF [source: {cite('nxp-um10204')}]",
                          f"<= {cb_max * 1e12:g} pF", [sda]))
        # addresses
        at: dict[int, list[str]] = {}
        for d in devs:
            a = c.parts[d].spec.get("address")
            if a is not None:
                at.setdefault(int(a), []).append(d)
        clash = {a: ds for a, ds in at.items() if len(ds) > 1}
        refs = [sda] + devs
        if clash:
            out.append(_c("i2c", f"I2C addresses unique on {sda}", "fail",
                          "; ".join(f"{', '.join(ds)} all at {hex(a)}" for a, ds in clash.items())
                          + ". Fix: set another address on one of them (address jumpers: c.module(..., address=...)) or a second bus / a multiplexer",
                          "one device per address", refs))
        else:
            out.append(_c("i2c", f"I2C addresses unique on {sda}", "pass",
                          ", ".join(f"{ds[0]} {hex(a)}" for a, ds in sorted(at.items())) or "no addressed devices", "one device per address", refs))
    return out


def _avr_wire_pullups(c: Circuit, an: Analysis) -> list[Check]:
    """Arduino's Wire library on an AVR board turns on the internal pull-ups (20-50 kOhm to 5 V) of SDA and SCL. A device on the bus
    that cannot take 5 V sees them (unless the firmware turns them off and the model says so)."""
    out = []
    for sda, scl, devs in i2c_buses(c):
        for b in devs:
            board = c.parts[b]
            if board.kind != "board" or not board.spec.get("wire_pullups"):
                continue
            rmin, rmax = board.spec["wire_pullups"]
            vcc = float(board.spec.get("vdd") or 5.0)
            bus = c.buses.get(sda)
            src = f" [source: {cite(board.srcs.get('wire_pullups', ''))}; {cite('atmega328p')}]"
            name = f"AVR internal pull-ups on I2C {sda} ({b})"
            refs = [b, sda] + ([scl] if scl else [])
            low = []                                   # (device pin, its absolute maximum, line)
            for line in (sda, scl):
                if line is None:
                    continue
                for r in c.nets[line].pins:
                    if r.part == b or c.parts[r.part].kind == "board":
                        continue
                    lv = _levels(c, an, r)
                    if lv.get("vmax") is not None and lv["vmax"] < AVR_VMAX - 1e-9:
                        low.append((r, lv["vmax"], line))
            if bus is not None and not bus.internal_pullups:
                out.append(_c("i2c", name, "pass", f"declared off (c.i2c(..., internal_pullups=False)): the firmware turns them off after Wire.begin() "
                              "(digitalWrite(SDA, LOW); digitalWrite(SCL, LOW);), else the lines see 5 V through 20-50 kΩ" + src, "", refs))
                continue
            if not low:
                out.append(_c("i2c", name, "pass", f"Wire.begin() turns on {b}'s internal pull-ups ({ohms(rmin)}-{ohms(rmax)} to {vcc:g} V) on SDA and SCL; "
                              f"every device on the bus takes {AVR_VMAX:g} V or more" + src, "", refs))
                continue
            worst = []
            for line in dict.fromkeys(x[2] for x in low):
                ups = pullups_on(c, an, line)
                g = sum(1.0 / r for _, r, _, _ in ups) + 1.0 / rmin
                v = (sum(v / r for _, r, v, _ in ups) + vcc / rmin) / g
                ext = ", ".join(f"{w} {ohms(r)} to {v_:g} V" for w, r, v_, _ in ups) or "no other pull-up"
                worst.append(f"{line} rises to {v:.2f} V ({ohms(rmin)} to {vcc:g} V against {ext})")
            devs_txt = "; ".join(f"{r} tolerates at most {vm:g} V [source: {cite(c.pin_spec(r).logic.src)}]" for r, vm, _ in low)
            out.append(_c("i2c", name, "warn",
                          f"Wire.begin() turns on {b}'s internal pull-ups, {ohms(rmin)}-{ohms(rmax)} to {vcc:g} V, on SDA and SCL{src}. {devs_txt}. "
                          f"With them on, {'; '.join(worst)}: over-voltage on the 3.3 V side. Fix: a level shifter between {b} and the 3.3 V devices, "
                          "or a 3.3 V-only bus: pull-ups to 3.3 V only and the internal pull-ups turned off after Wire.begin() "
                          "(digitalWrite(SDA, LOW); digitalWrite(SCL, LOW);), declared with c.i2c(sda, internal_pullups=False)",
                          f"every device on the bus takes {vcc:g} V, or the internal pull-ups off", refs + sorted({r.part for r, _, _ in low})))
    return out


# --------------------------------------------------------------------------- inductive loads


def _inductive(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for p in c.parts.values():
        if p.kind == "relay":
            a, b = "COIL1", "COIL2"
        elif p.kind in ("motor", "solenoid"):
            a, b = "+", "-"
        else:
            continue
        na, nb = c._net_of.get(PinRef(p.name, a)), c._net_of.get(PinRef(p.name, b))
        if not na or not nb:
            continue
        switch = None
        for n in (na, nb):
            info = an.nets[n]
            if info.kind == "switched":
                switch = (n, next(r for r in c.nets[n].pins if c.parts[r.part].kind in ("mosfet", "bjt") and r.pin in ("D", "C")))
            elif info.kind == "driven":
                switch = (n, info.drivers[0])
        if switch is None:
            continue                                   # switched by contacts, a driver module, or not at all: not this check's case
        low, by = switch
        high = nb if low == na else na
        i = max((s.current for s in an.through(p.name) if any(e.part == p.name and (p.kind != "relay" or e.enter.startswith("COIL"))
                                                              for e in s.elements)), default=0.0)
        i = 0.0 if i == INF else i
        diodes = [d for d in c.parts.values() if d.kind == "diode" and
                  {c._net_of.get(PinRef(d.name, "A")), c._net_of.get(PinRef(d.name, "K"))} == {low, high}]
        name = f"flyback diode {p.name} ({p.kind}{' coil' if p.kind == 'relay' else ''})"
        refs = [p.name, low, high, by.part]
        src = f" [source: {cite('te-coil-suppression')}]"
        if not diodes:
            out.append(_c("inductive", name, "fail",
                          f"{p.name} is an inductive load switched by {by} with no diode across it: at turn-off the coil's voltage spikes far above the "
                          f"supply and can destroy {by.part}. Fix: a diode across the coil, cathode to {high} (the supply side), anode to {low}{src}",
                          "a reverse-biased diode across the coil", refs))
            continue
        d = diodes[0]
        ka = c._net_of.get(PinRef(d.name, "K"))
        if ka != high:
            out.append(_c("inductive", name, "fail", f"{d.name} is across {p.name} the wrong way round (cathode on {low}): when {by.part} switches on it shorts "
                          f"{high} through {by.part}. Fix: turn {d.name} round, cathode to {high}{src}", "cathode to the supply side", refs + [d.name]))
            continue
        rating = float(d.spec["if_avg"])
        sev = _lvl(i, rating)
        out.append(_c("inductive", name, sev, f"{d.name} ({d.catalog}) across it, cathode to {high}; it carries the coil's {amps_text(i)} at turn-off, rated "
                      f"{amps_text(rating)}{_src(d, 'if_avg')}" + (". Fix: a diode rated for the coil current (1N4007, 1N5819)" if sev == "fail" else ""),
                      f"<= {amps_text(rating)}", refs + [d.name]))
    return out


def _contacts(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for p in c.parts.values():
        if p.kind != "relay" or not p.spec.get("contact_A"):
            continue
        ss = [s for s in an.through(p.name) if any(e.part == p.name and e.enter in ("COM", "NO") for e in s.elements)]
        i = max((s.current for s in ss), default=0.0)
        if not ss or i in (0.0, INF):
            continue
        sev = _lvl(i, p.spec["contact_A"])
        out.append(_c("switching", f"relay contacts {p.name}", sev, f"{amps_text(i)} through COM-NO ({', '.join(ss[0].parts)}) of {p.spec['contact_A']:g} A "
                      f"(resistive){_src(p, 'contact_A')}", f"<= {p.spec['contact_A']:g} A", [p.name] + ss[0].parts))
    return out


# --------------------------------------------------------------------------- transistors


def _gate_voltage(c: Circuit, an: Analysis, q) -> tuple[float | None, str]:
    g = c._net_of.get(PinRef(q.name, "G"))
    if not g:
        return None, "the gate is not connected"
    info = an.nets[g]
    if info.kind == "rail":
        return info.volts, f"G on rail {g}"
    best, how = None, ""
    for d in info.drivers:
        lv = _levels(c, an, d)
        v = lv.get("voh") if lv.get("voh") is not None else an.vdd.get(d.part)
        if v is not None and (best is None or v > best):
            best, how = v, f"{d} drives the gate (VOH min {v:g} V) [source: {cite(c.pin_spec(d).logic.src) if c.pin_spec(d).logic else ''}]"
    if not an.voltages_at(g):                                  # a series resistor from a driven pin into a gate that draws no current
        for rname, other in resistors_to_fixed(c, an, g):
            o = an.nets[other]
            for d in o.drivers:
                lv = _levels(c, an, d)
                v = lv.get("voh") if lv.get("voh") is not None else an.vdd.get(d.part)
                if v is not None and (best is None or v > best):
                    best, how = v, f"{d} through {rname} (VOH min {v:g} V, no current)"
            if o.kind == "rail" and (best is None or o.volts > best):
                best, how = o.volts, f"rail {other} through {rname}"
    for v, s, _combo in an.voltages_at(g):
        if s.network is None:
            continue
        ends = {n for t in an.strings if t.network == s.network for n in (t.a, t.b) if an.nets[n].kind == "driven"}
        scale = min((_levels(c, an, d)["voh"] / an.vdd[d.part] for n in ends for d in an.nets[n].drivers
                     if _levels(c, an, d).get("voh") is not None and an.vdd.get(d.part)), default=1.0)
        if best is None or v * scale > best:
            best, how = round(v * scale, 3), f"the node of {', '.join(s.parts)} ({v * scale:.2f} V at the gate: nodal solution, the drivers' VOH min scaled)"
    for s in an.strings:
        if g in s.inner and s.network is None:
            for va, vb, _i, node in s.combos:
                v = node.get(g)
                if v is None:
                    continue
                # through a resistor from a driven pin: scale by the driver's VOH min instead of its supply
                hi_net = s.a if va >= vb else s.b
                hdr = an.nets[hi_net]
                scale = 1.0
                for d in hdr.drivers:
                    lv = _levels(c, an, d)
                    if lv.get("voh") is not None and an.vdd.get(d.part):
                        scale = lv["voh"] / an.vdd[d.part]
                v *= scale
                if best is None or v > best:
                    best, how = round(v, 3), f"through {', '.join(s.parts)} from {hi_net} ({v:.2f} V at the gate, the driver's VOH min scaled through the divider)"
    return best, how


def _mosfets(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for q in c.parts.values():
        if q.kind != "mosfet":
            continue
        s = q.spec
        vg, how = _gate_voltage(c, an, q)
        name = f"gate drive {q.name} ({q.catalog})"
        table = sorted(s["rds_on"])                      # [(vgs, ohm max)]
        lowest = table[0][0]
        if vg is None:
            out.append(_c("switching", name, "fail", f"nothing drives {q.name}'s gate ({how})", "a GPIO or driver on G", [q.name]))
            continue
        usable = [(v, r) for v, r in table if v <= vg + 1e-9]
        if not usable:
            out.append(_c("switching", name, "fail", f"VGS {vg:.2f} V ({how}); {q.catalog}'s RDS(on) is specified only from VGS = {lowest:g} V: below that it is not "
                          f"guaranteed to switch fully on{_src(q, 'rds_on')}. Fix: a logic-level MOSFET specified at your drive voltage, or a gate driver", f"VGS >= {lowest:g} V", [q.name]))
            continue
        vgs_ok, rds = usable[-1]
        out.append(_c("switching", name, "pass", f"VGS {vg:.2f} V ({how}); RDS(on) {rds * 1000:g} mΩ max at VGS = {vgs_ok:g} V{_src(q, 'rds_on')}", f"VGS >= {lowest:g} V", [q.name]))
        d = c._net_of.get(PinRef(q.name, "D"))
        i = sum(x.current for x in an.at_net(d) if x.current != INF) if d else 0.0
        if i <= 0:
            continue
        pd = i * i * rds
        tj = c.ambient_C + pd * s["theta_ja"]
        sev = "fail" if i > s["id_max"] else _lvl(tj - c.ambient_C, s["tj_max"] - c.ambient_C)
        out.append(_c("switching", f"MOSFET dissipation {q.name}", sev,
                      f"{amps_text(i)} drain current (rated {s['id_max']:g} A): {pd * 1000:.3g} mW, junction {tj:.0f} C ({s['theta_ja']:g} C/W, no heatsink), limit {s['tj_max']:g} C{_src(q, 'theta_ja')}",
                      f"<= {s['tj_max']:g} C", [q.name, d]))
    return out


def _gate_pulldowns(c: Circuit, an: Analysis) -> list[Check]:
    """A MOSFET gate a board's GPIO drives needs a pull-down: an AVR's pins are tri-stated (inputs) while it resets and its
    bootloader runs, so a gate with nothing to ground floats and the MOSFET can turn on partly. Graded where the board's pin
    behaviour at reset is sourced (the ATmega328P boards); a warning, as the datasheet gives the pin state, not a failure rate."""
    out = []
    for q in c.parts.values():
        if q.kind != "mosfet":
            continue
        g = c._net_of.get(PinRef(q.name, "G"))
        if not g:
            continue
        sides = [(g, None)] + [(other, rname) for rname, other in resistors_to_fixed(c, an, g)]
        pins = [(d, rname, n) for n, rname in sides for d in an.nets[n].drivers if c.parts[d.part].kind == "board"]
        pins = [(d, rname, n) for d, rname, n in pins if (c.pin_spec(d).logic and c.pin_spec(d).logic.src == "atmega328p")]
        if not pins:
            continue
        name = f"gate pull-down {q.name} ({q.catalog})"
        downs = [(r, ohm, n) for n, _ in sides for r, ohm in pulldowns_on(c, an, n)]
        d, rname, _n = pins[0]
        via = f" through {rname}" if rname else ""
        src = f" [source: {cite('atmega328p')}]"
        if downs:
            r, ohm, n = downs[0]
            out.append(_c("switching", name, "pass", f"{r} ({ohms(ohm)}) pulls {q.name}'s gate to ground while {d}{via} is not driving it "
                          f"(the pin is tri-stated through reset{src})", "a resistor from the gate to GND", [q.name, r]))
        else:
            out.append(_c("switching", name, "warn",
                          f"{q.name}'s gate is driven by {d}{via} and nothing pulls it to ground: the pin is an input (high impedance) while "
                          f"the board resets and its bootloader runs{src}, so the gate floats and {q.name} can switch its load on partly. "
                          f"Fix: a resistor from {q.name}.G to GND (a pull-down, typically 10-100 kΩ)",
                          "a resistor from the gate to GND", [q.name, g]))
    return out


def _bjts(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    for q in c.parts.values():
        if q.kind != "bjt":
            continue
        s = q.spec
        b, col = c._net_of.get(PinRef(q.name, "B")), c._net_of.get(PinRef(q.name, "C"))
        name = f"transistor {q.name} ({q.catalog})"
        if b and an.nets[b].kind == "driven":
            out.append(_c("switching", name, "fail", f"{', '.join(map(str, an.nets[b].drivers))} drives the base directly: nothing limits the base current. "
                          "Fix: a base resistor (1 kΩ from a 5 V pin gives about 4 mA)", "a resistor in series with the base", [q.name, b]))
            continue
        vmin, vmax = float(s.get("vbe_on", 0.6)), float(s.get("vbe_max", s.get("vbe_on", 0.6)))
        ib = 0.0
        for x in (an.at_net(b) if b else []):                 # base current at the highest VBE(sat): the least drive the datasheet allows
            if x.current == INF or not x.current:
                continue
            vhi = x.va if x.flow[0] == x.a else x.vb
            k = max(0.0, (vhi - vmax)) / max(1e-9, (vhi - vmin))
            ib += x.current * k if x.flow[1] == b else -x.current * (vmax / vmin)
        ic = sum(x.current for x in an.at_net(col) if x.current != INF) if col else 0.0
        if ic <= 0:
            continue
        if ic > s["ic_max"]:
            out.append(_c("switching", name, "fail", f"{amps_text(ic)} collector current, over its {amps_text(s['ic_max'])} rating{_src(q, 'ic_max')}", f"<= {amps_text(s['ic_max'])}", [q.name, col]))
            continue
        need = ic / s["forced_beta"]
        sev = "pass" if ib >= need - 1e-12 else "warn"
        out.append(_c("switching", name, sev, f"IC {amps_text(ic)}, IB {amps_text(max(ib, 0))}: " +
                      (f"saturated (IB >= IC/{s['forced_beta']:g}, the datasheet's VCE(sat) condition)" if sev == "pass" else
                       f"IB under IC/{s['forced_beta']:g} = {amps_text(need)} (at VBE(sat) max {vmax:g} V): VCE(sat) is not guaranteed, the transistor may run hot. Fix: a smaller base resistor")
                      + _src(q, "forced_beta"), f"IB >= {amps_text(need)}", [q.name, b]))
    return out


# --------------------------------------------------------------------------- part data


def _parts(c: Circuit, an: Analysis) -> list[Check]:
    out = []
    odd = []
    for p in c.parts.values():
        if p.kind == "resistor":
            r = float(p.spec["ohm"])
            near = nearest_e(r, "E24")
            if abs(near - r) / r > 0.005:
                odd.append((p.name, r, near))
    if odd:
        out.append(_c("parts", "standard resistor values (E24)", "warn", "; ".join(f"{n} {ohms(r)} is not an E24 value (nearest {ohms(near)})" for n, r, near in odd)
                      + f" [source: {cite('iec-60063')}]", "E24 values", [n for n, _, _ in odd]))
    elif any(p.kind == "resistor" for p in c.parts.values()):
        out.append(_c("parts", "standard resistor values (E24)", "pass", "every resistor is an E24 value" + f" [source: {cite('iec-60063')}]"))
    generic = [p for p in c.parts.values() if p.kind == "led" and p.spec.get("generic")]
    if generic:
        out.append(_c("parts", "LED data from the LED's own datasheet", "warn",
                      ", ".join(f"{p.name} ({p.spec.get('color')}: Vf {p.spec['vf']:g} V, {amps_text(p.spec['if_max'])} max)" for p in generic)
                      + f" are graded on a typical 5 mm part's figures [source: {cite(generic[0].srcs.get('if_max', ''))}]; give led(..., vf=, if_max=) from your LED's datasheet",
                      "vf and if_max of the LED used", [p.name for p in generic]))
    for p in c.parts.values():
        if p.kind != "capacitor" or not p.spec.get("v_rated"):
            continue
        nets = [c._net_of.get(PinRef(p.name, n)) for n in p.pins]
        vs = [an.nets[n].volts or 0.0 for n in nets if n and an.nets[n].kind in ("rail", "gnd")]
        if len(vs) == 2:
            v = abs(vs[0] - vs[1])
            sev = _lvl(v, p.spec["v_rated"])
            out.append(_c("parts", f"capacitor voltage {p.name}", sev, f"{v:g} V across a {p.spec['v_rated']:g} V part (from the model file)",
                          f"<= {p.spec['v_rated']:g} V", [p.name]))
    return out
