"""The change package's human report, Markdown and self-contained HTML, both from package.json's data (so the website can
render the same thing from the JSON alone). Page 1: what changed, the verdict, the approval, every waiver, the warnings."""
from __future__ import annotations

import json
from pathlib import Path

from .package import esc, image_data_uri

STATUS = {"pass": "pass", "warn": "WARN", "fail": "FAIL", "not_run": "NOT RUN", None: "-"}
CRIT = {"pass": "pass", "pass_with_warning": "pass (with a warning)", "waived": "NOT RUN, waived", "human_verify": "a person verifies",
        "fail": "FAIL", "missing": "no such check", "not_run": "NOT RUN"}


def _labels(pkg: dict) -> tuple[str | None, str]:
    fr, to = pkg["revisions"]["from"], pkg["revisions"]["to"]
    a = None
    if fr:
        a = f"Rev {fr['letter']}" if fr.get("letter") else f"build {fr['build']} (unreleased)"
    return a, f"Rev {to['letter']}"


def _warnings(pkg: dict) -> list[str]:
    out = []
    ap = pkg["approval"]
    if ap.get("despite_changes"):
        out.append("The model file changed on disk between the plan and the approval; the approver accepted that change.")
    fr = pkg["revisions"]["from"]
    if fr and not fr.get("letter"):
        out.append(f"The base (build {fr['build']}) is not a released revision: it differs from every lettered revision.")
    if fr and fr.get("letter") == "A" and not fr.get("released"):
        out.append("No revision was released before: this package records the base as Rev A.")
    if pkg["removed_checks"]:
        out.append(f"{len(pkg['removed_checks'])} check(s) that ran on the base do not run on the result: "
                   + "; ".join(pkg["removed_checks"][:8]) + (" ..." if len(pkg["removed_checks"]) > 8 else "")
                   + ". A person confirms each was renamed, not dropped.")
    if pkg.get("unplanned_changes"):
        out.append("Changed although the approved plan did not list it: " + ", ".join(pkg["unplanned_changes"])
                   + ". A person confirms each was needed.")
    if pkg["plan"].get("open_questions"):
        out.append("The plan had open questions: " + " ".join(f"({q})" for q in pkg["plan"]["open_questions"])
                   + (f" Approver's note: {ap['note']}" if ap.get("note") else " The approval note does not answer them."))
    for m in pkg["outputs_missing"]:
        out.append(f"Not produced: {m['what']} ({m['why']}).")
    return out


def _verdict(pkg: dict) -> str:
    c = pkg["checks"]["summary"]["to"]
    s = f"{c['total']} checks: {c['pass']} pass, {c['warn']} warn, 0 fail, {c['not_run']} not run"
    if c["not_run"]:
        s += f" (waived by a person: {', '.join(w['check'] for w in pkg['waivers'])})"
    return s


def _val(v) -> str:
    return "-" if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else str(v))


