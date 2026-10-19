"""Files the pages load from eitri itself (three.js, fonts, icons), so nothing is fetched from the
internet: /vendor/... from eitri/viewer/vendor and /assets/... from eitri/web/assets, under any page
prefix (the editor lives at /p/<id>/ and asks for vendor/... relative to it)."""
from __future__ import annotations

from pathlib import Path

ROOTS = {"vendor": Path(__file__).parent / "viewer" / "vendor", "assets": Path(__file__).parent / "web" / "assets"}
TYPES = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml", ".png": "image/png",
         ".json": "application/json", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8"}


def resolve(path: str) -> tuple[Path, str] | None:
    """(file, content type) for a /vendor/... or /assets/... request path, or None."""
    parts = [p for p in path.split("/") if p]
    for i, p in enumerate(parts):
        if p in ROOTS:
            rel = parts[i + 1:]
            if not rel or any(x in ("..", ".") or x.startswith(".") for x in rel):
                return None
            root = ROOTS[p].resolve()
            f = root.joinpath(*rel).resolve()
            if root not in f.parents or not f.is_file():
                return None
            return f, TYPES.get(f.suffix.lower(), "application/octet-stream")
    return None


def serve(handler, path: str) -> bool:
    """Answer the request if it is for a bundled static file; True when answered."""
    hit = resolve(path)
    if hit is None:
        return False
    f, ctype = hit
    body = f.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "max-age=86400")
    handler.end_headers()
    handler.wfile.write(body)
    return True
