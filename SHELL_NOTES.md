# The eitri app shell in the core (branch `redesign/shell`)

Every page the open-source core serves now uses the eitri design system, copied unchanged from eitri-hosted's `design/` folder into
`eitri/web/assets/design/` (served at `/assets/design/…`; nothing is loaded from the hosted repo or the internet at runtime).
The core adds one stylesheet on top of it, `eitri/web/assets/app.css` (the editor's docked layout, the controls the editor's script makes,
the domain diagram editors, the print dialog's base classes), and one script, `eitri/web/assets/shell-host.js` (the OSS defaults for the
shell's slots). `eitri.css`, `editor.css` and `assets/fonts/` are gone; the fonts ship inside `design/fonts/`.

## Inventory

| Page, template or asset | Served at | Before | Now |
|---|---|---|---|
| Local project list (`eitri/web/home.html`): key setup, describe-a-design box, projects, recorded demos | `/` | old UI | new shell |
| One tool's project list (same page, filtered; the rail's tool links) | `/?tool=cad\|wires\|motion\|pneumatics\|firmware` | (did not exist) | new shell |
| Editor, CAD (`eitri/viewer/index.html`): 3D viewport, tools, checks, parameters, chat | `/p/<id>/` | old UI | new shell |
| Editor, Wires (`assets/harness.js`) | `/p/<id>/` (harness project) | old UI | new shell |
| Editor, Motion (`assets/mechanism.js` on `assets/domain_editor.js`) | `/p/<id>/` (mechanism project) | old UI | new shell |
| Editor, Pneumatics (`assets/pneumatics.js` on `domain_editor.js`) | `/p/<id>/` (pneumatics project) | old UI | new shell |
| Editor, Firmware (`assets/firmware.js` on `domain_editor.js`) | `/p/<id>/` (firmware project) | old UI | new shell |
| Demo mode: a recorded session's banner and replay in the editor | `/p/demo-<id>/`, `?replay=1` | old UI | new shell |
| Share page: the editor in read-only mode, its download buttons (served by a host's policy, e.g. eitri-hosted `/s/<token>/`) | host route | old UI | new shell |
| The share page's viewer: the 3D view or the domain diagram | same | old UI | new shell |
| Export for 3D printing dialog (`assets/print.js`, `assets/print.css`) | in the CAD editor | old UI | new shell (tokens) |
| Single-project viewer (`eitri view`, `start_viewer`) | `/` of its own server | old UI | new shell (same editor page; crumbs without a home link) |
| Not found page (`eitri/web/message.html`): unknown address, unknown project, for a browser | any unknown path, `/p/<missing>/` | plain text | new shell (other clients still get the same plain text) |
| In-page problems (WebGL missing, an editor that failed to start) | the editor's `#problem` overlay | old UI | new shell |
| Old landing page `site/` (eitri.io's former static site; eitri.io is served by eitri-hosted, which keeps its own media) | nothing in this repo served it | old UI | removed |
| `assets/eitri.css`, `assets/editor.css`, `assets/fonts/` | `/assets/…` | old UI | removed (replaced by `design/` + `app.css`) |
| `assets/icons.svg` (Lucide, the editor's tool icons), `assets/mark.svg` | `/assets/…` | shared | kept (4 icons added for the dock) |
| `assets/design/` (tokens, shell, glyphs, fonts, README) | `/assets/design/…` | — | new |
| `assets/app.css`, `assets/shell-host.js` | `/assets/…` | — | new |
| `/vendor/three/*` (three.js) | `/vendor/…` | unchanged | unchanged |

## Layout of the editor in the shell

- Top bar: the mark, `eitri / <tool> / <project>` (the project in the tool's accent), the ⌘K palette, the right-hand slot.
- Rail: the five tools (CAD, Wires, Motion, Pneumatics, Firmware); the current project's tool is marked. Bottom slot: the local project list.
- Canvas: the viewport is the 3D view (CAD) or the domain diagram, with its tool strip floating over it; the diagram refits to the space.
- Dock (right, 384 px, resizable, folds to a 44 px strip): the inspector panel (title, Export menu, the domain's selection and tables,
  checks, parameters, parts, edits, revisions, report…) over the chat panel. The chat sizes to its content up to half the dock, can be
  dragged taller, and collapses to its header (which then shows the last line said).
- Status bar (mono): the tool, `core vX.Y.Z`, `local`, the project folder, the build state (`rev N · K checks pass` / `… failing`),
  and one link: `combined workspace: eitri.io`.
- Phone (< 760 px): the rail is a bottom tab bar, the viewport takes the top of the screen, the chat and the inspector follow.

## Slots: what a host fills (window.EITRI_SHELL, set before `assets/shell-host.js`)

Every page has a `<!-- eitri:host-head -->` marker in `<head>`, just before `shell-host.js`. A host replaces it with its own `<script>`
that sets `window.EITRI_SHELL`; whatever it defines wins, the rest falls back to the OSS defaults.

| Slot | API | OSS default (local) | eitri-hosted |
|---|---|---|---|
| Top bar, right | `topbarEnd(end, topbar)` (or `<… slot="end">` children of `<eitri-topbar>`) | a `local` badge | usage meter + account menu |
| Rail, bottom | `railBottom: [{key, href, name, short, line, glyph}]` | Projects (`/`, the local list) | Projects (`/app`) |
| Status bar items | `status(footer, info)` after the page's own items; the items have ids: `#st-tool`, `#st-version`, `#st-where`, `#st-path`, `#st-build`, `#st-combined` | tool, core version, `local`, folder, build state, the eitri.io link | hide `#st-where`/`#st-path`, add plan and links |
| Rail tools, palette, home, mark | `rail`, `palette()`, `home`, `mark`, `brand` | the five tools (`/?tool=…`), tools + Projects + GitHub | `/cad` … `/firmware`, its own items |
| "A host is in charge" | `host: true` | — | set it: the editor then never adds OSS-only defaults |

When a page is served with `state.hosted` but no host config (`host` not set), the editor drops the `local` badge and the `local`
status item by itself, so an un-updated host never shows "local".

## Share page

- The download buttons come from the project's own export list (`eitri/exports.py`: `CAD_EXPORTS`, and `EXPORTS` in each
  `eitri/<domain>/domain.py`, every format a `DOWNLOADS` key), filtered to what the last good build actually wrote. The Export menu in
  the editor uses the same list. `state.json` carries it as `exports` (with `output`, the build file a domain's format comes from).
- The console was not clean on any share page: the page fetched `revisions` (refused by the share policy, then `revs.slice` threw),
  posted a thumbnail, and the domain editors loaded their diagram from `domain.svg` / `harness.svg`, which the host's route list does
  not include (so a shared Wires/Motion/Pneumatics/Firmware project showed no diagram at all), and the review image was requested when
  the last build (a quick rebuild after a hand edit) had not drawn one. In share mode the page now asks only for routes on the host's
  list (the diagram comes from `download?fmt=svg`), and the domain editors select instead of drag.

## Screenshot loop

`scripts/shell_shots.py workspace DIR` builds the fixtures (per domain a passing demo and a variant with a failing check);
`scripts/shell_shots.py round DIR OUT` shoots every page: per domain the editor with the chat open and closed, a failing check picked,
the Export menu open and the share page, plus the project list; 1440x900 and 390x844, dark and light (104 captures, contact sheets).

Five rounds. Round 1 found the diagram fitted to a floating inspector that was no longer there, the chat panel taking half the
dock when empty, and the share page inspector showing editing controls; round 2 the export menu clipped on phones and the print
dialog's focus ring and hard-coded orange; round 3 the old site and stylesheets still present; round 4 scored every page 4 with no
open defects; round 5 re-shot after the last fix (the checks card during a replay) with the same result. Every round: no console errors.

| Page | Matches the hosted shell | Editor usability | Hierarchy & polish | Mobile | Light theme |
|---|---|---|---|---|---|
| Project list (`/`, `/?tool=…`) | 4 | 4 | 4 | 4 | 4 |
| CAD editor (chat open / closed, failing check, export) | 4 | 4 | 4 | 4 | 4 |
| Wires editor | 4 | 4 | 4 | 4 | 4 |
| Motion editor | 4 | 4 | 4 | 4 | 4 |
| Pneumatics editor | 4 | 4 | 4 | 4 | 4 |
| Firmware editor | 4 | 4 | 4 | 4 | 4 |
| Share page, all five domains | 4 | 4 | 4 | 4 | 4 |

Not a 5 anywhere: the phone editor stacks the viewport over a long inspector (usable, but the checks are a scroll away), and the
diagrams of the four domains are drawn by the kernel on white paper in both themes.

## Click-through (the local app started as the README says: `eitri web <folder>`, no key)

Per domain: import a model with a failing check from the project list (create), see the check fail and pick it, fix it by hand
(Parameters → Apply & rebuild; firmware: the pin's role in the pinout inspector), edit by hand in the inspector (a wire's length,
a cylinder's bore, a joint opened), export the first file from the Export menu, open the project's share page. Every step passed with
no console errors in the editor or on the share page. Chat: eitri's Claude runs on an Anthropic API key only (Claude Code with
`--bare`); no key was used, so the chat was exercised with a recorded session replayed in the editor (the racket holder: 19 messages,
the builds, the checks going green) and by the suite's scripted session (test_web_browser.py).

## For the release notes

- The app shell: every page the core serves (the editor and its five domain editors, the project list, demo mode, the share page,
  the print dialog, not-found) uses the eitri design system: top bar with breadcrumb, a rail of the five tools, a docked inspector and
  chat beside the model (collapsible, the dock folds), a mono status bar. Light theme follows the system.
- The project list can be filtered by tool (`/?tool=wires`, the rail's links); the projects API reports each project's `kind`.
- One Export menu for every domain, from `eitri/exports.py` (each domain's `EXPORTS`); `state.json` has `exports` and `version`.
- Hosts fill three slots through `window.EITRI_SHELL` at the `<!-- eitri:host-head -->` marker (top bar end, rail bottom, status items).
- Share page fixes: download buttons per domain (a shared harness offers its BOM and cut list, not STEP); a clean console on every
  domain (it fetched owner-only routes; non-CAD shares showed no diagram under a host's route list).
- Removed: `assets/eitri.css`, `assets/editor.css`, `assets/fonts/` (now `assets/design/fonts/`), the old `site/` folder.
- For hosts: pages link `assets/design/*`, `assets/app.css` and `assets/shell-host.js` instead of `eitri.css` / `editor.css`;
  the editor's markup changed (`#side` is inside `#dock`, the title and home link moved to the top bar, `#homelink` is gone,
  drop-downs are `.dd`). eitri-hosted's share route list can stay as it is: the share page now needs nothing beyond it.
- Dependencies: `fast_simplification` (trimesh's mesh decimation) is now declared, and `PyYAML` is in the `dev` extra; a fresh
  install runs the whole suite with no skips. The full notes are in CHANGELOG.md (0.5.0).
