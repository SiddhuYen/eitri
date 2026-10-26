"""The harness model: connectors, wires, cables, splices and nets, and the Harness that holds them.

A harness model file is a Python file like a CAD model:

    from eitri.harness import *

    PARAMS = dict(arm_mm=180)

    def build(p):
        h = Harness("Quad power")
        batt = h.add(Connector("BATT", "XT60", pins=["+", "-"]))
        esc = h.add(Connector("ESC1", "XT30", pins=["+", "-"]))
        h.net("VBAT", current_A=20, voltage_V=14.8)
        h.connect(batt["+"], esc["+"], Wire(14, "RD", p.arm_mm), net="VBAT")
        return h

Everything is plain data; the checks, the diagram, the BOM and the cut list are computed from it by the rest of
this package (see eitri.harness.checks, .report, .outputs).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable

from .catalog import CATALOG, ConnectorSpec, Gauge, normalize_color, normalize_type, spec_for


class HarnessError(ValueError):
    """A harness was described wrongly (not an electrical problem: those are reported by the checks)."""


# --------------------------------------------------------------------------- endpoints


@dataclass(frozen=True)
class PinRef:
    """One pin of one connector: what `connector["+"]` gives."""
    connector: str
    pin: str
    owner: Any = field(default=None, compare=False, repr=False)       # the Connector it came from, to add it on first use

    def __str__(self) -> str:
        return f"{self.connector}.{self.pin}"


@dataclass(frozen=True)
class SpliceRef:
    splice: str

    def __str__(self) -> str:
        return self.splice


Endpoint = PinRef | SpliceRef


def endpoint_to_json(e: Endpoint) -> dict:
    return {"connector": e.connector, "pin": e.pin} if isinstance(e, PinRef) else {"splice": e.splice}


def endpoint_from_json(d: dict) -> Endpoint:
    return PinRef(d["connector"], d["pin"]) if "connector" in d else SpliceRef(d["splice"])


# --------------------------------------------------------------------------- parts


@dataclass(eq=False)
class Connector:
    """A connector: a name, a type from the catalog (or your own with explicit ratings) and its pins.

    pins: a count (pins "1".."N"), a list of pin names (["+", "-"]), or a dict {pin name: function label}.
    nc: pin names declared not connected (they pass the unconnected-pin check)."""
    name: str
    type: str = "BARE"
    pins: Any = None
    part_number: str = ""
    gender: str = ""                        # "male" | "female" | "" (a plug's contacts are male or female)
    notes: str = ""
    rating_a: float | None = None           # per-pin rating; defaults to the catalog's, required for unknown types to be graded
    pitch_mm: float | None = None
    terminal_pn: str = ""                   # crimp terminal part number (defaults to the catalog's)
    nc: Iterable[str] = ()
    powered: str = "usb"                    # a board (ARDUINO-UNO, ARDUINO-NANO): how the board itself is powered, "usb" or "vin"
    vin_V: float | None = None              # a board powered on VIN: that voltage (sets the 5 V regulator's thermal limit)
    pin_names: list[str] = field(default_factory=list, init=False)
    pin_labels: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip() or any(c in str(self.name) for c in ". \t\n"):
            raise HarnessError(f"connector name {self.name!r} must be non-empty and contain no dots or spaces")
        self.name = str(self.name)
        self.type = normalize_type(self.type)
        spec = self.spec
        pins = self.pins
        if pins is None:
            pins = spec.default_pins if spec else 1
        if isinstance(pins, bool):
            raise HarnessError(f"{self.name}: pins must be a count, a list of names or a dict")
        if isinstance(pins, int):
            if pins < 1:
                raise HarnessError(f"{self.name}: a connector needs at least one pin")
            names, labels = [str(i + 1) for i in range(pins)], {}
        elif isinstance(pins, dict):
            names, labels = [str(k) for k in pins], {str(k): str(v) for k, v in pins.items() if v}
        else:
            names, labels = [str(x) for x in pins], {}
        if not names:
            raise HarnessError(f"{self.name}: a connector needs at least one pin")
        if len(set(names)) != len(names):
            raise HarnessError(f"{self.name}: duplicate pin names in {names}")
        self.pin_names, self.pin_labels = names, labels
        self.pins = len(names)
        self.nc = [str(x) for x in self.nc]
        self.powered = str(self.powered or "usb").lower()
        if self.powered not in ("usb", "vin"):
            raise HarnessError(f"{self.name}: powered must be 'usb' or 'vin', not {self.powered!r}")
        for n in self.nc:
            if n not in names:
                raise HarnessError(f"{self.name}: nc pin {n!r} is not one of its pins {names}")

    @property
    def spec(self) -> ConnectorSpec | None:
        return spec_for(self.type)

    @property
    def known(self) -> bool:
        return self.spec is not None

    @property
    def rating(self) -> float | None:
        """Per-pin current rating in A: the explicit one, else the catalog's (None: not limiting / unknown)."""
        if self.rating_a is not None:
            return float(self.rating_a)
        return self.spec.rating_a if self.spec else None

    @property
    def pitch(self) -> float | None:
        return self.pitch_mm if self.pitch_mm is not None else (self.spec.pitch_mm if self.spec else None)

    @property
    def termination(self) -> str:
        return self.spec.termination if self.spec else "crimp"

    def pin(self, key) -> PinRef:
        """A pin by name or by 1-based position."""
        if isinstance(key, bool):
            raise HarnessError(f"{self.name}: not a pin: {key!r}")
        if isinstance(key, int):
            if not 1 <= key <= len(self.pin_names):
                raise HarnessError(f"{self.name} has no pin #{key}; it has {len(self.pin_names)} pins: {self.pin_names}")
            return PinRef(self.name, self.pin_names[key - 1], self)
        key = str(key)
        if key in self.pin_names:
            return PinRef(self.name, key, self)
        for n, label in self.pin_labels.items():
            if label == key:
                return PinRef(self.name, n, self)
        raise HarnessError(f"{self.name} has no pin {key!r}; its pins are {self.pin_names}")

    __getitem__ = pin

    def add_pin(self, name: str, label: str = "") -> None:
        name = str(name)
        if name in self.pin_names:
            raise HarnessError(f"{self.name} already has a pin {name!r}")
        self.pin_names.append(name)
        if label:
            self.pin_labels[name] = label
        self.pins = len(self.pin_names)

    def rename_pin(self, old: str, new: str) -> None:
        if old not in self.pin_names:
            raise HarnessError(f"{self.name} has no pin {old!r}")
        if new != old and new in self.pin_names:
            raise HarnessError(f"{self.name} already has a pin {new!r}")
        self.pin_names[self.pin_names.index(old)] = new
        if old in self.pin_labels:
            self.pin_labels[new] = self.pin_labels.pop(old)
        self.nc = [new if x == old else x for x in self.nc]

    def remove_pin(self, name: str) -> None:
        if name not in self.pin_names:
            raise HarnessError(f"{self.name} has no pin {name!r}")
        if len(self.pin_names) == 1:
            raise HarnessError(f"{self.name} would have no pins left")
        self.pin_names.remove(name)
        self.pin_labels.pop(name, None)
        self.nc = [x for x in self.nc if x != name]
        self.pins = len(self.pin_names)


