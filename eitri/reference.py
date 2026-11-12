"""Reference parts: a customer's STEP file as read-only geometry the design is built around.

    from eitri import *

    def build(p):
        motor = reference_step("inputs/customer_motor.step", name="motor", clearance=1.0, contact=["bracket"])
        face_holes = [h for h in motor.holes() if h.axis_name == "Z" and h.diameter < 3.5]
        bracket = ...                                    # designed against motor.bbox and face_holes
        for h in face_holes:
            motor.mount(h, part="bracket", screw="M3", series="medium", coaxial_tol=0.1)
        return {"bracket": bracket}                      # the motor is added to the assembly by reference_step

What eitri guarantees about a reference part:

- It is IMMUTABLE. The file's SHA-256 is recorded in the project (eitri.json "references") the first time a build
  reads it. If the file later changes, every build fails until a person accepts the new file on the command line
  (`eitri reference accept <project> inputs/<file>`); the agent has no tool for that. Direct edits that name it
  are refused, and every build proves its geometry after the direct edits is byte-for-byte what was loaded.
- It takes part in the build like any part: the viewer, the review image, the STEP/GLB exports, the report's
  interference and clearance pairs. On top of that each reference gets checks of its own: no designed part may
  overlap it; with `clearance=`, every designed part not listed in `contact=` keeps at least that gap; every
  declared mount is coaxial within its stated tolerance and its clearance hole follows ISO 273 (eitri.lib.fasteners).
- A reference declared once stays declared: a later build of the same model file that no longer declares it, or
  declares fewer mounts on it, fails (`eitri reference remove` is the person's way to drop one).
- The file is untrusted input. It must sit in the project's inputs/ folder (the one folder a sandboxed build is
  given), is size- and entity-capped and pre-scanned before OpenCASCADE parses it, is parsed inside the build
  (the subprocess, warm worker or sandbox that runs the model), and nothing in it is ever executed: STEP is data.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

__all__ = ["reference_step", "Reference", "HoleFeature", "ReferenceError"]

STATE_KEY = "references"          # eitri.json: {model file: {relative path: {sha256, bytes, name, mounts, accepted, by}}}
MAX_REFERENCES = 16               # per model file
REFERENCE_COLOR = "#9AA4AE"       # a neutral grey: customer geometry, not ours
REFERENCE_OPACITY = 0.55
OVERLAP_TOL_MM3 = 1e-3            # a numerical tolerance of the boolean, not a physical figure


def max_bytes() -> int:
    """The largest STEP file a reference may be (EITRI_REFERENCE_MAX_MB, default 20 MB). A sandboxed build also
    gets at most executor.MAX_INPUT_BYTES of model + inputs in total."""
    return int(float(os.environ.get("EITRI_REFERENCE_MAX_MB", "") or 20) * 1024 * 1024)


def max_entities() -> int:
    """The most STEP entity instances (#n = ...) a reference may hold (EITRI_REFERENCE_MAX_ENTITIES, default 500k)."""
    return int(float(os.environ.get("EITRI_REFERENCE_MAX_ENTITIES", "") or 500_000))


def max_faces() -> int:
    """The most B-rep faces a loaded reference may have (EITRI_REFERENCE_MAX_FACES, default 20k): the viewer
    meshes every face as its own pickable node."""
    return int(float(os.environ.get("EITRI_REFERENCE_MAX_FACES", "") or 20_000))


class ReferenceError(ValueError):
    """A reference part could not be used: a bad path, a file over a cap, a malformed or changed file."""


# ---------------------------------------------------------------------------- the build-time context

_local = threading.local()
_ACCEPTED: dict[str, dict] = {}            # set by the runner for each build: what the project accepted (by relative path)
_PARSED: dict[str, Any] = {}               # sha256 -> the loaded (unplaced) compound; a warm worker parses a file once
_PARSED_MAX = 8


_ACCEPTED_DIR: list = [None]               # the model folder _ACCEPTED belongs to (a warm worker builds many projects)


def set_accepted(accepted: dict | None, model_dir: Path | None = None) -> None:
    """The runner hands over what the project accepted for this model file before build(p) runs."""
    _ACCEPTED.clear()
    _ACCEPTED.update({str(k): dict(v) for k, v in (accepted or {}).items() if isinstance(v, dict)})
    _ACCEPTED_DIR[0] = Path(model_dir).resolve() if model_dir is not None else None


def begin(model_dir: Path) -> None:
    _local.model_dir = Path(model_dir).resolve()
    _local.declared = []


def take() -> list[dict]:
    """The references build(p) declared (each a plain dict, kept on the Parts as `parts.references`)."""
    out = list(getattr(_local, "declared", None) or [])
    _local.declared = None
    _local.model_dir = None
    return out


def _context_dir() -> Path:
    d = getattr(_local, "model_dir", None)
    return d if d is not None else Path.cwd().resolve()


# ---------------------------------------------------------------------------- reading the file safely

def resolve_path(path: str | os.PathLike, model_dir: Path) -> tuple[Path, str]:
    """(absolute path, path relative to the model folder) of a reference file. It must be a .step/.stp file in the
    project's inputs/ folder: not a link, not outside the folder. A bare file name is looked up in inputs/."""
    raw = str(path).replace("\\", "/").strip()
    if not raw:
        raise ReferenceError("reference_step needs a file path, e.g. reference_step('inputs/motor.step')")
    rel = raw if "/" in raw else f"inputs/{raw}"
    if rel.startswith("./"):
        rel = rel[2:]
    parts = rel.split("/")
    if os.path.isabs(raw) or len(parts) != 2 or parts[0] != "inputs" or parts[1] in ("", ".", "..") or parts[1].startswith("."):
        raise ReferenceError(f"reference file {raw!r}: reference files live directly in the project's inputs/ folder "
                             "(the only folder a sandboxed build is given), e.g. 'inputs/motor.step'. "
                             "`eitri reference add <project> <file.step>` copies one there and accepts it.")
    from .executor import safe_name
    if not safe_name(parts[1]):
        raise ReferenceError(f"reference file name {parts[1]!r}: use letters, digits, '_', '-' and '.' only (the names a "
                             "sandboxed build is given); rename the file")
    if Path(parts[1]).suffix.lower() not in (".step", ".stp"):
        raise ReferenceError(f"reference file {raw!r} is not a .step/.stp file")
    folder = model_dir / "inputs"
    target = folder / parts[1]
    if folder.is_symlink() or target.is_symlink():
        raise ReferenceError(f"reference file {rel!r} is a symbolic link; copy the real file into inputs/")
    if not target.is_file():
        raise ReferenceError(f"reference file {rel!r} does not exist in {model_dir}")
    if target.resolve().parent != folder.resolve():
        raise ReferenceError(f"reference file {rel!r} resolves outside the project's inputs/ folder")
    return target, rel


_ENTITY = re.compile(rb"#\d+\s*=")


def prescan(data: bytes, label: str = "the file") -> int:
    """Check that bytes look like one self-contained ISO 10303-21 (STEP) text file within the caps, before any
    parser sees them. Returns the entity count; raises ReferenceError otherwise."""
    if len(data) > max_bytes():
        raise ReferenceError(f"{label} is {len(data) / 1e6:.1f} MB; a reference file may be at most "
                             f"{max_bytes() / 1e6:.0f} MB (EITRI_REFERENCE_MAX_MB). Export a simplified STEP "
                             "(no internal detail, no fasteners) of the customer part.")
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n")
    if not head.startswith(b"ISO-10303-21;"):
        if head[:2] == b"\x1f\x8b" or head[:4] == b"PK\x03\x04":
            raise ReferenceError(f"{label} is compressed (gzip/zip, e.g. .stpZ or STEP-XML in a zip); unpack it to a plain .step")
        raise ReferenceError(f"{label} is not a STEP file (it does not start with 'ISO-10303-21;')")
    if b"END-ISO-10303-21;" not in data[-4096:]:
        raise ReferenceError(f"{label} is truncated or malformed (no 'END-ISO-10303-21;' at its end)")
    if b"\x00" in data:
        raise ReferenceError(f"{label} holds binary (NUL) bytes; a STEP file is text")
    if re.search(rb"=\s*DOCUMENT_FILE\s*\(", data):
        raise ReferenceError(f"{label} refers to other files (DOCUMENT_FILE external references); export one "
                             "self-contained STEP with all the geometry in it")
    bad = _unbalanced(data)
    if bad:
        raise ReferenceError(f"{label} is malformed: {bad}")
    n = len(_ENTITY.findall(data))
    if n == 0:
        raise ReferenceError(f"{label} has no entities (an empty DATA section)")
    if n > max_entities():
        raise ReferenceError(f"{label} has {n} STEP entities; a reference may have at most {max_entities()} "
                             "(EITRI_REFERENCE_MAX_ENTITIES). Export a simplified STEP of the customer part.")
    return n


_STRING = re.compile(rb"'(?:[^']|'')*'")


def _unbalanced(data: bytes) -> str | None:
    """A syntax pre-check of the DATA section: every statement (up to ';', strings and comments removed) has balanced
    parentheses and starts with an entity name. OpenCASCADE reads a broken file as far as it can without failing,
    so a file cut in the middle would otherwise load as 'no solids' instead of being named malformed."""
    body = re.sub(rb"/\*.*?\*/", b" ", _STRING.sub(b"''", data), flags=re.S)
    start = body.find(b"DATA;")
    end = body.rfind(b"ENDSEC;")
    if start < 0 or end < start:
        return "no DATA section"
    for k, stmt in enumerate(body[start + 5:end].split(b";")):
        stmt = stmt.strip()
        if not stmt or stmt == b"ENDSEC" or stmt.startswith(b"DATA"):     # several DATA sections (AP242 ed2) are allowed
            continue
        if stmt.count(b"(") != stmt.count(b")"):
            return f"unbalanced parentheses in statement {k + 1} ({stmt[:60].decode('latin-1', 'replace')}...)"
        if not re.match(rb"#\d+\s*=", stmt):
            return f"statement {k + 1} is not an entity instance ({stmt[:60].decode('latin-1', 'replace')}...)"
    return None


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def _parse(target: Path, rel: str, sha: str):
    """The file's solids as one compound (cached by content hash)."""
    hit = _PARSED.get(sha)
    if hit is not None:
        return hit
    import build123d as bd
    try:
        comp = bd.import_step(str(target))
    except Exception as exc:                       # OCCT's reader refused it
        raise ReferenceError(f"reference file {rel!r} could not be read as STEP: {type(exc).__name__}: {exc}") from None
    solids = list(comp.solids())
    if not solids:
        raise ReferenceError(f"reference file {rel!r} holds no solid bodies (a surface or wireframe model, or a malformed file?); "
                             "interference checks need solids: export the part as solids")
    shape = solids[0] if len(solids) == 1 else bd.Compound(solids)
    nf = len(shape.faces())
    if nf > max_faces():
        raise ReferenceError(f"reference file {rel!r} has {nf} faces; a reference may have at most {max_faces()} "
                             "(EITRI_REFERENCE_MAX_FACES). Export a simplified STEP (defeatured, no threads or text).")
    _PARSED[sha] = shape
    while len(_PARSED) > _PARSED_MAX:
        _PARSED.pop(next(iter(_PARSED)))
    return shape


def load_checked(target: Path, rel: str) -> tuple[Any, str, int, int]:
    """(shape, sha256, bytes, entities): read, cap, pre-scan, check against the accepted hash, parse, and prove
    the file did not change while it was parsed."""
    size = target.stat().st_size
    if size > max_bytes():
        raise ReferenceError(f"reference file {rel!r} is {size / 1e6:.1f} MB; a reference file may be at most "
                             f"{max_bytes() / 1e6:.0f} MB (EITRI_REFERENCE_MAX_MB). Export a simplified STEP of the customer part.")
    data = target.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    entities = prescan(data, f"reference file {rel!r}")
    _check_accepted(rel, sha)
    shape = _parse(target, rel, sha)
    if sha256_file(target)[0] != sha:
        raise ReferenceError(f"reference file {rel!r} changed while it was being read; build again")
    return shape, sha, len(data), entities


def _check_accepted(rel: str, sha: str) -> None:
    if _ACCEPTED_DIR[0] is None or _ACCEPTED_DIR[0] != _context_dir():
        return                                   # not this project's list (the runner re-checks every build anyway)
    acc = _ACCEPTED.get(rel)
    if acc and acc.get("sha256") and acc["sha256"] != sha:
        raise ReferenceError(changed_message(rel, acc["sha256"], sha))


def changed_message(rel: str, old: str, new: str) -> str:
    return (f"reference file {rel} CHANGED since it was accepted (sha256 {old[:16]}… is now {new[:16]}…). "
            "A reference part is read-only: restore the original file. If the customer really sent a new "
            f"revision, a person accepts it on the command line: `eitri reference accept <project> {rel}`.")


# ---------------------------------------------------------------------------- what the model file gets

@dataclass
class HoleFeature:
    """A full-round cylindrical feature of a reference part, in the assembly's coordinates (mm)."""
    kind: str                                   # "hole" (concave) or "boss" (convex: pins, pilots, shafts)
    diameter: float
    axis: tuple[float, float, float]            # unit direction
    center: tuple[float, float, float]          # middle of its length on the axis
    start: tuple[float, float, float]           # axis point at one end
    end: tuple[float, float, float]             # axis point at the other end
    depth: float
    through: bool | None

    @property
    def axis_name(self) -> str:
        for n, c in zip("XYZ", self.axis):
            if abs(abs(c) - 1) < 1e-3:
                return n
        return "({:.2f}, {:.2f}, {:.2f})".format(*self.axis)

    @property
    def radius(self) -> float:
        return self.diameter / 2

    def describe(self) -> str:
        kind = self.kind if self.kind != "hole" else ("through hole" if self.through else "blind hole" if self.through is False else "hole")
        c = self.center
        return f"Ø{self.diameter:g} {kind}, depth {self.depth:g}, axis {self.axis_name}, centre ({c[0]:g}, {c[1]:g}, {c[2]:g})"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "diameter": self.diameter, "axis": list(self.axis), "center": list(self.center),
                "start": list(self.start), "end": list(self.end), "depth": self.depth, "through": self.through}


