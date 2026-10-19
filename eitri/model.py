"""The model contract.

A eitri model is one Python file:

    from eitri import *

    PARAMS = dict(width=40.0, depth=20.0, thickness=6.0)

    def build(p):
        return bd.Box(p.width, p.depth, p.thickness)      # one part
        # or: return {"base": base, "lid": lid}            # named parts (assembly)

    def verify(parts, p, c):                                # optional
        c.equal("width", parts.size.X, p.width)

That is the whole contract: no decorators, no cache, no sidecars. `build(p)`
receives the parameters as an attribute-accessible mapping and returns build123d
geometry. eitri turns the result into named, coloured `Parts`, exports them,
measures them and renders them.
"""
from __future__ import annotations

import importlib
import importlib.util
import math
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import build123d as bd

# A readable palette for parts without an explicit colour (sRGB hex).
PALETTE = [
    "#6C8EBF", "#D79B5B", "#7FA86B", "#B06C8E", "#8C8C8C",
    "#5BA7A7", "#C9B458", "#9A7BB0", "#C96B6B", "#6BA3C9",
]


def srgb(hex_or_rgb: str | tuple, alpha: float = 1.0) -> bd.Color:
    """A build123d Color from an sRGB hex string such as "#2E3742" (or 0..1 tuple)."""
    if isinstance(hex_or_rgb, str):
        h = hex_or_rgb.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    else:
        r, g, b = hex_or_rgb[:3]
    return bd.Color(r, g, b, alpha)


