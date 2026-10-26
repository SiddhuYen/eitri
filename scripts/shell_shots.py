"""Screenshot rounds of the app shell (the redesign loop in SHELL_NOTES.md). Needs the dev install (playwright + chromium).

    python scripts/shell_shots.py workspace DIR          build a fixture workspace: per domain a passing and a failing project
    python scripts/shell_shots.py round DIR OUT [ONLY]   start eitri web on DIR, shoot every page into OUT (+ contact sheets)

A round, for each of CAD, Wires, Motion, Pneumatics, Firmware: the editor with the chat open and closed, a failing check picked, the
Export menu open, the read-only share page (served by the core's editor handler behind a policy like eitri-hosted's); plus the local
project list. Each at 1440x900 and 390x844, dark and light. Console errors go to OUT/errors.txt. Set EITRI_ARDUINO_CLI for firmware.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS = {"bracket": ("examples/bracket.py", "Wall bracket", []),
        "quad-power": ("eitri/demos/quad-power/quad_power.py", "Quad power harness", []),
        "pan-tilt": ("eitri/demos/pan-tilt/pan_tilt.py", "Pan-tilt camera mount", []),
        "air-clamp": ("eitri/demos/air-clamp/air_clamp.py", "Air clamp", []),
        "servo-sweep": ("eitri/demos/servo-sweep/servo_sweep.py", "Servo sweep", [])}
FAIL = {"bracket-thin": ("examples/bracket.py", "Wall bracket, thin wall",
                         [('    c.true("all holes through"', '    c.at_least("wall thickness (mm)", p.thickness, 5.0)\n    c.true("all holes through"')]),
        "quad-power-light": ("eitri/demos/quad-power/quad_power.py", "Quad power, light branches", [("branch_awg=14,", "branch_awg=18,")]),
        "pan-tilt-heavy": ("eitri/demos/pan-tilt/pan_tilt.py", "Pan-tilt, heavy camera", [("camera_g=90.0", "camera_g=550.0")]),
        "air-clamp-fast": ("eitri/demos/air-clamp/air_clamp.py", "Air clamp, fast stroke", [("speed=150,", "speed=600,")]),
        "servo-sweep-pwm": ("eitri/demos/servo-sweep/servo_sweep.py", "Servo sweep, LED dimmer",
                            [('fw.pin("D13", "out", "digital_out", net="LED")', 'fw.pin("D13", "out", "pwm", net="LED")')])}
DOMAINS = [("cad", "bracket", "bracket-thin"), ("wires", "quad-power", "quad-power-light"), ("motion", "pan-tilt", "pan-tilt-heavy"),
           ("pneumatics", "air-clamp", "air-clamp-fast"), ("firmware", "servo-sweep", "servo-sweep-pwm")]
SIZES = {"desk": (1440, 900), "phone": (390, 844)}
SHARE_GET = {"/", "/index.html", "/state.json", "/view.glb", "/topology.json", "/image.png", "/model.py", "/download"}   # eitri-hosted's list


class SharePolicy:
    """What eitri-hosted's SharePolicy does to the core editor handler (read-only, a fixed list of routes)."""
    def host_ok(self, handler, post):
        return not post

    def blocked(self, method, path):
        if method != "GET":
            return "This is a read-only shared view."
        return None if path in SHARE_GET or path.startswith(("/vendor/", "/assets/")) else "This is a read-only shared view."

    def state(self, st):
        st = dict(st)
        st["hosted"] = {"mode": "share", "public_id": "abc", "owner": "maker", "title": st.get("title") or st.get("name"), "domain": st.get("kind")}
        for k in ("edits_full", "edits", "edit_notes", "last_error"):
            st.pop(k, None)
        st.update(has_agent=False, home="/", needs_key=False, demo=None)
        return st


def make_workspace(ws: Path) -> None:
    from eitri.project import Project
    from eitri.web.app import set_project_title
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    for name, (src, title, subs) in {**PASS, **FAIL}.items():
        code = (ROOT / src).read_text(encoding="utf-8")
        for a, b in subs:
            assert a in code, (name, a)
            code = code.replace(a, b)
        pr = Project(ws / name, name=name.replace("-", "_"))
        pr.write_model(code)
        res = pr.build(note="first")
        set_project_title(pr.folder, title)
        print(f"{name:18} {pr.kind:11} {'ok' if res.ok else 'BUILD FAILED'}  {(res.summary or res.error or '').splitlines()[1 if res.ok and pr.kind != 'cad' else 0][:70]}")


