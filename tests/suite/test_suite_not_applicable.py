"""The electronics cross-domain checks are "not applicable" only when their subject provably does not exist in the project (no file of
that kind and nothing in the circuit that would need one); otherwise they stay "not run" and block the export. The fixture is the
clean-home smoke test's room climate box (Nano + BME280 + SSD1306, a case, a harness, firmware; no mechanism)."""
from __future__ import annotations

import shutil
from pathlib import Path

from eitri.model import load_model
from eitri_suite import electronics as E
from eitri_suite import workspace as W

BOX = Path(__file__).resolve().parent / "fixtures" / "sensor-box"
ACT = ("every actuated joint has its actuator", "stall current vs the drivers", "dissipation: regulators and drivers")


def variant(tmp_path: Path, name: str, edits: dict[str, list[tuple[str, str]]] | None = None, drop: tuple[str, ...] = (),
            add: dict[str, str] | None = None) -> Path:
    d = tmp_path / name
    shutil.copytree(BOX, d)
    for f in drop:
        (d / f).unlink()
    for f, subs in (edits or {}).items():
        text = (d / f).read_text(encoding="utf-8")
        for a, b in subs:
            assert a in text, (f, a)
            text = text.replace(a, b)
        (d / f).write_text(text, encoding="utf-8")
    for f, text in (add or {}).items():
        (d / f).write_text(text, encoding="utf-8")
    return d


def run(folder: Path):
    lm = load_model(folder / "elec.py")
    return E.run(lm.module, lm.build(), folder / "elec.py")


def one(checks, needle):
    hits = [c for c in checks if needle in c.name]
    assert len(hits) == 1, (needle, [c.name for c in checks])
    return hits[0]


def test_a_sensor_box_with_no_mechanism_and_no_actuator_is_not_applicable(tmp_path):
    checks, data = run(variant(tmp_path, "box"))
    for needle in ACT:
        c = one(checks, needle)
        assert c.status == "not_applicable" and c.ok and c.severity == "pass", (needle, c.measured)
        assert c.measured == ("not applicable: the project has no mechanism file and the circuit has no servo, motor or solenoid "
                              "(nothing is actuated)")
    assert data["not_applicable"] == 3
    assert all(c.status != "not_run" for c in checks if c.name.startswith(("actuators", "dissipation")))


def test_a_servo_with_no_mechanism_file_stays_not_run(tmp_path):
    d = variant(tmp_path, "servo", {"elec.py": [('    c.i2c("SDA"', ('    s = c.servo("PAN", "SG90")\n    c.net("SIG", nano["D9"], s["SIG"])\n'
                                                  '    c.connect(s["V+"], nano["5V"])\n    c.connect(s["GND"], nano["GND"])\n    c.i2c("SDA"'))]})
    checks, _ = run(d)
    for needle in ACT:
        c = one(checks, needle)
        assert c.status == "not_run", (needle, c.measured)
        assert "the project has no mechanism file, but the circuit has PAN" in c.measured


def test_a_motor_or_a_solenoid_counts_as_an_actuator_too(tmp_path):
    for part in ('c.motor("FAN", 5.0, 0.2)', 'c.solenoid("LATCH", 5.0, 0.3)'):
        d = variant(tmp_path, part.split('"')[1], {"elec.py": [('    c.i2c("SDA"', f'    {part}\n    c.i2c("SDA"')]})
        checks, _ = run(d)
        assert one(checks, "stall current vs the drivers").status == "not_run"


def test_a_mechanism_file_that_does_not_build_stays_not_run(tmp_path):
    d = variant(tmp_path, "badmech", add={"mech.py": "from eitri.mechanism import *\n\nPARAMS = dict()\n\ndef build(p):\n"
                                                    "    raise ValueError('half written')\n"})
    checks, _ = run(d)
    for needle in ACT:
        c = one(checks, needle)
        assert c.status == "not_run" and "mech.py does not build" in c.measured


def test_a_mechanism_named_in_coupling_but_missing_stays_not_run(tmp_path):
    d = variant(tmp_path, "named", {"elec.py": [('harness="wiring.py",', 'harness="wiring.py", mechanism="mech.py",')]})
    c = one(run(d)[0], "stall current vs the drivers")
    assert c.status == "not_run" and "COUPLING mechanism='mech.py' is not a file of the project" in c.measured


def test_no_harness_file_is_not_applicable(tmp_path):
    d = variant(tmp_path, "noharness", {"elec.py": [('harness="wiring.py", ', '')]}, drop=("wiring.py",))
    checks, _ = run(d)
    for needle in ("harness connectors are circuit parts", "rail currents in the harness"):
        c = one(checks, needle)
        assert c.status == "not_applicable" and "the project has no harness file" in c.measured


def test_no_firmware_file_with_a_board_stays_not_run(tmp_path):
    d = variant(tmp_path, "nofw", {"elec.py": [('firmware="fw.py", ', '')]}, drop=("fw.py",))
    c = one(run(d)[0], "firmware pins match the circuit")
    assert c.status == "not_run" and "the project has no firmware file, but the circuit has NANO" in c.measured


def test_no_firmware_file_and_no_board_is_not_applicable(tmp_path):
    elec = ('from eitri.electronics import *\n\nPARAMS = dict()\n\n\ndef build(p):\n    c = Circuit("Lamp", "a 5 V supply and an LED")\n'
            '    psu = c.supply("PSU", 5.0, current_A=1.0)\n    r = c.resistor("R1", 330)\n    d = c.led("D1", "red")\n'
            '    c.connect(psu["+"], r[1])\n    c.connect(r[2], d["A"])\n    c.connect(d["K"], psu["-"])\n    return c\n')
    d = variant(tmp_path, "lamp", drop=("fw.py", "wiring.py", "elec.py"), add={"elec.py": elec})
    checks, _ = run(d)
    c = one(checks, "firmware pins match the circuit")
    assert c.status == "not_applicable" and "no firmware file and the circuit has no board" in c.measured


