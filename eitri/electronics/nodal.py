"""A general DC solver for the parts of a circuit that are not a plain series string: modified nodal analysis (MNA) with an
active-set iteration for the piecewise elements. Written for eitri from the textbook method; no code or data from any other
circuit simulator.

The element models are the ones the closed-form series solver uses (eitri.electronics.analysis), so the two agree on a series
string:

    R       a resistance (a resistor, a relay coil)
    WIRE    no resistance (a closed switch, energized relay contacts)
    DIODE   an ideal diode with a fixed forward voltage: on (V_AK = vf, I >= 0) or off (I = 0, V_AK <= vf). LEDs and diodes.
    LIMIT   a load that takes its rated current (a motor, a solenoid): a short while it carries less than that, else a current
            source of its rating in the direction of the drive (|I| <= rating, and V * I >= 0)

    solve(elements, fixed, nodes)  -> Solution (node voltages, element currents) or None when no consistent state exists

Fixed nets (rails, grounds, driven pins at one of their levels) are known voltages. A path of WIRE and forward DIODE elements
between two fixed voltages that leaves drive voltage over is an unlimited current: those elements are reported as INF (the
build fails them) and the rest of the network is solved without them.

The active-set iteration starts with every diode off and every load as a short, then repeatedly turns on the diode with the most
forward voltage over its vf (one at a time, so the lowest-vf diode of a parallel group clamps the node first), turns off any
diode carrying reverse current, and moves loads between short and limit. Should it not settle, every state combination is
tried (up to 2^12). A tiny leakage to 0 V (GMIN, 1e-12 S) on every free node keeps a node that only off diodes touch at a
defined voltage; it changes no current by more than nanoamps.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

INF = float("inf")
GMIN = 1e-12                      # S, from every free node to 0 V
EPS_V = 1e-9                      # V: tolerance on a piecewise element's voltage condition
EPS_I = 1e-9                      # A: tolerance on a current condition; smaller currents are reported as 0
MAX_ITER = 200
MAX_ENUM = 12                     # piecewise elements; beyond 3^n / 2^n combinations is not tried


@dataclass
class Elem:
    id: str                       # a key the caller uses to read the result (a part name, "K1.coil")
    kind: str                     # R | WIRE | DIODE | LIMIT
    a: str                        # node names: current is positive from a to b (DIODE: a = anode)
    b: str
    value: float = 0.0            # R: ohms; DIODE: vf; LIMIT: the rated current


@dataclass
class Solution:
    v: dict[str, float]                               # every node's voltage (fixed ones too)
    i: dict[str, float]                               # every element's current, a -> b (INF: unlimited)
    states: dict[str, str] = field(default_factory=dict)


def _unlimited_paths(elems: list[Elem], fixed: dict[str, float]) -> set[str]:
    """Elements on a path of WIREs and forward DIODEs from a fixed node to another fixed node at a lower voltage, with voltage left
    over after the diodes' vf: nothing limits the current along it."""
    adj: dict[str, list[tuple[Elem, str, float]]] = {}
    for e in elems:
        if e.kind == "WIRE":
            adj.setdefault(e.a, []).append((e, e.b, 0.0))
            adj.setdefault(e.b, []).append((e, e.a, 0.0))
        elif e.kind == "DIODE":
            adj.setdefault(e.a, []).append((e, e.b, e.value))            # forward only
    out: set[str] = set()

    def walk(start: float, node: str, drop: float, path: list[Elem], seen: set[str]) -> None:
        for e, nxt, vf in adj.get(node, ()):
            if nxt in seen:
                continue
            if nxt in fixed:
                if start - drop - vf - fixed[nxt] > EPS_V:
                    out.update(x.id for x in path + [e])
                continue
            walk(start, nxt, drop + vf, path + [e], seen | {nxt})

    for n, v in fixed.items():
        walk(v, n, 0.0, [], {n})
    return out


