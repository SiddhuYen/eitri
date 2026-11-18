# Tickets and change packages

A **ticket** is one piece of work a customer asks for ("our camera is 200 g now, make the tilt hold it"). eitri takes it
through four steps:

1. **Ticket.** The request in the customer's words, the design and revision it changes, constraints, and acceptance
   criteria. Each criterion maps to a named check or is marked "not machine-checkable: a person verifies it".
2. **Plan, then a person approves it.** Before changing anything, the agent writes a plan: the files and parameters it
   will change, the checks it expects to change, what eitri will *not* check, and open questions. Until a **person**
   approves the plan, eitri refuses to write or build the model. No agent tool can approve.
3. **Work.** The agent changes the design and builds it as usual.
4. **Change package.** `eitri package` rebuilds the base revision and the result from scratch and compares them. It
   writes a folder and a zip for a person to review and sign off. If any check fails or did not run, it writes no
   package.

The same code serves the CLI and the agent's tools (the plugin's MCP server and the app's agent).

## Files

```
<project>/tickets/
  active                    id of the ticket the project works under (absent: nothing is gated)
  revisions.json            revision letters per model file (Rev A, Rev B, ...)
  T-0001/
    ticket.yaml             the ticket: request, target, constraints, acceptance, plan, approval, waivers
    events.jsonl            append-only history: created, plan_written, approved, waived, package_refused, packaged, closed
    refused.md              the last refusal, with every reason
    T-0001-<stem>-rev-B/    the change package, and T-0001-<stem>-rev-B.zip next to it
```

`ticket.yaml` is meant to be read and edited by people. Changing its request, target, constraints, acceptance criteria
or plan after approval voids the approval.

## Statuses

| status | meaning | model may change? |
|---|---|---|
| `draft` | created, no plan yet | no |
| `awaiting_approval` | the plan is written | no |
| `approved` | a person approved this exact plan | yes |
| `packaged` | a change package exists (more work and a new package are allowed) | yes |
| `closed` / `cancelled` | a person ended it | not gated |

A project has at most one active ticket.

## Commands

```sh
eitri ticket new  -C <project> --title "..." --request "customer's words" \
                  --criterion "tilt holds the camera => torque joint tilt*" \
                  --criterion "fits the bracket => human" --constraint "..." [--file f.py] [--base latest|N|new]
eitri ticket show [ID] -C <project> [--json]
eitri ticket list -C <project>
eitri ticket plan ID plan.yaml -C <project>     # the agent normally does this with ticket_plan
eitri ticket approve ID -C <project> [--by NAME] [--note "..."] [--despite-changes]
eitri ticket waive ID "<check name>" --reason "..." -C <project>
eitri ticket close ID -C <project> [--cancel]
eitri build <project>/<model.py> --record [--set name=value ...] [--config NAME] [--note "..."]   # apply the plan yourself
eitri package [ID] -C <project> [--json]
```

**Without an agent.** After `eitri ticket approve`, a person applies the plan with `eitri build <model.py> --record --set
wp_x=95 --note "T-0001: wp_x 80 -> 95"`: it writes the values into PARAMS and builds a recorded project revision through the same
Project methods the agent's tools use, so the plan gate refuses it before approval exactly as it refuses the agent, and the
package flags a parameter the plan did not list on page 1. (A plain `eitri build` without `--record` records nothing: its
`--set` is a one-off override, and a package cannot use it.) A code change made by hand in the model file is recorded with
`eitri build <model.py> --record --note "..."`.

A criterion's check is a check name as the report gives it, or a glob such as `torque joint tilt*` (useful when the
check name carries a part name that the change replaces). `ticket show` and the agent's `ticket_new` list the check
names the base build runs.

Agent tools (MCP and the API backend): `ticket_new`, `ticket_show`, `ticket_list`, `ticket_plan`, `package`; on the
combined server (`eitri mcp --combined`): `combined_ticket_new`, `combined_ticket_show`, `combined_ticket_plan`,
`combined_package`. There is deliberately no tool to approve, waive or close.

## Who can approve

`approve`, `waive` and `close` are for a person, in their own terminal. Three things stop an agent from doing them:

- **No agent tool reaches them.** A test lists every tool on both MCP servers and the API backend.
- **They refuse inside an agent's process.** eitri's MCP server and build workers set `EITRI_AGENT_PROCESS` for
  everything they start, and Claude Code sets `CLAUDECODE` in its shell. Codex's and Cursor's markers (`CODEX_SANDBOX`,
  `CURSOR_AGENT`) are listed as well, but those two have not been verified.
