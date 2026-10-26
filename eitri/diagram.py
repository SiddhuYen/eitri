"""2-D diagrams drawn by our own layout code: a Scene of plain items (rect, text, line, circle, polyline, bezier path), written
as SVG for the browser editor (every element carries data-kind / data-id so the page can select and highlight it) and rasterised
to PNG with Pillow for the agent's review image. No Graphviz, no browser, no new dependencies.

Every domain's diagram (the harness wiring diagram, a kinematic sketch, a pneumatic schematic, a pinout) builds a Scene with its
own layout code and hands it here. Items are dicts: {"t": "rect", x, y, w, h, rx, fill, stroke, sw}, {"t": "text", x, y, s, size,
fill, weight?, anchor?}, {"t": "line", x1, y1, x2, y2, stroke, sw, dash?}, {"t": "circle", cx, cy, r, fill, stroke, sw},
{"t": "polyline", pts, stroke, sw, fill?, closed?, dash?}, {"t": "path", pts (4 bezier control points), stroke, sw, dash?, alpha?};
"g": "<kind>:<id>" groups consecutive items into an SVG <g data-kind data-id>, "cls" is the element's class.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

FONT = 11
INK = "#1b2430"
MUTED = "#6b7686"
BOX = "#fbfcfd"
HEADER = "#e4e9ef"
BORDER = "#3a4656"
FAIL = "#d92d20"
WARN = "#f08c00"


_GLYPH_W: dict[str, float] = {}         # per character, at size 1: the wider of IBM Plex Sans (the SVG in the browser) and Pillow's
_FONTS: list = []                        # built-in font (the PNG review image), so a box sized with text_w holds the text in both


def _glyph_fonts() -> list:
    if not _FONTS:
        from pathlib import Path
        from PIL import ImageFont
        fonts = []
        plex = Path(__file__).parent / "web" / "assets" / "design" / "fonts" / "ibm-plex-sans-600.woff2"
        try:
            fonts.append(ImageFont.truetype(str(plex), 100))
        except (OSError, ValueError):
            pass
        try:
            fonts.append(ImageFont.load_default(size=100))
        except TypeError:                                   # Pillow < 10.1
            pass
        _FONTS.append(fonts)
    return _FONTS[0]


def text_w(s: str, size: float = FONT) -> float:
    """The width of a text at a font size, measured (bold-safe: Plex SemiBold), falling back to 0.62 em a character."""
    fonts = _glyph_fonts()
    if not fonts:
        return len(s) * size * 0.62
    w = 0.0
    for ch in str(s):
        g = _GLYPH_W.get(ch)
        if g is None:
            g = max(f.getlength(ch) for f in fonts) / 100.0
            _GLYPH_W[ch] = g
        w += g
    return w * size


@dataclass
class Scene:
    width: float
    height: float
    items: list[dict] = field(default_factory=list)
    nodes: dict[str, dict] = field(default_factory=dict)       # name -> {kind, x, y, w, h}
    pins: dict[str, dict] = field(default_factory=dict)       # "CONN.PIN" -> {y, xl, xr}
    wires: dict[str, dict] = field(default_factory=dict)      # id -> {pts, label_at}
    footer_top: float = 0.0


# --------------------------------------------------------------------------- layout



def _wrap(text: str, width: int) -> list[str]:
    width = max(width, 40)
    out, line = [], ""
    for word in text.split(" "):
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return (out + [line])[:4] if line else out[:4]



def _overlap(a, b, pad: float = 2.0) -> bool:
    return a[0] < b[0] + b[2] + pad and b[0] < a[0] + a[2] + pad and a[1] < b[1] + b[3] + pad and b[1] < a[1] + a[3] + pad


def _bez(pts, t: float) -> tuple[float, float]:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
    u = 1 - t
    return (u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
            u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3)



def status_from(report) -> dict[str, str]:
    """{ref: "fail" | "warn"} from a HarnessReport: what the non-passing checks are about (failures win)."""
    out: dict[str, str] = {}
    for c in report.checks:
        if c.severity == "pass":
            continue
        for r in c.refs:
            if out.get(r) != "fail":
                out[r] = c.severity
    return out


def footer_from(report, limit: int = 14) -> list[tuple[str, str]]:
    n_fail, n_warn = len(report.failed), len(report.warnings)
    lines = [("head", f"Checks: {len(report.checks) - n_fail - n_warn} pass, {n_warn} warn, {n_fail} FAIL")]
    for c in report.failed + report.warnings:
        lines.append((c.severity, f"{'FAIL' if c.severity == 'fail' else 'WARN'}  {c.name}: {c.measured}"))
    if len(lines) > limit:
        lines = lines[:limit] + [("head", f"... and {len(lines) - limit} more (see the report)")]
    if n_fail == 0 and n_warn == 0:
        lines.append(("pass", "every check passes"))
    return lines


# --------------------------------------------------------------------------- SVG


def _bezier_d(pts) -> str:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
    return f"M{x0:.1f},{y0:.1f} C{x1:.1f},{y1:.1f} {x2:.1f},{y2:.1f} {x3:.1f},{y3:.1f}"


def scene_to_svg(scene: Scene, standalone: bool = True) -> str:
    out = []
    if standalone:
        out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{scene.width:.0f}" height="{scene.height:.0f}" '
                   f'viewBox="0 0 {scene.width:.0f} {scene.height:.0f}" font-family="IBM Plex Sans, Helvetica, Arial, sans-serif" class="harness">')
        out.append(f'<rect width="100%" height="100%" fill="#ffffff"/>')
    group = None

    def close():
        if group is not None:
            out.append("</g>")

    for it in scene.items:
        g = it.get("g", "")
        if g != group:
            close()
            kind, _, ident = g.partition(":")
            if ident:
                out.append(f'<g class="{kind}" data-kind="{kind}" data-id="{escape(ident)}">')
            else:
                out.append(f'<g class="{escape(g)}">')
            group = g
        out.append(_svg_item(it))
    close()
    if standalone:
        out.append("</svg>")
    return "\n".join(out)


def _svg_item(it: dict) -> str:
    cls = f' class="{it["cls"]}"' if it.get("cls") else ""
    t = it["t"]
    if t == "rect":
        fill = it["fill"]
        op = f' fill-opacity="{it["alpha_fill"]}"' if "alpha_fill" in it else ""
        pin = f' data-pin="{escape(str(it["pin"]), {chr(34): "&quot;"})}"' if "pin" in it else ""
        return (f'<rect{cls}{pin} x="{it["x"]:.1f}" y="{it["y"]:.1f}" width="{it["w"]:.1f}" height="{it["h"]:.1f}" rx="{it.get("rx", 0)}" '
                f'fill="{fill}"{op} stroke="{it["stroke"]}" stroke-width="{it["sw"]}"/>')
    if t == "text":
        anchor = f' text-anchor="{it["anchor"]}"' if it.get("anchor") else ""
        weight = f' font-weight="{it["weight"]}"' if it.get("weight") else ""
        halo = ""
        if it.get("bg"):
            x, y, w, h = text_box(it, pad=2)
            halo = f'<rect class="halo" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="2" fill="{it["bg"]}" fill-opacity="0.9" stroke="none"/>'
        return halo + f'<text{cls} x="{it["x"]:.1f}" y="{it["y"]:.1f}" font-size="{it["size"]}" fill="{it["fill"]}"{anchor}{weight}>{escape(str(it["s"]))}</text>'
    if t == "line":
        dash = f' stroke-dasharray="{it["dash"]}"' if it.get("dash") else ""
        return f'<line{cls} x1="{it["x1"]:.1f}" y1="{it["y1"]:.1f}" x2="{it["x2"]:.1f}" y2="{it["y2"]:.1f}" stroke="{it["stroke"]}" stroke-width="{it["sw"]}"{dash}/>'
    if t == "circle":
        return f'<circle{cls} cx="{it["cx"]:.1f}" cy="{it["cy"]:.1f}" r="{it["r"]}" fill="{it["fill"]}" stroke="{it["stroke"]}" stroke-width="{it["sw"]}"/>'
    if t == "polyline":
        dash = f' stroke-dasharray="{it["dash"]}"' if it.get("dash") else ""
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in it["pts"])
        tag = "polygon" if it.get("closed") else "polyline"
        return f'<{tag}{cls} points="{pts}" fill="{it.get("fill", "none")}" stroke="{it["stroke"]}" stroke-width="{it["sw"]}"{dash} stroke-linejoin="round" stroke-linecap="round"/>'
    if t == "path":
        dash = f' stroke-dasharray="{it["dash"]}"' if it.get("dash") else ""
        alpha = f' stroke-opacity="{it["alpha"]}"' if "alpha" in it else ""
        return (f'<path{cls} d="{_bezier_d(it["pts"])}" fill="none" stroke="{it["stroke"]}" stroke-width="{it["sw"]}"{alpha}{dash} '
                f'stroke-linecap="round"/>')
    return ""


# --------------------------------------------------------------------------- PNG (Pillow)

SS = 3          # drawn at 3x, shown at 1.5x: smooth edges without anti-aliased primitives
OUT = 1.5


def _font(size: float):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                                  # Pillow < 10.1: only a bitmap font
        return ImageFont.load_default()


def _flatten(pts, n: int = 28) -> list[tuple[float, float]]:
    return [_bez(pts, i / n) for i in range(n + 1)]


def _hex_rgb(h: str, alpha: float = 1.0, bg=(255, 255, 255)):
    if not h or h == "none":
        return None
    h = h.lstrip("#")
    c = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(round(c[i] * alpha + bg[i] * (1 - alpha)) for i in range(3))


def scene_to_png(scene: Scene, path) -> str:
    from PIL import Image, ImageDraw
    W, H = int(scene.width * SS), int(scene.height * SS)
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    fonts: dict = {}

    def font(size, bold=False):
        k = (size, bold)
        if k not in fonts:
            fonts[k] = _font(size * SS)
        return fonts[k]

    for it in scene.items:
        t = it["t"]
        if t == "rect":
            if it.get("alpha_fill", 1.0) == 0.0:
                continue
            box = [it["x"] * SS, it["y"] * SS, (it["x"] + it["w"]) * SS, (it["y"] + it["h"]) * SS]
            fill = _hex_rgb(it["fill"], it.get("alpha_fill", 1.0))
            outline = _hex_rgb(it["stroke"])
            if it.get("rx", 0) > 0:
                d.rounded_rectangle(box, radius=it["rx"] * SS, fill=fill, outline=outline, width=max(1, round(it["sw"] * SS)))
            else:
                d.rectangle(box, fill=fill, outline=outline, width=max(1, round(it["sw"] * SS)))
        elif t == "text":
            if it.get("bg"):
                x, y, w, h = text_box(it, pad=2)
                d.rounded_rectangle([x * SS, y * SS, (x + w) * SS, (y + h) * SS], radius=2 * SS, fill=_hex_rgb(it["bg"], 0.9))
            f = font(it["size"], it.get("weight") == "700")
            anchor = {"middle": "ms", "end": "rs"}.get(it.get("anchor"), "ls")
            d.text((it["x"] * SS, it["y"] * SS), str(it["s"]), font=f, fill=_hex_rgb(it["fill"]) or (0, 0, 0), anchor=anchor,
                   stroke_width=1 if it.get("weight") == "700" and SS >= 3 else 0)
        elif t == "line":
            if _hex_rgb(it["stroke"]):
                _line(d, [(it["x1"] * SS, it["y1"] * SS), (it["x2"] * SS, it["y2"] * SS)], _hex_rgb(it["stroke"]), it["sw"] * SS, it.get("dash"))
        elif t == "circle":
            r = it["r"] * SS
            box = [it["cx"] * SS - r, it["cy"] * SS - r, it["cx"] * SS + r, it["cy"] * SS + r]
            if it.get("dash") and _hex_rgb(it["stroke"]):                     # a dashed circle: short arcs
                for k in range(0, 360, 20):
                    d.arc(box, k, k + 10, fill=_hex_rgb(it["stroke"]), width=max(1, round(it["sw"] * SS)))
                if _hex_rgb(it["fill"]):
                    d.ellipse(box, fill=_hex_rgb(it["fill"]))
            else:
                d.ellipse(box, fill=_hex_rgb(it["fill"]), outline=_hex_rgb(it["stroke"]), width=max(1, round(it["sw"] * SS)))
        elif t == "polyline":
            pts = [(x * SS, y * SS) for x, y in it["pts"]]
            if it.get("fill") and it["fill"] != "none":
                d.polygon(pts, fill=_hex_rgb(it["fill"]))
            if it.get("closed") and pts:
                pts = pts + [pts[0]]
            if it["sw"] > 0 and it["stroke"] != "none":
                _line(d, pts, _hex_rgb(it["stroke"]), it["sw"] * SS, it.get("dash"))
        elif t == "path":
            if it.get("alpha", 1.0) == 0.0:
                continue
            col = _hex_rgb(it["stroke"], it.get("alpha", 1.0))
            if col:
                _line(d, [(x * SS, y * SS) for x, y in _flatten(it["pts"])], col, it["sw"] * SS, it.get("dash"))
    img = img.resize((round(scene.width * OUT), round(scene.height * OUT)), Image.LANCZOS)
    img.save(str(path), "PNG")
    return str(path)


def _line(d, pts, color, width, dash=None) -> None:
    width = max(1, round(width))
    if not dash:
        d.line(pts, fill=color, width=width, joint="curve")
        r = width / 2
        for x, y in (pts[0], pts[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)
        return
    on, off = (float(v) * SS for v in dash.split())
    # walk the polyline, drawing the "on" stretches only
    pos, drawing, left = 0.0, True, on
    cur = pts[0]
    for nxt in pts[1:]:
        seg = math.dist(cur, nxt)
        if seg == 0:
            continue
        done = 0.0
        while done < seg:
            step = min(left, seg - done)
            a = (cur[0] + (nxt[0] - cur[0]) * done / seg, cur[1] + (nxt[1] - cur[1]) * done / seg)
            b = (cur[0] + (nxt[0] - cur[0]) * (done + step) / seg, cur[1] + (nxt[1] - cur[1]) * (done + step) / seg)
            if drawing:
                d.line([a, b], fill=color, width=width)
            done += step
            left -= step
            if left <= 1e-9:
                drawing = not drawing
                left = on if drawing else off
        cur = nxt


# --------------------------------------------------------------------------- labels: geometry, collisions, placement
#
# Every text item is checked. A text may sit inside a box (a connector's pin rows, a view's frame): that is content. A text that
# crosses a line (of any group, its own included: a wire's label never sits on its wire), another text, or the edge of a box of
# another group collides. A text that carries `cands` (a list of (x, y, anchor) positions, best first) is movable: place_labels()
# moves it to the first candidate that collides with nothing.

CONTAINERS = {"frame", "view", "paper", "bg", "card", "halo", "board"}       # rect classes that hold content: only their edges are obstacles


def text_box(it: dict, pad: float = 0.0) -> tuple[float, float, float, float]:
    """(x, y, w, h) of a text item: its anchor, the measured width, the font's ascent and descent."""
    size = float(it.get("size", FONT))
    w = text_w(str(it["s"]), size)
    a = it.get("anchor")
    x = it["x"] - w / 2 if a == "middle" else it["x"] - w if a == "end" else it["x"]
    y = it["y"] - size * 0.78
    return x - pad, y - pad, w + 2 * pad, size * 1.0 + 2 * pad