def report_md(pkg: dict) -> str:
    t, d = pkg["ticket"], pkg["design"]
    a, b = _labels(pkg)
    ap = pkg["approval"]
    L = [f"# {t['id']}: {t['title']}", "",
         f"**{d['file']}** ({d['label']}, project {d['project']}): " + (f"{a} -> {b}" if a else f"{b} (a new design)"), "",
         f"**Result:** {_verdict(pkg)}.", "",
         f"**Plan approved by** {ap.get('by')} at {ap.get('at')} ({ap.get('method')}" + (f", {ap['tty']}" if ap.get("tty") else "") + ")."
         + (f" Note: {ap['note']}" if ap.get("note") else ""), ""]
    if pkg["waivers"]:
        L += ["## WAIVERS (checks that did not run, accepted by a person)", ""]
        L += [f"- **{w['check']}**: {w['reason']} ({w['by']}, {w['at']})" for w in pkg["waivers"]]
        L.append("")
    warn = _warnings(pkg)
    if warn:
        L += ["## Read before signing", ""] + [f"- {x}" for x in warn] + [""]
    L += ["## Acceptance criteria", "", "| # | criterion | checked by | result |", "|---|---|---|---|"]
    for c in pkg["acceptance"]:
        by = ", ".join(c["matched"]) if c["matched"] else ("a person (not machine-checkable)" if c["human_verify"] else c.get("check") or "")
        L.append(f"| {c['id']} | {_md(c['text'])} | {_md(by)} | {CRIT.get(c['status'], c['status'])} |")
    if not pkg["acceptance"]:
        L.append("| - | the ticket gave no criteria | - | - |")
    L += ["", "## Request (customer's words)", ""] + ["> " + l for l in str(t["request"]).splitlines()] + [""]
    if t.get("constraints"):
        L += ["Constraints:", ""] + [f"- {c}" for c in t["constraints"]] + [""]
    p = pkg["plan"]
    L += ["## Approved plan", "", p.get("summary") or "", ""]
    if p.get("parameters"):
        L += [f"- {x.get('name')}: {x.get('from', '?')} -> {x.get('to', '?')}" + (f" ({x['why']})" if x.get("why") else "") for x in p["parameters"]] + [""]
    if p.get("checks_affected"):
        L += ["Checks it expected to change: " + "; ".join(p["checks_affected"]), ""]
    L += ["## Revisions", "", "| rev | build | date | ticket | description |", "|---|---|---|---|---|"]
    L += [f"| {r['letter']} | {r['build']} | {str(r['date'])[:10]} | {r.get('ticket') or '-'} | {_md(r.get('description') or '')} |" for r in pkg["revision_table"]]
    L.append("")
    if pkg["parameters"] is not None:
        ch = [x for x in pkg["parameters"] if x["change"] != "same"]
        L += ["## Parameter changes", ""]
        if ch:
            L += [f"| parameter | {a} | {b} |", "|---|---|---|"] + [f"| {x['name']} | {_val(x['from'])} | {_val(x['to'])} |" for x in ch]
        else:
            L.append("No PARAMS value changed (the change is in the model code: see source.diff).")
        L.append("")
    if pkg["bom"] is not None:
        L += ["## Parts / BOM changes", ""]
        if pkg["bom"]:
            L += ["| table | item | change | what changed |", "|---|---|---|---|"]
            for r in pkg["bom"]:
                what = ", ".join(f"{f}: {_val((r['from'] or {}).get(f))} -> {_val((r['to'] or {}).get(f))}" for f in r["fields"]) if r["fields"] else ""
                L.append(f"| {r['table']} | {_md(r['key'])} | {r['change']} | {_md(what)} |")
        else:
            L.append("No part or BOM line changed.")
        L.append("")
    L += ["## Checks, before and after", ""]
    if a:
        sa = pkg["checks"]["summary"]["from"]
        L += [f"{a}: {sa['pass']} pass, {sa['warn']} warn, {sa['fail']} fail, {sa['not_run']} not run. {b}: {_verdict(pkg)}.", "",
              f"| check | {a} | {b} | measured ({b}) | source |", "|---|---|---|---|---|"]
        for r in pkg["checks"]["rows"]:
            L.append(f"| {_md(r['name'])} | {STATUS[(r['from'] or {}).get('status')]} | {STATUS[(r['to'] or {}).get('status')]} | "
                     f"{_md((r['to'] or r['from'])['measured'])[:240]} | {_md(r['source'])[:240]} |")
    else:
        L += [f"| check | {b} | measured | source |", "|---|---|---|---|"]
        for r in pkg["checks"]["rows"]:
            L.append(f"| {_md(r['name'])} | {STATUS[r['to']['status']]} | {_md(r['to']['measured'])[:240]} | {_md(r['source'])[:240]} |")
    L += ["", "## Not checked", "", "eitri's checks do not cover the following. A person checks them, or accepts them, before release.", ""]
    L += [f"- {n['what']} ({n['why']})" for n in pkg["not_checked"]]
    co = pkg["change_order"]
    L += ["", "## Change order (draft: a person completes and signs it)", "", "| field | value |", "|---|---|"]
    for k in ("number", "title", "ticket", "reason", "description", "affected_items", "from_revision", "to_revision", "originator",
              "date", "disposition", "effectivity", "approved_by", "approved_date"):
        v = co.get(k)
        v = "; ".join(v) if isinstance(v, list) else (v if v not in (None, "") else "_(blank)_")
        L.append(f"| {k.replace('_', ' ')} | {_md(v)} |")
    L += ["", "## Files", ""]
    L += [f"- `{f['path']}` ({f['role']}" + (f", {f['bytes']} bytes" if f.get("bytes") else "") + ")" for f in pkg["files"]]
    L += ["", f"Generated {pkg['generated']} by eitri {pkg['eitri_version']} from fresh builds of both revisions. "
              "package.json holds all of this as data (schema eitri.package/1)."]
    return "\n".join(L) + "\n"


