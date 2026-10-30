# Changelog

Release notes for earlier versions are on the GitHub releases page (https://github.com/SiddhuYen/eitri/releases) and in the
annotated tags (`git show v0.4.0`).

## 0.5.4: anchors, and a host's own view in the editor

### New

- **Anchors.** A CAD model declares named points and frames inside `build(p)`: `anchor("UNO", at=(50, 0, 10), z=(0, 0, 1),
  x=(1, 0, 0), part="base")` (a `Location` gives all three; `part` says which part it belongs to, so it moves with it). They are
  recorded with the build (`result.json` and `state.json` `anchors`: `[{name, at, z, x, part}]`, `BuildResult.anchors`), listed in
  the build summary so the agent sees them, kept across direct edits, and documented in the agent's model contract. The core
  attaches no meaning to a name: a host places purchased parts, connectors or probes at them.

### New for hosts

- `EITRI_SHELL.views(state)` and `switchView(key)`: a host's own views as tabs beside the model-file tabs (`<body data-view>` while
  one is up; `eitri:views` on the document redraws the strip).
- `window.eitriViewer`: the editor's three.js scene for a host's scripts (scene, camera, controls, a `hostGroup` of the host's own,
  the parts by name, labels, framing), with `eitri:state`, `eitri:events` and `eitri:model` events on the document. See
  `eitri/web/assets/design/README.md`.

### Breaking for hosts

None. `state.json` gains `anchors`; existing fields, routes, export formats, the build-extension API and the shell slots are unchanged.

## 0.5.3: the share card follows the file

### Fixed

- **The read-only share page's "Shared view" card follows the current file.** It was drawn once, from the first file, and never
  again: on a share page with several model files, a Wires tab still offered the CAD file's STEP / STL downloads. The card is now
  drawn again whenever the file or its available downloads change.

### Breaking for hosts

None. `state.json`, routes, export formats, the build-extension API and the shell slots are unchanged.

## 0.5.2: projects with several model files

A host can show a project that holds several model files (a CAD part, its wiring, its firmware) in one editor, a tab per file. The
open-source core never switches files on its own, so locally there are no tabs, and a project with one model file looks and answers
exactly as in 0.5.1.

### Changed

- **Each model file keeps its own last good build.** `Project.last_ok_revision()` is the current file's (`model=` asks for another),
  and `state.json`, the downloads, undo and redo, the Revisions panel and `compare()` follow the current file. After a switch the page
  shows that file's own geometry or diagram, checks and exports, or no build if it has none: never the last file's build under the
  new file's editor. `restore()` refuses a revision of another file (it would have copied that file's source over this one).
  A good build keeps its `result.json` in `revisions/NNN/`.
