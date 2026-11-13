"""The ticket file, its history, the plan gate, and the person-only actions (approve, waive, close).

A ticket's status:

    draft              created; no plan yet                         -> the model may not be written or built
    awaiting_approval  the agent wrote its plan                     -> still refused, until a person approves
    approved           a person approved this exact plan            -> work goes ahead; `package` may run
    packaged           a change package was produced                -> more work and a new package are allowed
    closed / cancelled a person ended it                            -> not active: nothing is gated

Changing the ticket's content (request, target, constraints, acceptance criteria, plan) after approval voids the approval:
the approval signs a hash of that content. The signature is an HMAC with a key kept in eitri's config folder (outside the
project), so a ticket.yaml edited to say "approved" does not pass the gate. eitri.io plugs in its own signer (set_signer).

Who can approve: only a person, through `eitri ticket approve` in their own terminal. No agent tool reaches approve(), and
approve() itself refuses (NotAPerson) inside an agent's process (the markers in AGENT_ENV: eitri's own MCP server and agent
loops set EITRI_AGENT_PROCESS for everything they start; Claude Code sets CLAUDECODE in its shell) and without an interactive
terminal on stdin and /dev/tty, where the person types back a one-time code. This stops an agent from approving by mistake or
by claiming it asked; it cannot stop a program that runs as the same user and deliberately forges the record with the key.
"""
from __future__ import annotations

import datetime as _dt
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA = "eitri.ticket/1"
STATUSES = ("draft", "awaiting_approval", "approved", "packaged", "closed", "cancelled")
ACTIVE_STATES = ("draft", "awaiting_approval", "approved", "packaged")
WORK_STATES = ("approved", "packaged")            # states in which the model may change
PLAN_FIELDS = ("summary", "files", "parameters", "checks_affected", "not_checked", "open_questions")
CONTENT_FIELDS = ("id", "title", "request", "customer", "target", "constraints", "acceptance", "plan")
# environment variables that mark a process started by an agent (eitri's own, Claude Code's shell, Codex's sandboxed shell,
# Cursor's agent terminal); approve/waive/close refuse to run when any is set
AGENT_ENV = ("EITRI_AGENT_PROCESS", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED",
             "CURSOR_AGENT")
CODE_ALPHABET = "ACDEFHJKMNPRTUVWXY3479"           # no 0/O, 1/I/L, 2/Z, 5/S, 8/B, 6/G: easy to read and type back


class TicketError(ValueError):
    """A ticket action that cannot be done as asked (the message says why and what to do)."""


class ChangeRefused(TicketError):
    """The project works under a ticket that is not approved: the model may not be written or built."""


class NotAPerson(TicketError):
    """A person-only action (approve, waive, close) was called from an agent's process or without a terminal."""


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------------------------------------------- files
def tickets_dir(project) -> Path:
    return Path(project.folder) / "tickets"


def norm_id(tid: str | int) -> str:
    s = str(tid).strip().upper()
    m = re.fullmatch(r"(?:T-?)?0*(\d+)", s)
    if not m:
        raise TicketError(f"not a ticket id: {tid!r} (ids look like T-0001)")
    return f"T-{int(m.group(1)):04d}"


