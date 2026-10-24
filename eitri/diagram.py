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


def text_w(s: str, size: float = FONT) -> float:
    return len(s) * size * 0.58


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
        return f'<text{cls} x="{it["x"]:.1f}" y="{it["y"]:.1f}" font-size="{it["size"]}" fill="{it["fill"]}"{anchor}{weight}>{escape(str(it["s"]))}</text>'
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
