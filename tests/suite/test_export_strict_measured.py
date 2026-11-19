"""0.9.3, Siddharth's decisions of 2026-11-18 (Option A for both):

1. strict mesh: a printable part whose mesh cannot be written for printing is a failing check of every CAD build of a combined project
   (eitri_suite.printcheck), so combined_build shows it and an export or a package refuses it like any FAIL;
2. measured board sizes: a mount may declare the person's own measurements (COUPLING mounts[...] measured=); the mounting checks then
   run on them with the status "assumed": the free combined export passes, a change package refuses until a person waives it, and
   every place says "board size measured by the user, not from a datasheet". Undeclared, an outline-less module stays not run."""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from eitri.checks import MEASURED_BY_USER
from eitri.model import load_model
from eitri_suite import projectchecks
from eitri_suite import workspace as W

BOX = Path(__file__).resolve().parent / "fixtures" / "sensor-box"
TANGENT = (" + 0.5, iy - p.boss_od / 2 + 0.5", ", iy - p.boss_od / 2")
MEASURED = 'measured=dict(size=(25.4, 18.0, 1.6), hole_spacing=(20.32, 12.7), hole_d=2.5, by="calipers on my board, 2026-11-18")'
PLAIN_BME = [('c.module("BME280", "BME280-2652", address=0x77)', 'c.module("BME280", "BME280", address=0x77)'),
             ('bme["VIN"]', 'bme["VCC"]'), ('bme["SDI"]', 'bme["SDA"]'), ('bme["SCK"]', 'bme["SCL"]')]
PLAIN_BME_HARNESS = [('pins=["VIN", "GND", "SCK", "SDI"]', 'pins=["VCC", "GND", "SCL", "SDA"]'),
                     ('bme["VIN"]', 'bme["VCC"]'), ('bme["SDI"]', 'bme["SDA"]'), ('bme["SCK"]', 'bme["SCL"]')]
MOUNT = 'carrier=["base"], screw="M2.5", clearance_mm=0.5)'


def variant(tmp_path: Path, name: str, edits: dict | None = None, drop=("fw.py",)) -> Path:
    d = tmp_path / name
    shutil.copytree(BOX, d, ignore=shutil.ignore_patterns("__pycache__", *drop))
    for f, subs in (edits or {}).items():
        text = (d / f).read_text(encoding="utf-8")
        for a, b in subs:
            assert a in text, (f, a)
            text = text.replace(a, b)
        (d / f).write_text(text, encoding="utf-8")
    return d


def cad_checks(folder: Path):
    lm = load_model(folder / "model.py")
    return projectchecks.extras("cad", lm.module, lm.build(), folder / "model.py")[0]


def elec_checks(folder: Path):
    lm = load_model(folder / "elec.py")
    return projectchecks.extras("electronics", lm.module, lm.build(), folder / "elec.py")[0]      # the build's entry: rules attached


def blocking_for(folder: Path, file: str, checks) -> list[dict]:
    rows = [{**asdict(c), "status": c.status} for c in checks]

    class P:
        history = [SimpleNamespace(number=1, model=file, ok=True, summary="", note="", created=0.0)]
        reload = lambda self: None
        model_files = lambda self: [file]
        build_result = lambda self, rev, history=None: {"report": {"checks": rows}}
    P.folder = folder
    return W.blocking(P())


# ---------------------------------------------------------------------------- 1. strict mesh

def test_a_tangent_boss_fails_the_mesh_check_and_says_where_and_how_to_fix(tmp_path):
    checks = cad_checks(variant(tmp_path, "tangent", {"model.py": [TANGENT]}))
    c = next(c for c in checks if c.name == "print: base mesh can be written for printing")
    assert c.status == "fail" and c.rule == "suite.print-mesh" and c.source
    assert "8 edge(s) shared by more than two faces" in c.measured and "at 8 place(s): near (" in c.measured
    assert "overlap the bodies by about 0.5 mm, or separate them" in c.measured and "would write no STL or 3MF" in c.measured
    names = {c.name for c in checks if c.name.startswith("print:")}
    assert names == {"print: base mesh can be written for printing", "print: lid mesh can be written for printing"}  # stand-ins excluded
    blocked = blocking_for(tmp_path / "tangent", "model.py", checks)
    assert [r["name"] for r in blocked if r["status"] == "fail"] == ["print: base mesh can be written for printing"]


