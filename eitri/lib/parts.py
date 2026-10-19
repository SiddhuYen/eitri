"""Standard parts with real geometry: gears, threads, screws, nuts, washers, bearings.

Thin wrappers over bd_warehouse with eitri conventions (parts stand on Z=0,
metric sizes like "M3"). Screws/nuts/bearings are for assemblies and for cutting
exact pockets; they are simplified (no thread ridges) unless noted.
"""
from __future__ import annotations

import math

import build123d as bd
from build123d import Align, Axis, Cylinder, Pos

from .bearings import bearing_dims
from .fasteners import screw_pitch, tap_drill

__all__ = ["spur_gear", "gear_pitch_diameter", "gear_outer_diameter", "gear_center_distance",
           "external_thread", "internal_thread", "threaded_rod", "tapped_hole", "screw", "hex_nut", "washer",
           "bearing"]


def gear_pitch_diameter(module: float, teeth: int) -> float:
    """Pitch diameter = module * teeth."""
    return module * teeth


def gear_outer_diameter(module: float, teeth: int) -> float:
    """Tip (outer) diameter = module * (teeth + 2)."""
    return module * (teeth + 2)


def gear_center_distance(module: float, teeth_a: int, teeth_b: int) -> float:
    """Centre distance between two meshing spur gears of the same module."""
    return module * (teeth_a + teeth_b) / 2


def spur_gear(module: float, teeth: int, thickness: float, bore: float = 0.0, pressure_angle: float = 20.0,
              hub_diameter: float = 0.0, hub_height: float = 0.0, root_fillet: float | None = None) -> bd.Shape:
    """An involute spur gear standing on Z=0, axis Z, optional bore and hub on top.

    Outer diameter = module*(teeth+2); two gears mesh at centre distance
    module*(t1+t2)/2. Typical FDM: module >= 1, teeth >= 12.
    """
    from bd_warehouse.gear import SpurGear
    g = SpurGear(module=module, tooth_count=teeth, pressure_angle=pressure_angle, thickness=thickness,
                 root_fillet=root_fillet, align=(Align.CENTER, Align.CENTER, Align.MIN))
    g = bd.Solid(g.wrapped) if not isinstance(g, bd.Solid) and len(g.solids()) == 1 else g
    if hub_diameter and hub_height:
        g = g + Pos(0, 0, thickness) * Cylinder(hub_diameter / 2, hub_height, align=(Align.CENTER, Align.CENTER, Align.MIN))
    if bore:
        g = g - Pos(0, 0, -1) * Cylinder(bore / 2, thickness + hub_height + 2, align=(Align.CENTER, Align.CENTER, Align.MIN))
    g.label = f"gear_m{module:g}_t{teeth}"
    return g


def _size_to_major_pitch(size: str, pitch: float | None) -> tuple[float, float]:
    major = float(size.upper().lstrip("M"))
    return major, (pitch or screw_pitch(size))


def external_thread(size: str = "M8", length: float = 10.0, pitch: float | None = None,
                    end_finishes: tuple[str, str] = ("fade", "fade")) -> bd.Shape:
    """The thread ridge of an ISO external thread (no core!), from Z=0 up `length`.

    Fuse it onto a core cylinder of the minor diameter; or just use threaded_rod().
    size like "M8" or a diameter; pitch defaults to the coarse pitch.
    """
    from bd_warehouse.thread import IsoThread
    major, p = _size_to_major_pitch(size, pitch)
    return IsoThread(major_diameter=major, pitch=p, length=length, external=True, end_finishes=end_finishes)


def internal_thread(size: str = "M8", length: float = 10.0, pitch: float | None = None,
                    end_finishes: tuple[str, str] = ("fade", "fade")) -> bd.Shape:
    """The ridge solid of an ISO internal thread, from Z=0 up `length`: bore the body to the
    major diameter, then ADD this solid (see tapped_hole())."""
    from bd_warehouse.thread import IsoThread
    major, p = _size_to_major_pitch(size, pitch)
    return IsoThread(major_diameter=major, pitch=p, length=length, external=False, end_finishes=end_finishes)