- **The editor follows the current file.** When it changes (a tab, the host, the agent's `use_model`), the page clears the last
  file's view and draws the new one; before, going from CAD to a harness and back left the harness editor over the 3D view.

### New for hosts

- `state.json`: `file` (the current model file) and `models`, every model file in the project:
  `[{file, kind, name, current, ok_revision, checks: {pass, warn, fail}}]`.
- `EITRI_SHELL.switchModel(file)`: with it the editor shows a tab strip, one tab per file with the domain's glyph, accent and check
  verdict; `models(state)` chooses the tabs, and `addModel(kind)` adds an "Add" menu of the kinds the project has no file of.
- `EITRI_SHELL.place(state)` → `{crumb, railKey, toolName}`: the editor's crumb, marked rail item and status bar name, instead of the
  current file's tool.
- Python: `Project.model_files()`, `Project.revisions_of(model)`, `Project.build_result(rev)` (what a build wrote to result.json; read it
  instead of `out/result.json`, which holds whichever file was built last). The domain editors have `destroy()`; rail items carry
  `data-key`. All of it is in `eitri/web/assets/design/README.md`.

### Breaking for hosts

None: additive only. Existing `state.json` fields, routes, export formats, the build-extension API and the shell slots are unchanged,
and for a project with one model file every field and route answers as in 0.5.1.

## 0.5.1: checks you can trust, with their sources

Every number a check grades against now has a recorded source (a datasheet, a standard, or a vendor page), and several checks
that passed when they should not now fail or warn. Designs that were wrong before can report differently after upgrading: that
is the point of this release. eitri stays API-key only (it never uses a Claude.ai or Claude Code login).

### Changed results (read before upgrading)

- **quad-power demo: 28 pass, 5 warn** (was 32 pass, 0 warn). The XT60 battery lead carries a 44.5 A peak, above its 30 A
  continuous rating (within its 60 A short-time rating), and each XT30 runs at its full 20 A. The flight-controller lead is now
  24 AWG: 22 AWG does not fit a JST PH contact.
- **servo-sweep demo: 12 pass** (was 11): the servo now declares its own supply (`power="external"`).
- **pan-tilt demo: 12 pass** (was 11), and **air-clamp demo: 23 pass, 1 warn** (was 20 pass): new checks that pass.
- **Voltage drop is about 20 % higher everywhere.** Copper is taken at 75 C (K = 12.9, NEC Chapter 9 Table 8) instead of 29 C
  (10.75). A net near its limit may now fail.
- **Servo continuous torque.** Without a rated value, an actuator is graded against 1/3 of stall (was 1/2), an assumption the
  message names; actuators in the new catalog use their datasheet's continuous rating (Feetech STS3215: 6.5 kg·cm). Joints that
  passed between 1/3 and 1/2 of stall now warn.
- **Connector ratings are continuous, not burst.** XT30 20 A, XT60 30 A, XT90 45 A (Amass), Micro-Fit 3.0 by the wire crimped in
  (18 AWG 8.5 A, 20-30 AWG 5 A), spades by the wire crimped in (TE FASTON spec), Dupont 3 A (Amphenol FCI Mini-PV).

### New checks

- **Wires.** Power budget on Arduino Uno / Nano supply pins (5V on USB 500 mA, on VIN the regulator's thermal limit, 3.3V 50 mA)
  with a shared-ground check; a wire must fit the contacts it is crimped into (JST XH, PH, Dupont, Micro-Fit ranges); peaks
  between a connector's continuous and short-time rating warn.
- **Firmware.** The same power budget; a servo with no declared supply warns; an output pin declared with `current_A` is graded
  against the 20 mA per I/O pin; a `PIN_` constant the pinout does not define fails; a declared pin written as a number warns.
- **Motion.** "stays above its base plane": a mechanism that reaches below its base warns; `m.mount("edge" | "ceiling" | "wall")`
  turns it off for one meant to.
- **Pneumatics.** Pressure ratings: each supply and regulator against the lowest-rated component it reaches; over is a FAIL naming
  it (`max_bar=` from the part's datasheet, else vendor defaults: valve 7 bar, cylinder 10, regulator inlet 16, ...). A cylinder's
  CAP and ROD on one line fails.

### Improved

- **Diagrams.** Pneumatic tubes are routed at right angles into their named ports, never along each other; labels in every domain
  sit beside their own line and never overlap a label, a line or a component; a planar mechanism draws one view; a slide draws its
  stroke; the FRL symbol draws a lubricator only when fitted; tube sizes say "ID"; small currents read in µA.
- **Firmware inputs are named as built:** `fw.button()` and `fw.potentiometer()` beside `fw.limit_switch()`.
- **Clearer messages:** the editor counts warnings apart from passes ("27 of 32 checks pass, 5 warn"); a chamber that cannot vent
  names the undeclared exhaust ports; the mass rollup says where the payload is counted; a misused `payload()` says how to call it.
- **BOM part numbers:** Micro-Fit housings (43645, 2-12 circuits) and terminals chosen per wire size and gender.

### Breaking for hosts

None. `state.json` gains fields (`current_A` on firmware pins, `max_bar` on pneumatic components); existing fields, routes, export
formats, the build-extension API and the shell slots are unchanged.

## 0.5.0: the eitri app shell

Every page the core serves now uses the eitri design system (copied into `eitri/web/assets/design/`, nothing loaded from elsewhere
at runtime). Editing, chat, the checks, exports, the build-extension API, demo mode and bring-your-own-key behave as before.

### New

- **The app shell on every page.** The editor and its five domain editors (CAD, Wires, Motion, Pneumatics, Firmware), the project
  list, demo mode, the share page, the print-export dialog and a new not-found page share one layout: a top bar with a breadcrumb
  (`eitri / wires / Quad power harness`), a rail of the five tools with the project list at its foot, and a mono status bar (tool,
  core version, `local`, project folder, build state, one eitri.io link).
- **Docked panels in the editor.** The model or a domain's diagram fills the canvas; the inspector (checks, parameters, the
  selection, revisions, report) and the chat sit in panels docked on the right. The chat sizes to its content, drags taller and
  collapses to its header; the dock folds to a strip; the domain diagrams refit to the space they get.
- **Light theme.** Dark by default, light when the system is (or `data-theme="light"` on `<html>`).
- **Projects by tool.** The project list can be filtered by tool (`/?tool=wires`: the rail's links); the projects API reports each
  project's `kind` and the core `version`.
- **One Export menu for every domain**, built from `eitri/exports.py` (`CAD_EXPORTS`, and `EXPORTS` beside `DOWNLOADS` in each
  `eitri/<domain>/domain.py`). `state.json` carries `exports` and `version` (and, running locally, the project `folder`).
- **Host slots.** A host fills the top bar's right side, the rail's bottom and the status bar through `window.EITRI_SHELL`, set at
  the `<!-- eitri:host-head -->` marker every page has before `assets/shell-host.js` (`topbarEnd`, `railBottom`, `status`,
  `host: true`; see `SHELL_NOTES.md`). With no host, the core shows a "local" badge and the local project list.

### Fixed

- **Share page downloads per domain.** The download buttons come from the project's own export list, filtered to what its last
  good build wrote: a shared harness offers its diagram, BOM, cut list and WireViz YAML, not the four CAD formats.
- **Share page console.** It was never clean: the page fetched the owner's revisions (refused, then a script error), posted a
  thumbnail, asked for a review image a quick rebuild had not drawn, and loaded domain diagrams from a route a host's share list
  does not include, so shared Wires, Motion, Pneumatics and Firmware projects showed no diagram. In share mode the page now asks
  only for routes a read-only view serves; the domain editors select instead of drag, and the inspector shows values, not controls.
- **Checks card during a replay** now follows the final report of a revision (it kept an earlier verdict).
- **Undeclared dependencies.** `fast_simplification` (trimesh's quadric decimation, used when an imported mesh has over 200,000
  triangles) is now a dependency; before, importing such a mesh failed in a fresh install. `PyYAML` is in the `dev` extra (a test
  reads the WireViz YAML eitri writes itself). A fresh `pip install -e '.[dev]'` runs the whole suite with no skips (with
  arduino-cli and the AVR core installed, the firmware domain's documented system dependency).

### Removed

- `eitri/web/assets/eitri.css`, `eitri/web/assets/editor.css` and `eitri/web/assets/fonts/` (the fonts now live in
  `eitri/web/assets/design/fonts/`).
- The old `site/` folder (eitri.io's former landing page; nothing in this repo served it, and eitri.io is served by eitri-hosted).

### Breaking for hosts

- **Stylesheets and scripts.** Pages link `assets/design/fonts.css`, `tokens.css`, `shell.css`, `assets/design/glyphs.js`,
  `assets/app.css`, `assets/shell-host.js` and `assets/design/shell.js` instead of `assets/eitri.css` and `assets/editor.css`,
  which are gone. A host page that linked the old files must link the new ones.
- **Editor markup.** `#side` (the inspector) now sits inside `#dock` with the chat in `#chatpanel`; the project title and the way
  home moved to the top bar (`<eitri-topbar>`) and `#homelink` is gone; drop-downs use the class `.dd` (the shell owns `.menu`);
  the domain editors' inspector moved from `#hedit .hinspector` to `#domaininsp .hinspector`. Scripts a host appends to the editor
  page that query these elements need updating.
- **Slots.** A host that wants its usage meter, account menu or Projects link in the chrome defines `window.EITRI_SHELL` at the
  `<!-- eitri:host-head -->` marker (`host: true` stops the editor adding the OSS "local" defaults). Served with `state.hosted` but
  no config, the editor drops the "local" badge and status item by itself.
- **Share routes.** No change needed: eitri-hosted's share route list (`SHARE_GET`) is enough; the share page loads the domain
  diagram from `download?fmt=svg`.
