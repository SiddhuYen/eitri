"""Reference parts (eitri.reference): a customer's STEP file as read-only geometry a design is built around.

The two fixtures are made by tests/fixtures/reference/make_fixtures.py (no third-party CAD files)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from eitri import reference as R
from eitri.lib.fasteners import ISO_273, METRIC, iso273_clearance
from eitri.model import load_model
from eitri.project import Project

FIX = Path(__file__).parent / "fixtures" / "reference"


class _TTY:
    """A person at /dev/tty: reads back the one-time code they were shown (eitri.tickets.store.terminal_confirm)."""

    def __init__(self):
        self.seen = ""

    def write(self, text):
        self.seen += text

    def flush(self):
        pass

    def readline(self):
        import re as _re
        return _re.findall(r"type this code: (\w+)", self.seen)[-1] + "\n"

    def fileno(self):
        raise OSError

    def close(self):
        pass


@pytest.fixture
def person(tmp_path, monkeypatch):
    """A person in their own terminal (no agent markers, a stand-in /dev/tty), eitri's signing key in a temporary folder:
    reference add / accept / remove are person-only, like `eitri ticket approve`."""
    from eitri.tickets import store
    for k in store.AGENT_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EITRI_CONFIG_DIR", str(tmp_path / "config"))
    store.set_signer(store.LocalKeySigner())
    tty = _TTY()
    monkeypatch.setattr(store, "_open_tty", lambda: tty)
    return tty
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "motor-bracket"


def _folder(tmp_path: Path, *names: str) -> Path:
    (tmp_path / "inputs").mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copyfile(FIX / n, tmp_path / "inputs" / n)
    return tmp_path


# ---------------------------------------------------------------------------- untrusted input

def _good() -> bytes:
    return (FIX / "test_block.step").read_bytes()


@pytest.mark.parametrize("data, says", [
    (b"solid stl\nendsolid\n", "not a STEP file"),
    (b"\x1f\x8b\x08\x00garbage", "compressed"),
    (b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\n", "truncated"),
    (b"ISO-10303-21;\nDATA;\n#1=X(\x00);\nENDSEC;\nEND-ISO-10303-21;\n", "NUL"),
    (b"ISO-10303-21;\nDATA;\n#1 = DOCUMENT_FILE('a','b',$,#2,'c',$);\nENDSEC;\nEND-ISO-10303-21;\n", "external references"),
    (b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n", "no entities"),
])
def test_prescan_refuses_what_is_not_one_plain_step_file(data, says):
    with pytest.raises(R.ReferenceError, match=says):
        R.prescan(data, "f.step")


def test_prescan_caps_size_and_entities(monkeypatch):
    assert R.prescan(_good(), "f") > 100
    monkeypatch.setenv("EITRI_REFERENCE_MAX_ENTITIES", "50")
    with pytest.raises(R.ReferenceError, match="STEP entities"):
        R.prescan(_good(), "f")
    monkeypatch.delenv("EITRI_REFERENCE_MAX_ENTITIES")
    monkeypatch.setenv("EITRI_REFERENCE_MAX_MB", "0.01")
    with pytest.raises(R.ReferenceError, match="at most"):
        R.prescan(_good(), "f")


def test_reference_files_must_sit_in_inputs(tmp_path):
    folder = _folder(tmp_path, "test_block.step")
    assert R.resolve_path("test_block.step", folder)[1] == "inputs/test_block.step"
    assert R.resolve_path("inputs/test_block.step", folder)[1] == "inputs/test_block.step"
    (folder / "parts").mkdir()
    shutil.copyfile(FIX / "test_block.step", folder / "parts" / "b.step")
    for bad in ("parts/b.step", "../test_block.step", "inputs/../parts/b.step", str(folder / "inputs" / "test_block.step"),
                "inputs/missing.step", "inputs/model.py", "inputs/a b<x>.step"):
        with pytest.raises(R.ReferenceError):
            R.resolve_path(bad, folder)
    (folder / "inputs" / "link.step").symlink_to(folder / "parts" / "b.step")
    with pytest.raises(R.ReferenceError, match="symbolic link"):
        R.resolve_path("inputs/link.step", folder)


def test_a_malformed_file_fails_the_build_cleanly(tmp_path):
    folder = _folder(tmp_path)
    data = _good()
    (folder / "inputs" / "bad.step").write_bytes(data[: len(data) // 2] + b"\nENDSEC;\nEND-ISO-10303-21;\n")   # cut mid-DATA
    (folder / "m.py").write_text('from eitri import *\n\ndef build(p):\n    reference_step("inputs/bad.step")\n    return Box(1, 1, 1)\n')
    with pytest.raises(R.ReferenceError, match="bad.step.* is malformed"):
        load_model(folder / "m.py").build()
    with pytest.raises(R.ReferenceError, match="unbalanced"):
        R.prescan(b"ISO-10303-21;\nDATA;\n#1=A((1,2);\nENDSEC;\nEND-ISO-10303-21;\n", "f")


# ---------------------------------------------------------------------------- the helpers

def test_features_bbox_and_mass_of_the_fixtures(tmp_path):
    folder = _folder(tmp_path, "test_motor.step", "test_block.step")
    R.begin(folder)
    motor = R.reference_step("inputs/test_motor.step", name="motor")
    block = R.reference_step("test_block.step", name="block", at=(100, 0, 0), density=2.7)
    assert len(R.take()) == 2
    bb = motor.bbox
    assert (round(bb.min.X, 3), round(bb.max.Z, 3), round(bb.min.Z, 3)) == (-21, 22, -40)
    holes = motor.holes(axis="Z")
    assert [h.diameter for h in holes] == [2.5] * 4 and all(h.through is False and h.depth == 4.5 for h in holes)
    assert sorted((h.center[0], h.center[1]) for h in holes) == [(-15.5, -15.5), (-15.5, 15.5), (15.5, -15.5), (15.5, 15.5)]
    assert sorted(b.diameter for b in motor.bosses()) == [5, 22]
    assert motor.hole(near=(15, 15, 0)).center[:2] == (15.5, 15.5)
    with pytest.raises(R.ReferenceError, match="density"):
        motor.mass_properties()
    assert sorted((h.axis_name, h.diameter, h.through) for h in block.holes()) == [("X", 6, False), ("Z", 4, True), ("Z", 4, True)]
    assert block.hole(near=(118, 0, 0)).center == (116, 0, 0)                         # placed with at=
    mp = block.mass_properties()
    assert mp["mass_g"] == pytest.approx(block.volume / 1000 * 2.7, abs=1e-3)
    assert motor.shape is not motor.shape                                               # a copy each time


def test_iso273_table_and_the_screw_clearance_table_agree():
    for size, (fine, medium, coarse) in ISO_273.items():
        if size in METRIC:
            assert (METRIC[size]["close"], METRIC[size]["normal"], METRIC[size]["loose"]) == (fine, medium, coarse), size
    assert iso273_clearance("M3", "medium") == 3.4 and iso273_clearance("m12", "coarse") == 14.5
    with pytest.raises(KeyError):
        iso273_clearance("M7")
    with pytest.raises(ValueError):
        iso273_clearance("M3", "snug")


def test_edits_never_touch_a_reference(tmp_path):
    folder = _folder(tmp_path, "test_block.step")
    (folder / "m.py").write_text('''from eitri import *

def build(p):
    reference_step("inputs/test_block.step", name="block")
    return {"plate": Pos(0, 0, 20) * Box(40, 20, 4)}

EDITS = [
    {"op": "delete_part", "part": "block"},
    {"op": "move", "part": "block", "delta": [5, 0, 0]},
    {"op": "combine", "part": "plate", "tool": "block", "mode": "cut"},
    {"op": "move", "part": "plate", "delta": [1, 0, 0]},
]
''')
    model = load_model(folder / "m.py")
    parts = model.build()
    notes = model.edit_notes
    assert [n.ok for n in notes] == [False, False, False, True]
    assert all("reference part" in n.detail for n in notes[:3])
    assert "block" in parts and parts.references[0]["name"] == "block"
    assert R._same_signature(R.signature(parts["block"]), parts.references[0]["signature"])


# ---------------------------------------------------------------------------- the whole loop, in a project

BAD = '''from eitri import *

PARAMS = dict(hole_dx=0.0, hole_d=4.5, base_gap=0.5, overlap=1.0)

def build(p):
    blk = reference_step("inputs/test_block.step", name="block", clearance=1.0, contact=["clamp"])
    holes = blk.holes(axis="Z")
    clamp = Pos(0, 0, 5 + 3 - p.overlap) * Box(40, 20, 6)         # sits on the block's top (overlap > 0 digs in)
    for h in holes:
        clamp -= Pos(h.center[0] + p.hole_dx, h.center[1], 8) * Cylinder(p.hole_d / 2, 20)
        blk.mount(h, part="clamp", screw="M4", series="medium", coaxial_tol=0.1)
    clamp -= Pos(0, 0, 8) * Cylinder(5, 20)                     # room for the Ø8 boss
    side = Pos(20 + p.base_gap + 2, 0, 0) * Box(4, 20, 10)         # a neighbour that must keep 1 mm away
    return {"clamp": clamp, "side": side}
'''


def test_project_records_the_hash_and_the_checks_catch_a_bad_design(tmp_path):
    folder = _folder(tmp_path / "job", "test_block.step")
    (folder / "clamp.py").write_text(BAD)
    pr = Project(folder, name="clamp")
    res = pr.build(note="bad", render=False)
    assert res.error is None, res.error
    checks = {c.name: c for c in res.report.checks}
    bad = sorted(n for n, c in checks.items() if not c.ok)
    assert any(n.startswith("no designed part overlaps reference block") for n in bad), bad
    assert any(n.startswith("clearance to reference block >= 1 mm") for n in bad), bad
    assert not any("mount" in n for n in bad), bad                   # Ø4.5 coaxial holes: ISO 273 medium for M4
    rec = pr.references()["inputs/test_block.step"]
    assert rec["by"] == "first build" and rec["mounts"] == 2 and len(rec["sha256"]) == 64
    assert rec["sha256"] == R.sha256_file(folder / "inputs" / "test_block.step")[0]
    part = next(p for p in res.report.parts if p.name == "block")
    assert part.reference == "inputs/test_block.step" and part.min_wall is None


def test_fixing_the_design_turns_every_reference_check_green(tmp_path):
    folder = _folder(tmp_path / "job", "test_block.step")
    (folder / "clamp.py").write_text(BAD)
    pr = Project(folder, name="clamp")
    res = pr.build(note="good", render=False, params={"hole_dx": "0.3", "hole_d": "4.4", "base_gap": "1.5", "overlap": "0"})
    assert res.error is None, res.error
    failed = [c.line() for c in res.report.checks if not c.ok]
    assert sorted(f.split(":")[1].strip() for f in failed) == ["clamp hole coaxial", "clamp hole coaxial"], failed
    res = pr.build(note="good", render=False, params={"hole_d": "4.5", "base_gap": "1.5", "overlap": "0"})
    assert res.error is None, res.error
    assert [c.line() for c in res.report.checks if not c.ok] == []
    names = [c.name for c in res.report.checks]
    assert sum("coaxial" in n for n in names) == 2 and sum("ISO 273 medium" in n for n in names) == 2


def test_a_changed_file_fails_until_a_person_accepts_it(tmp_path, capsys, person):
    from eitri.cli import main as cli
    folder = _folder(tmp_path / "job", "test_block.step")
    (folder / "clamp.py").write_text(BAD.replace("blk.mount(h, ", "pass  # blk.mount(h, "))
    pr = Project(folder, name="clamp")
    assert pr.build(note="first", render=False).error is None
    f = folder / "inputs" / "test_block.step"
    f.write_bytes(f.read_bytes().replace(b"test_block", b"test_blocc", 1))           # still a valid STEP file
    res = pr.build(note="changed", render=False)
    assert not res.ok and "CHANGED since it was accepted" in res.error and "eitri reference accept" in res.error
    assert cli(["reference", "list", str(folder)]) == 1
    assert "CHANGED" in capsys.readouterr().out
    assert cli(["reference", "accept", str(folder), "inputs/test_block.step", "--by", "Pat Person"]) == 0
    assert "type this code" in person.seen and "inputs/test_block.step" in person.seen        # the person saw what they accept
    rec = Project(folder).references()["inputs/test_block.step"]
    assert rec["by"] == "Pat Person" and rec["person"]["signature"] and rec["person"]["method"] == "cli-terminal"
    assert Project(folder).reference_acceptance("inputs/test_block.step") == (f"Pat Person on {rec['person']['at'][:10]}", True)
    pr.reload()
    assert pr.build(note="accepted", render=False).error is None
    assert cli(["reference", "list", str(folder)]) == 0


def test_a_reference_cannot_be_dropped_and_direct_edits_on_it_are_refused(tmp_path, person):
    from eitri.cli import main as cli
    folder = _folder(tmp_path / "job", "test_block.step")
    (folder / "clamp.py").write_text(BAD)
    pr = Project(folder, name="clamp")
    assert pr.build(note="first", render=False).error is None
    with pytest.raises(ValueError, match="reference part"):
        pr.add_edit({"op": "push_pull", "part": "block", "face": {"normal": [0, 0, 1]}, "distance": 2})
    pr.write_model(BAD.replace("        blk.mount(h, ", "        pass  # "))                  # fewer mounts: kept
    res = pr.build(note="fewer mounts", render=False)
    assert res.error is None
    assert [c.name for c in res.report.checks if not c.ok and "kept" in c.name] == ["reference block: declared mounting checks are kept"]
    pr.write_model(BAD.replace('    blk = reference_step("inputs/test_block.step", name="block", clearance=1.0, contact=["clamp"])\n',
                               "    from types import SimpleNamespace as _N\n    blk = _N(holes=lambda axis=None: [], mount=lambda *a, **k: None)\n"))
    res = pr.build(note="dropped", render=False)
    assert not res.ok and "no longer declares the reference" in res.error
    assert cli(["reference", "remove", str(folder), "inputs/test_block.step"]) == 0
    pr.reload()
    assert pr.build(note="removed by the person", render=False).error is None


def test_cli_add_copies_and_accepts(tmp_path, capsys, person):
    from eitri.cli import main as cli
    folder = tmp_path / "job"
    Project(folder, name="m")
    assert cli(["reference", "add", str(folder), str(FIX / "test_motor.step"), "--name", "motor"]) == 0
    assert (folder / "inputs" / "motor.step").read_bytes() == (FIX / "test_motor.step").read_bytes()
    assert Project(folder).references()["inputs/motor.step"]["person"]["signature"]
    assert 'reference_step("inputs/motor.step", name="motor")' in capsys.readouterr().out
    bad = tmp_path / "bad.step"
    bad.write_bytes(b"not a step file")
    assert cli(["reference", "add", str(folder), str(bad)]) == 1


def test_example_motor_bracket_builds_green_and_exports_the_motor(tmp_path):
    folder = tmp_path / "motor-bracket"
    shutil.copytree(EXAMPLE, folder)
    pr = Project(folder, name="motor_bracket")
    res = pr.build(note="example", render=False, formats=("step",))
    assert res.error is None, res.error
    assert [c.line() for c in res.report.checks if not c.ok] == []
    assert sum("coaxial" in c.name for c in res.report.checks) == 4
    data = json.loads((folder / "out" / "result.json").read_text())
    assert [r["name"] for r in data["references"]] == ["motor"]
    import build123d as bd
    labels = sorted(c.label for c in bd.import_step(res.outputs["step"]).children)
    assert labels == ["base", "bracket", "motor"]
    from eitri.model import load_model as _lm
    from eitri.printexport import make_plan
    plan = make_plan(_lm(folder / "motor_bracket.py").build())
    motor = next(q for q in plan.parts if q.name == "motor")
    assert not motor.included and "reference part" in motor.bought_reason


def test_accepted_hashes_apply_only_to_their_own_project(tmp_path):
    a = _folder(tmp_path / "a", "test_block.step")
    b = _folder(tmp_path / "b", "test_block.step")
    R.set_accepted({"inputs/test_block.step": {"sha256": "0" * 64}}, a)     # a warm worker built project a last
    R.begin(b)
    R.reference_step("inputs/test_block.step")                              # project b is not judged by a's list
    R.take()
    R.begin(a)
    with pytest.raises(R.ReferenceError, match="CHANGED since it was accepted"):
        R.reference_step("inputs/test_block.step")
    R.take()
    R.set_accepted({})


def test_accept_add_and_remove_are_person_only_like_ticket_approve(tmp_path, monkeypatch, capsys):
    """The same barriers as `eitri ticket approve`: refused in an agent's process (its markers), refused without a terminal, a
    hand-edited acceptance record does not verify, and nothing is recorded or copied when refused."""
    import subprocess
    import sys
    from eitri.cli import main as cli
    from eitri.tickets import store
    monkeypatch.setenv("EITRI_CONFIG_DIR", str(tmp_path / "config"))
    folder = _folder(tmp_path / "job", "test_block.step")
    (folder / "clamp.py").write_text(BAD)
    pr = Project(folder, name="clamp")
    assert pr.build(note="first", render=False).error is None             # first use records the hash (unsigned: no person)
    before = json.dumps(Project(folder).references(), sort_keys=True)
    for k in store.AGENT_ENV:
        monkeypatch.delenv(k, raising=False)
    for marker in ("EITRI_AGENT_PROCESS", "CLAUDECODE"):
        monkeypatch.setenv(marker, "1")
        with pytest.raises(store.NotAPerson, match=marker):
            pr.accept_reference("inputs/test_block.step", confirm=lambda a, b: {})
        with pytest.raises(store.NotAPerson, match=marker):
            pr.remove_reference("inputs/test_block.step", confirm=lambda a, b: {})
        assert cli(["reference", "accept", str(folder), "inputs/test_block.step"]) == 1
        assert cli(["reference", "add", str(folder), str(FIX / "test_motor.step"), "--name", "motor"]) == 1
        assert not (folder / "inputs" / "motor.step").exists()              # a refused add copies nothing
        monkeypatch.delenv(marker)
    assert "refused" in capsys.readouterr().err
    with pytest.raises(store.NotAPerson, match="interactive terminal"):  # pytest's stdin is not a terminal
        pr.accept_reference("inputs/test_block.step")
    env = {k: v for k, v in __import__("os").environ.items() if k not in store.AGENT_ENV}
    proc = subprocess.run([sys.executable, "-m", "eitri", "reference", "accept", str(folder), "inputs/test_block.step"], input="",
                          capture_output=True, text=True, env={**env, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                                                               "EITRI_LAZY_IMPORT": "1"}, timeout=300)
    assert proc.returncode == 1 and "terminal" in proc.stderr, proc.stderr
    assert json.dumps(Project(folder).references(), sort_keys=True) == before
    # a person accepts; then a hand edit of the record (an agent's own Write tool) no longer verifies
    tty = _TTY()
    monkeypatch.setattr(store, "_open_tty", lambda: tty)
    store.set_signer(store.LocalKeySigner())
    f = folder / "inputs" / "test_block.step"
    f.write_bytes(f.read_bytes().replace(b"test_block", b"test_blocc", 1))
    pr.accept_reference("inputs/test_block.step", by="Pat Person")
    assert pr.reference_acceptance("inputs/test_block.step")[1] is True
    st = json.loads((folder / "eitri.json").read_text())
    st["references"]["clamp.py"]["inputs/test_block.step"]["sha256"] = "0" * 64
    (folder / "eitri.json").write_text(json.dumps(st))
    assert Project(folder).reference_acceptance("inputs/test_block.step")[1] is False