- **They need an interactive terminal.** Stdin must be a TTY and `/dev/tty` must open. eitri shows the person the full
  plan, then a one-time code on `/dev/tty` only, and records nothing until they type the code back.

The approval records who approved (`--by`, default the OS user), when, the host, the terminal, a note, and a hash of the
ticket content. It is signed with an HMAC key kept in eitri's config folder (`ticket-key`), not in the project. A
`ticket.yaml` edited by hand to say "approved" fails the gate and the package. Approve on the machine where the
package is made: another machine has no key, so the signature cannot be checked there.

What this does not stop: a program running as the same user that deliberately unsets the markers, or reads the key and
forges a record. The goal is to make an approval deliberate and traceable, and to stop an agent from claiming it asked
when it did not. It is not a security boundary against hostile code on the person's own computer.

If the model file changes on disk between the plan and the approval (an agent's own file tools can do that), `approve`
refuses. `--despite-changes` accepts the change, and the package's first page says so.

## The change package

| file | what |
|---|---|
| `package.json` | everything, as data (schema `eitri.package/1`: `eitri/tickets/package.schema.json`) |
| `report.md`, `report.html` | the human report; the HTML is self-contained (images inlined, no scripts, no links out) |
| `checks.csv` | each check before and after: status (pass / warn / fail / not run), measured, expected, source (the check's structured sources: text, revision, URL), rule id, source basis, source URLs |
| `parameters.csv` | parameter diff |
| `bom_diff.csv` | changed parts and BOM lines (CAD: parts with size, volume, hole sizes and centres; other domains: their BOM CSVs) |
| `source.diff` | unified diff of the model file |
| `model/rev-A/`, `model/rev-B/` | the model file at each revision |
| `outputs/` | Rev B's outputs, rebuilt from scratch. CAD: STEP, STL, 3MF and the engineering drawing (SVG + DXF). Other domains: the files their build writes (diagram, BOM, cut list, netlist, joint and mass tables, sketch...) |
| `images/` | each revision's review image |
| `ticket.yaml` | the ticket as approved |

A new design (`--base new`, or no earlier build) gives the same package without the parameter, BOM and source diffs.

The report's **first page** shows the result, who approved it, every waiver with its reason, and a "Read before
signing" box. That box lists:
- parameters changed that the plan did not list;
- checks that ran on the base and no longer run;
- open questions the plan raised;
- a base that is not a released revision;
- outputs that could not be produced.

The **Not checked** section lists what eitri's checks do not cover:
- the plan's own "not checked" items;
- every criterion a person must verify;
- every waived check;
- every check that disappeared;
- each domain's standing limits (for example, no FEA for CAD and no dynamic loads for mechanisms);
- cross-domain checks, which run only in a combined project (a ticket on a combined project packages them: below).

The **change order** is a draft. eitri fills in the reason, description, affected items, from and to revisions, and the
originator. The number, disposition, effectivity and sign-off are left blank for a person.

Revision letters follow ASME Y14.35's rule: I, O, Q, S, X and Z are not used. The first package of a design records its
base as Rev A.

## Combined projects (several model files)

A combined project (the combined workspace, `eitri mcp --combined`: its `eitri.json` says `"workspace": "combined"`) is one
machine as several model files: CAD, mechanism, harness, firmware, pneumatics, electronics. Each file's checks run on their
own and against the others (eitri_suite's cross-domain checks: masses from the CAD into the torque check, harness routes
against the CAD, firmware pins against the harness, the supply against the actuators that move together...). A change to one
file can fail a check of another, so a ticket on a combined project covers **the whole project**:

- **Target.** Every model file with its base revision (its last good build), or "new" for a file that has none. A blank file
  (the empty `model.py` a new combined project starts with) is not part of the design. `--file` and a revision number are
  refused; `--base latest` or `--base new` only.
- **Plan and approval.** The plan records the hash of every model file. `approve` refuses when any of them changed, appeared
  or went since the plan (`--despite-changes` accepts it, on page 1). A plan's parameters may name their file
  (`{file: mech.py, name: payload_g, from: 50, to: 40}`).
- **The gate** is the project's: while the ticket is not approved, no file is written or built, through the combined tools,
  the viewer or the CLI.
- **The package** rebuilds every file of the base and of the result **together**, in a fresh folder marked as a combined
  project, in the workspace's build order, so every cross-domain check runs again. Check names are qualified by their file
  (`wiring.py: overcurrent protection`). A criterion may name a check with its file, or with the bare name to match it in
  any file. A waiver names the check with its file (a bare name is accepted when only one file has that check).
- **The refusal rule is the same, over every file:** any check of any file (cross-domain ones included) that fails, or did
  not run without a waiver, refuses the package; so do a file on disk with no good build and a fresh build that fails.
- **Revision letters** are the project's (the machine's): one letter for every file together, in `revisions.json` under
  `"project"`, each recording every file's build.

The package holds the same files as a single-file one, per file where it matters: `model/rev-A/` and `model/rev-B/` with every
model file, one `source.diff` over the files that changed (added and removed files against `/dev/null`), `parameters.csv` and
`bom_diff.csv` with a file column, `checks.csv` with `file` and `cross_domain` columns, `outputs/<file stem>/` per file and
`outputs/build_package/` (eitri's build package of Rev B: print files, consolidated BOM, cut list, wiring steps, assembly
order). Page 1 adds a table of the model files: each one's change, base and result build and its checks. A file changed,
added or removed that the plan's `files` did not list is flagged in "Read before signing". In `package.json`,
`design.kind` is `"combined"`, `design.files` lists the files, check rows carry `file`, `check` and `cross_domain`, and the
revisions carry `builds` (`{file: build}`; `build` is the highest of them).

What a combined package still does not check: interactions between the files beyond the declared links (EMC between the
harness and the circuit, heat, wear), and nothing was built or measured.

```sh
eitri ticket new -C ~/eitri-projects/uno-arm --title "Lighter payload" --request "Make it carry 40 g" \
                 --criterion "shoulder holds it => torque joint shoulder*"
# the agent writes the plan (combined_ticket_plan), the person approves:
eitri ticket approve T-0001 -C ~/eitri-projects/uno-arm
# the agent changes the files (combined_put_files) and builds (combined_build), then:
eitri package T-0001 -C ~/eitri-projects/uno-arm
```

## When eitri refuses

`eitri package` writes no package and lists every reason, also in `tickets/<id>/refused.md`, when:

- the approval does not cover the ticket as it is now (the ticket was edited, the plan was rewritten, or the signature
  does not check);
- the model file on disk has no good build, or nothing changed since the base;
- a fresh build of the base or the result fails;
- **any check of the result fails.** A failed check is fixed in the design. It is never waived and never loosened;
- **any check did not run** (an input the check needs is unset) and no person has waived it;
- an acceptance criterion names a check the build does not run.

A person waives one check that did not run with `eitri ticket waive ID "<check>" --reason "..."`. The reason is
printed on the package's first page. eitri refuses to waive a failed check, a check that ran, or a check name the last
build does not have.

## Example: the pan-tilt demo, a heavier camera

`tests/test_tickets.py::test_end_to_end_pan_tilt_servo_change` runs this example end to end. Only the person's
terminal is stood in. The customer moves to a 200 g camera. The agent:

1. opens the ticket with four criteria:
   - the tilt torque (`torque joint tilt*`);
   - the tilt speed (`speed joint tilt`);
   - the target is still reached (`target ahead reachable`);
   - "the MG996R fits the existing yoke bracket", which a person verifies.
2. writes the plan: `camera_g` goes from 90 to 200, and a TowerPro MG996R goes on the tilt. Its figures are cited in
   the model from towerpro.com.tw: 9.4 kg·cm stall at 4.8 V, 0.19 s/60°, and 80 g, the higher of the page's two
   weights. The plan asks one open question: is the supply still 4.8 to 5 V?
3. is refused on its first edit, until the person approves with the note "5 V supply, yes.";
4. makes the change and builds: 12 pass, 0 fail;
5. packages Rev A to Rev B.

The resulting package:
- shows `camera_g` 90 → 200 in the parameter diff;
- shows `SG90 (tilt)` removed and `MG996R (tilt)` added in the BOM diff, along with the tilt joint's new stall, speed and
  margin;
- flags that `torque joint tilt (SG90)` no longer runs: it was renamed to `torque joint tilt (MG996R)`, and a person
  confirms that;
- leaves the bracket fit to a person.
