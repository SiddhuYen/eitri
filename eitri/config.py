"""eitri's per-user settings: the Anthropic API key and nothing else secret.

The key comes from ANTHROPIC_API_KEY when it is set, else from config.json in the user config folder
(macOS ~/Library/Application Support/eitri, Linux $XDG_CONFIG_HOME/eitri or ~/.config/eitri,
Windows %APPDATA%\\eitri; EITRI_CONFIG_DIR overrides). The file is written readable by its owner
only and never inside a project or the repository.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# measured on the 22-task benchmark (bench/RESULTS.md): list-price cost per request, Claude Opus, effort high
MEASURED_COST_PER_REQUEST_USD = 0.24


def config_dir() -> Path:
    env = os.environ.get("EITRI_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "eitri"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "eitri"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "eitri"


def config_path() -> Path:
    return config_dir() / "config.json"


def _load() -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def api_key() -> str | None:
    """The key to use: the environment first, then the saved one."""
    env = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if env:
        return env
    saved = str(_load().get("anthropic_api_key") or "").strip()
    return saved or None


def key_source() -> str | None:
    """Where the key comes from: "environment", "config" or None."""
    if (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return "environment"
    return "config" if str(_load().get("anthropic_api_key") or "").strip() else None


def save_api_key(key: str) -> Path:
    data = _load()
    data["anthropic_api_key"] = key.strip()
    _save(data)
    return config_path()


def forget_api_key() -> None:
    data = _load()
    if data.pop("anthropic_api_key", None) is not None:
        _save(data)


def mask(key: str | None) -> str:
    if not key:
        return ""
    return key[:7] + "…" + key[-4:] if len(key) > 14 else "…"


def validate_api_key(key: str, timeout: float = 15.0) -> tuple[bool, str]:
    """Check a key with the cheapest call there is: list one model (no tokens are used).
    Returns (ok, message)."""
    key = (key or "").strip()
    if not key:
        return False, "paste a key first"
    if not key.startswith("sk-ant-"):
        return False, "that does not look like an Anthropic API key (they start with sk-ant-)"
    try:
        import anthropic
    except ImportError:                                   # pragma: no cover - a dependency
        return False, "the anthropic package is missing; reinstall eitri"
    try:
        client = anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=1)
        client.models.list(limit=1)
        return True, "the key works"
    except anthropic.AuthenticationError:
        return False, "Anthropic rejected this key (invalid or revoked)"
    except anthropic.PermissionDeniedError:
        return False, "this key is not allowed to use the API (check its workspace permissions)"
    except anthropic.APIConnectionError:
        return False, "could not reach api.anthropic.com (check your internet connection)"
    except anthropic.APIStatusError as exc:
        return False, f"Anthropic answered {exc.status_code}: {exc.message}"
    except Exception as exc:  # pragma: no cover
        return False, f"{type(exc).__name__}: {exc}"
