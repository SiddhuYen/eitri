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

# The largest load ratio (load / theoretical force) per kind of duty: SMC Best Pneumatics technical data, "Air Cylinders Model
# Selection", Step 1 "Determine the load factor in accordance with the purpose" (and Data 1: Bore Size Selection, catalogue
# p.1570; https://ca01.smcworld.com/catalog/BEST-technical-data-en/pdf/6-2-1-m21-43-tech_en.pdf, re-read 2026-11-07): static
# operation (clamping, low-speed vise crimping) 0.7 or less; dynamic operation, vertical and horizontal movement of the load,
# 0.5 or less; horizontal movement of load on guide, 1 or less; "If it is particularly necessary to operate at high speeds,
# the load rate must be reduced further". Festo's DSBC catalogue (2023/08, p.18) likewise sizes for 50 % of theoretical force.
# A cylinder's duty defaults to "moving", the strictest that applies to most designs.
LOAD_RATIOS = {"moving": 0.5, "static": 0.7, "guided": 1.0}

# Maximum working pressure by kind, when a component gives no max_bar of its own (fetched 2026-10-26; vendor datasheets as listed
# by distributors). The supply is not rated: it is the source.
PRESSURE_RATINGS = {
    "regulator": (10.0, "SMC AR20-AR60 regulators: maximum operating pressure 1.0 MPa (SMC AR_EU.pdf, standard specifications; "
                  "Festo LR-D-MINI takes 16 bar: the lower is used)"),
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
    min_bar: float | None = None         # minimum operating pressure from the part's datasheet (None: not graded)
    catalog: dict = field(default_factory=dict)   # filled from a catalogue part (part=): sources, catalogue figures, extra facts
    connections: dict = field(default_factory=dict)  # port -> its connection: "push-in 6" (a built-in fitting for 6 mm tube),
                                                     # a female thread "M5" / "Rc1/8" / "G1/8", or a male thread "R1/8 male"

    def __post_init__(self) -> None:
        if not self.name or any(ch in str(self.name) for ch in ". \t\n"):
            raise PneumaticsError(f"component name {self.name!r} must be non-empty and contain no dots or spaces")
        self.name = str(self.name)
        if self.min_bar is not None:
            self.min_bar = float(self.min_bar)
            if self.min_bar < 0:
                raise PneumaticsError(f"{self.name}: min_bar must not be negative")
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
    C_exhaust: float | None = None       # ISO 6358 C, b of the work-to-exhaust passages (A/B -> R/S) when the datasheet gives them apart
    b_exhaust: float | None = None
    coil_V: float | None = None          # solenoid coil rated voltage (DC) and power, from the datasheet: the I/O list's outputs
    coil_W: float | None = None
    connector: str = ""                  # the coil's electrical entry (plug, M8 ...)
    manifold: str = ""                   # the manifold it is mounted on (Circuit.mount) and its station
    station: int | None = None
    series: str = ""                     # its catalogue series (SY3000 ...), to match a manifold
    signal: str = ""                     # the control signal name for the coil (14 side); "" -> <name>_14
    signal_12: str = ""                  # the 12-side coil of a double-solenoid valve; "" -> <name>_12
    terminal: str = ""                   # where the coil lands (terminal / PLC output address), free text for the I/O list
    terminal_12: str = ""

    def __post_init__(self) -> None:
        if self.cls not in VALVE_CLASSES:
            raise PneumaticsError(f"valve {self.name}: class {self.cls!r} is not one of {list(VALVE_CLASSES)}")
        self.kind, self.ports = "valve", list(VALVE_CLASSES[self.cls]["ports"])
        super().__post_init__()
        if self.normally not in ("closed", "open") or self.center not in ("closed", "exhaust", "pressure"):
            raise PneumaticsError(f"valve {self.name}: normally must be closed|open and center closed|exhaust|pressure")
        if self.C_exhaust is not None:
            self.C_exhaust = _pos(self.C_exhaust, f"{self.name} C_exhaust")
        if self.b_exhaust is not None and not 0 <= float(self.b_exhaust) < 1:
            raise PneumaticsError(f"{self.name}: b_exhaust must be in [0, 1)")
        for k in ("coil_V", "coil_W"):
            if getattr(self, k) is not None:
                setattr(self, k, _pos(getattr(self, k), f"{self.name} {k}"))

    @property
    def coils(self) -> list[tuple[str, str, str]]:
        """The solenoid coils as (ISO 5599 pilot number, signal name, terminal): 14 drives position 2; 12 is the return side of a
        double-solenoid valve (or a 5/3's other side)."""
        if self.actuation != "solenoid":
            return []
        out = [("14", self.signal or f"{self.name}_14", self.terminal)]
        if self.returns == "solenoid" or self.cls == "5/3":
            out.append(("12", self.signal_12 or f"{self.name}_12", self.terminal_12))
        return out

    def flow_data(self, from_port: str, to_port: str) -> tuple[float, float] | None:
        """(C, b) for a passage: the exhaust figures for a work port to an exhaust port when given, else the valve's C, b."""
        if to_port in EXHAUST_PORTS and self.C_exhaust is not None:
            return self.C_exhaust, (self.b_exhaust if self.b_exhaust is not None else (self.b if self.b is not None else 0.0))
        if self.has_flow_data:
            return self.C, self.b
        return None

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
    duty: str = "moving"                 # what the load does, for the load-ratio limit (LOAD_RATIOS): moving | static | guided
    load_mass_kg: float | None = None    # the mass the rod moves (for the end-of-stroke kinetic energy against the cushioning)
    piston_mass_kg: float = 0.0          # the cylinder's own moving mass (piston + rod), from the datasheet
    allowable_ke_J: float | None = None  # the cushioning's allowable kinetic energy, from the datasheet
    speed_min_mm_s: float | None = None  # the catalogue's piston speed range
    speed_max_mm_s: float | None = None
    magnet: bool | None = None           # a magnetic piston (needed by reed / magnetic position sensors); None: not stated
    on_power_loss: str | None = None     # what it must do when the electrical power is lost: hold | extend | retract | release
    on_air_loss: str | None = None       # ... and when the air supply is lost (failsafe.py grades both against the valves)

    def __post_init__(self) -> None:
        self.kind, self.ports = "cylinder", (["CAP", "ROD"] if self.acting == "double" else ["CAP"])
        super().__post_init__()
        if self.acting not in ("double", "single"):
            raise PneumaticsError(f"cylinder {self.name}: acting must be double or single")
        self.duty = str(self.duty).strip().lower()
        if self.duty not in LOAD_RATIOS:
            raise PneumaticsError(f"cylinder {self.name}: duty must be one of {', '.join(LOAD_RATIOS)} (moving: the cylinder moves the "
                                  "load vertically or horizontally; static: clamping, low-speed pressing; guided: horizontal on a guide)")
        self.bore_mm, self.rod_mm, self.stroke_mm = _pos(self.bore_mm, "bore_mm"), _pos(self.rod_mm, "rod_mm"), _pos(self.stroke_mm, "stroke_mm")
        if self.rod_mm >= self.bore_mm:
            raise PneumaticsError(f"cylinder {self.name}: rod_mm must be smaller than bore_mm")
        if float(self.load_N) < 0 or float(self.cycles_per_min) < 0 or not 0 <= float(self.friction_fraction) < 1:
            raise PneumaticsError(f"cylinder {self.name}: load_N and cycles_per_min must not be negative, friction_fraction in [0, 1)")
        self.load_N, self.cycles_per_min = float(self.load_N), float(self.cycles_per_min)
        if self.load_retract_N is None:
            self.load_retract_N = 0.0 if self.acting == "single" else self.load_N
        self.load_retract_N = float(self.load_retract_N)
        for k in ("on_power_loss", "on_air_loss"):
            v = getattr(self, k)
            if v is not None:
                v = str(v).strip().lower()
                if v not in ("hold", "extend", "retract", "release"):
                    raise PneumaticsError(f"cylinder {self.name}: {k} must be hold, extend, retract or release, got {v!r}")
                setattr(self, k, v)
        for k in ("speed_mm_s", "speed_retract_mm_s", "spring_N", "load_mass_kg", "allowable_ke_J", "speed_min_mm_s", "speed_max_mm_s"):
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
class Manifold(Component):
    """A valve manifold base (SMC SS5Y...): common supply P and exhausts R (EA) and S (EB) shared by the valves mounted on its
    stations (Circuit.mount). Its C, b are the catalogue's figures for a valve mounted on it (they replace the valve's own)."""
    stations: int = 2
    series: str = ""                     # the valve series it takes (SY3000 ...): mount() checks it
    C_exhaust: float | None = None
    b_exhaust: float | None = None
    blanking_plate: str = ""             # the blanking plate assembly part for an empty station
    max_stations: int | None = None      # the catalogue's largest manifold; above `both_sides_above` the catalogue asks for supply and
    both_sides_above: int | None = None  # exhaust on both sides

    def __post_init__(self) -> None:
        self.kind, self.ports = "manifold", ["P", "R", "S"]
        super().__post_init__()
        self.stations = int(self.stations)
        if self.stations < 1:
            raise PneumaticsError(f"manifold {self.name}: stations must be at least 1")
        if self.C_exhaust is not None:
            self.C_exhaust = _pos(self.C_exhaust, f"{self.name} C_exhaust")


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
    max_bar: float | None = None         # the tube's maximum working pressure (graded with the components it carries air to)
    catalog: dict = field(default_factory=dict)
    od_mm: float | None = None           # outside diameter (push-in fittings are sized by it)
    direct: bool = False                 # no tube: the two ports are screwed straight together (a speed controller in a cylinder port)

    def __post_init__(self) -> None:
        if not self.name or any(ch in str(self.name) for ch in ". \t\n"):
            raise PneumaticsError(f"tube name {self.name!r} must be non-empty and contain no dots or spaces")
        if self.max_bar is not None:
            self.max_bar = _pos(self.max_bar, f"tube {self.name} max_bar")
        if self.od_mm is not None:
            self.od_mm = _pos(self.od_mm, f"tube {self.name} od_mm")
            if self.od_mm <= self.id_mm:
                raise PneumaticsError(f"tube {self.name}: od_mm must be larger than id_mm")
        self.id_mm, self.length_m = _pos(self.id_mm, f"tube {self.name} id_mm"), _pos(self.length_m, f"tube {self.name} length_m")
        self.fittings = int(self.fittings)
        if self.fittings < 0:
            raise PneumaticsError(f"tube {self.name}: fittings must not be negative")
        if self.C is not None:
            self.C = _pos(self.C, f"tube {self.name} C")

    @property
    def area_mm2(self) -> float:
        return math.pi / 4 * self.id_mm ** 2


SENSOR_POSITIONS = ("extended", "retracted")
SENSOR_OUTPUTS = ("2-wire", "PNP", "NPN", "reed")


@dataclass(eq=False)
class Sensor:
    """A cylinder position sensor (a reed or solid-state switch on the cylinder's slot): an input of the I/O list."""
    name: str
    cylinder: str
    position: str = "extended"           # extended | retracted: where on the stroke it switches
    output: str = "PNP"                  # 2-wire | PNP | NPN | reed
    V_min: float | None = None           # supply / load voltage range from the datasheet
    V_max: float | None = None
    load_mA_min: float | None = None
    load_mA_max: float | None = None
    drop_V: float | None = None
    leakage_mA: float | None = None
    connector: str = ""
    signal: str = ""                     # "" -> <cylinder>_<EXTENDED|RETRACTED>
    terminal: str = ""
    part_number: str = ""
    notes: str = ""
    fits: tuple = ()                     # cylinder series the sensor mounts on (from the catalogue)
    catalog: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or any(ch in str(self.name) for ch in ". \t\n"):
            raise PneumaticsError(f"sensor name {self.name!r} must be non-empty and contain no dots or spaces")
        self.position = str(self.position).strip().lower()
        if self.position not in SENSOR_POSITIONS:
            raise PneumaticsError(f"sensor {self.name}: position must be one of {SENSOR_POSITIONS}")
        if self.output not in SENSOR_OUTPUTS:
            raise PneumaticsError(f"sensor {self.name}: output must be one of {SENSOR_OUTPUTS}")
        if not self.signal:
            self.signal = f"{self.cylinder}_{self.position.upper()}"


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b)))
    return a == b


