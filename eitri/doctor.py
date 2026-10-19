"""`eitri doctor` (is this install complete and working?) and `eitri key` (the Anthropic API key).

Both are written to be run by a person or by an AI agent setting eitri up: plain lines, a stable
first word per line (ok / warn / FAIL), `--json` for machines, and a non-zero exit code when eitri
cannot work.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


def _line(status: str, label: str, detail: str) -> str:
    return f"  {status:<5}{label}: {detail}"


def run_doctor(online: bool = False, as_json: bool = False) -> int:
    checks: list[dict] = []

    def add(status: str, label: str, detail: str) -> None:
        checks.append({"status": status, "check": label, "detail": detail})
        if not as_json:
            print(_line(status, label, detail), flush=True)

    if not as_json:
        print("eitri doctor")
    v = sys.version_info
    add("ok" if v >= (3, 10) else "FAIL", "python", f"{v.major}.{v.minor}.{v.micro} ({sys.executable})")

    t = time.time()
    try:
        import build123d  # noqa: F401
        import OCP  # noqa: F401
        from importlib.metadata import PackageNotFoundError, version
        versions = []
        for dist in ("build123d", "cadquery-ocp-novtk", "cadquery-ocp"):
            try:
                versions.append(f"{dist.replace('cadquery-ocp-novtk', 'OCP').replace('cadquery-ocp', 'OCP')} {version(dist)}")
            except PackageNotFoundError:
                continue
        add("ok", "CAD kernel", f"{', '.join(versions) or 'build123d'}")
    except Exception as exc:  # noqa: BLE001 - reported
        add("FAIL", "CAD kernel", f"build123d/OCP would not import: {type(exc).__name__}: {exc}")

    if checks[-1]["status"] == "ok":
        t = time.time()
        try:
            from .kernel import build_model
            with tempfile.TemporaryDirectory(prefix="eitri-doctor-") as tmp:
                model = Path(tmp) / "cube.py"
                model.write_text("from eitri import *\n\nPARAMS = dict(size=20.0)\n\ndef build(p):\n"
                                 "    return Box(p.size, p.size, p.size)\n", encoding="utf-8")
                old = os.environ.get("EITRI_WORKER")
                os.environ["EITRI_WORKER"] = "0"           # a plain subprocess: nothing left running afterwards
                try:
                    res = build_model(model, Path(tmp) / "out", formats=("step",), render=True)
                finally:
                    if old is None:
                        os.environ.pop("EITRI_WORKER", None)
                    else:
                        os.environ["EITRI_WORKER"] = old
                ok = res.ok and res.report is not None and abs(res.report.volume - 8000) < 1e-3 and "png" in res.outputs
                add("ok" if ok else "FAIL", "test build",
                    f"20 mm cube, volume {res.report.volume:g} mm^3, STEP + report + review image in {time.time() - t:.1f} s"
                    if ok else f"the build did not come out right: {(res.error or 'unexpected result')[:300]}")
        except Exception as exc:  # noqa: BLE001
            add("FAIL", "test build", f"{type(exc).__name__}: {exc}")

    from .agent.claude_cli import bundled_claude_binary, find_claude_binary, supports_bare
    binary = find_claude_binary()
    if binary:
        try:
            import subprocess
            version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:  # noqa: BLE001
            version = "?"
        where = "bundled with claude-agent-sdk" if binary == bundled_claude_binary() else binary
        add("ok", "Claude Code binary", f"{version or '?'} ({where}); API-key-only mode (--bare) supported"
            if supports_bare(binary) else f"{version} ({where})")
    else:
        add("FAIL", "Claude Code binary", "not found: reinstall eitri (it comes with the claude-agent-sdk package)")

    from .config import api_key, config_path, key_source, mask, validate_api_key
    key = api_key()
    if not key:
        add("warn", "API key", "not set - the editor and the demo projects work without one; to chat with Claude run "
            "`eitri key set` or paste a key on the home page (https://console.anthropic.com/settings/keys)")
    else:
        where = "ANTHROPIC_API_KEY" if key_source() == "environment" else str(config_path())
        if online:
            ok, msg = validate_api_key(key)
            add("ok" if ok else "FAIL", "API key", f"{mask(key)} from {where}: {msg}")
        else:
            add("ok", "API key", f"{mask(key)} from {where} (not checked online; `eitri doctor --online` checks it)")

    try:
        from .demos import list_demos
        demos = list_demos()
        add("ok" if demos else "warn", "demo projects", ", ".join(d["title"] for d in demos) if demos else "none bundled")
    except Exception as exc:  # noqa: BLE001
        add("warn", "demo projects", f"{type(exc).__name__}: {exc}")

    failed = [c for c in checks if c["status"] == "FAIL"]
    ready = not failed
    next_step = "run `eitri` - it opens http://127.0.0.1:8765 in your browser"
    if as_json:
        print(json.dumps({"ready": ready, "checks": checks, "next": next_step if ready else None}, indent=2))
    elif ready:
        print(f"eitri is ready. Next: {next_step}.")
    else:
        print("eitri is NOT ready: fix the FAIL lines above (reinstalling usually does it).")
    return 0 if ready else 1


def run_key(action: str, key: str | None = None, check: bool = True) -> int:
    from .config import api_key, config_path, forget_api_key, key_source, mask, save_api_key, validate_api_key
    if action == "show":
        k = api_key()
        if not k:
            print("no API key set (add one with `eitri key set`)")
            return 1
        where = "the ANTHROPIC_API_KEY environment variable" if key_source() == "environment" else str(config_path())
        print(f"{mask(k)} from {where}")
        return 0
    if action == "forget":
        forget_api_key()
        print(f"removed the saved key from {config_path()}" + (" (ANTHROPIC_API_KEY is still set in this environment)"
                                                                if key_source() == "environment" else ""))
        return 0
    # set
    if not key:
        if sys.stdin.isatty():
            import getpass
            key = getpass.getpass("Anthropic API key (input hidden): ")
        else:
            key = sys.stdin.readline()
    key = (key or "").strip()
    if check:
        ok, msg = validate_api_key(key)
        if not ok:
            print(f"not saved: {msg}", file=sys.stderr)
            return 1
    elif not key.startswith("sk-ant-"):
        print("not saved: Anthropic API keys start with sk-ant-", file=sys.stderr)
        return 1
    path = save_api_key(key)
    print(f"saved {mask(key)} to {path} (readable only by you)")
    return 0
