"""What each actuator does on a loss of electrical power and on a loss of air, from the valves that drive it.

The design declares, per cylinder, what it must do: `on_power_loss=` and `on_air_loss=`, each one of
    "hold"     stay where it is (keep the last commanded position)
    "extend"   end up extended (or keep pushing out)
    "retract"  end up retracted
    "release"  exert no force (both chambers vented)
and the check fails when the valves give a different state. Nothing declared: the check is "not run" and says why.

Valve behaviour (SMC 3/4/5-Port Solenoid Valves Precautions M-E03-3-02, https://www.smcworld.com/upfiles/pl/M-E03-3-02.pdf, and the
ISO 1219 symbols of the SY catalogue CAT.EUS11-83, https://static.smc.eu/pdf/SY_EU.pdf p.1-3; fetched 2026-11-12):
- solenoid valve, spring return (2 position single): de-energised it returns to its rest position (position 1); an internally
  piloted valve also returns there when the pilot air is lost.
- 2 position double solenoid (returns="solenoid" or "detent"): no return spring; it stays in its last position when both coils are
  off (M-E03-3-02 p.2 "Precautions for 2-position double solenoid valves": momentary energisation of at least 0.1 s switches it;
  p.1 item 10: on first use "actuators may travel in an unexpected direction depending on the switching position of the valve").
- 3 position (5/3): spring centred, so de-energised (or without pilot air) it goes to its centre: closed centre traps the air in
  both chambers, exhaust centre vents both, pressure centre pressurises both. M-E03-3-02 p.1 item 3: with a closed centre "it is
  difficult to make the piston stop at the required position accurately due to the compressibility of air ... it may not be
  possible to hold a stopped position for an extended period of time": a closed-centre hold is graded as a WARNING.
- manual, lever, pedal, roller and pilot valves are not moved by a loss of electrical power (they keep their position).
- loss of air: no chamber keeps a supply; a chamber still joined to the supply line or an exhaust loses its pressure (the supply
  line bleeds down; M-E03-3-02 p.1 item 7: install a residual-pressure release, residual pressure can remain); only air trapped
  between a closed valve port and the cylinder keeps acting (a closed centre, a closed 2/2 or 3/2). A single-acting cylinder's
  spring retracts it once its chamber vents.

Start state and air return (power_up(), air_return()). Further sources (fetched 2026-11-12):
- SMC Actuators Precautions M-E03-3-03 (https://www.smcworld.com/upfiles/pl/M-E03-3-03.pdf) p.1: item 8 "Design the circuitry to
  prevent the sudden lurching of driven objects. When a cylinder is driven by an exhaust center type directional control valve or
  when starting up after residual pressure is exhausted from the circuit, etc., the piston and its driven object will lurch at a high
  speed if pressure is applied to one side of the cylinder because of the absence of air pressure inside the cylinder"; item 9
  (emergency stop, power outage); item 11 "Consider the action of the cylinder when restarting after an emergency stop ... Install
  manually controlled equipment for safety when the actuator has to be reset to the starting position"; item 12 (closed centre
  does not hold). SMC Safety Instructions M-E03-3-01 p.1 item 3: "Before machinery/equipment is restarted, take measures to prevent
  unexpected operation and malfunction."
- SMC M-E03-3-02 p.1 item 10 (double solenoid, first use: "actuators may travel in an unexpected direction depending on the
  switching position of the valve"); p.7 Maintenance item 2 ("When the equipment is operated after remounting or replacement, first
  confirm that measures are in place to prevent the lurching of actuators"; with a double solenoid valve "releasing residual pressure
  rapidly may cause the spool valve to malfunction ... or the connected actuator to operate").
- Internally piloted valves need supply pressure to shift: SMC SY catalogue p.2 "Internal pilot operating pressure range" 0.15-0.7
  MPa (2 position single), 0.1-0.7 (double); Festo VUVG data sheet 574371 "Pilot air supply Internal", "Pilot pressure 0.3 MPa...0.8
  MPa", "Type of reset Mechanical spring". So when air returns with a coil still energised, the pilot shifts the main spool to the
  commanded position again, and the cylinder is driven there.
- Festo VUVG catalogue 2010/08 p.12: 5/3 normal positions "C = Normally closed, U = Normally open, E = Normally exhausted" (SMC:
  closed / pressure / exhaust centre).

These are design rules on the valve states. They are not a risk assessment (ISO 12100) or the validation of a safety function
(ISO 13849): a declared safeguard is printed as the basis and listed for a person to verify.
"""
from __future__ import annotations