@dataclass
class Ticket:
    folder: Path                 # tickets/T-0001
    data: dict

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def status(self) -> str:
        return self.data.get("status", "draft")

    @property
    def path(self) -> Path:
        return self.folder / "ticket.yaml"

    @property
    def target_file(self) -> str:
        return (self.data.get("target") or {}).get("file") or ""

    @property
    def base_revision(self) -> int | None:
        return (self.data.get("target") or {}).get("revision")

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".yaml.tmp")
        tmp.write_text(dump_yaml(self.data), encoding="utf-8")
        os.replace(tmp, self.path)

    def event(self, kind: str, **data: Any) -> None:
        rec = {"at": now_iso(), "event": kind, **data}
        with (self.folder / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def events(self) -> list[dict]:
        p = self.folder / "events.jsonl"
        if not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


def dump_yaml(data: Any) -> str:
    import yaml

    class _D(yaml.SafeDumper):
        pass

    def _str(dumper, s):
        style = "|" if "\n" in s else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", s, style=style)
    _D.add_representer(str, _str)
    head = ("# eitri ticket (https://github.com/SiddhuYen/eitri/blob/main/docs/tickets.md). Editing the request, target,\n"
            "# constraints, acceptance or plan after approval voids the approval. status/approval/waivers are written by eitri.\n")
    return head + yaml.dump(data, Dumper=_D, sort_keys=False, allow_unicode=True, width=110)


def _read(folder: Path) -> Ticket:
    import yaml
    path = folder / "ticket.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TicketError(f"no ticket {folder.name} in this project") from None
    except yaml.YAMLError as exc:
        raise TicketError(f"{path} is not valid YAML: {exc}") from None
    if not isinstance(data, dict) or "id" not in data:
        raise TicketError(f"{path} is not an eitri ticket")
    return Ticket(folder, data)


def load(project, tid: str | int) -> Ticket:
    return _read(tickets_dir(project) / norm_id(tid))


def list_tickets(project) -> list[Ticket]:
    d = tickets_dir(project)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.iterdir()):
        if f.is_dir() and re.fullmatch(r"T-\d{4,}", f.name) and (f / "ticket.yaml").is_file():
            try:
                out.append(_read(f))
            except TicketError:
                continue
    return out


def active(project) -> Ticket | None:
    """The ticket the project works under (its status is one of ACTIVE_STATES), else None."""
    p = tickets_dir(project) / "active"
    try:
        tid = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not tid:
        return None
    try:
        t = load(project, tid)
    except TicketError:
        return None
    return t if t.status in ACTIVE_STATES else None


def _set_active(project, tid: str | None) -> None:
    p = tickets_dir(project) / "active"
    if tid:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(tid + "\n", encoding="utf-8")
    elif p.exists():
        p.unlink()


def _next_id(project) -> str:
    nums = [int(t.id.split("-")[1]) for t in list_tickets(project)]
    return f"T-{(max(nums) + 1 if nums else 1):04d}"


# ---------------------------------------------------------------------------------------------------------------- content
def content_sha256(data: dict) -> str:
    """The hash an approval signs: everything a person approves (request, target, constraints, criteria, plan)."""
    body = {k: data.get(k) for k in CONTENT_FIELDS}
    return hashlib.sha256(_canon(body).encode("utf-8")).hexdigest()


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _criteria(items: Iterable[Any] | None) -> list[dict]:
    """Acceptance criteria: each maps to a named check (a check name, or a glob such as "torque joint tilt*"), or is
    explicitly human-verified. Strings: "text => check name" or "text => human"."""
    out = []
    for i, it in enumerate(items or (), 1):
        if isinstance(it, str):
            text, _, check = it.partition("=>")
            it = {"text": text.strip()}
            check = check.strip()
            if check.lower() in ("human", "human verifies", "human_verify", "not machine-checkable"):
                it["human_verify"] = True
            elif check:
                it["check"] = check
        if not isinstance(it, dict) or not str(it.get("text", "")).strip():
            raise TicketError(f"acceptance criterion {i}: give its text")
        check = str(it.get("check") or "").strip()
        human = bool(it.get("human_verify"))
        if bool(check) == human:
            raise TicketError(f"acceptance criterion {i} ({it['text']!r}): map it to one named check (check: <name>) or mark "
                              "it human_verify: true (not machine-checkable: a person verifies it), not both and not neither")
        row = {"id": str(it.get("id") or f"AC-{i}"), "text": str(it["text"]).strip()}
        if check:
            row["check"] = check
        else:
            row["human_verify"] = True
            row["note"] = "not machine-checkable: human verifies"
        out.append(row)
    return out


def _strings(items: Iterable[Any] | str | None) -> list[str]:
    if items is None:
        return []
    if isinstance(items, str):
        items = [l for l in items.splitlines()]
    return [str(x).strip() for x in items if str(x).strip()]


