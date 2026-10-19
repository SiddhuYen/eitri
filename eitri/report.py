"""The automatic geometry report produced after every build.

The report is what makes eitri self-correcting: the agent never has to remember
to measure, because every build comes back with validity, solids, bounding box,
volume, holes, thinnest wall, interferences between parts and the model's own
`verify()` checks - as data and as a compact text summary.
"""
from __future__ import annotations

import copy
import hashlib
import io
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any

import build123d as bd
from build123d import Vector

from . import measure
from .model import CheckResult, Checker, Parts


@dataclass
class HoleInfo:
    kind: str
    diameter: float
    depth: float
    through: bool | None
    axis: str
    center: tuple[float, float, float]
    count: int = 1


@dataclass
class PartReport:
    name: str
    color: str
    solids: int
    faces: int
    edges: int
    valid: bool
    volume: float
    area: float
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    size: tuple[float, float, float]
    center_of_mass: tuple[float, float, float]
    free_edges: int
    holes: list[HoleInfo] = field(default_factory=list)
    bosses: list[HoleInfo] = field(default_factory=list)
    fillet_radii: list[float] = field(default_factory=list)
    min_wall: float | None = None
    min_wall_at: tuple[float, float, float] | None = None
    sharp_edges: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    material: str | None = None
    mass_g: float | None = None
    group: str | None = None
    inertia: dict | None = None            # mass properties (inertia tensor about the centre of mass) when a material is set


@dataclass
class PairInfo:
    a: str
    b: str
    overlap_volume: float
    distance: float


@dataclass
class Report:
    parts: list[PartReport]
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    size: tuple[float, float, float]
    volume: float
    pairs: list[PairInfo] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    analysis_seconds: float = 0.0
    bom: list[dict] = field(default_factory=list)          # assemblies: identical parts grouped, numbered for balloons
    far_pairs: int = 0                                      # part pairs not measured: their boxes are over near_gap mm apart
    near_gap: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def ok(self) -> bool:
        """No failed checks and no invalid geometry."""
        return all(c.ok for c in self.checks) and all(p.valid for p in self.parts)


def _t3(v: Vector) -> tuple[float, float, float]:
    return (round(v.X, 4), round(v.Y, 4), round(v.Z, 4))


def _group_holes(hs: list[measure.Hole]) -> list[HoleInfo]:
    """Collapse identical holes (same diameter/depth/axis/through) into counted rows, keeping centres."""
    rows: list[HoleInfo] = []
    for h in hs:
        info = HoleInfo(h.kind, round(h.diameter, 3), round(h.depth, 3), h.through,
                        measure._axis_name(h.axis), _t3(h.center))
        rows.append(info)
    return rows


def analyze_part(name: str, color: str, shape: bd.Shape, wall_samples: int = 600, material: str | None = None) -> PartReport:
    solids = list(shape.solids())
    faces = shape.faces()
    edges = shape.edges()
    valid = measure.is_valid(shape)
    vols = [measure.signed_volume(s) for s in solids] if solids else [measure.signed_volume(shape)]
    volume = sum(vols)
    bb = measure.bounding_box(shape)
    try:
        com = shape.center(bd.CenterOf.MASS)
    except Exception:
        com = bb.center()
    free = measure.free_edge_count(shape)
    issues: list[str] = []
    if not valid:
        issues.append("geometry is INVALID (BRepCheck failed) - booleans/exports may misbehave")
    if not solids:
        if faces:
            try:
                from . import surfaces as SF
                c = SF.continuity(shape)
                issues.append(f"surface body: {len(faces)} face(s), {shape.area:.1f} mm², {c['free_edges']} free edge(s), internal edges G0 {c['G0']} / G1 {c['G1']} / G2 {c['G2']} (thicken or close it to get a solid)")
            except Exception:
                issues.append("surface body (no solid): thicken or close it to get a solid")
        else:
            issues.append("no faces at all")
    for i, v in enumerate(vols):
        if v < -1e-9:
            issues.append(f"solid {i + 1} has NEGATIVE volume ({v:.3f} mm^3): inverted/reversed solid")
    tiny = [v for v in vols if 0 <= v < 1e-3]
    if tiny:
        issues.append(f"{len(tiny)} sliver solid(s) with volume < 0.001 mm^3")
    if len(solids) > 1:
        issues.append(f"{len(solids)} disjoint solids in one part (floating geometry? split into parts or fuse)")
    if free and solids:
        issues.append(f"{free} free edge(s) on a solid part")
    if max(bb.size.X, bb.size.Y, bb.size.Z) > 2000:
        issues.append("part is over 2 m in extent - check units (mm expected)")

    hs = measure.cylindrical_features(shape) if faces and len(faces) < 4000 else []
    hole_rows = _group_holes([h for h in hs if h.kind == "hole"])
    boss_rows = _group_holes([h for h in hs if h.kind == "boss"])
    try:
        fr = measure.fillet_radii(shape) if len(faces) < 4000 else []
    except Exception:
        fr = []

    mw = None
    mw_at = None
    if solids and len(faces) < 6000:
        try:
            sample = measure.min_wall(shape, samples=wall_samples)
        except Exception:
            sample = None
        if sample is not None:
            mw = round(sample.thickness, 3)
            mw_at = _t3(sample.point)
            if sample.thickness < 1.5:
                issues.append(f"thinnest wall ~{sample.thickness:.2f} mm at {mw_at} "
                              f"({'a sliver' if sample.thickness < 0.8 else 'below the 1.5 mm FDM minimum'}; "
                              "thicken it, or say why it is intended)")

    sharp: list[str] = []
    if solids and len(edges) < 6000:
        try:
            found = measure.sharp_edges(shape, max_interior_deg=30.0)
        except Exception:
            found = []
        sharp = [s.describe() for s in found[:6]]
        if found:
            issues.append(f"{len(found)} knife edge(s) thinner than 30 deg (feather edges that print/break badly): "
                          + "; ".join(sharp[:3]))

    return PartReport(
        name=name, color=color, solids=len(solids), faces=len(faces), edges=len(edges), valid=valid,
        volume=round(volume, 3), area=round(shape.area, 3), bbox_min=_t3(bb.min), bbox_max=_t3(bb.max),
        size=_t3(bb.size), center_of_mass=_t3(com), free_edges=free, holes=hole_rows, bosses=boss_rows,
        fillet_radii=fr, min_wall=mw, min_wall_at=mw_at, sharp_edges=sharp, issues=issues,
        material=material, mass_g=(round(measure.mass_grams(shape, material), 2) if material in measure.DENSITY else None),
    )


