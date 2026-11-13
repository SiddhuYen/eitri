"""The project-level checks the suite extension adds to a build, by what was built:

    cad         route fit and mating faces against the harness named by INTEGRATION (eitri_suite.crossdomain), and the assembly:
                the harness placed at the CAD anchors, the wires' 3D paths (eitri_suite.assembly)
    harness     the same, from the harness side
    mechanism   the mechanism's COUPLING to the CAD model: CAD-derived link masses and self-collision (eitri_suite.coupling), and
                whether a free-standing base tips over (eitri_suite.stability; the CAD build re-runs it)
    firmware    the firmware pinout against the harness (COUPLING pins; eitri_suite.crossdomain), every pin wired, the
                pick-and-place SEQUENCE as joint poses (eitri_suite.assembly), and the supply against the actuators the SEQUENCE moves
                together (eitri_suite.power)
    pneumatics  nothing (a pneumatic circuit has no cross-domain check yet)
    electronics the circuit against the harness (pins, rail currents, wire ampacity, voltage at the loads, supply capacity), the
                firmware (pins), the mechanism (actuators and their stall current, driver dissipation) and the CAD model (board outlines
                and mounting holes): eitri_suite.electronics

What each model file declares (its routes, mating faces, coupled links, mapped pins) is handed back as the extension's guard, which
core compares with what earlier builds declared (see eitri.extensions), so a declaration cannot be deleted to make a check go away.
"""
from __future__ import annotations

from pathlib import Path

from eitri.harness.checks import HCheck

from . import assembly, coupling, crossdomain


def declared(module) -> dict[str, dict[str, int]]:
    from . import electronics
    if electronics.is_electronics_module(module):
        return electronics.declared_counts(module)
    out = {}
    out.update(coupling.declared_counts(getattr(module, "COUPLING", None)) if _is_mechanism_module(module) else {})
    out.update(crossdomain.declared_pinout(getattr(module, "COUPLING", None)) if not _is_mechanism_module(module) else {})
    out.update(crossdomain.declared_counts(getattr(module, "INTEGRATION", None)))
    return out


def _is_mechanism_module(module) -> bool:
    return isinstance(getattr(module, "COUPLING", None), dict) and "links" in module.COUPLING


def flat_declared(module) -> dict[str, int]:
    """What the model declares, as the flat numeric dict core's guard keeps ("integration.routes": 1). The core compares it with what
    earlier builds declared and fails "suite: declared checks are kept" if anything shrank or went."""
    return {f"{name}.{key}": val for name, counts in declared(module).items() for key, val in counts.items()}


def _board_hint(folder: Path) -> tuple[str | None, str | None]:
    """(board key, the harness connector that is the board) from the project's firmware file, read without building it: the
    `board=` of its Firmware(...) and the connector its COUPLING pins name ("UNO.D9" -> UNO)."""
    import re
    from .projectfiles import files_by_kind
    for f in files_by_kind(folder).get("firmware", []):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"""Firmware\([^)]*?board\s*=\s*['"]([A-Za-z0-9_-]+)['"]""", text)
        c = re.search(r"""pins\s*=\s*\{[^}]*?['"]([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z0-9]+['"]""", text)
        if m:
            return m.group(1), (c.group(1) if c else None)
    return None, None


def extras(kind: str, module, built, model_path: Path) -> tuple[list[HCheck], dict]:
    """(checks, result data) for one build of `kind`: every check with its rule, kind and sources (eitri_suite.rules) and, when it
    did not run, its first-class not-run status (core eitri.checks: `not_run` inputs; the "not run: " text stays for older readers)."""
    from . import rules
    checks, data = _extras(kind, module, built, model_path)
    data["siblings"] = sibling_digests(Path(model_path))     # what this build's cross-domain checks read (the web workspace's staleness)
    rules.attach_sources(checks, built)
    for c in checks:
        if str(c.measured).startswith("not run: ") and not c.not_run:
            c.not_run = [str(c.measured)[len("not run: "):].split(":")[0].split(";")[0][:120]]
    return checks, data