class Params(dict):
    """Parameters with attribute access: p.width is p["width"]."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(
                f"no parameter {name!r}; PARAMS has: {', '.join(self)}"
            ) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


def coerce_param(old: Any, text: str) -> Any:
    """Coerce a command-line string to the type of the existing parameter value."""
    if isinstance(old, bool):
        return text.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(old, int) and not isinstance(old, bool):
        f = float(text)
        return int(f) if f.is_integer() else f
    if isinstance(old, float):
        return float(text)
    if isinstance(old, (list, tuple)):
        import ast
        return type(old)(ast.literal_eval(text))
    return text


@dataclass
class Part:
    name: str
    shape: bd.Shape
    color: str  # sRGB hex
    material: str | None = None  # a DENSITY key (pla, steel, ...) for mass in the report
    opacity: float = 1.0         # 1 = solid; lower values render translucent in the viewer
    group: str | None = None     # component group (sub-assembly) the part belongs to
    face_colors: dict | None = None   # {face index: sRGB hex} appearances on individual faces

    @property
    def solids(self) -> list[bd.Solid]:
        return list(self.shape.solids())


class Parts:
    """The named parts a model built, in order. Mapping-like: parts["lid"]."""

    def __init__(self, parts: Iterable[Part]):
        self._parts = list(parts)
        self._by_name = {p.name: p for p in self._parts}
        if len(self._by_name) != len(self._parts):
            raise ValueError("part names must be unique")
        self.joints: list[dict] = []          # assembly joints (eitri.assembly), filled by apply_edits

    def __getitem__(self, key: str | int) -> bd.Shape:
        if isinstance(key, int):
            return self._parts[key].shape
        return self._by_name[key].shape

    def __iter__(self):
        return iter(self._parts)

    def __len__(self) -> int:
        return len(self._parts)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    @property
    def names(self) -> list[str]:
        return [p.name for p in self._parts]

    @property
    def compound(self) -> bd.Compound:
        """Everything as one Compound (children keep labels and colours)."""
        children = []
        for p in self._parts:
            s = p.shape
            s.label = p.name
            try:
                s.color = srgb(p.color)
            except Exception:
                pass
            children.append(s)
        return bd.Compound(children=children, label="model")

    @property
    def bbox(self) -> bd.BoundBox:
        if not self._parts:
            return bd.Vertex(0, 0, 0).bounding_box()
        from .measure import bounding_box
        boxes = [bounding_box(p.shape) for p in self._parts]
        bb = boxes[0]
        for b in boxes[1:]:
            bb = bb.add(b)
        return bb

    @property
    def size(self) -> bd.Vector:
        return self.bbox.size

    @property
    def all_solids(self) -> list[bd.Solid]:
        out: list[bd.Solid] = []
        for p in self._parts:
            out.extend(p.shape.solids())
        return out


def normalize_result(result: Any, default_name: str) -> Parts:
    """Turn whatever build() returned into Parts."""
    items: list[tuple[str, bd.Shape]] = []

    def add(name: str, shape: Any) -> None:
        if shape is None:
            return
        if isinstance(shape, (bd.BuildPart, bd.BuildSketch, bd.BuildLine)):
            shape = getattr(shape, "part", None) or getattr(shape, "sketch", None) or shape.line
        if not isinstance(shape, bd.Shape):
            raise TypeError(
                f"build() returned {type(shape).__name__} for {name!r}; "
                "return build123d shapes (a Part/Solid/Compound), or a dict of them"
            )
        items.append((name, shape))

    if isinstance(result, Mapping):
        for k, v in result.items():
            add(str(k), v)
    elif isinstance(result, (list, tuple)):
        for i, v in enumerate(result):
            label = getattr(v, "label", "") or f"part_{i + 1}"
            add(label, v)
    elif isinstance(result, bd.Compound) and result.children:
        for i, child in enumerate(result.children):
            label = getattr(child, "label", "") or f"part_{i + 1}"
            add(label, child)
    else:
        label = getattr(result, "label", "") or default_name
        add(label, result)

    if not items:
        if result is None:
            raise ValueError("build() returned None (no geometry); return a shape, a dict of parts, or {} for a blank design")
        return Parts([])                      # a blank design: nothing yet, the user sketches in the viewer

    # unique names
    seen: dict[str, int] = {}
    uniq: list[tuple[str, bd.Shape]] = []
    for name, shape in items:
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        uniq.append((name, shape))

    parts = []
    for i, (name, shape) in enumerate(uniq):
        color = _hex_of(getattr(shape, "color", None)) or PALETTE[i % len(PALETTE)]
        parts.append(Part(name=name, shape=shape, color=color, material=material_key(getattr(shape, "material", None)),
                          opacity=_alpha_of(getattr(shape, "color", None))))
    return Parts(parts)


def material_key(material: Any) -> str | None:
    """Our density-table key for a material given as a string ("steel") or as a
    build123d Material object (shape.material = "aluminum" -> Alu_G6061_T6)."""
    from .measure import DENSITY
    if material is None:
        return None
    name = str(getattr(material, "name", None) or material).strip().lower()
    if not name:
        return None
    if name in DENSITY:
        return name
    aliases = {"alu": "aluminium", "aluminum": "aluminium", "stainless": "stainless", "steel": "steel", "pla": "pla", "petg": "petg",
               "abs": "abs", "nylon": "nylon", "pa6": "nylon", "pa12": "nylon", "resin": "resin", "brass": "brass", "copper": "copper",
               "titanium": "titanium", "ti6": "titanium", "acrylic": "acrylic", "pmma": "acrylic", "wood": "wood"}
    for prefix, key in aliases.items():
        if name.startswith(prefix):
            return key
    return name[:40]


def _alpha_of(color: Any) -> float:
    try:
        rgba = tuple(color)
        return max(0.05, min(1.0, float(rgba[3]))) if len(rgba) > 3 else 1.0
    except Exception:
        return 1.0


def _hex_of(color: Any) -> str | None:
    if color is None:
        return None
    try:
        rgba = tuple(color)  # build123d Color iterates (r, g, b, a)
        r, g, b = (max(0, min(255, round(c * 255))) for c in rgba[:3])
        return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Checks: verify(parts, p, c)
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    ok: bool
    measured: str
    expected: str = ""

    def line(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        exp = f" (expected {self.expected})" if self.expected else ""
        return f"[{mark}] {self.name}: {self.measured}{exp}"


class Checker:
    """Collects verification results; never raises on a failing check."""

    def __init__(self) -> None:
        self.results: list[CheckResult] = []

    def _fmt(self, v: Any) -> str:
        if isinstance(v, float):
            return f"{v:.3f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.3g}"
        if isinstance(v, bd.Vector):
            return f"({v.X:.3f}, {v.Y:.3f}, {v.Z:.3f})"
        return str(v)

    def equal(self, name: str, actual: float, expected: float, tol: float = 0.01) -> bool:
        ok = abs(float(actual) - float(expected)) <= tol
        self.results.append(CheckResult(name, ok, self._fmt(actual), f"{self._fmt(expected)} ±{tol:g}"))
        return ok

    def between(self, name: str, actual: float, lo: float, hi: float) -> bool:
        ok = lo <= float(actual) <= hi
        self.results.append(CheckResult(name, ok, self._fmt(actual), f"{lo:g}..{hi:g}"))
        return ok

    def at_least(self, name: str, actual: float, minimum: float) -> bool:
        ok = float(actual) >= minimum
        self.results.append(CheckResult(name, ok, self._fmt(actual), f">= {minimum:g}"))
        return ok

    def at_most(self, name: str, actual: float, maximum: float) -> bool:
        ok = float(actual) <= maximum
        self.results.append(CheckResult(name, ok, self._fmt(actual), f"<= {maximum:g}"))
        return ok

    def count(self, name: str, items: Any, expected: int) -> bool:
        n = items if isinstance(items, int) else len(list(items))
        ok = n == expected
        self.results.append(CheckResult(name, ok, str(n), str(expected)))
        return ok

    def true(self, name: str, condition: Any, detail: str = "") -> bool:
        ok = bool(condition)
        self.results.append(CheckResult(name, ok, detail or ("yes" if ok else "no")))
        return ok

    def note(self, name: str, value: Any) -> None:
        """Record a measurement without judging it."""
        self.results.append(CheckResult(name, True, self._fmt(value)))

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if not r.ok]


# ---------------------------------------------------------------------------
# Loading a model file
# ---------------------------------------------------------------------------

@dataclass
class LoadedModel:
    path: Path
    module: Any
    params: Params
    param_docs: dict[str, str] = field(default_factory=dict)
    edit_notes: list = field(default_factory=list)
    param_exprs: dict[str, str] = field(default_factory=dict)     # parameters given as expressions of other parameters
    configs: dict[str, dict] = field(default_factory=dict)        # named parameter sets (CONFIGS in the model file)
    config: str | None = None

    @property
    def edits(self) -> list[dict]:
        raw = getattr(self.module, "EDITS", None) or []
        return [dict(e) for e in raw if isinstance(e, Mapping)]

    def build(self) -> Parts:
        """build(p), then the file's direct EDITS (viewer push/pull, fillets, ...) on top."""
        result = self.module.build(self.params)
        parts = result if isinstance(result, Parts) else normalize_result(result, self.path.stem)
        mats = getattr(self.module, "MATERIALS", None) or {}
        if isinstance(mats, Mapping):
            for part in parts:
                if part.name in mats and mats[part.name]:
                    part.material = material_key(mats[part.name])
        edits = self.edits
        if edits:
            from .edits import apply_edits
            parts, self.edit_notes = apply_edits(parts, edits)
        return parts

    def verify(self, parts: Parts) -> Checker:
        c = Checker()
        fn = getattr(self.module, "verify", None)
        if fn is not None:
            fn(parts, self.params, c)
        return c