@dataclass(eq=False)
class Wire:
    """A wire (a template: connect() makes its own copy for every connection, so one Wire may be reused).

    gauge: an AWG number (18) or a metric size ("0.5mm2"). color: a code ("RD", "BK", "RD/WH" for a stripe) or name."""
    gauge: Any
    color: str = "BK"
    length_mm: float | None = None
    insulation: str = ""                    # "silicone", "PVC", ... (BOM description)
    notes: str = ""

    def __post_init__(self) -> None:
        self.gauge = Gauge.parse(self.gauge)
        self.color = normalize_color(self.color)
        if self.length_mm is not None and not isinstance(self.length_mm, (int, float)):
            raise HarnessError(f"wire length must be a number of mm, got {self.length_mm!r}")


@dataclass(eq=False)
class Cable:
    """A multi-core cable: its cores are Wires; connect them with Harness.connect_cable().

    Cores without a length of their own take the cable's length_mm. shield=True means the cable has an overall
    shield: it must be terminated (connect_cable(shield_a=..., shield_b=...)) or the checks fail."""
    name: str
    wires: list = field(default_factory=list)
    shield: bool = False
    jacket_color: str = "BK"
    length_mm: float | None = None
    part_number: str = ""
    notes: str = ""
    shield_ends: list = field(default_factory=list, init=False)       # PinRefs the shield is terminated to

    def __post_init__(self) -> None:
        self.name = str(self.name)
        if not self.wires:
            raise HarnessError(f"cable {self.name}: it needs at least one core (wires=[Wire(...), ...])")
        self.jacket_color = normalize_color(self.jacket_color)
        for w in self.wires:
            if not isinstance(w, Wire):
                raise HarnessError(f"cable {self.name}: cores must be Wire(...) objects, got {type(w).__name__}")
            if w.length_mm is None:
                if self.length_mm is None:
                    raise HarnessError(f"cable {self.name}: give the cable a length_mm or every core a length")
                w.length_mm = self.length_mm
        if self.length_mm is None:
            self.length_mm = max(w.length_mm for w in self.wires)


