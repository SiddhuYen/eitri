"""A dependency-light software renderer for CAD review images.

Tessellates build123d shapes with OCCT, then rasterizes them with numpy
(z-buffer, flat Lambert shading, B-rep feature edges drawn on top with a depth
test). No GPU, no browser, no OpenGL: it runs anywhere build123d runs and takes
well under a second for typical parts.

The main entry point is `contact_sheet(parts, path, ...)`, which writes a 2x2
sheet of labelled views (iso / front / top / right) with overall dimensions and a
scale bar - the image the agent reads after every build. `render_view` draws one
custom view; `section_sheet` cuts the model with a plane and colours the cut faces.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import build123d as bd

from .model import Parts, srgb

# --------------------------------------------------------------------------
# Tessellation
# --------------------------------------------------------------------------

@dataclass
class Mesh:
    vertices: np.ndarray            # (N, 3)
    triangles: np.ndarray           # (M, 3) int
    color: tuple[float, float, float]
    edges: list[np.ndarray] = field(default_factory=list)   # polylines (K, 3)
    name: str = ""
    cut_faces: np.ndarray | None = None  # bool mask (M,) of triangles lying on a section plane


def _hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _edge_polyline(edge: bd.Edge, ang_defl: float, lin_defl: float) -> np.ndarray | None:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_TangentialDeflection

    try:
        if edge.geom_type == bd.GeomType.LINE:
            a, b = edge.position_at(0), edge.position_at(1)
            return np.array([[a.X, a.Y, a.Z], [b.X, b.Y, b.Z]])
        curve = BRepAdaptor_Curve(edge.wrapped)
        td = GCPnts_TangentialDeflection(curve, ang_defl, lin_defl, 2)
        n = td.NbPoints()
        if n < 2:
            return None
        pts = np.empty((n, 3))
        for i in range(1, n + 1):
            p = td.Value(i)
            pts[i - 1] = (p.X(), p.Y(), p.Z())
        return pts
    except Exception:
        return None


def tessellate(shape: bd.Shape, color: str, diag: float, name: str = "",
               quality: float = 1.0, with_edges: bool = True) -> Mesh | None:
    """Mesh one shape. `diag` is the whole model's bbox diagonal (sets tolerances)."""
    lin = max(diag * 0.0015 / quality, 1e-3)
    ang = 0.25 / quality
    try:
        from .fastmesh import all_triangles
        shape.mesh(lin, ang)                       # build123d's rule: reuse a fine enough mesh, else mesh
        tri = all_triangles(shape)                 # every triangle in one C++ call
        v = tri.reshape(-1, 3).astype(float)
        t = np.arange(len(v), dtype=np.int64).reshape(-1, 3)
    except Exception:
        try:
            verts, tris = shape.tessellate(lin, ang)
        except Exception:
            return None
        v = np.array([[p.X, p.Y, p.Z] for p in verts], dtype=float) if tris else np.zeros((0, 3))
        t = np.array(tris, dtype=np.int64)
    if len(t) == 0:
        return None
    edges: list[np.ndarray] = []
    if with_edges:
        for e in shape.edges():
            pl = _edge_polyline(e, 0.35 / quality, max(diag * 0.003 / quality, 1e-3))
            if pl is not None and len(pl) >= 2:
                edges.append(pl)
    return Mesh(vertices=v, triangles=t, color=_hex_to_rgb(color), edges=edges, name=name)


def tessellate_parts(parts: Parts, quality: float = 1.0, with_edges: bool = True) -> list[Mesh]:
    diag = max(parts.bbox.diagonal, 1e-6)
    meshes = []
    for p in parts:
        fcolors = getattr(p, "face_colors", None) or {}
        if fcolors:                                   # per-face appearances: the coloured faces as meshes of their own
            faces = p.shape.faces()
            by_color: dict[str, list] = {}
            plain = []
            for i, f in enumerate(faces):
                c = fcolors.get(i, fcolors.get(str(i)))
                (by_color.setdefault(c, []) if c else plain).append(f)
            m = tessellate(bd.Compound(plain), p.color, diag, p.name, quality, False) if plain else None
            if m is not None:
                if with_edges:
                    m.edges = [pl for pl in (_edge_polyline(e, 0.35 / quality, max(diag * 0.003 / quality, 1e-3)) for e in p.shape.edges()) if pl is not None and len(pl) >= 2]
                meshes.append(m)
            for c, fs in by_color.items():
                mc = tessellate(bd.Compound(fs), c, diag, p.name, quality, False)
                if mc is not None:
                    meshes.append(mc)
            continue
        m = tessellate(p.shape, p.color, diag, p.name, quality, with_edges)
        if m is not None:
            meshes.append(m)
    return meshes


