# Changelog

Release notes for earlier versions are on the GitHub releases page (https://github.com/SiddhuYen/eitri/releases) and in the
annotated tags (`git show v0.4.0`).

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
