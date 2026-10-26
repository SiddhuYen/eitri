"""The pneumatic circuit model: a textual circuit of ISO 1219 component classes joined by tubes at named ports.

    from eitri.pneumatics import *

    PARAMS = dict(p_bar=6.0, bore_mm=32, stroke_mm=100)

    def build(p):
        c = Circuit("Clamp")
        air = c.supply("AIR", pressure_bar=7.0, flow_l_min=600)
        frl = c.frl("FRL", set_bar=p.p_bar, C=2.5, b=0.35)
        v = c.valve("V1", "5/2", actuation="solenoid", C=1.2, b=0.3)
        cyl = c.cylinder("C1", bore_mm=p.bore_mm, rod_mm=12, stroke_mm=p.stroke_mm, load_N=350, speed_mm_s=200, cycles_per_min=20)
        c.tube("T1", air["OUT"], frl["IN"], id_mm=8, length_m=2.0)
        c.tube("T2", frl["OUT"], v["P"], id_mm=6, length_m=1.0)
        c.tube("T3", v["A"], cyl["CAP"], id_mm=6, length_m=1.5)
        c.tube("T4", v["B"], cyl["ROD"], id_mm=6, length_m=1.5)
        c.silencer(v["R"]); c.silencer(v["S"])
        return c

Components have named ports (a valve's P, A, B, R, S by ISO 5599 convention; a cylinder's CAP and ROD; IN / OUT; a node's N).
A tube joins two ports; a port takes one tube, a Node any number (a tee). Flow data per component is the ISO 6358 pair:
C, the sonic conductance in L/(s·bar) ANR, and b, the critical pressure ratio; a component without them gets a conservative
estimate and the checks say so. Pressures are gauge bar unless named _abs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

P_ATM = 1.01325           # bar, standard atmosphere (ISO 6358 uses ANR: 1.01325 bar abs, 20 C, 65 % RH)
VALVE_CLASSES = {
    # ways/positions: ports, and the passages of each position as (from, to) pairs (both directions flow)
    "2/2": {"ports": ["P", "A"], "positions": [[], [("P", "A")]]},
    "3/2": {"ports": ["P", "A", "R"], "positions": [[("A", "R")], [("P", "A")]]},
    "4/2": {"ports": ["P", "A", "B", "R"], "positions": [[("P", "A"), ("B", "R")], [("P", "B"), ("A", "R")]]},
    "5/2": {"ports": ["P", "A", "B", "R", "S"], "positions": [[("P", "A"), ("B", "S")], [("P", "B"), ("A", "R")]]},
    "5/3": {"ports": ["P", "A", "B", "R", "S"], "positions": [[("P", "A"), ("B", "S")], [], [("P", "B"), ("A", "R")]]},
}
EXHAUST_PORTS = ("R", "S")

# Maximum working pressure by kind, when a component gives no max_bar of its own (fetched 2026-10-26; vendor datasheets as listed
# by distributors). The supply is not rated: it is the source.
PRESSURE_RATINGS = {
    "regulator": (16.0, "Festo LR-D-MINI regulator: operating (inlet) pressure 1-16 bar"),
    "frl": (10.0, "Festo MS4-LFR filter regulators: variants listed at 0.8-14, 1-10 and 2-12 bar; the lowest maximum is used"),
    "valve": (7.0, "SMC SY3000 5-port solenoid valve: 0.1-0.7 MPa (internal pilot)"),
    "flow_control": (10.0, "SMC AS speed controllers: maximum operating pressure 1 MPa"),
    "cylinder": (10.0, "Festo DSNU round cylinders (ISO 6432): maximum operating pressure 10 bar"),
    "silencer": (10.0, "Festo U silencers: operating pressure 0-10 bar"),
    "node": (10.0, "SMC KQ2 one-touch fittings (union tee KQ2T): -100 kPa to 1 MPa"),
}


def pressure_rating(comp) -> tuple[float | None, str]:
    """(maximum working pressure in bar, where it comes from): the component's max_bar, else the default for its kind."""
    if comp.max_bar is not None:
        return comp.max_bar, "as given"
    bar, src = PRESSURE_RATINGS.get(comp.kind, (None, ""))
    return bar, (f"default: {src}" if bar is not None else "not rated")


