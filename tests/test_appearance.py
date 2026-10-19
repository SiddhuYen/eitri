"""Per-face appearances: the appearance op colours individual faces by selector, the colours survive later
edits (resolved on the finished part), the GLB carries one material per colour, and clear resets them."""
from __future__ import annotations

import json
import struct

import build123d as bd

from eitri.edits import apply_edits, describe
from eitri.model import Part, Parts
from eitri.viewglb import write_view_glb


def _glb_json(path):
    data = path.read_bytes()
    assert data[:4] == b"glTF"
    length = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20:20 + length])


def test_face_colours(tmp_path):
    box = bd.Box(40, 30, 10, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    parts = Parts([Part("box", box, "#999999", "aluminum")])
    top = {"kind": "face", "type": "plane", "center": [0, 0, 10], "normal": [0, 0, 1], "area": 1200}
    side = {"kind": "face", "type": "plane", "center": [20, 0, 5], "normal": [1, 0, 0], "area": 300}
    out, notes = apply_edits(parts, [
        {"op": "appearance", "part": "box", "faces": [top], "color": "#ff0000"},
        {"op": "fillet", "part": "box", "edges": [{"kind": "edge", "type": "line", "mid": [0, 15, 10], "length": 40}], "radius": 2},   # a later edit: the top face moves on
        {"op": "appearance", "part": "box", "face": side, "color": "#0000ff"},
        {"op": "appearance", "part": "box", "faces": [top], "color": "red"},                                                           # not a hex colour
    ])
    assert [n.ok for n in notes] == [True, True, True, False] and "#rrggbb" in notes[3].detail
    part = next(p for p in out if p.name == "box")
    faces = part.shape.faces()
    assert part.face_colors and len(part.face_colors) == 2
    coloured = {part.face_colors[i]: faces[i] for i in part.face_colors}
    assert abs(coloured["#ff0000"].center().Z - 10) < 1e-6 and abs(coloured["#0000ff"].center().X - 20) < 1e-6
    glb = tmp_path / "v.glb"
    write_view_glb(out, glb, tmp_path / "t.json")
    g = _glb_json(glb)
    names = [m["name"] for m in g["materials"]]
    assert names[0] == "box" and sorted(n for n in names if n.startswith("box:")) == ["box:#0000ff", "box:#ff0000"]
    red = next(m for m in g["meshes"] if m["primitives"][0]["material"] == names.index("box:#ff0000"))
    assert red["name"].startswith("box:f")
    assert describe({"op": "appearance", "part": "box", "faces": [top, side], "color": "#ff0000"}) == "colour 2 face(s) of box #ff0000"
    # the agent's renders show the colour too: a top view of the box is mostly red
    from PIL import Image
    from eitri.render import contact_sheet
    png = contact_sheet(out, str(tmp_path / "top.png"), views=("top",))
    px = list(Image.open(png).convert("RGB").getdata())
    red = sum(1 for r, g, b in px if r > 150 and g < 90 and b < 90)
    grey = sum(1 for r, g, b in px if abs(r - g) < 12 and abs(g - b) < 12 and 90 < r < 200)
    assert red > 2000 and red > grey, (red, grey)
    out2, notes2 = apply_edits(parts, [{"op": "appearance", "part": "box", "faces": [top], "color": "#ff0000"}, {"op": "appearance", "part": "box", "clear": True}])
    assert all(n.ok for n in notes2) and not next(p for p in out2 if p.name == "box").face_colors
