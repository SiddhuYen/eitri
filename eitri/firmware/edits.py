"""Direct edits of a firmware project's pinout (the editor's pinout table), kept in the model file's EDITS list. Sources are
read-only in the editor: the agent writes them."""
from __future__ import annotations

from .boards import board as find_board
from .model import ROLES, DIRECTIONS, Firmware, FirmwareError, PinDecl

FIRMWARE_OPS = frozenset({"set_pin", "add_pin", "remove_pin", "set_board"})


def target_of(e: dict) -> str:
    return str(e.get("net") or e.get("pin") or e.get("board") or "")


def describe(e: dict) -> str:
    op = e.get("op")
    if op == "set_pin":
        return f"pin {e.get('net')}: " + ", ".join(f"{k} = {e[k]}" for k in ("pin", "direction", "role", "pullup", "rename") if k in e)
    if op == "add_pin":
        return f"add pin {e.get('pin')} as {e.get('net') or e.get('pin')} ({e.get('direction', 'out')} {e.get('role', '')})".replace(" )", ")")
    if op == "remove_pin":
        return f"remove pin {e.get('net')}"
    if op == "set_board":
        return f"board = {e.get('board')}"
    return str(e)


def merge_edit(prev: dict, new: dict) -> dict | None:
    if prev.get("op") != new.get("op") or prev.get("suppressed") or new.get("suppressed"):
        return None
    if new.get("op") == "set_pin" and prev.get("net") == new.get("net") and "rename" not in prev:
        return {**prev, **{k: v for k, v in new.items() if k != "by"}}
    if new.get("op") == "set_board":
        return {**prev, **{k: v for k, v in new.items() if k != "by"}}
    return None


def _apply_one(fw: Firmware, e: dict) -> str:
    op = e["op"]
    if op == "set_pin":
        d = fw.get_pin(str(e["net"]))
        if "pin" in e:
            d.pin = str(e["pin"]).strip()
        if "direction" in e:
            if e["direction"] not in DIRECTIONS:
                raise FirmwareError(f"direction must be one of {DIRECTIONS}")
            d.direction = e["direction"]
        if "role" in e:
            if e["role"] not in ROLES:
                raise FirmwareError(f"role must be one of {ROLES}")
            d.role = e["role"]
        if "pullup" in e:
            d.pullup = bool(e["pullup"])
        if e.get("rename"):
            new = str(e["rename"])
            PinDecl(d.pin, d.direction, d.role, new)              # validates the identifier
            if any(x.net == new for x in fw.pins if x is not d):
                raise FirmwareError(f"net {new!r} is already used")
            d.net = new
        return f"{d.symbol} = {d.pin} ({d.direction} {d.role})"
    if op == "add_pin":
        d = fw.pin(e["pin"], e.get("direction", "out"), e.get("role"), net=e.get("net", ""), pullup=bool(e.get("pullup", False)))
        return f"{d.symbol} = {d.pin}"
    if op == "remove_pin":
        d = fw.get_pin(str(e["net"]))
        fw.pins.remove(d)
        return f"removed {d.symbol}"
    if op == "set_board":
        if find_board(str(e["board"])) is None:
            raise FirmwareError(f"unknown board {e['board']!r}")
        fw.board_key = str(e["board"])
        return f"board {fw.board_key}"
    raise FirmwareError(f"unknown firmware edit {op!r}")


def apply_edits(fw: Firmware, edits: list[dict]) -> list:
    from ..edits import EditNote
    notes = []
    for e in edits:
        op = e.get("op")
        if op not in FIRMWARE_OPS:
            notes.append(EditNote(str(op), "", False, f"{op!r} is not a firmware edit"))
            continue
        if e.get("suppressed"):
            notes.append(EditNote(op, target_of(e), True, "suppressed"))
            continue
        try:
            notes.append(EditNote(op, target_of(e), True, _apply_one(fw, e)))
        except (FirmwareError, KeyError, ValueError, TypeError) as exc:
            notes.append(EditNote(op, target_of(e), False, f"{type(exc).__name__}: {exc}"))
    return notes


def describe_state(fw: Firmware) -> dict:
    out: dict = {"board": {"board": fw.board_key}}
    for d in fw.pins:
        out[f"pin {d.net}"] = {"pin": d.pin, "direction": d.direction, "role": d.role}
    for s in fw.sources:
        out[f"source {s.name}"] = {"lines": len(s.code.splitlines()), "chars": len(s.code)}
    return out