def _segments(it: dict) -> list[tuple[float, float, float, float]]:
    t = it["t"]
    if t == "line":
        return [(it["x1"], it["y1"], it["x2"], it["y2"])]
    if t in ("polyline", "path"):
        pts = list(it["pts"]) if t == "polyline" else _flatten(it["pts"], 16)
        if t == "polyline" and it.get("closed") and pts:
            pts = pts + [pts[0]]
        return [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])]
    return []


def _seg_hits_box(seg, box) -> bool:
    """Liang-Barsky: does the segment cross the box (x, y, w, h)?"""
    x1, y1, x2, y2 = seg
    bx, by, bw, bh = box
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - bx), (dx, bx + bw - x1), (-dy, y1 - by), (dy, by + bh - y1)):
        if p == 0:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                return False
    return True


def _segs_cross(a, b) -> bool:
    """Do two segments (x1, y1, x2, y2) intersect?"""
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    p1, p2, p3, p4 = (a[0], a[1]), (a[2], a[3]), (b[0], b[1]), (b[2], b[3])
    o1, o2, o3, o4 = orient(p1, p2, p3), orient(p1, p2, p4), orient(p3, p4, p1), orient(p3, p4, p2)
    return o1 != o2 and o3 != o4


def _inside(inner, outer) -> bool:
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[0] + inner[2] <= outer[0] + outer[2] and inner[1] + inner[3] <= outer[1] + outer[3]