def _from_part(kind: str, part: str, cls, kw: dict) -> dict:
    """Fill a component's keyword arguments from a catalogue part number (eitri.pneumatics.catalog). A figure the model also gives
    must agree with the part's."""
    from . import catalog as _cat
    try:
        fields, series = _cat.lookup(kind, part)
    except _cat.CatalogError as e:
        raise PneumaticsError(str(e)) from None
    names = set(cls.__dataclass_fields__)
    extra = {}
    for k, v in fields.items():
        if k not in names:
            extra[k] = v
            continue
        if kw.get(k) is not None and not _same(kw[k], v):
            raise PneumaticsError(f"{kw.get('name', '')}: {part} has {k}={v!r} (datasheet) but the model gives {kw[k]!r}; drop the "
                                  f"argument or pick the part that matches")
        kw[k] = v
    if not kw.get("part_number"):
        kw["part_number"] = str(part).strip()
    kw["catalog"] = {"series": series.pattern, "sources": [ _cat.cite(s) for s in series.sources], "source_ids": list(series.sources),
                     "figures": dict(series.figures), "info": extra}
    return kw


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
        self.sensors: dict[str, Sensor] = {}
        self.mounts: dict[str, tuple[str, int]] = {}     # valve name -> (manifold name, station)
        self.control_V: float | None = None          # the control voltage (DC) the coils and sensors run on (the I/O list's)
        self.meta: dict[str, str] = {}               # the drawing's title block (drawing_info)

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

    def valve(self, name: str, cls: str | None = None, actuation: str | None = None, returns: str | None = None, C: float | None = None,
              b: float | None = None, part: str | None = None, **kw) -> Valve:
        kw.update(name=name, cls=cls, actuation=actuation, returns=returns, C=C, b=b)
        if part:
            kw = _from_part("valve", part, Valve, kw)
        kw = {k: v for k, v in kw.items() if v is not None}
        kw.setdefault("cls", "5/2")
        return self.add(Valve(**kw))

    def cylinder(self, name: str, bore_mm: float | None = None, rod_mm: float | None = None, stroke_mm: float | None = None,
                 acting: str | None = None, load_N: float = 0.0, part: str | None = None, **kw) -> Cylinder:
        kw.update(name=name, bore_mm=bore_mm, rod_mm=rod_mm, stroke_mm=stroke_mm, acting=acting, load_N=load_N)
        if part:
            kw = _from_part("cylinder", part, Cylinder, kw)
        missing = [k for k in ("bore_mm", "rod_mm", "stroke_mm") if kw.get(k) is None]
        if missing:
            raise PneumaticsError(f"cylinder {name}: give {', '.join(missing)} (or part= a catalogue part number)")
        return self.add(Cylinder(**{k: v for k, v in kw.items() if v is not None}))

    def flow_control(self, name: str, meter: str = "out", C: float | None = None, b: float | None = None, part: str | None = None, **kw) -> FlowControl:
        kw.update(name=name, meter=meter, C=C, b=b)
        if part:
            kw = _from_part("flow_control", part, FlowControl, kw)
        return self.add(FlowControl(**{k: v for k, v in kw.items() if v is not None}))

    def sensor(self, name: str, cylinder, position: str = "extended", part: str | None = None, **kw) -> Sensor:
        """A position sensor on a cylinder (switches at the extended or retracted end): an input of the I/O list."""
        cyl = cylinder.name if isinstance(cylinder, Component) else str(cylinder)
        if cyl not in self.components or self.components[cyl].kind != "cylinder":
            raise PneumaticsError(f"sensor {name}: {cyl!r} is not a cylinder of this circuit")
        if name in self.sensors or name in self.components:
            raise PneumaticsError(f"{name!r} is already defined")
        kw.update(name=name, cylinder=cyl, position=position)
        if part:
            kw = _from_part("sensor", part, Sensor, kw)
        s = Sensor(**{k: v for k, v in kw.items() if v is not None})
        self.sensors[name] = s
        return s

    def drawing_info(self, project: str = "", ticket: str = "", revision: str = "", title: str = "", drawn_by: str = "", approved_by: str = "") -> None:
        """The circuit drawing's title block: project, ticket id, revision, title; drawn by (default "eitri <version>"); approved by
        (left blank for the customer's signature unless given)."""
        self.meta.update({k: str(v) for k, v in dict(project=project, ticket=ticket, revision=revision, title=title, drawn_by=drawn_by,
                                                      approved_by=approved_by).items() if v})

    def control_voltage(self, volts: float) -> None:
        """The DC control voltage the valve coils and sensors are switched with (usually the PLC's 24 V)."""
        self.control_V = _pos(volts, "control voltage")

    def node(self, name: str, **kw) -> Node:
        return self.add(Node(name, **kw))

    def manifold(self, name: str, stations: int | None = None, part: str | None = None, **kw) -> Manifold:
        """A valve manifold base: ports P (common supply), R and S (common exhausts); mount valves on it with mount()."""
        kw.update(name=name, stations=stations)
        if part:
            kw = _from_part("manifold", part, Manifold, kw)
        if kw.get("stations") is None:
            raise PneumaticsError(f"manifold {name}: give stations= (or part= a catalogue part number)")
        return self.add(Manifold(**{k: v for k, v in kw.items() if v is not None}))

    def mount(self, valve, manifold, station: int | None = None) -> None:
        """Mount a valve on a manifold station: its P, R and S join the manifold's common supply and exhausts (no tubes, no
        fittings, no silencers of its own) and it takes the catalogue's flow figures for a valve on that manifold."""
        v = self.get(valve.name if isinstance(valve, Component) else str(valve))
        m = self.get(manifold.name if isinstance(manifold, Component) else str(manifold))
        if v.kind != "valve" or m.kind != "manifold":
            raise PneumaticsError(f"mount(valve, manifold): {v.name} is a {v.kind}, {m.name} a {m.kind}")
        if v.name in self.mounts:
            raise PneumaticsError(f"{v.name} is already mounted on {self.mounts[v.name][0]}")
        if m.series and v.series and m.series != v.series:
            raise PneumaticsError(f"{v.name} is an {v.series} valve; manifold {m.name} takes {m.series} valves")
        for p in ("P", "R", "S"):
            if p in v.ports and (self._tubes_at(v.port(p)) or v.port(p) in self.attached or v.port(p) in self.vents):
                raise PneumaticsError(f"{v.name}.{p} already has a connection: a mounted valve's P, R and S are the manifold's")
        used = {st for (mn, st) in self.mounts.values() if mn == m.name}
        if station is None:
            station = next(i for i in range(1, len(used) + 2) if i not in used)
        if station in used:
            raise PneumaticsError(f"manifold {m.name}: station {station} is taken")
        self.mounts[v.name] = (m.name, int(station))
        v.manifold, v.station = m.name, int(station)
        if m.C is not None:                                       # the catalogue gives the valve-on-manifold figures
            v.catalog.setdefault("info", {})["body_ported_flow"] = (v.C, v.b, v.C_exhaust, v.b_exhaust)
            v.C, v.b = m.C, m.b
            if m.C_exhaust is not None:
                v.C_exhaust, v.b_exhaust = m.C_exhaust, m.b_exhaust

    def silencer(self, port, name: str | None = None, C: float | None = None, b: float | None = None, part: str | None = None, **kw) -> Silencer:
        """A silencer screwed onto an exhaust port (no tube)."""
        ref = self._port(port)
        if ref.component in self.mounts and ref.port in ("R", "S"):
            raise PneumaticsError(f"{ref.component} is mounted on {self.mounts[ref.component][0]}: put the silencer on the manifold's {ref.port}")
        kw.update(name=name or f"S_{ref.component}_{ref.port}", C=C, b=b)
        if part:
            kw = _from_part("silencer", part, Silencer, kw)
        s = self.add(Silencer(**{k: v for k, v in kw.items() if v is not None}))
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

    def tube(self, name: str, a, b, id_mm: float | None = None, length_m: float = 1.0, part: str | None = None, **kw) -> Tube:
        if any(t.name == name for t in self.tubes):
            raise PneumaticsError(f"tube {name!r} is already defined")
        ra, rb = self._port(a), self._port(b)
        if ra == rb:
            raise PneumaticsError(f"tube {name}: both ends are {ra}")
        for r in (ra, rb):
            if r.component in self.mounts and r.port in ("P", "R", "S"):
                raise PneumaticsError(f"tube {name}: {r} is fed by manifold {self.mounts[r.component][0]}; connect the manifold's {r.port} instead")
            if self.components[r.component].kind != "node" and (self._tubes_at(r) or r in self.attached or r in self.vents):
                raise PneumaticsError(f"tube {name}: port {r} already has a connection (use a Node for a tee)")
        kw.update(id_mm=id_mm)
        if part:
            kw = _from_part("tube", part, Tube, kw)
        if kw.get("id_mm") is None:
            kw["id_mm"] = 6.0
        t = Tube(name, ra, rb, length_m=length_m, **{k: v for k, v in kw.items() if v is not None})
        self.tubes.append(t)
        return t

    def screw_in(self, a, b, name: str | None = None) -> Tube:
        """Two ports screwed straight together, with no tube (a speed controller or a fitting in a cylinder port): a joint with a
        tiny length for the flow estimate, no tube and no fitting in the BOM."""
        ra, rb = self._port(a), self._port(b)
        return self.tube(name or f"J_{ra.component}_{ra.port}", ra, rb, id_mm=4.0, length_m=0.01, fittings=0, direct=True)

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
        if ref.component in self.mounts and ref.port in ("P", "R", "S"):          # a mounted valve: into the manifold's gallery
            out.append(("manifold", None, PortRef(self.mounts[ref.component][0], ref.port)))
        comp = self.components.get(ref.component)
        if comp is not None and comp.kind == "manifold":                          # the gallery: every mounted valve's same port
            for vname, (mname, _st) in self.mounts.items():
                if mname == comp.name and ref.port in self.components[vname].ports:
                    out.append(("manifold", None, PortRef(vname, ref.port)))
        return out

    def __repr__(self) -> str:
        return f"Circuit({self.name!r}: {len(self.components)} components, {len(self.tubes)} tubes)"


def is_circuit(obj: Any) -> bool:
    return isinstance(obj, Circuit)