def _r(v: float, n: int = 4) -> float:
    return round(float(v), n) + 0.0


def _features(shape) -> list[HoleFeature]:
    from . import measure
    out = []
    for h in measure.cylindrical_features(shape):
        out.append(HoleFeature(h.kind, _r(h.diameter, 3), (_r(h.axis.X, 6), _r(h.axis.Y, 6), _r(h.axis.Z, 6)),
                               (_r(h.center.X), _r(h.center.Y), _r(h.center.Z)),
                               (_r(h.start.X), _r(h.start.Y), _r(h.start.Z)), (_r(h.end.X), _r(h.end.Y), _r(h.end.Z)),
                               _r(h.depth, 3), h.through))
    out.sort(key=lambda f: (f.kind, f.diameter, f.center))
    return out


class Reference:
    """A loaded reference part. Read-only: `.shape` hands out a copy; the assembly holds its own."""

    def __init__(self, record: dict, shape):
        self._record = record
        self._shape = shape

    # identity
    @property
    def name(self) -> str:
        return self._record["name"]

    @property
    def path(self) -> str:
        return self._record["path"]

    @property
    def sha256(self) -> str:
        return self._record["sha256"]

    # geometry
    @property
    def shape(self):
        """A copy of the placed geometry, for booleans against it (cut a pocket the shape of the part...)."""
        from .model import _copy_shape
        return _copy_shape(self._shape)

    @property
    def bbox(self):
        from .measure import bounding_box
        return bounding_box(self._shape)

    @property
    def size(self):
        return self.bbox.size

    @property
    def volume(self) -> float:
        from .measure import signed_volume
        return abs(signed_volume(self._shape))

    def features(self) -> list[HoleFeature]:
        """Every full-round cylindrical hole and boss, with axis, diameter and position."""
        return _features(self._shape)

    def holes(self, min_diameter: float = 0.0, max_diameter: float = math.inf, axis: str | None = None) -> list[HoleFeature]:
        return [f for f in self.features() if f.kind == "hole" and min_diameter <= f.diameter <= max_diameter
                and (axis is None or f.axis_name == axis.upper())]

    def bosses(self, min_diameter: float = 0.0, max_diameter: float = math.inf, axis: str | None = None) -> list[HoleFeature]:
        return [f for f in self.features() if f.kind == "boss" and min_diameter <= f.diameter <= max_diameter
                and (axis is None or f.axis_name == axis.upper())]

    def hole(self, near, diameter: float | None = None, kind: str = "hole") -> HoleFeature:
        """The hole (or boss, kind="boss") whose axis passes closest to the point `near`."""
        p = _xyz(near)
        cands = [f for f in self.features() if f.kind == kind and (diameter is None or abs(f.diameter - diameter) < 0.05)]
        if not cands:
            raise ReferenceError(f"reference {self.name!r} has no {kind}" + (f" of Ø{diameter:g}" if diameter else ""))
        return min(cands, key=lambda f: _line_distance(p, f.center, f.axis))

    def mass_properties(self, density: float | None = None, material: str | None = None) -> dict:
        """Volume, mass, centre of mass and inertia; needs a density (g/cm^3) or a material name, given here or
        to reference_step (the customer's figure: a STEP file carries no reliable density)."""
        from . import measure
        rho = density if density is not None else self._record.get("density")
        mat = material or self._record.get("material")
        if rho is None and not mat:
            raise ReferenceError(f"reference {self.name!r}: give density= (g/cm^3) or material= for mass properties")
        if rho is None and mat.lower() not in measure.DENSITY:
            raise ReferenceError(f"unknown material {mat!r}; give density= (g/cm^3) instead")
        return measure.mass_properties(self._shape, float(rho) if rho is not None else mat.lower())

    # mounting
    def mount(self, hole, part: str, screw: str, series: str = "medium", coaxial_tol: float = 0.1,
              angle_tol: float = 0.5, dia_tol: float = 0.01) -> None:
        """Declare a mounting hole pair: `hole` (a HoleFeature of this reference, or a point near its axis) and the
        clearance hole of designed part `part` that a `screw` (M3...) passes through into it. Every build then checks
        that the part has a hole coaxial with it (axis offset <= coaxial_tol mm, angle <= angle_tol deg; the
        smallest such hole is the clearance hole) whose diameter is the ISO 273 `series` (fine | medium | coarse)
        clearance for the screw within dia_tol mm. The tolerances are yours to state; the table is sourced."""
        from .lib.fasteners import iso273_clearance
        h = hole if isinstance(hole, HoleFeature) else self.hole(hole)
        iso273_clearance(screw, series)                         # an unknown size or series fails here, in build(p)
        for v, what in ((coaxial_tol, "coaxial_tol"), (angle_tol, "angle_tol"), (dia_tol, "dia_tol")):
            if not (isinstance(v, (int, float)) and v >= 0 and math.isfinite(v)):
                raise ReferenceError(f"mount: {what} must be a number >= 0")
        self._record["mounts"].append({"hole": h.to_dict(), "part": str(part), "screw": str(screw), "series": str(series),
                                       "coaxial_tol": float(coaxial_tol), "angle_tol": float(angle_tol), "dia_tol": float(dia_tol)})

    def describe(self) -> str:
        bb = self.bbox
        feats = self.features()
        lines = [f"reference {self.name} ({self.path}, sha256 {self.sha256[:12]}…): bbox X {bb.min.X:g}..{bb.max.X:g} "
                 f"Y {bb.min.Y:g}..{bb.max.Y:g} Z {bb.min.Z:g}..{bb.max.Z:g}"]
        lines += ["  " + f.describe() for f in feats[:40]]
        if len(feats) > 40:
            lines.append(f"  ... {len(feats) - 40} more")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"Reference({self.name!r}, {self.path!r})"


