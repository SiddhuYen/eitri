"""The viewer server's timeline endpoints: update/move with rollback, downloads, state extras."""
import json
import shutil
import urllib.request
from pathlib import Path

from eitri.project import Project
from eitri.viewer.server import start_viewer

MODEL = '''from eitri import *

PARAMS = dict(width=60.0, depth=40.0, thickness=6.0)

def build(p):
    return Box(p.width, p.depth, p.thickness, align=(Align.CENTER, Align.CENTER, Align.MIN))
'''

TOP = {"kind": "face", "type": "plane", "normal": [0, 0, 1], "center": [0, 0, 6], "area": 2400}


def _call(url, path, payload=None):
    req = urllib.request.Request(url + path.lstrip("/"), data=None if payload is None else json.dumps(payload).encode(),
                                 method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, dict(r.headers), r.read()


def _json(url, path, payload=None):
    return json.loads(_call(url, path, payload)[2])


def test_timeline_endpoints_and_downloads(tmp_path: Path):
    folder = tmp_path / "plate"
    shutil.rmtree(folder, ignore_errors=True)
    proj = Project(folder, name="plate")
    proj.write_model(MODEL)
    assert proj.build(note="first", render=False).ok
    url = start_viewer(proj)

    r = _json(url, "/edit", {"op": "hole", "part": "plate", "face": TOP, "origin": [0, 0, 6], "normal": [0, 0, 1], "at": [10, 5], "d": 5})
    assert r["ok"] and len(r["state"]["edits"]) == 1 and r["notes"][0]["ok"]
    r = _json(url, "/edit", {"op": "push_pull", "part": "plate", "face": TOP, "distance": 2})
    assert r["ok"] and len(r["state"]["edits"]) == 2

    # edit feature: change the hole's diameter in place
    hole = dict(r["state"]["edits_full"][0]); hole["d"] = 8
    r = _json(url, "/edits/update", {"index": 0, "edit": hole})
    assert r["ok"] and r["state"]["edits_full"][0]["d"] == 8 and "Ø8" in r["state"]["edits"][0]["description"]

    # a change that cannot build is rolled back with the reason
    bad = dict(hole); bad["part"] = "nope"
    r = _json(url, "/edits/update", {"index": 0, "edit": bad})
    assert not r["ok"] and "no such part" in r["error"] and r["state"]["edits_full"][0]["part"] == "plate"

    # suppress via update, then reorder
    sup = dict(hole); sup["suppressed"] = True
    r = _json(url, "/edits/update", {"index": 0, "edit": sup})
    assert r["ok"] and "(suppressed)" in r["state"]["edits"][0]["description"]
    r = _json(url, "/edits/move", {"index": 1, "to": 0})
    assert r["ok"] and r["state"]["edits_full"][0]["op"] == "push_pull" and r["state"]["edits_full"][1]["op"] == "hole"

    st = _json(url, "/state.json")
    assert "steel" in st["materials"] and st["edits_full"][1]["suppressed"] is True
    assert st["parts"][0]["mass_g"] is None

    status, headers, body = _call(url, "/download?fmt=stl")
    assert status == 200 and "plate.stl" in headers.get("Content-Disposition", "") and len(body) > 100
    status, headers, body = _call(url, "/download?fmt=step")
    assert status == 200 and body.startswith(b"ISO-10303")
    # undo goes back to the previous different model; redo returns
    before = _json(url, "/state.json")["ok_revision"]
    r = _json(url, "/undo", {})
    assert r["ok"] and r["state"]["edits_full"][0]["op"] == "hole"                 # back before the reorder
    r = _json(url, "/redo", {})
    assert r["ok"] and r["state"]["edits_full"][0]["op"] == "push_pull" and r["state"]["ok_revision"] > before
    assert not _json(url, "/redo", {})["ok"]

    status, headers, body = _call(url, "/download?fmt=svg&inline=1")
    assert status == 200 and b"<svg" in body and b'id="view-front"' in body and "attachment" not in headers.get("Content-Disposition", "")
    status, headers, body = _call(url, "/download?fmt=dxf&views=front,top")
    assert status == 200 and "plate.dxf" in headers.get("Content-Disposition", "") and b"HIDDEN" in body
    try:
        _call(url, "/download?fmt=exe")
        assert False, "unknown format must 404"
    except urllib.error.HTTPError as exc:
        assert exc.code == 404


def test_interference_endpoint(tmp_path: Path):
    proj = Project(tmp_path / "pair", name="pair")
    proj.write_model("from eitri import *\nPARAMS = {}\n\ndef build(p):\n    return {'a': Box(20, 20, 20), 'b': Pos(15, 0, 0) * Box(20, 20, 20), 'c': Pos(100, 0, 0) * Box(5, 5, 5)}\n")
    assert proj.build(note="first", render=False).ok
    url = start_viewer(proj)
    r = _json(url, "/interference")
    assert r["ok"] and r["pairs"] == [{"a": "a", "b": "b", "volume": 2000.0}] and r["glb"]
    status, headers, body = _call(url, r["glb"])
    assert status == 200 and body[:4] == b"glTF"


def test_sketch_endpoints(tmp_path: Path):
    proj = Project(tmp_path / "sk", name="plate")
    proj.write_model(MODEL)
    assert proj.build(note="first", render=False).ok
    url = start_viewer(proj)
    sk = {"type": "sketch", "entities": [], "constraints": [], "dims": []}
    r = _json(url, "/sketch/op", {"sketch": sk, "op": "from_simple", "args": {"profile": {"type": "rect", "center": [0, 0], "w": 20, "h": 10}}})
    assert r["ok"] and len(r["sketch"]["entities"]) == 4 and r["status"]["ok"] and r["preview"]["profiles"] == 1 and r["status"]["dof"] == 2
    sk = r["sketch"]
    r = _json(url, "/sketch/op", {"sketch": sk, "op": "fillet", "args": {"a": "l1", "b": "l2", "r": 2}})
    assert r["ok"] and r["result"].startswith("a") and r["preview"]["profiles"] == 1
    sk = r["sketch"]
    r = _json(url, "/sketch/op", {"sketch": sk, "op": "set_dim", "args": {"index": 0, "value": 40}})
    assert r["ok"] and r["status"]["ok"]
    sk = r["sketch"]
    # the solved sketch extrudes through the normal edit path
    r = _json(url, "/edit", {"op": "sketch_extrude", "part": "plate", "face": TOP, "origin": [0, 0, 6], "normal": [0, 0, 1], "profile": sk, "distance": 3, "mode": "add"})
    assert r["ok"], r.get("error")
    st = r["state"]
    assert "sketch (" in st["edits"][0]["description"] and abs(st["parts"][0]["volume"] - (60 * 40 * 6 + (40 * 10 - (1 - 3.14159 / 4) * 4) * 3)) < 0.05
    # a conflicting dimension is refused with the reason
    bad = dict(sk); bad["dims"] = sk["dims"] + [{"type": "length", "a": "l3", "value": 99}]
    r = _json(url, "/sketch/solve", {"sketch": bad})
    assert r["ok"] and not r["status"]["ok"] and r["status"]["conflicts"]
