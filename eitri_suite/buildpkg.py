"""The build package: everything needed to print, wire and assemble a design that passes its checks, as one folder.

A formatter over data eitri already has; no new kernel logic. Given a project it builds the CAD model and the harness (in the build
subprocess, like any build), refuses unless every check passes (warnings are listed, `force=True` writes it anyway with a loud banner),
and writes, under <project>/build_package/ by default:

    README.md              what is in the package, the checks' verdict, the estimates and unverified figures to look at first
    manifest.json          the same, machine-readable, with every file
    print/                 print_manifest.md/.json (per part: material, mass, orientation, supports, plate), parts/*.stl, plates/*.stl
    bom.json  bom.md  bom.csv                the consolidated bill of materials (eitri.bom)
    harness/               cut list, diagram (SVG + PNG), WireViz YAML, the harness BOM
    wiring_steps.md        cut, strip, crimp, splice, insert, label, continuity and power-on steps, from the harness data
    assembly_order.md      parts in kinematic order with their servos, fasteners, cable routes and mating connectors
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from . import bom as bom_mod
from .projectfiles import files_by_kind
from eitri.harness import outputs as hout


class PackageRefused(RuntimeError):
    """The project does not pass its checks, so no package was written (force=True writes one anyway)."""

    def __init__(self, failures: list[str]):
        super().__init__("the project fails its checks, so there is nothing to build yet: " + "; ".join(failures[:6]) + (f" (+{len(failures) - 6} more)" if len(failures) > 6 else ""))
        self.failures = failures


def suite_data(result: dict) -> dict:
    """What the suite extension put in a build's result.json (result["extensions"]["suite"])."""
    return (result.get("extensions") or {}).get("suite") or {}


def _status(c: dict) -> str:
    try:
        from eitri.checks import status_of                 # core 0.8.0: pass | warn | fail | not_run
        return status_of(c)
    except ImportError:                                     # an older core: the severity and the "not run: " text
        if not c.get("ok", True) or c.get("severity") == "fail":
            return "fail"
        return "not_run" if str(c.get("measured", "")).startswith("not run: ") else c.get("severity") or "pass"


def _verdict(checks: list[dict]) -> dict:
    fail = [c for c in checks if _status(c) == "fail"]
    off = [c for c in checks if _status(c) == "not_run"]
    warn = [c for c in checks if _status(c) == "warn"]
    return {"checks": len(checks), "pass": len(checks) - len(fail) - len(warn) - len(off), "warn": len(warn) + len(off), "fail": len(fail),
            "not_run": len(off), "failing": [f"{c['name']}: {c['measured']}" for c in fail],
            "not_run_checks": [f"{c['name']}: {c['measured']}" for c in off],
            "warnings": [f"{c['name']}: {c['measured']}" for c in warn + off]}


def _build(model_path: Path, scratch: Path, label: str, guards: dict | None, render: bool) -> tuple[Any, dict]:
    from eitri.kernel import build_model
    out = scratch / label
    res = build_model(model_path, out, render=render, formats=(), guards=guards or None)
    if not res.ok:
        raise PackageRefused([f"{model_path.name} does not build: {(res.error or '').strip().splitlines()[-1] if res.error else 'unknown error'}"])
    data = json.loads((out / "result.json").read_text(encoding="utf-8"))
    return res, data


# ---------------------------------------------------------------------------- the generated documents

