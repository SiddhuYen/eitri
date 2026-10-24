"""Air clamp: a double-acting Ø40 cylinder on a 5/2 solenoid valve with meter-out speed control, fed from a 6 bar FRL."""
from eitri.pneumatics import *

PARAMS = dict(p_bar=6.0, bore_mm=40, rod_mm=16, stroke_mm=80, load_N=400, speed=150, cycles_per_min=15)
CONFIGS = {"fast": dict(speed=600), "light": dict(load_N=150)}


def build(p):
    c = Circuit("Air clamp", "double-acting clamp cylinder, 5/2 valve, meter-out speed control")
    air = c.supply("AIR", pressure_bar=7.0, flow_l_min=500)
    frl = c.frl("FRL", set_bar=p.p_bar, C=2.5, b=0.35)
    v = c.valve("V1", "5/2", actuation="solenoid", returns="spring", C=1.0, b=0.3)
    fc = c.flow_control("FC1", meter="out", C=0.5, b=0.3)
    cyl = c.cylinder("C1", bore_mm=p.bore_mm, rod_mm=p.rod_mm, stroke_mm=p.stroke_mm, load_N=p.load_N, speed_mm_s=p.speed, cycles_per_min=p.cycles_per_min)
    c.tube("T1", air["OUT"], frl["IN"], id_mm=8, length_m=2.0)
    c.tube("T2", frl["OUT"], v["P"], id_mm=6, length_m=1.0)
    c.tube("T3", v["A"], cyl["CAP"], id_mm=6, length_m=1.5)
    c.tube("T4", v["B"], fc["A"], id_mm=6, length_m=1.0)
    c.tube("T5", fc["B"], cyl["ROD"], id_mm=6, length_m=0.5)
    c.silencer(v["R"])
    c.silencer(v["S"])
    return c


def verify(c, p, chk):
    f = cylinder_forces(c.get("C1"), p.p_bar)
    chk.at_least("clamp force margin", f["extend_usable_N"] / p.load_N, 1.4)
