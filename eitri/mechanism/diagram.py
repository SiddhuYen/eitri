"""The kinematic diagram: the chain at its home pose in a side view (X right, Z up) and a plan view (X right, Y up), the
worst-load pose ghosted in the side view, joints marked by type, masses and targets plotted. Drawn with eitri.diagram."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..diagram import FAIL, FONT, INK, MUTED, WARN, Scene, footer_from, scene_to_png, scene_to_svg, status_from, text_w   # noqa: F401
from .model import Mechanism

MARGIN = 28
PANEL_W = 430
PANEL_H = 330
GAP = 36
LINK_W = 5.0
PASS = "#1a7f4b"
GHOST = "#9aa5b1"
LINK = "#2b3a4a"
TARGET_OK, TARGET_BAD = "#1a7f4b", "#d92d20"


@dataclass
class _View:
    name: str
    ax: tuple[int, int]            # which world axes map to (right, up)
    x0: float
    y0: float
    scale: float
    cx: float                      # world centre
    cy: float

    def to(self, p) -> tuple[float, float]:
        u, v = float(p[self.ax[0]]), float(p[self.ax[1]])
        return (self.x0 + PANEL_W / 2 + (u - self.cx) * self.scale, self.y0 + PANEL_H / 2 - (v - self.cy) * self.scale)


def _bounds(points: list[np.ndarray], ax: tuple[int, int]) -> tuple[float, float, float, float]:
    us = [float(p[ax[0]]) for p in points] or [0.0]
    vs = [float(p[ax[1]]) for p in points] or [0.0]
    return min(us), max(us), min(vs), max(vs)


def _view(name: str, ax: tuple[int, int], points: list[np.ndarray], x0: float, y0: float) -> _View:
    lo_u, hi_u, lo_v, hi_v = _bounds(points, ax)
    span_u, span_v = max(hi_u - lo_u, 1.0), max(hi_v - lo_v, 1.0)
    scale = min((PANEL_W - 70) / span_u, (PANEL_H - 70) / span_v)
    return _View(name, ax, x0, y0, scale, (lo_u + hi_u) / 2, (lo_v + hi_v) / 2)


def worst_pose(m: Mechanism, rep) -> dict[str, float] | None:
    """The pose of the most loaded joint (relative to its stall), from the report."""
    best = None
    for j in (rep.joint_info if rep else []):
        if j.required is None:
            continue
        score = j.required / j.stall if j.stall else j.required
        if best is None or score > best[0]:
            best = (score, j.worst_pose)
    return dict(best[1]) if best else None


def build_scene(m: Mechanism, rep=None, status: dict[str, str] | None = None, footer: list[tuple[str, str]] | None = None, title: str | None = None) -> Scene:
    status = status or {}
    try:
        order = m.order()
        home = m.points(m.home_pose())
    except Exception as exc:                                   # a broken structure: the report says why; draw what we can
        sc = Scene(560, 120)
        sc.items.append({"t": "text", "x": MARGIN, "y": 22, "s": title or m.name, "size": 14, "weight": "700", "fill": INK, "g": "title"})
        sc.items.append({"t": "text", "x": MARGIN, "y": 60, "s": f"no diagram: {exc}", "size": FONT, "fill": FAIL, "g": "title"})
        return sc
    wp = worst_pose(m, rep)
    ghost = m.points(wp) if wp and wp != m.home_pose() else None
    all_pts = list(home["joints"].values()) + list(home["ends"].values()) + [home["tip"]] + list(home["masses"].values()) + [t.at for t in m.targets] + [np.zeros(3)]
    if ghost:
        all_pts += list(ghost["joints"].values()) + list(ghost["ends"].values()) + [ghost["tip"]]
    top = MARGIN + 22
    side = _view("side view  (X right, Z up)", (0, 2), all_pts, MARGIN, top)
    plan = _view("plan view  (X right, Y up)", (0, 1), all_pts, MARGIN + PANEL_W + GAP, top)
    width = MARGIN * 2 + PANEL_W * 2 + GAP
    sc = Scene(width, top + PANEL_H + MARGIN)
    items = sc.items
    items.append({"t": "text", "x": MARGIN, "y": 22, "s": title or m.name, "size": 14, "weight": "700", "fill": INK, "g": "title"})
    for v in (side, plan):
        items.append({"t": "rect", "x": v.x0, "y": v.y0, "w": PANEL_W, "h": PANEL_H, "rx": 6, "fill": "#fbfcfd", "stroke": "#c5ccd6", "sw": 1, "g": "frame"})
        items.append({"t": "text", "x": v.x0 + 10, "y": v.y0 + 16, "s": v.name, "size": FONT - 1, "fill": MUTED, "g": "frame"})
        # scale bar: a round length
        bar = 10 ** np.floor(np.log10(max(PANEL_W / v.scale / 4, 1e-6)))
        for k in (5, 2, 1):
            if bar * k * v.scale <= PANEL_W / 3:
                bar *= k
                break
        bx, by = v.x0 + 10, v.y0 + PANEL_H - 12
        items.append({"t": "line", "x1": bx, "y1": by, "x2": bx + bar * v.scale, "y2": by, "stroke": MUTED, "sw": 2, "g": "frame"})
        items.append({"t": "text", "x": bx + bar * v.scale + 6, "y": by + 4, "s": f"{bar:g} mm", "size": FONT - 2, "fill": MUTED, "g": "frame"})
        # ground: a hatch at the base link's origin
        o = v.to(np.zeros(3))
        items.append({"t": "line", "x1": o[0] - 18, "y1": o[1], "x2": o[0] + 18, "y2": o[1], "stroke": INK, "sw": 1.5, "g": "frame"})
        for k in range(-3, 4):
            items.append({"t": "line", "x1": o[0] + k * 6, "y1": o[1], "x2": o[0] + k * 6 - 5, "y2": o[1] + 6, "stroke": INK, "sw": 1, "g": "frame"})

    def chain(v: _View, pts, color: str, width: float, group: bool, dash: str | None = None) -> None:
        for j in order:
            start = pts["joints"][j.name]
            end = pts["ends"][j.child]
            link = m.links[j.child]
            if link.length_mm <= 0:
                continue
            a, b = v.to(start), v.to(end)
            g = f"link:{j.child}" if group else "ghost"
            st = status.get(j.child)
            col = FAIL if (group and st == "fail") else WARN if (group and st == "warn") else color
            items.append({"t": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1], "stroke": col, "sw": width, "g": g, "cls": "link", **({"dash": dash} if dash else {})})
        base = m.links[m.base]
        if base.length_mm > 0:
            a, b = v.to(np.zeros(3)), v.to(pts["ends"][m.base])
            items.append({"t": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1], "stroke": color, "sw": width, "g": f"link:{m.base}" if group else "ghost", "cls": "link", **({"dash": dash} if dash else {})})

    for v in (side, plan):
        if ghost and v is side:
            chain(v, ghost, GHOST, LINK_W - 1.5, False, "6 5")
            tp = v.to(ghost["tip"])
            items.append({"t": "text", "x": tp[0] + 8, "y": tp[1] + 4, "s": "worst-load pose", "size": FONT - 2, "fill": GHOST, "g": "ghost"})
        chain(v, home, LINK, LINK_W, True)
        # masses
        for name, p in home["masses"].items():
            pm = next(x for x in m.masses if x.name == name)
            q = v.to(p)
            r = 4 + min(8, pm.mass_g ** 0.5 / 3)
            items.append({"t": "circle", "cx": q[0], "cy": q[1], "r": r, "fill": "#f08c00" if pm.kind == "payload" else "#8d99ae", "stroke": "#ffffff", "sw": 1, "g": f"mass:{name}", "cls": "mass"})
            items.append({"t": "text", "x": q[0] + r + 3, "y": q[1] + 4, "s": f"{name} {pm.mass_g:g} g", "size": FONT - 2, "fill": MUTED, "g": f"mass:{name}"})
        # centres of mass
        for name, l in m.links.items():
            if l.mass_g > 0:
                q = v.to(home["cg"][name])
                items.append({"t": "circle", "cx": q[0], "cy": q[1], "r": 2.5, "fill": "#ffffff", "stroke": LINK, "sw": 1, "g": f"link:{name}", "cls": "cg"})
        # joints
        for j in order:
            q = v.to(home["joints"][j.name])
            st = status.get(j.name)
            col = FAIL if st == "fail" else WARN if st == "warn" else INK
            g = f"joint:{j.name}"
            if j.type == "revolute":
                items.append({"t": "circle", "cx": q[0], "cy": q[1], "r": 7, "fill": "#ffffff", "stroke": col, "sw": 2, "g": g, "cls": "joint"})
                items.append({"t": "circle", "cx": q[0], "cy": q[1], "r": 2, "fill": col, "stroke": "none", "sw": 0, "g": g, "cls": "joint"})
            elif j.type == "prismatic":
                items.append({"t": "rect", "x": q[0] - 7, "y": q[1] - 7, "w": 14, "h": 14, "rx": 1, "fill": "#ffffff", "stroke": col, "sw": 2, "g": g, "cls": "joint"})
                items.append({"t": "line", "x1": q[0] - 4, "y1": q[1], "x2": q[0] + 4, "y2": q[1], "stroke": col, "sw": 2, "g": g})
            else:
                items.append({"t": "polyline", "pts": [(q[0], q[1] - 6), (q[0] + 6, q[1]), (q[0], q[1] + 6), (q[0] - 6, q[1])], "closed": True, "fill": "#ffffff", "stroke": col, "sw": 1.5, "g": g, "cls": "joint"})
            if v is side:
                ji = next((x for x in (rep.joint_info if rep else []) if x.name == j.name), None)
                label = j.name
                if ji and ji.required is not None and ji.stall:
                    label += f"  {ji.required:.1f}/{ji.stall:.1f} {ji.load_unit}"
                elif ji and ji.required is not None:
                    label += f"  {ji.required:.1f} {ji.load_unit}"
                items.append({"t": "text", "x": q[0] + 10, "y": q[1] - 9, "s": label, "size": FONT - 1, "weight": "700", "fill": col, "g": g, "cls": "label"})
                if j.limit:
                    items.append({"t": "text", "x": q[0] + 10, "y": q[1] + 17, "s": f"{j.limit[0]:g}..{j.limit[1]:g} {j.unit}", "size": FONT - 3, "fill": MUTED, "g": g})
            else:
                items.append({"t": "text", "x": q[0] + 10, "y": q[1] + 4, "s": j.name, "size": FONT - 2, "fill": col, "g": g, "cls": "label"})
            sc.nodes[j.name] = {"kind": "joint", "x": q[0], "y": q[1], "w": 14, "h": 14}
        # the tip
        tp = v.to(home["tip"])
        items.append({"t": "polyline", "pts": [(tp[0], tp[1] - 6), (tp[0] + 6, tp[1] + 4), (tp[0] - 6, tp[1] + 4)], "closed": True, "fill": INK, "stroke": "none", "sw": 0, "g": "tip", "cls": "tip"})
        # targets
        for t in m.targets:
            q = v.to(t.at)
            reached = next((ti.reachable for ti in (rep.target_info if rep else []) if ti.name == t.name), None)
            col = TARGET_OK if reached else TARGET_BAD if reached is False else MUTED
            g = f"target:{t.name}"
            items.append({"t": "line", "x1": q[0] - 6, "y1": q[1] - 6, "x2": q[0] + 6, "y2": q[1] + 6, "stroke": col, "sw": 2, "g": g, "cls": "target"})
            items.append({"t": "line", "x1": q[0] - 6, "y1": q[1] + 6, "x2": q[0] + 6, "y2": q[1] - 6, "stroke": col, "sw": 2, "g": g, "cls": "target"})
            items.append({"t": "circle", "cx": q[0], "cy": q[1], "r": max(3.0, t.tol_mm * v.scale), "fill": "none", "stroke": col, "sw": 1, "dash": "2 2", "g": g, "cls": "target"})
            items.append({"t": "text", "x": q[0] + 9, "y": q[1] + 4, "s": t.name + ("" if reached else " (missed)" if reached is False else ""), "size": FONT - 2, "fill": col, "g": g, "cls": "label"})
    if footer:
        y = sc.height + 6
        sc.footer_top = y
        items.append({"t": "line", "x1": MARGIN, "y1": y, "x2": width - MARGIN, "y2": y, "stroke": "#c5ccd6", "sw": 1, "g": "footer"})
        y += 18
        from ..diagram import _wrap
        for sev, text in footer:
            col = {"fail": FAIL, "warn": "#b25e00", "pass": PASS, "head": INK}.get(sev, INK)
            for k, ln in enumerate(_wrap(text, int((width - 2 * MARGIN) / (FONT * 0.58)))):
                items.append({"t": "text", "x": MARGIN + (0 if k == 0 else 14), "y": y, "s": ln, "size": FONT, "weight": "700" if sev == "head" else "400", "fill": col, "g": "footer"})
                y += 15
        sc.height = y + 8
    return sc