def _xyz(v) -> tuple[float, float, float]:
    if hasattr(v, "X"):
        return (float(v.X), float(v.Y), float(v.Z))
    t = tuple(float(c) for c in v)
    if len(t) != 3:
        raise ReferenceError(f"a point needs three numbers, got {v!r}")
    return t


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a) -> float:
    return math.sqrt(_dot(a, a))


def _line_distance(p, origin, axis) -> float:
    d = _sub(p, origin)
    t = _dot(d, axis)
    return _norm((d[0] - axis[0] * t, d[1] - axis[1] * t, d[2] - axis[2] * t))


def reference_step(path: str, name: str | None = None, at=None, clearance: float | None = None,
                   contact: Iterable[str] = (), material: str | None = None, density: float | None = None,
                   color: str = REFERENCE_COLOR, opacity: float = REFERENCE_OPACITY) -> Reference:
    """Load a customer's STEP file (in the project's inputs/ folder) as an immutable reference part and add it to
    the assembly under `name` (default: the file name). `at` places it (a Location such as Pos(..) * Rot(..), or
    (x, y, z)). `clearance` (mm): every designed part not named in `contact` must stay at least this far from it
    (parts in `contact` may touch it, never overlap it). `material` or `density` (g/cm^3) gives it a mass.
    Returns a Reference: .bbox, .holes(), .bosses(), .hole(near=), .features(), .mass_properties(), .mount(...)."""
    import build123d as bd
    model_dir = _context_dir()
    target, rel = resolve_path(path, model_dir)
    declared = getattr(_local, "declared", None)
    nm = str(name or Path(rel).stem)
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_\-]{0,63}$", nm):
        raise ReferenceError(f"reference name {nm!r}: use letters, digits, _ or - (it becomes the part's name)")
    if declared is not None:
        if any(d["name"] == nm for d in declared):
            raise ReferenceError(f"reference name {nm!r} is declared twice")
        if any(d["path"] == rel for d in declared):
            raise ReferenceError(f"{rel} is already a reference in this model; place it once (or copy the file for a second instance)")
        if len(declared) >= MAX_REFERENCES:
            raise ReferenceError(f"at most {MAX_REFERENCES} reference parts per model file")
    if clearance is not None and not (isinstance(clearance, (int, float)) and clearance >= 0 and math.isfinite(clearance)):
        raise ReferenceError("clearance must be a number of mm >= 0 (or None for no clearance check)")
    if density is not None and not (isinstance(density, (int, float)) and density > 0 and math.isfinite(density)):
        raise ReferenceError("density must be a positive number in g/cm^3")
    shape, sha, nbytes, entities = load_checked(target, rel)
    placed = shape
    if at is not None:
        loc = at if isinstance(at, bd.Location) else bd.Location(_xyz(at))
        placed = shape.moved(loc)
    from .model import _copy_shape
    placed = _copy_shape(placed)
    record = {"name": nm, "path": rel, "sha256": sha, "bytes": nbytes, "entities": entities,
              "clearance": None if clearance is None else float(clearance), "contact": [str(c) for c in contact],
              "material": material, "density": None if density is None else float(density), "color": color,
              "opacity": float(opacity), "mounts": [], "fingerprint": None}
    ref = Reference(record, placed)
    if declared is not None:
        record["_shape"] = placed
        declared.append(record)
    return ref


