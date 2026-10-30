# eitri design system

The look and the shell every eitri page shares: tokens, fonts, glyphs, and the app chrome (top bar, rail, status bar, panels, buttons,
⌘K palette, menus, dialogs). Plain CSS and plain JavaScript (custom elements), no build step and no dependencies. eitri.io uses it, and the
open-source core is meant to copy this folder as it is, so it knows nothing about accounts, projects storage, usage, or the
combined workspace. A host fills those in through slots (see "Slots").

```
design/
  fonts/            IBM Plex Sans 400/500/600, JetBrains Mono 400/500 (woff2, SIL OFL 1.1: the licences are next to them)
  fonts.css         @font-face rules, relative to this folder
  tokens.css        colours (dark default, light), the five tool accents, type scale, radii, layout sizes
  shell.css         every component below
  glyphs.js         the icon set: window.eitriGlyphs and <e-glyph name="...">
  shell.js          <eitri-topbar>, <eitri-rail>, the ⌘K palette, menus, toasts, confirm/ask dialogs
```

## Using it

```html
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="design/fonts.css">
  <link rel="stylesheet" href="design/tokens.css">
  <link rel="stylesheet" href="design/shell.css">
  <script src="design/glyphs.js"></script>
  <script>window.EITRI_SHELL = { /* the host's configuration, see below */ };</script>
  <script src="design/shell.js"></script>
</head>
<body class="app" data-tool="cad">
  <a class="skip" href="#main">Skip to the content</a>
  <eitri-topbar crumbs='[["eitri","/"],["cad"]]' tag="free"></eitri-topbar>
  <eitri-rail active="cad"></eitri-rail>
  <main id="main" class="canvas">
    <div class="viewport">…</div>
    <aside class="inspector">…</aside>
  </main>
  <footer class="status"><span><span class="dot" aria-hidden="true"></span>eitri CAD</span><span class="sp"></span><a href="…">…</a></footer>
</body>
</html>
```

Load the scripts in `<head>`: the elements must be defined before the body is parsed so the chrome renders without a flash.
eitri.io bundles the same files into one script (`scripts/build_ui.py`); the core can link them one by one.

`<body class="app">` is a grid: top bar across the top, the rail down the left, the canvas, the status bar along the bottom. Under 760 px
the rail becomes a bottom tab bar, the top bar sticks, and the canvas scrolls.

## Tokens (`tokens.css`)

- Surfaces, darkest to lightest: `--bg`, `--bg-2`, `--panel`, `--raised`, `--raised-2`. Lines: `--line`, `--line-2`.
  Text: `--fg`, `--fg-2`, `--muted`; `--faint` for borders and decorative separators. States: `--ok`, `--bad`, `--warn` and their `-soft` backgrounds. `--scrim` behind dialogs.
- Tool accents: `--c-cad`, `--c-wires`, `--c-motion`, `--c-pneumatics`, `--c-firmware`, and `--c-all` (neutral, for anything that is not
  one tool). `data-tool="<tool>"` on any element sets `--tool` for everything inside (`--tool-ink`, the text colour on it, is set per theme); components
  use `--tool` for focus rings, the active rail item, primary buttons, pressed chips.
- Theme: dark by default, light under `prefers-color-scheme: light`; `data-theme="dark|light"` on `<html>` forces one.
- Type: `--font-ui` (Plex Sans), `--font-mono` (JetBrains Mono); sizes `--t-2xs` … `--t-2xl` and `--t-hero`. Radii `--r1` … `--r3`.
- Layout: `--top-h` (44px), `--rail-w` (56px), `--status-h` (26px), `--inspector-w` (380px).

## Components

**Top bar**: `<eitri-topbar crumbs='[["eitri","/"],["projects"]]' tag="beta">`. Renders the mark (a link to `home`), the crumbs
(JSON: `[label, href]` pairs; the last one is the current page, marked with the tool colour), an optional `tag`, the "Go to… ⌘K" button (only when `EITRI_SHELL.palette` is set; the shortcut is bound only then too),
and the right-hand slot `.end`. Children with `slot="end"` are moved into `.end`. On a phone only the current crumb shows.

