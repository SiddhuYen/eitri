"""Form (sculpted) bodies: a quad control cage, Catmull-Clark subdivision with creases and symmetry, cage
edits (move vertices / faces, extrude a face, insert an edge loop, crease edges), and conversion to a
smooth B-rep solid: every control quad becomes one B-spline patch through its subdivided point grid,
the patches are sewn into a shell and made solid."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import build123d as bd
from build123d import Vector
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopoDS import TopoDS


@dataclass
class Cage:
    """A closed quad mesh: vertices, quads (4 vertex indices, counter-clockwise seen from outside),
    creased edges (sharp through subdivision) and an optional mirror symmetry axis."""
    vertices: list[list[float]]
    faces: list[list[int]]
    creases: list[list[int]] = field(default_factory=list)      # [a, b] vertex pairs
    symmetry: str | None = None                                  # "x" | "y" | "z": the cage is kept mirror-symmetric about that plane

    def to_dict(self) -> dict:
        return {"vertices": [[round(c, 5) for c in v] for v in self.vertices], "faces": [list(f) for f in self.faces],
                "creases": [list(c) for c in self.creases], "symmetry": self.symmetry}

    @staticmethod
    def from_dict(d: dict) -> "Cage":
        return Cage([list(map(float, v)) for v in d["vertices"]], [list(map(int, f)) for f in d["faces"]],
                    [list(map(int, c)) for c in d.get("creases", [])], d.get("symmetry"))

    def copy(self) -> "Cage":
        return Cage.from_dict(self.to_dict())

    # ---- topology helpers
    def edges(self) -> list[tuple[int, int]]:
        seen = set()
        out = []
        for f in self.faces:
            for i in range(len(f)):
                a, b = f[i], f[(i + 1) % len(f)]
                key = (min(a, b), max(a, b))
                if key not in seen:
                    seen.add(key)
                    out.append(key)
        return out

    def face_center(self, fi: int) -> Vector:
        f = self.faces[fi]
        return sum((Vector(*self.vertices[i]) for i in f), Vector(0, 0, 0)) / len(f)

    def face_normal(self, fi: int) -> Vector:
        f = self.faces[fi]
        p = [Vector(*self.vertices[i]) for i in f]
        n = Vector(0, 0, 0)
        for i in range(len(p)):                                   # Newell's method
            a, b = p[i], p[(i + 1) % len(p)]
            n = n + Vector((a.Y - b.Y) * (a.Z + b.Z), (a.Z - b.Z) * (a.X + b.X), (a.X - b.X) * (a.Y + b.Y))
        return n.normalized() if n.length > 1e-12 else Vector(0, 0, 1)

    def is_closed(self) -> bool:
        count: dict[tuple[int, int], int] = {}
        for f in self.faces:
            for i in range(len(f)):
                key = (min(f[i], f[(i + 1) % len(f)]), max(f[i], f[(i + 1) % len(f)]))
                count[key] = count.get(key, 0) + 1
        return all(c == 2 for c in count.values())


# ---------------------------------------------------------------------------- primitives

def box(w: float = 20, d: float = 20, h: float = 20, nx: int = 1, ny: int = 1, nz: int = 1, center=(0, 0, 0)) -> Cage:
    """A box cage with nx x ny x nz segments per side (sits with its centre at `center`)."""
    cx, cy, cz = center
    verts: list[list[float]] = []
    index: dict[tuple, int] = {}
    def vid(i, j, k):
        key = (i, j, k)
        if key not in index:
            index[key] = len(verts)
            verts.append([cx - w / 2 + w * i / nx, cy - d / 2 + d * j / ny, cz - h / 2 + h * k / nz])
        return index[key]
    faces = []
    for i in range(nx):
        for j in range(ny):
            faces.append([vid(i, j, 0), vid(i, j + 1, 0), vid(i + 1, j + 1, 0), vid(i + 1, j, 0)])            # bottom (normal -z)
            faces.append([vid(i, j, nz), vid(i + 1, j, nz), vid(i + 1, j + 1, nz), vid(i, j + 1, nz)])        # top
    for i in range(nx):
        for k in range(nz):
            faces.append([vid(i, 0, k), vid(i + 1, 0, k), vid(i + 1, 0, k + 1), vid(i, 0, k + 1)])            # front (-y)
            faces.append([vid(i, ny, k), vid(i, ny, k + 1), vid(i + 1, ny, k + 1), vid(i + 1, ny, k)])        # back
    for j in range(ny):
        for k in range(nz):
            faces.append([vid(0, j, k), vid(0, j, k + 1), vid(0, j + 1, k + 1), vid(0, j + 1, k)])            # left (-x)
            faces.append([vid(nx, j, k), vid(nx, j + 1, k), vid(nx, j + 1, k + 1), vid(nx, j, k + 1)])        # right
    return Cage(verts, faces)


def sphere(r: float = 10, n: int = 2, center=(0, 0, 0)) -> Cage:
    """A quad sphere: a subdivided cube projected onto the sphere."""
    c = box(2, 2, 2, n, n, n)
    cx, cy, cz = center
    c.vertices = [[cx + r * x / math.sqrt(x * x + y * y + z * z), cy + r * y / math.sqrt(x * x + y * y + z * z), cz + r * z / math.sqrt(x * x + y * y + z * z)] for x, y, z in c.vertices]
    return c


def cylinder(r: float = 10, h: float = 20, n: int = 2, nz: int = 1, center=(0, 0, 0)) -> Cage:
    """A capped cylinder with quad caps: a box with n x n x nz segments whose sides are rounded into a circle."""
    c = box(2, 2, h, n, n, nz, (0, 0, 0))
    cx, cy, cz = center
    out = []
    for x, y, z in c.vertices:
        m = max(abs(x), abs(y))
        if m < 1e-12:
            out.append([cx, cy, cz + z]); continue
        # map the square to the disc: radial scale by the square's "radius" along this direction
        k = r * m / math.hypot(x, y)
        out.append([cx + x * k, cy + y * k, cz + z])
    c.vertices = out
    return c


def torus(R: float = 15, r: float = 5, n: int = 8, m: int = 4, center=(0, 0, 0)) -> Cage:
    cx, cy, cz = center
    verts = [[cx + (R + r * math.cos(2 * math.pi * j / m)) * math.cos(2 * math.pi * i / n), cy + (R + r * math.cos(2 * math.pi * j / m)) * math.sin(2 * math.pi * i / n), cz + r * math.sin(2 * math.pi * j / m)]
             for i in range(n) for j in range(m)]
    faces = []
    for i in range(n):
        for j in range(m):
            a, b, c2, d = i * m + j, i * m + (j + 1) % m, ((i + 1) % n) * m + (j + 1) % m, ((i + 1) % n) * m + j
            faces.append([a, d, c2, b])
    return Cage(verts, faces)


PRIMITIVES = {"box": box, "sphere": sphere, "cylinder": cylinder, "torus": torus}


# ---------------------------------------------------------------------------- cage edits

def move_vertices(cage: Cage, indices: Sequence[int], delta: Sequence[float]) -> None:
    dx, dy, dz = (float(c) for c in delta)
    for i in set(int(k) for k in indices):
        v = cage.vertices[i]
        cage.vertices[i] = [v[0] + dx, v[1] + dy, v[2] + dz]
    apply_symmetry(cage)


def move_face(cage: Cage, fi: int, delta: Sequence[float] | None = None, along_normal: float | None = None) -> None:
    f = cage.faces[fi]
    if along_normal is not None:
        n = cage.face_normal(fi) * float(along_normal)
        delta = (n.X, n.Y, n.Z)
    move_vertices(cage, f, delta or (0, 0, 0))


def extrude_face(cage: Cage, fi: int, distance: float, scale: float = 1.0) -> list[int]:
    """Pull a face out along its normal: four new side quads, the face moves to the new ring."""
    f = list(cage.faces[fi])
    n = cage.face_normal(fi)
    c = cage.face_center(fi)
    new_ids = []
    for i in f:
        p = Vector(*cage.vertices[i])
        q = c + (p - c) * scale + n * float(distance)
        new_ids.append(len(cage.vertices))
        cage.vertices.append([q.X, q.Y, q.Z])
    for k in range(len(f)):
        a, b = f[k], f[(k + 1) % len(f)]
        cage.faces.append([a, b, new_ids[(k + 1) % len(f)], new_ids[k]])
    cage.faces[fi] = new_ids
    apply_symmetry(cage)
    return new_ids


def insert_loop(cage: Cage, edge: Sequence[int], t: float = 0.5) -> int:
    """Insert an edge loop crossing the given edge (and the ring of quads around it) at parameter t.
    Returns the number of edges split."""
    a, b = int(edge[0]), int(edge[1])
    ring = []                                          # the sequence of edges to split: walk across quads
    cur = (a, b)
    visited = set()
    while cur not in visited:
        visited.add(cur)
        ring.append(cur)
        # the quad containing cur (as a..b in order) -> the opposite edge
        nxt = None
        for f in cage.faces:
            if len(f) != 4:
                continue
            for i in range(4):
                if f[i] == cur[0] and f[(i + 1) % 4] == cur[1]:
                    nxt = (f[(i + 3) % 4], f[(i + 2) % 4])        # opposite edge, same direction sense across the quad
                    break
            if nxt:
                break
        if nxt is None:
            break
        cur = nxt                                      # the adjacent quad holds this edge in this order
        if cur == (a, b) or (cur[1], cur[0]) == (a, b):
            break
    # split every ring edge with a new vertex
    mid: dict[tuple[int, int], int] = {}
    for (p, q) in ring:
        key = (min(p, q), max(p, q))
        if key in mid:
            continue
        P, Q = Vector(*cage.vertices[p]), Vector(*cage.vertices[q])
        M = P + (Q - P) * float(t if p == key[0] else 1 - t)
        mid[key] = len(cage.vertices)
        cage.vertices.append([M.X, M.Y, M.Z])
    new_faces = []
    for f in cage.faces:
        if len(f) != 4:
            new_faces.append(f); continue
        keys = [(min(f[i], f[(i + 1) % 4]), max(f[i], f[(i + 1) % 4])) for i in range(4)]
        hits = [i for i, k in enumerate(keys) if k in mid]
        if len(hits) == 2 and (hits[1] - hits[0]) == 2:          # split the quad into two across the two midpoints
            i0, i1 = hits
            m0, m1 = mid[keys[i0]], mid[keys[i1]]
            # f = [v0, v1, v2, v3]; edges i0: v_i0-v_i0+1, i1 opposite
            r = f[i0:] + f[:i0]                                     # rotate so that the split edge is first: r0-r1 split by m0, r2-r3 split by m1
            new_faces.append([r[0], m0, m1, r[3]])
            new_faces.append([m0, r[1], r[2], m1])
        else:
            new_faces.append(f)
    cage.faces = new_faces
    apply_symmetry(cage)
    return len(mid)


def crease(cage: Cage, edges: Sequence[Sequence[int]], on: bool = True) -> None:
    for e in edges:
        key = [int(e[0]), int(e[1])]
        rev = [key[1], key[0]]
        cage.creases = [c for c in cage.creases if c != key and c != rev]
        if on:
            cage.creases.append(key)


def apply_symmetry(cage: Cage, tol: float = 1e-6) -> None:
    """Mirror the positive half of the cage onto the negative half about the symmetry plane
    (vertices are paired by their mirrored position)."""
    if not cage.symmetry:
        return
    ax = {"x": 0, "y": 1, "z": 2}[cage.symmetry]
    pos = [v for v in cage.vertices]
    for i, v in enumerate(pos):
        if v[ax] <= tol:
            continue
        mirror = list(v); mirror[ax] = -v[ax]
        best, bd_ = None, 1e9
        for j, u in enumerate(pos):
            if j == i or u[ax] > -tol:
                continue
            d = math.dist([u[k] if k != ax else -u[ax] for k in range(3)], v)
            if d < bd_:
                bd_, best = d, j
        if best is not None and bd_ < 0.5 * max(1.0, math.dist(v, [0, 0, 0])):
            cage.vertices[best] = mirror


# ---------------------------------------------------------------------------- Catmull-Clark

def subdivide(cage: Cage) -> Cage:
    """One Catmull-Clark step (creased edges stay sharp: their edge points are midpoints and their
    end vertices move by the crease rule)."""
    V = [Vector(*v) for v in cage.vertices]
    F = cage.faces
    crease_set = {(min(a, b), max(a, b)) for a, b in cage.creases}
    face_pts = [sum((V[i] for i in f), Vector(0, 0, 0)) / len(f) for f in F]
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for fi, f in enumerate(F):
        for i in range(len(f)):
            key = (min(f[i], f[(i + 1) % len(f)]), max(f[i], f[(i + 1) % len(f)]))
            edge_faces.setdefault(key, []).append(fi)
    edge_pts: dict[tuple[int, int], Vector] = {}
    for key, fs in edge_faces.items():
        a, b = key
        if key in crease_set or len(fs) < 2:
            edge_pts[key] = (V[a] + V[b]) / 2
        else:
            edge_pts[key] = (V[a] + V[b] + face_pts[fs[0]] + face_pts[fs[1]]) / 4
    vert_faces: dict[int, list[int]] = {}
    vert_edges: dict[int, list[tuple[int, int]]] = {}
    for fi, f in enumerate(F):
        for i in range(len(f)):
            vert_faces.setdefault(f[i], []).append(fi)
            key = (min(f[i], f[(i + 1) % len(f)]), max(f[i], f[(i + 1) % len(f)]))
            for v in key:
                if key not in vert_edges.setdefault(v, []):
                    vert_edges[v].append(key)
    new_V: list[Vector] = []
    for vi, P in enumerate(V):
        fs = vert_faces.get(vi, [])
        es = vert_edges.get(vi, [])
        cr = [e for e in es if e in crease_set]
        n = len(fs)
        if len(cr) >= 3:                                           # a corner (three or more creases meet): fixed
            new_V.append(P)
        elif len(cr) == 2:                                         # on a crease: 1/8 rule along the crease
            other = [V[e[0] if e[1] == vi else e[1]] for e in cr]
            new_V.append(P * 0.75 + (other[0] + other[1]) * 0.125)
        elif n == 0:
            new_V.append(P)
        else:
            Fa = sum((face_pts[fi] for fi in fs), Vector(0, 0, 0)) / n
            Ra = sum(((V[e[0]] + V[e[1]]) / 2 for e in es), Vector(0, 0, 0)) / len(es)
            new_V.append((Fa + Ra * 2 + P * (n - 3)) / n)
    verts = [[p.X, p.Y, p.Z] for p in new_V]
    index_e: dict[tuple[int, int], int] = {}
    for key, p in edge_pts.items():
        index_e[key] = len(verts); verts.append([p.X, p.Y, p.Z])
    index_f: list[int] = []
    for p in face_pts:
        index_f.append(len(verts)); verts.append([p.X, p.Y, p.Z])
    faces = []
    for fi, f in enumerate(F):
        k = len(f)
        for i in range(k):
            v = f[i]
            e_prev = (min(f[(i - 1) % k], v), max(f[(i - 1) % k], v))
            e_next = (min(v, f[(i + 1) % k]), max(v, f[(i + 1) % k]))
            faces.append([v, index_e[e_next], index_f[fi], index_e[e_prev]])
    new_creases = []
    for a, b in cage.creases:
        key = (min(a, b), max(a, b))
        if key in index_e:
            new_creases.append([a, index_e[key]]); new_creases.append([index_e[key], b])
    return Cage(verts, faces, new_creases, cage.symmetry)


def subdivided(cage: Cage, levels: int) -> Cage:
    c = cage
    for _ in range(max(0, int(levels))):
        c = subdivide(c)
    return c


# ---------------------------------------------------------------------------- to a smooth solid

def _quad_grid(cage: Cage, fi: int, levels: int) -> list[list[Vector]]:
    """The (2^L + 1)^2 grid of subdivided points covering one control quad, by subdividing the cage
    and tracking the quad's descendants: implemented by subdividing with the quad's corner order kept
    (each Catmull-Clark step turns quad [v0..v3] into 4 quads laid out as a 3x3 point grid)."""
    # grid of vertex indices per level: start with the quad corners as a 2x2 grid
    grids: dict[int, list[list[int]]] = {fi: [[cage.faces[fi][0], cage.faces[fi][1]], [cage.faces[fi][3], cage.faces[fi][2]]]}
    c = cage
    sizes = 2
    grid = grids[fi]
    for _ in range(levels):
        nxt = subdivide(c)
        # after subdivision, old vertex i keeps index i; edge (a,b) -> index of its edge point; face -> its face point
        nV = len(c.vertices)
        edge_index: dict[tuple[int, int], int] = {}
        k = nV
        # replicate the index assignment of subdivide(): edges in the order of first appearance over faces
        seen = set()
        order = []
        for f in c.faces:
            for i in range(len(f)):
                key = (min(f[i], f[(i + 1) % len(f)]), max(f[i], f[(i + 1) % len(f)]))
                if key not in seen:
                    seen.add(key); order.append(key)
        for key in order:
            edge_index[key] = k; k += 1
        face_index = {j: k + j for j in range(len(c.faces))}
        # the sub-quads of the tracked grid cells: find each cell's face index in c
        face_of: dict[tuple, int] = {}
        for j, f in enumerate(c.faces):
            face_of[tuple(sorted(f))] = j
        n = sizes
        new_grid = [[0] * (2 * n - 1) for _ in range(2 * n - 1)]
        for i in range(n):
            for j in range(n):
                new_grid[2 * i][2 * j] = grid[i][j]
        for i in range(n):
            for j in range(n - 1):
                a, b = grid[i][j], grid[i][j + 1]
                new_grid[2 * i][2 * j + 1] = edge_index[(min(a, b), max(a, b))]
        for i in range(n - 1):
            for j in range(n):
                a, b = grid[i][j], grid[i + 1][j]
                new_grid[2 * i + 1][2 * j] = edge_index[(min(a, b), max(a, b))]
        for i in range(n - 1):
            for j in range(n - 1):
                cell = tuple(sorted((grid[i][j], grid[i][j + 1], grid[i + 1][j + 1], grid[i + 1][j])))
                new_grid[2 * i + 1][2 * j + 1] = face_index[face_of[cell]]
        grid = new_grid
        sizes = 2 * n - 1
        c = nxt
    return [[Vector(*c.vertices[idx]) for idx in row] for row in grid]


def _patch(grid: list[list[Vector]]) -> bd.Face:
    """An interpolating B-spline surface through a point grid with uniform (iso-parametric) parameters:
    adjacent patches then share their boundary curves exactly, so they sew without gaps."""
    from OCP.Approx import Approx_ParametrizationType
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.collections import HArray2_gp_Pnt
    from OCP.GeomAPI import GeomAPI_PointsToBSplineSurface
    from OCP.gp import gp_Pnt
    n, m = len(grid), len(grid[0])
    arr = HArray2_gp_Pnt(1, n, 1, m)
    for i in range(n):
        for j in range(m):
            p = grid[i][j]
            arr.SetValue(i + 1, j + 1, gp_Pnt(p.X, p.Y, p.Z))
    api = GeomAPI_PointsToBSplineSurface()
    api.Interpolate(arr, Approx_ParametrizationType.Approx_IsoParametric, False)
    return bd.Face(BRepBuilderAPI_MakeFace(api.Surface(), 1e-6).Face())


def to_solid(cage: Cage, levels: int = 2, tolerance: float = 1e-3) -> bd.Solid:
    """The smooth body: one interpolating B-spline patch per control quad through its subdivided grid
    (creases make sharp edges because the grids share the crease points), sewn and made solid."""
    if not cage.is_closed():
        raise ValueError("the form cage is not closed (every edge needs two faces)")
    if any(len(f) != 4 for f in cage.faces):
        raise ValueError("the form cage must be all quads")
    levels = max(1, int(levels))
    sew = BRepBuilderAPI_Sewing(tolerance)
    for fi in range(len(cage.faces)):
        sew.Add(_patch(_quad_grid(cage, fi, levels)).wrapped)
    sew.Perform()
    sh = sew.SewedShape()
    shell = None
    if sh.ShapeType() == TopAbs_ShapeEnum.TopAbs_SHELL:
        shell = TopoDS.Shell(sh)
    else:
        shells = bd.Compound(sh).shells()
        if len(shells) == 1:
            shell = shells[0].wrapped
    if shell is None or not BRep_Tool.IsClosed_s(shell):
        raise ValueError("the form's patches did not close into a shell")
    sol = bd.Solid(BRepBuilderAPI_MakeSolid(shell).Solid())
    if sol.volume < 0:
        sol = bd.Solid(TopoDS.Solid(sol.wrapped.Reversed()))
    if not sol.is_valid:
        raise ValueError("the form did not make a valid solid")
    return sol


def cage_summary(cage: Cage, levels: int) -> str:
    return f"form cage: {len(cage.vertices)} vertices, {len(cage.faces)} quads, {len(cage.creases)} creased edge(s), {levels} subdivision level(s)" + (f", symmetric about {cage.symmetry}" if cage.symmetry else "")