# --------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------

VIEW_DIRS = {
    # name: (camera direction FROM target, up hint)
    "iso": ((1.0, -1.0, 0.8), (0, 0, 1)),
    "iso_back": ((-1.0, 1.0, 0.8), (0, 0, 1)),
    "iso_below": ((1.0, -1.0, -0.8), (0, 0, 1)),
    "front": ((0.0, -1.0, 0.0), (0, 0, 1)),
    "back": ((0.0, 1.0, 0.0), (0, 0, 1)),
    "top": ((0.0, 0.0, 1.0), (0, 1, 0)),
    "bottom": ((0.0, 0.0, -1.0), (0, 1, 0)),
    "right": ((1.0, 0.0, 0.0), (0, 0, 1)),
    "left": ((-1.0, 0.0, 0.0), (0, 0, 1)),
}


@dataclass
class Camera:
    right: np.ndarray
    up: np.ndarray
    forward: np.ndarray  # direction the camera looks (into the screen)
    center: np.ndarray
    scale: float         # pixels per mm
    width: int
    height: int

    def project(self, pts: np.ndarray) -> np.ndarray:
        """World (N,3) -> screen (N,3): x px, y px (down), depth (larger = farther)."""
        rel = pts - self.center
        x = rel @ self.right
        y = rel @ self.up
        z = rel @ self.forward
        sx = self.width / 2 + x * self.scale
        sy = self.height / 2 - y * self.scale
        return np.column_stack([sx, sy, z])


