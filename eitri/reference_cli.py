"""`eitri reference ...`: a person's control over a project's reference parts (customer STEP files).

    eitri reference list   <project>                       what is accepted, and whether each file still matches
    eitri reference add    <project> <file.step> [--name]  copy a customer file into inputs/ and accept it
    eitri reference accept <project> inputs/<file.step>    accept the file as it is now (the customer sent a new revision)
    eitri reference remove <project> inputs/<file.step>    forget one (the customer part is no longer in the job)

These are deliberately command-line only: the agent has no tool that accepts or removes a reference, so it can never
make a changed customer file pass, or drop a reference to get rid of its checks. add, accept and remove are person-only
with the same barriers as `eitri ticket approve` (eitri.tickets.store.person_record): refused in an agent's process, a
one-time code typed back on the person's terminal, and a signed record in eitri.json. Nothing here parses the STEP file
(that happens inside a build, where model code runs); it is only size-capped, pre-scanned and hashed.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def add_parser(sub) -> None:
    r = sub.add_parser("reference", help="the customer STEP files a design is built around (read-only reference parts): "
                                         "list, add, accept a new revision, remove")
    rs = r.add_subparsers(dest="ref_cmd", required=True)
    ls = rs.add_parser("list", help="the accepted reference files and whether each still matches its recorded SHA-256")
    ls.add_argument("project")
    a = rs.add_parser("add", help="copy a STEP file into the project's inputs/ folder and accept it")
    a.add_argument("project")
    a.add_argument("file")
    a.add_argument("--name", default=None, help="the reference part's name (default: the file name)")
    a.add_argument("--model", default=None, help="model file to accept it for (default: the project's current one)")
    a.add_argument("--by", default=None, help="your name, for the record (default: your login name)")
    ac = rs.add_parser("accept", help="accept the file now at inputs/<file> (a new revision from the customer)")
    ac.add_argument("project")
    ac.add_argument("path")
    ac.add_argument("--model", default=None)
    ac.add_argument("--by", default=None, help="your name, for the record (default: your login name)")
    rm = rs.add_parser("remove", help="forget an accepted reference (its reference_step() can then be taken out of the model)")
    rm.add_argument("project")
    rm.add_argument("path")
    rm.add_argument("--model", default=None)
    rm.add_argument("--by", default=None, help="your name, for the record (default: your login name)")


def _project(folder: str):
    from .project import STATE_FILE, Project
    f = Path(folder)
    if not (f / STATE_FILE).is_file():
        raise SystemExit(f"{f} is not an eitri project (no {STATE_FILE}); build it once first, or give the project folder")
    return Project(f)


def main(args: argparse.Namespace) -> int:
    from .reference import ReferenceError, STATE_KEY, prescan, resolve_path, sha256_file
    from .tickets.store import TicketError
    try:
        pr = _project(args.project)
        if args.ref_cmd == "list":
            allrefs = pr.state.get(STATE_KEY) or {}
            if not any(allrefs.values()):
                print("no reference files accepted in this project")
                return 0
            bad = 0
            for model, refs in sorted(allrefs.items()):
                for rel, rec in sorted(refs.items()):
                    try:
                        target, _ = resolve_path(rel, pr.folder)
                        now, _n = sha256_file(target)
                        status = "ok" if now == rec.get("sha256") else f"CHANGED (now {now[:16]}…)"
                    except ReferenceError as exc:
                        status = f"MISSING ({exc})"
                    how, signed = pr.reference_acceptance(rel, model)
                    if not signed:
                        status += "; the acceptance record's signature does NOT verify (edited by hand, or accepted on another machine)"
                    bad += status != "ok"
                    print(f"{model}: {rec.get('name') or rel}  {rel}  sha256 {str(rec.get('sha256'))[:16]}…  "
                          f"{rec.get('bytes')} bytes  mounts {rec.get('mounts', 0)}  accepted: {how}  [{status}]")
            return 1 if bad else 0
        if args.ref_cmd == "add":
            src = Path(args.file)
            if not src.is_file():
                print(f"no such file: {src}", file=sys.stderr)
                return 2
            if src.suffix.lower() not in (".step", ".stp"):
                print("a reference file must be a .step/.stp file", file=sys.stderr)
                return 2
            data = src.read_bytes()
            prescan(data, src.name)
            stem = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in (args.name or src.stem)).strip("_") or "reference"
            dest = pr.folder / "inputs" / f"{stem}{src.suffix.lower()}"
            if dest.exists() and dest.read_bytes() != data:
                print(f"{dest.relative_to(pr.folder)} already exists with other contents; to take a new revision of it, "
                      f"copy the file over it yourself and run `eitri reference accept {args.project} {dest.relative_to(pr.folder)}`",
                      file=sys.stderr)
                return 1
            from .tickets.store import require_person
            require_person("eitri reference add")              # before the copy: a refused add leaves inputs/ as it was
            dest.parent.mkdir(parents=True, exist_ok=True)
            copied = not dest.exists()
            if copied:
                shutil.copyfile(src, dest)
            rel = str(dest.relative_to(pr.folder)).replace("\\", "/")
            try:
                _old, sha = pr.accept_reference(rel, model=args.model, by=args.by, action="eitri reference add")
            except TicketError:
                if copied:
                    dest.unlink(missing_ok=True)
                raise
            print(f"accepted {rel} (sha256 {sha}) for {args.model or pr.model_path.name}")
            print(f'use it in the model:  {stem} = reference_step("{rel}", name="{stem}")')
            return 0
        if args.ref_cmd == "accept":
            old, sha = pr.accept_reference(args.path, model=args.model, by=args.by)
            if old == sha:
                print(f"{args.path} is unchanged (sha256 {sha}); nothing to accept")
            else:
                print(f"accepted {args.path}: sha256 {old or '(new)'} -> {sha}")
                print("rebuild the project: its checks now run against the new file")
            return 0
        if args.ref_cmd == "remove":
            if pr.remove_reference(args.path, model=args.model, by=args.by):
                print(f"forgot {args.path}; take its reference_step(...) out of the model file (the file itself is kept)")
                return 0
            print(f"{args.path} is not an accepted reference of {args.model or pr.model_path.name}", file=sys.stderr)
            return 1
    except ReferenceError as exc:
        print(f"not accepted: {exc}", file=sys.stderr)
        return 1
    except TicketError as exc:                       # NotAPerson (an agent's process, no terminal) or a wrong code
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 2
