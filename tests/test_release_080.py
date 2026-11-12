"""0.8.0 integration: "not run" as a check status, the registry entry point and files, the public names hosts use."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from eitri.checks import check, checks_not_run, from_verify, status_of, switched_off
from eitri.model import CheckResult

ROOT = Path(__file__).resolve().parents[1]


def test_not_run_is_a_status_and_the_text_form_still_reads():
    c = switched_off(check("protection", "F1 interrupting rating", "warn", "no short-circuit current"), "PSU.short_circuit_A")
    assert c.status == "not_run" and c.severity == "warn" and c.ok
    assert c.line().startswith("[WARN] F1 interrupting rating: not run: ")          # what older readers parse
    assert check("x", "a", "fail", "m").status == "fail" and check("x", "a", "warn", "m").status == "warn"
    failed = switched_off(check("x", "b", "fail", "m"), "k")
    assert failed.status == "fail"                                                    # a failed check still fails
    # the JSON form: written by every report, derived for an older build that has no "status"
    assert status_of({"severity": "warn", "measured": "not run: x"}) == "not_run"
    assert status_of({"ok": True, "measured": "fine"}) == "pass" and status_of({"ok": False, "measured": "not run: x"}) == "fail"
    assert status_of({"status": "not_run"}) == "not_run"
    row = checks_not_run([c])[0]
    assert row["status"] == "not_run" and row["inputs"] == ["PSU.short_circuit_A"]


def test_cad_checks_can_be_not_run_and_keep_their_rule():
    from eitri.reference import _res
    r = _res("cad.reference-clearance", "clearance to reference motor", True, "no clearance= given", "clearance=",
             not_run=("motor.clearance",))
    assert isinstance(r, CheckResult) and r.status == "not_run" and r.rule == "cad.reference-clearance"
    assert r.line().startswith("[WARN] clearance to reference motor: not run: ")
    assert checks_not_run([r])[0]["category"] == "verify"            # a CAD check row (no category of its own)
    v = from_verify(r)
    assert v.status == "not_run" and v.rule == "cad.reference-clearance"
    from eitri.model import Checker
    ck = Checker()
    ck.at_least("edge", 5, 4)
    assert ck.results[0].rule == "verify" and ck.results[0].status == "pass"


def test_report_json_carries_status(tmp_path):
    from eitri.harness import Harness  # noqa: F401  (the domain imports)
    import importlib.util
    from eitri.harness.report import analyze
    from eitri.model import Params
    spec = importlib.util.spec_from_file_location("_sa", ROOT / "eitri/demos/servo-arm/servo_arm_harness.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = analyze(mod.build(Params(mod.PARAMS))).to_dict()
    assert {c["status"] for c in d["checks"]} <= {"pass", "warn", "fail", "not_run"}
    assert [c["name"] for c in d["checks"] if c["status"] == "not_run"] == [x["name"] for x in d["checks_not_run"]] == ["F1 interrupting rating"]


def test_eitri_hardware_command_starts_the_mcp_server(monkeypatch):
    import eitri.cli
    import eitri_hardware
    seen = []
    monkeypatch.setattr(eitri.cli, "main", lambda argv: seen.append(list(argv)) or 0)
    monkeypatch.delenv("EITRI_LAZY_IMPORT", raising=False)
    assert eitri_hardware.main([]) == 0 and seen[-1] == ["mcp"]
    import os
    assert os.environ["EITRI_LAZY_IMPORT"] == "1"
    eitri_hardware.main(["mcp"])
    eitri_hardware.main(["doctor", "--json"])
    assert seen[-2:] == [["mcp"], ["doctor", "--json"]]
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts == {"eitri": "eitri.cli:main", "eitri-hardware": "eitri_hardware:main"}


def test_registry_files():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"^<!-- mcp-name: io\.github\.SiddhuYen/eitri -->$", readme, re.M)
    glama = json.loads((ROOT / "glama.json").read_text(encoding="utf-8"))
    assert glama["$schema"] == "https://glama.ai/mcp/schemas/server.json" and glama["maintainers"] == ["SiddhuYen"]


def test_public_names_for_hosts():
    from eitri.electronics import checks as ec
    assert ec._gate_voltage is ec.gate_voltage and ec._rail_loads is ec.rail_loads and ec._rail_limit is ec.rail_limit
    from eitri.mechanism import Actuator
    assert Actuator("TowerPro MG996R").stall_current()[0] == 2.5
    amps, why = Actuator("SG90").stall_current()
    assert amps == 2.0 and why.startswith("ASSUMPTION")
    assert Actuator("STS3215").stall_current()[0] is None
    assert Actuator("x", stall_A=1.5).stall_current() == (1.5, "the model file (x stall_A)")


def test_3mf_export_leaves_the_process_locale_alone(tmp_path):
    """lib3mf (build123d's Mesher) sets the C locale for the whole process; after a 3MF export the warm worker or the web server
    would then read and write text as ASCII and fail on the next "Ø" or "°". kernel.export_parts restores it."""
    import locale
    from eitri import Box
    from eitri.kernel import export_parts
    from eitri.model import normalize_result
    before = (locale.getpreferredencoding(False), locale.setlocale(locale.LC_CTYPE))
    out = export_parts(normalize_result(Box(4, 3, 5), "plate"), tmp_path, "plate", ("3mf",))
    assert Path(out["3mf"]).stat().st_size > 200
    assert (locale.getpreferredencoding(False), locale.setlocale(locale.LC_CTYPE)) == before
