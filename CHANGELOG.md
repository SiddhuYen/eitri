# Changelog

Release notes for earlier versions are on the GitHub releases page (https://github.com/SiddhuYen/eitri/releases) and in the
annotated tags (`git show v0.4.0`).

## Unreleased (0.8.1): PLC signal inputs, sourced industrial connectors, a command-line path from approval to package

### Changed

- **Signal nets are graded against the devices at their ends**, not the Arduino 20 mA pin figure for every `signal=True` net.
  Declare them: `h.mcu_pin(pin, board="uno")` (or a board connector type), `h.plc_input(pin, iec_type=1|2|3)` (IEC 61131-2:
  ON current and voltage, and a 2-wire sensor's OFF leakage, as drawn in TI's ISO1228 data sheet SLLSFN5B Figure 8-1, graph-read),
  `h.sensor_output(pin, part=...)` (the Festo SMT-8M-A and SMC D-M9B catalogue figures) or with the sensor's own `max_output_A=`.
  A signal net with no declared end is "not run" (it was a pass on the wrong limit). New check "signal net N input <pin>".
  The servo-arm demo and the harness reference examples declare their Uno / Nano pins.

### Added

- **Industrial connectors with sourced ratings**: `M8-3` (4 A, 60 V DC), `M8-4` (3 A, 30 V: the lower of Pepperl+Fuchs' 3 A / 4 A),
  `M12-4` (4 A, 250 V), `M12-5` (4 A, 60 V DC), all A-coded cordset ends (Pepperl+Fuchs data sheets), and `WAGO-2002-1201` (2.5 mm²
  push-in feed-through terminal: 20 A, 600 V per UL 1059, the lower of its UL and IEC figures; 0.25-4 mm²). New checks: "connector
  voltage" (a catalogue connector's DC rating against its nets' voltage) and a mm² wire-range "wires fit the ... terminals".
  Mirrored in `data/components/connectors.jsonl`.
- **`eitri build <model.py> --record [--set k=v] [--config c] [--note ...]`**: the command-line path from an approved ticket to a
  package. It writes the values into PARAMS and records a project revision through the same Project methods as the agent's tools:
  the plan gate refuses it before approval, and the package flags unplanned parameters as it does for the agent.

### Fixed

- "one gauge per net" on a thin wire with no `current_A` of its own (a cable's return core) now names the wire and says how to
  give it its own current; the check itself is unchanged.

## 0.8.0: tickets and checked change packages, a source on every check, fuses, pneumatic catalogue parts, reference parts

eitri for a shop that takes a ticket and hands back a checked change package. A ticket holds the customer's request and
acceptance criteria; the agent writes a plan; **only a person approves it**, in their own terminal; the package is a fresh
rebuild of the base and the result with every check's status and source, the diffs, the outputs and a draft change order,
and it is refused while any check fails or did not run unwaived. Every check in every domain now carries a structured
source. This release replaces the planned 0.7.1: its sourced figure fixes are here too.

Every changed figure cites its document, revision and page in the code. Where sources disagree the conservative figure
is used (the lower for a limit, the higher for a draw).

### Added

- **"Not run" is a check status.** Every check (and every `verify()` / reference check) has `status`: `pass | warn | fail |
  not_run`, in `result.json` (each check's `"status"`) and in `checks_not_run` (now in the CAD report too). The older
  form still works: a not-run check keeps severity `warn` and text starting "not run: ". The fuse, pneumatic fail-safe and
  cushioning, reference-clearance and other switched-off checks use it, and so does the package's refusal rule.
- **Change packages cite the structured sources**: each check row of `package.json` has `source` (text, revision, URL),
  `sources` (the records) and `rule`; `checks.csv` adds `rule`, `source_basis` and `source_urls`; report.md/html print
  the same text. No row with a rule says "no document cited" any more, in any domain.
- **Rules and sources for the new checks**: harness overcurrent protection (Littelfuse ATOF / MINI / 487 / 477, Bourns
  MF-R, Blue Sea Systems / ABYC E-11, Arduino's 20 mA pin rating, each with the address it was read at), pneumatic
  catalogue checks (each part's catalogue page), end-of-stroke energy (SMC RB), fail-safe (SMC M-E03-3-02, SY symbols),
  and the reference-part checks (`cad.reference-*`). `tests/test_check_sources.py` scans every module that makes checks.
- **One person-only confirmation**: `eitri reference add|accept|remove` use the same barriers as `eitri ticket approve`
  (refused in an agent's process, a one-time code typed back on `/dev/tty`, a signed record in `eitri.json`);
  `eitri reference list` says who accepted each file and whether the record's signature verifies.
- **Agent prompt**: a static section on tickets (plan first, a person approves with `eitri ticket approve`, never claim
  approval), sources, waivers and customer files in every domain's prompt and plugin skill; pneumatics: declare
  `on_power_loss=` / `on_air_loss=`.
- **Public API for hosts**: `eitri.electronics.checks.rail_loads`, `rail_limit`, `gate_voltage` (the `_`-prefixed names
  stay as aliases). `Actuator(stall_A=)` and `Actuator.stall_current()`: the model's figure, else the catalogue's sourced one
  (MG996R 2.5 A; SG90 2 A, an ASSUMPTION), else None (a check that needs it does not run). In the mechanism JSON.
- **MCP registry**: the `eitri-hardware` command (no arguments, or `mcp`: the local MCP server with the kernel loaded
  lazily, for `uvx eitri-hardware mcp`); `mcp-name: io.github.SiddhuYen/eitri` in the README; `glama.json`.
- **Pneumatics catalogue parts** (`part=`): Festo DSNU-20-...-P-A, SMC CDQ2B32-...DZ / CQ2B32, SMC SY3120 / SY3220 /
  SY5120 5/2 valves, SMC TU0604 tube, AS2201F-01-06 speed controller, AN05-M5 silencer, SMC D-M9B and Festo SMT-8M-A
  position sensors; every figure cited in `eitri/pneumatics/catalog.py` (datasheet URL, revision, page).
- **Pneumatics checks** on datasheet figures: the cylinder's computed force against the force its datasheet prints,
  minimum operating pressure (cylinders and pilot-operated valves), piston speed range, end-of-stroke kinetic energy
  against the cushioning (`load_mass_kg=`; "not run" without it), coil voltage, sensor target (magnetic piston, series)
  and voltage; tubes with `max_bar=` join the pressure-rating check; valves take `C_exhaust=` / `b_exhaust=` for the
  A/B -> R/S passage.
- **I/O list** (`<name>.io.md` / `.io.csv`): every solenoid coil a DO, every `c.sensor(...)` a DI, with signal name,
  function, part, voltage, current, connector and terminal, for the PLC programmer. `c.control_voltage(24)`.
- `examples/pneumatic-clamp-fixture/`: a pusher + clamp fixture built from these parts.
- **Pneumatics fail-safe check**: per cylinder, the state its valves give on a loss of electrical power and on a loss of air
  (5/2 spring return: rest position; 5/2 double solenoid: holds; 5/3: its centre; SMC M-E03-3-02) against the declared
  `on_power_loss=` / `on_air_loss=` (hold | extend | retract | release); "not run" when undeclared.
- `c.screw_in(a, b)` (a speed controller screwed into a cylinder port) and per-port `connections=`: the BOM counts a fitting
  only where a threaded port needs one (KQ2H part numbers from the KQ2 catalogue), a tee once (KQ2T).
- **Reference parts: design around a customer's existing part.** `reference_step("inputs/motor.step", name="motor",
  clearance=1.0, contact=["bracket"])` in a CAD model loads a STEP file as immutable geometry that joins the assembly,
  the viewer, the review image, the exports and the BOM (never a print plate). Helpers: `.bbox`, `.holes()` /
  `.bosses()` / `.hole(near=)` (axis, diameter, position, depth), `.mass_properties()` with a given density,
  `.shape` (a copy for booleans). Each build checks: the file still has the SHA-256 recorded in `eitri.json` (a
  changed file fails the build until a person runs `eitri reference accept`), the geometry is unmodified after direct
  edits (edits naming it are refused), no designed part overlaps it, parts not in `contact` keep `clearance`, and
  every `motor.mount(hole, part=, screw=, series=)` is coaxial within its stated tolerance with an ISO 273 clearance (as published by Albany County Fasteners)
  diameter. A declared reference, and its mounts, cannot be dropped (`eitri reference remove`). The file is treated
  as untrusted: it must be in `inputs/`, is capped (20 MB, 500k entities, 20k faces) and pre-scanned before it is
  parsed inside the build. New `eitri reference list|add|accept|remove`; example `examples/motor-bracket/`; docs
  `docs/reference-parts.md`.
- **ISO 273 clearance holes** as a sourced table: `ISO_273`, `iso273_clearance(size, series)` (fine/medium/coarse,
  M1.6-M30), from the Albany County Fasteners chart labelled ISO 273 (c) 2025; Optimas's chart agrees up to M10 and
  gives larger M12-M16 holes without claiming ISO 273. `screw_clearance()` already matched it.

- **Tickets and change packages** (`docs/tickets.md`). `eitri ticket new|show|list|plan|approve|waive|close` and
  `eitri package` open a ticket, take a plan and produce a change package. The agent gets the tools `ticket_new`,
  `ticket_show`, `ticket_list`, `ticket_plan` and `package`; none of them can approve, waive or close.
  - **Plan gate.** While the project's active ticket has no approved plan, the model cannot be written, built or
    restored.
  - **Person-only actions.** Only a person approves, waives or closes, in their own terminal with a one-time code. These
    actions refuse inside agent processes. Each approval is signed with a key kept outside the project, and editing the
    ticket voids it.
  - **The package.** It rebuilds the base and the result from scratch and contains:
    - `package.json` (schema `eitri.package/1`);
    - the report as Markdown and as self-contained HTML;
    - the before/after check table with each check's source;
    - parameter, parts/BOM and source diffs;
    - Rev B's outputs (STEP/STL/3MF and the drawing for CAD);
    - the revision table and a "Not checked" section;
    - a draft change order.
  - **Refusals.** No package when any check fails or did not run. A person may waive a check that did not run, with a
    reason printed on page one; a failed check can never be waived.
  - **New dependency:** PyYAML.
- **Electronics**: `c.battery(name, chemistry, cells)` and `v_max=` on a supply. A battery straight on VIN is
  graded at its full charge (a 12 V lead-acid on charge, 14.5 V, fails an Uno's 7-12 V); a battery on a board's 5V
  pin fails. A MOSFET gate driven from an Uno or Nano pin with no pull-down is a warning (the pin floats through reset).
- **Checks switched off by an unset input are reported**: an actuator with no `travel_deg`, a supply with no rating,
  a load with no voltage range and the like. The check is a warning whose text starts "not run:", the summary has a
  "Not run" line, and the build's `result.json` report lists them under `checks_not_run`.
  `Actuator(continuous=True)` declares an actuator that turns without end.
- **Every check names its source, in every domain**: each check result has a `source` (a list of `{text, url, rev,
  basis}`), a `kind` (`"rule"`: every check is a deterministic rule) and a `rule` id (`"harness.wire-ampacity"`), in
  the text report and in `result.json`. `basis` says what the limit is: a cited `document`, a `requirement` the person
  gave ("design requirement": `max_drop_pct`, a target, a load, every `verify()` check), a `model` figure the model
  file gives (`rating_a=`, `stall_kgcm=`), an eitri `rule` with no figure (connectivity), a `tool` verdict
  (arduino-cli), or an `assumption` (an ESTIMATE with no source, listed rather than hidden). A check line ends with
  `[source: ...]`; the documents' URLs follow the checks as a numbered `Sources:` list. Each domain's rule table is
  `eitri/<domain>/rules.py`. A build extension's check that gives no source says so.
- **Harness overcurrent protection** (DC up to 60 V). `h.source(pin, voltage_V, max_current_A=, short_circuit_A=)`
  declares a supply or battery; `Fuse(name, type, rating_A)` and `Breaker(...)` are two-terminal parts (LINE, LOAD) wired
  in line. Catalog: Littelfuse ATO and MINI blade fuses, 5x20 mm cartridge fuses with DC ratings and Bourns MF-R PTCs,
  each figure with its datasheet; any other part takes its figures from the model file. New checks: every branch fed from
  a source that can deliver more than its thinnest wire's ampacity has a device before its loads; the device's rating (a
  PTC's trip current) is at most the thinnest wire it protects; the steady load is within the maker's continuous-load
  derating at `Harness(ambient_C=)` (default 40 °C, said in the report); the DC voltage rating covers the system
  voltage; the source's short-circuit current is within the interrupting rating; the device is within 175 mm of the
  source (ABYC E-11). A missing figure (a source with no short-circuit current, a supply net with no declared source) is
  "not run", never a pass. AC sources and sources above 60 V are refused; a net above 60 V fails as out of scope.
  `h.net(..., signal=True)` marks a net driven by a logic pin (20 mA at most). New example:
  `eitri/demos/servo-arm/servo_arm_harness.py` (one ATO 5 A fuse at a Mean Well 5 V supply).
  Existing harnesses that declare no source now show one "not run" warning for their supply nets (`quad-power`: 28
  pass, 6 warn).

### Changed

- **Fasteners**: countersinks take the ISO 10642:2020 head diameter (M3 6.72, M5 11.2, M6 13.44, M8 17.92, M10 22.4,
  M12 26.88 mm; M4 8.96). Heat-set insert holes follow ruthex: M2.5 4.0 mm, M8 9.6 mm.
- **Bearings**: a bare miniature bearing name is the open bearing (683 is 2 mm wide); `683ZZ` / `-2RS` give the
  shielded width (3 mm). Eleven sizes changed (NSK E126i).
- **Harness**: Micro-Fit 3.0 is rated by wire size and by the number of powered circuits (Molex PS-43045 Rev P,
  wire-to-wire: 18 AWG 7 A at 2 circuits down to 5 A at 24). 40 AWG chassis rating 0.09 A. FASTON cites Rev C and
  adds 10 AWG 24 A.
- **Pneumatics**: the load-ratio limit follows the cylinder's new `duty=`: `"moving"` 0.5 (the default),
  `"static"` 0.7 (clamping), `"guided"` 1.0 (SMC). A regulator with no rating defaults to 10 bar (SMC AR). Default
  flow estimates: silencer C 1.0 (SMC AN05), flow control C 0.3 (SMC AS M5); a speed controller's free-flow
  direction passes no more than its throttle.
- **Pneumatics, air consumption**: the lines between valve and cylinder are refilled from atmospheric, so they take
  V x p_gauge / p_atm per stroke, not x p_abs / p_atm (SMC Best Pneumatics technical data, Data 2: 2 m of 6 mm bore at
  0.5 MPa is 0.56 L ANR per cycle; eitri gave 0.67). The swept-volume and required-flow formulas already matched SMC.
- **Pneumatics, end-of-stroke energy**: the impact speed is 2 x the mean speed (SMC RB catalogue p.5: "collision speed
  = 2 x average speed" when the speed comes from the stroke time); it was the mean speed.
- **Pneumatics, BOM fittings**: no more fitting per tube end on built-in push-in ports and tees (they were double-counted).
- **Pneumatics, default valve C**: 0.6, b 0.29 (the smallest body-ported SMC SY3000, CAT.EUS11-83 p.3); the old 1.0 came
  from the series' best-case cover figure.
- **Servos**: SG90 draws 2 A at stall (the higher of TowerPro's "0.5A - 2A"). STS3215 16.5 kg·cm stall, 4 rated at
  6 V (Feetech spec sheet). An actuator with no published continuous rating is assumed at 1/5 of stall (was 1/3).
- **Uno on VIN**: the 5 V regulator's heat uses 85 °C/W, the worst board in the SPX1117 datasheet's Table 1 (was 46,
  its best case). At 9 V on VIN the 5V pin gives about 0.29 A (was 0.54 A).

### Fixed

- `curl -fsSL https://eitri.io/install | sh -s -- --plugin` on a machine with no agent app: eitri installs and the script
  exits 0 with "install Claude Code, Codex or Cursor, then run eitri plugin install" (it exited 1 pointing at FAIL lines
  that did not exist). A real registration failure still exits 1.

### Known limits

- Approval is a barrier against an agent approving by accident or through eitri's tools, not a security boundary against
  code running as the same user (it can unset the markers or read the signing key). A signed approval verifies only on the
  machine that made it. The real-terminal flow (a person typing the code) is tested with a stand-in terminal.
- One model file per ticket; combined (multi-domain) projects are hosted-only and have no tickets. A removed check (a servo
  swap renames it) is flagged on page one, not refused. A firmware package is refused wherever arduino-cli is missing.
- A harness that declares no source gets a "not run" warning, not a fail, so a package for it is refused until a person
  waives it or the source is declared. The quad-power demo declares none (it would need fuses past the catalogue's 40 A).
- The ISO 273 table is one manufacturer's ISO-labelled chart (Albany County Fasteners); the standard itself is paywalled
  and was not read. The check says so. Optimas differs at M12-M16.
- A reference file is accepted on its first build (trust on first use); hosted builds cap inputs at 8 MB and have no
  upload or accept UI yet. Hole detection finds full-round cylinders only.
- Pneumatic catalogue: about 11 part numbers. The schematic has no device tags, sensors or title block; no manifolds, no
  cycle time. The end-of-stroke speed uses SMC's 2x factor, possibly over-conservative without a speed controller.
- CAD geometry findings (thin wall, knife edge) are report issues with no sourced figure (1.5 mm, 30 deg).
- Board and module outlines are not in `eitri.electronics.catalog` yet (hosted keeps them).

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
  multi-domain workspace on eitri.io (`eitri-combined`, `https://eitri.io/mcp`, the default for whole machines: CAD,
  mechanisms, harnesses, firmware and pneumatics; electronics is not part of combined projects yet), and
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

### Fixed

- **Countersinks were upside down.** `countersink_cutter`, `screw_hole_cutter(head="countersink")`,
  `cut_holes(head="countersink")` and the viewer's hole edit with a countersunk head made the cone narrow at the entry
  face and widest about one head-depth inside the part (an undercut a flat-head screw cannot seat in), on every face.
  The cone now opens at the entry face. A model that used these will change shape on its next build; the shipped demos
  do not use them (the racket holder's final model builds its own cutter).

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
