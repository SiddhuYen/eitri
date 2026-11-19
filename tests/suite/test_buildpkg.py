"""The build package, the new agent tools and the CLI around them."""
from __future__ import annotations

import math

import json
import shutil
from pathlib import Path

import pytest

from eitri_suite.buildpkg import PackageRefused, build_package, wiring_steps
from eitri.harness import Connector, Harness, Wire
from eitri.project import Project

DEMO = Path(__file__).resolve().parents[2] / "eitri_suite" / "demos" / "robot-arm"
NO_FIRMWARE = shutil.ignore_patterns("fw.py", "uno_wiring.py", "__pycache__")       # compiling firmware needs arduino-cli: the sandbox has it (tests/test_domain_builds.py)


@pytest.fixture
def arm(tmp_path):
    folder = tmp_path / "arm"
    shutil.copytree(DEMO, folder, ignore=NO_FIRMWARE)
    return Project(folder, name="arm")


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    folder = tmp_path_factory.mktemp("pkg") / "arm"
    shutil.copytree(DEMO, folder, ignore=NO_FIRMWARE)
    pr = Project(folder, name="arm")
    return pr, build_package(pr), folder / "build_package"


def test_a_passing_project_gets_a_complete_package(package):
    pr, m, out = package
    assert m["passing"] and not m["forced"] and m["checks"]["cad"]["fail"] == 0 and m["checks"]["harness"]["fail"] == 0
    for f in ("README.md", "manifest.json", "bom.json", "bom.md", "bom.csv", "wiring_steps.md", "assembly_order.md", "print/print_manifest.json", "print/print_manifest.md",
              "harness/wiring.cutlist.csv", "harness/wiring.cutlist.md", "harness/wiring.harness.svg", "harness/wiring.png", "harness/wiring.wireviz.yaml", "images/overview.png"):
        assert (out / f).is_file(), f
    assert sorted(m["files"]) == m["files"] and all((out / f).is_file() for f in m["files"])
    assert json.loads((out / "manifest.json").read_text())["project"] == "arm"


def test_the_print_manifest_has_orientation_supports_and_real_stl(package):
    _, m, out = package
    pm = json.loads((out / "print" / "print_manifest.json").read_text())
    names = {p["name"] for p in pm["parts"]}
    assert names == {"base", "turret", "upper_arm", "forearm"}                      # the servos and boards are bought, not printed
    for p in pm["parts"]:
        assert p["orientation"] and len(p["size_mm"]) == 3 and p["supports"] in ("suggested", "not needed") and p["material"] == "pla" and p["mass_g"] > 0
        stl = out / p["stl"]
        assert stl.is_file() and stl.stat().st_size > 200
    assert (out / "print" / "plates").is_dir() and not pm["problems"]
    import trimesh
    mesh = trimesh.load(out / "print" / "parts" / "base.stl")
    assert mesh.is_watertight and abs(mesh.volume - (140 * 90 * 8 - 4 * math.pi * 1.7 ** 2 * 8)) < 1.0   # the base plate less its four M3 bolt holes
    assert "| base |" in (out / "print" / "print_manifest.md").read_text()


def test_bom_in_the_package_matches_the_consolidated_bom(package):
    pr, _, out = package
    from eitri_suite import bom as B
    data = json.loads((out / "bom.json").read_text())
    assert data["totals"] == B.consolidated(pr.folder, pr.model_path, name="arm")["totals"]
    assert "MG996R" in (out / "bom.md").read_text()


def test_wiring_steps_come_from_the_harness_data(package):
    _, _, out = package
    text = (out / "wiring_steps.md").read_text()
    for needle in ("## 1. Cut the wire", "1 x 260 mm of 22 AWG RD: SH_V", "strip 2.5 mm", "crimp Dupont 2.54 crimp contact into PCA_CH1 pos SIG",
                   "pin 21 (SDA)", "Continuity test", "beep ESP32.21 to PCA_I2C.SDA (I2C_SDA)", "First power-on", "net V5: 5 V at the source"):
        assert needle in text, needle
    assert text.count("- [ ] beep") == 16                       # + the supply fuse's lead (PWR_F)


