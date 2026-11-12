"""The change package: a fresh rebuild of the base revision and of the result, compared, checked, and written as a folder and
a zip a person reviews and signs off.

    <id>-<stem>-rev-<B>/
      package.json         machine-readable (schema eitri.package/1, package.schema.json next to this file)
      report.md            the human report; report.html the same, self-contained (images inlined)
      checks.csv           before/after check table: status, measured, expected, source
      parameters.csv       parameter diff                 (not for a new design)
      bom_diff.csv         BOM / parts diff               (not for a new design)
      source.diff          unified diff of the model file (not for a new design)
      model/rev-A/, model/rev-B/   the model file at each revision
      outputs/             the regenerated outputs of Rev B: STEP/STL/3MF + drawing (CAD), or the domain's files
      images/              each revision's review image
      ticket.yaml          the ticket as approved

Refusal rule: no package when the approval is not valid, a fresh build fails, any check of the result FAILS (never waivable),
any check is NOT RUN without a person's waiver, or an acceptance criterion names a check the build does not run.
"""
from __future__ import annotations

import base64
import csv
import difflib
import fnmatch
import hashlib
import html
import io
import json
import shutil
from pathlib import Path
from typing import Any

from .checks import counts, normalise
from .store import (Ticket, TicketError, content_sha256, file_sha256, load, now_iso, tickets_dir, verify_approval,
                    verify_waiver)

SCHEMA = "eitri.package/1"
# revision letters per ASME Y14.35's rule: I, O, Q, S, X and Z are not used (they read as numbers or as each other)
LETTERS = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in "IOQSXZ"]
SKIP_OUTPUTS = ("view_glb", "topology", "progress")
# what each domain's checks do not cover: printed in every package's "Not checked" section
NOT_COVERED = {
    "cad": ["Strength, stiffness and fatigue: no stress or deflection analysis (no FEA).",
            "Tolerances, fits and GD&T: the model is nominal geometry.",
            "Manufacturability beyond the wall and edge findings (tool access, draft, print support, sheet-metal bend relief).",
            "Material choice and properties, surface finish, fastener torque and thread engagement.",
            "Fit with parts outside this model file (mating parts, the rest of the machine)."],
    "mechanism": ["Dynamic and inertial loads (acceleration, impacts): torque is graded statically at the worst pose.",
                  "Backlash, compliance and deflection of links, bearings and shafts; fastener and bracket strength.",
                  "Servo heating over a duty cycle and supply voltage sag under load.",
                  "Collisions between links other than the base-plane check."],
    "harness": ["Crimp and solder quality, strain relief, routing and chafe points in the machine.",
                "EMC, shielding and noise coupling.", "Connector mating cycles and environmental sealing."],
    "pneumatics": ["Cushioning and end-of-stroke impact, valve response time, dynamic pressure transients.",
                   "Leak rates, tube bend radius and fitting compatibility on the installed machine.",
                   "Safety functions (ISO 13849 performance level, lockout, exhaust on e-stop)."],
    "firmware": ["Runtime behaviour: nothing is run or simulated on hardware; eitri never uploads to a board.",
                 "Timing, interrupt load, power-up and brown-out states, behaviour under real sensor noise."],
    "electronics": ["Breadboard and module level only: no PCB layout, trace width, creepage or clearance.",
                    "EMC, ESD and transients beyond the listed checks; inrush and start-up currents unless declared.",
                    "Thermal conditions inside an enclosure; component tolerances and ageing."],
}
ALWAYS_NOT_COVERED = ["Cross-domain interaction with other model files (servo torque against the CAD arm, current against the "
                      "wiring...): those checks run in the eitri.io combined workspace, not in this package.",
                      "Physical build and test: every result here is computed; nothing was built or measured."]


class PackageRefused(TicketError):
    """No package: `reasons` says every check or condition that stops it."""

    def __init__(self, reasons: list[str], report: str | None = None):
        self.reasons = list(reasons)
        self.report = report
        super().__init__("No change package: " + " | ".join(self.reasons))


