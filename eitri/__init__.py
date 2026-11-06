"""eitri - a Claude-Code-like agent for parametric CAD.

Model files start with `from eitri import *`, which gives them build123d (both
as `bd` and as bare names), the feature library and the measurement helpers.
"""
from __future__ import annotations

try:
    import build123d as bd
except ImportError as _exc:                          # the one system library the kernel needs on Linux
    if "libGL" not in str(_exc):
        raise
    raise ImportError(f"{_exc}. The CAD kernel (OpenCASCADE) needs the system OpenGL library: on Debian/Ubuntu "
                      "`sudo apt-get install libgl1`, on Fedora `sudo dnf install mesa-libGL`") from _exc
from build123d import *  # noqa: F401,F403  - bare build123d names for model files

from .model import PALETTE, Params, Parts, Checker, srgb  # noqa: F401
from .measure import *  # noqa: F401,F403
from .lib import *  # noqa: F401,F403
from .anchors import anchor  # noqa: F401  - named points and frames of the model (eitri.anchors)

__version__ = "0.6.0"
