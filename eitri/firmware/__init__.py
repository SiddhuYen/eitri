"""Firmware: a microcontroller project as a typed spec (a board, a pinout, peripherals, the sources), checked statically and
compiled headless with arduino-cli (the fifth domain of eitri).

A firmware model file has the same contract as a CAD or harness model, but build(p) returns a Firmware:

    from eitri.firmware import *
"""
from __future__ import annotations

from ..model import Checker, Params                                    # noqa: F401
from .boards import BOARDS, Board, PinCap, board, boards_table          # noqa: F401
from .checks import scan_calls, strip_comments                         # noqa: F401
from .compile import CompileResult, compile_sketch, find_cli           # noqa: F401
from .model import DIRECTIONS, ROLES, Firmware, FirmwareError, Peripheral, PinDecl, Source, is_firmware   # noqa: F401

__all__ = ["Firmware", "FirmwareError", "PinDecl", "Peripheral", "Source", "ROLES", "DIRECTIONS", "BOARDS", "board"]