def sibling_digests(model_path: Path) -> dict[str, str]:
    """{file: sha256} of every other model file in the project folder at this build."""
    import hashlib
    from .projectfiles import files_by_kind
    me = Path(model_path).resolve()
    out = {}
    for files in files_by_kind(me.parent).values():
        for f in files:
            if Path(f).resolve() != me:
                try:
                    out[Path(f).name] = hashlib.sha256(Path(f).read_bytes()).hexdigest()
                except OSError:
                    continue
    return out


def _extras(kind: str, module, built, model_path: Path) -> tuple[list[HCheck], dict]:
    from eitri.anchors import to_json as anchors_json
    folder = Path(model_path).resolve().parent
    data: dict = {"declares": declared(module)}
    checks: list[HCheck] = []
    if kind == "electronics":
        from . import electronics
        e_checks, e_data = electronics.run(module, built, Path(model_path))
        data["electronics"] = e_data
        return e_checks, data
    if kind == "mechanism":
        c = getattr(module, "COUPLING", None)
        if c is not None:
            checks, cdata = coupling.run(built, c, folder, Path(model_path))
            data["coupling"] = cdata
        return checks, data
    if kind == "firmware":
        checks, info = crossdomain.project_checks(model_path, built, kind, module)
        if info:
            data["integration"] = info
        hpath, _hname = crossdomain.harness_for_firmware(module, folder)
        harness = mech = None
        if hpath is not None and hpath.is_file():
            try:
                from eitri.model import load_model
                from eitri.harness.model import Harness
                h = load_model(hpath).build()
                harness = h if isinstance(h, Harness) else None
            except Exception:                               # noqa: BLE001 - the harness's own build reports its error
                harness = None
        if harness is not None:
            checks += assembly.pin_wiring_checks(built, harness, getattr(module, "COUPLING", None))
        if getattr(module, "SEQUENCE", None) is not None:
            mech = _mechanism_for(module, folder)
            seq_checks, seq = assembly.sequence(module, built, mech)
            checks += seq_checks
            if seq is not None:
                data["sequence"] = seq
        from . import power                                 # the supply against the actuators this firmware moves together
        p_checks, p_data = power.firmware_check(module, built, folder, mech if mech is not None else _mechanism_for(module, folder), harness)
        checks += p_checks
        if p_data:
            data["power"] = p_data
        return checks, data
    pair = crossdomain.load_pair(model_path, built, kind, module)
    checks, info = crossdomain.project_checks(model_path, built, kind, module, pair=pair)
    if info:
        data["integration"] = info
    coupled = None
    if pair is not None and not pair.problem and pair.parts is not None and len(pair.parts) and pair.cad_path is not None:
        coupled = _coupled_mechanism(folder, pair.cad_path)
    bindings = links_eff = frames = None
    if coupled is not None:                                     # the actuators bound to their joints: the map every body is posed with
        from . import coupling as cpl_mod
        from . import kinematics
        mech, cpl, mpath = coupled
        bindings = kinematics.actuator_bindings(pair.parts, mech, cpl, dict(getattr(pair.cad_module, "HARDWARE", None) or {}))
        links_eff = kinematics.effective_links(cpl.get("links") or {}, bindings)
        seq = None
        try:
            seq = cpl_mod.firmware_sequence(folder, mech, mpath.name)
        except Exception:                                       # noqa: BLE001
            seq = None
        frames = kinematics.keyframes(mech, seq)
    components: list[dict] = []
    if pair is not None and not pair.problem and pair.parts is not None and len(pair.parts) and pair.harness is not None:   # a blank CAD: nothing to land on yet
        anchors = anchors_json(pair.parts)
        board_key, board_connector = _board_hint(folder)
        poses = _poses(coupled, links_eff, frames) if coupled else None
        a_checks, a_data = assembly.run(pair.parts, anchors, pair.harness, pair.integration, board_key, board_connector, poses=poses,
                                        components_checked=coupled is not None)
        checks += a_checks
        data["assembly"] = a_data
        components = assembly.components(a_data["placed"], anchors, pair.harness)
    if coupled is not None:                                     # the bodies of every domain against the chain that moves them, whichever side changed
        from . import kinematics
        mech, cpl, _mpath = coupled
        a_check, a_info = kinematics.attachment_check(pair.parts, mech, links_eff, poses=frames, components=components)
        checks += [kinematics.actuator_check(bindings), a_check]
        data["placement"] = {"attachment": a_info["bodies"], "actuators": bindings["joints"], "links_map": links_eff}
        if kind == "cad":
            i_check, i_found = kinematics.interference_check(pair.parts, mech, links_eff)
            checks.append(i_check)
            data["placement"]["interference"] = i_found
            from . import stability                         # the base's footprint and the masses are this file's: re-checked here too
            s_check, s_data = stability.check(pair.parts, mech, cpl, links_eff, bindings, dict(getattr(pair.cad_module, "HARDWARE", None) or {}),
                                              getattr(pair.cad_module, "PURCHASED", None), seq)
            checks.append(s_check)
            data["stability"] = s_data
    return checks, data