def test_assembly_order_follows_the_kinematic_tree(package):
    _, _, out = package
    text = (out / "assembly_order.md").read_text()
    order = [text.index(f"joint **{j}**") for j in ("yaw", "shoulder", "elbow")]
    assert order == sorted(order) and text.index("the fixed link **base**") < order[0]
    for needle in ("3 x MG996R", "seat the MG996R on turret", "the joint may move -20 to 80 deg", "turret cable hole", "plug PCA_CH0 into pwm_board",
                   "the design needs 1.7 kg·cm", "print upper_arm"):
        assert needle in text, needle


def test_readme_leads_with_the_verdict_and_the_unverified_figures(package):
    _, _, out = package
    text = (out / "README.md").read_text()
    assert text.startswith("# Build package: arm") and "THIS DESIGN FAILS" not in text and "have not been compared with datasheets" in text and "MG996R" in text
    assert "ESTIMATE" in text


def test_a_failing_project_is_refused_and_force_says_so(arm, tmp_path):
    mech = (arm.folder / "mech.py").read_text().replace("payload_g=50.0", "payload_g=600.0")
    (arm.folder / "mech.py").write_text(mech)
    with pytest.raises(PackageRefused) as e:
        build_package(arm)
    assert any("torque joint shoulder" in f for f in e.value.failures) and "[mechanism]" in str(e.value)
    assert not (arm.folder / "build_package").exists()                       # nothing half-written
    m = build_package(arm, force=True)
    assert not m["passing"] and m["forced"]
    readme = (arm.folder / "build_package" / "README.md").read_text()
    assert "THIS DESIGN FAILS ITS CHECKS" in readme and "FAIL [mechanism] torque joint shoulder" in readme.replace("  - ", "")


def test_a_harness_failure_also_refuses(arm):
    wiring = (arm.folder / "wiring.py").read_text().replace("lead_mm=260", "lead_mm=150")      # too short for the cable route
    (arm.folder / "wiring.py").write_text(wiring)
    with pytest.raises(PackageRefused) as e:
        build_package(arm)
    assert any("wire length" in f for f in e.value.failures)


def test_a_project_without_cad_is_refused(tmp_path):
    (tmp_path / "wiring.py").write_text((DEMO / "wiring.py").read_text())
    with pytest.raises(PackageRefused, match="no CAD model"):
        build_package(Project(tmp_path, name="wiring"))


def test_wiring_steps_for_splices_and_other_terminations():
    h = Harness("t")
    a = h.add(Connector("A", "RING", pins=1))
    b = h.add(Connector("B", "XT60", pins=["+", "-"]))
    s = h.splice("S")
    h.net("V", current_A=2, voltage_V=12)
    h.connect(a, s, Wire(14, "RD", 200), net="V", name="IN")
    h.connect(s, b["+"], Wire(14, "RD", 100), net="V", name="OUT")
    text = wiring_steps(h, [{"name": "V", "voltage_V": 12, "drop_pct": 0.5, "drop_V": 0.06, "current_A": 2}])
    assert "S (crimp splice): join IN, OUT" in text and "solder to B + cup" in text and "net V: 12 V at the source; the design expects at most 0.50% (0.060 V)" in text


# ---------------------------------------------------------------------------- tools, prompt, CLI
















SENSOR_BOX = Path(__file__).resolve().parent / "fixtures" / "sensor-box"


def test_the_boards_stand_ins_are_not_counted_as_printable_parts(tmp_path):
    """The electronics file's mounts name the CAD bodies that stand in for the bought boards: the package does not print them."""
    folder = tmp_path / "box"
    shutil.copytree(SENSOR_BOX, folder, ignore=shutil.ignore_patterns("fw.py", "__pycache__"))
    m = build_package(Project(folder, name="model"), force=True)
    readme = (folder / "build_package" / "README.md").read_text(encoding="utf-8")
    assert "Print: 2 printable part(s)" in readme, readme[:600]
    assert "3 bought part(s) left out: controller, oled, bme280" in readme
    pm = json.loads((folder / "build_package" / "print" / "print_manifest.json").read_text(encoding="utf-8"))
    assert {p["name"] for p in pm["parts"]} == {"base", "lid"}
    assert sorted(p.name for p in (folder / "build_package" / "print" / "parts").glob("*.stl")) == ["base.stl", "lid.stl"]
    assert not m["forced"] and "NOT EVERY PART" not in readme          # every check passes: nothing was forced


