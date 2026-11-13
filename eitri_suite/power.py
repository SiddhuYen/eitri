"""Supply current against what the firmware moves together.

The electronics file's "actuators: stall current vs <driver>" check (eitri_suite.electronics) takes every actuator stalled at once.
This check sharpens it with what the firmware does: the actuators that move in the same step of the firmware's SEQUENCE are taken
at their catalog stall current (the conservative figure: a servo starting a move under load draws close to stall), plus the logic
load on the same supply, against the supply's rated current. It does not replace the all-stalled check, and it also runs where
that one cannot: in the firmware file's build, from the supply the CAD file buys, when the project has no electronics file.

Which actuators move together (firmware COUPLING motion=):
  "together" (the default)  every servo whose angle changes from one SEQUENCE step to the next moves in that step; at power-up every
                            servo drives to the first step's angle at once.
  "sequential"              the sketch moves one joint at a time, start-up included (a declaration: the sketch itself is not read).
  no SEQUENCE               all actuators at once, and the check says so.
Currents: the core electronics catalog's stall current for the joint's actuator (eitri.electronics.catalog SERVOS, sourced), or the
firmware's own servo(current_A=) when higher or when the model is not in the catalog. Servos on a board pin (power="5V"/"3V3") are the
core power-budget check's, not this one's; a servo with no power= is counted on the external supply (conservative). Holding servos
are counted at 0 A (no published holding current): a servo holding a load draws more, so the margin is not a guarantee.
Logic load: the board's supply-pin limit (ATmega328P VCC 200 mA for an Uno/Nano: the most the MCU can draw, from its datasheet) plus
the firmware's load(current_A=) entries, when the board is powered from the same supply: the harness wires the supply to the
board's 5V/VIN pin, or no harness says otherwise.
Supply rating: the power supply the CAD file's PURCHASED/HARDWARE buys (eitri_suite.catalog output_current_a, read from the maker's
datasheet and cited; an unverified catalog rating makes the check "not run"; said so); in
the electronics build, the circuit's own supply (what the all-stalled check reads).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from eitri.harness.checks import HCheck

NAME = "power: the supply covers the actuators that move together"
CAT = "integration"
WARN_FRACTION = 0.8
POWER_PINS = ("5V", "VIN", "+5V", "5V0", "VCC", "VBUS")


def _check(name: str, severity: str, measured: str, expected: str = "", refs=()) -> HCheck:
    return HCheck(name=name, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category=CAT,
                  refs=list(dict.fromkeys(str(r) for r in refs if r)))


def not_run(why: str, refs=(), name: str = NAME) -> HCheck:
    c = _check(name, "warn", f"not run: {why}", "a rated supply and the current of every actuator", refs)
    c.not_run = [why.split(":")[0][:120]]                    # status not_run (core eitri.checks)
    return c


def _a(x: float) -> str:
    return f"{x:.2f} A"


def _cite(sid: str) -> str:
    try:
        from eitri.electronics.sources import cite
        return cite(sid)
    except Exception:                                        # noqa: BLE001
        return sid


# ---------------------------------------------------------------------------- what moves together

def move_sets(module, fw, mech=None) -> tuple[list[tuple[str, list[str]]], str, list[str]]:
    """([(label, [servo names moving together])], basis, problems) from the firmware's SEQUENCE and COUPLING motion."""
    servos = [p.name for p in fw.peripherals if p.kind == "servo"]
    coupling = getattr(module, "COUPLING", None)
    coupling = coupling if isinstance(coupling, dict) else {}
    motion = str(coupling.get("motion") or "together").strip().lower()
    problems = [] if motion in ("together", "sequential") else [f"COUPLING motion={coupling.get('motion')!r} is not 'together' or 'sequential'"]
    seq = None
    if getattr(module, "SEQUENCE", None) is not None:
        from .assembly import sequence
        try:
            _c, seq = sequence(module, fw, mech)
        except Exception:                                    # noqa: BLE001
            seq = None
    if not seq or not seq.get("steps"):
        return [("all at once (the firmware declares no SEQUENCE)", servos)], "no SEQUENCE in the firmware: every actuator counted at once", problems
    steps = seq["steps"]
    sets: list[tuple[str, list[str]]] = []
    prev: dict[str, float] | None = None
    for s in steps:
        cur = {str(k): float(v) for k, v in (s.get("servos") or {}).items()}
        if prev is None:
            moving = [n for n in servos if n in cur] or list(servos)
            label = f"power-up to step '{s['name']}'"
        else:
            moving = [n for n in servos if n in cur and abs(cur[n] - prev.get(n, cur[n])) > 1e-9]
            label = f"step '{s['name']}'"
        if moving:
            sets.append((label, moving))
        prev = {**(prev or {}), **cur}
    if motion == "sequential":
        sets = [(f"{label}, {n} alone", [n]) for label, ms in sets for n in ms]
        basis = "COUPLING motion='sequential': the sketch moves one joint at a time (declared; the sketch is not read)"
    else:
        basis = ("the SEQUENCE: the servos whose angle changes in a step move together, and every servo moves at power-up "
                 "(COUPLING motion='sequential' declares a sketch that moves one joint at a time)")
    return sets, basis, problems


