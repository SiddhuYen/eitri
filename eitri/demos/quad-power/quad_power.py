"""Quadcopter power harness: battery XT60 -> power distribution splice -> 4 x ESC XT30 + flight controller JST-PH.

Each ESC is rated 20 A and the flight controller draws 0.5 A. The four ESCs never peak together, so the pack
(the trunk) is sized for the measured in-flight peak, `pack_peak_a`; each branch is sized for its own ESC's full 20 A.
"""
from eitri.harness import *

PARAMS = dict(
    esc_max_a=20.0,        # each ESC's rating, A
    fc_a=0.5,              # flight controller draw, A
    pack_peak_a=44.0,      # measured peak battery current in flight, A (all four ESCs together)
    pack_v=14.8,           # 4S LiPo nominal, V
    trunk_mm=90,           # battery connector to the distribution splice
    front_mm=170,          # splice to the front ESCs
    rear_mm=210,           # splice to the rear ESCs
    fc_mm=120,             # splice to the flight controller
    trunk_awg=8,
    branch_awg=14,
    fc_awg=22,
)

ESCS = ("ESC1", "ESC2", "ESC3", "ESC4")             # front-left, front-right, rear-left, rear-right


def build(p):
    h = Harness("Quadcopter power harness", "4S battery -> distribution splice -> 4 ESCs + flight controller")
    batt = h.add(Connector("BATT", "XT60", pins=["+", "-"], gender="female", part_number="XT60H-F"))
    escs = [h.add(Connector(n, "XT30", pins=["+", "-"], gender="male", part_number="XT30U-M")) for n in ESCS]
    fc = h.add(Connector("FC", "JST-PH", pins={"1": "VBAT", "2": "GND"}, gender="female"))
    pdb_p, pdb_n = h.splice("PDB_P"), h.splice("PDB_N")

    trunk_a = p.pack_peak_a + p.fc_a
    h.net("VBAT", current_A=trunk_a, voltage_V=p.pack_v, max_drop_pct=3)
    h.net("GND", current_A=trunk_a, voltage_V=p.pack_v, max_drop_pct=3, round_trip=False)     # the return: counted once

    arm = {"ESC1": p.front_mm, "ESC2": p.front_mm, "ESC3": p.rear_mm, "ESC4": p.rear_mm}
    # supply, source toward load
    h.connect(batt["+"], pdb_p, Wire(p.trunk_awg, "RD", p.trunk_mm), net="VBAT")
    for esc in escs:
        h.connect(pdb_p, esc["+"], Wire(p.branch_awg, "RD", arm[esc.name]), net="VBAT", current_A=p.esc_max_a)
    h.connect(pdb_p, fc["1"], Wire(p.fc_awg, "RD", p.fc_mm), net="VBAT", current_A=p.fc_a)
    # return, load toward source
    for esc in escs:
        h.connect(esc["-"], pdb_n, Wire(p.branch_awg, "BK", arm[esc.name]), net="GND", current_A=p.esc_max_a)
    h.connect(fc["2"], pdb_n, Wire(p.fc_awg, "BK", p.fc_mm), net="GND", current_A=p.fc_a)
    h.connect(pdb_n, batt["-"], Wire(p.trunk_awg, "BK", p.trunk_mm), net="GND")
    return h


def verify(h, p, c):
    stats = net_stats(h)
    c.at_most("pack peak is within the four ESC ratings (A)", p.pack_peak_a, 4 * p.esc_max_a)
    c.at_most("battery to ESC voltage drop (%)", stats["VBAT"].drop_pct, 2.0)
    c.count("a supply and a return wire for every load", len(h.links), 2 * (len(ESCS) + 1) + 2)
    c.at_least("longest arm run covers the rear ESCs (mm)", max(l.length_mm for l in h.links), p.rear_mm)