@dataclass(eq=False)
class Splice:
    """A junction of three or more wires (a crimp splice, a solder sleeve, a terminal block)."""
    name: str
    kind: str = "crimp"                     # crimp | solder | terminal
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.name or any(c in str(self.name) for c in ". \t\n"):
            raise HarnessError(f"splice name {self.name!r} must be non-empty and contain no dots or spaces")
        self.name = str(self.name)

    @property
    def ref(self) -> SpliceRef:
        return SpliceRef(self.name)


@dataclass(eq=False)
class Net:
    """Electrical intent of a group of connections: what the checks grade them against.

    current_A: the net's maximum current (every connection of the net carries it unless it says otherwise).
    voltage_V: the supply voltage the voltage-drop percentage is taken against.
    max_drop_pct: the allowed drop from a source to a load.
    round_trip: count the return conductor in the drop (V = 2*K*I*L/CM); False for a net that is itself the return."""
    name: str
    current_A: float | None = None
    voltage_V: float | None = None
    max_drop_pct: float = 3.0
    round_trip: bool = True
    notes: str = ""


@dataclass(eq=False)
class Link:
    """One wire in the harness: where it goes, what it is, and which net it belongs to."""
    id: str
    a: Endpoint
    b: Endpoint
    gauge: Gauge
    color: str
    length_mm: float
    net: str | None = None
    current_A: float | None = None          # this wire's own current (default: the net's)
    cable: str | None = None
    core: int | None = None
    kind: str = "wire"                      # wire | shield
    label: str = ""
    insulation: str = ""
    notes: str = ""

    def ends(self) -> tuple[Endpoint, Endpoint]:
        return self.a, self.b

    def other(self, e: Endpoint) -> Endpoint:
        return self.b if e == self.a else self.a


# --------------------------------------------------------------------------- the harness