def wiring_steps(h, nets: list[dict] | None = None) -> str:
    """Numbered build steps for a harness: from its cut list, terminations, splices, nets."""
    cut = hout.cutlist(h)
    out = [f"# Wiring steps: {h.name}", "", f"{len(cut)} wires, {len(h.connectors)} connectors, {len(h.splices)} splices. Every length is end to end (strip lengths included).", ""]
    n = 1
    out += [f"## {n}. Cut the wire", ""]
    groups: OrderedDict[tuple, list[dict]] = OrderedDict()
    for r in sorted(cut, key=lambda r: (r["gauge"], r["color"], -r["cut_mm"])):
        groups.setdefault((r["gauge"], r["color"], r["cut_mm"]), []).append(r)
    for (gauge, color, mm), rows in groups.items():
        out.append(f"- [ ] {len(rows)} x {mm:g} mm of {gauge} {color}: " + ", ".join(r["id"] for r in rows))
    n += 1
    out += ["", f"## {n}. Strip the ends", ""]
    strips: OrderedDict[float, list[str]] = OrderedDict()
    for r in cut:
        strips.setdefault(r["strip_a_mm"], []).append(f"{r['id']} (end {r['from']})")
        strips.setdefault(r["strip_b_mm"], []).append(f"{r['id']} (end {r['to']})")
    for mm in sorted(strips):
        out.append(f"- [ ] strip {mm:g} mm: " + ", ".join(strips[mm]))
    n += 1
    out += ["", f"## {n}. Terminate each end", ""]
    for r in cut:
        out.append(f"- [ ] {r['id']} ({r['gauge']} {r['color']}), end {r['from']}: {r['term_a']}")
        out.append(f"- [ ] {r['id']}, end {r['to']}: {r['term_b']}")
    if h.splices:
        n += 1
        out += ["", f"## {n}. Make the splices", ""]
        for s in h.splices.values():
            ids = [l.id for l in h.links_at(s.ref)]
            out.append(f"- [ ] {s.name} ({s.kind} splice): join {', '.join(ids)}")
    n += 1
    out += ["", f"## {n}. Insert the terminals into the housings", "", "Push each crimp terminal in until it clicks, in pin order; tug each wire to check it holds.", ""]
    from eitri.harness.model import PinRef
    for c in h.connectors.values():
        if c.termination != "crimp":
            continue
        rows = []
        for pin in c.pin_names:
            ls = h.links_at(PinRef(c.name, pin))
            rows.append(f"pin {pin}" + (f" ({c.pin_labels[pin]})" if pin in c.pin_labels else "") + ": " + (", ".join(f"{l.id} {l.color}" for l in ls) if ls else "empty"))
        out.append(f"- [ ] {c.name} ({c.type}, {c.pins} pos" + (f", {c.part_number}" if c.part_number else "") + "): " + "; ".join(rows))
    n += 1
    out += ["", f"## {n}. Label", "", f"- [ ] Put a wire marker with its id on both ends of every wire ({2 * len(cut)} markers): " + ", ".join(r["id"] for r in cut), ""]
    n += 1
    out += [f"## {n}. Continuity test (power off)", ""]
    for r in cut:
        out.append(f"- [ ] beep {r['from']} to {r['to']} ({r['id']}): continuity, and no continuity to any other wire's pin")
    nets = [x for x in (nets or []) if x.get("voltage_V") and x.get("drop_pct") is not None]
    n += 1
    out += ["", f"## {n}. First power-on", "", "- [ ] Check the supply polarity at the connector before plugging anything in.", "- [ ] Power on with the load disconnected, then connect one load at a time."]
    for x in nets:
        out.append(f"- [ ] net {x['name']}: {x['voltage_V']:g} V at the source; the design expects at most {x['drop_pct']:.2f}% ({x['drop_V']:.3f} V) lost to the wiring at {x['current_A']:g} A" if x.get("current_A") is not None
                   else f"- [ ] net {x['name']}: {x['voltage_V']:g} V at the source")
    return "\n".join(out) + "\n"


