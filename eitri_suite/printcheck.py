"""Every printable part of a combined project's CAD file can be written for printing: its mesh is closed and 2-manifold (every edge
shared by exactly two triangles, consistently wound, the solid's own volume). It is the same test the print export (core
eitri.printexport.mesh_problem) runs before it writes an STL or 3MF, made a check of every CAD build, so a part the export would
drop fails in combined_build, before an export is asked for, and a strict combined export or a change package refuses it like any
FAIL.

Printable = every part with a solid except bought ones: fasteners and metal parts (eitri.printexport.classify), the HARDWARE
catalog parts, a customer's reference part, and the bodies the electronics file's COUPLING mounts name as stand_in= (the board
the person buys, drawn in its place).
"""
from __future__ import annotations

from pathlib import Path

from eitri.harness.checks import HCheck

CAT = "print"
FIX = "overlap the bodies by about 0.5 mm, or separate them"


def _check(name: str, severity: str, measured: str, expected: str, refs=()) -> HCheck:
    return HCheck(name=name, ok=severity != "fail", measured=measured, expected=expected, severity=severity, category=CAT,
                  refs=list(refs))


def stand_ins(folder: Path, cad: Path) -> dict[str, str]:
    """{CAD body: the board it stands in for} from the project's electronics file(s) whose COUPLING mounts are on this CAD file."""
    from eitri.model import load_model

    from .electronics import coupling_of
    from .projectfiles import files_by_kind
    out: dict[str, str] = {}
    for f in files_by_kind(folder).get("electronics", []):
        try:
            coupling = coupling_of(load_model(f).module)
        except Exception:                                   # noqa: BLE001 - its own build reports the error
            continue
        named = coupling.get("cad")
        if named and Path(str(named)).name != Path(cad).name:
            continue
        for board, m in (coupling.get("mounts") or {}).items():
            s = m.get("stand_in") if isinstance(m, dict) else None
            if s:
                out[str(s)] = f"the circuit's {board}"
    return out


def run(module, parts, model_path: Path) -> list[HCheck]:
    from eitri import measure
    from eitri.printexport import classify, mesh_problem, part_mesh
    folder = Path(model_path).resolve().parent
    hardware = set(getattr(module, "HARDWARE", None) or {})
    skip = stand_ins(folder, Path(model_path))
    out: list[HCheck] = []
    for p in parts:
        shape = p.shape
        try:
            if not list(shape.solids()):
                continue
        except Exception:                                   # noqa: BLE001
            continue
        bought, _why = classify(p.name, getattr(p, "material", None))
        if bought or p.name in hardware or p.name in skip or getattr(p, "reference", None):
            continue
        name = f"print: {p.name} mesh can be written for printing"
        try:
            m = part_mesh(shape, name=p.name)              # the print export's own tolerances (eitri.printexport.export_plan)
            err = mesh_problem(m, round(abs(measure.signed_volume(shape)), 2))
        except Exception as exc:                           # noqa: BLE001 - a part that cannot even be meshed is not printable
            err = f"it could not be meshed ({type(exc).__name__}: {str(exc)[:120]})"
        if err:
            err = err.replace("; fix the model (overlap the bodies or separate them)", "")
            out.append(_check(name, "fail", f"{p.name}: {err}. The print export would write no STL or 3MF for it. Fix: {FIX} where it "
                              "says (two solids touching along a line or at a point, such as a boss tangent to a wall)",
                              "a closed, 2-manifold mesh", [p.name]))
        else:
            out.append(_check(name, "pass", f"{p.name}: closed, every edge shared by two triangles, its solid's volume ({len(m.faces)} triangles)",
                              "a closed, 2-manifold mesh", [p.name]))
    return out