# ---------------------------------------------------------------------------------------------------------------- builds
def _fresh_build(project, source: str, file: str, folder: Path, guards: dict | None, cad: bool):
    """Build `source` as `file` in an empty folder (with the project's inputs/), out of process like every build."""
    from ..kernel import build_model
    folder.mkdir(parents=True, exist_ok=True)
    model = folder / file
    model.write_text(source, encoding="utf-8")
    inputs = Path(project.folder) / "inputs"
    if inputs.is_dir():
        shutil.copytree(inputs, folder / "inputs", dirs_exist_ok=True)
    res = build_model(model, folder / "out", formats=("step", "stl", "3mf") if cad else (), render=True, guards=guards or None)
    data = None
    try:
        data = json.loads((folder / "out" / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return res, data


def _kind(source: str) -> str:
    from ..domains import kind_of_source
    return kind_of_source(source)


# ---------------------------------------------------------------------------------------------------------------- letters
def _letter(i: int) -> str:
    n = len(LETTERS)
    return LETTERS[i] if i < n else _letter(i // n - 1) + LETTERS[i % n]


def _letter_index(s: str) -> int:
    i = 0
    for ch in s:
        i = i * len(LETTERS) + LETTERS.index(ch) + 1
    return i - 1


def _revisions_path(project) -> Path:
    return tickets_dir(project) / "revisions.json"


def read_revisions(project) -> dict:
    try:
        return json.loads(_revisions_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": "eitri.revisions/1", "files": {}}


def _plan_letters(project, t: Ticket, file: str, base_build: int | None, base_sha: str | None,
                  base_date: str | None = None) -> tuple[dict | None, dict, list]:
    """(from, to, the table after this package): from/to are {letter, build, ...}; from None for a new design."""
    revs = read_revisions(project)
    table = [dict(r) for r in revs.get("files", {}).get(file, []) if r.get("ticket") != t.id]
    frm = None
    if base_build is not None:
        hit = next((r for r in reversed(table) if r.get("build") == base_build or (base_sha and r.get("source_sha256") == base_sha)), None)
        if hit:
            frm = {"letter": hit["letter"], "build": base_build, "released": True}
        elif not table:
            frm = {"letter": "A", "build": base_build, "released": False}
            table.append({"letter": "A", "build": base_build, "date": base_date or now_iso(), "ticket": None, "source_sha256": base_sha,
                          "description": f"baseline: the revision {t.id} started from (recorded by {t.id})"})
        else:
            frm = {"letter": None, "build": base_build, "released": False}
    last = max((_letter_index(r["letter"]) for r in table), default=-1)
    to = {"letter": _letter(last + 1)}
    return frm, to, table


# ---------------------------------------------------------------------------------------------------------------- diffs
def params_diff(a: dict | None, b: dict) -> list[dict]:
    a = a or {}
    rows = []
    for k in list(a) + [k for k in b if k not in a]:
        x, y = a.get(k), b.get(k)
        change = "added" if k not in a else "removed" if k not in b else "changed" if x != y else "same"
        rows.append({"name": k, "from": x, "to": y, "change": change})
    return rows


def _cad_tables(data: dict) -> dict[str, list[dict]]:
    rep = (data or {}).get("report") or {}
    rows = []
    for p in rep.get("parts") or []:
        holes = {}
        for h in p.get("holes") or []:
            key = f"Ø{h.get('diameter'):g}" + (" through" if h.get("through") else f" x {h.get('depth'):g} deep")
            holes[key] = holes.get(key, 0) + int(h.get("count") or 1)
        centres = sorted(tuple(round(c, 2) for c in (h.get("center") or [])) for h in p.get("holes") or [])
        rows.append({"name": p.get("name"), "material": p.get("material") or "", "volume_mm3": p.get("volume"),
                     "size_mm": "x".join(f"{s:g}" for s in p.get("size") or []),
                     "holes": ", ".join(f"{n}x {k}" for k, n in holes.items()),
                     "hole_centres": "; ".join("(" + ", ".join(f"{v:g}" for v in c) + ")" for c in centres)})
    tables = {"parts": rows}
    if rep.get("bom"):
        tables["bom"] = [{"name": r.get("name"), "qty": r.get("qty"), "material": r.get("material"), "volume_mm3": r.get("volume"),
                          "parts": ";".join(r.get("parts") or [])} for r in rep["bom"]]
    return tables


def _domain_tables(kind: str, data: dict) -> dict[str, list[dict]]:
    from ..domains import module
    keys = (getattr(module(kind), "BOM_KEYS", None) or {}).get("files") or ()
    outs = (data or {}).get("outputs") or {}
    tables = {}
    for k in keys:
        path = outs.get(k)
        if not path or not str(path).endswith(".csv"):
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        tables[k.replace("_csv", "") if k != "csv" else "bom"] = [{c: _num(v) for c, v in r.items()} for r in csv.DictReader(io.StringIO(text))]
    return tables


def _num(v: str) -> str:
    """A CSV number without float noise (0.36000000000000004 -> 0.36); text is left as it is."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return v if v.strip().lower() in ("nan", "inf", "-inf", "true", "false") else f"{f:.6g}"


def bom_tables(kind: str, data: dict) -> dict[str, list[dict]]:
    return _cad_tables(data) if kind == "cad" else _domain_tables(kind, data)


_KEYS = ("refs", "ref", "name", "joint", "net", "wire", "description", "item")


def _key_of(*tables: list[dict]) -> str | None:
    """The first identifying column present in every row and unique within each table."""
    rows = [r for t in tables for r in t]
    if not rows:
        return None
    for k in _KEYS:
        if all(k in r for r in rows) and all(len({r.get(k) for r in t}) == len(t) for t in tables):
            return k
    return None


def bom_diff(a: dict[str, list[dict]], b: dict[str, list[dict]]) -> list[dict]:
    """Row changes per table: {table, key, change: added | removed | changed, from, to, fields}; unchanged rows are left out."""
    out = []
    for table in list(a) + [t for t in b if t not in a]:
        ra, rb = a.get(table) or [], b.get(table) or []
        key = _key_of(ra, rb)
        ident = (lambda r: str(r.get(key))) if key else (lambda r: json.dumps(r, sort_keys=True, default=str))
        ma, mb = {ident(r): r for r in ra}, {ident(r): r for r in rb}
        for k in list(ma) + [k for k in mb if k not in ma]:
            x, y = ma.get(k), mb.get(k)
            if x is not None and y is not None:
                fields = [f for f in dict.fromkeys(list(x) + list(y)) if str(x.get(f)) != str(y.get(f))]
                if fields:
                    out.append({"table": table, "key": k, "change": "changed", "from": x, "to": y, "fields": fields})
            elif x is not None:
                out.append({"table": table, "key": k if key else (x.get("name") or x.get("item") or k[:60]), "change": "removed", "from": x, "to": None, "fields": []})
            else:
                out.append({"table": table, "key": k if key else (y.get("name") or y.get("item") or k[:60]), "change": "added", "from": None, "to": y, "fields": []})
    return out


def check_table(before: list[dict] | None, after: list[dict]) -> list[dict]:
    rows = []
    bmap = {r["name"]: r for r in before or []}
    amap = {r["name"]: r for r in after}
    for name in list(amap) + [n for n in bmap if n not in amap]:
        x, y = bmap.get(name), amap.get(name)
        if before is None:
            change = "new"
        elif x is None:
            change = "added"
        elif y is None:
            change = "removed"
        elif x["status"] != y["status"]:
            change = "status"
        elif (x["measured"], x["expected"]) != (y["measured"], y["expected"]):
            change = "value"
        else:
            change = "same"
        ref = y or x
        rows.append({"name": name, "category": ref["category"], "source": ref["source"], "sources": ref.get("sources") or [],
                     "rule": ref.get("rule") or "", "change": change,
                     "from": None if x is None else {k: x[k] for k in ("status", "measured", "expected", "inputs_unset")},
                     "to": None if y is None else {k: y[k] for k in ("status", "measured", "expected", "inputs_unset")}})
    return rows


def _match(pattern: str, rows: list[dict]) -> list[dict]:
    pat = " ".join(pattern.lower().split())
    glob = any(ch in pat for ch in "*?[")
    return [r for r in rows if (fnmatch.fnmatchcase(" ".join(r["name"].lower().split()), pat) if glob
                                else " ".join(r["name"].lower().split()) == pat)]


# ---------------------------------------------------------------------------------------------------------------- main
def make_package(project, tid: str | int, *, keep_build: bool = False) -> dict:
    """Build the change package for an approved ticket, or raise PackageRefused with every reason. Returns package.json's
    data with "folder" and "zip" added."""
    from ..domains import label
    t = load(project, tid)
    ok, why = verify_approval(t)
    if not ok:
        raise PackageRefused([why + (f"; a person approves it with `eitri ticket approve {t.id}`" if t.status != "draft" else
                                     "; the agent writes the plan, then a person approves it")])
    folder = Path(project.folder)
    file = t.target_file
    path = folder / file
    if not path.is_file():
        raise PackageRefused([f"{file} does not exist in the project"])
    src_b = path.read_text(encoding="utf-8")
    revs = project.revisions_of(file)
    built = [h for h in revs if h.ok and _read(folder / h.model) == src_b]
    if not built:
        raise PackageRefused([f"{file} as it is on disk has no good build: build it (rebuild), then package"])
    rev_b = built[-1]
    base_n = t.base_revision
    rev_a = next((h for h in revs if h.number == base_n), None) if base_n is not None else None
    if base_n is not None and rev_a is None:
        raise PackageRefused([f"the base revision {base_n} of {file} is gone from revisions/"])
    src_a = _read(folder / rev_a.model) if rev_a else None
    if src_a is not None and src_a == src_b:
        raise PackageRefused([f"{file} is the same as the base revision {base_n}: nothing was changed under {t.id}"])
    kind = _kind(src_b)
    cad = kind == "cad"
    staging = t.folder / ".package-build"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        return _make(project, t, file, kind, cad, src_a, src_b, rev_a, rev_b, staging, label)
    finally:
        if not keep_build:
            shutil.rmtree(staging, ignore_errors=True)


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _make(project, t, file, kind, cad, src_a, src_b, rev_a, rev_b, staging, label) -> dict:
    from ..update import installed_version
    reasons: list[str] = []
    res_a = data_a = None
    if src_a is not None:
        res_a, data_a = _fresh_build(project, src_a, file, staging / "base", None, _kind(src_a) == "cad")
        if not res_a.ok or not data_a:
            reasons.append(f"the base revision {rev_a.number} no longer builds with this eitri ({(res_a.error or 'no result')[:300]}): "
                           "the before/after comparison would be wrong")
    res_b, data_b = _fresh_build(project, src_b, file, staging / "result", project.guards(), cad)
    if not res_b.ok or not data_b:
        reasons.append(f"the fresh build of the result failed: {(res_b.error or 'no result')[:500]}")
    if reasons:
        _refuse(t, reasons)
    rows_b = normalise(data_b)
    rows_a = normalise(data_a) if data_a else None
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
        reasons_note = f"waiver for {w['check']!r} is no longer valid (the ticket changed after it was given, or it does not verify)"
        if any(r["name"] == w["check"] and r["status"] == "not_run" for r in rows_b):
            reasons.append(reasons_note + "; a person waives it again")
    criteria = []
    for a in t.data.get("acceptance") or []:
        row = {"id": a["id"], "text": a["text"], "check": a.get("check"), "human_verify": bool(a.get("human_verify")),
               "matched": [], "status": "human_verify" if a.get("human_verify") else None}
        if a.get("check"):
            hits = _match(a["check"], rows_b)
            row["matched"] = [h["name"] for h in hits]
            if not hits:
                reasons.append(f"{a['id']} ({a['text']}) maps to the check {a['check']!r}, which this build does not run; "
                               f"names it does run are in checks.csv (the ticket's criteria change only with a new approval)")
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

    base_sha = hashlib.sha256(src_a.encode("utf-8")).hexdigest() if src_a is not None else None
    frm, to, table = _plan_letters(project, t, file, rev_a.number if rev_a else None, base_sha, _iso(rev_a.created) if rev_a else None)
    if frm:
        frm.update({"source_sha256": base_sha, "date": _iso(rev_a.created)})
    to.update({"build": rev_b.number, "source_sha256": hashlib.sha256(src_b.encode("utf-8")).hexdigest(), "date": now_iso()})
    stem = Path(file).stem
    tickets = Path(t.folder)
    for old in list(tickets.glob(f"{t.id}-*-rev-*")):            # an earlier package of this ticket is replaced
        shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
    out = tickets / f"{t.id}-{stem}-rev-{to['letter']}"
    out.mkdir(parents=True)
    missing: list[dict] = []

    # model sources, diff, images, outputs
    (out / "model" / f"rev-{to['letter']}").mkdir(parents=True)
    (out / "model" / f"rev-{to['letter']}" / file).write_text(src_b, encoding="utf-8")
    fl = frm["letter"] if frm and frm.get("letter") else (f"build-{rev_a.number}" if rev_a else None)
    if src_a is not None:
        (out / "model" / f"rev-{fl}").mkdir(parents=True)
        (out / "model" / f"rev-{fl}" / file).write_text(src_a, encoding="utf-8")
        diff = "".join(difflib.unified_diff(src_a.splitlines(keepends=True), src_b.splitlines(keepends=True),
                                            f"Rev {fl}/{file}", f"Rev {to['letter']}/{file}"))
        (out / "source.diff").write_text(diff, encoding="utf-8")
    (out / "images").mkdir()
    images = {}
    for tag, res in ((fl, res_a), (to["letter"], res_b)):
        if res is not None and res.image and Path(res.image).is_file():
            dst = out / "images" / f"rev-{tag}.png"
            shutil.copy2(res.image, dst)
            images[tag] = dst
    (out / "outputs").mkdir()
    for k, p in (data_b.get("outputs") or {}).items():
        if k in SKIP_OUTPUTS or k == "png" or not Path(p).is_file():
            continue
        shutil.copy2(p, out / "outputs" / Path(p).name)
    if cad:
        for fmt in ("step", "stl", "3mf"):
            if fmt not in (data_b.get("outputs") or {}):
                missing.append({"what": fmt.upper(), "why": "the build did not write it"})
        missing += _cad_drawing(staging / "result" / file, out / "outputs", stem)

    params = params_diff(data_a.get("params") if data_a else None, data_b.get("params") or {}) if data_a else None
    boms = bom_diff(bom_tables(_kind(src_a), data_a), bom_tables(kind, data_b)) if data_a else None
    checks = check_table(rows_a, rows_b)
    removed = [c for c in checks if c["change"] == "removed"]
    planned = {str(x.get("name")) for x in (t.data.get("plan") or {}).get("parameters") or []}
    unplanned = [x["name"] for x in params or [] if x["change"] != "same" and x["name"] not in planned]

    not_checked = []
    for n in (t.data.get("plan") or {}).get("not_checked") or []:
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
                                   "part it is about, or dropped: a person checks which)"})
    for n in NOT_COVERED.get(kind, []):
        not_checked.append({"kind": "domain", "what": n, "why": f"outside what eitri's {label(kind) if kind != 'cad' else 'CAD'} checks cover"})
    for n in ALWAYS_NOT_COVERED:
        not_checked.append({"kind": "scope", "what": n, "why": "outside what a single-file eitri package covers"})

    ap = t.data["approval"]
    plan = t.data.get("plan") or {}
    affected = [file] + sorted({f"{r['table']}: {r['key']}" for r in boms or []})
    table.append({"letter": to["letter"], "build": rev_b.number, "date": to["date"], "ticket": t.id, "source_sha256": to["source_sha256"],
                  "description": t.data.get("title")})
    pkg: dict[str, Any] = {
        "schema": SCHEMA,
        "generated": now_iso(),
        "eitri_version": installed_version(),
        "ticket": {k: t.data.get(k) for k in ("id", "title", "customer", "request", "constraints", "created", "created_by")},
        "design": {"project": Path(project.folder).name, "file": file, "kind": kind, "label": "CAD" if cad else label(kind),
                   "new_design": src_a is None},
        "revisions": {"from": frm, "to": to},
        "revision_table": table,
        "approval": {k: ap.get(k) for k in ("by", "at", "method", "tty", "note", "despite_changes", "content_sha256", "signer")}
                    | {"verified": True},
        "plan": {k: plan.get(k) for k in ("summary", "files", "parameters", "checks_affected", "not_checked", "open_questions", "written", "written_by")},
        "waivers": [{k: w.get(k) for k in ("check", "reason", "by", "at", "method")} for w in waivers],
        "acceptance": criteria,
        "checks": {"summary": {"from": counts(rows_a) if rows_a is not None else None, "to": counts(rows_b)}, "rows": checks},
        "removed_checks": [c["name"] for c in removed],
        "unplanned_changes": unplanned,
        "parameters": params,
        "bom": boms,
        "not_checked": not_checked,
        "change_order": {
            "number": "", "title": t.data.get("title"), "ticket": t.id,
            "reason": t.data.get("request"),
            "description": plan.get("summary", "") + ("\nParameters: " + "; ".join(f"{p['name']} {p['from']} -> {p['to']}" for p in params if p["change"] != "same") if params else ""),
            "affected_items": affected,
            "from_revision": (frm or {}).get("letter"), "to_revision": to["letter"],
            "originator": t.data.get("created_by"), "date": now_iso()[:10],
            "disposition": "", "effectivity": "", "approved_by": "", "approved_date": "",
        },
        "outputs_missing": missing,
        "files": [],
    }
    _write_tables(out, pkg)
    (out / "ticket.yaml").write_text(t.path.read_text(encoding="utf-8"), encoding="utf-8")
    from .report import report_html, report_md
    pkg["files"] = _files(out) + [{"path": n, "role": r, "sha256": None, "bytes": None}       # written next, from this list
                                  for n, r in (("report.md", "report"), ("report.html", "report"), ("package.json", "package"))]
    (out / "report.md").write_text(report_md(pkg), encoding="utf-8")
    (out / "report.html").write_text(report_html(pkg, images), encoding="utf-8")
    (out / "package.json").write_text(json.dumps(pkg, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    zip_path = Path(shutil.make_archive(str(out), "zip", root_dir=out.parent, base_dir=out.name))

    revs = read_revisions(project)
    revs.setdefault("files", {})[file] = table
    _revisions_path(project).write_text(json.dumps(revs, indent=1), encoding="utf-8")
    t.data["package"] = {"folder": str(out.relative_to(Path(project.folder))), "zip": str(zip_path.relative_to(Path(project.folder))),
                         "at": pkg["generated"], "from_letter": (frm or {}).get("letter"), "to_letter": to["letter"],
                         "from_build": rev_a.number if rev_a else None, "to_build": rev_b.number}
    t.data["status"] = "packaged"
    t.save()
    t.event("packaged", **t.data["package"], checks=counts(rows_b))
    return {**pkg, "folder": str(out), "zip": str(zip_path)}


def _iso(ts: float) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _refuse(t: Ticket, reasons: list[str], rows_a=None, rows_b=None) -> None:
    lines = [f"# {t.id}: no change package", "", f"Refused {now_iso()}. Every reason:", ""] + [f"- {r}" for r in reasons]
    if rows_b:
        lines += ["", "## Checks of the result (fresh build)", "", "| check | status | measured |", "|---|---|---|"]
        lines += [f"| {_md(r['name'])} | {r['status']} | {_md(r['measured'])[:200]} |" for r in rows_b]
    text = "\n".join(lines) + "\n"
    (t.folder / "refused.md").write_text(text, encoding="utf-8")
    t.event("package_refused", reasons=reasons)
    raise PackageRefused(reasons, text)


def _md(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _cad_drawing(model: Path, outdir: Path, stem: str) -> list[dict]:
    """The engineering drawing (SVG + DXF) of Rev B; a drawing that cannot be made is listed, never silently skipped."""
    try:
        from ..drawing import drawing_dxf, drawing_svg
        from ..model import load_model
        parts = load_model(model).build()
        svg = drawing_svg(parts, stem, views=("front", "top", "right", "iso"))
        (outdir / f"{stem}.drawing.svg").write_text(svg, encoding="utf-8")
        drawing_dxf(parts, stem, outdir / f"{stem}.dxf", views=("front", "top", "right"))
        return []
    except Exception as exc:  # noqa: BLE001 - the package says what is missing
        return [{"what": "drawing (SVG + DXF)", "why": f"{type(exc).__name__}: {exc}"[:300]}]


def _write_tables(out: Path, pkg: dict) -> None:
    def w(name: str, header: list[str], rows: list[list]):
        buf = io.StringIO()
        cw = csv.writer(buf, lineterminator="\n")
        cw.writerow(header)
        cw.writerows(rows)
        (out / name).write_text(buf.getvalue(), encoding="utf-8")
    fr, to = pkg["revisions"]["from"], pkg["revisions"]["to"]
    a = f"rev_{(fr or {}).get('letter') or 'base'}"
    b = f"rev_{to['letter']}"
    w("checks.csv", ["check", "category", "change", f"{a}_status", f"{a}_measured", f"{b}_status", f"{b}_measured", f"{b}_expected", "source",
                    "rule", "source_basis", "source_urls"],
      [[r["name"], r["category"], r["change"], (r["from"] or {}).get("status", ""), (r["from"] or {}).get("measured", ""),
        (r["to"] or {}).get("status", ""), (r["to"] or {}).get("measured", ""), (r["to"] or {}).get("expected", ""), r["source"],
        r.get("rule", ""), " ".join(dict.fromkeys(x["basis"] for x in r.get("sources") or [])),
        " ".join(dict.fromkeys(x["url"] for x in r.get("sources") or [] if x.get("url")))]
       for r in pkg["checks"]["rows"]])
    if pkg["parameters"] is not None:
        w("parameters.csv", ["parameter", a, b, "change"], [[p["name"], p["from"], p["to"], p["change"]] for p in pkg["parameters"]])
    if pkg["bom"] is not None:
        w("bom_diff.csv", ["table", "item", "change", "fields", a, b],
          [[r["table"], r["key"], r["change"], ";".join(r["fields"]), json.dumps(r["from"], ensure_ascii=False) if r["from"] else "",
            json.dumps(r["to"], ensure_ascii=False) if r["to"] else ""] for r in pkg["bom"]])


def _files(out: Path) -> list[dict]:
    roles = {"report.md": "report", "report.html": "report", "checks.csv": "checks", "parameters.csv": "parameters",
             "bom_diff.csv": "bom", "source.diff": "source_diff", "ticket.yaml": "ticket"}
    rows = []
    for p in sorted(out.rglob("*")):
        if not p.is_file() or p.relative_to(out).as_posix() in ("package.json", "report.md", "report.html"):
            continue
        rel = p.relative_to(out).as_posix()
        role = roles.get(rel) or rel.split("/")[0].rstrip("s")       # model, output, image
        rows.append({"path": rel, "role": role, "sha256": file_sha256(p), "bytes": p.stat().st_size})
    return rows


def image_data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))