def make_camera(direction: Sequence[float], up: Sequence[float], points: np.ndarray,
                width: int, height: int, padding: float = 0.08,
                fixed_scale: float | None = None, center: np.ndarray | None = None) -> Camera:
    d = np.asarray(direction, dtype=float)
    d /= np.linalg.norm(d)
    forward = -d
    upv = np.asarray(up, dtype=float)
    if abs(np.dot(upv / np.linalg.norm(upv), forward)) > 0.999:
        upv = np.array([0.0, 1.0, 0.0]) if abs(forward[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    right = np.cross(forward, upv)
    right /= np.linalg.norm(right)
    true_up = np.cross(right, forward)
    if center is None:
        lo, hi = points.min(axis=0), points.max(axis=0)
        center = (lo + hi) / 2
    rel = points - center
    x = rel @ right
    y = rel @ true_up
    ext_x = max(x.max() - x.min(), 1e-6)
    ext_y = max(y.max() - y.min(), 1e-6)
    if fixed_scale is None:
        scale = min(width * (1 - 2 * padding) / ext_x, height * (1 - 2 * padding) / ext_y)
    else:
        scale = fixed_scale
    # recentre on the projected bbox so the model sits in the middle
    shift = right * (x.max() + x.min()) / 2 + true_up * (y.max() + y.min()) / 2
    center = center + shift
    return Camera(right, true_up, forward, center, scale, width, height)


# --------------------------------------------------------------------------
# Rasterizer
# --------------------------------------------------------------------------

LIGHT_KEY = np.array([-0.4, 0.6, 0.7])   # camera space: x right, y up, z toward viewer
LIGHT_FILL = np.array([0.6, 0.2, 0.5])
BACKGROUND = (248, 249, 251)
CUT_COLOR = (0.93, 0.35, 0.35)


def _shade(colors: np.ndarray, normals_cam: np.ndarray) -> np.ndarray:
    """Per-triangle shaded RGB in 0..1 from base colours and camera-space normals."""
    n = normals_cam.copy()
    # two-sided: flip normals facing away from the viewer
    flip = n[:, 2] < 0
    n[flip] *= -1
    key = np.clip(n @ (LIGHT_KEY / np.linalg.norm(LIGHT_KEY)), 0, 1)
    fill = np.clip(n @ (LIGHT_FILL / np.linalg.norm(LIGHT_FILL)), 0, 1)
    intensity = 0.30 + 0.55 * key + 0.20 * fill
    shaded = colors * intensity[:, None]
    # faint specular on the key light
    shaded += (key[:, None] ** 24) * 0.12
    return np.clip(shaded, 0, 1)


def rasterize(meshes: Iterable[Mesh], cam: Camera, edge_color=(40, 44, 52),
              edge_alpha: float = 0.9, draw_edges: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Return (rgb uint8 HxWx3, zbuffer HxW)."""
    W, H = cam.width, cam.height
    zbuf = np.full(W * H, np.inf, dtype=np.float64)
    cbuf = np.empty((W * H, 3), dtype=np.float64)
    cbuf[:] = np.array(BACKGROUND) / 255.0

    meshes = list(meshes)
    tris: list[tuple] = []
    for mesh in meshes:
        if len(mesh.triangles) == 0:
            continue
        scr = cam.project(mesh.vertices)
        tri = mesh.triangles
        p0, p1, p2 = scr[tri[:, 0]], scr[tri[:, 1]], scr[tri[:, 2]]
        # world normals -> camera space
        w0, w1, w2 = mesh.vertices[tri[:, 0]], mesh.vertices[tri[:, 1]], mesh.vertices[tri[:, 2]]
        nw = np.cross(w1 - w0, w2 - w0)
        ln = np.linalg.norm(nw, axis=1)
        keep = ln > 1e-12
        nw = nw[keep] / ln[keep][:, None]
        p0, p1, p2 = p0[keep], p1[keep], p2[keep]
        ncam = np.column_stack([nw @ cam.right, nw @ cam.up, -(nw @ cam.forward)])
        base = np.tile(np.array(mesh.color), (len(p0), 1))
        if mesh.cut_faces is not None:
            cf = mesh.cut_faces[keep]
            base[cf] = CUT_COLOR
        tris.append((p0, p1, p2, _shade(base, ncam)))
    if tris:                     # every part in one pass: an assembly costs its triangles, not its part count
        _fill_triangles(*(np.concatenate(a) for a in zip(*tris)), zbuf, cbuf, W, H)

    rgb = (cbuf.reshape(H, W, 3) * 255).astype(np.uint8)

    if draw_edges:
        zb = zbuf.reshape(H, W)
        finite = zb[np.isfinite(zb)]
        depth_range = (finite.max() - finite.min()) if finite.size else 1.0
        bias = max(depth_range * 0.004, 1e-6)
        over = np.zeros((H, W), dtype=bool)
        lines = [pl for mesh in meshes for pl in mesh.edges if len(pl) >= 2]
        if lines:
            _draw_polylines(cam.project(np.concatenate(lines)), [len(pl) for pl in lines], zb, bias, over, W, H)
        ec = np.array(edge_color, dtype=float)
        rgb_f = rgb.astype(float)
        rgb_f[over] = rgb_f[over] * (1 - edge_alpha) + ec * edge_alpha
        rgb = rgb_f.astype(np.uint8)
    return rgb, zbuf.reshape(H, W)


def _fill_triangles(p0, p1, p2, colors, zbuf, cbuf, W, H, budget: int = 3_000_000) -> None:
    """Vectorised scanline conversion with a z-buffer. Each triangle's pixel span on every row it covers
    comes from its three edge functions (linear in x along a row), so a triangle costs about its own
    area, not its bounding box: a big face or a long sliver (fan triangulations of faces with holes)
    no longer pays for the empty pixels around it. Spans are widened by a pixel and every candidate is
    re-tested with the per-pixel edge functions, and triangles go in the same order as the bucketed
    rasterizer this replaced (power-of-two bbox width/height), so even z-ties resolve identically."""
    xs = np.stack([p0[:, 0], p1[:, 0], p2[:, 0]], axis=1)
    ys = np.stack([p0[:, 1], p1[:, 1], p2[:, 1]], axis=1)
    x0 = np.floor(xs.min(axis=1)).astype(int)
    x1 = np.ceil(xs.max(axis=1)).astype(int)
    y0 = np.floor(ys.min(axis=1)).astype(int)
    y1 = np.ceil(ys.max(axis=1)).astype(int)
    # cull fully off-screen
    on = (x1 >= 0) & (x0 < W) & (y1 >= 0) & (y0 < H)
    if not on.any():
        return
    idx_all = np.nonzero(on)[0]
    x0c = np.clip(x0[idx_all], 0, W - 1)
    y0c = np.clip(y0[idx_all], 0, H - 1)
    x1c = np.clip(x1[idx_all], 0, W - 1)
    y1c = np.clip(y1[idx_all], 0, H - 1)
    # area for barycentrics
    ax, ay = p0[:, 0], p0[:, 1]
    bx, by = p1[:, 0], p1[:, 1]
    cx, cy = p2[:, 0], p2[:, 1]
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    okA_all = np.abs(area) > 1e-12
    inv_area = np.where(okA_all, 1.0 / np.where(okA_all, area, 1.0), 0.0)

    bw = np.ceil(np.log2(np.maximum(x1c - x0c + 1, 1))).astype(int)
    bh = np.ceil(np.log2(np.maximum(y1c - y0c + 1, 1))).astype(int)
    key = bw * 64 + bh
    order = np.argsort(key, kind="stable")
    order = order[okA_all[idx_all[order]]]               # degenerate triangles draw nothing
    if not len(order):
        return
    t = idx_all[order]
    eps = -1e-6
    # one entry per (triangle, row): the row's pixel-centre y and the triangle's edge functions along it
    h = y1c[order] - y0c[order] + 1
    rt = np.repeat(t, h)
    ry = np.repeat(y0c[order], h) + (np.arange(int(h.sum())) - np.repeat(np.cumsum(h) - h, h))
    py = ry + 0.5
    ia = inv_area[rt]
    lo = np.repeat(x0c[order], h).astype(np.float64)
    hi = np.repeat(x1c[order], h).astype(np.float64)
    A0 = (by[rt] - cy[rt]) * ia
    A1 = (cy[rt] - ay[rt]) * ia
    B0 = (bx[rt] * (cy[rt] - py) - (by[rt] - py) * cx[rt]) * ia
    B1 = (cx[rt] * (ay[rt] - py) - (cy[rt] - py) * ax[rt]) * ia
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        for A, B in ((A0, B0), (A1, B1), (-(A0 + A1), 1.0 - B0 - B1)):     # w2 = 1 - w0 - w1
            # w = A * px + B >= eps along the row: px >= (eps - B) / A when A > 0, <= when A < 0
            edge = (eps - B) / A
            lo = np.where(A > 0, np.maximum(lo, np.floor(edge - 0.5) - 1), lo)
            hi = np.where(A < 0, np.minimum(hi, np.ceil(edge - 0.5) + 1), hi)
    # into the triangle's own columns; fmax/fmin also turn an overflowed (inf) or NaN bound into a finite one
    x0r, x1r = np.repeat(x0c[order], h), np.repeat(x1c[order], h)
    lo = np.fmin(np.fmax(lo, x0r), x1r + 1).astype(np.int64)
    hi = np.fmax(np.fmin(hi, x1r), x0r - 1).astype(np.int64)
    n = np.maximum(hi - lo + 1, 0)
    # candidate pixels in the old order (triangle, then row, then x), in chunks of at most `budget`
    csum = np.cumsum(n)
    first = 0
    while first < len(n):
        last = int(np.searchsorted(csum, (csum[first - 1] if first else 0) + budget, side="right"))
        last = max(last, first + 1)
        nr = n[first:last]
        if nr.sum():
            fr = np.repeat(np.arange(first, last), nr)
            fx = np.repeat(lo[first:last], nr) + (np.arange(int(nr.sum())) - np.repeat(np.cumsum(nr) - nr, nr))
            tt = rt[fr]
            pxf = fx + 0.5
            pyf = py[fr]
            iaf = inv_area[tt]
            w0 = ((bx[tt] - pxf) * (cy[tt] - pyf) - (by[tt] - pyf) * (cx[tt] - pxf)) * iaf
            w1 = ((cx[tt] - pxf) * (ay[tt] - pyf) - (cy[tt] - pyf) * (ax[tt] - pxf)) * iaf
            w2 = 1.0 - w0 - w1
            inside = (w0 >= eps) & (w1 >= eps) & (w2 >= eps)
            if inside.any():
                w0, w1, w2, tt = w0[inside], w1[inside], w2[inside], tt[inside]
                z_f = w0 * p0[tt, 2] + w1 * p1[tt, 2] + w2 * p2[tt, 2]
                pix_f = ry[fr][inside].astype(np.int64) * W + fx[inside]
                np.minimum.at(zbuf, pix_f, z_f)
                win = z_f <= zbuf[pix_f]
                cbuf[pix_f[win]] = colors[tt[win]]
        first = last


def _draw_polylines(scr: np.ndarray, lengths: list[int], zb: np.ndarray, bias: float, over: np.ndarray, W: int, H: int) -> None:
    """Depth-tested feature edges: many polylines (concatenated screen points, `lengths` each) in one pass."""
    ends = np.cumsum(lengths) - 1                       # each polyline's last point
    starts = np.ones(len(scr), dtype=bool)
    starts[ends] = False                                # a segment starts at every point but a polyline's last
    si = np.flatnonzero(starts)
    a = scr[si]
    b = scr[si + 1]
    seg_len = np.hypot(b[:, 0] - a[:, 0], b[:, 1] - a[:, 1])
    n = np.maximum(np.ceil(seg_len / 0.6).astype(int), 1)
    total = int(n.sum())
    if total == 0:
        return
    seg = np.repeat(np.arange(len(a)), n)
    start = np.repeat(np.cumsum(n) - n, n)
    frac = (np.arange(total) - start) / n[seg]
    pts = a[seg] + (b[seg] - a[seg]) * frac[:, None]
    # include each polyline's final endpoint
    pts = np.vstack([pts, scr[ends]])
    px = np.round(pts[:, 0]).astype(int)
    py = np.round(pts[:, 1]).astype(int)
    ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
    px, py, z = px[ok], py[ok], pts[ok, 2]
    if len(px) == 0:
        return
    # visible if not behind the surface (with a bias so coplanar edges show)
    vis = z <= zb[py, px] + bias
    # also treat edges over background (silhouette gaps) as visible
    vis |= ~np.isfinite(zb[py, px])
    over[py[vis], px[vis]] = True


# --------------------------------------------------------------------------
# Composition helpers
# --------------------------------------------------------------------------

def _font(size: int) -> ImageFont.ImageFont:
    for name in ("Helvetica.ttc", "Arial.ttf", "DejaVuSans.ttf", "Menlo.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_triad(draw: ImageDraw.ImageDraw, cam: Camera, origin: tuple[int, int], length: int = 34) -> None:
    axes = {"X": ((1, 0, 0), (220, 60, 60)), "Y": ((0, 1, 0), (60, 160, 60)), "Z": ((0, 0, 1), (60, 90, 220))}
    f = _font(12)
    for label, (vec, col) in axes.items():
        v = np.array(vec, dtype=float)
        dx = float(v @ cam.right) * length
        dy = -float(v @ cam.up) * length
        if abs(dx) < 1 and abs(dy) < 1:
            continue  # pointing at the viewer
        end = (origin[0] + dx, origin[1] + dy)
        draw.line([origin, end], fill=col, width=2)
        draw.text((end[0] + 3, end[1] - 6), label, fill=col, font=f)


def _nice_scale_length(scale_px_per_mm: float, target_px: float = 80) -> float:
    mm = target_px / max(scale_px_per_mm, 1e-9)
    mag = 10 ** math.floor(math.log10(mm)) if mm > 0 else 1
    for m in (1, 2, 5, 10):
        if m * mag >= mm:
            return m * mag
    return 10 * mag


def render_view(meshes: list[Mesh], view: str | Sequence[float] = "iso", width: int = 640, height: int = 480,
                label: str | None = None, up: Sequence[float] | None = None,
                draw_edges: bool = True, padding: float = 0.1) -> Image.Image:
    """Render one labelled view to a PIL image."""
    if isinstance(view, str):
        direction, up_hint = VIEW_DIRS[view]
        name = view
    else:
        direction, up_hint = tuple(view), (0, 0, 1)
        name = "custom"
    if up is not None:
        up_hint = tuple(up)
    pts = np.vstack([m.vertices for m in meshes]) if meshes else np.zeros((1, 3))
    cam = make_camera(direction, up_hint, pts, width, height, padding)
    rgb, _ = rasterize(meshes, cam, draw_edges=draw_edges)
    img = Image.fromarray(rgb, "RGB")
    draw = ImageDraw.Draw(img)
    title = label if label is not None else name
    f = _font(15)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(200, 204, 210))
    draw.text((8, 6), title, fill=(30, 30, 30), font=f)
    _draw_triad(draw, cam, (width - 56, height - 30))
    # scale bar
    L = _nice_scale_length(cam.scale)
    px = L * cam.scale
    x0, y0 = 10, height - 14
    draw.line([(x0, y0), (x0 + px, y0)], fill=(60, 60, 60), width=2)
    draw.line([(x0, y0 - 4), (x0, y0 + 4)], fill=(60, 60, 60), width=2)
    draw.line([(x0 + px, y0 - 4), (x0 + px, y0 + 4)], fill=(60, 60, 60), width=2)
    draw.text((x0 + px + 6, y0 - 9), f"{L:g} mm", fill=(60, 60, 60), font=_font(12))
    return img


def contact_sheet(parts: Parts, path: str, views: Sequence[str] = ("iso", "front", "top", "right"),
                  cell: tuple[int, int] = (640, 480), quality: float = 1.0, title: str | None = None,
                  legend: bool = True) -> str:
    """Write a labelled multi-view sheet (2 columns). Returns the path."""
    if len(parts) == 0:
        sheet = Image.new("RGB", (640, 160), (255, 255, 255))
        ImageDraw.Draw(sheet).text((10, 8), (title + "   " if title else "") + "blank design: no geometry yet (sketch in the viewer or describe a part)", fill=(20, 20, 20), font=_font(15))
        sheet.save(path)
        return path
    meshes = tessellate_parts(parts, quality=quality)
    bb = parts.bbox
    cols = 2 if len(views) > 1 else 1
    rows = math.ceil(len(views) / cols)
    cw, ch = cell
    header = 34
    sheet = Image.new("RGB", (cw * cols, ch * rows + header), (255, 255, 255))
    for i, v in enumerate(views):
        img = render_view(meshes, v, cw, ch)
        sheet.paste(img, ((i % cols) * cw, header + (i // cols) * ch))
    draw = ImageDraw.Draw(sheet)
    size = bb.size
    text = (title + "   " if title else "") + \
        f"bbox {size.X:.2f} x {size.Y:.2f} x {size.Z:.2f} mm   " \
        f"(X {bb.min.X:.1f}..{bb.max.X:.1f}, Y {bb.min.Y:.1f}..{bb.max.Y:.1f}, Z {bb.min.Z:.1f}..{bb.max.Z:.1f})"
    draw.text((10, 8), text, fill=(20, 20, 20), font=_font(15))
    if legend and len(parts) > 1:
        x = cw * cols - 10
        f = _font(13)
        for p in reversed(list(parts)):
            w = draw.textlength(p.name, font=f)
            x -= w + 26
            draw.rectangle([x, 10, x + 14, 24], fill=p.color, outline=(80, 80, 80))
            draw.text((x + 18, 8), p.name, fill=(30, 30, 30), font=f)
    sheet.save(path)
    return path


def section_parts(parts: Parts, plane: str = "XZ", offset: float = 0.0, keep: str = "+") -> Parts:
    """Cut every part with an axis-aligned plane; return the kept half as new Parts.

    plane: "XY" (normal Z), "XZ" (normal Y) or "YZ" (normal X). offset moves the
    plane along its normal. keep "+" keeps the side the normal points to.
    """
    normal = {"XY": bd.Vector(0, 0, 1), "XZ": bd.Vector(0, 1, 0), "YZ": bd.Vector(1, 0, 0)}[plane.upper()]
    bb = parts.bbox
    big = bb.diagonal * 4 + 10
    # a half-space box on the side we remove
    sign = -1 if keep == "+" else 1
    centre = normal * (offset + sign * big / 2)
    cutter = bd.Pos(centre.X, centre.Y, centre.Z) * bd.Box(big, big, big)
    from .model import Part
    out = []
    for p in parts:
        try:
            cut = p.shape - cutter
        except Exception:
            cut = p.shape
        if cut.volume > 1e-9 if hasattr(cut, "volume") else True:
            out.append(Part(p.name, cut, p.color))
    return Parts(out) if out else parts


def section_sheet(parts: Parts, path: str, plane: str = "XZ", offset: float = 0.0,
                  cell: tuple[int, int] = (720, 540), quality: float = 1.0) -> str:
    """A two-view sheet of a sectioned model: straight onto the cut, plus an iso of the kept half."""
    cut_parts = section_parts(parts, plane, offset, keep="+")
    normal = {"XY": (0, 0, 1), "XZ": (0, 1, 0), "YZ": (1, 0, 0)}[plane.upper()]
    meshes = tessellate_parts(cut_parts, quality=quality)
    # mark triangles on the cut plane
    n = np.array(normal, dtype=float)
    for m in meshes:
        tri = m.triangles
        centroids = m.vertices[tri].mean(axis=1)
        d = np.abs(centroids @ n - offset)
        v0, v1, v2 = m.vertices[tri[:, 0]], m.vertices[tri[:, 1]], m.vertices[tri[:, 2]]
        tn = np.cross(v1 - v0, v2 - v0)
        ln = np.linalg.norm(tn, axis=1) + 1e-12
        coplanar = (np.abs((tn / ln[:, None]) @ n) > 0.999) & (d < 1e-3 * max(parts.bbox.diagonal, 1))
        m.cut_faces = coplanar
    look_from = tuple(-x for x in normal)   # look at the cut face from the removed side
    up = (0, 0, 1) if plane.upper() != "XY" else (0, 1, 0)
    cw, ch = cell
    sheet = Image.new("RGB", (cw * 2, ch + 34), (255, 255, 255))
    img1 = render_view(meshes, look_from, cw, ch, label=f"section {plane.upper()} @ {offset:g} (cut faces red)", up=up)
    iso_dir = np.array(VIEW_DIRS["iso"][0], dtype=float)
    if np.dot(iso_dir, np.array(look_from)) < 0:
        iso_dir = iso_dir - 2 * n * (iso_dir @ n)   # mirror so we see the cut side
    img2 = render_view(meshes, tuple(iso_dir), cw, ch, label="section iso")
    sheet.paste(img1, (0, 34))
    sheet.paste(img2, (cw, 34))
    draw = ImageDraw.Draw(sheet)
    size = parts.size
    draw.text((10, 8), f"section view   bbox {size.X:.2f} x {size.Y:.2f} x {size.Z:.2f} mm", fill=(20, 20, 20), font=_font(15))
    sheet.save(path)
    return path


def exploded_parts(parts: Parts, factor: float = 0.6) -> Parts:
    """Move each part away from the assembly centre for an exploded view."""
    from .model import Part
    centre = parts.bbox.center()
    diag = parts.bbox.diagonal
    out = []
    for p in parts:
        c = p.shape.bounding_box().center()
        d = c - centre
        if d.length < 1e-9:
            out.append(p)
            continue
        shift = d.normalized() * (factor * diag * 0.5) + d * factor
        out.append(Part(p.name, p.shape.moved(bd.Location((shift.X, shift.Y, shift.Z))), p.color))
    return Parts(out)


def turntable(parts: Parts, path: str, frames: int = 36, size: tuple[int, int] = (480, 360), elevation: float = 0.6,
              quality: float = 1.0, duration_ms: int = 70) -> str:
    """An animated GIF of the model turning once about Z (viewed from `elevation` above the horizon)."""
    meshes = tessellate_parts(parts, quality=quality)
    pts = np.vstack([m.vertices for m in meshes]) if meshes else np.zeros((1, 3))
    w, h = size
    imgs = []
    # one scale for every frame: the camera that fits the bounding sphere
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    centre = (lo + hi) / 2
    radius = float(np.linalg.norm(hi - lo)) / 2 or 1.0
    scale = min(w, h) * 0.42 / radius
    for k in range(frames):
        ang = 2 * math.pi * k / frames
        direction = (math.cos(ang), math.sin(ang), elevation)
        cam = make_camera(direction, (0, 0, 1), pts, w, h, fixed_scale=scale, center=centre)
        cam.center = centre
        rgb, _ = rasterize(meshes, cam, draw_edges=True)
        imgs.append(Image.fromarray(rgb, "RGB").quantize(colors=128, method=Image.Quantize.MEDIANCUT))
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=duration_ms, loop=0, optimize=False)
    return path
