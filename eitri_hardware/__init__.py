"""`eitri-hardware`: the command named after the PyPI package, for MCP registries and `uvx eitri-hardware mcp`.

With no arguments, or with `mcp`, it starts eitri's local MCP server on stdio (the same server as `eitri mcp`), with the CAD
kernel loaded on first use (EITRI_LAZY_IMPORT=1) so it answers an agent's `initialize` within its connect timeout. Any other
arguments go to the `eitri` command unchanged (`eitri-hardware doctor` is `eitri doctor`).

This is its own tiny package, not part of `eitri`, because importing anything in `eitri` loads the package first: the
environment variable has to be set before that.
"""
from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] == "mcp":
        os.environ.setdefault("EITRI_LAZY_IMPORT", "1")
        args = ["mcp"] + args[1:]
    from eitri.cli import main as eitri_main
    return eitri_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
