// eitri design system: the tool glyphs, as <e-glyph name="cad|wires|motion|pneumatics|firmware|all|projects|...">. 24-unit grid, 1.6 strokes
// with round joins. Hosts may add their own: window.eitriGlyphs.mine = '<path d="..."/>' before the element is parsed.
(function () {
  'use strict';
  var G = window.eitriGlyphs = Object.assign({
    all: '<path d="M12 2.8 20.2 7.4v9.2L12 21.2l-8.2-4.6V7.4Z"/><path d="M3.8 7.4 12 12l8.2-4.6M12 12v9.2"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/>',
    cad: '<path d="M4 4.5h5.5v10H20v5H4Z"/><circle cx="6.75" cy="8.25" r="1.25"/><circle cx="16.5" cy="17" r="1.25"/>',
    wires: '<rect x="2.5" y="7" width="4.5" height="10" rx="1"/><rect x="17" y="7" width="4.5" height="10" rx="1"/><path d="M7 9.5c5 0 5 5 10 5M7 12c5 0 5 0 10 0M7 14.5c5 0 5-5 10-5"/>',
    motion: '<path d="M3.5 20.5h9"/><circle cx="8" cy="17" r="2"/><path d="m9.3 15.5 4.1-5.3"/><circle cx="14.6" cy="8.7" r="1.8"/><path d="m16.3 8 3.6-1.6M19.9 6.4l.6 2.3M19.9 6.4l-1-2.1"/>',
    pneumatics: '<rect x="2.5" y="8" width="11.5" height="8" rx="1.5"/><path d="M10 12h11.5M21.5 10v4M5.5 8V5h3v3"/><path d="M10 9v6"/>',
    firmware: '<rect x="6" y="6" width="12" height="12" rx="1.5"/><path d="M9.5 6V3M12 6V3M14.5 6V3M9.5 21v-3M12 21v-3M14.5 21v-3M6 9.5H3M6 12H3M6 14.5H3M21 9.5h-3M21 12h-3M21 14.5h-3"/><circle cx="9.3" cy="9.3" r=".9" fill="currentColor" stroke="none"/>',
    projects: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    user: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5"/>',
    more: '<circle cx="5.5" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="18.5" cy="12" r="1.3" fill="currentColor" stroke="none"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    share: '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18 14v5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 19V8a1.5 1.5 0 0 1 1.5-1.5H10"/>',
    pencil: '<path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17Z"/><path d="m14.5 7.5 3 3"/>',
    trash: '<path d="M4.5 7h15M10 11v6M14 11v6M6 7l1 12.5A1.5 1.5 0 0 0 8.5 21h7a1.5 1.5 0 0 0 1.5-1.5L18 7M9 7V4.5h6V7"/>',
    github: '<path d="M9 19c-4 1.3-4-2-5.5-2.5M14.5 21v-3.2c0-1 .1-1.6-.5-2.2 2.6-.3 5.5-1.3 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.3 4.3 0 0 0-.1-3.2s-1-.3-3.4 1.3a11.6 11.6 0 0 0-6 0C6.3 2.9 5.3 3.2 5.3 3.2a4.3 4.3 0 0 0-.1 3.2A4.6 4.6 0 0 0 3.9 9.6c0 4.7 2.9 5.7 5.5 6-.6.6-.6 1.2-.5 2.2V21"/>',
    doc: '<path d="M7 3.5h7l4 4v13H7Z"/><path d="M14 3.5v4h4M9.5 12h6M9.5 15.5h6"/>',
    out: '<path d="M14 4h5.5v16H14M10 8l-4 4 4 4M6 12h10"/>',
    gauge: '<path d="M4 18a8 8 0 1 1 16 0"/><path d="m12 18 4-6"/>'
  }, window.eitriGlyphs || {});
  if (window.customElements && !customElements.get('e-glyph')) {
    customElements.define('e-glyph', class extends HTMLElement {
      connectedCallback() {
        if (this.firstChild) return;
        this.setAttribute('aria-hidden', 'true');
        this.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
          (G[this.getAttribute('name')] || G.all) + '</svg>';
      }
    });
  }
})();