**Rail**: `<eitri-rail active="cad">`. Draws `EITRI_SHELL.rail` (tools, with a tooltip of name and one line each; an item with `sep: true` is
followed by a divider), then, if `EITRI_SHELL.railBottom` has items, a flexible gap, a divider and those items. `active` is the `key` of the current item (`aria-current="page"`).
`glyph` names an icon; when it is left out the item's `key` is used, so a tool's key is its icon. Bottom items are drawn neutral.

**Status bar**: `<footer class="status">`: items left to right, `<span class="sp"></span>` pushes the rest right. `.dot` is a tool-coloured
dot, `.ok` / `.bad` colour an item.

**Canvas**: `<main class="canvas">` with a `.viewport` (the 3D view; `.vp-label`, `.vp-hint`, `.vp-axes` overlays) and an `.inspector`
on the right. `<main class="canvas single">` scrolls one column instead, holding a `.page` (`.page-head` with `h1` and `.sub`; `.page-narrow`
for a centred column). Add `.gridbg` for the dotted engineering grid.

**Panels**: `.panel` (a bordered surface; `h2` inside is a small mono section label with a rule), `.insp-head`, `.eyebrow` with
`.pill-tag`, `.sect` (a section of the inspector), `.note`, `.rows` (bordered list), `.pair`, `dl.props` (label/value grid).

**Buttons and inputs**: `.btn` (`.primary` in the tool colour, neutral on `data-tool="all"` or with `.neutral`; `.ghost`, `.danger`, `.sm`,
`.icon`), `.input`, `.field` (an input with a button beside it), `label.lbl`, `.check`, `.hint`, `.prompt` (a text area with a `.bar` of actions), `.fine` (small print).
`.chips` of `.chip` buttons use `aria-pressed`; `.badge` is a small mono label (with a glyph, in the tool colour under `data-tool`).
`.pill` is the rounded mono status pill (`.bar` with an `i` for a fill, `.out` for exhausted).

**Notices**: `.notice` (`.warn`, `.bad`; `.acts` for buttons on the right).

**Cards**: `.cards` is a responsive grid of `.card` (each with `data-tool`): `.thumb` (an image, or a glyph), `.flags` (small labels over
the thumbnail), `.body` with `.name` (a link whose click area is the whole card), `.meta`, and an optional `.more` button for a menu.
`.empty` is the one-line empty state.

**Documents and tables**: `.doc` (long text: terms, privacy), `table.data`, `.stats` (a row of big numbers), `.meterbar`.

**Command palette**: ⌘K / Ctrl K, or the top bar's "Go to…" button, opens a modal list of `EITRI_SHELL.palette()` items, grouped by `g`,
filtered by what is typed (name, description and `kw`). An item has `href` or `run()`, and a `glyph` or a `tool` (its glyph and colour).
It is a combobox over a listbox: arrows move, Enter opens, Escape closes and returns focus.

**Menus**: `eitriShell.menu(button, list)` turns `list` into a `role="menu"` popup for `button` (`aria-haspopup`, `aria-expanded`,
`aria-controls`). Items are elements with `role="menuitem"`; `.head` is a heading, `.sep` a divider. Arrow keys, Home/End, Escape (back to
the button), Tab, an outside click, scroll or resize close it. The list is moved to `<body>` so no container clips it.

**Dialogs and toasts**: `eitriShell.confirm({title, body, ok, danger})` → `Promise<boolean>`;
`eitriShell.ask({title, label, value, ok, max})` → `Promise<string|null>` (a text field, preselected); both are native `<dialog>`s with
focus kept inside. `eitriShell.toast(text, {bad})` shows a short message in a polite live region. `dialog.dialog` styles any other dialog.

