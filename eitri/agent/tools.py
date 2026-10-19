"""The agent's tools, as plain Python over a Project.

Each tool returns a ToolResult(text, images). The MCP server and the SDK loop
both wrap these, so there is one implementation of every tool.
"""
from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..project import Project

MAX_TEXT = 12000


@dataclass
class ToolResult:
    text: str
    images: list[str] = field(default_factory=list)   # PNG paths
    is_error: bool = False


class Tools:
    """Tool implementations bound to one project. Methods named without underscore are tools."""

    def __init__(self, project: Project, on_event: Callable[[str, dict], None] | None = None,
                 render_quality: float = 1.0):
        self.project = project
        self.on_event = on_event or (lambda kind, data: None)
        self.render_quality = render_quality

    # ------------------------------------------------------------------ build
    def build_model(self, code: str, note: str = "") -> ToolResult:
        """Write the complete model file and build it.

        Returns the geometry report (sizes, holes, thin walls, interferences,
        verify() results) and a 4-view image of the result. Use this for the
        first version and for larger rewrites; use edit_model for small changes.
        """
        code = _normalize_code(code)
        self.project.write_model(code)
        return self._build_and_report(note or "build_model")

    def edit_model(self, old: str, new: str, note: str = "") -> ToolResult:
        """Replace one exact snippet of the current model file with new text, then rebuild.

        `old` must occur exactly once in the file. Returns the same report and
        image as build_model.
        """
        current = self.project.read_model()
        if current is None:
            return ToolResult("There is no model file yet; call build_model first.", is_error=True)
        n = current.count(old)
        if n == 0:
            # tolerate trailing-whitespace / indentation differences
            alt = _fuzzy_find(current, old)
            if alt is None:
                return ToolResult("edit_model: `old` was not found in the model file. Re-read it with "
                                  "read_model and copy the snippet exactly.", is_error=True)
            old = alt
            n = 1
        if n > 1:
            return ToolResult(f"edit_model: `old` occurs {n} times; include more context so it is unique.",
                              is_error=True)
        self.project.write_model(current.replace(old, new, 1))
        return self._build_and_report(note or "edit_model")

    def set_params(self, params: dict[str, Any] | str | None = None, config: str | None = None) -> ToolResult:
        """Change PARAMS values in the model file (no other code change) and rebuild.

        `params` is an object like {"width": 45, "hole_d": 5.5}; a value may be an expression of
        other parameters as a string ("width / 2"). `config` applies a named parameter set from the
        model's CONFIGS dict instead."""
        if config:
            try:
                new = self.project.apply_config(config)
            except KeyError as exc:
                return ToolResult(str(exc), is_error=True)
            return self._build_and_report(f"configuration {config}: " + ", ".join(f"{k}={v}" for k, v in new.items()))
        if params is None:
            return ToolResult("give params or a config name", is_error=True)
        if isinstance(params, str):
            text = params.strip()
            try:
                params = json.loads(text)
            except json.JSONDecodeError:
                try:
                    params = dict(kv.split("=", 1) for kv in text.replace(",", " ").split())
                except ValueError:
                    return ToolResult("set_params: give an object like {\"width\": 45}", is_error=True)
        if not isinstance(params, dict) or not params:
            return ToolResult("set_params: give a non-empty object like {\"width\": 45}", is_error=True)
        try:
            new = self.project.set_params(params)
        except Exception as exc:
            return ToolResult(f"set_params failed: {exc}", is_error=True)
        res = self._build_and_report("set_params " + ", ".join(f"{k}={v}" for k, v in params.items()))
        res.text = "PARAMS now: " + json.dumps(new) + "\n\n" + res.text
        return res

    def read_model(self) -> ToolResult:
        """Return the current model file's source code."""
        code = self.project.read_model()
        if code is None:
            return ToolResult("No model file yet.")
        return ToolResult(code)

    # ---------------------------------------------------------------- inspect
    def inspect(self, code: str) -> ToolResult:
        """Run Python against the built model for ad-hoc measurements and print the results.

        In scope: `parts` (the model's named parts; parts["name"] or parts[0] is a
        build123d shape; parts.size / parts.bbox for the whole model), `p` (PARAMS),
        `bd` and all build123d names, and the measurement helpers: holes(shape,
        diameter=None, axis=None, through=None), bosses(shape), min_wall(shape),
        min_distance(a, b), overlap_volume(a, b), point_inside(shape, (x, y, z)),
        faces_with_normal(shape, (0, 0, 1)), planar_faces_at(shape, "Z", 10),
        signed_volume(shape), fillet_radii(shape), mass_grams(shape, "pla").
        Anything you print is returned. The model is rebuilt from the file first.
        """
        ok, out = self.project.run_python(code)
        text = out or "(no output)"
        return ToolResult(_truncate(text), is_error=not ok)

    def view(self, views: list[str] | None = None, section: str | None = None, exploded: bool = False,
             hide: list[str] | None = None, only: list[str] | None = None, size: int = 720, turntable: int = 0) -> ToolResult:
        """Render extra views of the current build.

        views: any of iso, iso_back, iso_below, front, back, top, bottom, left, right
        (default: iso_back and bottom - the views the standard sheet does not show).
        section: "XZ", "XY:12.5" or "YZ:-4" cuts the model with that plane at the
        given offset and colours the cut faces red (use it to check walls, bores,
        internal cavities). exploded: spread the parts of an assembly apart.
        hide/only: part names to hide or to show alone. turntable: a number of frames
        writes an animated GIF of the model turning (out/turntable.gif) instead.
        """
        if self.project.last_ok_revision() is None and not self.project.model_path.exists():
            return ToolResult("Nothing built yet.", is_error=True)
        args: list[str] = []
        if views:
            args += ["--views", ",".join(views)]
        if section:
            args += ["--section", section]
        if exploded:
            args.append("--exploded")
        if hide:
            args += ["--hide", ",".join(hide)]
        if only:
            args += ["--only", ",".join(only)]
        args += ["--size", str(size)]
        if turntable:
            out_gif = self.project.out_dir / "turntable.gif"
            ok, text = self._run_render(out_gif, args + ["--turntable", str(int(turntable))])
            if not ok:
                return ToolResult(f"turntable failed: {text}", is_error=True)
            return ToolResult(f"{text or 'rendered'}: {out_gif}")
        out_png = self.project.out_dir / "view.png"
        ok, text = self._run_render(out_png, args)
        if not ok:
            return ToolResult(f"view failed: {text}", is_error=True)
        return ToolResult(text or "rendered", images=[str(out_png)])

    def library(self, query: str = "") -> ToolResult:
        """Look up the eitri feature library: list everything, or get the full docstring
        and signature of functions matching `query` (a name or keyword)."""
        from .. import lib as _lib
        from .library_docs import catalog, describe
        if not query.strip():
            return ToolResult(catalog())
        return ToolResult(describe(query))

    def export(self, formats: list[str]) -> ToolResult:
        """Export the current model to additional formats: any of step, stl, 3mf, glb."""
        formats = [f.lower().lstrip(".") for f in formats]
        bad = [f for f in formats if f not in ("step", "stl", "3mf", "glb")]
        if bad:
            return ToolResult(f"unknown formats: {bad}", is_error=True)
        res = self.project.build(note="export " + ",".join(formats), formats=tuple(formats), render=False)
        if not res.ok:
            return ToolResult(f"export failed: {res.error}", is_error=True)
        return ToolResult("Wrote:\n" + "\n".join(f"- {k}: {v}" for k, v in res.outputs.items()))

    # ------------------------------------------------------------------ direct edits (the viewer's timeline)
    def list_edits(self) -> ToolResult:
        """List the model's direct edits (the EDITS list the user made in the viewer: push/pull,
        sketches, holes, shells, patterns, ...) with their index, description and full JSON."""
        from ..edits import describe
        edits = self.project.edits()
        if not edits:
            return ToolResult("EDITS is empty: the model is fully parametric.")
        lines = [f"{i + 1}. {describe(e)}\n   {json.dumps(e)}" for i, e in enumerate(edits)]
        return ToolResult(f"{len(edits)} direct edit(s):\n" + "\n".join(lines))

    def update_edit(self, index: int, patch: dict[str, Any] | str) -> ToolResult:
        """Change one direct edit in place (1-based index) and rebuild. `patch` is a JSON
        object merged over the edit (e.g. {"distance": 4} or {"suppressed": true}); to
        change nested fields pass the whole field (e.g. {"profile": {...}}). The edit is
        kept geometric: do not change its selectors. Prefer this to rewriting EDITS by hand."""
        from ..edits import describe
        if isinstance(patch, str):
            try:
                patch = json.loads(patch)
            except json.JSONDecodeError as exc:
                return ToolResult(f"patch is not valid JSON: {exc}", is_error=True)
        edits = self.project.edits()
        if not 1 <= int(index) <= len(edits):
            return ToolResult(f"there is no edit #{index}; EDITS has {len(edits)} entries", is_error=True)
        old = edits[int(index) - 1]
        new = {**old, **patch, "by": "claude"}          # the timeline shows who changed it last
        self.project.update_edit(int(index) - 1, new)
        return self._build_and_report(f"update edit {index}: {describe(new)}")

    def sketch_op(self, index: int, op: str, args: dict[str, Any] | str | None = None) -> ToolResult:
        """Edit the 2D sketch of a direct edit (1-based index; sketch_extrude/revolve/loft/sweep
        features) with the kernel's sketch operations, then rebuild. Ops: add {entity}, remove {ids},
        move {ids, delta}, rotate {ids, centre, angle}, copy {ids, delta}, mirror {ids, a, b},
        pattern_rect {ids, nx, ny, dx, dy}, pattern_circular {ids, n, centre, angle}, split {id, at},
        trim {id, near}, extend {id, end}, offset {ids, distance}, fillet {a, b, r}, chamfer {a, b, d},
        construction {ids, on}, constrain {constraint}, unconstrain {index}, dimension {dim},
        undimension {index}, set_dim {index, value}, project {part, edges|face, normal, origin},
        svg {path, scale}, dxf {path, layer, scale}, from_simple {profile}, solve {}. A simple profile (rect/circle/...) is
        converted to a full sketch first. Returns the solved sketch, its status (dof, conflicts)
        and the build report."""
        from ..viewer.server import _sketch_op
        from .. import sketch as S
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError as exc:
                return ToolResult(f"args is not valid JSON: {exc}", is_error=True)
        args = args or {}
        edits = self.project.edits()
        if not 1 <= int(index) <= len(edits):
            return ToolResult(f"there is no edit #{index}; EDITS has {len(edits)} entries", is_error=True)
        edit = dict(edits[int(index) - 1])
        prof = edit.get("profile")
        if not isinstance(prof, dict):
            return ToolResult(f"edit #{index} ({edit.get('op')}) has no sketch profile", is_error=True)
        if prof.get("type") != "sketch":
            prof = S.from_simple(prof)
        try:
            if op != "solve":
                _sketch_op(self.project, prof, op, args)
            st = S.solve(prof) if (prof.get("constraints") or prof.get("dims")) else {"ok": True, "dof": None, "conflicts": []}
        except Exception as exc:
            return ToolResult(f"sketch op failed: {type(exc).__name__}: {exc}", is_error=True)
        edit["profile"] = prof
        edit["by"] = "claude"
        self.project.update_edit(int(index) - 1, edit)
        res = self._build_and_report(f"sketch {op} on edit {index}")
        head = f"Sketch now: {S.summary(prof)}; solver {'ok' if st['ok'] else 'CONFLICT ' + ', '.join(st['conflicts'][:4])}; dof {st.get('dof')}\n{json.dumps(prof)[:3000]}\n\n"
        return ToolResult(head + res.text, res.images, res.is_error)

    def remove_edit(self, index: int) -> ToolResult:
        """Remove one direct edit (1-based index) and rebuild."""
        edits = self.project.edits()
        if not 1 <= int(index) <= len(edits):
            return ToolResult(f"there is no edit #{index}; EDITS has {len(edits)} entries", is_error=True)
        self.project.remove_edit(int(index) - 1)
        return self._build_and_report(f"remove edit {index}")

    def cam(self, ops: list | str | None = None, part: str | None = None, post: str = "grbl", material: str | None = None,
            safe_z: float = 5.0) -> ToolResult:
        """2.5-D toolpaths and G-code for a part held top-up on a 3-axis mill. `ops` is a list of
        operations in order: "face" | "contour" | "pocket" | "drill" | "chamfer", or objects with options:
        {"op": "contour", "tool": "endmill_6", "side": "outside"|"inside", "tabs": 3, "tab_width": 6,
        "tab_height": 2, "depth": null, "stepdown": null, "stock_to_leave": 0}, {"op": "pocket", "tool",
        "stepdown", "stepover"} (pockets open to the top are found automatically), {"op": "drill",
        "tool": "drill_5", "diameter": 5, "peck": 3}, {"op": "face", "tool": "facemill_20", "depth": 0.5},
        {"op": "chamfer", "tool": "vbit_90", "size": 1}. Tools: endmill_3/6/10, ball_6, drill_3/5/6.5,
        vbit_90, facemill_20. post: grbl | linuxcnc. material scales the feeds (defaults to the part's).
        Writes out/<part>.gcode and out/<part>.toolpaths.png (shown) and returns the summary with the
        time estimate."""
        from .. import cam as C
        from ..model import load_model
        try:
            if isinstance(ops, str):
                ops = json.loads(ops) if ops.strip().startswith("[") else [o.strip() for o in ops.split(",") if o.strip()]
            parts = load_model(self.project.model_path).build()
            name = part or parts.names[0]
            if name not in parts.names:
                return ToolResult(f"no part {name!r}; parts: {parts.names}", is_error=True)
            shape = parts[name]
            mat = material or getattr(next(p for p in parts if p.name == name), "material", None)
            job = C.plan(shape, name, ops or ["face", {"op": "contour", "tabs": 3}, "pocket", "drill"], post=post, material=mat, safe_z=safe_z)
            self.project.out_dir.mkdir(parents=True, exist_ok=True)
            gpath = self.project.out_dir / f"{name}.gcode"
            gpath.write_text(C.gcode(job), encoding="utf-8")
            png = C.preview_png(job, shape, self.project.out_dir / f"{name}.toolpaths.png")
        except Exception as exc:
            return ToolResult(f"cam failed: {type(exc).__name__}: {exc}", is_error=True)
        return ToolResult(job.summary() + f"\nG-code: {gpath}", images=[png])

    def cut_file(self, part: str | None = None, kerf: float = 0.2, fmt: str = "dxf", flat_pattern: bool = False) -> ToolResult:
        """A 2-D cut file (laser / waterjet / plasma) of a flat part: its outlines at mid-thickness with
        half the kerf added outside and removed inside, as DXF (layers CUT_OUTER/CUT_INNER) or SVG. For a
        sheet-metal part, flat_pattern=true cuts its flat pattern instead. Writes out/<part>.cut.<fmt>."""
        from .. import cam as C
        from ..model import load_model
        try:
            parts = load_model(self.project.model_path).build()
            name = part or parts.names[0]
            if flat_pattern:
                states = getattr(parts, "sheet_states", {})
                if name not in states:
                    return ToolResult(f"{name!r} is not a sheet-metal part", is_error=True)
                from ..sheetmetal import flat_pattern as _fp
                shape, _lines = _fp(states[name])
            else:
                if name not in parts.names:
                    return ToolResult(f"no part {name!r}; parts: {parts.names}", is_error=True)
                shape = parts[name]
            self.project.out_dir.mkdir(parents=True, exist_ok=True)
            out = C.cut_file(shape, self.project.out_dir / f"{name}.cut.{fmt}", kerf=float(kerf), fmt=fmt)
        except Exception as exc:
            return ToolResult(f"cut file failed: {type(exc).__name__}: {exc}", is_error=True)
        return ToolResult(f"cut file written: {out} (kerf {float(kerf):g} mm)")

    def print_prep(self, bed: list[float] | str = "220x220", layer: float = 0.2, orient: bool = True, parts: list[str] | None = None) -> ToolResult:
        """Prepare for 3-D printing: the best of the six axis-aligned orientations per part (overhang
        area, support estimate, height, bed contact), and the parts arranged on the bed; writes the
        arranged plate as out/<name>.bed.stl and out/<name>.bed.3mf."""
        from .. import additive as A
        from ..kernel import export_parts
        from ..model import Part, Parts, load_model
        try:
            if isinstance(bed, str):
                w, _, d = bed.lower().partition("x")
                bed_t = (float(w), float(d or w))
            else:
                bed_t = (float(bed[0]), float(bed[1]))
            built = load_model(self.project.model_path).build()
            sel = Parts([p for p in built if (not parts or p.name in parts)])
            report = A.print_report(sel, bed_t, layer=float(layer))
            placed, placements, skipped = A.arrange(sel, bed_t, orient=orient)
            plate = Parts([Part(n, s, next(p.color for p in sel if p.name == n)) for n, s in placed])
            self.project.out_dir.mkdir(parents=True, exist_ok=True)
            out = export_parts(plate, self.project.out_dir, f"{self.project.name}.bed", ("stl", "3mf")) if len(plate) else {}
        except Exception as exc:
            return ToolResult(f"print prep failed: {type(exc).__name__}: {exc}", is_error=True)
        return ToolResult(report + (f"\nplate: {out.get('stl')} and {out.get('3mf')}" if out else ""))

    def compare_revisions(self, a: int | None = None, b: int | None = None) -> ToolResult:
        """Compare two build revisions (default: the previous successful build against the current one):
        which parts were added, removed or changed (volume, bounding box, face count) and the diff of
        the model source."""
        try:
            res = self.project.compare(a, b)
        except Exception as exc:
            return ToolResult(f"compare failed: {exc}", is_error=True)
        return ToolResult(res["summary"] + ("\n\nsource diff:\n" + res["source_diff"] if res["source_diff"] else "\n(no source change)"))

    def drawing(self, views: list[str] | None = None, sheet: str = "A4", style: str = "iso", projection: str = "third",
                notes: list[str] | None = None, annotations: list[dict] | str | None = None, gdt: list[dict] | str | None = None,
                datums: list[dict] | str | None = None, scales: dict | str | None = None, pdf: bool = False,
                dims: list[dict] | str | None = None) -> ToolResult:
        """Make an engineering drawing of the current model and write <name>.drawing.svg, <name>.dxf (and
        <name>.drawing.pdf when pdf=true) in the output folder; returns their paths and a PNG preview of the
        sheet to check. Views: front, back, top,
        bottom, left, right, iso, "section:XZ@20", "halfsection:XZ@0|YZ@0" (sectioned one side of the second
        plane), "detail:front@x,y:r:scale" (a circle of the parent view, enlarged), "aux:nx,ny,nz" (a view
        from that direction), "broken:front@x1..x2" (that band of the parent removed). Sheets A4, A3, letter;
        style iso|ansi (dimension text), projection third|first. notes: lines for the notes block.
        annotations: [{"view", "at": [x, y] (view mm, relative to the look-at point), "text"}] leaders;
        gdt: [{"view", "at", "symbol": flatness|perpendicularity|position|..., "tolerance", "datums": ["A"]}];
        datums: [{"view", "at", "letter"}]; scales: {"iso": 0.5}. Centre marks, centrelines, radius and
        angle dimensions, hole callouts, overall dimensions, balloons and a parts list (assemblies) are
        automatic. dims adds your own: [{"view", "from": [u, v], "to": [u, v], "kind": horizontal|vertical|
        aligned (default by direction), "offset": 8, "text"}] or groups {"view", "points": [[u, v], ...],
        "axis": "x"|"y", "offset", "baseline": true} (chain between consecutive points, or baseline: every
        point from the first); u, v are view mm relative to the look-at point like `at`, a negative offset
        puts the dimension below/left. A view with your own dims gets no automatic overall dimensions.
        Look at the preview: view labels sit just under each view, so keep dimensions clear of them."""
        from ..drawing import drawing_dxf, drawing_pdf, drawing_png, drawing_svg
        from ..model import load_model
        views = tuple(views or ("front", "top", "right", "iso"))
        def _j(x):
            if isinstance(x, str):
                try:
                    return json.loads(x) if x.strip() else None
                except json.JSONDecodeError as exc:
                    raise ValueError(f"not valid JSON: {exc}")
            return x
        try:
            annotations, gdt, datums, scales, dims = _j(annotations), _j(gdt), _j(datums), _j(scales), _j(dims)
            parts = load_model(self.project.model_path).build()
            svg = drawing_svg(parts, self.project.name, views=views, sheet=sheet, style=style, projection=projection, notes=notes or "",
                              annotations=annotations, gdt=gdt, datums=datums, scales=scales, dims=dims)
            self.project.out_dir.mkdir(parents=True, exist_ok=True)
            svg_path = self.project.out_dir / f"{self.project.name}.drawing.svg"
            svg_path.write_text(svg, encoding="utf-8")
            dxf_path = drawing_dxf(parts, self.project.name, self.project.out_dir / f"{self.project.name}.dxf", views=tuple(v for v in views if v != "iso"), sheet=sheet)
            out = f"drawing written: {svg_path} and {dxf_path} (views: {', '.join(views)}; {projection} angle, {style.upper()})"
            if pdf:
                pdf_path = drawing_pdf(svg, self.project.out_dir / f"{self.project.name}.drawing.pdf", sheet)
                out += f"; PDF: {pdf_path}"
        except Exception as exc:
            return ToolResult(f"drawing failed: {type(exc).__name__}: {exc}", is_error=True)
        images = []
        try:                                                    # a preview to look at (needs Chromium; silently skipped without)
            images.append(str(drawing_png(svg, self.project.out_dir / f"{self.project.name}.drawing.png", sheet)))
            out += "; preview PNG attached - check that the views, dimensions and notes sit cleanly"
        except Exception:
            pass
        return ToolResult(out, images=images)

    def design_notes(self, content: str) -> ToolResult:
        """Overwrite DESIGN.md: the running notes for this project (requirements as a
        checklist, assumptions, interface dimensions, decisions). Keep it short and current."""
        self.project.design_notes_path.write_text(content.strip() + "\n", encoding="utf-8")
        return ToolResult("DESIGN.md updated.")

    # ------------------------------------------------------------- internals
    def _build_and_report(self, note: str) -> ToolResult:
        self.on_event("build_start", {"note": note})
        res = self.project.build(note=note, render=True, quality=self.render_quality)
        self.on_event("build_end", {"ok": res.ok, "seconds": res.seconds, "revision": self.project.revision,
                                    "outputs": res.outputs, "error": res.error})
        header = f"[revision {self.project.revision}] "
        if not res.ok:
            text = header + "BUILD FAILED\n" + (res.error or "unknown error")
            if res.summary:
                text += "\n\n" + res.summary
            return ToolResult(_truncate(text), images=[res.image] if res.image else [], is_error=True)
        text = header + f"built in {res.build_seconds:.1f}s; files: " + \
            ", ".join(Path(v).name for k, v in res.outputs.items() if k != "png") + "\n" + res.summary
        failed = [c for c in (res.report.checks if res.report else []) if not c.ok]
        issues = (res.report.issues if res.report else []) + \
            [i for p in (res.report.parts if res.report else []) for i in p.issues]
        if failed or issues:
            text += "\n\nATTENTION: " + ("; ".join(c.name for c in failed) + " failed verify()" if failed else "") + \
                (("; " if failed else "") + f"{len(issues)} geometry issue(s) flagged above" if issues else "")
        text += "\n\nThe image shows iso / front / top / right views. Check it against the request before finishing."
        return ToolResult(_truncate(text), images=[res.image] if res.image else [])

    def _run_render(self, out_png: Path, args: list[str]) -> tuple[bool, str]:
        import subprocess
        import sys
        from ..executor import current, require_local_exec
        executor = current()
        if executor is not None:
            res = executor.run("render", self.project.folder, self.project.model_path.name, args, 240, out_name=out_png.name)
            data = res.files.get(out_png.name)
            if res.error or res.code != 0 or data is None:
                return False, res.error or (res.stderr or res.stdout)[-2000:] or "render failed"
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(data)
            return True, res.stdout.strip()
        require_local_exec("a render")
        cmd = [sys.executable, "-m", "eitri._render_cli", "--model", str(self.project.model_path),
               "--out", str(out_png)] + args
        try:
            proc = subprocess.run(cmd, cwd=str(self.project.folder), capture_output=True, text=True, encoding="utf-8", timeout=240)
        except subprocess.TimeoutExpired:
            return False, "render timed out"
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout)[-2000:]
        return True, proc.stdout.strip()