def assembly_order(parts, module, mech, links: dict | None, integration: dict | None, plan, bom: dict, firmware_file: str | None = None) -> str:
    """Assembly steps in kinematic order: from the project's Mechanism (the public kernel's object; `links` is its COUPLING's link ->
    CAD parts map), HARDWARE / PURCHASED, INTEGRATION and the print plan."""
    out = ["# Assembly order", ""]
    n = 1
    printed = [q for q in plan.parts if q.included and not q.problem]
    out += [f"## {n}. Print and prepare", ""]
    for q in printed:
        out.append(f"- [ ] print {q.name} ({q.orientation}" + (f", supports suggested: {q.overhang_risk} overhang" if q.overhang_risk in ("medium", "high") else ", no supports") + ")")
    out.append("- [ ] remove brims and supports, test-fit the servo pockets and clean them with a file where needed")
    n += 1
    fasteners = [r for r in bom["items"] if r["category"] in ("fastener", "bearing") and r["origin"] == "catalog"]
    out += ["", f"## {n}. Gather the hardware", ""]
    for r in bom["items"]:
        if r["origin"] == "catalog" and r["category"] != "material":
            out.append(f"- [ ] {r['qty']:g} x {r['id']}: {r['description']}")
    infos = []
    if mech is not None:
        from eitri.mechanism.checks import run_checks
        links = links or {}
        infos = run_checks(mech)[1]
        n += 1
        out += ["", f"## {n}. Build the mechanism, base first", ""]
        out.append(f"- [ ] the fixed link **{mech.base}**: {', '.join(links.get(mech.base, [mech.base]))}")
        for j in mech.order():
            child = ", ".join(links.get(j.child, [j.child]))
            lim = f"{j.limit[0]:g} to {j.limit[1]:g} {j.unit}" if j.limit else "fixed"
            if j.actuator is not None:
                out.append(f"- [ ] joint **{j.name}**: seat the {j.actuator.name} on {j.parent}; centre it (mid travel) before fitting the horn")
            out.append(f"- [ ] attach **{j.child}** ({child}) at joint {j.name}; zero pose is {j.name} = 0 {j.unit}; the joint may move {lim}")
        if fasteners:
            out.append("- [ ] secure parts with the listed fasteners (M3 screws into the servo ears and bosses; do not overtighten printed threads)")
    if integration:
        n += 1
        out += ["", f"## {n}. Route the harness", ""]
        for r in integration.get("routes") or []:
            thr = ", ".join(str(t.get("label") or t.get("part")) for t in r.get("through") or [])
            out.append(f"- [ ] route '{r['name']}': pull wires {', '.join(r['wires'])} through " + (thr or "the model") + f" with {r.get('service_loop_pct', 10)}% slack")
        for m_ in integration.get("mating") or []:
            out.append(f"- [ ] plug {m_['connector']} into {m_['part']} (approach from the +{ 'XYZ'[max(range(3), key=lambda i: abs((m_.get('normal') or [0, 0, 1])[i]))] } side, {m_.get('approach_mm', 20):g} mm of room)")
    if firmware_file:
        out.append(f"- [ ] flash the firmware ({firmware_file}); its pinout is checked against the harness")
    graded = [i for i in infos if i.required is not None and i.stall is not None]
    if graded:
        n += 1
        out += ["", f"## {n}. Check against the design", ""]
        for i in graded:
            rated = f", {i.rated:.1f} {i.load_unit} recommended continuous" if i.rated is not None else ""
            out.append(f"- [ ] joint {i.name}: the design needs {i.required:.1f} {i.load_unit} of holding load; the actuator ({i.actuator}) gives {i.stall:.1f} {i.load_unit} at stall{rated} (figures as declared in the model)")
    return "\n".join(out) + "\n"


