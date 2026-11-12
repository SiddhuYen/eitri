"""`eitri ticket new|show|list|plan|approve|waive|close` and `eitri package`: the person's side of a ticket.

approve, waive and close are only here (no agent tool has them). They refuse inside an agent's process and without an
interactive terminal, and ask the person to type back a one-time code shown only on their terminal."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def add_parsers(sub) -> None:
    t = sub.add_parser("ticket", help="tickets: a request in, a plan a person approves, a checked change package out")
    ts = t.add_subparsers(dest="ticket_cmd", required=True)

    def proj(p):
        p.add_argument("-C", "--project", default=".", help="project folder (default: the current folder)")

    n = ts.add_parser("new", help="open a ticket on the project (it becomes the active one)")
    proj(n)
    n.add_argument("--title", required=True)
    n.add_argument("--request", default=None, help="the request in the customer's words (or --request-file)")
    n.add_argument("--request-file", default=None, help="read the request from this file ('-' for stdin)")
    n.add_argument("--criterion", action="append", default=[], help='an acceptance criterion: "text => check name" (a name or glob) '
                   'or "text => human" when no check proves it; repeat for each')
    n.add_argument("--constraint", action="append", default=[], help="a constraint; repeat for each")
    n.add_argument("--file", default=None, help="the model file the ticket changes (default: the project's current one)")
    n.add_argument("--base", default="latest", help='the base revision: "latest" (default), a revision number, or "new"')
    n.add_argument("--customer", default="")
    n.add_argument("--by", default=None, help="who opened it (default: your user name)")

    s = ts.add_parser("show", help="show a ticket (default: the active one)")
    proj(s)
    s.add_argument("id", nargs="?", default="")
    s.add_argument("--json", action="store_true")

    ls = ts.add_parser("list", help="every ticket of the project")
    proj(ls)

    pl = ts.add_parser("plan", help="write the plan from a YAML/JSON file (the agent normally does this with ticket_plan)")
    proj(pl)
    pl.add_argument("id")
    pl.add_argument("plan_file", help="YAML or JSON with summary, files, parameters, checks_affected, not_checked, open_questions")

    a = ts.add_parser("approve", help="approve the plan (a person, in their own terminal; asks for a one-time code)")
    proj(a)
    a.add_argument("id")
    a.add_argument("--by", default=None, help="your name as it should appear on the package (default: your user name)")
    a.add_argument("--note", default="", help="answers to the plan's open questions, conditions")
    a.add_argument("--despite-changes", action="store_true", help="approve although the model file changed since the plan "
                   "was written (recorded on the package's first page)")

    w = ts.add_parser("waive", help="waive one check that did NOT RUN, with a written reason (never a failed check)")
    proj(w)
    w.add_argument("id")
    w.add_argument("check", help="the check's name exactly as the report gives it")
    w.add_argument("--reason", required=True)
    w.add_argument("--by", default=None)

    c = ts.add_parser("close", help="end a ticket (done), or --cancel it; the project is no longer gated by it")
    proj(c)
    c.add_argument("id")
    c.add_argument("--cancel", action="store_true")
    c.add_argument("--note", default="")
    c.add_argument("--by", default=None)

    pk = sub.add_parser("package", help="the change package of an approved ticket: fresh rebuilds, diffs, checks, report, zip")
    pk.add_argument("id", nargs="?", default="", help="ticket id (default: the active ticket)")
    proj(pk)
    pk.add_argument("--json", action="store_true", help="print package.json instead of the summary")


def _project(path: str):
    from ..project import Project
    folder = Path(path).expanduser()
    if not (folder / "eitri.json").is_file():
        raise SystemExit(f"{folder} is not an eitri project (no eitri.json); give it with -C <folder>")
    return Project(folder)


def _read_request(args) -> str:
    if args.request_file:
        return sys.stdin.read() if args.request_file == "-" else Path(args.request_file).read_text(encoding="utf-8")
    return args.request or ""


def ticket_main(args) -> int:
    from . import store as S
    try:
        project = _project(args.project)
        cmd = args.ticket_cmd
        if cmd == "new":
            t = S.new_ticket(project, args.title, _read_request(args), constraints=args.constraint, acceptance=args.criterion,
                             file=args.file, base=args.base, customer=args.customer, created_by=args.by or S._who(None)["by"])
            print(S.describe(project, t))
            return 0
        if cmd == "show":
            t = S.load(project, args.id) if args.id else S.active(project)
            if t is None:
                print("no active ticket (eitri ticket list shows them all)")
                return 1
            print(json.dumps(t.data, indent=1, default=str) if args.json else S.describe(project, t))
            return 0
        if cmd == "list":
            rows = S.list_tickets(project)
            act = S.active(project)
            for t in rows:
                print(f"{t.id}  {t.status:<18} {t.data.get('title')}" + ("   (active)" if act and act.id == t.id else ""))
            if not rows:
                print("no tickets")
            return 0
        if cmd == "plan":
            import yaml
            plan = yaml.safe_load(Path(args.plan_file).read_text(encoding="utf-8")) or {}
            acc = plan.pop("acceptance", None) if isinstance(plan, dict) else None
            t = S.set_plan(project, args.id, plan, acceptance=acc, by=S._who(None)["by"])
            print(S.describe(project, t))
            return 0
        if cmd == "approve":
            t = S.approve(project, args.id, by=args.by, note=args.note, despite_changes=args.despite_changes)
            print(f"approved {t.id} as {t.data['approval']['by']} at {t.data['approval']['at']}")
            return 0
        if cmd == "waive":
            t = S.waive(project, args.id, args.check, args.reason, by=args.by)
            print(f"waived {args.check!r} on {t.id}; the reason is printed on the package's first page")
            return 0
        if cmd == "close":
            t = S.close(project, args.id, by=args.by, cancel=args.cancel, note=args.note)
            print(f"{t.id} is {t.status}")
            return 0
    except S.TicketError as exc:
        print(f"eitri ticket {args.ticket_cmd}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nnothing was recorded", file=sys.stderr)
        return 130
    return 2


def package_main(args) -> int:
    from . import store as S
    from .package import PackageRefused, make_package
    try:
        project = _project(args.project)
        tid = args.id or (S.active(project).id if S.active(project) else "")
        if not tid:
            print("no active ticket: give the ticket id", file=sys.stderr)
            return 1
        pkg = make_package(project, tid)
    except PackageRefused as exc:
        print("NO CHANGE PACKAGE. Every reason:", file=sys.stderr)
        for r in exc.reasons:
            print(f"  - {r}", file=sys.stderr)
        return 1
    except S.TicketError as exc:
        print(f"eitri package: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({k: v for k, v in pkg.items()}, indent=1, default=str))
        return 0
    s = pkg["checks"]["summary"]["to"]
    fr = (pkg["revisions"]["from"] or {}).get("letter")
    print(f"{pkg['ticket']['id']}: {pkg['design']['file']} " + (f"Rev {fr} -> " if fr else "") + f"Rev {pkg['revisions']['to']['letter']}")
    print(f"checks: {s['pass']} pass, {s['warn']} warn, 0 fail, {s['not_run']} not run" + (" (waived)" if s["not_run"] else ""))
    for w in pkg["waivers"]:
        print(f"waiver: {w['check']}: {w['reason']}")
    print(f"not checked: {len(pkg['not_checked'])} item(s) (report.md, 'Not checked')")
    for m in pkg["outputs_missing"]:
        print(f"not produced: {m['what']} ({m['why']})")
    print(f"package: {pkg['folder']}\nzip:     {pkg['zip']}")
    return 0
