# eitri plugin 0.9.2

Hardware design inside your agent, with checks that run on every build, all on your computer. The plugin adds skills and
two local MCP servers to Claude Code: the combined workspace, which designs a whole machine (structure, mechanism, wiring,
firmware, pneumatics and its circuit) and checks every domain against the others, and six single domains (mechanical CAD,
wire harnesses, mechanisms, pneumatics, firmware, electronics). Every check names its source: a datasheet, a standard, a
vendor page, or an assumption that says so. There is no account and no API key: your agent does the design work on its
own subscription, and eitri never calls Claude.

## What it contains

- Skills: `design` (picks the combined workspace or one local domain, and says which), `combined`, and one skill per
  local domain (`cad`, `wires`, `motion`, `pneumatics`, `firmware`, `electronics`).
- Two MCP servers, both stdio processes on your computer, started by `bin/eitri-mcp`: `eitri` (`eitri mcp`, the six
  single domains) and `eitri-combined` (`eitri mcp --combined`, the combined workspace). A browser viewer on
  127.0.0.1 shows each build; it answers only this computer.

## It needs eitri installed

The servers are the `eitri` command (PyPI package `eitri-hardware`, Python 3.12, macOS or Linux; Windows is untested).
Install eitri and register the plugin in one line:

    curl -fsSL https://eitri.io/install | sh -s -- --plugin

With eitri installed, the plugin can also come from its marketplace inside Claude Code (2.1.275 or later):

    /plugin install eitri --marketplace SiddhuYen/eitri

If eitri is not installed, `bin/eitri-mcp` runs `eitri-hardware==0.9.2` once through uvx instead; that first download
(about 390 MB) usually takes longer than the agent's start-up limit, so install eitri first.

## What it downloads, and when

- Installing: the installer fetches uv from astral.sh if it is missing, then eitri and its dependencies from PyPI (about
  390 MB, or the matching GitHub release archive if PyPI is unreachable). The uvx fallback above downloads the same.
- Firmware only: the first time a design's firmware needs compiling, your agent runs `eitri setup firmware -y` (your agent
  asks you first), which downloads arduino-cli and the Arduino AVR core (about 65 MB) from Arduino or Homebrew;
  `--esp32` adds the ESP32 core from Espressif (about 1.2 GB).
- Updates: only when you run `eitri update` (it asks pypi.org). Nothing else is downloaded.

## What it sends

Your designs, files, prompts and chats stay on your computer. The plugin sends two anonymous counts to
https://eitri.io/api/ping: one `install` event when you run `eitri plugin install`, and one `project_created` event per
new project. Each holds exactly: the event name, a random install id (a uuid made once and kept in eitri's config folder,
tied to no account), the eitri version, the OS (darwin or linux), the agent app (claude-code, codex, cursor or unknown),
and, for `project_created`, the project's domain (combined, cad, wires, motion, pneumatics, firmware or electronics).
Nothing else: no prompts, files, paths, names or errors. eitri.io stores what it receives with the date, not the network
address. Turn the counts off with `eitri telemetry off` (or set `EITRI_TELEMETRY=off`; `EITRI_NO_PING=1` and
`DO_NOT_TRACK=1` work too); `eitri telemetry status` shows what would be sent.

Privacy policy: https://eitri.io/privacy

## License

FSL-1.1-MIT (Functional Source License, Version 1.1, MIT Future License): source-available. You may use, modify and
redistribute it for any purpose except offering a competing commercial product or service; each version becomes
MIT-licensed two years after it is released. Source: https://github.com/SiddhuYen/eitri
