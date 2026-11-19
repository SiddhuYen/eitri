"""Tickets and change packages on a combined project: several model files (CAD, mechanism, harness, firmware, pneumatics,
electronics) that are checked on their own and against each other (eitri_suite's cross-domain checks).

A ticket on a combined project covers the whole project, not one file: the cross-domain checks of each file read the others, so
a change to one file can fail a check of another. Its target is every model file with the revision it starts from:

    target: {combined: true, files: [{file: model.py, revision: 12}, {file: elec.py, revision: null}, ...]}

(null: the file is new under this ticket). The plan records every model file's hash, so approval refuses when any file changed
after the plan. The package rebuilds every file of the base and of the result together, in a folder of their own marked as a
combined project, in the workspace's build order, so every cross-domain check runs fresh. Check names in the package are the file
and the check, "wiring.py: overcurrent protection", so the same check name in two files stays two rows.

The refusal rule is the single-file one, over every file: no package when the approval is not valid, a file on disk has no good
build, a fresh build of any file fails, any check of any file FAILS (never waivable), any check is NOT RUN without a person's
waiver, or an acceptance criterion names a check no file runs.

Revision letters: one letter for the whole project (the machine), in tickets/revisions.json under "project"; each lettered
revision records the build of every file.

    <id>-<project>-rev-<B>/
      package.json, report.md, report.html, checks.csv, parameters.csv, bom_diff.csv, source.diff, ticket.yaml  (as single-file)
      model/rev-A/<every file>, model/rev-B/<every file>
      outputs/<file stem>/     each file's regenerated outputs (CAD: STEP/STL/3MF + drawing)
      outputs/build_package/   eitri's build package of Rev B (print files, consolidated BOM, cut list, wiring steps, assembly order)
      images/                  each revision's CAD review image
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .checks import counts, normalise
from .store import Ticket, TicketError, now_iso, tickets_dir, verify_waiver

PROJECT_KEY = "project"          # revisions.json: the combined project's lettered revisions


# ---------------------------------------------------------------------------------------------------------------- the project
def is_combined(project) -> bool:
    try:
        from eitri_suite import is_combined_folder
    except ImportError:
        return False
    return is_combined_folder(project.folder)


def all_model_files(project) -> list[str]:
    """Every model file of the combined project, in build order (blank ones included)."""
    from eitri_suite.workspace import model_files
    return model_files(Path(project.folder))


def _blank(text: str | None) -> bool:
    from eitri_suite.workspace import is_blank
    return text is None or not text.strip() or is_blank(text)


def design_files(project) -> list[str]:
    """The model files that are part of the design: every model file but a blank one (the blank model.py a new combined project
    starts with, when the machine has no CAD)."""
    out = []
    for f in all_model_files(project):
        if not _blank(_read(Path(project.folder) / f)):
            out.append(f)
    return out


def target(project, base) -> dict:
    """A combined ticket's target: every design file with its base revision ("latest": its last good build; "new": none)."""
    if base not in ("latest", None, "new", "none"):
        raise TicketError("a combined project's ticket starts from the latest good build of every file (base \"latest\") or "
                          "from nothing (\"new\"); a single revision number names one file's build, not the project's")
    files = []
    for f in design_files(project):
        rev = None
        if base == "latest":
            last = project.last_ok_revision(model=f)
            rev = last.number if last else None
        files.append({"file": f, "revision": rev})
    return {"combined": True, "files": files}


def qualify(file: str, name: str) -> str:
    return f"{file}: {name}"


def _rows(file: str, data: dict) -> list[dict]:
    from eitri_suite.workspace import is_cross_domain
    out = []
    for r in normalise(data):
        out.append({**r, "name": qualify(file, r["name"]), "check": r["name"], "file": file,
                    "cross_domain": is_cross_domain(r.get("category", ""), r["name"])})
    return out


def last_rows(project) -> list[dict]:
    """Every design file's checks in its last good build, with qualified names ("wiring.py: <check>")."""
    rows: list[dict] = []
    for f in design_files(project):
        last = project.last_ok_revision(model=f)
        data = project.build_result(last) if last else None
        if data:
            rows += _rows(f, data)
    return rows