# ---------------------------------------------------------------------------- the runner's side

def attach(parts, declared: list[dict]) -> None:
    """Add the declared references to the Parts build(p) returned (as parts of their own, flagged read-only)."""
    from .model import Part, material_key
    from .report import fingerprint
    names = set(parts.names)
    for rec in declared:
        shape = rec.pop("_shape")
        if rec["name"] in names:
            raise ReferenceError(f"build() returned a part named {rec['name']!r}, the name of a reference part; "
                                 "rename one of them (reference parts are added to the assembly by reference_step)")
        rec["fingerprint"] = fingerprint(shape)
        rec["signature"] = signature(shape)
        mat = material_key(rec["material"]) if rec.get("material") else None
        parts._parts.append(Part(rec["name"], shape, rec["color"], mat, rec["opacity"], reference=rec))
        parts._by_name[rec["name"]] = parts._parts[-1]
    parts.references = [dict(r) for r in declared]


def signature(shape) -> list:
    """What the reference's geometry is, for proving an edit did not change it: topology counts, volume, area and
    bounding box (stable under copies and meshing, unlike the B-rep text, whose tolerances the kernel may touch)."""
    from . import measure
    bb = measure.bounding_box(shape)
    return [len(shape.solids()), len(shape.faces()), len(shape.edges()), round(abs(measure.signed_volume(shape)), 3),
            round(float(shape.area), 3)] + [round(float(v), 4) for v in (bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z)]


