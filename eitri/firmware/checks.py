"""The firmware checks: what every build is graded on.

    board        the target board is one eitri knows (uno, nano, esp32)
    pins         every declared pin exists on the board and may be used (not an ESP32 flash pin); no physical pin is declared twice
                 or taken by the hardware serial port while Serial is in use
    capability   each pin can do its role and direction: PWM on a PWM-capable pin, analog on an ADC pin, output not on an input-only
                 pin, an interrupt on an interrupt-capable pin, a pull-up where the pin has one; warnings for strapping pins, ADC2
                 with Wi-Fi, the LED pin as an input
    usage        the sources use every declared pin exactly as declared (pinMode direction, digitalWrite / analogWrite / analogRead /
                 servo.attach / attachInterrupt matching the role), use no pin the pinout does not declare, and use every pin it does
    compile      arduino-cli compiles the sketch for the board; each compile error is reported (file, line, message); without
                 arduino-cli or the board's core the check fails and says how to install it

The usage scan reads pin arguments written as PIN_<NET> (the constants eitri_pins.h defines), integer literals or A0..A7; a pin held in
a variable of another name is not followed (the scan says which calls it could not resolve).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import power as _power
from ..checks import Check, check as _check
from .boards import Board, PinCap
from .compile import CompileResult
from .model import ROLE_DIRECTION, Firmware, PinDecl

_CALLS = re.compile(r"\b(pinMode|digitalWrite|digitalRead|analogWrite|analogRead|tone|noTone|ledcAttachPin|ledcAttach|dacWrite|attachInterrupt|\w+\s*\.\s*attach)\s*\(\s*"
                    r"(digitalPinToInterrupt\s*\(\s*)?([A-Za-z_]\w*|\d+)\s*(?:\)\s*)?(?:,\s*([A-Z_]+))?", re.M)
OUT_ROLES = {"digital_out", "pwm", "dac", "servo", "serial_tx"}


@dataclass
class PinInfo:
    pin: str
    canonical: str | None
    number: int | None
    net: str
    symbol: str
    direction: str
    role: str
    peripheral: str
    uses: list[str] = field(default_factory=list)      # "pinMode(OUTPUT) @ sketch.ino:12", ...
    status: str = "pass"


def strip_comments(code: str) -> str:
    """Comments and string literals blanked out (line numbers kept)."""
    out = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), code, flags=re.S)
    out = re.sub(r"//[^\n]*", "", out)
    return re.sub(r'"(?:\\.|[^"\\\n])*"', '""', out)


def scan_calls(fw: Firmware) -> list[dict]:
    """Every pin-API call in the sources: {file, line, fn, arg, mode}."""
    found = []
    for s in fw.sources:
        code = strip_comments(s.code)
        for m in _CALLS.finditer(code):
            fn = re.sub(r"\s+", "", m.group(1))
            if "." in fn:
                if not fn.endswith(".attach"):
                    continue
                fn = "attach"
            if fn == "attachInterrupt" and not m.group(2) and not m.group(3).isdigit():
                pass
            found.append({"file": s.name, "line": code[:m.start()].count("\n") + 1, "fn": fn, "arg": m.group(3), "mode": m.group(4)})
    return found


def _resolve_arg(arg: str, fw: Firmware, b: Board | None, by_symbol: dict[str, PinDecl]) -> tuple[PinDecl | None, PinCap | None, bool]:
    """(declaration, board pin, understood): a PIN_ constant, a literal or A0..A7; any other identifier is not understood."""
    if arg in by_symbol:
        d = by_symbol[arg]
        return d, (b.resolve(d.pin) if b else None), True
    if arg.isdigit() or re.fullmatch(r"A[0-7]", arg):
        cap = b.resolve(arg) if b else None
        if cap is None:
            return None, None, True
        for d in fw.pins:
            if b and b.resolve(d.pin) == cap:
                return d, cap, True
        return None, cap, True
    return None, None, False


def run_checks(fw: Firmware, compiled: CompileResult | None = None) -> tuple[list[Check], list[PinInfo], dict]:
    checks: list[Check] = []
    b = fw.board
    infos: list[PinInfo] = []
    if b is None:
        checks.append(_check("board", "target board", "fail", f"unknown board {fw.board_key!r}", "one of uno, nano, esp32"))
    else:
        checks.append(_check("board", "target board", "pass", f"{b.name} ({b.fqbn})"))
    serial_used = fw.serial_baud is not None or any(re.search(r"\bSerial\s*\.\s*begin\s*\(", strip_comments(s.code)) for s in fw.sources)

    # -- pins against the board
    by_cap: dict[str, list[PinDecl]] = {}
    for d in fw.pins:
        cap = b.resolve(d.pin) if b else None
        info = PinInfo(d.pin, cap.name if cap else None, cap.number if cap else None, d.net, d.symbol, d.direction, d.role, d.peripheral)
        infos.append(info)
        ref = cap.name if cap else d.pin
        if b is None:
            info.status = "ungraded"
            continue
        if cap is None:
            checks.append(_check("pins", f"pin {d.pin} ({d.net})", "fail", f"{d.pin} is not a pin of the {b.key}", "a pin of the board", [ref]))
            info.status = "fail"
            continue
        if cap.forbidden:
            checks.append(_check("pins", f"pin {cap.name} ({d.net})", "fail", f"{cap.name} must not be used: {cap.forbidden}", "", [ref]))
            info.status = "fail"
            continue
        by_cap.setdefault(cap.name, []).append(d)
        problems = []
        if ROLE_DIRECTION[d.role] != d.direction and ROLE_DIRECTION[d.role] != "inout":
            problems.append(f"role {d.role} is an {ROLE_DIRECTION[d.role]}put but the pin is declared {d.direction}")
        if d.role in OUT_ROLES and not cap.digital_out:
            problems.append(f"{cap.name} cannot drive an output" + (" (" + "; ".join(cap.notes) + ")" if cap.notes else ""))
        if d.role == "pwm" and not cap.pwm:
            pwm_pins = ", ".join(p.name for p in b.pins.values() if p.pwm and not p.forbidden)
            problems.append(f"{cap.name} has no PWM (PWM-capable: {pwm_pins if len(pwm_pins) < 80 else 'any output-capable GPIO'})")
        if d.role == "analog_in" and not cap.analog_in:
            problems.append(f"{cap.name} has no ADC (analog inputs: {', '.join(p.name for p in b.pins.values() if p.analog_in and not p.forbidden)[:120]})")
        if d.role == "dac" and not cap.dac:
            problems.append(f"{cap.name} has no DAC")
        if d.role == "interrupt" and not cap.interrupt:
            problems.append(f"{cap.name} cannot raise an external interrupt (on the {b.key}: {', '.join(p.name for p in b.pins.values() if p.interrupt)[:80]})")
        if d.role in ("digital_in", "interrupt") and not cap.digital_in:
            problems.append(f"{cap.name} is analog-only")
        if d.pullup and not cap.pullup:
            problems.append(f"{cap.name} has no internal pull-up (fit an external resistor)")
        if problems:
            checks.append(_check("capability", f"pin {cap.name} ({d.net}) can be {d.role}", "fail", "; ".join(problems), f"a {d.role} pin", [ref]))
            info.status = "fail"
        else:
            notes = [n for n in cap.notes if not (n.startswith("ADC2") and not (fw.wifi and d.role == "analog_in")) and not (n.startswith("drives the on-board LED") and d.direction == "out")
                     and not (n.startswith("Serial") or n.startswith("UART0") or n.startswith("I2C") or n == "analog input only")]
            if fw.wifi and d.role == "analog_in" and any(n.startswith("ADC2") for n in cap.notes):
                notes = [n for n in notes] or ["ADC2: analog reads fail while Wi-Fi is on"]
            if notes:
                checks.append(_check("capability", f"pin {cap.name} ({d.net}) can be {d.role}", "warn", f"{cap.name} ({cap.number}) as {d.role}: " + "; ".join(notes), "", [ref]))
                info.status = "warn"
            else:
                checks.append(_check("capability", f"pin {cap.name} ({d.net}) can be {d.role}", "pass", f"{cap.name} ({cap.number}) as {d.direction} {d.role}", "", [ref]))
    if b is not None:
        clashes = [(n, ds) for n, ds in by_cap.items() if len(ds) > 1]
        serial_clash = [(n, ds) for n, ds in by_cap.items() if serial_used and n in b.serial_pins and not any(d.role.startswith("serial") for d in ds)]
        if clashes or serial_clash:
            parts = [f"{n} is declared {len(ds)} times ({', '.join(d.net for d in ds)})" for n, ds in clashes]
            parts += [f"{n} ({', '.join(d.net for d in ds)}) is a Serial pin and Serial is in use" for n, ds in serial_clash]
            checks.append(_check("pins", "no pin assigned twice", "fail", "; ".join(parts), "one role per physical pin", [n for n, _ in clashes + serial_clash]))
            for i in infos:
                if i.canonical in {n for n, _ in clashes + serial_clash}:
                    i.status = "fail"
        else:
            checks.append(_check("pins", "no pin assigned twice", "pass", f"{len(by_cap)} physical pin(s), one role each" + (f"; Serial on {'/'.join(b.serial_pins)} kept free" if serial_used else "")))

    # -- the sources use the pinout exactly as declared
    if not fw.sources:
        checks.append(_check("usage", "sources", "fail", "the project has no source files", "fw.source('sketch.ino', code)"))
    elif fw.main_sketch is None:
        checks.append(_check("usage", "sources", "fail", f"no .ino file among {[s.name for s in fw.sources]}: arduino-cli needs a main sketch", "one .ino file"))
    by_symbol = {d.symbol: d for d in fw.pins}
    calls = scan_calls(fw)
    used: dict[int, list[dict]] = {}
    undeclared, unresolved = [], []
    for c in calls:
        d, cap, understood = _resolve_arg(c["arg"], fw, b, by_symbol)
        where = f"{c['file']}:{c['line']}"
        if not understood:
            unresolved.append(f"{c['fn']}({c['arg']}) at {where}")
            continue
        if d is None:
            undeclared.append(f"{c['fn']}({c['arg']}" + (f" = {cap.name}" if cap else "") + f") at {where}")
            continue
        used.setdefault(id(d), []).append({**c, "where": where})
    for c in calls:
        pass
    for d, info in zip(fw.pins, infos):
        uses = used.get(id(d), [])
        info.uses = [f"{u['fn']}" + (f"({u['mode']})" if u.get("mode") and u["fn"] == "pinMode" else "") + f" @ {u['where']}" for u in uses]
        ref = info.canonical or d.pin
        mentioned = any(re.search(rf"\b{re.escape(d.symbol)}\b", strip_comments(s.code)) for s in fw.sources)
        if not uses and not mentioned:
            checks.append(_check("usage", f"{d.symbol} used as declared", "fail", f"{d.pin} ({d.net}, {d.role}) is declared but the sources never use it", f"the sketch uses {d.symbol}", [ref]))
            info.status = "fail"
            continue
        fns = {u["fn"] for u in uses}
        modes = {u["mode"] for u in uses if u["fn"] == "pinMode"}
        bad = []
        if d.direction == "out" and modes & {"INPUT", "INPUT_PULLUP", "INPUT_PULLDOWN"}:
            bad.append(f"pinMode(INPUT) on a pin declared out")
        if d.direction == "in" and "OUTPUT" in modes:
            bad.append("pinMode(OUTPUT) on a pin declared in")
        if d.direction == "in" and fns & {"digitalWrite", "analogWrite", "tone", "dacWrite"}:
            bad.append(f"{', '.join(sorted(fns & {'digitalWrite', 'analogWrite', 'tone', 'dacWrite'}))} on a pin declared in")
        if "analogWrite" in fns and d.role not in ("pwm", "dac"):
            bad.append(f"analogWrite on a pin declared {d.role} (declare it pwm)")
        if fns & {"ledcAttachPin", "ledcAttach"} and d.role != "pwm":
            bad.append(f"LEDC PWM on a pin declared {d.role} (declare it pwm)")
        if "analogRead" in fns and d.role != "analog_in":
            bad.append(f"analogRead on a pin declared {d.role} (declare it analog_in)")
        if "attach" in fns and d.role != "servo":
            bad.append(f"servo .attach on a pin declared {d.role} (declare it servo)")
        if "attachInterrupt" in fns and d.role != "interrupt":
            bad.append(f"attachInterrupt on a pin declared {d.role} (declare it interrupt)")
        missing = []
        if d.role == "servo" and "attach" not in fns:
            missing.append("no servo .attach(" + d.symbol + ")")
        if d.role == "digital_out" and "OUTPUT" not in modes:
            missing.append(f"no pinMode({d.symbol}, OUTPUT) (an output left as an input floats)")
        if d.role == "pwm" and not fns & {"analogWrite", "ledcAttachPin", "ledcAttach"}:
            missing.append("no analogWrite / LEDC on it")
        if d.role == "analog_in" and "analogRead" not in fns:
            missing.append("no analogRead on it")
        if d.role == "interrupt" and "attachInterrupt" not in fns:
            missing.append("no attachInterrupt on it")
        if d.role == "digital_in" and not fns & {"digitalRead", "attachInterrupt"}:
            missing.append("no digitalRead on it")
        if d.pullup and d.direction == "in" and modes and "INPUT_PULLUP" not in modes:
            missing.append(f"declared with a pull-up but pinMode is {', '.join(sorted(m for m in modes if m))}")
        if bad or missing:
            checks.append(_check("usage", f"{d.symbol} used as declared", "fail", "; ".join(bad + missing) + (f" (uses: {', '.join(info.uses[:4])})" if info.uses else ""),
                                 f"{d.direction} {d.role} as declared", [ref]))
            info.status = "fail"
        else:
            checks.append(_check("usage", f"{d.symbol} used as declared", "pass", ", ".join(info.uses[:4]) or f"{d.symbol} referenced", "", [ref]))
    if undeclared:
        checks.append(_check("usage", "no undeclared pins", "fail", "the sources use pins the pinout does not declare: " + "; ".join(undeclared[:6]),
                             "declare every pin in the pinout and use its PIN_ constant", []))
    elif calls:
        checks.append(_check("usage", "no undeclared pins", "pass", f"{len(calls) - len(unresolved)} pin call(s) resolved to declared pins"))
    if unresolved:
        checks.append(_check("usage", "pin calls resolved", "warn", "not followed (a pin held in another variable): " + "; ".join(unresolved[:6]), "PIN_<NET> constants from eitri_pins.h", []))

    checks += _power_budget(fw)

    # -- compile
    comp: dict = {}
    if compiled is not None:
        comp = {k: v for k, v in compiled.__dict__.items() if k != "command"}
        if not compiled.available:
            checks.append(_check("compile", "compiles (arduino-cli)", "fail", compiled.message, "a headless arduino-cli compile for the board"))
        elif compiled.ok:
            size = (f"; flash {compiled.flash_bytes} bytes ({compiled.flash_pct}%)" if compiled.flash_bytes is not None else "") + \
                   (f", RAM {compiled.ram_bytes} bytes ({compiled.ram_pct}%)" if compiled.ram_bytes is not None else "")
            sev = "warn" if compiled.ram_pct is not None and compiled.ram_pct >= 90 else "pass"
            checks.append(_check("compile", "compiles (arduino-cli)", sev, f"compiled for {b.fqbn if b else '?'} in {compiled.seconds:g} s" + (" (cached)" if compiled.cached else "") + size
                                 + ("; RAM above 90% leaves little for the stack" if sev == "warn" else ""), "", [fw.main_sketch.name] if fw.main_sketch else []))
            own = [w for w in compiled.warnings if any(w["file"] == s.name for s in fw.sources)]
            if own:
                checks.append(_check("compile", "compiler warnings in the sources", "warn", "; ".join(f"{w['file']}:{w['line']}: {w['msg']}" for w in own[:5]), "no warnings", sorted({w["file"] for w in own})))
        else:
            errs = compiled.errors or [{"file": "", "line": 0, "msg": compiled.message}]
            checks.append(_check("compile", "compiles (arduino-cli)", "fail", f"{len(errs)} error(s): " + "; ".join((f"{e['file']}:{e['line']}: " if e.get("file") else "") + e["msg"] for e in errs[:5]),
                                 "no compile errors", sorted({e["file"] for e in errs if e.get("file")})))
    return checks, infos, comp


def _power_budget(fw: Firmware) -> list[Check]:
    """What the peripherals declared on a board supply pin (servo(power="5V", current_A=...), load(...)) draw, against what the pin can
    supply when the board is powered the way fw.powered says (limits and their sources: eitri.power)."""
    out: list[Check] = []
    by_pin: dict[str, list] = {}
    for per in fw.peripherals:
        pw = per.params.get("power")
        if pw in ("5V", "3V3"):
            by_pin.setdefault(pw, []).append(per)
    ext = [p for p in fw.peripherals if p.params.get("power") == "external"]
    if ext:
        out.append(_check("power", "loads on their own supply", "pass",
                          ", ".join(p.name + (f" ({p.params['current_A']:g} A)" if p.params.get("current_A") is not None else "") for p in ext)
                          + ": not on the board's supply pins; their supply's ground must be wired to the board's GND (a shared ground)",
                          "a common ground with the board", [p.name for p in ext]))
    for pin, pers in by_pin.items():
        names = [p.name for p in pers]
        how = "USB" if fw.powered == "usb" else (f"VIN {fw.vin_V:g} V" if fw.vin_V is not None else "VIN")
        name = f"supply {pin} pin ({fw.board_key}, on {how})"
        unknown = [p.name for p in pers if p.params.get("current_A") is None]
        if unknown:
            out.append(_check("power", name, "warn", f"{', '.join(unknown)} on the {pin} pin with no current_A: not graded",
                              "current_A: the worst-case draw from the datasheet (a servo's stall current)", names))
            continue
        draw = sum(p.params["current_A"] for p in pers)
        lim = _power.supply_limit(fw.board_key, pin, fw.powered, fw.vin_V)
        src = f" [source: {lim.source}]" if lim.source else ""
        parts = ", ".join(f"{p.name} {p.params['current_A']:g} A" for p in pers)
        if lim.limit_A is None:
            out.append(_check("power", name, "warn", f"{draw:g} A drawn ({parts}); not graded: {lim.basis}{src}",
                              "the board's supply limit, or power=\"external\" with a shared ground", names))
            continue
        sev = _power.grade(draw, lim)
        if sev == "fail":
            out.append(_check("power", name, "fail",
                              f"{draw:g} A drawn ({parts}), but the {pin} pin can supply {lim.limit_A:g} A, limited by {lim.basis}. "
                              f"Fix: {_power.advice(pin)}; declare it power=\"external\".{src}", f"<= {lim.limit_A:g} A", names))
        else:
            out.append(_check("power", name, sev, f"{draw:g} A of {lim.limit_A:g} A ({parts}), limited by {lim.basis}"
                              + ("; the board's own draw shares this budget" if sev == "warn" else "") + src,
                              f"<= {lim.limit_A:g} A" + (" (within 20% of the limit)" if sev == "warn" else ""), names))
    return out
