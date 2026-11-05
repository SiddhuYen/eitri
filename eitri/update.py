"""`eitri update` and the one-line "a newer eitri is available" notice.

Privacy. eitri's automatic network calls go to Anthropic only. Asking PyPI for the newest version is a call
to https://pypi.org, so it happens only when you ask for it:

- `eitri update` (and `eitri update --check`),
- `eitri doctor --online`,
- once a day when eitri starts, but only if you turned that on: `eitri update --auto on` (or EITRI_UPDATE_CHECK=1).

The answer is cached in <config folder>/update-check.json. When `eitri` / `eitri chat` starts and the cache knows of a
newer version, it prints one line saying so. Reading the cache is local and makes no network call.
EITRI_NO_UPDATE_NOTICE=1 hides the line.

How the upgrade runs depends on how eitri was installed. uv writes a receipt (uv-receipt.toml) into every tool
environment it creates. From PyPI: `uv tool upgrade eitri-cad`. From a git URL or a release archive (the installer's
fallback before eitri-cad was on PyPI): reinstall from PyPI. From a local checkout (`./install.sh`): eitri says how
to pull and reinstall and changes nothing. Not installed with uv tool (pip, a dev venv): eitri prints the pip command.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PACKAGE = "eitri-cad"
PYPI_JSON = f"https://pypi.org/pypi/{PACKAGE}/json"
INSTALL_URL = "https://eitri.io/install"
CHECK_EVERY_S = 24 * 3600
STARTUP_TIMEOUT_S = 2.0          # the opt-in daily check at startup never holds eitri up longer than this


# ----------------------------------------------------------------------------- versions

def installed_version() -> str:
    try:
        from importlib.metadata import version
        return version(PACKAGE)
    except Exception:  # noqa: BLE001 - running from a source tree without metadata
        from . import __version__
        return __version__


def _fallback_key(v: str) -> tuple:
    """The numeric release part; a pre-release such as 0.6.0rc1 sorts below 0.6.0."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)(.*)$", v or "")
    nums = tuple(int(x) for x in m.group(1).split(".")) if m else ()
    return nums + (0,) * (4 - len(nums)) + ((0,) if m and m.group(2).strip() else (1,),)


def is_newer(latest: str | None, current: str | None) -> bool:
    if not latest or not current:
        return False
    try:
        from packaging.version import Version
        return Version(latest) > Version(current)
    except Exception:  # noqa: BLE001 - no `packaging`, or a version it cannot parse
        return _fallback_key(latest) > _fallback_key(current)


# ----------------------------------------------------------------------------- PyPI and the cache

def fetch_latest(timeout: float = 5.0) -> tuple[str | None, str | None]:
    """(newest version on PyPI, error). One GET to pypi.org; nothing about you or your projects is sent
    beyond what any HTTP request carries (the User-Agent names eitri and its version)."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(PYPI_JSON, headers={"User-Agent": f"eitri/{installed_version()} (update check)",
                                                     "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_tls_context()) as resp:   # noqa: S310 - a fixed https URL
            data = json.loads(resp.read().decode("utf-8"))
        v = str((data.get("info") or {}).get("version") or "").strip()
        return (v or None), (None if v else "PyPI answered without a version")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, f"{PACKAGE} is not on PyPI yet"
        return None, f"PyPI answered {exc.code}"
    except Exception as exc:  # noqa: BLE001 - offline, DNS, TLS, timeout
        return None, f"could not reach pypi.org ({type(exc).__name__})"


def _tls_context():
    """certifi's CA bundle when it is installed (it comes with anthropic/httpx): uv's own Python builds on macOS
    have no system CA path, so the default context fails with CERTIFICATE_VERIFY_FAILED."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def cache_path() -> Path:
    from .config import config_dir
    return config_dir() / "update-check.json"


def read_cache() -> dict:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_cache(latest: str | None, error: str | None) -> None:
    data = {"checked_at": time.time(), "latest": latest, "error": error}
    if latest is None:                    # keep the last known version through a failed check
        data["latest"] = read_cache().get("latest")
    try:
        p = cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def check(timeout: float = 5.0) -> dict:
    """Ask PyPI now and cache the answer: {current, latest, newer, error}."""
    latest, error = fetch_latest(timeout)
    write_cache(latest, error)
    current = installed_version()
    return {"current": current, "latest": latest, "newer": is_newer(latest, current), "error": error}


def cached_newer() -> str | None:
    """The newer version the cache knows of, without any network call."""
    latest = read_cache().get("latest")
    return latest if is_newer(latest, installed_version()) else None


# ----------------------------------------------------------------------------- the opt-in daily check

def auto_check_enabled() -> bool:
    env = (os.environ.get("EITRI_UPDATE_CHECK") or "").strip().lower()
    if env:
        return env in ("1", "true", "yes", "on")
    from .config import get_setting
    return bool(get_setting("update_check", False))