def test_a_part_whose_mesh_cannot_be_written_is_said_on_the_first_page(tmp_path):
    """A corner boss tangent to two walls leaves edges shared by more than two faces: that part gets no STL, and the README says so."""
    folder = tmp_path / "box"
    shutil.copytree(SENSOR_BOX, folder, ignore=shutil.ignore_patterns("fw.py", "__pycache__"))
    f = folder / "model.py"
    f.write_text(f.read_text(encoding="utf-8").replace(" + 0.5, iy - p.boss_od / 2 + 0.5", ", iy - p.boss_od / 2"), encoding="utf-8")
    build_package(Project(folder, name="model"), force=True)
    readme = (folder / "build_package" / "README.md").read_text(encoding="utf-8")
    assert "NOT EVERY PART COULD BE WRITTEN FOR PRINTING" in readme and "base (" in readme and "shared by more than two faces" in readme


def test_make_plan_leaves_a_stand_in_out_even_when_forced_in():
    import build123d as bd
    from eitri.printexport import PrintSettings, make_plan
    parts = [type("P", (), {"name": n, "shape": bd.Box(10, 10, 2), "color": "#888888"})() for n in ("case", "pcb")]
    plan = make_plan(parts, PrintSettings(stand_ins={"pcb": "the circuit's NANO"}, include=["pcb"], orient=False))
    by = {q.name: q for q in plan.parts}
    assert by["case"].included and not by["pcb"].included and by["pcb"].bought
    assert by["pcb"].bought_reason == "stand-in for the circuit's NANO (bought, not printed)"
    assert plan.summary.startswith("1 printable part(s)")


def test_the_package_carries_the_sketch_it_compiled_and_no_torque_wording_without_actuators(tmp_path):
    """The firmware folder is the sketch exactly as the package's build staged and compiled it; a design that moves nothing gets no
    torque or servo wording on its first page."""
    import hashlib
    folder = tmp_path / "box"
    shutil.copytree(SENSOR_BOX, folder, ignore=shutil.ignore_patterns("__pycache__"))
    m = build_package(Project(folder, name="model"), force=True)       # force: without arduino-cli the compile check fails
    pkg = folder / "build_package"
    fw = m["firmware"]
    assert fw["sketch"] == "climate" and fw["main"] == "climate.ino" and fw["fqbn"] == "arduino:avr:nano"
    assert set(fw["files"]) == {"climate.ino", "eitri_pins.h"}
    for name, sha in fw["files"].items():
        assert hashlib.sha256((pkg / "firmware" / "climate" / name).read_bytes()).hexdigest() == sha
    assert "digitalWrite(PIN_SDA, LOW);" in (pkg / "firmware" / "climate" / "climate.ino").read_text(encoding="utf-8")
    assert "firmware/climate/climate.ino" in m["files"]
    readme = (pkg / "README.md").read_text(encoding="utf-8")
    assert "- `firmware/climate/climate.ino`" in readme and "**Firmware:** `firmware/climate/` is the sketch exactly as" in readme
    assert "compile check: " in readme and "eitri never uploads" in readme
    assert "flash the firmware (firmware/climate/climate.ino)" in (pkg / "assembly_order.md").read_text(encoding="utf-8")
    assert m["actuated"] is False and "torque" not in readme and "servo" not in readme.lower()


def test_a_design_with_actuators_keeps_the_torque_wording(package):
    _pr, m, out = package
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert m["actuated"] is True
    assert "the torque margins in the checks rest on them" in readme and "servo continuous torque" in readme