def test_a_tangent_boss_refuses_the_package_and_a_half_millimetre_overlap_packages(tmp_path):
    from eitri.project import Project
    from eitri_suite.buildpkg import PackageRefused, build_package
    bad = variant(tmp_path, "tangent", {"model.py": [TANGENT]})
    with pytest.raises(PackageRefused, match="print: base mesh can be written for printing"):
        build_package(Project(bad, name="model"))
    good = variant(tmp_path, "overlap")                        # the fixture's bosses run 0.5 mm into the walls
    assert all(c.status == "pass" for c in cad_checks(good) if c.name.startswith("print:"))
    m = build_package(Project(good, name="model"))
    assert m["passing"] and not m["print"]["problems"]
    assert (good / "build_package" / "print" / "parts" / "base.stl").is_file()


# ---------------------------------------------------------------------------- 2. measured board sizes

def test_an_undeclared_outline_less_module_stays_not_run_and_says_how_to_declare(tmp_path):
    checks = elec_checks(variant(tmp_path, "plain", {"elec.py": PLAIN_BME, "wiring.py": PLAIN_BME_HARNESS}))
    c = next(c for c in checks if c.name == "mounting: BME280 fits its CAD mount")
    assert c.status == "not_run" and "measured=dict(size=(L, W, T)" in c.measured
    assert not any(c.status == "assumed" for c in checks)


def test_a_declared_measurement_is_assumed_says_so_and_passes_the_free_export(tmp_path):
    d = variant(tmp_path, "measured", {"elec.py": PLAIN_BME + [(MOUNT, MOUNT[:-1] + ",\n            " + MEASURED + ")")],
                                       "wiring.py": PLAIN_BME_HARNESS})
    checks = elec_checks(d)
    mine = [c for c in checks if c.name.startswith("mounting: BME280")]
    assert {c.name for c in mine} == {"mounting: BME280 hole pattern matches the CAD", "mounting: BME280 outline is clear of the CAD bodies",
                                      "mounting: BME280 clearance to the CAD bodies"}
    for c in mine:
        assert c.status == "assumed" and c.ok, (c.name, c.measured)
        assert c.measured.startswith(f"{MEASURED_BY_USER} (ASSUMPTION; calipers on my board, 2026-11-18): ")
        assert c.line().startswith(f"[ASSUMED] {c.name}: {MEASURED_BY_USER}")
        assert any(s.basis == "assumption" for s in c.source)
    assert all(c.status != "assumed" for c in checks if not c.name.startswith("mounting: BME280"))
    assert not [r for r in blocking_for(d, "elec.py", checks) if r["name"].startswith("mounting: BME280")]


def test_a_measurement_that_does_not_match_the_cad_still_fails(tmp_path):
    d = variant(tmp_path, "off", {"elec.py": PLAIN_BME + [(MOUNT, MOUNT[:-1] + ",\n            " + MEASURED.replace("20.32", "19.0") + ")")],
                                  "wiring.py": PLAIN_BME_HARNESS})
    c = next(c for c in elec_checks(d) if c.name == "mounting: BME280 hole pattern matches the CAD")
    assert c.status == "fail" and MEASURED_BY_USER in c.measured


@pytest.mark.parametrize("bad, says", [
    ('measured=dict(size=(25.4, 18.0), hole_spacing=(20.32, 12.7), hole_d=2.5, by="x y z")', "numbers in mm"),
    ('measured=dict(size=(25.4, 18.0, 1.6), hole_spacing=(20.32, 12.7), hole_d=2.5)', "missing by"),
    ('measured=dict(size=(25.4, 18.0, 1.6), hole_d=2.5, by="calipers")', "missing hole_spacing (or holes)"),
    ('measured=dict(size=(25.4, 18.0, 1.6), hole_spacing=(40, 12.7), hole_d=2.5, by="calipers")', "do not fit inside"),
])
def test_an_incomplete_measurement_fails_and_asks_for_the_figure(tmp_path, bad, says):
    d = variant(tmp_path, "bad", {"elec.py": PLAIN_BME + [(MOUNT, MOUNT[:-1] + ",\n            " + bad + ")")], "wiring.py": PLAIN_BME_HARNESS})
    c = next(c for c in elec_checks(d) if c.name == "mounting: BME280 fits its CAD mount")
    assert c.status == "fail" and says in c.measured


def test_the_build_package_says_measured_on_its_first_page(tmp_path):
    from eitri.project import Project
    from eitri_suite.buildpkg import build_package
    d = variant(tmp_path, "measured", {"elec.py": PLAIN_BME + [(MOUNT, MOUNT[:-1] + ",\n            " + MEASURED + ")")],
                                       "wiring.py": PLAIN_BME_HARNESS})
    m = build_package(Project(d, name="model"))
    first = (d / "build_package" / "README.md").read_text(encoding="utf-8").split("## Contents")[0]
    assert "**BOARD SIZE MEASURED BY THE USER, NOT FROM A DATASHEET.**" in first and "mounting: BME280 hole pattern" in first
    assert "(3 on a board size measured by the user, not from a datasheet)" in first
    assert m["checks"]["electronics"]["assumed"] == 3