import itertools

from .model import Circuit, Cylinder, Valve

STATES = ("hold", "extend", "retract", "release")
SOURCE = "SMC M-E03-3-02 (3/4/5-port solenoid valve precautions) p.1-2; SY catalogue CAT.EUS11-83 symbols"


def _after(v: Valve, event: str) -> int | None:
    """The valve's position after the event (index into v.positions), or None when it keeps its last position."""
    if v.cls == "5/3":
        return 1                                               # spring centred: power off or pilot air gone
    if event == "power" and v.actuation != "solenoid":
        return None                                            # not electrically operated
    if v.returns == "spring":
        return v.rest_position
    return None                                                # double solenoid / detent: stays where it was


def _port_state(c: Circuit, cyl: Cylinder, port: str, positions: dict[str, int], event: str) -> str:
    """P pressurised, X vented, B blocked (air trapped) for one cylinder port with the valves fixed at `positions`."""
    from .checks import find_path
    ref = cyl.port(port)
    sup = find_path(c, ref, "supply", positions)
    exh = find_path(c, ref, "exhaust", positions)
    if event == "power":
        return "P" if sup.found else "X" if exh.found else "B"
    return "X" if (sup.found or exh.found) else "B"            # air lost: the supply line bleeds down too


def _state(cyl: Cylinder, ports: dict[str, str]) -> str:
    if cyl.acting == "single":
        return {"P": "extend", "X": "retract", "B": "trapped"}[ports["CAP"]]
    cap, rod = ports["CAP"], ports["ROD"]
    if cap == "P":
        return "extend"                                        # rod side vented, blocked or pressurised (larger cap area wins)
    if rod == "P":
        return "retract"
    if cap == "B" and rod == "B":
        return "trapped"
    if cap == "X" and rod == "X":
        return "release"
    return "drift"                                             # one chamber trapped, the other vented: moves until it balances


def _valves_of(c: Circuit, cyl: Cylinder) -> list[Valve]:
    """The valves on any path from the cylinder's ports (in any position)."""
    from .checks import find_path
    names: list[str] = []
    for port in cyl.ports:
        for goal in ("supply", "exhaust"):
            r = find_path(c, cyl.port(port), goal)
            names += [n for n in r.valve_positions if n not in names]
    return [c.components[n] for n in names if isinstance(c.components.get(n), Valve)]


def outcome(c: Circuit, cyl: Cylinder, event: str) -> tuple[str, str]:
    """(state, how) of a cylinder after the event ("power" or "air"). state is one of hold, extend, retract, release, trapped
    (a hold by air trapped behind closed valve ports), drift."""
    valves = _valves_of(c, cyl)
    fixed, free = {}, []
    for v in valves:
        pos = _after(v, event)
        if pos is None:
            free.append(v)
        else:
            fixed[v.name] = pos
    states = set()
    for combo in itertools.product(*[range(len(v.positions)) for v in free]) if free else [()]:
        positions = dict(fixed)
        positions.update({v.name: i for v, i in zip(free, combo)})
        states.add(_state(cyl, {p: _port_state(c, cyl, p, positions, event) for p in cyl.ports}))
    how = ", ".join([f"{n} to position {i + 1}" + (" (centre)" if c.components[n].cls == "5/3" else " (rest)") for n, i in fixed.items()]
                    + [f"{v.name} stays in its last position" for v in free]) or "no valve"
    if len(states) == 1:
        return states.pop(), how
    if event == "power":
        return "hold", how                                     # a valve that keeps its last position keeps the last commanded state
    return ("trapped" if states == {"trapped"} else "release" if states <= {"release"} else "drift"), how