# ---------------------------------------------------------------------------- currents

def servo_currents(fw, module, mech=None) -> tuple[dict[str, tuple[float, str]], list[str], list[str]]:
    """({servo on the external supply: (amps, where from)}, servos on a board pin (not this check's), servos with no figure)."""
    from eitri.electronics.catalog import SERVOS
    coupling = getattr(module, "COUPLING", None)
    coupling = coupling if isinstance(coupling, dict) else {}
    jmap = {str(k): str(v) for k, v in (coupling.get("joints") or {}).items()}
    acts = {}
    if mech is not None:
        acts = {j.name: j.actuator for j in mech.joints if j.actuator is not None}
    out: dict[str, tuple[float, str]] = {}
    board_pin, unknown = [], []
    for p in fw.peripherals:
        if p.kind != "servo":
            continue
        power = (p.params or {}).get("power")
        if power in ("5V", "3V3"):
            board_pin.append(p.name)
            continue
        declared = (p.params or {}).get("current_A")
        a = acts.get(jmap.get(p.name, ""))
        cat = None
        if a is not None:                                    # "MG996R", or a name that holds a catalog model as a word ("SG90 jaw")
            words = str(a.name).upper().replace("-", " ").replace("_", " ").split()
            key = str(a.name).upper() if str(a.name).upper() in SERVOS else next((w for w in words if w in SERVOS), None)
            cat = SERVOS.get(key) if key else None
        amps, src = None, ""
        if a is not None and hasattr(a, "stall_current"):       # core 0.8.0: the model's stall_A, else the mechanism catalogue's sourced figure
            try:
                sa, ssrc = a.stall_current()
            except Exception:                                    # noqa: BLE001
                sa, ssrc = None, ""
            if sa is not None:
                amps, src = float(sa), f"{a.name} stall {float(sa):g} A [source: {ssrc}]"
        if cat is not None and (amps is None or float(cat["stall"]) > amps + 1e-12):    # the electronics catalogue's, when higher
            amps, src = float(cat["stall"]), f"{a.name} stall {cat['stall']:g} A [source: {_cite(cat['src'])}]"
        if declared is not None and (amps is None or float(declared) > amps + 1e-12):
            amps, src = float(declared), f"servo({p.name}) current_A {float(declared):g} A (firmware file)" + (f", above the catalog {cat['stall']:g} A" if cat else "")
        if amps is None:
            unknown.append(p.name + (f" ({a.name}: not in the catalog)" if a is not None else " (no joint/actuator model and no current_A)"))
            continue
        if power is None:
            src += "; no power= in the firmware, counted on the external supply"
        out[p.name] = (amps, src)
    return out, board_pin, unknown


def board_logic(fw) -> tuple[float, str] | None:
    """(amps, basis) of the controller board's own draw (the conservative supply-pin limit), or None when unknown."""
    try:
        from eitri.electronics.catalog import BOARDS
        b = fw.board
        key = getattr(b, "key", None) or getattr(b, "name", None)
        e = BOARDS.get(str(key).lower()) if key else None
        if e is None:
            return None
        lim, sid = e["spec"].get("total_limit_A"), e["srcs"].get("total_limit_A", "")
        if not lim:
            return None
        return float(lim), f"{e['title']} {lim * 1000:g} mA supply-pin limit [source: {_cite(sid)}]"
    except Exception:                                        # noqa: BLE001
        return None


def firmware_loads(fw) -> list[tuple[str, float]]:
    return [(p.name, float(p.params["current_A"])) for p in fw.peripherals
            if p.kind == "load" and (p.params or {}).get("current_A") is not None]