def _same_signature(a, b) -> bool:
    if not a or not b or len(a) != len(b):
        return False
    if list(a[:3]) != list(b[:3]):
        return False
    return all(abs(float(x) - float(y)) <= 1e-6 * max(1.0, abs(float(x))) + 1e-3 for x, y in zip(a[3:], b[3:]))


def public(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def verify_files(parts, model_dir: Path, accepted: dict) -> str | None:
    """Before anything else is made of a build: every declared reference file still has the bytes it was parsed
    from and that the project accepted; every accepted reference is still declared. An error message, or None."""
    declared = list(getattr(parts, "references", None) or [])
    for rec in declared:
        try:
            target, rel = resolve_path(rec["path"], model_dir)
            now, _ = sha256_file(target)
        except (ReferenceError, OSError) as exc:
            return f"reference {rec['name']!r}: {exc}"
        acc = (accepted or {}).get(rel) or {}
        if acc.get("sha256") and acc["sha256"] != now:
            return changed_message(rel, acc["sha256"], now)
        if now != rec["sha256"]:
            return f"reference file {rel} changed after it was loaded (sha256 {rec['sha256'][:16]}… is now {now[:16]}…); build again"
    have = {r["path"] for r in declared}
    gone = [(p, a) for p, a in (accepted or {}).items() if p not in have]
    if gone:
        return ("this model no longer declares the reference part(s) " + ", ".join(f"{a.get('name') or p} ({p})" for p, a in gone)
                + ". A reference stays in the design once declared, so its interference and mounting checks cannot be "
                  "dropped: put the reference_step(...) back. If the person really removed the customer part from the job, "
                  "they run `eitri reference remove <project> <path>`.")
    return None


def _pair_lookup(report) -> dict:
    out = {}
    for pr in getattr(report, "pairs", []) or []:
        out[(pr.a, pr.b)] = out[(pr.b, pr.a)] = (pr.overlap_volume, pr.distance)
    return out


def checks(parts, report, accepted: dict | None = None, signatures: dict | None = None) -> list:
    """The reference parts' own checks (CheckResults for the report): geometry untouched, no interference,
    clearance, the kept-mounts guard, and every declared mount (coaxial + ISO 273 clearance diameter)."""
    from . import measure
    from .model import CheckResult
    from .report import _box_gap
    out: list = []
    declared = list(getattr(parts, "references", None) or [])
    if not declared:
        return out
    by_name = {p.name: p for p in parts}
    ref_names = {r["name"] for r in declared}
    designed = [p for p in parts if p.name not in ref_names]
    pairs = _pair_lookup(report)
    for rec in declared:
        nm = rec["name"]
        part = by_name.get(nm)
        if part is None:
            out.append(CheckResult(f"reference {nm}: still in the assembly", False, "missing (deleted by an edit?)", "present, unmodified"))
            continue
        now = (signatures or {}).get(nm) or signature(part.shape)      # taken right after the direct edits
        same = _same_signature(now, rec.get("signature"))
        out.append(CheckResult(f"reference {nm}: geometry unmodified ({rec['path']}, sha256 {rec['sha256'][:12]})", same,
                               "identical to the file" if same else "CHANGED after loading (an edit touched it?)", "byte-identical B-rep"))
        rbox = measure.bounding_box(part.shape)
        clearance = rec.get("clearance")
        contact = set(rec.get("contact") or [])
        reach = max(float(getattr(report, "near_gap", 0.0) or 0.0), clearance or 0.0)
        over: list[str] = []
        tight: list[tuple[float, str]] = []
        checked = 0
        for p in designed:
            if _box_gap(rbox, measure.bounding_box(p.shape)) > reach + 1e-9:
                continue
            checked += 1
            got = pairs.get((p.name, nm))
            if got is None:
                try:
                    ov = measure.overlap_volume(p.shape, part.shape)
                    dist = 0.0 if ov > 1e-9 else measure.min_distance(p.shape, part.shape)
                except Exception:
                    over.append(f"{p.name} (could not be measured)")
                    continue
            else:
                ov, dist = got
            if ov > OVERLAP_TOL_MM3:
                over.append(f"{p.name} {ov:.3g} mm^3")
            elif clearance is not None and p.name not in contact:
                tight.append((dist, p.name))
        out.append(CheckResult(f"no designed part overlaps reference {nm}", not over,
                               ("overlap: " + ", ".join(over)) if over else f"none ({checked} part(s) within {reach:g} mm measured)",
                               f"overlap <= {OVERLAP_TOL_MM3:g} mm^3"))
        if clearance is not None:
            bad = sorted((d, n) for d, n in tight if d < clearance - 1e-6)
            least = min(tight) if tight else None
            measured = (", ".join(f"{n} {d:.3g} mm" for d, n in bad) if bad else
                        f"tightest {least[1]} {least[0]:.3g} mm" if least else
                        f"every non-contact part is over {reach:g} mm away (bounding boxes)")
            out.append(CheckResult(f"clearance to reference {nm} >= {clearance:g} mm" + (f" (contact allowed: {', '.join(sorted(contact))})" if contact else ""),
                                   not bad, measured, f">= {clearance:g} mm"))
        kept = int(((accepted or {}).get(rec["path"]) or {}).get("mounts") or 0)
        if kept > len(rec.get("mounts") or []):
            out.append(CheckResult(f"reference {nm}: declared mounting checks are kept", False,
                                   f"{len(rec['mounts'])} mount(s) declared", f">= {kept} (earlier builds declared {kept})"))
        for i, m in enumerate(rec.get("mounts") or [], 1):
            out += _mount_checks(nm, i, m, by_name.get(m["part"]))
    return out


def _mount_checks(ref_name: str, i: int, m: dict, part) -> list:
    from . import measure
    from .lib.fasteners import ISO_273_SOURCE, iso273_clearance
    from .model import CheckResult
    h = m["hole"]
    c = tuple(h["center"])
    ax = tuple(h["axis"])
    label = f"mount {i} ({ref_name} Ø{h['diameter']:g} at ({c[0]:g}, {c[1]:g}, {c[2]:g}) -> {m['part']}, {m['screw']})"
    want = iso273_clearance(m["screw"], m["series"])
    if part is None:
        return [CheckResult(f"{label}: coaxial clearance hole", False, f"no part named {m['part']!r}", "a designed part")]
    if getattr(part, "reference", None):
        return [CheckResult(f"{label}: coaxial clearance hole", False, f"{m['part']} is a reference part", "a designed part")]
    cos_tol = math.cos(math.radians(m["angle_tol"]))
    best = None
    for f in measure.cylindrical_features(part.shape):
        if f.kind != "hole":
            continue
        fa = (f.axis.X, f.axis.Y, f.axis.Z)
        cosang = abs(_dot(fa, ax))
        if cosang < cos_tol:
            continue
        fc = (f.center.X, f.center.Y, f.center.Z)
        off = max(_line_distance(fc, c, ax), _line_distance(c, fc, fa))
        ang = math.degrees(math.acos(min(1.0, cosang)))
        cand = (off > m["coaxial_tol"] + 1e-9, f.diameter if off <= m["coaxial_tol"] + 1e-9 else off, off, ang, f)
        if best is None or cand[:2] < best[:2]:          # coaxial ones first, the smallest of them: the clearance bore
            best = cand
    if best is None:
        return [CheckResult(f"{label}: coaxial clearance hole", False, f"{m['part']} has no hole parallel to it "
                            f"(within {m['angle_tol']:g} deg)", f"offset <= {m['coaxial_tol']:g} mm, angle <= {m['angle_tol']:g} deg")]
    _, _, off, ang, f = best
    coax = off <= m["coaxial_tol"] + 1e-9 and ang <= m["angle_tol"] + 1e-9
    out = [CheckResult(f"{label}: {m['part']} hole coaxial", coax,
                       f"axis offset {off:.3g} mm, angle {ang:.2g} deg (nearest {m['part']} hole Ø{f.diameter:.3g})",
                       f"offset <= {m['coaxial_tol']:g} mm, angle <= {m['angle_tol']:g} deg")]
    if coax:
        okd = abs(f.diameter - want) <= m["dia_tol"] + 1e-9
        out.append(CheckResult(f"{label}: clearance Ø per ISO 273 {m['series']}", okd, f"Ø{f.diameter:.3g}",
                               f"Ø{want:g} ±{m['dia_tol']:g} ({ISO_273_SOURCE})"))
    return out


def summary(parts) -> str:
    """Lines for the build summary: which parts are references and where they came from."""
    declared = list(getattr(parts, "references", None) or [])
    if not declared:
        return ""
    lines = ["Reference parts (read-only customer geometry; never edit them, design around them):"]
    for r in declared:
        extra = []
        if r.get("clearance") is not None:
            extra.append(f"clearance >= {r['clearance']:g} mm" + (f" except {', '.join(r['contact'])}" if r.get("contact") else ""))
        if r.get("mounts"):
            extra.append(f"{len(r['mounts'])} mount(s) declared")
        lines.append(f"  {r['name']}: {r['path']} sha256 {r['sha256'][:12]}…, {r['bytes']} bytes" + (f"; {'; '.join(extra)}" if extra else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------- the project's record (eitri.json)

def accepted_for(state: dict, model_name: str) -> dict:
    return {k: dict(v) for k, v in (((state or {}).get(STATE_KEY) or {}).get(model_name) or {}).items()}


def accepted_from_folder(folder: Path, model_name: str) -> dict:
    """What a project folder's eitri.json accepted for a model file (empty when there is no project)."""
    import json
    f = Path(folder) / "eitri.json"
    try:
        return accepted_for(json.loads(f.read_text(encoding="utf-8")), model_name)
    except (OSError, ValueError):
        return {}


def remember(state: dict, model_name: str, declared: list[dict], now: float, by: str = "first build") -> bool:
    """Record references a build read for the first time (trust on first use), and the most mounts each declared.
    Never changes a recorded hash. Returns whether anything changed."""
    allrefs = dict((state or {}).get(STATE_KEY) or {})
    mine = {k: dict(v) for k, v in (allrefs.get(model_name) or {}).items()}
    changed = False
    for r in declared or []:
        rel = r.get("path")
        if not rel or not r.get("sha256"):
            continue
        cur = mine.get(rel)
        if cur is None:
            mine[rel] = {"sha256": r["sha256"], "bytes": r.get("bytes"), "name": r.get("name"),
                         "mounts": len(r.get("mounts") or []), "accepted": round(now, 3), "by": by}
            changed = True
        elif cur.get("sha256") == r["sha256"]:
            n = max(int(cur.get("mounts") or 0), len(r.get("mounts") or []))
            if n != cur.get("mounts") or cur.get("name") != r.get("name"):
                cur.update(mounts=n, name=r.get("name"))
                changed = True
    if changed:
        allrefs[model_name] = mine
        state[STATE_KEY] = allrefs
    return changed