# ---------------------------------------------------------------------------------------------------------------- create
def new_ticket(project, title: str, request: str, *, constraints: Iterable[str] | str | None = None,
               acceptance: Iterable[Any] | None = None, file: str | None = None, base: int | str | None = "latest",
               customer: str = "", created_by: str = "agent") -> Ticket:
    """Open a ticket on the project and make it the active one. `base`: "latest" (the target file's last good build, or a new
    design when it has none), a revision number, or None / "new" (a new design: the package has no diff sections)."""
    if not str(title).strip() or not str(request).strip():
        raise TicketError("a ticket needs a title and the request in the customer's words")
    cur = active(project)
    if cur is not None:
        raise TicketError(f"{cur.id} ({cur.data.get('title')}) is still {cur.status} on this project; a person finishes it with "
                          f"`eitri ticket close {cur.id}` before another ticket starts")
    file = Path(file).name if file else project.model_path.name
    if not file.endswith(".py"):
        file += ".py"
    if base in (None, "new", "none"):
        rev = None
    elif base == "latest":
        last = project.last_ok_revision(model=file)
        rev = last.number if last else None
    else:
        n = int(base)
        hist = {h.number: h for h in project.revisions_of(file)}
        if n not in hist:
            raise TicketError(f"revision {n} is not a build of {file} (its builds: {', '.join(map(str, hist)) or 'none'})")
        if not hist[n].ok:
            raise TicketError(f"revision {n} of {file} failed to build; the base must be a good build")
        rev = n
    tid = _next_id(project)
    data = {
        "schema": SCHEMA, "id": tid, "title": str(title).strip(), "status": "draft",
        "created": now_iso(), "created_by": created_by, "customer": customer or "",
        "request": str(request).strip(),
        "target": {"file": file, "revision": rev},
        "constraints": _strings(constraints),
        "acceptance": _criteria(acceptance),
        "plan": None, "approval": None, "waivers": [], "package": None,
    }
    t = Ticket(tickets_dir(project) / tid, data)
    t.save()
    t.event("created", by=created_by, target=data["target"])
    _set_active(project, tid)
    return t


def set_plan(project, tid: str | int, plan: dict, *, acceptance: Iterable[Any] | None = None,
             constraints: Iterable[str] | str | None = None, by: str = "agent") -> Ticket:
    """Write (or rewrite) the plan; the ticket waits for a person's approval. A plan rewritten after approval voids it."""
    t = load(project, tid)
    if t.status not in ACTIVE_STATES:
        raise TicketError(f"{t.id} is {t.status}; a closed ticket takes no plan")
    if not isinstance(plan, dict):
        raise TicketError("the plan is an object with: " + ", ".join(PLAN_FIELDS))
    unknown = [k for k in plan if k not in PLAN_FIELDS]
    if unknown:
        raise TicketError(f"unknown plan field(s) {unknown}; a plan has: {', '.join(PLAN_FIELDS)} (status and approval are "
                          "not the agent's to set)")
    summary = str(plan.get("summary") or "").strip()
    if not summary:
        raise TicketError("the plan needs a summary: what will change and why, in a few sentences")
    params = []
    for p in plan.get("parameters") or []:
        if isinstance(p, str):
            name, _, rest = p.partition(":")
            frm, _, to = rest.partition("->")
            p = {"name": name.strip(), "from": frm.strip(), "to": to.strip()}
        if not isinstance(p, dict) or not p.get("name"):
            raise TicketError("each planned parameter is {name, from, to, why} (or \"name: from -> to\")")
        params.append({k: p.get(k) for k in ("file", "name", "from", "to", "why") if p.get(k) not in (None, "")})
    files = _strings(plan.get("files")) or ([t.target_file] if t.target_file else [])
    t.data["plan"] = {
        "summary": summary, "files": files, "parameters": params,
        "checks_affected": _strings(plan.get("checks_affected")),
        "not_checked": _strings(plan.get("not_checked")),
        "open_questions": _strings(plan.get("open_questions")),
        "written": now_iso(), "written_by": by,
        "model_sha256": file_sha256(Path(project.folder) / t.target_file) if t.target_file else None,
    }
    if acceptance is not None:
        t.data["acceptance"] = _criteria(acceptance)
    if constraints is not None:
        t.data["constraints"] = _strings(constraints)
    if t.data.get("approval"):
        t.event("approval_voided", reason="the plan was rewritten", was=t.data["approval"])
    t.data["approval"] = None
    t.data["status"] = "awaiting_approval"
    t.save()
    t.event("plan_written", by=by, content_sha256=content_sha256(t.data))
    return t