def _coupled_mechanism(folder: Path, cad_path: Path):
    """(Mechanism, its COUPLING, its path) of the project's mechanism file that couples to this CAD file, or None."""
    from eitri.model import load_model
    from eitri.mechanism.model import Mechanism
    from .projectfiles import files_by_kind
    by_kind = files_by_kind(folder)
    cads = by_kind.get("cad", [])
    for f in by_kind.get("mechanism", []):
        try:
            if "COUPLING" not in f.read_text(encoding="utf-8"):
                continue
            lm = load_model(f)
            c = getattr(lm.module, "COUPLING", None)
            if not isinstance(c, dict) or not isinstance(c.get("links"), dict):
                continue
            target = c.get("cad")
            if (target and (folder / target).resolve() != Path(cad_path).resolve()) or (not target and len(cads) != 1):
                continue
            m = lm.build()
            if isinstance(m, Mechanism):
                return m, c, f
        except Exception:                                   # noqa: BLE001 - its own build reports its error
            continue
    return None


def _poses(coupled, links: dict, frames: list) -> list[dict]:
    """The poses the Assembly view shows the machine in, beyond the build pose (kinematics.keyframes: home and each step of the
    firmware SEQUENCE), as {label, T: {CAD part: 4x4}} from the mechanism's own transforms over the effective links map."""
    mech, _c, _mpath = coupled
    of = {str(n): str(l) for l, ns in (links or {}).items() for n in (ns or [])}
    out = []
    try:
        for label, q in (frames or [])[1:]:
            T = mech.transforms(q)
            out.append({"label": label, "T": {n: T[l] for n, l in of.items() if l in T}})
    except Exception:                                       # noqa: BLE001
        pass
    return out


def _mechanism_for(module, folder: Path):
    """The mechanism a firmware file's sequence is checked against: COUPLING mechanism=, else the project's only mechanism file."""
    from .projectfiles import files_by_kind
    coupling = getattr(module, "COUPLING", None)
    coupling = coupling if isinstance(coupling, dict) else {}
    files = files_by_kind(folder).get("mechanism", [])
    name = coupling.get("mechanism")
    path = folder / name if name else (files[0] if len(files) == 1 else None)
    if path is None or not path.is_file():
        return None
    try:
        from eitri.model import load_model
        from eitri.mechanism.model import Mechanism
        m = load_model(path).build()
        return m if isinstance(m, Mechanism) else None
    except Exception:                                       # noqa: BLE001 - its own build reports its error
        return None
