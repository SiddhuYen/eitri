"""Installing and updating eitri: `eitri update` (version check, cache, how eitri was installed), the startup notice
(no network unless opted in), `eitri setup firmware` (with a fake runner: nothing is installed), and the version
numbers the release workflow and the install script must agree on. No network, no API key."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

import eitri
from eitri import setup_firmware as SF
from eitri import update as U

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _config(tmp_path, monkeypatch):
    monkeypatch.setenv("EITRI_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("EITRI_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("EITRI_NO_UPDATE_NOTICE", raising=False)
    monkeypatch.setattr(U, "installed_version", lambda: "0.5.5")


def _no_network(*a, **k):
    raise AssertionError("eitri asked pypi.org without being asked to")


# ----------------------------------------------------------------------------- versions

@pytest.mark.parametrize("latest,current,newer", [
    ("0.5.6", "0.5.5", True), ("0.6.0", "0.5.10", True), ("0.5.10", "0.5.9", True),
    ("0.5.5", "0.5.5", False), ("0.5.4", "0.5.5", False), ("0.6.0rc1", "0.6.0", False),
    (None, "0.5.5", False), ("1.0", "0.9.9", True),
])
def test_is_newer(latest, current, newer):
    assert U.is_newer(latest, current) is newer
    if latest:
        assert (U._fallback_key(latest) > U._fallback_key(current)) is newer


def test_versions_agree():
    """pyproject, eitri.__version__ and the install script's fallback tag must name the same release
    (scripts/check_dist.py checks the same thing in CI, plus the git tag)."""
    py = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    tag = re.search(r'^EITRI_FALLBACK_TAG="\$\{EITRI_FALLBACK_TAG:-v([^}]+)\}"', (ROOT / "scripts/install-remote.sh").read_text(), re.M).group(1)
    assert py == eitri.__version__ == tag


# ----------------------------------------------------------------------------- the notice and privacy

def test_startup_notice_makes_no_network_call_by_default(monkeypatch, capsys):
    monkeypatch.setattr(U, "fetch_latest", _no_network)
    assert U.startup_notice() is None
    U.write_cache("0.5.9", None)                       # what an earlier `eitri update --check` learned
    line = U.startup_notice()
    assert line == "eitri 0.5.9 is available (you have 0.5.5): run `eitri update`"
    assert line in capsys.readouterr().err
    monkeypatch.setenv("EITRI_NO_UPDATE_NOTICE", "1")
    assert U.startup_notice() is None


def test_startup_notice_daily_check_when_opted_in(monkeypatch):
    calls = []
    monkeypatch.setattr(U, "fetch_latest", lambda timeout=5.0: (calls.append(timeout), ("0.6.0", None))[1])
    U.run_update(auto="on")
    assert U.auto_check_enabled()
    assert U.startup_notice().startswith("eitri 0.6.0 is available")
    assert calls == [U.STARTUP_TIMEOUT_S]
    U.startup_notice()                                 # within a day: from the cache, no second call
    assert len(calls) == 1
    cache = json.loads(U.cache_path().read_text())
    cache["checked_at"] = time.time() - U.CHECK_EVERY_S - 1
    U.cache_path().write_text(json.dumps(cache))
    U.startup_notice()
    assert len(calls) == 2
    U.run_update(auto="off")
    assert not U.auto_check_enabled()
    monkeypatch.setenv("EITRI_UPDATE_CHECK", "1")
    assert U.auto_check_enabled()


def test_failed_check_keeps_last_known_version(monkeypatch):
    U.write_cache("0.5.8", None)
    monkeypatch.setattr(U, "fetch_latest", lambda timeout=5.0: (None, "could not reach pypi.org (URLError)"))
    info = U.check()
    assert info["latest"] is None and "pypi.org" in info["error"]
    assert U.cached_newer() == "0.5.8"


def test_notice_never_breaks_startup(monkeypatch):
    monkeypatch.setenv("EITRI_UPDATE_CHECK", "1")
    monkeypatch.setattr(U, "fetch_latest", lambda timeout=5.0: 1 / 0)
    assert U.startup_notice() is None


def test_doctor_offline_reads_only_the_cache(monkeypatch):
    from eitri.doctor import _version_check
    monkeypatch.setattr(U, "fetch_latest", _no_network)
    rows = []
    _version_check(lambda *r: rows.append(r), online=False)
    assert rows[0][0] == "ok" and rows[0][2].startswith("0.5.5")
    U.write_cache("0.5.7", None)
    rows.clear()
    _version_check(lambda *r: rows.append(r), online=False)
    assert rows[0][0] == "warn" and "0.5.7 is available" in rows[0][2]


# ----------------------------------------------------------------------------- how eitri was installed

RECEIPTS = {
    "pypi": '[tool]\nrequirements = [{ name = "eitri-cad" }]\npython = "3.12"\n',
    "git": '[tool]\nrequirements = [{ name = "eitri-cad", git = "https://github.com/SiddhuYen/eitri" }]\n',
    "url": '[tool]\nrequirements = [{ name = "eitri-cad", url = "https://github.com/SiddhuYen/eitri/archive/refs/tags/v0.5.5.tar.gz" }]\n',
    "path": '[tool]\nrequirements = [{ name = "eitri-cad", path = "/tmp/eitri_cad-0.5.5-py3-none-any.whl" }]\n',
    "directory": '[tool]\nrequirements = [{ name = "eitri-cad", directory = "/Users/x/eitri" }]\n',
}


@pytest.mark.parametrize("kind", sorted(RECEIPTS))
def test_install_source_from_receipt(tmp_path, kind):
    (tmp_path / "uv-receipt.toml").write_text(RECEIPTS[kind])
    assert U.install_source(str(tmp_path))["kind"] == kind


def test_install_source_without_receipt(tmp_path):
    assert U.install_source(str(tmp_path))["kind"] == "other"


def test_receipt_fallback_parser(monkeypatch):
    """Python 3.10 has no tomllib: the inline table is read by hand."""
    monkeypatch.setitem(sys.modules, "tomllib", None)
    assert U._receipt_requirement(RECEIPTS["git"])["git"] == "https://github.com/SiddhuYen/eitri"
    assert U._receipt_requirement(RECEIPTS["pypi"]) == {"name": "eitri-cad"}


def test_plan_upgrade():
    cmd, _ = U.plan_upgrade({"kind": "pypi", "detail": ""}, "/bin/uv")
    assert cmd == ["/bin/uv", "tool", "upgrade", "eitri-cad"]
    for kind in ("git", "url", "path"):
        cmd, msg = U.plan_upgrade({"kind": kind, "detail": "x"}, "/bin/uv")
        assert cmd == ["/bin/uv", "tool", "install", "--force", "--python", "3.12", "eitri-cad"] and "PyPI" in msg
    cmd, msg = U.plan_upgrade({"kind": "directory", "detail": "/src/eitri"}, "/bin/uv")
    assert cmd is None and "git -C /src/eitri pull" in msg
    cmd, msg = U.plan_upgrade({"kind": "other", "detail": "/venv"}, "/bin/uv")
    assert cmd is None and "pip install -U eitri-cad" in msg
    cmd, msg = U.plan_upgrade({"kind": "pypi", "detail": ""}, None)
    assert cmd is None and "eitri.io/install" in msg


def test_run_update(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(U.subprocess, "call", lambda cmd: ran.append(cmd) or 0)
    monkeypatch.setattr(U, "install_source", lambda prefix=None: {"kind": "pypi", "detail": ""})
    monkeypatch.setattr(U, "find_uv", lambda: "/bin/uv")
    monkeypatch.setattr(U, "fetch_latest", lambda timeout=5.0: ("0.5.5", None))
    assert U.run_update() == 0 and not ran
    assert "0.5.5 is the newest" in capsys.readouterr().out
    monkeypatch.setattr(U, "fetch_latest", lambda timeout=5.0: ("0.6.0", None))
    assert U.run_update(check_only=True) == 0 and not ran
    assert U.run_update(dry_run=True) == 0 and not ran
    assert U.run_update() == 0 and ran == [["/bin/uv", "tool", "upgrade", "eitri-cad"]]
    monkeypatch.setattr(U, "fetch_latest", lambda timeout=5.0: (None, "eitri-cad is not on PyPI yet"))
    assert U.run_update() == 1
    assert "not on PyPI yet" in capsys.readouterr().out


# ----------------------------------------------------------------------------- eitri setup firmware

class FakeArduino:
    """Records commands; answers the read-only probes like arduino-cli 1.x."""

    def __init__(self, cores=(), libs=(), urls=""):
        self.cores, self.libs, self.urls, self.ran = set(cores), set(libs), urls, []

    def __call__(self, cmd, capture):
        self.ran.append((cmd, capture))
        args = cmd[1:]
        if args == ["version"]:
            return 0, "arduino-cli  Version: 1.3.1 Commit: x Date: y\n"
        if args == ["core", "list"]:
            return 0, "ID Installed Latest Name\n" + "".join(f"{c} 1.0 1.0 X\n" for c in self.cores)
        if args == ["lib", "list"]:
            return 0, "Name Installed Available Location Description\n" + "".join(f"{l} 1.0 - user -\n" for l in self.libs)
        if args[:2] == ["config", "get"]:
            return 0, self.urls
        return 0, ""

    def steps(self):
        return [" ".join(c[1:]) if c[0].endswith("arduino-cli") else " ".join(c) for c, capture in self.ran if not capture]


def _with_cli(monkeypatch, tmp_path):
    cli = tmp_path / "arduino-cli"
    cli.write_text("#!/bin/sh\nexit 0\n")
    cli.chmod(0o755)
    monkeypatch.setenv("EITRI_ARDUINO_CLI", str(cli))
    return str(cli)


def test_setup_firmware_fresh(monkeypatch, tmp_path):
    _with_cli(monkeypatch, tmp_path)
    fake = FakeArduino()
    assert SF.run_setup_firmware(run=fake, verify=False) == 0
    assert fake.steps() == ["core update-index", "core install arduino:avr", "lib install Servo"]


def test_setup_firmware_skips_what_is_installed(monkeypatch, tmp_path):
    _with_cli(monkeypatch, tmp_path)
    fake = FakeArduino(cores={"arduino:avr"}, libs={"Servo"})
    assert SF.run_setup_firmware(run=fake, verify=False) == 0
    assert fake.steps() == ["core update-index"]


def test_setup_firmware_esp32(monkeypatch, tmp_path):
    _with_cli(monkeypatch, tmp_path)
    fake = FakeArduino(cores={"arduino:avr"}, libs={"Servo"})
    assert SF.run_setup_firmware(esp32=True, run=fake, verify=False) == 0
    assert fake.steps() == [f"config add board_manager.additional_urls {SF.ESP32_INDEX}", "core update-index",
                            "core install esp32:esp32", "lib install ESP32Servo"]
    fake = FakeArduino(cores={"arduino:avr", "esp32:esp32"}, libs={"Servo", "ESP32Servo"}, urls=f"- {SF.ESP32_INDEX}\n")
    assert SF.run_setup_firmware(esp32=True, run=fake, verify=False) == 0
    assert fake.steps() == ["core update-index"]


def test_setup_firmware_dry_run_changes_nothing(monkeypatch, tmp_path):
    _with_cli(monkeypatch, tmp_path)
    fake = FakeArduino()
    assert SF.run_setup_firmware(esp32=True, dry_run=True, run=fake, verify=False) == 0
    assert fake.steps() == []                          # only the read-only probes ran


def test_setup_firmware_asks_before_installing_arduino_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("EITRI_ARDUINO_CLI", raising=False)
    monkeypatch.setattr("eitri.firmware.compile.find_cli", lambda: None)
    monkeypatch.setattr(SF, "install_command", lambda: (["brew", "install", "arduino-cli"], "Homebrew"))
    fake = FakeArduino()
    asked = []
    assert SF.run_setup_firmware(run=fake, ask=lambda q: asked.append(q) or False, verify=False) == 1
    assert asked and fake.ran == []                    # said no: nothing ran
    assert SF.run_setup_firmware(dry_run=True, run=fake, ask=_no_network, verify=False) == 0
    assert fake.ran == []
    out = capsys.readouterr().out
    assert "would run (installing arduino-cli): brew install arduino-cli" in out
    # --yes installs without asking; then the (still fake) arduino-cli is found
    cli = _with_cli(monkeypatch, tmp_path)
    found = iter([None, cli])
    monkeypatch.setattr("eitri.firmware.compile.find_cli", lambda: next(found))
    monkeypatch.delenv("EITRI_ARDUINO_CLI")
    assert SF.run_setup_firmware(yes=True, run=fake, ask=_no_network, verify=False) == 0
    assert fake.steps()[0] == "brew install arduino-cli"


def test_install_hint_points_at_setup():
    from eitri.firmware.compile import install_hint
    assert "`eitri setup firmware`" in install_hint("arduino:avr")
    assert "`eitri setup firmware --esp32`" in install_hint("esp32:esp32")
    assert "arduino-cli core install arduino:avr" in install_hint("arduino:avr")
