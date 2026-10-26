// eitri's own configuration of the design system's shell (assets/design, see its README): the open-source core's defaults for the
// three host slots. A host that serves these pages (eitri-hosted) fills the slots instead by defining window.EITRI_SHELL before this
// script runs: every page has a `<!-- eitri:host-head -->` marker in <head>, just before this script, for the host's own <script>.
// Whatever the host defines wins; anything it leaves out falls back to the defaults below.
//
//   window.EITRI_SHELL = {
//     host: true,                                  a host is in charge: the editor drops the OSS-only "local" defaults it adds later
//     topbarEnd(end, topbar) {},                   top bar, right-hand slot (hosted: usage meter + account menu; OSS: a "local" badge)
//     railBottom: [{key, href, name, short, line, glyph}],   rail, bottom slot (hosted: Projects; OSS: the local project list)
//     status(footer, info) {},                     status bar items, called after the page's own items are drawn (info: {tool, version, page})
//     rail, palette, home, mark, brand             as in the design README (the OSS rail is the five tools, nothing else)
//   }
(function () {
  'use strict';
  var script = document.currentScript;
  var root = script && script.getAttribute('data-root') || '/';            // where the local project list lives (the website's /)
  var assets = script ? script.src.replace(/shell-host\.js(\?.*)?$/, '') : root + 'assets/';
  var TOOLS = [
    { key: 'cad', name: 'eitri CAD', short: 'CAD', line: 'Mechanical parts and assemblies', kind: 'cad' },
    { key: 'wires', name: 'eitri Wires', short: 'Wires', line: 'Wire harnesses and cable assemblies', kind: 'harness' },
    { key: 'motion', name: 'eitri Motion', short: 'Motion', line: 'Linkages, joints and actuators', kind: 'mechanism' },
    { key: 'pneumatics', name: 'eitri Pneumatics', short: 'Pneumatics', line: 'ISO 1219 air circuits', kind: 'pneumatics' },
    { key: 'firmware', name: 'eitri Firmware', short: 'Firmware', line: 'Arduino pinouts and sketches, compiled', kind: 'firmware' }
  ];
  var host = window.EITRI_SHELL || {};
  var defaults = {
    home: root,
    mark: assets + 'mark.svg',
    brand: 'eitri',
    rail: TOOLS.map(function (t) { return { key: t.key, href: root + '?tool=' + t.key, name: t.name, short: t.short, line: t.line }; }),
    railBottom: [{ key: 'projects', href: root, name: 'Projects', short: 'Projects', line: 'Your projects on this computer', glyph: 'projects' }],
    palette: function () {
      var items = TOOLS.map(function (t) { return { g: 'Tools', n: t.name, d: t.line, href: root + '?tool=' + t.key, tool: t.key }; });
      items.push({ g: 'Pages', n: 'Projects', d: 'Your projects on this computer', href: root, glyph: 'projects' });
      items.push({ g: 'Pages', n: 'Source on GitHub', d: 'github.com/SiddhuYen/eitri', href: 'https://github.com/SiddhuYen/eitri', glyph: 'github' });
      return items;
    },
    topbarEnd: function (end) {
      var b = document.createElement('span');
      b.className = 'badge local-badge';
      b.title = 'eitri runs on this computer: your projects stay in a folder here';
      b.textContent = 'local';
      end.appendChild(b);
    }
  };
  var cfg = {};
  Object.keys(defaults).forEach(function (k) { cfg[k] = defaults[k]; });
  Object.keys(host).forEach(function (k) { cfg[k] = host[k]; });
  window.EITRI_SHELL = cfg;
  // the status bar slot: the page draws its own items (ids #st-tool, #st-version, #st-where, #st-path, #st-build, #st-combined),
  // then a host's status(footer, info) adds or hides items, once the page is parsed
  function statusSlot() {
    var footer = document.querySelector('footer.status');
    if (footer && typeof cfg.status === 'function') cfg.status(footer, { page: document.body.classList.contains('editor') ? 'editor' : document.body.classList.contains('home') ? 'projects' : 'page', tool: document.body.dataset.tool });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', statusSlot, { once: true }); else statusSlot();
  // the tools, for the pages: a project's kind (cad, harness, ...) <-> its tool (cad, wires, ...)
  window.eitriTools = {
    list: TOOLS,
    ofKind: function (kind) { for (var i = 0; i < TOOLS.length; i++) if (TOOLS[i].kind === (kind || 'cad')) return TOOLS[i]; return TOOLS[0]; },
    byKey: function (key) { for (var i = 0; i < TOOLS.length; i++) if (TOOLS[i].key === key) return TOOLS[i]; return null; },
    root: root
  };
})();
