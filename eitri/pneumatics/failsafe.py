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