def _linear(elems: list[Elem], fixed: dict[str, float], free: list[str], states: dict[str, str]) -> tuple[dict, dict] | None:
    """Solve with every piecewise element in a given state; None if that state is inconsistent (a loop of fixed voltages)."""
    idx = {n: k for k, n in enumerate(free)}
    vsrc = [e for e in elems if e.kind == "WIRE" or (e.kind == "DIODE" and states[e.id] == "on") or (e.kind == "LIMIT" and states[e.id] == "short")]
    nv, m = len(free), len(vsrc)
    A = np.zeros((nv + m, nv + m))
    z = np.zeros(nv + m)
    for k in range(nv):
        A[k, k] += GMIN

    def node(n):                    # (row/col index or None, known voltage)
        return (idx[n], 0.0) if n in idx else (None, fixed[n])

    for e in elems:
        (ia, va), (ib, vb) = node(e.a), node(e.b)
        if e.kind == "R":
            g = 1.0 / e.value
            for i_, j_, sgn in ((ia, ia, 1), (ib, ib, 1), (ia, ib, -1), (ib, ia, -1)):
                if i_ is not None and j_ is not None:
                    A[i_, j_] += sgn * g
            if ia is not None and ib is None:
                z[ia] += g * vb
            if ib is not None and ia is None:
                z[ib] += g * va
        elif e.kind == "LIMIT" and states[e.id] in ("src+", "src-"):
            cur = e.value if states[e.id] == "src+" else -e.value          # leaves a, enters b
            if ia is not None:
                z[ia] -= cur
            if ib is not None:
                z[ib] += cur
    for k, e in enumerate(vsrc):
        row = nv + k
        (ia, va), (ib, vb) = node(e.a), node(e.b)
        emf = e.value if e.kind == "DIODE" else 0.0
        # V(a) - V(b) = emf; its current (a -> b) leaves a and enters b
        rhs = emf
        if ia is not None:
            A[row, ia] += 1.0
            A[ia, row] += 1.0
        else:
            rhs -= va
        if ib is not None:
            A[row, ib] -= 1.0
            A[ib, row] -= 1.0
        else:
            rhs += vb
        z[row] = rhs
    if nv + m:
        x, *_ = np.linalg.lstsq(A, z, rcond=None)
        resid = float(np.max(np.abs(A @ x - z)))
        if resid > 1e-7 * max(1.0, float(np.max(np.abs(z)))):
            return None
    else:
        x = np.zeros(0)
    v = {n: float(x[idx[n]]) for n in free}
    v.update(fixed)
    cur: dict[str, float] = {}
    for k, e in enumerate(vsrc):
        cur[e.id] = float(x[nv + k])
    for e in elems:
        if e.id in cur:
            continue
        if e.kind == "R":
            cur[e.id] = (v[e.a] - v[e.b]) / e.value
        elif e.kind == "DIODE":
            cur[e.id] = 0.0
        elif e.kind == "LIMIT":
            cur[e.id] = e.value if states[e.id] == "src+" else -e.value
    return v, cur


def _violations(elems: list[Elem], v: dict, cur: dict, states: dict) -> tuple[list[tuple[float, str, str]], list[tuple[str, str]]]:
    """(voltage-type violations (how far, element, new state): turn these on one at a time; current-type ones: fix all at once)."""
    volt, amp = [], []
    for e in elems:
        s = states.get(e.id)
        if e.kind == "DIODE":
            vd = v[e.a] - v[e.b]
            if s == "off" and vd > e.value + EPS_V:
                volt.append((vd - e.value, e.id, "on"))
            elif s == "on" and cur[e.id] < -EPS_I:
                amp.append((e.id, "off"))
        elif e.kind == "LIMIT":
            vd = v[e.a] - v[e.b]
            if s == "short" and abs(cur[e.id]) > e.value + EPS_I:
                amp.append((e.id, "src+" if cur[e.id] > 0 else "src-"))
            elif s == "src+" and vd < -EPS_V:
                volt.append((-vd, e.id, "short"))
            elif s == "src-" and vd > EPS_V:
                volt.append((vd, e.id, "short"))
    return volt, amp


def solve(elems: list[Elem], fixed: dict[str, float]) -> Solution | None:
    """Node voltages and element currents of a network whose fixed nodes are at the given voltages."""
    inf = _unlimited_paths(elems, fixed)
    live = [e for e in elems if e.id not in inf]
    nodes = sorted({n for e in elems for n in (e.a, e.b)})
    free = [n for n in nodes if n not in fixed]
    pw = [e for e in live if e.kind in ("DIODE", "LIMIT")]
    states = {e.id: ("off" if e.kind == "DIODE" else "short") for e in pw}

    def finish(v, cur, st):
        # an off diode sitting exactly at its vf (a matched twin of a conducting one) conducts too: the solve then shares the
        # current between them (the least-squares solution of parallel equal voltages splits it evenly)
        for _ in range(len(pw)):
            ties = [e.id for e in pw if e.kind == "DIODE" and st[e.id] == "off" and v[e.a] - v[e.b] >= e.value - EPS_V]
            if not ties:
                break
            trial = dict(st, **{t: "on" for t in ties})
            r = _linear(live, fixed, free, trial)
            if r is None:
                break
            volt, amp = _violations(live, r[0], r[1], trial)
            if volt or amp:
                break
            st, (v, cur) = trial, r
        out = {e.id: INF for e in elems if e.id in inf}
        for k, x in cur.items():
            out[k] = 0.0 if abs(x) < EPS_I else x
        return Solution(v, out, dict(st))

    seen: set[tuple] = set()
    for _ in range(MAX_ITER):
        key = tuple(sorted(states.items()))
        if key in seen:
            break
        seen.add(key)
        r = _linear(live, fixed, free, states)
        if r is None:
            break
        v, cur = r
        volt, amp = _violations(live, v, cur, states)
        if not volt and not amp:
            return finish(v, cur, states)
        for eid, new in amp:
            states[eid] = new
        if volt:
            _d, eid, new = max(volt)
            states[eid] = new
    # did not settle: try every combination
    if len(pw) > MAX_ENUM:
        return None
    choices = [("off", "on") if e.kind == "DIODE" else ("short", "src+", "src-") for e in pw]
    for combo in itertools.product(*choices):
        st = {e.id: s for e, s in zip(pw, combo)}
        r = _linear(live, fixed, free, st)
        if r is None:
            continue
        v, cur = r
        volt, amp = _violations(live, v, cur, st)
        if not volt and not amp:
            return finish(v, cur, st)
    return None
