"""Subprocess entry point for extra views (view tool and `eitri view`)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--views", default="")
    ap.add_argument("--section", default="")
    ap.add_argument("--exploded", action="store_true")
    ap.add_argument("--hide", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--size", type=int, default=720)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--turntable", type=int, default=0, help="frames of a turntable GIF instead of a contact sheet")
    args = ap.parse_args(argv)

    from .model import Parts, load_model
    from .render import contact_sheet, exploded_parts, section_sheet

    overrides = dict(s.split("=", 1) for s in args.set)
    model = load_model(args.model, overrides)
    parts = model.build()
    hide = {h for h in args.hide.split(",") if h}
    only = {o for o in args.only.split(",") if o}
    unknown = (hide | only) - set(parts.names)
    if unknown:
        print(f"unknown part names {sorted(unknown)}; parts are {parts.names}", file=sys.stderr)
        return 2
    if only:
        parts = Parts([p for p in parts if p.name in only])
    elif hide:
        parts = Parts([p for p in parts if p.name not in hide])
    if args.exploded:
        parts = exploded_parts(parts)
    w = args.size
    cell = (w, int(w * 0.75))
    if args.turntable:
        from .render import turntable
        turntable(parts, args.out, frames=args.turntable, size=(w, int(w * 0.75)))
        print(f"turntable GIF, {args.turntable} frames; parts shown: {parts.names}" + (" (exploded)" if args.exploded else ""))
        return 0
    if args.section:
        plane, _, off = args.section.partition(":")
        offset = float(off) if off else 0.0
        section_sheet(parts, args.out, plane=plane.upper(), offset=offset, cell=cell)
        print(f"section {plane.upper()} at {offset:g}: cut faces are red; parts shown: {parts.names}")
    else:
        views = tuple(v for v in args.views.split(",") if v) or ("iso_back", "bottom")
        contact_sheet(parts, args.out, views=views, cell=cell, title=Path(args.model).stem)
        print(f"views {', '.join(views)}; parts shown: {parts.names}" + (" (exploded)" if args.exploded else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