class PneumaticsError(ValueError):
    """The circuit is described wrongly (not a sizing problem: those are reported by the checks)."""


@dataclass(frozen=True)
class PortRef:
    component: str
    port: str

    def __str__(self) -> str:
        return f"{self.component}.{self.port}"


def _pos(v, what: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise PneumaticsError(f"{what} must be a number, got {v!r}") from None
    if x <= 0:
        raise PneumaticsError(f"{what} must be positive, got {x:g}")
    return x


@dataclass(eq=False)
class Component:
    name: str
    kind: str = "component"
    ports: list[str] = field(default_factory=list)
    part_number: str = ""
    notes: str = ""
    C: float | None = None               # ISO 6358 sonic conductance, L/(s·bar) ANR
    b: float | None = None               # ISO 6358 critical pressure ratio
    max_bar: float | None = None         # maximum working pressure from the part's datasheet (default: PRESSURE_RATINGS by kind)

    def __post_init__(self) -> None:
        if not self.name or any(ch in str(self.name) for ch in ". \t\n"):
            raise PneumaticsError(f"component name {self.name!r} must be non-empty and contain no dots or spaces")
        self.name = str(self.name)
        if self.C is not None:
            self.C = _pos(self.C, f"{self.name} C")
        if self.b is not None:
            self.b = float(self.b)
            if not 0 <= self.b < 1:
                raise PneumaticsError(f"{self.name}: b must be in [0, 1), got {self.b:g}")
        if self.max_bar is not None:
            self.max_bar = _pos(self.max_bar, f"{self.name} max_bar")

    def port(self, name: str) -> PortRef:
        name = str(name).upper()
        if name not in self.ports:
            raise PneumaticsError(f"{self.name} has no port {name!r}; its ports are {self.ports}")
        return PortRef(self.name, name)

    __getitem__ = port

    @property
    def has_flow_data(self) -> bool:
        return self.C is not None and self.b is not None

    def passages(self, position: int | None = None) -> list[tuple[str, str]]:
        """Which ports a flow may pass between inside the component (both directions)."""
        return []


@dataclass(eq=False)
class Supply(Component):
    pressure_bar: float = 6.0            # gauge, at the source
    flow_l_min: float | None = None      # the source's capacity, L/min ANR (None: not graded)

    def __post_init__(self) -> None:
        self.kind, self.ports = "supply", ["OUT"]
        super().__post_init__()
        self.pressure_bar = _pos(self.pressure_bar, f"{self.name} pressure_bar")
        if self.flow_l_min is not None:
            self.flow_l_min = _pos(self.flow_l_min, f"{self.name} flow_l_min")


@dataclass(eq=False)
class Regulator(Component):
    """A pressure regulator; `frl=True` draws it as a filter-regulator(-lubricator) unit."""
    set_bar: float = 6.0
    frl: bool = False
    lubricator: bool = False

    def __post_init__(self) -> None:
        self.kind, self.ports = ("frl" if self.frl else "regulator"), ["IN", "OUT"]
        super().__post_init__()
        self.set_bar = _pos(self.set_bar, f"{self.name} set_bar")

    def passages(self, position=None):
        return [("IN", "OUT")]


@dataclass(eq=False)
class Valve(Component):
    cls: str = "5/2"
    actuation: str = "solenoid"          # solenoid | manual | pilot | lever | pedal | roller
    returns: str = "spring"              # spring | solenoid | detent
    normally: str = "closed"             # 3/2 and 2/2: closed | open (which position is the rest position)
    center: str = "closed"               # 5/3: closed | exhaust | pressure (the middle position)

    def __post_init__(self) -> None:
        if self.cls not in VALVE_CLASSES:
            raise PneumaticsError(f"valve {self.name}: class {self.cls!r} is not one of {list(VALVE_CLASSES)}")
        self.kind, self.ports = "valve", list(VALVE_CLASSES[self.cls]["ports"])
        super().__post_init__()
        if self.normally not in ("closed", "open") or self.center not in ("closed", "exhaust", "pressure"):
            raise PneumaticsError(f"valve {self.name}: normally must be closed|open and center closed|exhaust|pressure")

    @property
    def positions(self) -> list[list[tuple[str, str]]]:
        pos = [list(p) for p in VALVE_CLASSES[self.cls]["positions"]]
        if self.cls == "5/3":
            pos[1] = [] if self.center == "closed" else [("A", "R"), ("B", "S")] if self.center == "exhaust" else [("P", "A"), ("P", "B")]
        if self.cls in ("3/2", "2/2") and self.normally == "open":
            pos = list(reversed(pos))
        return pos

    @property
    def rest_position(self) -> int:
        return 1 if self.cls == "5/3" else 0

    def passages(self, position: int | None = None):
        if position is None:
            return [pair for p in self.positions for pair in p]
        return self.positions[position]


@dataclass(eq=False)
class Cylinder(Component):
    bore_mm: float = 32.0
    rod_mm: float = 12.0
    stroke_mm: float = 100.0
    acting: str = "double"               # double | single (spring return, one port: CAP)
    load_N: float = 0.0                  # the force the extend stroke must deliver
    load_retract_N: float | None = None  # the retract stroke (default: the same, or 0 for a single-acting spring return)
    speed_mm_s: float | None = None      # required extend speed
    speed_retract_mm_s: float | None = None
    cycles_per_min: float = 0.0
    spring_N: float | None = None        # single-acting: the return spring's force at full extension
    friction_fraction: float = 0.10      # ESTIMATE: seal friction and losses as a fraction of the theoretical force (10 % is the usual sizing allowance)

    def __post_init__(self) -> None:
        self.kind, self.ports = "cylinder", (["CAP", "ROD"] if self.acting == "double" else ["CAP"])
        super().__post_init__()
        if self.acting not in ("double", "single"):
            raise PneumaticsError(f"cylinder {self.name}: acting must be double or single")
        self.bore_mm, self.rod_mm, self.stroke_mm = _pos(self.bore_mm, "bore_mm"), _pos(self.rod_mm, "rod_mm"), _pos(self.stroke_mm, "stroke_mm")
        if self.rod_mm >= self.bore_mm:
            raise PneumaticsError(f"cylinder {self.name}: rod_mm must be smaller than bore_mm")
        if float(self.load_N) < 0 or float(self.cycles_per_min) < 0 or not 0 <= float(self.friction_fraction) < 1:
            raise PneumaticsError(f"cylinder {self.name}: load_N and cycles_per_min must not be negative, friction_fraction in [0, 1)")
        self.load_N, self.cycles_per_min = float(self.load_N), float(self.cycles_per_min)
        if self.load_retract_N is None:
            self.load_retract_N = 0.0 if self.acting == "single" else self.load_N
        self.load_retract_N = float(self.load_retract_N)
        for k in ("speed_mm_s", "speed_retract_mm_s", "spring_N"):
            if getattr(self, k) is not None:
                setattr(self, k, _pos(getattr(self, k), f"{self.name} {k}"))

    @property
    def area_bore_mm2(self) -> float:
        return math.pi / 4 * self.bore_mm ** 2

    @property
    def area_rod_mm2(self) -> float:
        return math.pi / 4 * self.rod_mm ** 2

    @property
    def area_annulus_mm2(self) -> float:
        return self.area_bore_mm2 - self.area_rod_mm2


@dataclass(eq=False)
class FlowControl(Component):
    """A throttle-check (speed controller) inline: ports A (toward the valve) and B (toward the cylinder). meter="out" throttles
    the flow leaving the cylinder (B -> A) and passes A -> B freely; meter="in" the opposite; "both" is a plain throttle."""
    meter: str = "out"

    def __post_init__(self) -> None:
        self.kind, self.ports = "flow_control", ["A", "B"]
        super().__post_init__()
        if self.meter not in ("out", "in", "both"):
            raise PneumaticsError(f"flow control {self.name}: meter must be out, in or both")

    def passages(self, position=None):
        return [("A", "B")]

    def throttles(self, from_port: str, to_port: str) -> bool:
        return self.meter == "both" or (self.meter == "out" and from_port == "B") or (self.meter == "in" and from_port == "A")


@dataclass(eq=False)
class Silencer(Component):
    def __post_init__(self) -> None:
        self.kind, self.ports = "silencer", ["IN"]
        super().__post_init__()


@dataclass(eq=False)
class Node(Component):
    """A junction (a tee or a manifold): any number of tubes meet at its port N."""

    def __post_init__(self) -> None:
        self.kind, self.ports = "node", ["N"]
        super().__post_init__()


@dataclass(eq=False)
class Tube:
    name: str
    a: PortRef
    b: PortRef
    id_mm: float = 6.0
    length_m: float = 1.0
    fittings: int = 2                    # push-in fittings along the run (one at each end)
    C: float | None = None               # given from a datasheet; else estimated from the bore and length
    b_ratio: float | None = None
    part_number: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.name or any(ch in str(self.name) for ch in ". \t\n"):
            raise PneumaticsError(f"tube name {self.name!r} must be non-empty and contain no dots or spaces")
        self.id_mm, self.length_m = _pos(self.id_mm, f"tube {self.name} id_mm"), _pos(self.length_m, f"tube {self.name} length_m")
        self.fittings = int(self.fittings)
        if self.fittings < 0:
            raise PneumaticsError(f"tube {self.name}: fittings must not be negative")
        if self.C is not None:
            self.C = _pos(self.C, f"tube {self.name} C")

    @property
    def area_mm2(self) -> float:
        return math.pi / 4 * self.id_mm ** 2


# --------------------------------------------------------------------------- the circuit


class Circuit:
    def __init__(self, name: str = "circuit", description: str = ""):
        self.name = name
        self.description = description
        self.components: dict[str, Component] = {}
        self.tubes: list[Tube] = []
        self.vents: set[PortRef] = set()             # ports open to atmosphere (an exhaust without a silencer)
        self.attached: dict[PortRef, PortRef] = {}   # a silencer screwed straight onto an exhaust port: port -> silencer.IN
        self.layout: dict[str, list[float]] = {}

    # -- building ----------------------------------------------------------
    def add(self, comp: Component) -> Component:
        if comp.name in self.components:
            raise PneumaticsError(f"component {comp.name!r} is already defined")
        self.components[comp.name] = comp
        return comp

    def supply(self, name: str, pressure_bar: float, flow_l_min: float | None = None, **kw) -> Supply:
        return self.add(Supply(name, pressure_bar=pressure_bar, flow_l_min=flow_l_min, **kw))

    def regulator(self, name: str, set_bar: float, C: float | None = None, b: float | None = None, **kw) -> Regulator:
        return self.add(Regulator(name, set_bar=set_bar, C=C, b=b, **kw))

    def frl(self, name: str, set_bar: float, C: float | None = None, b: float | None = None, lubricator: bool = False, **kw) -> Regulator:
        return self.add(Regulator(name, set_bar=set_bar, C=C, b=b, frl=True, lubricator=lubricator, **kw))

    def valve(self, name: str, cls: str = "5/2", actuation: str = "solenoid", returns: str = "spring", C: float | None = None, b: float | None = None, **kw) -> Valve:
        return self.add(Valve(name, cls=cls, actuation=actuation, returns=returns, C=C, b=b, **kw))

    def cylinder(self, name: str, bore_mm: float, rod_mm: float, stroke_mm: float, acting: str = "double", load_N: float = 0.0, **kw) -> Cylinder:
        return self.add(Cylinder(name, bore_mm=bore_mm, rod_mm=rod_mm, stroke_mm=stroke_mm, acting=acting, load_N=load_N, **kw))

    def flow_control(self, name: str, meter: str = "out", C: float | None = None, b: float | None = None, **kw) -> FlowControl:
        return self.add(FlowControl(name, meter=meter, C=C, b=b, **kw))

    def node(self, name: str, **kw) -> Node:
        return self.add(Node(name, **kw))

    def silencer(self, port, name: str | None = None, C: float | None = None, b: float | None = None, **kw) -> Silencer:
        """A silencer screwed onto an exhaust port (no tube)."""
        ref = self._port(port)
        s = self.add(Silencer(name or f"S_{ref.component}_{ref.port}", C=C, b=b, **kw))
        if ref in self.attached or ref in self.vents or self._tubes_at(ref):
            raise PneumaticsError(f"{ref} already has something on it")
        self.attached[ref] = s.port("IN")
        return s

    def exhaust(self, port) -> PortRef:
        """An exhaust port left open to atmosphere (no silencer)."""
        ref = self._port(port)
        if ref in self.attached or self._tubes_at(ref):
            raise PneumaticsError(f"{ref} already has something on it")
        self.vents.add(ref)
        return ref

    def tube(self, name: str, a, b, id_mm: float = 6.0, length_m: float = 1.0, **kw) -> Tube:
        if any(t.name == name for t in self.tubes):
            raise PneumaticsError(f"tube {name!r} is already defined")
        ra, rb = self._port(a), self._port(b)
        if ra == rb:
            raise PneumaticsError(f"tube {name}: both ends are {ra}")
        for r in (ra, rb):
            if self.components[r.component].kind != "node" and (self._tubes_at(r) or r in self.attached or r in self.vents):
                raise PneumaticsError(f"tube {name}: port {r} already has a connection (use a Node for a tee)")
        t = Tube(name, ra, rb, id_mm, length_m, **kw)
        self.tubes.append(t)
        return t

    def _port(self, x) -> PortRef:
        if isinstance(x, PortRef):
            ref = x
        elif isinstance(x, Component):
            if len(x.ports) != 1:
                raise PneumaticsError(f"{x.name} has ports {x.ports}; say which: {x.name}['PORT']")
            ref = x.port(x.ports[0])
        elif isinstance(x, str) and "." in x:
            comp, _, port = x.partition(".")
            if comp not in self.components:
                raise PneumaticsError(f"no component {comp!r} (in {x!r}); components: {list(self.components)}")
            ref = self.components[comp].port(port)
        elif isinstance(x, str) and x in self.components:
            return self._port(self.components[x])
        else:
            raise PneumaticsError(f"cannot connect to {x!r}; use component['PORT'] or 'NAME.PORT'")
        if ref.component not in self.components:
            raise PneumaticsError(f"component {ref.component!r} is not in the circuit; add it first")
        if ref.port not in self.components[ref.component].ports:
            raise PneumaticsError(f"{ref.component} has no port {ref.port!r}")
        return ref

    def _tubes_at(self, ref: PortRef) -> list[Tube]:
        return [t for t in self.tubes if t.a == ref or t.b == ref]

    def place(self, name: str, x: float, y: float) -> None:
        self.layout[name] = [float(x), float(y)]

    def apply_layout(self, layout) -> None:
        for k, v in (layout or {}).items():
            try:
                self.layout[str(k)] = [float(v[0]), float(v[1])]
            except (TypeError, ValueError, IndexError):
                continue

    # -- queries -------------------------------------------------------------
    def get(self, name: str) -> Component:
        if name not in self.components:
            raise PneumaticsError(f"no component {name!r}; components: {list(self.components)}")
        return self.components[name]

    def get_tube(self, name: str) -> Tube:
        for t in self.tubes:
            if t.name == name:
                return t
        raise PneumaticsError(f"no tube {name!r}; tubes: {[t.name for t in self.tubes]}")

    def of_kind(self, kind: str) -> list[Component]:
        return [c for c in self.components.values() if c.kind == kind]

    @property
    def cylinders(self) -> list[Cylinder]:
        return [c for c in self.components.values() if isinstance(c, Cylinder)]

    @property
    def supplies(self) -> list[Supply]:
        return [c for c in self.components.values() if isinstance(c, Supply)]

    def connections_at(self, ref: PortRef) -> list[tuple[str, Any, PortRef]]:
        """What a port is joined to outside its component: ("tube", Tube, other port) and ("attached", None, silencer port)."""
        out: list[tuple[str, Any, PortRef]] = []
        for t in self.tubes:
            if t.a == ref:
                out.append(("tube", t, t.b))
            elif t.b == ref:
                out.append(("tube", t, t.a))
        if ref in self.attached:
            out.append(("attached", None, self.attached[ref]))
        for port, s in self.attached.items():
            if s == ref:
                out.append(("attached", None, port))
        return out

    def __repr__(self) -> str:
        return f"Circuit({self.name!r}: {len(self.components)} components, {len(self.tubes)} tubes)"


def is_circuit(obj: Any) -> bool:
    return isinstance(obj, Circuit)