def threaded_rod(size: str = "M8", length: float = 20.0, pitch: float | None = None) -> bd.Shape:
    """A fully threaded rod along Z standing on Z=0 (real ISO thread geometry)."""
    from bd_warehouse.thread import IsoThread
    major, p = _size_to_major_pitch(size, pitch)
    thread = IsoThread(major_diameter=major, pitch=p, length=length, external=True, end_finishes=("fade", "fade"))
    core = Cylinder(thread.min_radius + 0.01, length, align=(Align.CENTER, Align.CENTER, Align.MIN))
    rod = core + thread
    rod.label = f"rod_{size}"
    return rod


def tapped_hole(body: bd.Shape, size: str, at: tuple[float, float, float], depth: float,
                pitch: float | None = None) -> bd.Shape:
    """Cut a real ISO internal thread into `body`: the hole starts at point `at` (on a
    face normal to +Z) and goes down `depth`. Slow-ish (helical sweep); use a
    tap-drill hole (tap_drill(size)) when only the printed/machined hole matters."""
    from bd_warehouse.thread import IsoThread
    major, p = _size_to_major_pitch(size, pitch)
    x, y, z = at
    bore = Pos(x, y, z + 1) * Cylinder(major / 2 + 0.05, depth + 1, align=(Align.CENTER, Align.CENTER, Align.MAX))
    thread = IsoThread(major_diameter=major, pitch=p, length=depth, external=False, end_finishes=("fade", "fade"))
    thread = Pos(x, y, z - depth) * thread
    return (body - bore) + thread


def _bdw_size(size: str) -> str:
    size = size.upper()
    return size if "-" in size else f"{size}-{screw_pitch(size):g}"


def screw(size: str = "M3", length: float = 10.0, kind: str = "socket_head") -> bd.Shape:
    """A simplified screw model for assemblies: head at the top (Z=0), shank pointing -Z.

    kind: socket_head (ISO 4762), countersunk (ISO 10642), button (ISO 7380),
    pan (ISO 14583), hex (ISO 4014). Sizes "M2".."M12".
    """
    from bd_warehouse import fastener as f
    cls = {"socket_head": f.SocketHeadCapScrew, "countersunk": f.CounterSunkScrew, "button": f.ButtonHeadScrew,
           "pan": f.PanHeadScrew, "hex": f.HexHeadScrew}[kind]
    s = cls(size=_bdw_size(size), length=length, simple=True)
    shape = bd.Solid(s.wrapped) if len(s.solids()) == 1 else bd.Compound(s.wrapped)
    # normalise: head top at Z=0, shank down -Z
    bb = shape.bounding_box()
    shape = shape.moved(bd.Location((0, 0, -bb.max.Z)))
    shape.label = f"screw_{size}x{length:g}"
    return shape


def hex_nut(size: str = "M3") -> bd.Shape:
    """A hex nut (ISO 4032) standing on Z=0, bore along Z."""
    from bd_warehouse.fastener import HexNut
    n = HexNut(size=_bdw_size(size), simple=True)
    shape = bd.Solid(n.wrapped) if len(n.solids()) == 1 else bd.Compound(n.wrapped)
    bb = shape.bounding_box()
    shape = shape.moved(bd.Location((0, 0, -bb.min.Z)))
    shape.label = f"nut_{size}"
    return shape


def washer(size: str = "M3") -> bd.Shape:
    """A plain washer (ISO 7089) lying on Z=0."""
    from bd_warehouse.fastener import PlainWasher
    w = PlainWasher(size=size.upper(), fastener_type="iso7089")      # washers are sized by the plain thread size
    shape = bd.Solid(w.wrapped) if len(w.solids()) == 1 else bd.Compound(w.wrapped)
    bb = shape.bounding_box()
    shape = shape.moved(bd.Location((0, 0, -bb.min.Z)))
    shape.label = f"washer_{size}"
    return shape


def bearing(name: str = "608") -> bd.Shape:
    """A ball bearing model (inner race, outer race, balls) standing on Z=0, e.g. "608", "625", "6001"."""
    from bd_warehouse.bearing import SingleRowDeepGrooveBallBearing
    bore, od, w = bearing_dims(name)
    b = SingleRowDeepGrooveBallBearing(size=f"M{bore:g}-{od:g}-{w:g}")
    shape = bd.Compound(b.wrapped)
    bb = shape.bounding_box()
    shape = shape.moved(bd.Location((0, 0, -bb.min.Z)))
    shape.label = f"bearing_{name}"
    return shape