def resolve_check(project, name: str) -> str:
    """The qualified name of a check a person names: "file.py: check" as it is, or a bare check name that only one file runs."""
    name = str(name).strip()
    rows = last_rows(project)
    if any(r["name"] == name for r in rows):
        return name
    hits = sorted({r["name"] for r in rows if r["check"] == name})
    if len(hits) > 1:
        raise TicketError(f"{name!r} is a check of {len(hits)} files: name it with its file, one of: " + "; ".join(repr(h) for h in hits))
    return hits[0] if hits else name


def check_names_text(project, t: Ticket) -> str:
    """The checks the base builds run, by qualified name (for the agent to map acceptance criteria to)."""
    names = []
    for f, n in t.target_files.items():
        rev = next((h for h in project.revisions_of(f) if h.number == n), None) if n is not None else None
        data = project.build_result(rev) if rev else None
        if data:
            names += [r["name"] for r in _rows(f, data)]
    if not names:
        return ""
    return ("\n\nChecks the base builds run, by name (a criterion may give the file, \"wiring.py: <check>\", or the bare check name "
            "to match it in any file):\n" + "\n".join(f"- {n}" for n in names[:160]) + (f"\n- ... ({len(names) - 160} more)" if len(names) > 160 else ""))


# ---------------------------------------------------------------------------------------------------------------- builds
def _read(p: Path) -> str | None:
    try:
        return Path(p).read_text(encoding="utf-8")
    except OSError:
        return None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _set_sha(sources: dict[str, str]) -> str:
    h = hashlib.sha256()
    for f in sorted(sources):
        h.update(f.encode() + b"\0" + sources[f].encode("utf-8") + b"\0")
    return h.hexdigest()