class Harness:
    def __init__(self, name: str = "harness", description: str = ""):
        self.name = name
        self.description = description
        self.connectors: dict[str, Connector] = {}
        self.splices: dict[str, Splice] = {}
        self.nets: dict[str, Net] = {}
        self.cables: dict[str, Cable] = {}
        self.links: list[Link] = []
        self.layout: dict[str, list[float]] = {}
        self._next = 1

    # -- adding things ------------------------------------------------------
    def add(self, *items):
        """Add connectors, splices, nets or cables; returns the item (or the tuple of items)."""
        for it in items:
            if isinstance(it, Connector):
                self._unique(it.name, "connector")
                self.connectors[it.name] = it
            elif isinstance(it, Splice):
                self._unique(it.name, "splice")
                self.splices[it.name] = it
            elif isinstance(it, Net):
                if it.name in self.nets:
                    raise HarnessError(f"net {it.name!r} is already defined")
                self.nets[it.name] = it
            elif isinstance(it, Cable):
                if it.name in self.cables:
                    raise HarnessError(f"cable {it.name!r} is already defined")
                self.cables[it.name] = it
            else:
                raise HarnessError(f"cannot add a {type(it).__name__} to a Harness")
        return items[0] if len(items) == 1 else items

    def _unique(self, name: str, what: str) -> None:
        if name in self.connectors or name in self.splices:
            raise HarnessError(f"a connector or splice named {name!r} already exists; {what} names must be unique")

    def connector(self, name: str, type: str = "BARE", pins=None, **kw) -> Connector:
        return self.add(Connector(name, type, pins, **kw))

    def splice(self, name: str, kind: str = "crimp", **kw) -> Splice:
        return self.add(Splice(name, kind, **kw))

    def net(self, name: str, current_A: float | None = None, voltage_V: float | None = None, max_drop_pct: float = 3.0,
            round_trip: bool = True, **kw) -> Net:
        return self.add(Net(name, current_A, voltage_V, max_drop_pct, round_trip, **kw))

    def nc(self, *pins) -> None:
        """Declare pins not connected (they pass the unconnected-pin check)."""
        for p in pins:
            ref = self._pin(p)
            c = self.connectors[ref.connector]
            if ref.pin not in c.nc:
                c.nc = list(c.nc) + [ref.pin]

    # -- connecting -----------------------------------------------------------
    def _endpoint(self, x) -> Endpoint:
        if isinstance(x, (PinRef, SpliceRef)):
            ref = x
        elif isinstance(x, Splice):
            ref = x.ref
        elif isinstance(x, Connector):
            if len(x.pin_names) != 1:
                raise HarnessError(f"{x.name} has {len(x.pin_names)} pins; say which: {x.name}[pin]")
            ref = x.pin(1)
        elif isinstance(x, str):
            if x in self.splices:
                ref = SpliceRef(x)
            else:
                name, dot, pin = x.partition(".")
                if not dot:
                    if name in self.connectors and len(self.connectors[name].pin_names) == 1:
                        ref = self.connectors[name].pin(1)
                    else:
                        raise HarnessError(f"{x!r} is neither a splice nor 'CONNECTOR.PIN'")
                elif name in self.connectors:
                    ref = self.connectors[name].pin(pin)
                else:
                    raise HarnessError(f"no connector {name!r} (in {x!r}); connectors: {list(self.connectors)}")
        else:
            raise HarnessError(f"cannot connect to {x!r}; use connector['pin'], a Splice, or 'CONN.PIN'")
        if isinstance(ref, PinRef):
            if ref.connector not in self.connectors:        # a connector built but not added yet is added by connect()
                raise HarnessError(f"connector {ref.connector!r} is not in the harness; add it with h.add(...)")
            if ref.pin not in self.connectors[ref.connector].pin_names:
                raise HarnessError(f"{ref.connector} has no pin {ref.pin!r}")
        elif ref.splice not in self.splices:
            raise HarnessError(f"splice {ref.splice!r} is not in the harness; add it with h.splice(...)")
        return ref

    def _pin(self, x) -> PinRef:
        e = self._endpoint(x)
        if not isinstance(e, PinRef):
            raise HarnessError(f"{x!r} is a splice, not a connector pin")
        return e

    def _auto_add(self, *xs) -> None:
        for x in xs:
            if isinstance(x, PinRef):
                x = x.owner
            if isinstance(x, Connector) and x.name not in self.connectors:
                self.add(x)
            elif isinstance(x, Splice) and x.name not in self.splices:
                self.add(x)

    def _new_id(self, name: str | None) -> str:
        if name:
            if any(l.id == name for l in self.links):
                raise HarnessError(f"a wire named {name!r} already exists")
            return str(name)
        while any(l.id == f"W{self._next}" for l in self.links):
            self._next += 1
        wid = f"W{self._next}"
        self._next += 1
        return wid

    def _net_name(self, net) -> str | None:
        if net is None:
            return None
        if isinstance(net, Net):
            if net.name not in self.nets:
                self.add(net)
            return net.name
        if net not in self.nets:
            raise HarnessError(f"unknown net {net!r}; define it first with h.net({net!r}, current_A=..., voltage_V=...); nets: {list(self.nets)}")
        return str(net)

    def connect(self, a, b, wire: Wire, net=None, current_A: float | None = None, length_mm: float | None = None,
                name: str | None = None, label: str = "") -> Link:
        """Run a wire from `a` to `b` (connector pins or splices). Direction matters for power nets: connect from the
        source toward the load, because the voltage drop is summed along that direction."""
        if not isinstance(wire, Wire):
            raise HarnessError("connect(a, b, wire): the third argument must be a Wire(gauge, color, length_mm)")
        self._auto_add(a, b)
        ea, eb = self._endpoint(a), self._endpoint(b)
        length = length_mm if length_mm is not None else wire.length_mm
        if length is None:
            raise HarnessError(f"wire {ea}->{eb} has no length; give Wire(gauge, color, length_mm) or connect(..., length_mm=...)")
        link = Link(self._new_id(name), ea, eb, wire.gauge, wire.color, float(length), self._net_name(net),
                    None if current_A is None else float(current_A), label=label, insulation=wire.insulation, notes=wire.notes)
        self.links.append(link)
        return link

    def connect_cable(self, cable: Cable, a_pins: list, b_pins: list, net: list | None = None, shield_a=None, shield_b=None,
                      name: str | None = None) -> list[Link]:
        """Connect a multi-core cable: core i runs from a_pins[i] to b_pins[i]. `net` may list a net per core.
        shield_a / shield_b: the pin each end of the cable's shield is terminated to (None: not terminated there)."""
        if not isinstance(cable, Cable):
            raise HarnessError("connect_cable(cable, a_pins, b_pins): the first argument must be a Cable")
        if len(a_pins) != len(cable.wires) or len(b_pins) != len(cable.wires):
            raise HarnessError(f"cable {cable.name} has {len(cable.wires)} cores but {len(a_pins)} / {len(b_pins)} pins were given")
        if net is not None and len(net) != len(cable.wires):
            raise HarnessError(f"cable {cable.name}: give one net per core ({len(cable.wires)}), or none")
        if cable.name not in self.cables:
            self.add(cable)
        out = []
        for i, (w, pa, pb) in enumerate(zip(cable.wires, a_pins, b_pins)):
            ln = self.connect(pa, pb, w, net=net[i] if net else None, name=f"{name}.{i + 1}" if name else None)
            ln.cable, ln.core = cable.name, i + 1
            out.append(ln)
        for side, pin in (("a", shield_a), ("b", shield_b)):
            if pin is not None:
                ref = self._pin(pin)
                cable.shield_ends.append(ref)
        return out

    # -- queries ----------------------------------------------------------------
    def link(self, wire_id: str) -> Link:
        for l in self.links:
            if l.id == wire_id:
                return l
        raise HarnessError(f"no wire {wire_id!r}; wires: {[l.id for l in self.links]}")

    def links_at(self, e: Endpoint) -> list[Link]:
        return [l for l in self.links if l.a == e or l.b == e]

    def wire_current(self, l: Link) -> float | None:
        if l.current_A is not None:
            return l.current_A
        n = self.nets.get(l.net) if l.net else None
        return n.current_A if n else None

    def net_of(self, l: Link) -> Net | None:
        return self.nets.get(l.net) if l.net else None

    def apply_layout(self, layout: dict | None) -> None:
        """Layout hints from the model file or the editor: {node name: [x, y]} (diagram pixels)."""
        for k, v in (layout or {}).items():
            try:
                self.layout[str(k)] = [float(v[0]), float(v[1])]
            except (TypeError, ValueError, IndexError):
                continue

    def place(self, name: str, x: float, y: float) -> None:
        self.layout[name] = [float(x), float(y)]

    # -- renaming (used by the editor's direct edits) ---------------------------------
    def rename_connector(self, old: str, new: str) -> None:
        if old not in self.connectors:
            raise HarnessError(f"no connector {old!r}")
        if new == old:
            return
        if any(c in new for c in ". \t\n") or not new:
            raise HarnessError(f"connector name {new!r} must be non-empty and contain no dots or spaces")
        self._unique(new, "connector")
        conn = self.connectors.pop(old)
        conn.name = new
        self.connectors[new] = conn         # keeps insertion order close enough: moved to the end
        for l in self.links:
            if isinstance(l.a, PinRef) and l.a.connector == old:
                l.a = PinRef(new, l.a.pin)
            if isinstance(l.b, PinRef) and l.b.connector == old:
                l.b = PinRef(new, l.b.pin)
        for cab in self.cables.values():
            cab.shield_ends = [PinRef(new, p.pin) if p.connector == old else p for p in cab.shield_ends]
        if old in self.layout:
            self.layout[new] = self.layout.pop(old)

    def rename_pin(self, connector: str, old: str, new: str) -> None:
        c = self.connectors[connector]
        c.rename_pin(old, new)
        for l in self.links:
            if l.a == PinRef(connector, old):
                l.a = PinRef(connector, new)
            if l.b == PinRef(connector, old):
                l.b = PinRef(connector, new)
        for cab in self.cables.values():
            cab.shield_ends = [PinRef(connector, new) if p == PinRef(connector, old) else p for p in cab.shield_ends]

    def remove_pin(self, connector: str, pin: str) -> None:
        ref = PinRef(connector, pin)
        if self.links_at(ref):
            raise HarnessError(f"{ref} still has wires ({', '.join(l.id for l in self.links_at(ref))}); remove or move them first")
        self.connectors[connector].remove_pin(pin)

    def remove_link(self, wire_id: str) -> None:
        self.links.remove(self.link(wire_id))

    def clone(self) -> "Harness":
        return copy.deepcopy(self)

    def __repr__(self) -> str:
        return f"Harness({self.name!r}: {len(self.connectors)} connectors, {len(self.links)} wires, {len(self.splices)} splices)"


def is_harness(obj: Any) -> bool:
    return isinstance(obj, Harness)