def print_manifest(plan, files: dict) -> tuple[dict, str]:
    rows = []
    for q in plan.parts:
        if not q.included:
            continue
        rows.append({"name": q.name, "material": q.material if hasattr(q, "material") else None, "volume_mm3": q.volume, "orientation": q.orientation, "rotation": q.rotation,
                     "size_mm": q.size, "overhang_risk": q.overhang_risk, "support_volume_mm3": round(q.support_volume, 1),
                     "supports": "suggested" if q.overhang_risk in ("medium", "high") or q.support_volume > 0.05 * max(q.volume, 1) else "not needed",
                     "min_wall_mm": q.min_wall, "plate": q.plate, "problem": q.problem, "stl": files.get(q.name)})
    md = ["# Print manifest", "", plan.summary, "", "| part | orientation | size (mm) | supports | min wall | plate | file |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        size = " x ".join(f"{v:g}" for v in r["size_mm"])
        wall = "" if r["min_wall_mm"] is None else f"{r['min_wall_mm']:g} mm"
        md.append(f"| {r['name']} | {r['orientation']} | {size} | {r['supports']} ({r['overhang_risk']} overhang) | {wall} | {r['plate']} | {r['stl'] or r['problem'] or ''} |")
    return {"summary": plan.summary, "parts": rows}, "\n".join(md) + "\n"


# ---------------------------------------------------------------------------- the package

def build_package(project, out_dir: str | Path | None = None, force: bool = False, bed: tuple[float, float] = (220.0, 220.0),
                  strict: bool = False) -> dict:
    """Write the build package for a project. Raises PackageRefused unless every check passes (or force). With strict
    (the combined workspace's export rule) a check that did not run refuses it too, and force does not override either. Returns the manifest."""
    from eitri.harness.checks import net_stats
    from eitri.model import load_model
    from eitri.printexport import PrintSettings, export_plan, make_plan
    folder = Path(project.folder)
    art = bom_mod.artifacts(folder, project.model_path)
    if art.cad is None:
        raise PackageRefused(["the project has no CAD model to print (a build package is for a design with parts)"])
    out = Path(out_dir) if out_dir else folder / "build_package"
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="eitri-package-") as tmp:
        scratch = Path(tmp)
        cad_guards = project.guards() if art.cad == project.model_path else None
        cad_res, cad_data = _build(art.cad, scratch, "cad", cad_guards, render=True)
        har_res = har_data = None
        if art.harness is not None:
            har_res, har_data = _build(art.harness, scratch, "harness", None, render=True)
        checks = {"cad": _verdict((cad_data.get("report") or {}).get("checks", []))}
        if har_data is not None:
            checks["harness"] = _verdict((har_data.get("report") or {}).get("checks", []))
        others: dict[str, tuple[Path, Any, dict]] = {}          # the project's other domains: mechanism, pneumatics, firmware
        for kind, files in files_by_kind(folder).items():
            if kind in ("mechanism", "pneumatics", "firmware", "electronics"):
                f = files[0]
                res_k, data_k = _build(f, scratch, kind, project.guards() if f == project.model_path else None, render=False)
                others[kind] = (f, res_k, data_k)
                checks[kind] = _verdict((data_k.get("report") or {}).get("checks", []))
        invalid = [p["name"] for p in (cad_data.get("report") or {}).get("parts", []) if not p.get("valid", True)]
        failing = [f"[{k}] {f}" for k, v in checks.items() for f in v["failing"]] + [f"[cad] part {n} has invalid geometry" for n in invalid]
        if strict:
            failing += [f"[{k}] NOT RUN {f}" for k, v in checks.items() for f in v["not_run_checks"]]
        if failing and (strict or not force):
            raise PackageRefused(failing)

        out.mkdir(parents=True, exist_ok=True)
        for sub in ("print", "harness", "mechanism", "pneumatics", "firmware"):
            shutil.rmtree(out / sub, ignore_errors=True)
        files: list[str] = []

        def put(rel: str, text: str) -> None:
            f = out / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
            files.append(rel)

        # print: orientation, supports, STL per part
        lm = load_model(art.cad)
        parts = lm.build()
        hardware = set((getattr(lm.module, "HARDWARE", None) or {}))
        plan = make_plan(parts, PrintSettings(bed=bed, exclude=sorted(hardware)), report=cad_data.get("report"))
        written = export_plan(plan, out / "print", formats=("stl",), stem=art.cad.stem, make_zip=False)
        stl = {Path(f).stem: str(Path(f).relative_to(out)) for f in written["files"] if f.endswith(".stl") and "/parts/" in f}
        pm, pmd = print_manifest(plan, stl)
        for p in pm["parts"]:                                   # the material and mass of each part, from the report
            rp = next((r for r in (cad_data.get("report") or {}).get("parts", []) if r["name"] == p["name"]), {})
            p["material"], p["mass_g"] = rp.get("material"), rp.get("mass_g")
        pm["problems"] = written["problems"]
        files += [str(Path(f).relative_to(out)) for f in written["files"]]
        put("print/print_manifest.json", json.dumps(pm, indent=1))
        put("print/print_manifest.md", pmd)
        # bom
        bom = bom_mod.consolidated(folder, project.model_path, name=project.name)
        put("bom.json", bom_mod.to_json(bom))
        put("bom.md", bom_mod.to_markdown(bom))
        put("bom.csv", bom_mod.to_csv(bom))
        # harness
        if har_res is not None:
            for key in ("cutlist_md", "cutlist_csv", "svg", "png", "wireviz", "bom_md", "csv"):
                src = har_res.outputs.get(key)
                if src and Path(src).is_file():
                    dst = out / "harness" / Path(src).name
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    files.append(str(dst.relative_to(out)))
            h = load_model(art.harness).build()
            put("wiring_steps.md", wiring_steps(h, [vars(n) if not isinstance(n, dict) else n for n in (har_data.get("report") or {}).get("nets", [])]))
        # assembly
        mech_obj, links = None, None
        if "mechanism" in others:
            mlm = load_model(others["mechanism"][0])
            mech_obj, links = mlm.build(), (getattr(mlm.module, "COUPLING", None) or {}).get("links")
        for kind, (f, res_k, data_k) in others.items():           # each other domain's outputs, in its own folder
            for key, src in res_k.outputs.items():
                src = Path(src)
                if src.is_file() and src.suffix in (".md", ".csv", ".svg", ".png", ".ino", ".json", ".h", ".cpp"):
                    dst = out / kind / src.name
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    files.append(str(dst.relative_to(out)))
        put("assembly_order.md", assembly_order(parts, lm.module, mech_obj, links, getattr(lm.module, "INTEGRATION", None), plan, bom,
                                                others["firmware"][0].name if "firmware" in others else None))
        if cad_res.image and Path(cad_res.image).is_file():
            (out / "images").mkdir(exist_ok=True)
            shutil.copy2(cad_res.image, out / "images" / "overview.png")
            files.append("images/overview.png")

        manifest = {"project": project.name, "generated": time.strftime("%Y-%m-%d %H:%M:%S"), "passing": not failing, "forced": bool(failing and force),
                    "checks": checks, "models": {"cad": art.cad.name, "harness": art.harness.name if art.harness else None, **{k: v[0].name for k, v in others.items()}},
                    "bom_totals": bom["totals"], "unverified": bom["unverified"], "print": {"summary": plan.summary, "problems": written["problems"]},
                    "seconds": round(time.time() - t0, 1)}
        readme = _readme(manifest, checks, bom, plan, sorted(set(files)))
        put("README.md", readme)
        manifest["files"] = sorted(set(files) | {"manifest.json"})
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    manifest["out"] = str(out)
    return manifest