# ---------------------------------------------------------------------------------------------------------------- signing
class LocalKeySigner:
    """HMAC-SHA256 with a random key in eitri's config folder (created on first use, readable only by its owner)."""
    name = "local-key"

    def _key(self, create: bool) -> bytes | None:
        from ..config import config_dir
        path = config_dir() / "ticket-key"
        try:
            return bytes.fromhex(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            if not create:
                return None
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key.hex() + "\n")
        return key

    def sign(self, payload: dict) -> str:
        return hmac.new(self._key(create=True), _canon(payload).encode("utf-8"), hashlib.sha256).hexdigest()

    def verify(self, payload: dict, signature: str) -> bool:
        key = self._key(create=False)
        if key is None or not signature:
            return False
        good = hmac.new(key, _canon(payload).encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(good, str(signature))


_SIGNER: Any = LocalKeySigner()


def set_signer(signer) -> None:
    """A host (eitri.io) signs approvals its own way: an object with sign(payload) -> str and verify(payload, sig) -> bool."""
    global _SIGNER
    _SIGNER = signer


def get_signer():
    return _SIGNER


def _approval_payload(t: Ticket, rec: dict) -> dict:
    return {"ticket": t.id, **{k: v for k, v in rec.items() if k != "signature"}}


def verify_approval(t: Ticket) -> tuple[bool, str]:
    """(valid, why not): the approval is signed by this machine's key (or the host's signer) and covers the ticket as it is."""
    rec = t.data.get("approval")
    if t.status not in WORK_STATES:
        return False, f"{t.id} is {t.status.replace('_', ' ')}"
    if not isinstance(rec, dict):
        return False, f"{t.id} says {t.status} but holds no approval record"
    if rec.get("content_sha256") != content_sha256(t.data):
        return False, (f"{t.id} changed after it was approved (request, target, constraints, criteria or plan): the approval "
                       "no longer covers it")
    if not _SIGNER.verify(_approval_payload(t, rec), rec.get("signature", "")):
        return False, (f"{t.id}'s approval signature does not verify on this machine (the record was edited by hand, or it "
                       "was approved on another machine)")
    return True, ""


# ---------------------------------------------------------------------------------------------------------------- gate
def gate(project, action: str = "change the model") -> None:
    """Raise ChangeRefused when the project's active ticket does not allow the model to change (Project calls this before
    it writes, restores or builds a model file)."""
    t = active(project)
    if t is None:
        return
    if t.status == "draft":
        raise ChangeRefused(
            f"Refused to {action}: this project works under ticket {t.id} ({t.data.get('title')}), which has no plan yet. "
            f"Write the plan first (ticket_plan: the files and parameters you will change, the checks you expect to be affected, "
            f"what will not be checked, open questions); then a person approves it.")
    if t.status == "awaiting_approval":
        raise ChangeRefused(
            f"Refused to {action}: ticket {t.id} is awaiting approval. The person approves it by running "
            f"`eitri ticket approve {t.id}` in their own terminal; no tool you have can approve it. Do not tell them it is "
            f"approved, and do not start the change, until ticket_show says approved.")
    ok, why = verify_approval(t)
    if not ok:
        raise ChangeRefused(f"Refused to {action}: {why}. The person approves it again with `eitri ticket approve {t.id}`.")


# ---------------------------------------------------------------------------------------------------------------- person-only
def require_person(action: str) -> None:
    """Refuse inside an agent's process. The check is by environment marker: a deliberate `env -u` gets past it (see the
    module docstring for what this does and does not stop)."""
    hit = [k for k in AGENT_ENV if os.environ.get(k)]
    if hit:
        raise NotAPerson(f"`{action}` is for a person, in their own terminal, not for an agent: this process was started by an "
                         f"agent ({', '.join(hit)} is set). Ask the person to run it themselves.")


def _open_tty():
    """The person's terminal, for the one-time code (a separate function so tests can stand in a terminal)."""
    if not (sys.stdin and sys.stdin.isatty()):
        raise NotAPerson("this needs an interactive terminal: run it yourself in a terminal (not through an agent, a pipe or a "
                         "script)")
    try:
        from eitri.terminal import TerminalIO
        return TerminalIO()                       # not open("/dev/tty", "r+"): a terminal is not seekable (eitri/terminal.py)
    except OSError:
        raise NotAPerson("this needs an interactive terminal (/dev/tty cannot be opened): run it yourself in a terminal") from None


def terminal_confirm(text: str, what: str) -> dict:
    """Show `text` on the person's terminal, ask them to type back a one-time code, return {tty} when they did. The code is
    printed only to /dev/tty, never to stdout or a log."""
    tty = _open_tty()
    try:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        tty.write(text.rstrip() + "\n\n")
        tty.write(f"To {what}, type this code: {code}\n> ")
        tty.flush()
        typed = (tty.readline() or "").strip().upper().replace(" ", "")
        if not hmac.compare_digest(typed, code):
            raise TicketError("the code did not match: nothing was recorded")
        try:
            name = os.ttyname(tty.fileno())
        except OSError:
            name = "tty"
        return {"tty": name}
    finally:
        tty.close()


def person_record(action: str, text: str, what: str, *, by: str | None = None,
                  confirm: Callable[[str, str], dict] = terminal_confirm, method: str = "cli-terminal") -> dict:
    """The one person-only confirmation every such action uses (ticket approve / waive / close, reference add / accept /
    remove): refuse in an agent's process, show `text` on the person's terminal and take back the typed one-time code, and
    return who confirmed it, when and how (sign it with sign_record)."""
    require_person(action)
    extra = confirm(text, what)
    return {**_who(by), "at": now_iso(), "method": method, **(extra or {}), "signer": getattr(_SIGNER, "name", "host")}


def sign_record(kind: str, payload: dict, rec: dict) -> dict:
    """rec with its signature over (kind, payload, rec): the same signer as ticket approvals."""
    rec = {k: v for k, v in rec.items() if k != "signature"}
    rec["signature"] = _SIGNER.sign({"kind": kind, "payload": payload, "record": rec})
    return rec


def verify_record(kind: str, payload: dict, rec: dict | None) -> bool:
    if not isinstance(rec, dict) or not rec.get("signature"):
        return False
    body = {k: v for k, v in rec.items() if k != "signature"}
    return _SIGNER.verify({"kind": kind, "payload": payload, "record": body}, rec["signature"])


def _who(by: str | None) -> dict:
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    return {"by": (by or user or "unknown").strip(), "os_user": user, "host": socket.gethostname()}


def approve(project, tid: str | int, *, by: str | None = None, note: str = "", despite_changes: bool = False,
            confirm: Callable[[str, str], dict] = terminal_confirm, method: str = "cli-terminal") -> Ticket:
    """A person approves the plan exactly as it stands. Refuses in an agent's process, without the typed one-time code, and
    when the model file changed on disk since the plan was written (unless despite_changes, which is recorded)."""
    require_person("eitri ticket approve")
    t = load(project, tid)
    if t.status != "awaiting_approval":
        raise TicketError(f"{t.id} is {t.status.replace('_', ' ')}: only a ticket awaiting approval can be approved"
                          + (" (the agent writes its plan first)" if t.status == "draft" else ""))
    plan = t.data.get("plan") or {}
    changed = False
    if t.target_file and plan.get("model_sha256") != file_sha256(Path(project.folder) / t.target_file):
        changed = True
        if not despite_changes:
            raise TicketError(f"{t.target_file} changed on disk after the plan was written and before approval: look at the "
                              "change (git diff, or compare it with the base revision), then approve with --despite-changes "
                              "(recorded on the package's first page) or ask for a new plan")
    extra = confirm(describe(project, t), f"approve {t.id}")
    rec = {**_who(by), "at": now_iso(), "method": method, **(extra or {}), "note": note.strip(),
           "despite_changes": changed, "content_sha256": content_sha256(t.data), "signer": getattr(_SIGNER, "name", "host")}
    rec["signature"] = _SIGNER.sign(_approval_payload(t, rec))
    t.data["approval"] = rec
    t.data["status"] = "approved"
    t.save()
    t.event("approved", **{k: v for k, v in rec.items() if k != "signature"})
    return t


def waive(project, tid: str | int, check: str, reason: str, *, by: str | None = None,
          confirm: Callable[[str, str], dict] = terminal_confirm, method: str = "cli-terminal") -> Ticket:
    """A person waives one check that did not run, with a written reason (printed on the package's first page). A failed check
    can never be waived: package refuses on it regardless."""
    require_person("eitri ticket waive")
    t = load(project, tid)
    if t.status not in WORK_STATES:
        raise TicketError(f"{t.id} is {t.status.replace('_', ' ')}: waivers are given on an approved ticket")
    check, reason = str(check).strip(), str(reason).strip()
    if not check:
        raise TicketError("name the check to waive, exactly as the report names it")
    if len(reason) < 15:
        raise TicketError("give a written reason (a sentence: why it is acceptable that this check does not run)")
    last = _last_check_state(project, t, check)
    if last == "fail":
        raise TicketError(f"{check!r} FAILED in the last build: a failed check is never waived; fix the design")
    if last in ("pass", "warn"):
        raise TicketError(f"{check!r} ran in the last build ({last}); only a check that did not run can be waived")
    if last == "missing":
        raise TicketError(f"the last build has no check named {check!r}; name it exactly as the report does")
    extra = confirm(f"Waive the check {check!r} on {t.id} ({t.data.get('title')}).\nReason: {reason}\n"
                    "The waiver and its reason are printed on the first page of the change package.", f"waive {check!r}")
    rec = {"check": check, "reason": reason, **_who(by), "at": now_iso(), "method": method, **(extra or {}),
           "content_sha256": content_sha256(t.data)}
    rec["signature"] = _SIGNER.sign({"ticket": t.id, "waiver": {k: v for k, v in rec.items() if k != "signature"}})
    t.data["waivers"] = [w for w in (t.data.get("waivers") or []) if w.get("check") != check] + [rec]
    t.save()
    t.event("waived", **{k: v for k, v in rec.items() if k != "signature"})
    return t


def verify_waiver(t: Ticket, w: dict) -> bool:
    if w.get("content_sha256") != content_sha256(t.data):
        return False
    return _SIGNER.verify({"ticket": t.id, "waiver": {k: v for k, v in w.items() if k != "signature"}}, w.get("signature", ""))


def _last_check_state(project, t: Ticket, name: str) -> str | None:
    """pass | warn | fail | not_run for a check in the target file's last good build, "missing" when that build has no such
    check, None when there is no build to look at."""
    from .checks import normalise
    last = project.last_ok_revision(model=t.target_file) if t.target_file else None
    data = project.build_result(last) if last else None
    if not data:
        return None
    for row in normalise(data):
        if row["name"] == name:
            return row["status"]
    return "missing"


def close(project, tid: str | int, *, by: str | None = None, cancel: bool = False, note: str = "",
          confirm: Callable[[str, str], dict] = terminal_confirm) -> Ticket:
    """A person ends a ticket (closed: done; cancelled: dropped). The project is no longer gated by it."""
    require_person("eitri ticket close")
    t = load(project, tid)
    if t.status not in ACTIVE_STATES:
        raise TicketError(f"{t.id} is already {t.status}")
    what = "cancel" if cancel else "close"
    extra = confirm(f"{what.title()} {t.id} ({t.data.get('title')}), now {t.status.replace('_', ' ')}.", f"{what} {t.id}")
    t.data["status"] = "cancelled" if cancel else "closed"
    t.data["closed"] = {**_who(by), "at": now_iso(), "note": note.strip(), **(extra or {})}
    t.save()
    t.event(t.data["status"], **t.data["closed"])
    cur = (tickets_dir(project) / "active")
    if cur.is_file() and cur.read_text(encoding="utf-8").strip() == t.id:
        _set_active(project, None)
    return t


# ---------------------------------------------------------------------------------------------------------------- text
def describe(project, t: Ticket) -> str:
    """The ticket as a person reads it before approving (and as ticket_show returns it)."""
    d = t.data
    tgt = d.get("target") or {}
    lines = [f"{t.id}: {d.get('title')}  [{t.status.replace('_', ' ').upper()}]",
             f"Target: {tgt.get('file')} " + (f"from revision {tgt['revision']}" if tgt.get("revision") else "(a new design)"),
             "", "Request (customer's words):"] + ["  " + l for l in str(d.get("request", "")).splitlines()]
    if d.get("customer"):
        lines.insert(2, f"Customer: {d['customer']}")
    if d.get("constraints"):
        lines += ["", "Constraints:"] + [f"  - {c}" for c in d["constraints"]]
    lines += ["", "Acceptance criteria:"]
    for a in d.get("acceptance") or []:
        how = f"check: {a['check']}" if a.get("check") else "NOT machine-checkable: a person verifies it"
        lines.append(f"  {a['id']}. {a['text']}  ({how})")
    if not d.get("acceptance"):
        lines.append("  (none given: the package shows every check, but no criterion is tied to one)")
    plan = d.get("plan")
    if plan:
        lines += ["", f"Plan (written {plan.get('written')} by {plan.get('written_by')}):", "  " + plan["summary"]]
        if plan.get("files"):
            lines.append("  Files: " + ", ".join(plan["files"]))
        for p in plan.get("parameters") or []:
            lines.append(f"  Parameter {p.get('name')}: {p.get('from', '?')} -> {p.get('to', '?')}" + (f"  ({p['why']})" if p.get("why") else ""))
        if plan.get("checks_affected"):
            lines.append("  Checks expected to change: " + "; ".join(plan["checks_affected"]))
        if plan.get("not_checked"):
            lines.append("  NOT checked by eitri: " + "; ".join(plan["not_checked"]))
        if plan.get("open_questions"):
            lines += ["  OPEN QUESTIONS (answer them in --note, or ask for a new plan):"] + [f"    ? {q}" for q in plan["open_questions"]]
    else:
        lines += ["", "Plan: not written yet (the agent writes it with ticket_plan)."]
    ap = d.get("approval")
    if ap:
        ok, why = verify_approval(t)
        lines += ["", f"Approved by {ap.get('by')} at {ap.get('at')} ({ap.get('method')}" + (f", {ap['tty']}" if ap.get("tty") else "")
                  + ")" + ("" if ok else f"  -- NOT VALID: {why}")]
        if ap.get("note"):
            lines.append(f"  Note: {ap['note']}")
    for w in d.get("waivers") or []:
        lines.append(f"Waiver: {w['check']} - {w['reason']} ({w.get('by')}, {w.get('at')})")
    pk = d.get("package")
    if pk:
        lines.append(f"Package: {pk.get('folder')} (Rev {pk.get('from_letter') or '-'} -> Rev {pk.get('to_letter')}, {pk.get('at')})")
    nxt = {"draft": "Next: the agent writes the plan (ticket_plan).",
           "awaiting_approval": f"Next: a person reads this and runs `eitri ticket approve {t.id}` in their own terminal.",
           "approved": f"Next: the agent makes the change and builds; then `eitri package {t.id}` (or the package tool).",
           "packaged": f"Next: a person reviews the package; `eitri ticket close {t.id}` when done."}.get(t.status)
    if nxt:
        lines += ["", nxt]
    return "\n".join(lines)