def _visible(it: dict) -> bool:
    if it.get("cls") == "hit" or it.get("alpha", 1.0) == 0.0:
        return False
    if it["t"] == "rect":
        return not (it.get("alpha_fill", 1.0) == 0.0 and (it.get("stroke") in (None, "none") or it.get("sw", 1) == 0))
    if it["t"] in ("line", "polyline", "path"):
        return it.get("stroke") not in (None, "none") and it.get("sw", 1) > 0
    return True


def _hits(tb: tuple, text: dict, other: dict) -> bool:
    """Does the text with box tb collide with another scene item?"""
    if other is text or not _visible(other):
        return False
    t = other["t"]
    if t == "text":
        return _overlap(tb, text_box(other), pad=0.5)
    if t in ("line", "polyline", "path"):
        half = float(other.get("sw", 1)) / 2                     # the stroke's own width counts
        tbw = (tb[0] - half, tb[1] - half, tb[2] + 2 * half, tb[3] + 2 * half)
        bb = _bbox(other)
        if bb and not _overlap(tbw, bb, pad=0):
            return False
        return any(_seg_hits_box(sg, tbw) for sg in _segments(other))
    if t in ("rect", "circle"):
        box = (other["x"], other["y"], other["w"], other["h"]) if t == "rect" else \
              (other["cx"] - other["r"], other["cy"] - other["r"], 2 * other["r"], 2 * other["r"])
        if not _overlap(tb, box, pad=0):
            return False
        own = other.get("g") is not None and other.get("g") == text.get("g")
        if _inside(tb, box):                                      # content: in its own box, or in a container (a view, a frame)
            return not own and other.get("cls") not in CONTAINERS
        return other.get("cls") != "halo"                         # crosses a box's edge (its own symbol's too)
    return False