def _md(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


CSS = """body{font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1d232a;max-width:1100px;margin:24px auto;padding:0 20px}
h1{font-size:22px;margin:0 0 6px}h2{font-size:16px;margin:26px 0 8px;border-bottom:1px solid #d5dbe1;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:6px 0 12px;font-size:12.5px}th,td{border:1px solid #d5dbe1;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#f2f5f8}.pass{color:#17663a}.warn,.WARN{color:#8a5a00;font-weight:600}.fail,.FAIL{color:#b3261e;font-weight:700}
.nr{color:#6b3fa0;font-weight:700}.box{border:2px solid #1d232a;padding:10px 14px;margin:12px 0}.waiver{border-color:#6b3fa0;background:#f7f2fc}
.warnbox{border-color:#8a5a00;background:#fff8e6}.imgs{display:flex;gap:12px}.imgs figure{flex:1;margin:0}.imgs img{width:100%;border:1px solid #d5dbe1}
code,pre{font:12px ui-monospace,Menlo,monospace}pre{background:#f6f8fa;padding:8px;overflow:auto;max-height:420px}
@media print{.page1{page-break-after:always}}"""


def _cls(status) -> str:
    return {"pass": "pass", "warn": "warn", "fail": "fail", "not_run": "nr"}.get(status or "", "")


def report_html(pkg: dict, images: dict) -> str:
    t, d = pkg["ticket"], pkg["design"]
    a, b = _labels(pkg)
    ap = pkg["approval"]
    E = esc
    H = [f"<!doctype html><html><head><meta charset='utf-8'><title>{E(t['id'])} {E(t['title'])}</title><style>{CSS}</style></head><body>",
         "<section class='page1'>",
         f"<h1>{E(t['id'])}: {E(t['title'])}</h1>",
         f"<p><b>{E(d['file'])}</b> ({E(d['label'])}, project {E(d['project'])}): " + (f"{E(a)} &rarr; {E(b)}" if a else f"{E(b)} (a new design)") + "</p>",
         f"<div class='box'><b>Result:</b> {E(_verdict(pkg))}.<br><b>Plan approved by</b> {E(ap.get('by'))} at {E(ap.get('at'))} "
         f"({E(ap.get('method'))}{', ' + E(ap['tty']) if ap.get('tty') else ''})." + (f" Note: {E(ap['note'])}" if ap.get("note") else "") + "</div>"]
    if pkg["waivers"]:
        H.append("<div class='box waiver'><b>WAIVERS</b> (checks that did not run, accepted by a person)<ul>"
                 + "".join(f"<li><b>{E(w['check'])}</b>: {E(w['reason'])} ({E(w['by'])}, {E(w['at'])})</li>" for w in pkg["waivers"]) + "</ul></div>")
    warn = _warnings(pkg)
    if warn:
        H.append("<div class='box warnbox'><b>Read before signing</b><ul>" + "".join(f"<li>{E(x)}</li>" for x in warn) + "</ul></div>")
    H.append("<h2>Acceptance criteria</h2><table><tr><th>#</th><th>criterion</th><th>checked by</th><th>result</th></tr>")
    for c in pkg["acceptance"]:
        by = ", ".join(c["matched"]) if c["matched"] else ("a person (not machine-checkable)" if c["human_verify"] else c.get("check") or "")
        cls = "pass" if c["status"] in ("pass", "pass_with_warning") else "nr"
        H.append(f"<tr><td>{E(c['id'])}</td><td>{E(c['text'])}</td><td>{E(by)}</td><td class='{cls}'>{E(CRIT.get(c['status'], c['status']))}</td></tr>")
    if not pkg["acceptance"]:
        H.append("<tr><td colspan=4>the ticket gave no criteria</td></tr>")
    H.append("</table>")
    imgs = [(lbl, images.get(key)) for lbl, key in ((a, (pkg["revisions"]["from"] or {}).get("letter") or (f"build-{pkg['revisions']['from']['build']}" if pkg["revisions"]["from"] else None)),
                                                  (b, pkg["revisions"]["to"]["letter"])) if lbl]
    imgs = [(lbl, p) for lbl, p in imgs if p and Path(p).is_file()]
    if imgs:
        H.append("<div class='imgs'>" + "".join(f"<figure><img src='{image_data_uri(p)}' alt='{E(lbl)}'><figcaption>{E(lbl)}</figcaption></figure>" for lbl, p in imgs) + "</div>")
    H.append("</section>")
    H.append("<h2>Request (customer's words)</h2><blockquote>" + "<br>".join(E(l) for l in str(t["request"]).splitlines()) + "</blockquote>")
    if t.get("constraints"):
        H.append("<p>Constraints:</p><ul>" + "".join(f"<li>{E(c)}</li>" for c in t["constraints"]) + "</ul>")
    p = pkg["plan"]
    H.append(f"<h2>Approved plan</h2><p>{E(p.get('summary'))}</p>")
    if p.get("parameters"):
        H.append("<ul>" + "".join(f"<li>{E(x.get('name'))}: {E(x.get('from', '?'))} &rarr; {E(x.get('to', '?'))}" + (f" ({E(x['why'])})" if x.get("why") else "") + "</li>" for x in p["parameters"]) + "</ul>")
    H.append("<h2>Revisions</h2><table><tr><th>rev</th><th>build</th><th>date</th><th>ticket</th><th>description</th></tr>"
             + "".join(f"<tr><td>{E(r['letter'])}</td><td>{E(r['build'])}</td><td>{E(str(r['date'])[:10])}</td><td>{E(r.get('ticket') or '-')}</td><td>{E(r.get('description'))}</td></tr>" for r in pkg["revision_table"]) + "</table>")
    if pkg["parameters"] is not None:
        ch = [x for x in pkg["parameters"] if x["change"] != "same"]
        H.append("<h2>Parameter changes</h2>" + (f"<table><tr><th>parameter</th><th>{E(a)}</th><th>{E(b)}</th></tr>"
                 + "".join(f"<tr><td>{E(x['name'])}</td><td>{E(_val(x['from']))}</td><td>{E(_val(x['to']))}</td></tr>" for x in ch) + "</table>"
                 if ch else "<p>No PARAMS value changed (the change is in the model code: see source.diff).</p>"))
    if pkg["bom"] is not None:
        H.append("<h2>Parts / BOM changes</h2>")
        if pkg["bom"]:
            H.append("<table><tr><th>table</th><th>item</th><th>change</th><th>what changed</th></tr>")
            for r in pkg["bom"]:
                what = ", ".join(f"{f}: {_val((r['from'] or {}).get(f))} → {_val((r['to'] or {}).get(f))}" for f in r["fields"])
                H.append(f"<tr><td>{E(r['table'])}</td><td>{E(r['key'])}</td><td>{E(r['change'])}</td><td>{E(what)}</td></tr>")
            H.append("</table>")
        else:
            H.append("<p>No part or BOM line changed.</p>")
    H.append("<h2>Checks, before and after</h2><table><tr><th>check</th>" + (f"<th>{E(a)}</th>" if a else "") + f"<th>{E(b)}</th><th>measured ({E(b)})</th><th>source</th></tr>")
    for r in pkg["checks"]["rows"]:
        fs, ts = (r["from"] or {}).get("status"), (r["to"] or {}).get("status")
        H.append(f"<tr><td>{E(r['name'])}</td>" + (f"<td class='{_cls(fs)}'>{E(STATUS[fs])}</td>" if a else "")
                 + f"<td class='{_cls(ts)}'>{E(STATUS[ts])}</td><td>{E((r['to'] or r['from'])['measured'])}</td><td>{E(r['source'])}</td></tr>")
    H.append("</table>")
    H.append("<h2>Not checked</h2><p>eitri's checks do not cover the following. A person checks them, or accepts them, before release.</p><ul>"
             + "".join(f"<li>{E(n['what'])} <i>({E(n['why'])})</i></li>" for n in pkg["not_checked"]) + "</ul>")
    co = pkg["change_order"]
    H.append("<h2>Change order (draft: a person completes and signs it)</h2><table>")
    for k in ("number", "title", "ticket", "reason", "description", "affected_items", "from_revision", "to_revision", "originator",
              "date", "disposition", "effectivity", "approved_by", "approved_date"):
        v = co.get(k)
        v = "; ".join(v) if isinstance(v, list) else v
        H.append(f"<tr><th>{E(k.replace('_', ' '))}</th><td>{E(v) if v not in (None, '') else '<i>(blank)</i>'}</td></tr>")
    H.append("</table><h2>Files</h2><ul>" + "".join(f"<li><code>{E(f['path'])}</code> ({E(f['role'])})</li>" for f in pkg["files"]) + "</ul>")
    H.append(f"<p><small>Generated {E(pkg['generated'])} by eitri {E(pkg['eitri_version'])} from fresh builds of both revisions. "
             "package.json holds all of this as data (schema eitri.package/1).</small></p></body></html>")
    return "\n".join(H)
