"""Wire harness design: the second domain of eitri.

A harness model file is the same contract as a CAD model (PARAMS, build(p), optional verify and CONFIGS), but
build(p) returns a Harness:

    from eitri.harness import *

Importing this (and not `from eitri import *`) keeps build123d's own `Wire` class out of the way.
"""
from __future__ import annotations

from ..model import Checker, Params                                  # noqa: F401  (verify(h, p, c) uses c)
from .catalog import (CATALOG, Gauge, awg_ampacity, awg_circular_mils, catalog_table, normalize_color,   # noqa: F401
                      voltage_drop)
from .checks import HCheck, NetInfo, net_stats, run_checks           # noqa: F401
from .model import (Breaker, Cable, Connector, Fuse, Harness, HarnessError, Link, Net, PinRef, Source, Splice,   # noqa: F401
                    SpliceRef, Wire, is_harness)
from .fuses import FUSES, fuse_table                                  # noqa: F401

__all__ = ["Harness", "Connector", "Wire", "Cable", "Splice", "Net", "HarnessError", "Gauge", "CATALOG", "voltage_drop",
           "awg_ampacity", "awg_circular_mils", "net_stats", "Fuse", "Breaker", "FUSES"]