def _normalize_code(code: str) -> str:
    code = code.replace("\r\n", "\n")
    # strip a ```python fence if the model wrapped the file in one
    m = re.match(r"^\s*```(?:python)?\n(.*)\n```\s*$", code, re.S)
    if m:
        code = m.group(1)
    code = textwrap.dedent(code)
    if not code.endswith("\n"):
        code += "\n"
    return code


def _fuzzy_find(haystack: str, needle: str) -> str | None:
    """Find `needle` ignoring trailing whitespace per line; return the exact text in haystack."""
    n_lines = [l.rstrip() for l in needle.strip("\n").splitlines()]
    h_lines = haystack.splitlines(keepends=True)
    stripped = [l.rstrip("\n").rstrip() for l in h_lines]
    for i in range(len(stripped) - len(n_lines) + 1):
        if stripped[i:i + len(n_lines)] == n_lines:
            return "".join(h_lines[i:i + len(n_lines)]).rstrip("\n")
    # try ignoring leading indentation too
    n2 = [l.strip() for l in n_lines]
    s2 = [l.strip() for l in stripped]
    for i in range(len(s2) - len(n2) + 1):
        if s2[i:i + len(n2)] == n2:
            return "".join(h_lines[i:i + len(n2)]).rstrip("\n")
    return None


def _truncate(text: str, limit: int = MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit // 2] + "\n... [truncated] ...\n" + text[-limit // 2:]


TOOL_NAMES = ["build_model", "edit_model", "set_params", "read_model", "inspect", "view", "library", "export",
              "design_notes"]
