"""Writes the two reference-part fixtures (made here from plain numbers; no third-party CAD files).

    python tests/fixtures/reference/make_fixtures.py

test_motor.step  a made-up stepper-like motor (NOT a vendor model): a 42 x 42 x 40 mm body below z=0, a Ø22 x 2 mm
                 pilot boss and a Ø5 x 20 mm shaft on its front face, four Ø2.5 x 4.5 mm blind holes (an M3 tap-drill
                 size) on a 31 mm square on that face.
test_block.step  a 40 x 20 x 10 mm block centred on the origin: two Ø4 through holes along Z at (±12, 6), one Ø6 blind
                 hole along X from the +X face (depth 8), and a Ø8 x 5 mm boss along Z on its top face.
"""
from __future__ import annotations

import sys
from pathlib import Path

import build123d as bd

HERE = Path(__file__).resolve().parent


def motor() -> bd.Part:
    body = bd.Pos(0, 0, -20) * bd.Box(42, 42, 40)
    pilot = bd.Pos(0, 0, 1) * bd.Cylinder(11, 2)
    shaft = bd.Pos(0, 0, 12) * bd.Cylinder(2.5, 20)
    part = body + pilot + shaft
    for x in (-15.5, 15.5):
        for y in (-15.5, 15.5):
            part -= bd.Pos(x, y, -2.25) * bd.Cylinder(1.25, 4.5)
    return part


def block() -> bd.Part:
    part = bd.Box(40, 20, 10)
    for x in (-12, 12):
        part -= bd.Pos(x, 6, 0) * bd.Cylinder(2, 12)
    part -= bd.Pos(16, 0, 0) * (bd.Rot(0, 90, 0) * bd.Cylinder(3, 8))
    part += bd.Pos(0, 0, 7.5) * bd.Cylinder(4, 5)
    return part


def main(out: Path = HERE) -> None:
    for name, make in (("test_motor", motor), ("test_block", block)):
        shape = make()
        shape.label = name
        bd.export_step(shape, str(out / f"{name}.step"))
        print(name, round(shape.volume, 3), (out / f"{name}.step").stat().st_size, "bytes")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
