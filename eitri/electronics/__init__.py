"""Electronics at breadboard and module level: a board, supplies, regulators, breakout modules, passives, transistors, relays,
motors and servos joined by nets, checked against sourced datasheet limits (the sixth domain of eitri). No PCB layout.

A circuit model file has the same contract as a CAD or harness model, but build(p) returns a Circuit:

    from eitri.electronics import *
"""
from __future__ import annotations

from ..model import Checker, Params                                    # noqa: F401
from .analysis import Analysis, analyze as analyze_dc                  # noqa: F401
from .catalog import BOARDS, E12, E24, LEDS, MODULES, PARTS, SERVOS, catalog_table, nearest_e   # noqa: F401
from .model import Circuit, CircuitError, Logic, Net, Part, PinRef, PinSpec, amps_text, farads, is_circuit, ohms   # noqa: F401
from .sources import SOURCES, cite, sources_table                      # noqa: F401

__all__ = ["Circuit", "CircuitError", "Part", "PinRef", "Net", "nearest_e", "E12", "E24", "ohms", "amps_text", "BOARDS", "MODULES",
           "SERVOS", "PARTS", "LEDS", "SOURCES", "cite", "analyze_dc"]