# ---------------------------------------------------------------------------- the change package refuses it until a person waives it

@pytest.fixture
def person(tmp_path, monkeypatch):
    from eitri import tickets as T
    for k in T.store.AGENT_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EITRI_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("EITRI_NO_WARM", "1")
    T.set_signer(T.store.LocalKeySigner())
    return T


def test_a_change_package_refuses_a_measured_board_until_a_person_waives_it(tmp_path, person, monkeypatch):
    """The single-file package: it rebuilds its one file alone, so here the staging build gets the project's other files and the
    cross-domain checks run; the refusal rule itself is what is tested. A real combined project's package (every file rebuilt
    together, the same rule) is tests/test_tickets_combined.py::test_combined_package_refuses_assumed_until_waived_and_na_blocks_nothing."""
    from eitri.agent.tools import Tools
    from eitri.project import Project
    from eitri.tickets import package as pkg_mod
    T = person
    real = pkg_mod._fresh_build

    def with_siblings(project, source, file, folder, guards, cad):
        folder.mkdir(parents=True, exist_ok=True)
        for f in Path(project.folder).glob("*.py"):
            if f.name != file:
                shutil.copy2(f, folder / f.name)
        return real(project, source, file, folder, guards, cad)
    monkeypatch.setattr(pkg_mod, "_fresh_build", with_siblings)
    d = variant(tmp_path, "pkg", {"elec.py": PLAIN_BME + [(MOUNT, MOUNT[:-1] + ",\n            " + MEASURED + ")"),
                                                          ("PARAMS = dict()", "PARAMS = dict(max_draw=0.050)"),
                                                          ("0.020 + 0.0012, 0.050)", "0.020 + 0.0012, p.max_draw)")],
                                  "wiring.py": PLAIN_BME_HARNESS}, drop=())
    p = Project(d, name="elec")
    assert p.build("as delivered").ok
    T.new_ticket(p, "Tighter rail budget", "Hold the 3V3 rail draw under 45 mA.", acceptance=["the rail holds => 3V3 rail draw*"])
    T.set_plan(p, "T-0001", {"summary": "max_draw 0.050 -> 0.045.", "parameters": [{"name": "max_draw", "from": 0.05, "to": 0.045}]})
    T.approve(p, "T-0001", by="Pat Person", confirm=lambda text, what: {"tty": "test-terminal"})
    assert not Tools(p).set_params({"max_draw": 0.045}).is_error
    with pytest.raises(T.PackageRefused) as exc:
        T.make_package(p, "T-0001")
    why = str(exc.value)
    assert "ASSUMED mounting: BME280 hole pattern matches the CAD" in why and MEASURED_BY_USER in why and "eitri ticket waive" in why
    for name in ("mounting: BME280 hole pattern matches the CAD", "mounting: BME280 outline is clear of the CAD bodies",
                 "mounting: BME280 clearance to the CAD bodies"):
        T.waive(p, "T-0001", name, "The customer measured their own BME280 board with calipers and accepts the figures",
                by="Pat Person", confirm=lambda text, what: {"tty": "test-terminal"})
    pkg = T.make_package(p, "T-0001")
    raw = json.loads((Path(pkg["folder"]) / "package.json").read_text(encoding="utf-8"))
    assert raw["checks"]["summary"]["to"]["assumed"] == 3 and len(raw["assumed"]) == 3
    assert all(a["what"] == MEASURED_BY_USER and "calipers on my board" in a["declared"] for a in raw["assumed"])
    nc = [x for x in raw["not_checked"] if x["kind"] == "assumed"]
    assert len(nc) == 3 and all(MEASURED_BY_USER in x["why"] and "Pat Person" in x["why"] for x in nc)
    report = (Path(pkg["folder"]) / "report.md").read_text(encoding="utf-8")
    assert MEASURED_BY_USER in report


def test_the_combined_result_counts_the_assumed_checks():
    r = {"build": 1, "project_id": "box", "all_pass": True, "files": [{"file": "elec.py", "kind": "electronics", "built": True, "blank": False,
         "checks": [{"name": "mounting: X hole pattern", "ok": True, "severity": "pass", "status": "assumed", "measured": "m", "expected": "",
                     "cross_domain": True}]}], "seconds": 1, "view": "", "next_steps": ["export"], "cross_domain": {"checks": 1, "failing": 0}}
    text = W.results_text(r)
    assert "0 pass, 0 warn, 0 FAIL, 0 NOT RUN, 1 assumed (board size measured by the user, not from a datasheet)" in text
    assert "EVERY CHECK PASSES (1 of them ASSUMED: measured by the user)" in text and "ASSUMED [cross-domain]" in text