def load_step_file(path: str | Path) -> Parts:
    """Open an existing STEP/STP file as Parts (top-level occurrences become parts)."""
    path = Path(path).resolve()
    comp = bd.import_step(str(path))
    if comp.children:
        named = {}
        for i, child in enumerate(comp.children):
            if not child.solids() and not child.faces():
                continue
            named[child.label or f"part_{i + 1}"] = child
        if named:
            return normalize_result(named, path.stem)
    return normalize_result(comp, path.stem)


class _FileModule:
    """A mesh (STL/OBJ/3MF/PLY) or IGES file as a model: build() returns its geometry as parts."""

    def __init__(self, path: Path, decimate: int | None = None):
        self.path = path
        self.decimate = decimate

    def build(self, p):
        from . import meshio
        suffix = self.path.suffix.lower()
        if suffix in meshio.IGES_SUFFIXES:
            shapes = meshio.import_iges(self.path)
            if len(shapes) == 1:
                return normalize_result(shapes[0], self.path.stem)
            return normalize_result({f"{self.path.stem}_{i + 1}": s for i, s in enumerate(shapes)}, self.path.stem)
        solid, _rep = meshio.import_mesh(self.path, decimate_to=self.decimate)
        return normalize_result(solid, self.path.stem)


class _StepModule:
    """Makes a STEP file look like a model module: build() returns its geometry."""

    def __init__(self, path: Path):
        self.path = path

    def build(self, p):
        return load_step_file(self.path)


_EXPR_NAMES = {k: getattr(math, k) for k in ("sqrt", "sin", "cos", "tan", "atan", "atan2", "pi", "e", "radians", "degrees", "floor", "ceil", "hypot", "log", "exp")}
_EXPR_NAMES.update({"min": min, "max": max, "abs": abs, "round": round, "int": int, "float": float})