def board_on_supply(harness, board_connector: str | None) -> tuple[bool, str]:
    """Is the board powered from the actuators' supply? Only a harness whose board connector has no power pin says no."""
    if harness is None or not board_connector:
        return True, "no harness tells how the board is powered: counted on the supply (conservative)"
    cons = harness.connectors.values() if isinstance(harness.connectors, dict) else harness.connectors
    con = next((c for c in cons if str(getattr(c, "name", "")) == board_connector), None)
    if con is None:
        return True, f"the harness has no connector {board_connector}: the board counted on the supply (conservative)"
    raw = list(getattr(con, "pin_names", None) or []) + [str(v) for v in (getattr(con, "pin_labels", None) or {}).values()]
    pins = [str(x).upper() for x in raw]
    if any(p in POWER_PINS for p in pins):
        return True, f"the harness wires {board_connector}'s power pin"
    return False, f"the harness wires no power pin of {board_connector} ({', '.join(pins[:8])}): the board is powered separately"


# ---------------------------------------------------------------------------- the checks

def _grade(name: str, sets, currents: dict[str, tuple[float, str]], logic: list[tuple[str, float, str]], rating: float,
           rating_basis: str, basis: str, refs) -> tuple[HCheck, dict]:
    worst = None
    for label, ms in sets:
        on = [m for m in ms if m in currents]
        amps = sum(currents[m][0] for m in on)
        if worst is None or amps > worst[1]:
            worst = (label, amps, on)
    label, act_amps, on = worst if worst else ("nothing moves", 0.0, [])
    logic_amps = sum(a for _n, a, _s in logic)
    total = act_amps + logic_amps
    all_amps = sum(a for a, _s in currents.values()) + logic_amps
    sev = "fail" if total > rating + 1e-12 else "warn" if total >= WARN_FRACTION * rating else "pass"
    per = "; ".join(f"{m} {currents[m][1]}" for m in on)
    lg = (" + logic " + ", ".join(f"{n} {_a(a)} ({s})" for n, a, s in logic)) if logic else " + no logic load on this supply"
    msg = (f"{len(on)} actuator(s) move together at {label} ({', '.join(on) or 'none'}): {_a(act_amps)}{lg} = {_a(total)} of "
           f"{_a(rating)} ({rating_basis}): {total / rating * 100:.0f}%. Moves from {basis}. Per actuator: {per or 'none'}. "
           f"All actuators at once would be {_a(all_amps)}. Holding servos counted at 0 A")
    if sev == "fail":
        msg += (". Fix: a supply rated for at least this (with margin), or firmware that moves fewer of them at once "
                "(and COUPLING motion='sequential' if it moves one at a time)")
    data = {"worst": {"at": label, "moving": on, "actuators_A": round(act_amps, 3), "logic_A": round(logic_amps, 3), "total_A": round(total, 3)},
            "rating_A": rating, "all_at_once_A": round(all_amps, 3)}
    return _check(name, sev, msg, f"<= {_a(rating)} (warn from {WARN_FRACTION:.0%})", refs), data


def bom_supply(folder: Path) -> tuple[Any, str]:
    """(the one power supply the project's CAD files buy, why not) from PURCHASED / HARDWARE."""
    from eitri.model import load_model
    from .electronics import _bom_power
    from .projectfiles import files_by_kind
    found = []
    for f in files_by_kind(folder).get("cad", []):
        try:
            mod = load_model(f).module
        except Exception:                                    # noqa: BLE001
            continue
        for p in _bom_power(mod):
            if p not in found:
                found.append(p)
    if not found:
        return None, "no power supply with a rating in the CAD file's PURCHASED / HARDWARE (a suite-catalog id of category power)"
    if len(found) > 1:
        return None, f"the CAD files buy {len(found)} supplies ({', '.join(p.id for p in found)}): an electronics file says which feeds the actuators"
    return found[0], ""


def firmware_check(module, fw, folder: Path, mech=None, harness=None) -> tuple[list[HCheck], dict]:
    """The check in the firmware file's build: the firmware's moves, the CAD file's supply."""
    try:
        return _firmware_check(module, fw, folder, mech, harness)
    except Exception as exc:                                 # noqa: BLE001
        return [not_run(f"the check itself failed ({type(exc).__name__}: {exc})")], {}


