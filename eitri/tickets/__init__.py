"""Tickets: the unit of work eitri sells. A ticket comes in, the agent writes a plan, a PERSON approves it, the agent works,
and `package` hands back a checked change package (or refuses to).

    <project>/tickets/
      active                 the id of the ticket the project works under (absent: no ticket, nothing is gated)
      revisions.json         the released revision letters per model file (Rev A, Rev B...), written by package
      T-0001/
        ticket.yaml          the ticket: request, target, constraints, acceptance criteria, plan, approval, waivers
        events.jsonl         append-only history: created, plan written, approved, waived, built, packaged, refused, closed
        T-0001-<stem>-rev-B/ the change package (and T-0001-<stem>-rev-B.zip next to it)

One implementation for every surface: the CLI (`eitri ticket ...`, `eitri package`), the agent's tools (agent/tools.py, so the
plugin's MCP server and the app's agent loops). A program embedding eitri may plug in its own signer and confirmer.

The plan gate: while the active ticket is not approved, Project refuses to write or build the model (eitri.project calls
`gate`). Approval and waivers are person-only: no agent tool calls them, and `approve` / `waive` refuse to run inside an
agent's process (see store.require_person).
"""
from .store import (ACTIVE_STATES, STATUSES, ChangeRefused, NotAPerson, Ticket, TicketError, active, approve, close,
                    content_sha256, gate, get_signer, list_tickets, load, new_ticket, require_person, set_plan, set_signer,
                    terminal_confirm, verify_approval, waive)
from .package import PackageRefused, make_package

__all__ = ["ACTIVE_STATES", "STATUSES", "ChangeRefused", "NotAPerson", "PackageRefused", "Ticket", "TicketError", "active",
           "approve", "close", "content_sha256", "gate", "get_signer", "list_tickets", "load", "make_package", "new_ticket",
           "require_person", "set_plan", "set_signer", "terminal_confirm", "verify_approval", "waive"]
