# eitri design system

The look and the shell every eitri page shares: tokens, fonts, glyphs, and the app chrome (top bar, rail, status bar, panels, buttons,
⌘K palette, menus, dialogs). Plain CSS and plain JavaScript (custom elements), no build step and no dependencies. eitri.io uses it, and the
open-source core is meant to copy this folder as it is, so it knows nothing about accounts, projects storage, usage, or the
combined workspace. A host fills those in through three slots (see "Slots").

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
  topbarEnd: function (end, topbar) { /* fill the top bar's right-hand slot (end), after the page is parsed */ }
};
```

## Slots: what a host fills

| Slot | eitri.io (hosted) | the open-source core (local) |
|---|---|---|
| Top bar, right (`topbarEnd(end, topbar)`, or `slot="end"` children) | the usage meter (`.pill`, "$X of $4.00 today") and the account menu (Projects, Account, Admin, Sign out); "Sign in" when signed out | a "local" badge (`<span class="badge">local</span>`) |
| Rail, bottom (`railBottom`) | Projects (`/app`) | the local project list |
| Status bar (the `<footer class="status">` items) | tool, "free", core version, links | tool, core version, project path, build state |

The palette is a host function for the same reason: eitri.io adds Projects, Account and Sign out when someone is signed in.

## Accessibility

Focus is always visible (`:focus-visible`, in the tool colour). Pages start with a `.skip` link to `#main`. The rail's items are links with
tooltips that are also their accessible names; on a phone the tab bar shows the short names. Text colours meet WCAG AA in both themes
(the light theme uses darker accents: `--c-cad` #c2410c and so on); `--faint` is for borders and decorative separators, never text. `prefers-reduced-motion` turns transitions off.