# ---------------------------------------------------------------------------- reuse between builds
# A direct edit usually changes one part of an assembly, yet every build re-measures every part and every close
# pair. Results are kept per exact geometry: the fingerprint is a hash of the part's B-rep (without meshes), so a
# part (or a pair) whose geometry is byte-for-byte the same as before reuses its measurements; anything that
# changed, even slightly, is measured again. Lives as long as the process (the warm build worker).
_PART_CACHE: "OrderedDict[tuple, PartReport]" = OrderedDict()
_PAIR_CACHE: "OrderedDict[tuple, tuple[float, float]]" = OrderedDict()
_PART_CACHE_MAX, _PAIR_CACHE_MAX = 3000, 30000
cache_stats = {"part_hits": 0, "part_misses": 0, "pair_hits": 0, "pair_misses": 0}


def fingerprint(shape: bd.Shape) -> str | None:
    """A hash of the shape's exact geometry and placement (B-rep text without triangulation), or None."""
    try:
        from OCP.BRepTools import BRepTools
        from OCP.TopTools import TopTools_FormatVersion
        buf = io.BytesIO()
        BRepTools.Write_s(shape.wrapped, buf, False, False, TopTools_FormatVersion.TopTools_FormatVersion_VERSION_1)
        return hashlib.sha1(buf.getvalue()).hexdigest()
    except Exception:
        return None


def _cache_get(cache: OrderedDict, key):
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    return None


def _cache_put(cache: OrderedDict, key, value, limit: int) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)


