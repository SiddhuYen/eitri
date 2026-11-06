"""Check a built eitri-hardware before it is installed or published:

- dist/ holds exactly one wheel and one sdist, both of the version in pyproject.toml;
- every file git tracks under eitri/ is in the wheel and in the sdist (the package data: the web app and its
  assets, the viewer's three.js, the agent's .md prompts, the demo projects' tarballs) - nothing is left behind
  by a too-narrow [tool.setuptools.package-data] glob;
- pyproject.toml, eitri/__init__.py, the install script's fallback tag and (with --tag, in the release workflow)
  the git tag all name the same version.

    python scripts/check_dist.py [--dist dist] [--tag v0.5.5]

Prints the sizes; exits 1 with the reasons when anything is off.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# every kind of package data must be present at least once (a guard even where git is unavailable)
REQUIRED = ["eitri/web/home.html", "eitri/web/assets/", "eitri/viewer/vendor/three/", "eitri/agent/prompt.md",
            "eitri/demos/v8-engine/project.tar.xz", "eitri/demos/v8-engine/demo.json", "eitri/demos/quad-power/quad_power.py"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    ap.add_argument("--tag", default=None, help="the release tag, e.g. v0.5.5")
    args = ap.parse_args()
    problems: list[str] = []

    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    init_v = re.search(r'^__version__ = "([^"]+)"', (ROOT / "eitri/__init__.py").read_text(encoding="utf-8"), re.M).group(1)
    fallback = re.search(r'EITRI_FALLBACK_TAG:-v([^}]+)\}', (ROOT / "scripts/install-remote.sh").read_text(encoding="utf-8")).group(1)
    for what, v in (("eitri/__init__.py __version__", init_v), ("scripts/install-remote.sh EITRI_FALLBACK_TAG", fallback)):
        if v != version:
            problems.append(f"{what} is {v}, pyproject.toml says {version}")
    if args.tag and args.tag.lstrip("v") != version:
        problems.append(f"the tag {args.tag} does not match pyproject.toml's version {version}")

    dist = Path(args.dist)
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        problems.append(f"expected one wheel and one sdist in {dist}, found {[p.name for p in wheels + sdists]}")
    else:
        wheel, sdist = wheels[0], sdists[0]
        for p in (wheel, sdist):
            if f"-{version}" not in p.name:
                problems.append(f"{p.name} is not version {version}")
            if not p.name.startswith(f"eitri_hardware-{version}"):
                problems.append(f"{p.name} is not named eitri_hardware-{version}-... (the PyPI package is eitri-hardware)")
        in_wheel = set(zipfile.ZipFile(wheel).namelist())
        with tarfile.open(sdist) as t:
            in_sdist = {n.split("/", 1)[1] for n in t.getnames() if "/" in n}
        try:
            tracked = set(subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "eitri"], text=True).split())
        except (OSError, subprocess.CalledProcessError):
            tracked = set()
            print("note: git is unavailable; checking only the required files")
        for name, files in (("wheel", in_wheel), ("sdist", in_sdist)):
            missing = sorted(tracked - files)
            if missing:
                problems.append(f"{len(missing)} tracked file(s) missing from the {name}: {missing[:10]}")
            for req in REQUIRED:
                if not any(f == req or (req.endswith("/") and f.startswith(req)) for f in files):
                    problems.append(f"{req} is missing from the {name}")
        data = [f for f in in_wheel if f.startswith("eitri/") and not f.endswith(".py")]
        print(f"wheel  {wheel.name}: {wheel.stat().st_size / 1e6:.2f} MB, {len(in_wheel)} files ({len(data)} package-data files)")
        print(f"sdist  {sdist.name}: {sdist.stat().st_size / 1e6:.2f} MB, {len(in_sdist)} files")
        print(f"tracked files under eitri/: {len(tracked)}")

    if problems:
        print("\n".join("FAIL " + p for p in problems))
        return 1
    print(f"ok: eitri-hardware {version}; every tracked package file is in the wheel and the sdist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
