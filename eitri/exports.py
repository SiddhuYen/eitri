"""What each kind of project can be downloaded as: one ordered list per domain, used by the editor's Export menu, the read-only
share page's download buttons and the /download route alike, so they cannot drift apart.

    export_formats(kind) -> [{"fmt", "label", "title", "href", "output"}]

`output` is the build output a domain's file comes from (a key of the revision's outputs; a page offers the file only when the last good
build wrote it), None for the CAD formats, which are written on demand.

CAD formats are written by the kernel on demand (kernel.export_parts, the drawing module); every other domain's files are written by
its build (`DOWNLOADS` in eitri/<domain>/domain.py maps a format to the build output), and `EXPORTS` there names and orders them.
"""
from __future__ import annotations

# (fmt, label, title). "model.py" is the model source itself, served at its own route.
CAD_EXPORTS: tuple[tuple[str, str, str], ...] = (
    ("step", "STEP", "STEP (.step): the solid model, for any CAD program"),
    ("stl", "STL", "STL (.stl): triangle mesh, one file for all parts"),
    ("3mf", "3MF", "3MF (.3mf): mesh with parts and units, for slicers"),
    ("glb", "GLB", "GLB (.glb): the model for viewers and the web"),
    ("obj", "OBJ", "Wavefront OBJ (.obj): meshes, one object per part"),
    ("iges", "IGES", "IGES (.iges): surfaces and solids"),
    ("svg", "Drawing SVG", "The engineering drawing (.svg)"),
    ("dxf", "Drawing DXF", "The engineering drawing (.dxf)"),
    ("model.py", "Model source", "The parametric model file (.py)"),
)


def export_formats(kind: str | None) -> list[dict]:
    """The downloads a project of this kind offers, in menu order."""
    if not kind or kind == "cad":
        rows, outputs = CAD_EXPORTS, {}
    else:
        from .domains import module
        mod = module(kind)
        rows = tuple(getattr(mod, "EXPORTS", ())) or tuple((f, f.upper(), f) for f in mod.DOWNLOADS)
        outputs = {f: v[0] for f, v in mod.DOWNLOADS.items()}
    return [{"fmt": f, "label": label, "title": title, "href": "model.py" if f == "model.py" else f"download?fmt={f}", "output": outputs.get(f)}
            for f, label, title in rows]
