"""Export for 3D printing: an assembly -> the parts to print, each in its best orientation, laid out on as
many build plates as it takes, written as STL and 3MF (per part, per plate, and one zip), every mesh
checked watertight before it is written.

    plan = make_plan(parts, PrintSettings(bed=(220, 220)))       # what will be printed, where, and why
    files = export_plan(plan, parts, out_dir)                      # the files (+ problems, by part)

Bought parts (screws, nuts, washers, bearings, inserts, magnets, and anything given a metal material)
are listed but left out by default; any part can be switched in or out by name. Orientation reuses
additive.best_orientation (six axis-aligned candidates scored by support volume, overhang area, height
and bed contact); the thinnest wall is the report's measure.min_wall, compared with a threshold you set
(by default 2 perimeters of a 0.4 mm nozzle).
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import build123d as bd

from . import additive as A
from . import measure

METALS = {"steel", "stainless", "brass", "copper", "titanium", "aluminum", "aluminium"}
BOUGHT_WORDS = ("screw", "bolt", "nut", "washer", "bearing", "insert", "magnet")


@dataclass
class PrintSettings:
    bed: tuple[float, float] = (220.0, 220.0)
    bed_height: float = 250.0
    spacing: float = 5.0                  # gap between parts on a plate (mm)
    margin: float = 5.0                   # keep this far from the bed edge
    min_wall: float = 0.8                 # flag walls thinner than this (2 perimeters of a 0.4 mm nozzle)
    include_bought: bool = False
    include: list[str] = field(default_factory=list)     # force these parts in
    exclude: list[str] = field(default_factory=list)     # leave these parts out
    orient: bool = True
    critical_deg: float = 45.0


@dataclass
class PrintPart:
    name: str
    color: str
    bought: bool
    bought_reason: str
    included: bool
    volume: float                         # mm^3
    orientation: str = "as is"
    rotation: list | None = None
    size: list[float] = field(default_factory=list)       # oriented w, d, h (mm)
    overhang_area: float = 0.0
    support_volume: float = 0.0
    overhang_risk: str = "none"           # none | low | medium | high
    min_wall: float | None = None
    thin: bool = False
    plate: int | None = None              # 1-based
    x: float = 0.0                        # centre on its plate (bed centred on the origin)
    y: float = 0.0
    turned: bool = False                  # turned 90 degrees about Z to fit
    problem: str | None = None            # why it cannot be printed as is (too big, ...)


@dataclass
class PrintPlan:
    settings: PrintSettings
    parts: list[PrintPart]
    plates: int
    summary: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["settings"]["bed"] = list(self.settings.bed)
        return d


def classify(name: str, material: str | None) -> tuple[bool, str]:
    """Is this a bought part (not printed)? Returns (bought, reason)."""
    low = name.lower()
    for w in BOUGHT_WORDS:
        if re.search(rf"(^|[^a-z]){w}s?($|[^a-z])", low) or low.startswith(w):
            return True, f"looks like a {w} (bought, not printed)"
    if material and material.lower() in METALS:
        return True, f"{material} part (not printed)"
    return False, ""


def _risk(overhang: float, area: float) -> str:
    if overhang <= 1e-6:
        return "none"
    frac = overhang / max(area, 1e-9)
    return "low" if frac < 0.05 else "medium" if frac < 0.15 else "high"


def make_plan(parts, settings: PrintSettings | None = None, report: dict | None = None) -> PrintPlan:
    """Decompose, orient, check and lay out. `report` (a build's report dict) supplies the thinnest walls
    already measured; parts it does not cover are measured here."""
    st = settings or PrintSettings()
    walls = {p["name"]: p.get("min_wall") for p in (report or {}).get("parts", [])}
    out: list[PrintPart] = []
    shapes: dict[str, bd.Shape] = {}
    for p in parts:
        shape = p.shape
        if not list(shape.solids()):
            continue                                          # sketches / surfaces are not printable bodies
        bought, why = classify(p.name, getattr(p, "material", None))
        included = (st.include_bought or not bought) and p.name not in st.exclude
        if p.name in st.include:
            included = True
        pp = PrintPart(p.name, p.color, bought, why, included, round(abs(measure.signed_volume(shape)), 2))
        if included:
            if st.orient:
                best, _ = A.best_orientation(shape, st.critical_deg)
            else:
                best = A.analyze_orientation(shape, "as is", None, st.critical_deg)
            placed = A.oriented(shape, best)
            bb = measure.bounding_box(placed)
            pp.orientation, pp.rotation = best.name, (list(best.rotation[0]) + [best.rotation[1]] if best.rotation else None)
            pp.size = [round(bb.size.X, 2), round(bb.size.Y, 2), round(bb.size.Z, 2)]
            pp.overhang_area, pp.support_volume = best.overhang_area, best.support_volume
            pp.overhang_risk = _risk(best.overhang_area, float(shape.area))
            wall = walls.get(p.name)
            if wall is None:
                try:
                    sample = measure.min_wall(shape, samples=300)
                    wall = round(sample.thickness, 3) if sample else None
                except Exception:
                    wall = None
            pp.min_wall = wall
            pp.thin = wall is not None and wall < st.min_wall
            shapes[p.name] = placed
        out.append(pp)
    plates = _pack([q for q in out if q.included], st)
    n_in = sum(1 for q in out if q.included and not q.problem)
    plan = PrintPlan(st, out, plates)
    thin = [q.name for q in out if q.included and q.thin]
    skipped_bought = [q.name for q in out if q.bought and not q.included]
    too_big = [q.name for q in out if q.problem]
    lines = [f"{n_in} printable part(s) on {plates} plate(s) of {st.bed[0]:g} x {st.bed[1]:g} mm"]
    if skipped_bought:
        lines.append(f"{len(skipped_bought)} bought part(s) left out: {', '.join(skipped_bought[:8])}" + (" ..." if len(skipped_bought) > 8 else ""))
    if thin:
        lines.append(f"walls under {st.min_wall:g} mm in: {', '.join(thin)}")
    if too_big:
        lines.append(f"cannot be printed as is: {', '.join(too_big)}")
    plan.summary = "; ".join(lines)
    plan._shapes = shapes                                     # type: ignore[attr-defined]
    return plan


def _pack(items: list[PrintPart], st: PrintSettings) -> int:
    """Shelf packing, tallest footprint first, first fit over all plates (a part may turn 90 degrees about
    Z). Sets plate/x/y/turned on each part; returns the number of plates."""
    W, D = st.bed[0] - 2 * st.margin, st.bed[1] - 2 * st.margin
    order = sorted(items, key=lambda q: (max(q.size[0], q.size[1]) if q.size else 0, q.volume), reverse=True)
    plates: list[list[dict]] = []                             # per plate: shelves {y, h, x}
    for q in order:
        if not q.size:
            continue
        w, d, h = q.size
        if h > st.bed_height:
            q.problem = f"{h:g} mm tall: over the {st.bed_height:g} mm build height (split or scale it)"
            continue
        options = [(w, d, False), (d, w, True)]
        if all(a > W + 1e-9 or b > D + 1e-9 for a, b, _ in options):
            q.problem = f"{w:g} x {d:g} mm footprint: larger than the {st.bed[0]:g} x {st.bed[1]:g} mm bed (split or scale it)"
            continue
        placed = False
        for pi, shelves in enumerate(plates + [[]]):
            for a, b, turned in sorted(options, key=lambda o: o[1]):          # the flatter footprint first
                if a > W + 1e-9 or b > D + 1e-9:
                    continue
                for sh in shelves:                                         # an existing shelf with room
                    if b <= sh["h"] + 1e-9 and sh["x"] + a <= W + 1e-9:
                        q.plate, q.turned = pi + 1, turned
                        q.x, q.y = sh["x"] + a / 2 - W / 2, sh["y"] + b / 2 - D / 2
                        sh["x"] += a + st.spacing
                        placed = True
                        break
                if placed:
                    break
                top = (shelves[-1]["y"] + shelves[-1]["h"] + st.spacing) if shelves else 0.0
                if top + b <= D + 1e-9:                                     # a new shelf on this plate
                    shelves.append({"y": top, "h": b, "x": a + st.spacing})
                    q.plate, q.turned = pi + 1, turned
                    q.x, q.y = a / 2 - W / 2, top + b / 2 - D / 2
                    placed = True
                    break
            if placed:
                if pi == len(plates):
                    plates.append(shelves)
                break
        q.x, q.y = round(q.x, 2), round(q.y, 2)
    return len(plates)


def placed_shape(plan: PrintPlan, q: PrintPart) -> bd.Shape:
    """The part as it sits on its plate (oriented, turned, moved to its spot)."""
    s = plan._shapes[q.name]                                   # type: ignore[attr-defined]
    if q.turned:
        s = s.rotate(bd.Axis.Z, 90)
    return s.moved(bd.Location((q.x, q.y, 0)))


# ---------------------------------------------------------------------------- meshes
#
# Every file is written from one indexed mesh per part that has been checked first: OCCT's triangles,
# welded (faces share edge nodes up to rounding), every edge used by exactly two triangles, consistent
# winding, and the solid's volume. The STL and the 3MF are both written from that same mesh, and read
# back and checked again before they are saved.

@dataclass
class Mesh:
    vertices: "object"            # (n, 3) float64
    faces: "object"               # (m, 3) int64
    name: str = ""
    color: str = "#B0BEC5"


def part_mesh(shape: bd.Shape, tolerance: float = 0.02, angular: float = 0.2, name: str = "", color: str = "#B0BEC5") -> Mesh:
    import numpy as np
    from . import fastmesh
    fastmesh.mesh_shape(shape, tolerance, angular)
    tri = fastmesh.all_triangles(shape).astype(np.float64).reshape(-1, 3)
    key = np.round(tri / 1e-5).astype(np.int64)                  # weld nodes closer than 10 nm
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    faces = inverse.reshape(-1, 3).astype(np.int64)
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    return Mesh(tri[first], faces, name, color)


def mesh_problem(m: Mesh, expected_volume: float | None = None) -> str | None:
    """None when the mesh is a closed, consistently wound surface with the solid's volume; else why not
    (with where, so the model can be fixed)."""
    import numpy as np
    if len(m.faces) == 0:
        return "the mesh is empty"
    e = np.concatenate([m.faces[:, [0, 1]], m.faces[:, [1, 2]], m.faces[:, [2, 0]]])
    und = np.sort(e, axis=1)
    uniq, counts = np.unique(und, axis=0, return_counts=True)
    open_e, nonman = uniq[counts == 1], uniq[counts > 2]
    if len(open_e) or len(nonman):
        bad = np.concatenate([open_e, nonman])
        pts = m.vertices[bad.ravel()]
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        where = "near (" + ", ".join(f"{a:.1f}" if abs(a - b) < 0.05 else f"{a:.1f}..{b:.1f}" for a, b in zip(lo, hi)) + ")"
        kinds = []
        if len(open_e):
            kinds.append(f"{len(open_e)} open edge(s)")
        if len(nonman):
            kinds.append(f"{len(nonman)} edge(s) shared by more than two faces (bodies touching along a line or point)")
        return " and ".join(kinds) + f" {where}: the solid is not closed there; fix the model (overlap the bodies or separate them)"
    _, dir_counts = np.unique(e, axis=0, return_counts=True)        # each directed edge once if winding agrees
    if (dir_counts > 1).any():
        return "inconsistent triangle winding (some faces point inwards)"
    v0, v1, v2 = (m.vertices[m.faces[:, k]] for k in range(3))
    volume = float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0)
    if volume <= 0:
        return "the mesh encloses no volume (inside out)"
    if expected_volume and expected_volume > 0 and abs(volume - expected_volume) / expected_volume > 0.02:
        return f"the mesh volume {volume:.1f} mm^3 differs from the solid's {expected_volume:.1f} mm^3 by over 2%"
    return None


def stl_bytes(meshes: list[Mesh]) -> bytes:
    import numpy as np
    tris = np.concatenate([m.vertices[m.faces] for m in meshes]).astype(np.float32)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-20)
    rec = np.zeros(len(tris), dtype=[("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, tris
    header = b"eitri print export".ljust(80, b" ")
    return header + len(tris).to_bytes(4, "little") + rec.tobytes()


def threemf_bytes(meshes: list[Mesh]) -> bytes:
    """A 3MF package (core spec, millimetres): one named object per mesh with its colour, all on the build plate."""
    from xml.sax.saxutils import quoteattr
    out = io.BytesIO()
    colors = []
    for m in meshes:
        c = (m.color or "#B0BEC5").lstrip("#")[:6].upper()
        colors.append(f'#{c if len(c) == 6 else "B0BEC5"}FF')
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
             '<metadata name="Application">eitri</metadata>\n<resources>\n<basematerials id="1">\n']
    parts += [f'<base name={quoteattr(m.name or "part")} displaycolor="{c}"/>\n' for m, c in zip(meshes, colors)]
    parts.append("</basematerials>\n")
    for i, m in enumerate(meshes):
        parts.append(f'<object id="{i + 2}" type="model" name={quoteattr(m.name or f"part{i + 1}")} pid="1" pindex="{i}">'
                     "<mesh><vertices>")
        parts.append("".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in m.vertices))
        parts.append("</vertices><triangles>")
        parts.append("".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in m.faces))
        parts.append("</triangles></mesh></object>\n")
    parts.append("</resources>\n<build>\n")
    parts += [f'<item objectid="{i + 2}"/>\n' for i in range(len(meshes))]
    parts.append("</build>\n</model>\n")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", "".join(parts))
    return out.getvalue()


def read_back(data: bytes, fmt: str) -> list[Mesh]:
    """Parse a file this module wrote (binary STL, or 3MF), for the check before it is saved."""
    import numpy as np
    if fmt == "stl":
        n = int.from_bytes(data[80:84], "little")
        rec = np.frombuffer(data, dtype=[("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")], count=n, offset=84)
        tri = rec["v"].astype(np.float64).reshape(-1, 3)
        key = np.round(tri / 1e-4).astype(np.int64)                 # float32: weld at 0.1 um
        _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
        return [Mesh(tri[first], inverse.reshape(-1, 3).astype(np.int64))]
    import xml.etree.ElementTree as ET
    ns = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    out = []
    for obj in root.iter(ns + "object"):
        v = np.array([[float(e.get("x")), float(e.get("y")), float(e.get("z"))] for e in obj.iter(ns + "vertex")])
        f = np.array([[int(e.get("v1")), int(e.get("v2")), int(e.get("v3"))] for e in obj.iter(ns + "triangle")], dtype=np.int64)
        out.append(Mesh(v, f, obj.get("name") or ""))
    return out


def _file_bytes(meshes: list[Mesh], fmt: str, volumes: list[float]) -> tuple[bytes | None, str | None]:
    """The file's bytes after reading them back and checking them; or the problem."""
    data = stl_bytes(meshes) if fmt == "stl" else threemf_bytes(meshes)
    back = read_back(data, fmt)
    if fmt == "stl":
        err = mesh_problem(back[0], sum(volumes)) if len(meshes) == 1 else None    # a plate STL is one soup
        if len(meshes) > 1:
            n = sum(len(m.faces) for m in meshes)
            if len(back[0].faces) != n:
                err = f"the STL holds {len(back[0].faces)} triangles, expected {n}"
    else:
        if len(back) != len(meshes):
            return None, f"the 3MF holds {len(back)} objects, expected {len(meshes)}"
        err = next((f"{m.name}: {e}" for m, v in zip(back, volumes) if (e := mesh_problem(m, v))), None)
    return (None, f"{fmt.upper()} check after writing: {err}") if err else (data, None)


def export_plan(plan: PrintPlan, out_dir: str | Path, formats: tuple[str, ...] = ("stl", "3mf"),
                stem: str = "print", tolerance: float = 0.02, angular: float = 0.2, make_zip: bool = True) -> dict:
    """Write each included part and each plate as STL and 3MF. Each part's mesh is checked (closed,
    manifold, consistently wound, the solid's volume) and every file is read back and checked again; a
    part that fails is not written and is left off its plate, with the reason and where. Returns
    {files, problems, zip, parts_written}."""
    out = Path(out_dir)
    (out / "parts").mkdir(parents=True, exist_ok=True)
    (out / "plates").mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    problems: dict[str, str] = {}
    good: list[PrintPart] = []
    meshes: dict[str, Mesh] = {}
    for q in plan.parts:
        if q.included and q.problem:                           # planned out (too big, too tall): say so here too
            problems[q.name] = q.problem
        if not q.included or q.problem or q.plate is None:
            continue
        m = part_mesh(plan._shapes[q.name], tolerance, angular, q.name, q.color)     # type: ignore[attr-defined]
        err = mesh_problem(m, q.volume)
        if err:
            problems[q.name] = err
            continue
        blobs = {}
        for fmt in formats:
            data, err = _file_bytes([m], fmt, [q.volume])
            if err:
                problems[q.name] = err
                break
            blobs[fmt] = data
        if q.name in problems:
            continue
        for fmt, data in blobs.items():
            f = out / "parts" / f"{_safe(q.name)}.{fmt}"
            f.write_bytes(data)
            files.append(str(f))
        meshes[q.name] = m
        good.append(q)
    for n in range(1, plan.plates + 1):
        on = [q for q in good if q.plate == n]
        if not on:
            continue
        placed = []
        for q in on:                                          # the verified part mesh, moved to its spot
            import numpy as np
            m = meshes[q.name]
            v = m.vertices.copy()
            if q.turned:
                v = np.column_stack([-v[:, 1], v[:, 0], v[:, 2]])
            v[:, 0] += q.x
            v[:, 1] += q.y
            placed.append(Mesh(v, m.faces, q.name, q.color))
        for fmt in formats:
            data, err = _file_bytes(placed, fmt, [q.volume for q in on])
            if err:
                problems[f"plate {n}"] = err
                continue
            f = out / "plates" / f"{stem}_plate{n}.{fmt}"
            f.write_bytes(data)
            files.append(str(f))
    manifest = {"summary": plan.summary, "settings": plan.to_dict()["settings"], "problems": problems,
                "parts": [{k: v for k, v in asdict(q).items() if k not in ("color",)} for q in plan.parts]}
    (out / "print_plan.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    files.append(str(out / "print_plan.json"))
    zpath = None
    if make_zip:
        zpath = out / f"{stem}_print.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, str(Path(f).relative_to(out)))
    return {"files": files, "problems": problems, "zip": str(zpath) if zpath else None, "parts_written": len(good)}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "part"


# ---------------------------------------------------------------------------- pictures

def part_thumbnail(plan: PrintPlan, q: PrintPart, path: str | Path, size: tuple[int, int] = (220, 165)) -> str:
    """An iso picture of the part as it will print (the orientation preview): the part alone on a
    transparent background, so it sits on any theme."""
    import numpy as np
    from PIL import Image
    from .model import Part, Parts
    from .render import VIEW_DIRS, make_camera, rasterize, tessellate_parts
    w, h = size[0] * 2, size[1] * 2                                 # drawn at 2x, then reduced: smooth edges
    meshes = tessellate_parts(Parts([Part(q.name, plan._shapes[q.name], q.color)]))    # type: ignore[attr-defined]
    pts = np.vstack([m.vertices for m in meshes]) if meshes else np.zeros((1, 3))
    direction, up = VIEW_DIRS["iso"]
    cam = make_camera(direction, up, pts, w, h, 0.1)
    rgb, z = rasterize(meshes, cam, edge_color=(25, 28, 34), edge_alpha=0.7)
    rgba = np.dstack([rgb, np.where(np.isfinite(z), 255, 0).astype(np.uint8)])
    Image.fromarray(rgba, "RGBA").resize(size, Image.LANCZOS).save(path)
    return str(path)


def plate_picture(plan: PrintPlan, n: int, path: str | Path, px: int = 720, theme: str = "dark", caption: bool = False) -> str:
    """Plate n from above at a fixed scale, the bed drawn to size, each part shaded in its colour."""
    import numpy as np
    from PIL import Image, ImageDraw
    from .model import Part, Parts
    from .render import _font, make_camera, rasterize, tessellate_parts
    W, D = plan.settings.bed
    on = [q for q in plan.parts if q.included and q.plate == n and not q.problem]
    pad = 26
    scale = (px - 2 * pad) / max(W, D)
    w_img, h_img = int(W * scale) + 2 * pad, int(D * scale) + 2 * pad
    bg = (17, 20, 26) if theme == "dark" else (246, 247, 249)
    if on:
        meshes = tessellate_parts(Parts([Part(q.name, placed_shape(plan, q), q.color) for q in on]), quality=0.7)
        pts = np.array([[-W / 2, -D / 2, 0], [W / 2, D / 2, 0]], dtype=float)
        cam = make_camera((0, 0, 1), (0, 1, 0), pts, w_img, h_img, fixed_scale=scale, center=np.array([0.0, 0.0, 0.0]))
        rgb, z = rasterize(meshes, cam, edge_color=(20, 22, 28), edge_alpha=0.55)
        img = Image.fromarray(rgb, "RGB")
        arr = np.asarray(img).copy()
        arr[~np.isfinite(z)] = bg
        img = Image.fromarray(arr, "RGB")
    else:
        img = Image.new("RGB", (w_img, h_img), bg)
    draw = ImageDraw.Draw(img, "RGBA")
    line = (110, 120, 135, 255) if theme == "dark" else (150, 156, 166, 255)
    grid = (255, 255, 255, 18) if theme == "dark" else (0, 0, 0, 16)
    x0, y0 = pad, pad
    for gx in range(0, int(W) + 1, 10):
        draw.line([(x0 + gx * scale, y0), (x0 + gx * scale, y0 + D * scale)], fill=grid)
    for gy in range(0, int(D) + 1, 10):
        draw.line([(x0, y0 + gy * scale), (x0 + W * scale, y0 + gy * scale)], fill=grid)
    draw.rounded_rectangle([x0, y0, x0 + W * scale, y0 + D * scale], radius=8, outline=line, width=2)
    if caption:
        draw.text((x0, 6), f"plate {n} · {len(on)} part(s) · {W:g} x {D:g} mm", fill=line, font=_font(13))
    img.save(path)
    return str(path)


def plan_parts_from(parts_like) -> list:
    """Parts with solids only (helper for callers holding a Parts)."""
    return [p for p in parts_like if list(p.shape.solids())]


def human_count(plan: PrintPlan) -> str:
    n = sum(1 for q in plan.parts if q.included and not q.problem)
    return f"{n} printable part{'s' if n != 1 else ''}, {plan.plates} plate{'s' if plan.plates != 1 else ''}"



# ---------------------------------------------------------------------------- a project's print export

def settings_from(d: dict | None) -> PrintSettings:
    """PrintSettings from a request or the CLI (missing keys keep the defaults)."""
    d = d or {}
    st = PrintSettings()
    bed = d.get("bed")
    if isinstance(bed, str):
        w, _, depth = bed.lower().partition("x")
        bed = [w, depth or w]
    if bed:
        st.bed = (float(bed[0]), float(bed[1]))
    for key, conv in (("bed_height", float), ("spacing", float), ("margin", float), ("min_wall", float),
                      ("include_bought", bool), ("orient", bool), ("critical_deg", float)):
        if d.get(key) is not None:
            setattr(st, key, conv(d[key]))
    for key in ("include", "exclude"):
        v = d.get(key)
        if isinstance(v, str):
            v = [x.strip() for x in v.split(",") if x.strip()]
        if v:
            setattr(st, key, [str(x) for x in v])
    if not (20 <= st.bed[0] <= 2000 and 20 <= st.bed[1] <= 2000):
        raise ValueError("the bed must be between 20 and 2000 mm on each side")
    return st


class ProjectPrinter:
    """The print export of one project, as the editor's dialog uses it: plan (with pictures) and export,
    the plan kept for the export as long as the model and the settings are the same."""

    def __init__(self, project):
        self.project = project
        self._key = None
        self._plan: PrintPlan | None = None

    @property
    def folder(self) -> Path:
        return self.project.out_dir / "print"

    def _report(self) -> dict | None:
        rev = self.project.last_ok_revision()            # the current model file's last good build, if the file has not changed since
        try:
            data = self.project.build_result(rev) if rev else None
            if data and rev.created >= self.project.model_path.stat().st_mtime:
                return data.get("report")
        except OSError:
            pass
        return None

    def plan(self, settings: PrintSettings, pictures: bool = True) -> PrintPlan:
        from .model import load_model
        key = (self.project.model_path.stat().st_mtime, json.dumps(asdict(settings), sort_keys=True, default=str))
        if self._plan is not None and key == self._key:
            return self._plan
        parts = load_model(self.project.model_path).build()
        plan = make_plan(parts, settings, report=self._report())
        if pictures:
            thumbs = self.folder / "thumbs"
            if thumbs.exists():
                for f in thumbs.glob("*.png"):
                    f.unlink()
            thumbs.mkdir(parents=True, exist_ok=True)
            for q in plan.parts:
                if q.included and q.name in plan._shapes:              # type: ignore[attr-defined]
                    part_thumbnail(plan, q, thumbs / f"{_safe(q.name)}.png")
            for f in self.folder.glob("plate*.png"):
                f.unlink()
            for n in range(1, plan.plates + 1):
                plate_picture(plan, n, self.folder / f"plate{n}.png")
        self._key, self._plan = key, plan
        return plan

    def plan_json(self, settings: PrintSettings) -> dict:
        plan = self.plan(settings)
        stamp = int(time_now())
        d = plan.to_dict()
        for q in d["parts"]:
            q["thumb"] = f"print/file?path=thumbs/{_safe(q['name'])}.png&t={stamp}" if q["included"] else None
        d["plate_images"] = [f"print/file?path=plate{n}.png&t={stamp}" for n in range(1, plan.plates + 1)]
        d["headline"] = human_count(plan)
        d["model"] = self.project.name
        return d

    def export(self, settings: PrintSettings, formats=("stl", "3mf")) -> dict:
        plan = self.plan(settings, pictures=False) if self._plan is None else self.plan(settings)
        files_dir = self.folder / "files"
        if files_dir.exists():
            import shutil
            shutil.rmtree(files_dir)
        res = export_plan(plan, files_dir, formats=formats, stem=self.stem())
        rel = lambda f: str(Path(f).relative_to(self.folder))               # noqa: E731
        return {"ok": True, "headline": human_count(plan), "problems": res["problems"],
                "parts_written": res["parts_written"], "files": [rel(f) for f in res["files"]],
                "zip": f"print/file?path={rel(res['zip'])}" if res.get("zip") else None,
                "folder": str(files_dir)}

    def stem(self) -> str:
        """File names from the project's title ("V8 engine kit" -> v8_engine_kit), else its model name."""
        try:
            title = json.loads((self.project.folder / ".eitri" / "web.json").read_text(encoding="utf-8")).get("title")
        except (OSError, json.JSONDecodeError, AttributeError):
            title = None
        slug = re.sub(r"[^a-z0-9]+", "_", str(title or "").lower()).strip("_")
        return slug[:48] or self.project.name

    def file(self, rel: str) -> Path | None:
        """A file under out/print (thumbnails, plates, exported meshes, the zip)."""
        root = self.folder.resolve()
        f = (root / rel).resolve()
        return f if root in f.parents and f.is_file() else None


def time_now() -> float:
    import time
    return time.time()