def start_web(ws: Path, port: int = 8791) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-m", "eitri.cli", "web", str(ws), "--no-open", "--port", str(port)], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            return proc
        except OSError:
            time.sleep(0.25)
    proc.kill()
    raise SystemExit("eitri web did not start")


def shoot(ws: Path, out: Path, only: str = "", base: str = "http://127.0.0.1:8791") -> dict:
    from PIL import Image
    from playwright.sync_api import sync_playwright
    from eitri.project import Project
    from eitri.viewer.server import make_handler
    out.mkdir(parents=True, exist_ok=True)
    errors: dict[str, list[str]] = {}

    def page_shot(b, key, url, size, scheme, setup=None):
        if only and only not in key:
            return
        w, h = SIZES[size]
        pg = b.new_page(viewport={"width": w, "height": h}, color_scheme=scheme, device_scale_factor=1)
        errs: list[str] = []
        pg.on("console", lambda m: errs.append(f"{m.type}: {m.text[:200]}") if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        pg.add_init_script("try { localStorage.clear(); } catch (e) {}")
        pg.goto(url)
        pg.wait_for_timeout(3200)
        if setup:
            setup(pg)
            pg.wait_for_timeout(900)
        name = f"{key}-{size}-{scheme}.png"
        pg.screenshot(path=str(out / name))
        if errs:
            errors[name] = errs
        pg.close()

    chat_closed = lambda pg: pg.evaluate("() => window.__eitri_chat(false)")
    fail_check = lambda pg: pg.evaluate("() => { const c = document.querySelector('#checks .check.bad'); if (c) { c.scrollIntoView({block: 'center'}); c.click(); } }")
    export_open = lambda pg: pg.locator("#exportbtn").click()
    with sync_playwright() as p:
        b = p.chromium.launch()
        for size in SIZES:
            for scheme in ("dark", "light"):
                page_shot(b, "home", base + "/", size, scheme)
                for tool, good, bad in DOMAINS:
                    page_shot(b, f"{tool}-editor-chat", f"{base}/p/{good}/", size, scheme)
                    page_shot(b, f"{tool}-editor-nochat", f"{base}/p/{good}/", size, scheme, chat_closed)
                    page_shot(b, f"{tool}-failing", f"{base}/p/{bad}/", size, scheme, fail_check)
                    page_shot(b, f"{tool}-export", f"{base}/p/{good}/", size, scheme, export_open)
                    if not only or only in f"{tool}-share":
                        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Project(ws / good), threading.Lock(), policy=SharePolicy()))
                        threading.Thread(target=srv.serve_forever, daemon=True).start()
                        page_shot(b, f"{tool}-share", f"http://127.0.0.1:{srv.server_address[1]}/", size, scheme)
                        srv.shutdown()
        b.close()
    (out / "errors.txt").write_text(json.dumps(errors, indent=1) if errors else "no console errors\n", encoding="utf-8")
    for k in sorted({f.name.rsplit("-", 2)[0] for f in out.glob("*-desk-dark.png")}):        # contact sheets: a page's four captures
        files = [out / f"{k}-{s}-{c}.png" for s in ("desk", "phone") for c in ("dark", "light")]
        if not all(f.exists() for f in files):
            continue
        ims = [Image.open(f).convert("RGB") for f in files]
        ims = [im.resize((int(im.width * .6), int(im.height * .6))) for im in ims]
        sheet = Image.new("RGB", (sum(im.width for im in ims) + 30, max(im.height for im in ims)), (128, 128, 128))
        x = 0
        for im in ims:
            sheet.paste(im, (x, 0))
            x += im.width + 10
        sheet.save(out / f"sheet-{k}.png")
    return errors


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "workspace":
        make_workspace(Path(sys.argv[2]))
    elif cmd == "round":
        ws, out = Path(sys.argv[2]), Path(sys.argv[3])
        os.environ.setdefault("EITRI_CONFIG_DIR", str(ws.parent / "eitri-config"))      # no saved key: the pages as a first run sees them
        proc = start_web(ws)
        try:
            errs = shoot(ws, out, sys.argv[4] if len(sys.argv) > 4 else "")
        finally:
            proc.terminate()
        n = len(list(out.glob("*-dark.png"))) + len(list(out.glob("*-light.png")))
        print(f"{n} screenshots in {out}; console errors: {json.dumps(errs, indent=1) if errs else 'none'}")
    else:
        print(__doc__)
