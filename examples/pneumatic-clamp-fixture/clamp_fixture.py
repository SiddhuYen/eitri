"""Pneumatic clamp fixture: a pusher locates the part against a fixed stop, then a compact cylinder clamps it down.

Catalogue parts (every figure from the manufacturer's datasheet, see eitri/pneumatics/catalog.py):
  C1 pusher  Festo DSNU-20-50-P-A     ISO 6432 round cylinder, Ø20 x 50, magnetic piston, elastic cushioning
  C2 clamp   SMC CDQ2B32-20DZ         CQ2 compact cylinder, Ø32 x 20, magnet, rubber bumper
  V1         SMC SY3120-5LZ-C6        5/2 single solenoid, spring return (the pusher retracts when unpowered)
  V2         SMC SY3220-5LZ-C6        5/2 double solenoid (the clamp HOLDS its position if the power drops)
  tube       SMC TU0604               6 x 4 mm polyurethane, 8 bar at 20 C
  speed      SMC AS2201F-01-06        meter-out speed controllers, R1/8 x 6 mm
  silencers  SMC AN05-M5              on the valves' M5 exhausts
  sensors    Festo SMT-8M-A-PS-24V-E-0,3-M8D on C1 (PNP, M8), SMC D-M9B on C2 (2-wire)
Air: shop air 7 bar through an SMC AW20 filter-regulator set to 6 bar. Control: 24 V DC from the PLC panel.
"""
from eitri.pneumatics import *

PARAMS = dict(
    p_bar=6.0,              # regulated pressure
    push_N=60.0,            # pusher: slide the part (2 kg, mu 0.3 on the base plate) and hold it against the stop
    part_kg=2.0,
    pusher_head_kg=0.15,    # the pusher's head and bracket on the rod
    push_speed=150.0,       # mm/s, set on the speed controllers
    clamp_N=250.0,          # the clamp force the machining needs
    clamp_pad_kg=0.30,
    clamp_speed=80.0,
    cycles_per_min=4.0,     # one part every 15 s
    shop_air_l_min=150.0,   # the share of the compressor this station may draw
)


def build(p):
    c = Circuit("Clamp fixture", "pusher locates the part against a stop, compact cylinder clamps it; PLC-controlled, 24 V DC")
    c.control_voltage(24)
    air = c.supply("AIR", pressure_bar=7.0, flow_l_min=p.shop_air_l_min)
    frl = c.frl("FRL", set_bar=p.p_bar, part_number="SMC AW20-02BG", max_bar=10.0)           # AW20: 1.0 MPa max; no C published
    tee = c.node("MAN")                                                                        # the two valves' supply tee
    v1 = c.valve("V1", part="SMC SY3120-5LZ-C6", signal="PUSHER_EXTEND", terminal="")
    v2 = c.valve("V2", part="SMC SY3220-5LZ-C6", signal="CLAMP_CLOSE", signal_12="CLAMP_OPEN")
    c1 = c.cylinder("C1", part="FESTO DSNU-20-50-P-A", load_N=p.push_N, load_retract_N=10, speed_mm_s=p.push_speed,
                    cycles_per_min=p.cycles_per_min, load_mass_kg=p.part_kg + p.pusher_head_kg, duty="moving", magnet=True,
                    on_power_loss="retract", on_air_loss="release")          # pusher: back off when anything is lost
    c2 = c.cylinder("C2", part="SMC CDQ2B32-20DZ", load_N=p.clamp_N, load_retract_N=20, speed_mm_s=p.clamp_speed,
                    cycles_per_min=p.cycles_per_min, load_mass_kg=p.clamp_pad_kg, duty="static",
                    on_power_loss="hold", on_air_loss="release")             # clamp: keep the part on a power cut; no air = no clamp
    # meter-out speed control on both lines of both cylinders, screwed straight into the cylinder ports (no tube, no fitting)
    fc = {k: c.flow_control(k, meter="out", part="SMC AS2201F-01-06") for k in ("FC1A", "FC1B", "FC2A", "FC2B")}

    c.tube("T0", air["OUT"], frl["IN"], id_mm=6.5, length_m=3.0, part_number="SMC TU1065")    # 10 x 6.5 drop from the shop main
    c.tube("T1", frl["OUT"], tee["N"], part="SMC TU0604", length_m=0.5)
    c.tube("T2", tee["N"], v1["P"], part="SMC TU0604", length_m=0.2)
    c.tube("T3", tee["N"], v2["P"], part="SMC TU0604", length_m=0.2)
    c.tube("T4", v1["A"], fc["FC1B"]["A"], part="SMC TU0604", length_m=1.2)      # V1 position 1 (rest) feeds A: the pusher's ROD side
    c.screw_in(fc["FC1B"]["B"], c1["ROD"], name="J5")
    c.tube("T6", v1["B"], fc["FC1A"]["A"], part="SMC TU0604", length_m=1.2)
    c.screw_in(fc["FC1A"]["B"], c1["CAP"], name="J7")
    c.tube("T8", v2["A"], fc["FC2B"]["A"], part="SMC TU0604", length_m=1.0)
    c.screw_in(fc["FC2B"]["B"], c2["ROD"], name="J9")
    c.tube("T10", v2["B"], fc["FC2A"]["A"], part="SMC TU0604", length_m=1.0)
    c.screw_in(fc["FC2A"]["B"], c2["CAP"], name="J11")
    for v in (v1, v2):
        c.silencer(v["R"], part="SMC AN05-M5")
        c.silencer(v["S"], part="SMC AN05-M5")

    c.sensor("B1", c1, "retracted", part="FESTO SMT-8M-A-PS-24V-E-0,3-M8D", signal="PUSHER_HOME")
    c.sensor("B2", c1, "extended", part="FESTO SMT-8M-A-PS-24V-E-0,3-M8D", signal="PART_AT_STOP")
    c.sensor("B3", c2, "retracted", part="SMC D-M9B", signal="CLAMP_OPEN_POS")
    c.sensor("B4", c2, "extended", part="SMC D-M9B", signal="CLAMP_CLOSED_POS")
    return c


def verify(c, p, chk):
    f = cylinder_forces(c.get("C2"), p.p_bar)
    chk.at_least("clamp force margin (usable / required)", f["extend_usable_N"] / p.clamp_N, 1.5)
    # the pusher must hold the part against the stop with less force than the clamp, or it fights the clamp's friction
    chk.at_most("pusher force below clamp force", cylinder_forces(c.get("C1"), p.p_bar)["extend_theoretical_N"], f["extend_theoretical_N"])
    chk.count("PLC inputs", len(c.sensors), 4)