def _bbox(it: dict):
    sg = _segments(it)
    if not sg:
        return None
    xs = [v for s in sg for v in (s[0], s[2])]
    ys = [v for s in sg for v in (s[1], s[3])]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def collisions(scene: Scene, only_text: bool = True) -> list[tuple[dict, dict]]:
    """Every (text, item) pair that collides: what a readable diagram has none of. Footer text is not a diagram label."""
    texts = [it for it in scene.items if it["t"] == "text" and it.get("g") != "footer"]
    order = {id(t): i for i, t in enumerate(texts)}
    others = [it for it in scene.items if it.get("g") != "footer"]
    out = []
    for i, t in enumerate(texts):
        tb = text_box(t)
        for o in others:
            if o["t"] == "text" and order.get(id(o), -1) <= i:     # each pair of texts once
                continue
            if _hits(tb, t, o):
                out.append((t, o))
    return out


def tether_crossings(scene: Scene) -> list[tuple[dict, dict]]:
    """Placed labels whose tether (the short segment from the label to its own line) crosses another group's line: a label that
    sits next to a different wire than its own."""
    out = []
    lines = [o for o in scene.items if o["t"] in ("line", "polyline", "path") and _visible(o) and o.get("g") != "footer"]
    for it in scene.items:
        if it["t"] != "text" or not it.get("cands") or "cand_index" not in it:
            continue
        c = it["cands"][it["cand_index"]]
        if len(c) > 4 and c[4]:
            for o in lines:
                if o.get("g") != it.get("g") and any(_segs_cross(c[4], sg) for sg in _segments(o)):
                    out.append((it, o))
    return out