def _firmware_check(module, fw, folder, mech, harness) -> tuple[list[HCheck], dict]:
    currents, board_pin, unknown = servo_currents(fw, module, mech)
    if not currents and not unknown:
        return ([not_run("no servo on an external supply" + (f" ({', '.join(board_pin)} on a board pin: the firmware's power-budget check)" if board_pin else ""))]
                if board_pin else []), {}
    if unknown:
        return [not_run(f"no current for {', '.join(unknown)}: give the joint a catalog actuator (MG996R, SG90) or servo(current_A=)", unknown)], {}
    sets, basis, problems = move_sets(module, fw, mech)
    if problems:
        return [_check(NAME, "fail", "; ".join(problems), "COUPLING motion = 'together' | 'sequential'")], {}
    supply, why = bom_supply(folder)
    if supply is None:
        return [not_run(why, list(currents))], {}
    rating = supply.value("output_current_a")
    if not rating:
        return [not_run(f"{supply.id} has no output_current_a in the suite catalog", [supply.id])], {}
    rating = float(rating)
    spec = supply.spec("output_current_a")
    if not spec.verified:                                    # never a verdict on a rating nobody compared with a datasheet
        return [not_run(f"ASSUMPTION: unverified catalog figure: {supply.id} {rating:g} A [source: {spec.cite}]. Name a supply whose "
                        "rating is read from its datasheet (a verified catalog part), or declare the circuit's supply in an electronics file",
                        [supply.id])], {}
    rating_basis = f"{supply.name}: {rating:g} A [source: {spec.cite}]"
    from .crossdomain import harness_for_firmware
    logic: list[tuple[str, float, str]] = []
    coupling = getattr(module, "COUPLING", None) or {}
    pins = list((coupling.get("pins") or {}).values()) if isinstance(coupling, dict) else []
    board_con = str(pins[0]).split(".")[0] if pins else None
    if harness is None:
        hpath, _n = harness_for_firmware(module, folder)
        if hpath is not None and hpath.is_file():
            try:
                from eitri.model import load_model
                harness = load_model(hpath).build()
            except Exception:                                # noqa: BLE001
                harness = None
    on_supply, why_board = board_on_supply(harness, board_con)
    if on_supply:
        bl = board_logic(fw)
        if bl is not None:
            logic.append(("board", bl[0], bl[1] + f"; {why_board}"))
        logic += [(n, a, "load(current_A) in the firmware") for n, a in firmware_loads(fw)]
    chk, data = _grade(NAME, sets, currents, logic, rating, rating_basis, basis, [supply.id] + list(currents))
    if not on_supply:
        chk = HCheck(name=chk.name, ok=chk.ok, measured=chk.measured + f". Logic: {why_board}", expected=chk.expected, severity=chk.severity,
                     category=chk.category, refs=chk.refs)
    data.update(supply=supply.id, basis=basis)
    return [chk], data


def circuit_checks(groups: list[dict], fw, fw_module, mech, bindings: dict, circuit) -> tuple[list[HCheck], dict]:
    """In the electronics build: for each supply the actuators draw from (the groups of eitri_suite.electronics.actuator_checks with a
    rating), the actuators the firmware moves together, plus the boards powered from that net."""
    name = NAME + " (circuit)"
    supplied = [g for g in groups if g.get("kind") == "source" and g.get("rating")]
    if not supplied:
        return [], {}
    if fw is None:
        sets_c = None
        basis = "no firmware file: every actuator counted at once"
    else:
        sets, basis, problems = move_sets(fw_module, fw, mech)
        if problems:
            return [_check(name, "fail", "; ".join(problems), "COUPLING motion = 'together' | 'sequential'")], {}
        jmap = {}
        c = getattr(fw_module, "COUPLING", None)
        if isinstance(c, dict):
            jmap = {str(k): str(v) for k, v in (c.get("joints") or {}).items()}
        to_part = {s: bindings.get(j) for s, j in jmap.items() if bindings.get(j)}
        sets_c = [(label, [to_part[m] for m in ms if m in to_part]) for label, ms in sets]
    out, data = [], {}
    for g in supplied:
        currents = {pn: (i, txt) for pn, i, txt in g["items"]}
        sets_g = sets_c if sets_c is not None else [("all at once", list(currents))]
        mapped = {p for _l, ms in sets_g for p in ms}
        missing = [p for p in currents if p not in mapped]
        if missing and sets_c is not None:
            sets_g = [(l, ms + missing) for l, ms in sets_g]          # an actuator the firmware does not drive: counted in every move
        logic = []
        net = g.get("net")
        on_net = {r.part for r in circuit.nets[net].pins} if net and net in getattr(circuit, "nets", {}) else set()
        for part in getattr(circuit, "parts", {}).values():
            spec = getattr(part, "spec", {}) or {}
            if spec.get("total_limit_A") and part.name in on_net:
                logic.append((part.name, float(spec["total_limit_A"]), f"{getattr(part, 'title', part.name)} supply-pin limit [source: {_cite((part.srcs or {}).get('total_limit_A', ''))}]"))
        chk, d = _grade(name, sets_g, currents, logic, float(g["rating"]), g.get("basis") or "the circuit's supply", basis,
                        [getattr(g.get("drv"), "name", str(g.get("drv")))] + list(currents))
        out.append(chk)
        data[str(net)] = d
    return out, data
