"""The eitri feature library: verified parametric building blocks.

Every function here returns build123d geometry (or a cutter to subtract) and is
unit-tested for validity and dimensions, so an agent can rely on them instead of
reinventing screw holes, inserts, gears or threads from memory.
"""
from __future__ import annotations

# bind the submodules first: the star imports below shadow `enclosure` with the function
from . import fasteners as _f, features as _ft, enclosure as _e, bearings as _b, parts as _p, direct as _dr

from .fasteners import *  # noqa: F401,F403,E402
from .features import *  # noqa: F401,F403,E402
from .enclosure import *  # noqa: F401,F403,E402
from .bearings import *  # noqa: F401,F403,E402
from .parts import *  # noqa: F401,F403,E402
from .direct import *  # noqa: F401,F403,E402

__all__ = list(_f.__all__) + list(_ft.__all__) + list(_e.__all__) + list(_b.__all__) + list(_p.__all__) + list(_dr.__all__)