def _is_expression(text: str, params: Mapping) -> bool:
    """Whether a string PARAMS value is an expression or plain text. Text that is not Python ("M2.5",
    "MODEL A") or a single word that names no parameter or function ("M3", "PLA", "EITRI") is a value;
    anything else ("width / 2", "max(depth, 10)", a typo like "widht / 2") is an expression."""
    import ast
    text = text.strip()
    if not text or text.startswith(("'", '"')):
        return False
    try:
        body = ast.parse(text, mode="eval").body
    except SyntaxError:
        return False
    return not (isinstance(body, ast.Name) and body.id not in params and body.id not in _EXPR_NAMES)


def resolve_expressions(params: "Params") -> tuple["Params", dict[str, str]]:
    """PARAMS values written as strings are expressions of the other parameters ("width / 2",
    "max(depth, 10)"): resolved here, in dependency order. Returns the numeric Params and the
    expressions by name."""
    exprs = {k: v for k, v in params.items() if isinstance(v, str) and _is_expression(v, params)}
    out = Params(params)
    pending = dict(exprs)
    for _ in range(len(pending) + 1):
        if not pending:
            break
        progressed = False
        for k, expr in list(pending.items()):
            scope = dict(_EXPR_NAMES)
            scope.update({n: v for n, v in out.items() if not isinstance(v, str)})
            try:
                val = eval(compile(expr, f"<PARAMS[{k}]>", "eval"), {"__builtins__": {}}, scope)
            except NameError:
                continue
            except Exception as exc:
                raise ValueError(f"PARAMS[{k!r}] = {expr!r} failed: {type(exc).__name__}: {exc}") from exc
            out[k] = val
            del pending[k]
            progressed = True
        if not progressed:
            break
    if pending:
        raise ValueError("PARAMS expressions refer to unknown or circular names: " + ", ".join(f"{k} = {v!r}" for k, v in pending.items()))
    return out, exprs


def load_model(path: str | Path, overrides: Mapping[str, str] | None = None, config: str | None = None) -> LoadedModel:
    """Import a model file (its folder goes on sys.path) and resolve PARAMS.

    A .step/.stp path is accepted too: it loads as a parameterless model, so every
    tool (report, render, viewer) works on existing CAD files.
    """
    from .executor import require_local_exec
    require_local_exec("loading a model")             # hosted web processes never import model code
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    # never trust bytecode for model folders: an edit within the same second and the same
    # length would otherwise run stale code
    sys.dont_write_bytecode = True
    for cache in list(path.parent.glob("__pycache__")) + list(path.parent.glob("*/__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    importlib.invalidate_caches()
    if path.suffix.lower() in (".stl", ".obj", ".3mf", ".ply", ".off", ".igs", ".iges"):
        extra = {k: v for k, v in (overrides or {}).items() if k != "decimate"}
        if extra:
            raise KeyError("a mesh or IGES file has no parameters to override (only decimate=<triangles>)")
        dec = (overrides or {}).get("decimate")
        return LoadedModel(path=path, module=_FileModule(path, int(float(dec)) if dec else None), params=Params())
    if path.suffix.lower() in (".step", ".stp"):
        if overrides:
            raise KeyError("a STEP file has no parameters to override")
        return LoadedModel(path=path, module=_StepModule(path), params=Params())
    folder = str(path.parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location(f"eitri_model_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    if not hasattr(module, "build") or not callable(module.build):
        raise AttributeError(f"{path.name} must define build(p)")

    raw = getattr(module, "PARAMS", {}) or {}
    if not isinstance(raw, Mapping):
        raise TypeError("PARAMS must be a dict")
    params = Params(raw)
    docs = dict(getattr(module, "PARAM_DOCS", {}) or {})
    configs = {str(k): dict(v) for k, v in (getattr(module, "CONFIGS", {}) or {}).items() if isinstance(v, Mapping)}
    if config:
        if config not in configs:
            raise KeyError(f"unknown configuration {config!r}; CONFIGS has: {', '.join(configs) or 'none'}")
        for key, val in configs[config].items():
            if key not in params:
                raise KeyError(f"configuration {config!r} sets unknown parameter {key!r}")
            params[key] = val
    for key, text in (overrides or {}).items():
        if key not in params:
            raise KeyError(f"unknown parameter {key!r}; PARAMS has: {', '.join(params)}")
        params[key] = coerce_param(params[key], text) if not isinstance(params[key], str) else text
    params, exprs = resolve_expressions(params)
    return LoadedModel(path=path, module=module, params=params, param_docs=docs, param_exprs=exprs, configs=configs, config=config)