def _fresh_builds(project, sources: dict[str, str], folder: Path, *, result: bool) -> dict[str, tuple[Any, dict | None]]:
    """Build every file of `sources` together in an empty folder marked as a combined project (so the suite's cross-domain checks
    run, reading these files and nothing else), in the workspace's build order. The result's builds get the project's guards and
    accepted reference parts, as the project's own builds do; the base's are plain (like the single-file package)."""
    from eitri.kernel import build_model
    from eitri_suite.workspace import kind_of, model_files
    folder.mkdir(parents=True, exist_ok=True)
    for f, src in sources.items():
        (folder / f).write_text(src, encoding="utf-8")
    inputs = Path(project.folder) / "inputs"
    if inputs.is_dir():
        shutil.copytree(inputs, folder / "inputs", dirs_exist_ok=True)
    order = model_files(folder)
    (folder / "eitri.json").write_text(json.dumps({"workspace": "combined", "model": order[0] if order else None,
                                                   "revision": 0, "history": []}), encoding="utf-8")
    guards = (project.state.get("guards") or {}) if result else {}
    out: dict[str, tuple[Any, dict | None]] = {}
    for f in order:
        cad = kind_of(sources[f]) == "cad"
        dest = folder / "out" / Path(f).stem
        res = build_model(folder / f, dest, formats=("step", "stl", "3mf") if (cad and result) else (), render=cad,
                          guards=({k: dict(v) for k, v in (guards.get(f) or {}).items()} or None),
                          references=project.references(f) if result else None)
        data = None
        try:
            data = json.loads((dest / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        out[f] = (res, data)
    return out


# ---------------------------------------------------------------------------------------------------------------- letters
def _plan_letters(project, t: Ticket, base_builds: dict[str, int], base_sha: str | None, base_date: str | None):
    from .package import _letter, _letter_index, read_revisions
    revs = read_revisions(project)
    table = [dict(r) for r in revs.get(PROJECT_KEY, []) if r.get("ticket") != t.id]
    frm = None
    if base_builds:
        hit = next((r for r in reversed(table) if r.get("builds") == base_builds or (base_sha and r.get("source_sha256") == base_sha)), None)
        top = max(base_builds.values())
        if hit:
            frm = {"letter": hit["letter"], "build": top, "builds": dict(base_builds), "released": True}
        elif not table:
            frm = {"letter": "A", "build": top, "builds": dict(base_builds), "released": False}
            table.append({"letter": "A", "build": top, "builds": dict(base_builds), "date": base_date or now_iso(), "ticket": None,
                          "source_sha256": base_sha, "description": f"baseline: the project as {t.id} started from (recorded by {t.id})"})
        else:
            frm = {"letter": None, "build": top, "builds": dict(base_builds), "released": False}
    last = max((_letter_index(r["letter"]) for r in table), default=-1)
    return frm, {"letter": _letter(last + 1)}, table


# ---------------------------------------------------------------------------------------------------------------- main
def make_package(project, t: Ticket, *, keep_build: bool = False) -> dict:
    from .package import PackageRefused, _refuse
    folder = Path(project.folder)
    reasons: list[str] = []
    files_b = design_files(project)
    if not files_b:
        raise PackageRefused(["the combined project has no model file with a design in it: nothing to package"])
    hist = project.history
    sources_b: dict[str, str] = {}
    rev_b: dict[str, Any] = {}
    for f in files_b:
        src = _read(folder / f) or ""
        built = [h for h in project.revisions_of(f, hist) if h.ok and _read(folder / h.model) == src]
        if not built:
            reasons.append(f"{f} as it is on disk has no good build: build the project (combined_build, or `eitri build {f}`), then package")
            continue
        sources_b[f], rev_b[f] = src, built[-1]
    sources_a: dict[str, str] = {}
    rev_a: dict[str, Any] = {}
    for f, n in t.target_files.items():
        if n is None:
            continue
        h = next((h for h in project.revisions_of(f, hist) if h.number == n), None)
        src = _read(folder / h.model) if h else None
        if src is None:
            reasons.append(f"the base revision {n} of {f} is gone from revisions/")
            continue
        if not _blank(src):
            sources_a[f], rev_a[f] = src, h
    if reasons:
        raise PackageRefused(reasons)
    if sources_a and sources_a == sources_b:
        raise PackageRefused([f"every model file is the same as at the base: nothing was changed under {t.id}"])
    staging = t.folder / ".package-build"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        return _make(project, t, sources_a, sources_b, rev_a, rev_b, staging, PackageRefused, _refuse)
    finally:
        if not keep_build:
            shutil.rmtree(staging, ignore_errors=True)


def _make(project, t, sources_a, sources_b, rev_a, rev_b, staging, PackageRefused, _refuse) -> dict:
    from eitri.domains import label
    from eitri.update import installed_version
    from eitri_suite.workspace import kind_of, model_files
    from . import package as P
    reasons: list[str] = []
    builds_a = _fresh_builds(project, sources_a, staging / "base", result=False) if sources_a else {}
    for f, (res, data) in builds_a.items():
        if not res.ok or not data:
            reasons.append(f"the base revision {rev_a[f].number} of {f} no longer builds with this eitri together with the other base "
                           f"files ({(res.error or 'no result')[:300]}): the before/after comparison would be wrong")
    builds_b = _fresh_builds(project, sources_b, staging / "result", result=True)
    for f, (res, data) in builds_b.items():
        if not res.ok or not data:
            reasons.append(f"the fresh build of {f} failed: {(res.error or 'no result')[:500]}")
    if reasons:
        _refuse(t, reasons)
    order_b = list(builds_b)
    order_a = list(builds_a)
    kinds_b = {f: kind_of(sources_b[f]) for f in order_b}
    kinds_a = {f: kind_of(sources_a[f]) for f in order_a}
    rows_b = [r for f in order_b for r in _rows(f, builds_b[f][1])]
    rows_a = [r for f in order_a for r in _rows(f, builds_a[f][1])] if builds_a else None

    waivers, stale = [], []
    for w in t.data.get("waivers") or []:
        (waivers if verify_waiver(t, w) else stale).append(w)
    waived = {w["check"]: w for w in waivers}
    for r in rows_b:
        if r["status"] == "fail":
            reasons.append(f"FAIL {r['name']}: {r['measured']}" + (f" (expected {r['expected']})" if r["expected"] else "")
                           + ". A failed check is fixed in the design; it is never waived.")
        elif r["status"] == "not_run" and r["name"] not in waived:
            reasons.append(f"NOT RUN {r['name']}" + (f" ({', '.join(r['inputs_unset'])} not set)" if r["inputs_unset"] else "")
                           + f": set the input so it runs, or a person waives it with `eitri ticket waive {t.id} "
                           f"\"{r['name']}\" --reason \"...\"`")
    for w in stale:
        if any(r["name"] == w["check"] and r["status"] == "not_run" for r in rows_b):
            reasons.append(f"waiver for {w['check']!r} is no longer valid (the ticket changed after it was given, or it does not "
                           "verify); a person waives it again")
    criteria = []
    for a in t.data.get("acceptance") or []:
        row = {"id": a["id"], "text": a["text"], "check": a.get("check"), "human_verify": bool(a.get("human_verify")),
               "matched": [], "status": "human_verify" if a.get("human_verify") else None}
        if a.get("check"):
            hits = match(a["check"], rows_b)
            row["matched"] = [h["name"] for h in hits]
            if not hits:
                reasons.append(f"{a['id']} ({a['text']}) maps to the check {a['check']!r}, which no file's build runs; names they do "
                               f"run are in checks.csv (the ticket's criteria change only with a new approval)")
                row["status"] = "missing"
            elif any(h["status"] == "fail" for h in hits):
                row["status"] = "fail"
            elif all(h["status"] == "not_run" for h in hits):
                row["status"] = "waived" if all(h["name"] in waived for h in hits) else "not_run"
            elif any(h["status"] in ("warn", "not_run") for h in hits):
                row["status"] = "pass_with_warning"
            else:
                row["status"] = "pass"
        criteria.append(row)
    if reasons:
        _refuse(t, reasons, rows_a, rows_b)

    # ---- letters
    base_builds = {f: h.number for f, h in rev_a.items()}
    base_sha = _set_sha(sources_a) if sources_a else None
    base_date = P._iso(max(h.created for h in rev_a.values())) if rev_a else None
    frm, to, table = _plan_letters(project, t, base_builds, base_sha, base_date)
    if frm:
        frm.update({"source_sha256": base_sha, "date": base_date})
    builds_to = {f: h.number for f, h in rev_b.items()}
    to.update({"build": max(builds_to.values()), "builds": builds_to, "source_sha256": _set_sha(sources_b), "date": now_iso()})
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(project.folder).name).strip("-") or "project"
    tickets = Path(t.folder)
    for old in list(tickets.glob(f"{t.id}-*-rev-*")):            # an earlier package of this ticket is replaced
        shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
    out = tickets / f"{t.id}-{slug}-rev-{to['letter']}"
    out.mkdir(parents=True)
    missing: list[dict] = []
    fl = frm["letter"] if frm and frm.get("letter") else (f"build-{frm['build']}" if frm else None)

    # ---- model sources and the diff
    for f, src in sources_b.items():
        (out / "model" / f"rev-{to['letter']}").mkdir(parents=True, exist_ok=True)
        (out / "model" / f"rev-{to['letter']}" / f).write_text(src, encoding="utf-8")
    for f, src in sources_a.items():
        (out / "model" / f"rev-{fl}").mkdir(parents=True, exist_ok=True)
        (out / "model" / f"rev-{fl}" / f).write_text(src, encoding="utf-8")
    all_files = list(dict.fromkeys(order_b + [f for f in order_a if f not in builds_b]))
    file_change = {}
    for f in all_files:
        a, b = sources_a.get(f), sources_b.get(f)
        file_change[f] = ("new" if not sources_a else "added") if a is None else "removed" if b is None else "changed" if a != b else "same"
    if sources_a:
        diff = ""
        for f in all_files:
            a, b = sources_a.get(f), sources_b.get(f)
            if a == b:
                continue
            diff += "".join(difflib.unified_diff((a or "").splitlines(keepends=True), (b or "").splitlines(keepends=True),
                                                 f"Rev {fl}/{f}" if a is not None else "/dev/null",
                                                 f"Rev {to['letter']}/{f}" if b is not None else "/dev/null"))
        (out / "source.diff").write_text(diff, encoding="utf-8")

    # ---- images and outputs
    (out / "images").mkdir()
    images: dict[str, Path] = {}
    for tag, builds in ((fl, builds_a), (to["letter"], builds_b)):
        for f, (res, _data) in builds.items():
            if res is not None and res.image and Path(res.image).is_file():
                dst = out / "images" / f"rev-{tag}-{Path(f).stem}.png"
                shutil.copy2(res.image, dst)
                images.setdefault(tag, dst)                   # the report shows the first CAD file's image of each revision
    (out / "outputs").mkdir()
    for f in order_b:
        res, data = builds_b[f]
        dest = out / "outputs" / Path(f).stem
        dest.mkdir()
        for k, p in (data.get("outputs") or {}).items():
            if k in P.SKIP_OUTPUTS or k == "png" or not Path(p).is_file():
                continue
            shutil.copy2(p, dest / Path(p).name)
        if kinds_b[f] == "cad":
            for fmt in ("step", "stl", "3mf"):
                if fmt not in (data.get("outputs") or {}):
                    missing.append({"what": f"{f}: {fmt.upper()}", "why": "the build did not write it"})
            missing += [{**m, "what": f"{f}: {m['what']}"} for m in P._cad_drawing(staging / "result" / f, dest, Path(f).stem)]
    missing += _build_package(staging / "result", model_files(staging / "result"), out / "outputs")

    # ---- diffs
    params = None
    boms = None
    if builds_a:
        params, boms = [], []
        for f in all_files:
            da = (builds_a.get(f) or (None, None))[1]
            db = (builds_b.get(f) or (None, None))[1]
            params += [{"file": f, **x} for x in P.params_diff((da or {}).get("params") if da else None,
                                                              (db or {}).get("params") or {} if db else {})]
            ta = P.bom_tables(kinds_a[f], da) if da else {}
            tb = P.bom_tables(kinds_b[f], db) if db else {}
            boms += [{**r, "table": f"{f} {r['table']}"} for r in P.bom_diff(ta, tb)]
    checks = P.check_table(rows_a, rows_b)
    ref = {r["name"]: r for r in (rows_a or []) + rows_b}
    for c in checks:
        r = ref[c["name"]]
        c.update({"file": r["file"], "check": r["check"], "cross_domain": r["cross_domain"]})
    removed = [c for c in checks if c["change"] == "removed"]
    plan = t.data.get("plan") or {}
    planned = {(x.get("file") or None, str(x.get("name"))) for x in plan.get("parameters") or []}
    unplanned = [f"{x['file']}: {x['name']}" for x in params or [] if x["change"] != "same"
                 and (x["file"], x["name"]) not in planned and (None, x["name"]) not in planned]
    plan_files = set(plan.get("files") or [])
    unplanned += [f"{f} ({file_change[f]}; not among the plan's files)" for f in all_files
                  if file_change[f] in ("changed", "added", "removed") and f not in plan_files]

    # ---- not checked
    cross = [r for r in rows_b if r["cross_domain"]]
    not_checked = []
    for n in plan.get("not_checked") or []:
        not_checked.append({"kind": "plan", "what": n, "why": "the approved plan says eitri does not check it"})
    for a in criteria:
        if a["human_verify"]:
            not_checked.append({"kind": "human_verify", "what": f"{a['id']}: {a['text']}", "why": "not machine-checkable: a person verifies it"})
    for w in waivers:
        if any(r["name"] == w["check"] and r["status"] == "not_run" for r in rows_b):
            not_checked.append({"kind": "waived", "what": w["check"], "why": f"did not run; waived by {w.get('by')}: {w['reason']}"})
    for c in removed:
        not_checked.append({"kind": "removed_check", "what": c["name"],
                            "why": f"ran on Rev {fl} ({c['from']['status']}) and does not run on Rev {to['letter']} (renamed with the "
                                   "part it is about, dropped, or its file was removed: a person checks which)"})
    for kind in dict.fromkeys(kinds_b.values()):
        for n in P.NOT_COVERED.get(kind, []):
            if n.startswith("Fit with parts outside this model file"):
                n = "Fit with parts outside this project (the rest of the machine it goes into)."
            not_checked.append({"kind": "domain", "what": n, "why": f"outside what eitri's {label(kind) if kind != 'cad' else 'CAD'} checks cover"})
    not_checked.append({"kind": "scope", "what": f"Interactions between the files other than the {len(cross)} cross-domain checks listed "
                        "(e.g. electrical noise between the harness and the circuit, heat from one part on another, wear between moving "
                        "parts): eitri checks only the links the files declare (routes, mating faces, coupled links, pins, supplies).",
                        "why": "outside what eitri's cross-domain checks cover"})
    not_checked.append({"kind": "scope", "what": P.ALWAYS_NOT_COVERED[1], "why": "outside what an eitri package covers"})

    # ---- package.json
    ap = t.data["approval"]
    kinds_label = " + ".join(dict.fromkeys("CAD" if k == "cad" else label(k) for k in kinds_b.values()))
    affected = [f for f in all_files if file_change[f] != "same"] + sorted({f"{r['table']}: {r['key']}" for r in boms or []})
    table.append({"letter": to["letter"], "build": to["build"], "builds": builds_to, "date": to["date"], "ticket": t.id,
                  "source_sha256": to["source_sha256"], "description": t.data.get("title")})
    per_file = []
    for f in all_files:
        rb = [r for r in rows_b if r["file"] == f]
        per_file.append({"file": f, "kind": kinds_b.get(f) or kinds_a.get(f), "change": file_change[f],
                         "from_build": base_builds.get(f), "to_build": builds_to.get(f),
                         "checks": counts(rb) if f in builds_b else None,
                         "cross_domain_checks": sum(1 for r in rb if r["cross_domain"])})
    pkg: dict[str, Any] = {
        "schema": P.SCHEMA,
        "generated": now_iso(),
        "eitri_version": installed_version(),
        "ticket": {k: t.data.get(k) for k in ("id", "title", "customer", "request", "constraints", "created", "created_by")},
        "design": {"project": Path(project.folder).name, "file": ", ".join(order_b), "kind": "combined",
                   "label": f"combined project: {kinds_label}", "new_design": not sources_a, "combined": True, "files": per_file},
        "revisions": {"from": frm, "to": to},
        "revision_table": table,
        "approval": {k: ap.get(k) for k in ("by", "at", "method", "tty", "note", "despite_changes", "content_sha256", "signer")}
                    | {"verified": True},
        "plan": {k: plan.get(k) for k in ("summary", "files", "parameters", "checks_affected", "not_checked", "open_questions", "written", "written_by")},
        "waivers": [{k: w.get(k) for k in ("check", "reason", "by", "at", "method")} for w in waivers],
        "acceptance": criteria,
        "checks": {"summary": {"from": counts(rows_a) if rows_a is not None else None, "to": counts(rows_b),
                               "cross_domain": {"to": len(cross)}}, "rows": checks},
        "removed_checks": [c["name"] for c in removed],
        "unplanned_changes": unplanned,
        "parameters": params,
        "bom": boms,
        "not_checked": not_checked,
        "change_order": {
            "number": "", "title": t.data.get("title"), "ticket": t.id,
            "reason": t.data.get("request"),
            "description": plan.get("summary", "") + ("\nParameters: " + "; ".join(f"{p['file']} {p['name']} {p['from']} -> {p['to']}"
                                                                                     for p in params if p["change"] != "same") if params else ""),
            "affected_items": affected,
            "from_revision": (frm or {}).get("letter"), "to_revision": to["letter"],
            "originator": t.data.get("created_by"), "date": now_iso()[:10],
            "disposition": "", "effectivity": "", "approved_by": "", "approved_date": "",
        },
        "outputs_missing": missing,
        "files": [],
    }
    return P._finish(project, t, out, pkg, images, table, PROJECT_KEY,
                     {"from_build": (frm or {}).get("build"), "to_build": to["build"], "from_builds": base_builds or None,
                      "to_builds": builds_to}, rows_b)


def match(pattern: str, rows: list[dict]) -> list[dict]:
    """A criterion's check: matched on the qualified name ("wiring.py: <check>") first, else on the bare check name in any file."""
    from .package import _match
    hits = _match(pattern, rows)
    if hits:
        return hits
    bare = [{**r, "name": r["check"]} for r in rows]
    names = {id(b): r for b, r in zip(bare, rows)}
    return [names[id(b)] for b in _match(pattern, bare)]


def _build_package(folder: Path, order: list[str], outputs: Path) -> list[dict]:
    """eitri's build package of the result (print files, consolidated BOM, cut list, wiring steps, assembly order), written by the
    suite's own job on the fresh result folder; listed as missing when it cannot be made, never silently skipped."""
    from eitri_suite.workspace import ToolError, run_suite_job
    work = folder.parent / "build-package-job"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        res = run_suite_job(folder, order[0], "package", work)
    except ToolError as exc:
        return [{"what": "build package (print files, BOM, cut list, wiring steps, assembly order)", "why": str(exc)[:300]}]
    if not res.get("ok"):
        return [{"what": "build package (print files, BOM, cut list, wiring steps, assembly order)",
                 "why": str(res.get("refused") or "refused")[:300]}]
    shutil.move(str(work / "build_package"), str(outputs / "build_package"))
    return []
