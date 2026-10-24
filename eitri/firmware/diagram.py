"""The pinout diagram: the board as a pin table (every declared pin with its number, constant, direction and role), the
peripherals beside it wired to their pins, the compile verdict underneath. Pins carry data-kind="pin" data-id="<board pin>"."""
from __future__ import annotations

from ..diagram import FAIL, FONT, INK, MUTED, WARN, Scene, _wrap, footer_from, scene_to_png, scene_to_svg, status_from, text_w   # noqa: F401
from .model import Firmware

MARGIN = 28
ROW = 22
BOARD_W = 380
PER_W = 170
ROLE_COLOR = {"digital_out": "#2b6cd4", "digital_in": "#2e9e4f", "pwm": "#7d3fc4", "analog_in": "#c98a00", "dac": "#c98a00", "interrupt": "#1fb5ad",
              "servo": "#d6336c", "serial_tx": "#6b7686", "serial_rx": "#6b7686", "i2c_sda": "#6b7686", "i2c_scl": "#6b7686"}


def build_scene(fw: Firmware, rep=None, status: dict[str, str] | None = None, footer: list[tuple[str, str]] | None = None, title: str | None = None) -> Scene:
    status = status or {}
    b = fw.board
    info = {p.net: p for p in (rep.pins if rep else [])}
    top = MARGIN + 30
    n = max(len(fw.pins), 1)
    board_h = 44 + ROW * n + 26
    width = MARGIN * 2 + BOARD_W + 90 + PER_W
    sc = Scene(width, top + board_h + MARGIN)
    items = sc.items
    items.append({"t": "text", "x": MARGIN, "y": 22, "s": title or fw.name, "size": 14, "weight": "700", "fill": INK, "g": "title"})
    x0, y0 = MARGIN, top
    items.append({"t": "rect", "x": x0, "y": y0, "w": BOARD_W, "h": board_h, "rx": 6, "fill": "#f4f8f4" if b else "#fff5f5", "stroke": "#2f6b3a" if b else FAIL, "sw": 1.6, "g": "board", "cls": "board"})
    items.append({"t": "text", "x": x0 + 14, "y": y0 + 20, "s": b.name if b else f"unknown board {fw.board_key!r}", "size": FONT + 1, "weight": "700", "fill": INK, "g": "board"})
    items.append({"t": "text", "x": x0 + 14, "y": y0 + 34, "s": (b.fqbn if b else "") + (" · Wi-Fi on" if fw.wifi else ""), "size": FONT - 2, "fill": MUTED, "g": "board"})
    anchors: dict[str, tuple[float, float]] = {}
    for i, d in enumerate(fw.pins):
        pi = info.get(d.net)
        cap = b.resolve(d.pin) if b else None
        pid = cap.name if cap else d.pin
        st = status.get(pid) or (pi.status if pi and pi.status in ("fail", "warn") else None)
        col = FAIL if st == "fail" else WARN if st == "warn" else INK
        ry = y0 + 44 + i * ROW
        g = f"pin:{pid}"
        items.append({"t": "rect", "x": x0 + 6, "y": ry, "w": BOARD_W - 12, "h": ROW - 2, "rx": 3, "fill": "#ffffff" if i % 2 == 0 else "#f7f9fb", "stroke": col if st else "none", "sw": 1.4 if st else 0, "g": g, "cls": "row"})
        items.append({"t": "text", "x": x0 + 14, "y": ry + 14, "s": pid, "size": FONT, "weight": "700", "fill": col, "g": g})
        items.append({"t": "text", "x": x0 + 74, "y": ry + 14, "s": "" if cap is None else f"#{cap.number}", "size": FONT - 2, "fill": MUTED, "g": g})
        items.append({"t": "text", "x": x0 + 108, "y": ry + 14, "s": d.symbol, "size": FONT - 1, "fill": INK, "g": g})
        rc = ROLE_COLOR.get(d.role, MUTED)
        tag = f"{d.direction} {d.role}"
        tw = text_w(tag, FONT - 2) + 10
        items.append({"t": "rect", "x": x0 + BOARD_W - 14 - tw, "y": ry + 3, "w": tw, "h": ROW - 8, "rx": 7, "fill": rc, "stroke": "none", "sw": 0, "g": g, "cls": "tag"})
        items.append({"t": "text", "x": x0 + BOARD_W - 14 - tw / 2, "y": ry + 14, "s": tag, "size": FONT - 2, "fill": "#ffffff", "anchor": "middle", "g": g})
        anchors[d.net] = (x0 + BOARD_W, ry + ROW / 2 - 1)
        sc.nodes[pid] = {"kind": "pin", "x": x0, "y": ry, "w": BOARD_W, "h": ROW}
    if not fw.pins:
        items.append({"t": "text", "x": x0 + 14, "y": y0 + 60, "s": "no pins declared", "size": FONT, "fill": MUTED, "g": "board"})
    free = (len([p for p in b.pins.values() if not p.forbidden]) - len({(b.resolve(d.pin) or d).name if b.resolve(d.pin) else d.pin for d in fw.pins})) if b else 0
    items.append({"t": "text", "x": x0 + 14, "y": y0 + board_h - 9, "s": f"{max(free, 0)} other pin(s) free" if b else "", "size": FONT - 2, "fill": MUTED, "g": "board"})
    # peripherals, wired to their pins
    px = x0 + BOARD_W + 90
    py = y0
    for per in fw.peripherals:
        nets = [d for d in fw.pins if d.peripheral == per.name]
        h = 30 + 14 * max(1, len(per.pins))
        g = f"peripheral:{per.name}"
        items.append({"t": "rect", "x": px, "y": py, "w": PER_W, "h": h, "rx": 5, "fill": "#fbfcfd", "stroke": "#3a4656", "sw": 1.2, "g": g, "cls": "peripheral"})
        items.append({"t": "text", "x": px + 10, "y": py + 17, "s": f"{per.name} · {per.kind.replace('_', ' ')}", "size": FONT - 1, "weight": "700", "fill": INK, "g": g})
        for k, (fn, pin) in enumerate(per.pins.items()):
            items.append({"t": "text", "x": px + 10, "y": py + 32 + 14 * k, "s": f"{fn}: {pin}" + (f"  {per.params['baud']} baud" if per.kind == "serial" and k == 0 else ""), "size": FONT - 2, "fill": MUTED, "g": g})
        for d in nets:
            if d.net in anchors:
                ax, ay = anchors[d.net]
                items.append({"t": "polyline", "pts": [(ax, ay), (ax + 40, ay), (ax + 40, py + h / 2), (px, py + h / 2)], "stroke": ROLE_COLOR.get(d.role, MUTED), "sw": 1.6, "fill": "none", "g": g, "cls": "wire"})
        py += h + 14
    sc.height = max(sc.height, py + MARGIN)
    c = rep.compile if rep else {}
    if c:
        verdict = c.get("message") if not c.get("available") else ("compiled" + (f": flash {c['flash_pct']}%, RAM {c['ram_pct']}%" if c.get("flash_pct") is not None else "")) if c.get("ok") else f"compile failed: {len(c.get('errors') or [])} error(s)"
        colr = "#1a7f4b" if c.get("ok") else FAIL
        items.append({"t": "text", "x": MARGIN, "y": sc.height - 6, "s": f"arduino-cli: {verdict}", "size": FONT, "weight": "700", "fill": colr, "g": "compile"})
        sc.height += 14
    if footer:
        y = sc.height + 6
        sc.footer_top = y
        items.append({"t": "line", "x1": MARGIN, "y1": y, "x2": width - MARGIN, "y2": y, "stroke": "#c5ccd6", "sw": 1, "g": "footer"})
        y += 18
        for sev, text in footer:
            colr = {"fail": FAIL, "warn": "#b25e00", "pass": "#1a7f4b", "head": INK}.get(sev, INK)
            for k, ln in enumerate(_wrap(text, int((width - 2 * MARGIN) / (FONT * 0.58)))):
                items.append({"t": "text", "x": MARGIN + (0 if k == 0 else 14), "y": y, "s": ln, "size": FONT, "weight": "700" if sev == "head" else "400", "fill": colr, "g": "footer"})
                y += 15
        sc.height = y + 8
    return sc
