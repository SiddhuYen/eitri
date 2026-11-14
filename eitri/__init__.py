"""eitri - a Claude-Code-like agent for parametric CAD.

Model files start with `from eitri import *`, which gives them build123d (both
as `bd` and as bare names), the feature library and the measurement helpers.
"""
from __future__ import annotations

import os as _os

__version__ = "0.9.1"


def _load_all() -> None:
    """The model-file namespace: build123d (as `bd` and as bare names), the feature library, the measurement helpers."""
    try:
        import build123d as bd
    except ImportError as _exc:                      # the one system library the kernel needs on Linux
        if "libGL" not in str(_exc):
            raise
        raise ImportError(f"{_exc}. The CAD kernel (OpenCASCADE) needs the system OpenGL library: on Debian/Ubuntu "
                          "`sudo apt-get install libgl1`, on Fedora `sudo dnf install mesa-libGL`") from _exc
    g = globals()
    g["bd"] = bd
    exec("from build123d import *\n"                 # noqa: S102 - star imports into this module's namespace
         "from eitri.model import PALETTE, Params, Parts, Checker, srgb\n"
         "from eitri.measure import *\n"
         "from eitri.lib import *\n"
         "from eitri.anchors import anchor\n"
         "from eitri.reference import reference_step\n", g)


if _os.environ.get("EITRI_LAZY_IMPORT") == "1":
    # Plugin mode (`eitri mcp` started by the user's agent, which times out a server that is slow to answer): the CAD
    # kernel loads on first use instead of at `import eitri`, so the server answers at once. `from eitri import *`
    # still works: it asks for __all__, which loads everything (PEP 562).
    def __getattr__(name: str):
        if name.startswith("__") and name != "__all__":
            raise AttributeError(name)
        _load_all()
        g = globals()
        g.pop("__getattr__", None)
        g["__all__"] = [k for k in g if not k.startswith("_")]
        if name in g:
            return g[name]
        raise AttributeError(f"module 'eitri' has no attribute {name!r}")
else:
    _load_all()