def startup_notice(stream=None) -> str | None:
    """Called when `eitri` / `eitri chat` starts: refresh the cache once a day if the user opted in, then print one
    line if a newer version is known. Never raises."""
    stream = stream or sys.stderr
    try:
        if (os.environ.get("EITRI_NO_UPDATE_NOTICE") or "").strip() not in ("", "0"):
            return None
        if auto_check_enabled() and time.time() - float(read_cache().get("checked_at") or 0) > CHECK_EVERY_S:
            latest, error = fetch_latest(STARTUP_TIMEOUT_S)
            write_cache(latest, error)
        newer = cached_newer()
        if newer:
            line = f"eitri {newer} is available (you have {installed_version()}): run `eitri update`"
            print(line, file=stream, flush=True)
            return line
    except Exception:  # noqa: BLE001 - a notice must never break startup
        pass
    return None


# ----------------------------------------------------------------------------- how eitri was installed

def _receipt_requirement(text: str) -> dict:
    """The eitri-cad requirement from a uv-receipt.toml."""
    try:
        import tomllib                                         # Python 3.11+
        data = tomllib.loads(text)
        for req in (data.get("tool") or {}).get("requirements") or []:
            if isinstance(req, dict) and str(req.get("name", "")).replace("_", "-").lower() == PACKAGE:
                return req
        return {}
    except ImportError:                                        # Python 3.10: read the inline table by hand
        m = re.search(r'\{\s*name\s*=\s*"eitri[-_]cad"([^}]*)\}', text)
        if not m:
            return {}
        req = {"name": PACKAGE}
        for k, v in re.findall(r'(\w+)\s*=\s*"([^"]*)"', m.group(1)):
            req[k] = v
        return req
    except Exception:  # noqa: BLE001
        return {}


def install_source(prefix: str | None = None) -> dict:
    """{kind, detail}: kind is pypi, git, url, path (a wheel or archive file), directory (a checkout), or other
    (not a uv tool environment)."""
    receipt = Path(prefix or sys.prefix) / "uv-receipt.toml"
    if not receipt.is_file():
        return {"kind": "other", "detail": sys.prefix}
    try:
        req = _receipt_requirement(receipt.read_text(encoding="utf-8"))
    except OSError:
        req = {}
    for kind in ("git", "url", "directory", "editable", "path"):
        val = req.get(kind)
        if val:
            if kind == "editable" or (kind == "path" and Path(str(val)).is_dir()):
                kind = "directory"
            return {"kind": kind, "detail": str(val)}
    return {"kind": "pypi", "detail": str(req.get("specifier") or "")}


def find_uv() -> str | None:
    for cand in (os.environ.get("UV"), shutil.which("uv"), str(Path.home() / ".local" / "bin" / "uv"),
                 str(Path.home() / ".cargo" / "bin" / "uv")):
        if cand and Path(cand).is_file() and os.access(cand, os.X_OK):
            return cand
    return None


def plan_upgrade(source: dict, uv: str | None) -> tuple[list[str] | None, str]:
    """(command to run or None, what to tell the user)."""
    kind = source["kind"]
    if kind == "directory":
        d = source["detail"]
        return None, (f"this eitri was installed from the checkout at {d}; update it there: "
                      f"`git -C {d} pull && {Path(d) / 'install.sh'}`")
    if kind == "other":
        return None, (f"this eitri was not installed with `uv tool` (it runs from {source['detail']}); "
                      f"upgrade it the way it was installed, e.g. `python -m pip install -U {PACKAGE}`, "
                      f"or reinstall with `curl -fsSL {INSTALL_URL} | sh`")
    if uv is None:
        return None, f"uv was not found; reinstall with `curl -fsSL {INSTALL_URL} | sh`"
    if kind == "pypi":
        return [uv, "tool", "upgrade", PACKAGE], "upgrading with uv"
    # git / url / path: move it over to the PyPI package so later updates are plain upgrades
    return [uv, "tool", "install", "--force", "--python", "3.12", PACKAGE], f"reinstalling from PyPI (was: {source['detail']})"


def run_update(check_only: bool = False, auto: str | None = None, dry_run: bool = False) -> int:
    if auto is not None:
        from .config import set_setting
        on = auto == "on"
        set_setting("update_check", on)
        print("daily update check: " + ("on - when eitri starts, at most once a day, it asks pypi.org for the newest "
                                         "version (2 s timeout) and prints one line if there is one"
                                         if on else "off - eitri only asks pypi.org when you run `eitri update`"))
        return 0
    current = installed_version()
    info = check()
    latest = info["latest"]
    if latest is None:
        print(f"eitri {current} is installed; could not get the newest version: {info['error']}")
        if check_only:
            return 1
        print(f"to reinstall the newest release: curl -fsSL {INSTALL_URL} | sh")
        return 1
    if not info["newer"]:
        print(f"eitri {current} is the newest version" if not is_newer(current, latest)
              else f"eitri {current} is newer than the newest release on PyPI ({latest})")
        return 0
    print(f"eitri {latest} is available (you have {current})")
    if check_only:
        print("run `eitri update` to install it")
        return 0
    cmd, msg = plan_upgrade(install_source(), find_uv())
    if cmd is None:
        print(msg)
        return 1
    print(f"==> {msg}: {' '.join(cmd)}", flush=True)
    if dry_run:
        return 0
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"the update failed (exit {rc}); eitri {current} is still installed", file=sys.stderr)
        return rc
    print(f"updated to eitri {latest}. Check it: eitri doctor")
    return 0