**Glyphs**: `<e-glyph name="cad">`, 20 px, `currentColor`. Names: `all cad wires motion pneumatics firmware projects search user more plus
share pencil trash github doc out gauge`. `window.eitriGlyphs[name]` is the SVG markup.

All text that comes from outside the page (crumbs, palette items, menu labels, dialog text) is set with `textContent`, never as HTML.

## The host's configuration: `window.EITRI_SHELL`

Set it before `shell.js` runs. Every field is optional.

```js
window.EITRI_SHELL = {
  home: '/', mark: '/assets/mark.svg', brand: 'eitri',
  rail: [{ key: 'cad', href: '/cad', name: 'eitri CAD', short: 'CAD', line: 'Mechanical parts' }, …],  // sep: true draws a divider after one
  railBottom: [{ key: 'projects', href: '/projects', name: 'Projects', short: 'Projects', line: '…', glyph: 'projects' }],
  palette: function () { return [{ g: 'Tools', n: 'eitri CAD', d: 'Mechanical parts', href: '/cad', tool: 'cad' }, …]; },
  topbarEnd: function (end, topbar) { /* fill the top bar's right-hand slot (end), after the page is parsed */ },
  // the editor only (eitri/viewer/index.html), all optional:
  place: function (state) { return { crumb: ['workspace', '/workspace'], railKey: 'all', toolName: 'eitri workspace' }; },
  switchModel: function (file) { /* make state.models[i].file current; may return a promise */ },
  models: function (state) { return state.models; },     // the files to show as tabs (null or [] for none)
  addModel: function (kind) { /* make a new model file of this kind ("cad", "harness", ...) */ },
  views: function (state) { return [{ key: 'assembly', name: 'Assembly', glyph: 'all', active: false }]; },   // the host's own views, as tabs
  switchView: function (key) { /* show that view (null: back to the files); may return a promise */ }
};
```

## Slots: what a host fills

| Slot | eitri.io (hosted) | the open-source core (local) |
|---|---|---|
| Top bar, right (`topbarEnd(end, topbar)`, or `slot="end"` children) | the usage meter (`.pill`, "$X of $4.00 today") and the account menu (Projects, Account, Admin, Sign out); "Sign in" when signed out | a "local" badge (`<span class="badge">local</span>`) |
| Rail, bottom (`railBottom`) | Projects (`/app`) | the local project list |
| Status bar (the `<footer class="status">` items) | tool, "free", core version, links | tool, core version, project path, build state |

| The editor's place (`place(state)`) | its own crumb, rail item and status name for a project of several files | none: the current file's tool |
| Model file tabs (`switchModel(file)`, `models(state)`, `addModel(kind)`) | a tab per file, and Add | none: no tabs |
| The host's own views (`views(state)`, `switchView(key)`) and the viewer (`window.eitriViewer`) | an Assembly view of a combined project, drawn into the editor's scene | none |

The palette is a host function for the same reason: eitri.io adds Projects, Account and Sign out when someone is signed in.

### The editor's place: `place(state)`

By default the editor takes its crumb (`eitri / <tool> / <title>`), the rail item it marks and the status bar's tool name (`#st-tool`)
from the current model file's kind. `place(state)` is called with the editor's state on every redraw; when it returns an object, each
field it has replaces one of the three, and when it returns nothing the defaults stay:

- `crumb`: `[label, href]` (or a label) for the middle crumb;
- `railKey`: the `key` of the rail item to mark `aria-current="page"`, a bottom item included (every other is cleared);
- `toolName`: the status bar's tool name.

The page keeps the current file's accent (`data-tool`), its inspector and its Export menu: only where the page sits changes.

### More than one model file: tabs

A project folder may hold several model files (a CAD part, its wiring, its firmware): `state.models` lists them and `state.file` is
the current one. Every build, check and export the editor shows is the current file's: each file keeps its own last good build, so a
file that has never been built shows no build, whatever the others have. The open-source core never switches files itself (the agent's
host does, or `Project.set_model_file`), so locally there are no tabs and a project with one file looks as it always did.