def test_no_cad_file_or_the_blank_one_is_not_applicable_but_an_empty_design_is_not_run(tmp_path):
    d = variant(tmp_path, "nocad", {"elec.py": [('cad="model.py",', '')]}, drop=("model.py",))
    c = one(run(d)[0], "boards and modules fit their CAD mounts")
    assert c.status == "not_applicable" and "the project has no CAD file" in c.measured
    d = variant(tmp_path, "blank", add={"model.py": W.BLANKS["cad"]})
    c = one(run(d)[0], "boards and modules fit their CAD mounts")
    assert c.status == "not_applicable" and "model.py is the blank CAD file" in c.measured
    d = variant(tmp_path, "empty", add={"model.py": "from eitri import *\n\nPARAMS = dict(size=10)\n\ndef build(p):\n    return {}\n"})
    c = one(run(d)[0], "boards and modules fit their CAD mounts")
    assert c.status == "not_run" and "builds no bodies" in c.measured


def test_the_export_rule_skips_not_applicable_and_still_blocks_not_run(tmp_path):
    from dataclasses import asdict
    from types import SimpleNamespace
    d = variant(tmp_path, "box")
    checks, _ = run(d)
    rows = [{**asdict(c), "status": c.status} for c in checks]

    class P:
        folder, history = d, [SimpleNamespace(number=1, model="elec.py", ok=True, summary="", note="", created=0.0)]
        reload = lambda self: None
        model_files = lambda self: ["elec.py"]
        build_result = lambda self, rev, history=None: {"report": {"checks": rows}}
    blocked = W.blocking(P())
    assert not any(r["name"].startswith(("actuators", "dissipation")) for r in blocked)
    assert blocked == []                                                       # every check ran and passes, or does not apply
    rows.append({**rows[0], "name": "a check that did not run", "status": "not_run", "measured": "not run: x", "severity": "warn"})
    assert any(r["name"] == "a check that did not run" for r in W.blocking(P()))


# ---------------------------------------------------------------------------- mounting outlines: Adafruit 326 OLED, Adafruit 2652 BME280

def test_the_oled_and_the_bme280_have_sourced_outlines_and_the_box_mounts_both(tmp_path):
    from eitri_suite import elec_sources as es
    for key, product in (("SSD1306", "Adafruit 326"), ("BME280-2652", "Adafruit 2652")):
        ol = es.outline_for(key)
        assert product in ol["title"] and "STEMMA QT" in ol["title"] and "clone" in ol["title"]
        for sid in (ol["src"], ol["hole_src"], *ol["also"]):
            assert es.SOURCES[sid]["url"].startswith("https://") and es.SOURCES[sid]["fetched"] == "2026-11-18"
    assert es.outline_for("SSD1306")["size"] == (29.21, 31.75, 1.6)            # the ears' bounding box, larger than the page's 26.7
    assert es.outline_for("BME280-2652")["size"] == (25.4, 18.0, 1.6)          # the larger of the board file and the product page
    checks, _ = run(variant(tmp_path, "box"))
    for part in ("OLED", "BME280"):
        for what in ("hole pattern matches the CAD", "outline is clear of the CAD bodies", "clearance to the CAD bodies"):
            c = one(checks, f"mounting: {part} {what}")
            assert c.severity == "pass", (c.name, c.measured)
    assert "Adafruit 326" in one(checks, "mounting: OLED hole pattern").measured


def test_an_oled_mount_off_the_board_s_holes_fails(tmp_path):
    d = variant(tmp_path, "offset", {"model.py": [("oled_hx=12.065,", "oled_hx=12.0,")]})
    c = one(run(d)[0], "mounting: OLED hole pattern")
    assert c.severity == "fail" and "4 of 4 OLED holes" in c.measured


def test_the_plain_bme280_has_no_outline_and_says_which_parts_do(tmp_path):
    d = variant(tmp_path, "plain", {"elec.py": [('c.module("BME280", "BME280-2652", address=0x77)', 'c.module("BME280", "BME280", address=0x77)'),
                                                ('bme["VIN"]', 'bme["VCC"]'), ('bme["SDI"]', 'bme["SDA"]'), ('bme["SCK"]', 'bme["SCL"]')]})
    c = one(run(d)[0], "mounting: BME280 fits")
    assert c.status == "not_run" and "no published outline and hole pattern for BME280" in c.measured
    assert "SSD1306" in c.measured and "BME280-2652" in c.measured


def test_the_adafruit_2652_module_is_that_board_electrically():
    from eitri.electronics.catalog import MODULES
    from eitri.electronics.sources import SOURCES
    m = MODULES["BME280-2652"]
    pins = m["pins"]()
    assert pins["VIN"].v_range == (3.0, 5.0) and abs(pins["VIN"].current_A - (0.000714 + 0.000080 + 0.00033)) < 1e-9
    assert pins["SCK"].pullup_ohm == pins["SDI"].pullup_ohm == 10_000 and "i2c_sda" in pins["SDI"].roles
    assert m["spec"]["address"] == 0x77 and "Adafruit 2652" in m["title"]
    assert "AP2112K" in SOURCES["adafruit-2652"].quote and "80 max" in SOURCES["diodes-ap2112"].quote