def grade(required: str, got: str) -> str:
    if got == required:
        return "pass"
    if required == "hold" and got == "trapped":
        return "warn"
    return "fail"


DISCLAIMER = "a design rule on the valve states, not an ISO 12100 risk assessment or ISO 13849 validation"
STATE_OF = {"extended": "extend", "retracted": "retract"}


def driven_state(c: Circuit, cyl: Cylinder, positions: dict[str, int]) -> str:
    """The state the valves at `positions` drive the cylinder to with air on: extend, retract, trapped, release or drift."""
    return _state(cyl, {p: _port_state(c, cyl, p, positions, "power") for p in cyl.ports})


def start_positions(c: Circuit, plan) -> dict[str, int]:
    """Each valve's position at the start of the cycle (the idle / loading state, coils off): a spring-return valve at rest, a 5/3
    at its centre; a double-solenoid valve where the last command of the cycle left it (after a normal cycle end)."""
    pos: dict[str, int] = {}
    for v in [x for x in c.components.values() if isinstance(x, Valve)]:
        p = _after(v, "power")
        if p is not None:
            pos[v.name] = p
    if plan is not None:
        for sp in plan.steps:                                   # the last position each double-solenoid valve is switched to
            for m in sp.moves:
                if m.valve and m.position is not None and _after(c.components[m.valve], "power") is None:
                    pos[m.valve] = m.position
    return pos


def power_up(c: Circuit, cyl: Cylinder, plan) -> tuple[str, str]:
    """(kind, state, how): what the valves do to a cylinder at power-up with air on and every coil off. kind: "drives" (a determined state
    in the text's first word: extend / retract), "held" (double solenoid: the last position it was switched to), "free" (no drive:
    a closed centre traps the air, an exhaust centre vents it)."""
    valves = _valves_of(c, cyl)
    st, how = outcome(c, cyl, "power")
    if st in ("extend", "retract"):
        return "drives", st, how
    if st == "hold":
        last = driven_state(c, cyl, start_positions(c, plan))
        return "held", last, how
    return "free", st, how


def step_positions(c: Circuit, plan) -> list[tuple[str, dict[str, int]]]:
    """(label, valve positions) at the start of the cycle and after each step: what the valves hold while powered."""
    pos = start_positions(c, plan)
    out = [("start", dict(pos))]
    for sp in (plan.steps if plan is not None else []):
        for m in sp.moves:
            if m.valve and m.position is not None:
                pos[m.valve] = m.position
        for v in [x for x in c.components.values() if isinstance(x, Valve)]:     # a spring-return valve not held on falls back
            if v.returns == "spring" and v.cls != "5/3" and v.coils and not any(sp.signals.get(sig) == "on" for _p, sig, _t in v.coils):
                pos[v.name] = v.rest_position
        out.append((f"step {sp.index} {sp.step.name}", dict(pos)))
    return out


def air_return(c: Circuit, cyl: Cylinder, plan) -> list[tuple[str, str, str]]:
    """Where the cylinder is driven when air returns after a loss, with power on and the valves as commanded: [(situation, driven
    state, state during the loss)] for every situation in which it may move without a new command (the chambers vented or trapped
    during the loss, a drive to one end when the pressure returns). Without a sequence every position of its valves is a situation."""
    loss, _how = outcome(c, cyl, "air")
    valves = _valves_of(c, cyl)
    if plan is not None:
        situations = step_positions(c, plan)
    else:
        situations = [(", ".join(f"{v.name} position {i + 1}" for v, i in zip(valves, combo)) or "as built", {v.name: i for v, i in zip(valves, combo)})
                      for combo in itertools.product(*[range(len(v.positions)) for v in valves])]
    out = []
    for label, positions in situations:
        d = driven_state(c, cyl, {k: v for k, v in positions.items() if k in {x.name for x in valves}})
        if d in ("extend", "retract") and loss != d:
            out.append((label, d, loss))
    return out