When the host defines `switchModel(file)`, the editor draws a tab strip across the top of the canvas: one tab per file (`models(state)`,
default `state.models`), with the domain's glyph, its short name (and the file's name when two files share a kind), a dot for its last
build's checks (pass, warn, fail, or a ring before the first build) and the counts in its accessible name. It is a `role="tablist"`:
arrows move, Enter or Space opens. Opening a tab calls `switchModel(file)`, waits for it, then reloads the state; the page then clears
the last file's view (its geometry, domain editor, selection, checks baseline) and draws the new file's. The page follows a switch made
anywhere else too (the agent's `use_model`, another tab): it watches `state.file`. With `addModel(kind)` an "Add" button beside the tabs
opens a menu of the kinds the project has no file of; choosing one calls `addModel(kind)`, and the page follows whatever file the host
makes current.

`state.json` fields for this (additive: every other field is unchanged, and is the current file's):

| Field | |
|---|---|
| `file` | the current model file, e.g. `"wiring.py"` |
| `models` | `[{file, kind, name, current, ok_revision, checks: {pass, warn, fail}}]`, in the tools' order (CAD, wires, motion, pneumatics, firmware), then by file: `kind` is the domain (`"cad"`, `"harness"`, `"mechanism"`, `"pneumatics"`, `"firmware"`), `name` the file without `.py`, `ok_revision` its last good build (0: none), `checks` that build's verdicts counted as the editor counts them. One entry for a project with one file |

### A host's own view beside the tabs: `views(state)` and `switchView(key)`

A host may have something to show that is not one model file (eitri.io shows a combined project's domains together). `views(state)`
returns `[{key, name, short, glyph, active, verdict, said}]` and the editor draws one tab per entry after the file tabs: `glyph` names
the `<e-glyph>` (default `all`), `short` is the tab's text (default `name`), `verdict` (`ok`, `warn`, `bad`, `none`) draws the dot and
`said` goes in the title and the accessible name. Opening one calls `switchView(key)`; the editor then selects no file tab and sets
`<body data-view="key">`, and keeps drawing the current file underneath (its geometry, domain editor and inspector are untouched: the
host decides what covers them). A file tab while a view is up calls `switchView(null)` first, then `switchModel`. The host dispatches
`eitri:views` on the document when its views changed (a view opened or closed by its own controls), and the strip is drawn again.

### The viewer for a host's scripts: `window.eitriViewer`

The editor exposes its three.js scene to scripts the host adds to the page, so a host can draw into the 3D view instead of making
another: `scene`, `renderer`, `controls`, `camera()` (the one in use), `hostGroup` (a group of the host's own in the scene, kept across
model loads; everything else is the page's), `model()` (the loaded GLB), `parts()` (part name -> node) and `meshes()`, `bbox()`,
`centre()`, `radius()`, `rev()`, `state()`, `busy()`, `interacting()`, `setView(name)`, `frame(box, dir, factor, animate)` (look at a
`Box3` from `iso`, `hero`, `front`, `top`, ... or a direction), `addLabel(pos, text, cls)` / `clearLabels(cls)` (the HTML labels
layer, `labelLayer`), `light()` and `hideLoading()`. Three events on the document carry what the page learns: `eitri:state` (every
state poll; `detail` is the state), `eitri:events` (every agent poll; `detail` is the `events` answer, with `busy`, `live` and
`running`) and `eitri:model` (geometry placed or cleared; `detail` is `{rev, preview, parts}`, `rev` null when cleared). three.js
itself is on the page's import map (`import * as THREE from 'three'` in a module script gets the same instance).

## Accessibility

Focus is always visible (`:focus-visible`, in the tool colour). Pages start with a `.skip` link to `#main`. The rail's items are links with
tooltips that are also their accessible names; on a phone the tab bar shows the short names. Text colours meet WCAG AA in both themes
(the light theme uses darker accents: `--c-cad` #c2410c and so on); `--faint` is for borders and decorative separators, never text. `prefers-reduced-motion` turns transitions off.
