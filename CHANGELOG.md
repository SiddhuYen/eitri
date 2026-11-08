# Changelog

Release notes for earlier versions are on the GitHub releases page (https://github.com/SiddhuYen/eitri/releases) and in the
annotated tags (`git show v0.4.0`).

## 0.7.0: eitri as a plugin in your own agent

eitri now also runs inside the agent you already use (Claude Code, Codex, Cursor), on that agent's own subscription. In
plugin mode eitri never calls Claude and needs no API key: your agent writes the model files and calls eitri's tools.
The app (`eitri`) is unchanged and stays API-key only.

### Added

- **`eitri mcp`**: the local MCP server in plugin mode, for an agent to start. The six single domains with the same checks
  as the app. `new_project` / `open_project` / `list_projects` in a folder the person picks (default `~/eitri-projects`);
  the viewer opens in the browser at the first build and follows every rebuild; every build result ends with a
  **PROJECT STATUS** (each failing or warning check with its numbers, files edited but not rebuilt, what to do next),
  in place of the app's agent-loop reminders. New tools `rebuild`, `project_status`, `open_viewer`, `cad_reference`,
  `domain_guide`.
- **The eitri plugin** (`eitri/plugin_bundle/`): one plugin with two servers, the local `eitri` and the combined
  multi-domain workspace on eitri.io (`eitri-combined`, `https://eitri.io/mcp`, the default for whole machines), and
  skills: `design` (picks the combined workspace or one local domain and says which), `combined` (the build-and-fix loop)
  and one per local domain, generated from the app's own prompts. A Claude Code marketplace at the repository root
  (`/plugin marketplace add SiddhuYen/eitri`, then `/plugin install eitri@eitri`).
- **`eitri plugin install | login | logout | uninstall | status`**. `install` registers the plugin in every agent app it
  finds: Claude Code (a real plugin), Codex (`~/.codex/config.toml` servers and `~/.agents/skills`), Cursor
  (`~/.cursor/mcp.json`, `--cursor-rules` for a project rule). `login` opens eitri.io/account, checks the personal token
  with eitri.io, stores it readable only by you and tells you what happens to combined-workspace files. No shell file is
  edited; the plugin sends no telemetry.
- **`curl -fsSL https://eitri.io/install | sh -s -- --plugin`**: install, register the plugin and sign in, in one go.
  `eitri update` refreshes an installed plugin.

### Changed

- **Lazy import for the plugin server**: with `EITRI_LAZY_IMPORT=1` (set by the plugin's launcher and its Codex and
  Cursor entries), `import eitri` no longer loads the CAD kernel until a name is used, so the server answers an agent's
  connect within its time limit (Claude Code allows 30 s; loading the kernel took ~17 s on a busy Mac). `from eitri
  import *` gives the same names. The app's path is unchanged.
- MCP tool errors reach the agent as readable text (they were a generic "Error executing tool").

### Requirements

- **The plugin needs eitri 0.7.0 or newer** (the release that adds `eitri mcp` and `eitri plugin`). On an older eitri the
  plugin's launcher and `install … --plugin` say so instead of failing; run `eitri update`.

## 0.6.0: electronics, and a one-command install

eitri gains its sixth domain, circuits at breadboard and module level, and becomes installable with one command from PyPI
(`eitri-hardware`).

### Added

- **Electronics at breadboard and module level** (`from eitri.electronics import *`). A `Circuit` holds a board (Uno, Nano, ESP32
  DevKitC), supplies, regulators, catalog modules (PCA9685, BME280, SSD1306, HC-SR04), resistors, LEDs, diodes, capacitors,
  switches, MOSFETs, an NPN transistor, a relay, motors, solenoids and servos, joined by nets. A DC kernel classifies every net,
  solves series strings in closed form and branched networks (LEDs sharing a resistor, a divider feeding two loads) by nodal
  analysis with an ideal-diode iteration; the checks grade per-pin and per-chip current,
  LED current and current hogging, resistor power, unlimited-current strings, shorted and back-fed rails, supply ranges, rail budgets (servos at stall),
  regulator dropout and junction temperature, VIN range, common ground, logic levels and over-voltage on inputs, floating inputs,
  I2C pull-ups (UM10204 Rp(min) / Rp(max)) and addresses, an AVR board's Wire internal pull-ups against 3.3 V devices, flyback diodes on switched coils, MOSFET gate drive and heat, transistor
  saturation and relay contacts. Every limit has a recorded source (document, URL, quote, fetch date) in
  `eitri/electronics/sources.py`; where sources disagree the conservative figure is used (the lower limit, the higher draw); each build writes the sources it used. Outputs: a schematic (SVG + PNG review image, labels by the
  shared placer), BOM, netlist, sources. A browser editor (schematic, parts and nets tables, value and pin edits, drag), the agent's
  `electronics_reference()` tool, and the `uno-arm-circuit` demo (its `bad` configuration fails two checks on purpose).
- **Branched circuits are solved, not skipped.** Where three or more parts meet at a node that is not a rail (two LEDs on one
  resistor, a divider feeding two loads, a pin feeding several strings through one resistor) the network is solved by nodal
  analysis (`eitri/electronics/nodal.py`: MNA with an ideal-diode iteration, written for eitri). Series strings keep the closed form;
  a test checks the two agree on every series string. LEDs that share a resistor are graded on the worst case (each at typical Vf,
  the others at maximum, so one takes the whole current), and an LED that stays dark at typical Vf because another clamps the node
  is a warning (current hogging).
- **Wire's internal pull-ups on an Uno or Nano.** `Wire.begin()` turns on the ATmega's 20-50 kΩ pull-ups to 5 V on SDA and SCL. A
  device on the bus that cannot take 5.5 V (a 3.3 V BME280) is a warning with the line's worst voltage and the fixes: a level
  shifter, or a 3.3 V-only bus with the internal pull-ups turned off, declared with `c.i2c(sda, internal_pullups=False)` (also a
  toggle on the SDA net in the editor).
- **Catalog LEDs.** `c.led(name, part="WP7113SGD")` (also WP7113SRD/D, WP7113QBC/D, WP7113QWC/D) is graded on that part's own
  datasheet, with no "typical figures" warning; `vf_max=` for your own LED.
- **One-command install.** `curl -fsSL https://eitri.io/install | sh` (`scripts/install-remote.sh`) installs uv if it is missing, then
  `uv tool install eitri-hardware` from PyPI (or, if PyPI is unreachable, the tagged GitHub release archive), and runs `eitri doctor`.
  `./install.sh` in a checkout runs the same script against the checkout. It prints the download size and time first and never uses
  sudo (on Linux without libGL it prints the apt / dnf command).
- **`eitri update`** (`--check`, `--dry-run`, `--auto on|off`). It reads how eitri was installed (uv's receipt) and upgrades from
  PyPI, moves a git or archive install over to PyPI, or tells a checkout to `git pull && ./install.sh`. eitri asks pypi.org only when
  you run `eitri update` or `eitri doctor --online`, or once a day at startup if you turn that on; the answer is cached.
- **`eitri setup firmware`** (`--esp32`, `--yes`, `--dry-run`). Installs arduino-cli if missing (asking first: Homebrew on a Mac that
  has it, else Arduino's script into `~/.local/bin`), the AVR core and Servo (ESP32 core and ESP32Servo with `--esp32`), skips what is
  installed, and finishes with a test compile per board family.
- **`eitri --version`**, and `eitri doctor` lines for the installed version and the firmware toolchain.
- **PyPI releases.** A `v*` tag runs the `release` workflow: build, `scripts/check_dist.py` (every package file is in the wheel and
  sdist; pyproject, `__version__`, the installer's fallback tag and the tag agree), a clean install on macOS and Ubuntu with
  `eitri doctor`, then trusted publishing of that same sdist and wheel. The `install` workflow runs the build and install check on
  pushes to main and on pull requests.

### Changed

- **Sourcing rule, stated for every domain: the conservative figure.** Where sources disagree, the lower figure is used for a limit
  and the higher for a draw or load (the docs said "the lower figure"). The harness, power and mechanism catalogs only ever applied
  it to limits, so no number there changes. In the new electronics catalog the MG996R draws 2.5 A at stall (the distributor's figure
  at 6 V; TowerPro says 1.4 A) within TowerPro's narrower 4.8-6.6 V; the SG90's 0.5 A stays an ASSUMPTION (no stall figure is
  published).
- **`eitri doctor`**: a missing arduino-cli (or AVR core) is `info`, not `warn`: only firmware projects need it. Lines now start with
  `ok`, `info`, `warn` or `FAIL`.
- **README** images and links are absolute GitHub URLs, so they show on the PyPI page.
- The firmware "compiler unavailable" hint points at `eitri setup firmware`; arduino-cli is also looked for in `~/.local/bin`,
  `/opt/homebrew/bin` and `/usr/local/bin`.

### Fixed

- **A project folder that holds its own virtualenv builds.** The build worker forgets the project's own helper modules between
  builds; it also forgot every installed package under the folder (numpy, the CAD kernel), and the next import failed with
  "cannot load module more than once per process". It now keeps installed packages and eitri itself.

### Breaking for hosts

- **A new domain kind, `electronics`.** `eitri.domains.kind_of_source()` returns `"electronics"` for a model that starts
  `from eitri.electronics import *`, `domains.LABELS` gains it, `result.json` gains an `"electronics"` block for such builds, and the
  agent's tool list gains `electronics_reference()` (a sixth reference tool). A host that maps core kinds to its own domains must add
  it or route it elsewhere: eitri.io's `cadhost/domains.py` `KIND` has no entry for it yet.
- **`eitri doctor` has a fourth status, `info`.** Anything that parses its lines or `--json` `status` values must accept it
  (`ready` is unchanged: only `FAIL` makes it false).
- **Electronics analysis objects.** `Analysis.branches` now lists only networks the nodal solver could not solve (it listed every
  branch node), `Analysis.networks` is new, and a `String` can end at a branch node (`String.network`, `String.typical`).
- Nothing else: `state.json`, routes, export formats, the build-extension API, the shell slots and `APISession` are unchanged. The
  package name on PyPI is `eitri-hardware`; tags up to 0.5.6 installed from GitHub as `eitri-cad` (never on PyPI). The
  installer removes an `eitri-cad` tool before installing, and `eitri update` in one prints the switch:
  `uv tool uninstall eitri-cad && uv tool install eitri-hardware`. The command and the import stay `eitri`; installing from a
  git ref still works.

## 0.5.6: depth that holds at desk scale, and a cheaper API loop

### Changed

- **The API backend caches the whole conversation.** `APISession` (the Messages-API loop behind `eitri chat --backend api`, and
  the one eitri.io's hosted session subclasses) cached only the tools and system prompt, so every agent step paid full input price
  to re-send the history: tool results, review images, earlier thinking. It now also sets top-level automatic caching, so each
  step reads the conversation so far from cache and writes only what the last step appended. The history was already append-only
  and thinking blocks go back unchanged, so the cached prefix and preserved thinking both stay valid. Measured on the bench (4
  tasks plus 2 follow-ups, Opus 5.5, effort high): the cache-read share of the prompt went from 66% to 93%, and cost fell by about
  half ($2.72 to $1.34 for the same trajectories). The default effort is unchanged (`high`), and so are the requests when no new
  argument is set.
- **Usage per step.** Every API response is recorded in `APISession.step_log` (input, cache-write, cache-read and output
  tokens, cost, seconds, images carried in the history, stop reason). With `EITRI_USAGE_LOG` set to a file path, each record is
  also appended to that file as one JSON line. `TurnResult` from the API backend now carries the turn's `cost_usd`, `usage` and
  `duration_ms` (they were left at zero).

### Fixed

- **No more flicker between faces a millimetre apart.** The 3D view's near and far planes now follow the camera every frame,
  fitted to what is on screen with a margin, instead of a near plane at 1/200 of the model's radius and a far plane that only ever
  grew (to 100 m). A desk-scale model keeps its depth precision at any zoom.
- **Translucent parts draw in the right order.** Each translucent face (one mesh per B-rep face, all with their origin at the world
  origin) is ordered back to front by its own centre, every frame, rather than all at the origin in one arbitrary order; a translucent
  face that lies on an opaque one gives way to it in depth (polygon offset), the same from every side; translucent materials never
  write depth, so the opaque parts inside them show in their true colours.

### New for hosts

- `eitriViewer.setHostBounds(box)`: the bounds of a host's own scene (`hostGroup`), so the near and far planes fit it too while
  the editor's model is hidden; `eitriViewer.clip()` reads the current planes.
- **Effort per route.** `APISession(followup_effort=...)` sets the effort for a follow-up edit: a turn that starts on a project
  that already has a good build, so a resumed chat counts too. `send(effort=...)` sets the effort for one turn. Neither changes the
  session's top-level effort, which has to stay fixed for the cache to hold. Instead the turn's level goes out as an effort-only
  system message after the user turn (beta `mid-conversation-output-config-2026-07-01`), so the turn still reads the whole cached
  history. Neither is set by default. A host whose single turn spans several `send()` calls on a design that is still being
  created should not set `followup_effort`.
- `APISession(usage_log=...)`: a JSONL file for the per-step records (default `$EITRI_USAGE_LOG`).
- `bench.run --backend api` (with `--label` and `--followup-effort`): runs the bench through `APISession` with the configured key.
  It spends real money, with `--budget` enforced between steps. See `bench/README.md`.

### Breaking for hosts

None. `APISession`'s new arguments come last in the signature and default to off; with neither effort argument set it sends
the same effort as before. Requests gain top-level `cache_control`. `state.json`, routes, export formats, the build-extension API and
the shell slots are unchanged.

## 0.5.5: the agent is called eitri

### Changed

- **The agent is eitri in every message.** The editor, the local website, the CLI, the demos and the README call the agent
  eitri rather than Claude: "eitri is thinking", "eitri is writing the model", "Chat with eitri", "Create with eitri", "Watch eitri
  build it", and the error strings ("eitri is still working on the previous message"). Nothing else moved: the model ids, the
  `claude_cli` backend, the Claude Code binary and the Anthropic API key are still named for what they are, and the edit log's
  `by: "claude"` marker is unchanged so old timelines still read.

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
