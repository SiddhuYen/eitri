"""eitri - a Claude-Code-like agent for parametric CAD.

Model files start with `from eitri import *`, which gives them build123d (both
as `bd` and as bare names), the feature library and the measurement helpers.
"""
from __future__ import annotations

import build123d as bd
from build123d import *  # noqa: F401,F403  - bare build123d names for model files

from .model import PALETTE, Params, Parts, Checker, srgb  # noqa: F401
from .measure import *  # noqa: F401,F403
from .lib import *  # noqa: F401,F403

__version__ = "0.5.3"