def place_labels(scene: Scene, margin: float = 4.0) -> list[dict]:
    """Move every text that carries `cands` (and its `follow` texts) to its first candidate position that collides with nothing:
    the fixed items and the labels placed before it. A label left without a free position gets a repair round: each label it
    overlaps is re-placed first, and it tries again. Returns the labels that still found no free position (each left at its
    least-colliding candidate)."""
    followers = {id(f) for it in scene.items for f in it.get("follow", ())}
    movable = [x for x in scene.items if x["t"] == "text" and x.get("cands")]
    fixed = [it for it in scene.items if not (it["t"] == "text" and (it.get("cands") or id(it) in followers)) and it.get("g") != "footer"]
    top = scene.footer_top if getattr(scene, "footer_top", None) else scene.height

    def members(it):
        return [it] + list(it.get("follow", ()))

    def score(it, c, others):
        x, y = c[0], c[1]
        anchor = c[2] if len(c) > 2 else it.get("anchor")
        unit = [dict(it, x=x, y=y, anchor=anchor)] + [dict(f, x=x + f["dx"], y=y + f["dy"], anchor=anchor) for f in it.get("follow", ())]
        n = 0
        for k, trial in enumerate(unit):
            tb = text_box(trial, pad=1.0)
            if tb[0] < margin or tb[1] < margin or tb[0] + tb[2] > scene.width - margin or tb[1] + tb[3] > top - margin:
                n += 10_000
                continue
            me = members(it)[k]
            n += sum(1 for o in fixed if o is not me and _hits(tb, trial, o)) + sum(1 for o in others if _hits(tb, trial, o))
        tether = c[4] if len(c) > 4 else None                    # (x1, y1, x2, y2): label edge -> its own line; no other line between
        if tether:
            for o in fixed:
                if o.get("g") != it.get("g") and o["t"] in ("line", "polyline", "path") and _visible(o) and \
                        any(_segs_cross(tether, sg) for sg in _segments(o)):
                    n += 1
        return n, unit

    def put(it, others) -> int:
        best, best_n, best_i = None, None, 0
        for i, c in enumerate(it["cands"]):
            n, unit = score(it, c, others)
            if best_n is None or n < best_n:
                best, best_n, best_i = unit, n, i
            if n == 0:
                break
        for member, trial in zip(members(it), best):
            member["x"], member["y"], member["anchor"] = trial["x"], trial["y"], trial.get("anchor")
        it["cand_index"] = best_i
        return best_n

    placed: list[dict] = []
    stuck = []
    for it in movable:
        if put(it, placed):
            stuck.append(it)
        placed += members(it)
    for _round in range(2):                                     # repair: let a stuck label move a neighbour out of its way
        still = []
        for it in stuck:
            mine = members(it)
            others = [o for o in placed if not any(o is m for m in mine)]
            if not put(it, others):
                continue
            blockers = [o for o in movable if o is not it and any(_hits(text_box(m), m, b) for m in mine for b in members(o))]
            fixed_now = False
            for b in blockers:
                saved = [(m, m["x"], m["y"], m.get("anchor"), b.get("cand_index")) for m in members(b)]
                rest = [o for o in placed if not any(o is m for m in members(b)) and not any(o is m for m in mine)]
                # the stuck label first, then the blocker around it
                if put(it, rest) == 0 and put(b, rest + mine) == 0:
                    fixed_now = True
                    break
                for m, x, y, a, ci in saved:
                    m["x"], m["y"], m["anchor"] = x, y, a
                b["cand_index"] = saved[0][4]
                put(it, others)
            if not fixed_now:
                still.append(it)
        stuck = still
        if not stuck:
            break
    return stuck