def _box_gap(a: bd.BoundBox, b: bd.BoundBox) -> float:
    """Distance between two boxes (0 when they touch or overlap): a lower bound of the parts' distance."""
    d = [max(0.0, a.min.X - b.max.X, b.min.X - a.max.X), max(0.0, a.min.Y - b.max.Y, b.min.Y - a.max.Y),
         max(0.0, a.min.Z - b.max.Z, b.min.Z - a.max.Z)]
    return (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5


def analyze(parts: Parts, checker: Checker | None = None, wall_samples: int = 600,
            pair_limit: int = 800, progress=None, fingerprints: dict[str, str | None] | None = None) -> Report:
    """The build report. `progress(message)` is told what is being measured (the page shows it).
    `fingerprints` (part name -> fingerprint(), taken before the parts were meshed) lets unchanged parts and
    pairs reuse their measurements from an earlier build in this process."""
    t0 = time.time()
    say = progress or (lambda message: None)
    fps = fingerprints or {}
    part_reports = []
    plist = list(parts)
    samples = [wall_samples] * len(plist)
    if len(plist) > 1:                         # an assembly shares the wall-ray budget by surface area (>= 100 each)
        try:
            areas = [max(float(p.shape.area), 1e-9) for p in plist]
            budget = wall_samples + 100 * (len(plist) - 1)
            # in steps of 50 rays, so resizing one part does not shift every other part's share (and its cache)
            samples = [max(100, min(wall_samples, 50 * round(budget * a / sum(areas) / 50))) for a in areas]
        except Exception:
            pass
    for k, (p, n) in enumerate(zip(plist, samples), 1):
        material = getattr(p, "material", None)
        fp = fps.get(p.name)
        key = (fp, p.name, p.color, n, material) if fp else None
        hit = _cache_get(_PART_CACHE, key) if key else None
        if hit is not None:
            cache_stats["part_hits"] += 1
            part_reports.append(copy.deepcopy(hit))
            continue
        say(f"measuring {p.name}" + (f" ({k} of {len(plist)})" if len(plist) > 1 else "") + ": walls, holes")
        pr = analyze_part(p.name, p.color, p.shape, n, material)
        if key:
            cache_stats["part_misses"] += 1
            _cache_put(_PART_CACHE, key, copy.deepcopy(pr), _PART_CACHE_MAX)
        part_reports.append(pr)
    for pr, p in zip(part_reports, parts):
        pr.group = getattr(p, "group", None)
        if pr.material in measure.DENSITY and pr.solids:
            try:
                mp = measure.mass_properties(p.shape, pr.material)
                pr.inertia = {"principal": mp["principal"], "tensor": mp["inertia"], "units": mp["units"]}
            except Exception:
                pr.inertia = None
    bb = parts.bbox
    pairs: list[PairInfo] = []
    issues: list[str] = []
    near_gap = round(max(2.0, 0.02 * bb.diagonal), 1)
    far_pairs = 0
    if len(plist) > 1:
        # only pairs whose boxes come within near_gap can overlap or be close: the box gap is a lower bound of
        # the distance, so no close pair is skipped, and a big assembly costs its neighbours, not n^2 distances
        boxes = [measure.bounding_box(p.shape) for p in plist]
        near = sorted((g, i, j) for i in range(len(plist)) for j in range(i + 1, len(plist))
                      if (g := _box_gap(boxes[i], boxes[j])) <= near_gap)
        near = sorted((i, j) for _, i, j in near[:pair_limit])        # the closest first if there are very many
        far_pairs = len(plist) * (len(plist) - 1) // 2 - len(near)
        for k, (i, j) in enumerate(near, 1):
            a, b = plist[i], plist[j]
            fa, fb = fps.get(a.name), fps.get(b.name)
            pkey = (fa, fb) if fa and fb else None
            got = _cache_get(_PAIR_CACHE, pkey) if pkey else None
            if got is not None:
                cache_stats["pair_hits"] += 1
                ov, dist = got
            else:
                say(f"checking clearances: {a.name} and {b.name} ({k} of {len(near)})")
                try:
                    ov = measure.overlap_volume(a.shape, b.shape)
                    dist = 0.0 if ov > 1e-9 else measure.min_distance(a.shape, b.shape)
                except Exception:
                    continue
                if pkey:
                    cache_stats["pair_misses"] += 1
                    _cache_put(_PAIR_CACHE, pkey, (ov, dist), _PAIR_CACHE_MAX)
            pairs.append(PairInfo(a.name, b.name, round(ov, 4), round(dist, 4)))
            if ov > 1e-6:
                issues.append(f"INTERFERENCE: {a.name} and {b.name} overlap by {ov:.3f} mm^3")
    checks = list(checker.results) if checker else []
    rep = Report(
        parts=part_reports, bbox_min=_t3(bb.min), bbox_max=_t3(bb.max), size=_t3(bb.size),
        volume=round(sum(p.volume for p in part_reports), 3), pairs=pairs, checks=checks,
        issues=issues, analysis_seconds=round(time.time() - t0, 2), far_pairs=far_pairs, near_gap=near_gap,
    )
    if len(plist) > 1:
        try:
            from .assembly import bom
            rep.bom = bom(parts)
        except Exception:
            rep.bom = []
    return rep


def _fmt_holes(rows: list[HoleInfo], label: str) -> list[str]:
    if not rows:
        return []
    # collapse identical rows for brevity, listing centres
    groups: dict[tuple, list[HoleInfo]] = {}
    for r in rows:
        groups.setdefault((r.diameter, r.depth, r.through, r.axis), []).append(r)
    lines = []
    for (d, depth, through, axis), rs in sorted(groups.items()):
        kind = label
        if label == "hole":
            kind = "through" if through else ("blind" if through is False else "hole")
        centres = ", ".join(f"({c[0]:g},{c[1]:g},{c[2]:g})" for c in (r.center for r in rs[:6]))
        more = f" +{len(rs) - 6} more" if len(rs) > 6 else ""
        lines.append(f"    {len(rs)}x Ø{d:g} {kind} depth {depth:g} axis {axis}: centres {centres}{more}")
    return lines


def _summarize_core(report: Report, max_parts: int = 30) -> str:
    """Compact text summary for the agent (and the terminal)."""
    s = report.size
    lines = [f"Overall bbox {s[0]:g} x {s[1]:g} x {s[2]:g} mm "
             f"(X {report.bbox_min[0]:g}..{report.bbox_max[0]:g}, Y {report.bbox_min[1]:g}..{report.bbox_max[1]:g}, "
             f"Z {report.bbox_min[2]:g}..{report.bbox_max[2]:g}); total volume {report.volume:g} mm^3; "
             f"{len(report.parts)} part(s)"]
    for p in report.parts[:max_parts]:
        v = "valid" if p.valid else "INVALID"
        lines.append(f"- {p.name}: {p.solids} solid(s), {p.faces} faces, {v}; size {p.size[0]:g} x {p.size[1]:g} x {p.size[2]:g}; "
                     f"bbox X {p.bbox_min[0]:g}..{p.bbox_max[0]:g} Y {p.bbox_min[1]:g}..{p.bbox_max[1]:g} Z {p.bbox_min[2]:g}..{p.bbox_max[2]:g}; "
                     f"volume {p.volume:g} mm^3" + (f"; {p.material}, {p.mass_g:g} g" if p.mass_g is not None else ""))
        if p.min_wall is not None:
            lines.append(f"    thinnest wall ~{p.min_wall:g} mm at {p.min_wall_at}")
        lines += _fmt_holes(p.holes, "hole")
        lines += _fmt_holes(p.bosses, "boss/pin")
        if p.fillet_radii:
            lines.append(f"    rounded-edge radii: {', '.join(f'{r:g}' for r in p.fillet_radii[:8])}")
        for issue in p.issues:
            lines.append(f"    ! {issue}")
    if len(report.parts) > max_parts:
        lines.append(f"- ... {len(report.parts) - max_parts} more parts")
    if report.pairs:
        touching = [pr for pr in report.pairs if pr.overlap_volume <= 1e-6 and pr.distance < 1e-3]
        clear = sorted((pr for pr in report.pairs if pr.overlap_volume <= 1e-6 and pr.distance >= 1e-3),
                       key=lambda pr: pr.distance)                 # the tightest gaps are the ones that matter
        over = [pr for pr in report.pairs if pr.overlap_volume > 1e-6]
        if over:
            lines.append("Interferences: " + "; ".join(f"{pr.a}<->{pr.b} {pr.overlap_volume:g} mm^3" for pr in over))
        if touching:
            lines.append("In contact: " + ", ".join(f"{pr.a}<->{pr.b}" for pr in touching))
        if clear:
            lines.append("Clearances: " + ", ".join(f"{pr.a}<->{pr.b} {pr.distance:g} mm" for pr in clear[:12])
                         + (f" (+{len(clear) - 12} more)" if len(clear) > 12 else ""))
    if report.far_pairs:
        lines.append(f"({report.far_pairs} other part pair(s) are over {report.near_gap:g} mm apart: not listed)")
    for issue in report.issues:
        lines.append(f"! {issue}")
    if report.checks:
        lines.append("Checks (verify):")
        for c in report.checks:
            lines.append("  " + c.line())
    return "\n".join(lines)


def summarize(report: Report) -> str:
    """The text summary, plus the bill of materials and joints of an assembly."""
    text = _summarize_core(report)
    ine = [p for p in report.parts if p.inertia]
    if ine:
        text += "\nMass properties (about each part's centre of mass):\n" + "\n".join(
            f"  {p.name}: {p.mass_g:g} g, principal moments {', '.join(f'{v:.0f}' for v in p.inertia['principal'])} {p.inertia['units']}" for p in ine)
    if report.bom and len(report.bom) >= 1 and sum(r["qty"] for r in report.bom) > 1:
        lines = ["Bill of materials:"]
        for r in report.bom:
            mass = f", {r['mass_g']:g} g each" if r.get("mass_g") is not None else ""
            grp = f", group {r['group']}" if r.get("group") else ""
            lines.append(f"  {r['item']}. {r['name']} x{r['qty']} ({r['material']}, {r['volume']:g} mm^3{mass}{grp})" + (f" [{', '.join(r['parts'])}]" if r["qty"] > 1 else ""))
        text += "\n" + "\n".join(lines)
    return text