def _readme(manifest: dict, checks: dict, bom: dict, plan, files: list[str]) -> str:
    lines = [f"# Build package: {manifest['project']}", ""]
    if manifest["forced"]:
        lines += ["> **THIS DESIGN FAILS ITS CHECKS.** The package was written with force=True. Do not build from it until the failures below are fixed.", ""]
    for k, v in checks.items():
        lines.append(f"- **{k}**: {v['pass']} pass, {v['warn']} warn, {v['fail']} fail of {v['checks']} checks")
    for k, v in checks.items():
        for f in v["failing"]:
            lines.append(f"  - FAIL [{k}] {f}")
        for w in v["warnings"]:
            lines.append(f"  - warn [{k}] {w}")
    lines += ["", f"Print: {plan.summary}.", f"Parts cost (estimate, not a quote): ${bom['totals']['priced_total_usd']:,.2f} for {bom['totals']['priced_lines']} of {bom['totals']['lines']} lines; harness wire and terminals are unpriced.", ""]
    if bom["unverified"]["parts"]:
        lines += ["**Check before you order:** the catalog figures for " + ", ".join(bom["unverified"]["parts"]) + " have not been compared with datasheets, and the torque margins in the checks rest on them. "
                  "Estimates are marked ESTIMATE in the check text (servo continuous torque, wire insulation, print infill).", ""]
    lines += ["## Contents", ""] + [f"- `{f}`" for f in files]
    return "\n".join(lines) + "\n"
