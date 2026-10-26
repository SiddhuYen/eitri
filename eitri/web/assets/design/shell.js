// eitri design system: the shell's behaviour. Load it (after glyphs.js and the host's config) in <head>, before the body is parsed.
//
//   window.EITRI_SHELL = {                         the host's configuration (set before this script runs; every field optional)
//     home: '/', mark: '/assets/mark.svg', brand: 'eitri',
//     rail:   [{key, href, name, short, line, glyph, sep}],    the rail's items (tools); sep: a divider after this one
//     railBottom: [{key, href, name, short, line, glyph}],     the rail's bottom slot (hosted: Projects; OSS: local projects)
//     palette(): [{g, n, d, href | run(), glyph | tool}],      the ⌘K palette's items (a function: they may depend on who is signed in)
//     topbarEnd(end, topbar) {}                                 fills the top bar's right-hand slot (end) once the page is parsed (hosted: meter + account; OSS: a badge)
//   }
//
// Elements: <eitri-topbar crumbs='[["eitri","/"],["projects"]]' tag="private beta">, <eitri-rail active="cad">.
// Utilities on window.eitriShell: palette(), menu(button, list), toast(text, {bad}), confirm({...}), ask({...}), el(tag, attrs, text).
// Everything that shows text from outside the page sets it with textContent.
(function () {
  'use strict';
  var cfg = function () { return window.EITRI_SHELL || {}; };

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    return e;
  }
  function glyph(name) { return el('e-glyph', { name: name }); }
  var MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  function whenParsed(fn) { if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn, { once: true }); else fn(); }

  // ---------------------------------------------------------------- <eitri-topbar>
  class Topbar extends HTMLElement {
    connectedCallback() {
      if (this._done) return;
      this._done = true;
      if (!this.hasAttribute('role')) this.setAttribute('role', 'banner');
      var c = cfg(), home = el('a', { 'class': 'home', href: c.home || '/', 'aria-label': (c.brand || 'eitri') + ': home' });
      home.appendChild(el('img', { src: c.mark || '/assets/mark.svg', alt: '', width: '22', height: '22' }));
      this._crumbs = el('nav', { 'class': 'crumbs', 'aria-label': 'You are here' });
      this.prepend(home, this._crumbs, el('span', { 'class': 'sp' }));
      if (c.palette) {
        var b = el('button', { 'class': 'cmdk', type: 'button', 'data-palette': '', 'aria-label': 'Go to a tool or a page', 'aria-keyshortcuts': 'Meta+K Control+K' });
        b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/></svg>';
        b.appendChild(el('span', { 'class': 'lbl' }, 'Go to…'));
        b.appendChild(el('kbd', {}, MAC ? '⌘K' : 'Ctrl K'));
        b.addEventListener('click', function () { palette(); });
        this.appendChild(b);
      }
      this.end = el('div', { 'class': 'end' });
      this.appendChild(this.end);
      this.renderCrumbs();
      var self = this;
      whenParsed(function () {                     // the page's own slot content: <... slot="end"> children
        self.querySelectorAll(':scope > [slot="end"]').forEach(function (n) { n.removeAttribute('slot'); self.end.appendChild(n); });
        if (typeof cfg().topbarEnd === 'function') cfg().topbarEnd(self.end, self);
      });
    }
    static get observedAttributes() { return ['crumbs', 'tag']; }
    attributeChangedCallback() { if (this._done) this.renderCrumbs(); }
    renderCrumbs() {
      var nav = this._crumbs, list = [];
      try { list = JSON.parse(this.getAttribute('crumbs') || '[]'); } catch (e) { list = []; }
      nav.textContent = '';
      list.forEach(function (c, i) {
        var last = i === list.length - 1;
        if (i) nav.appendChild(el('span', { 'class': 'sep up', 'aria-hidden': 'true' }, '/'));
        if (!last && c[1]) nav.appendChild(el('a', { 'class': 'up', href: c[1] }, c[0]));
        else nav.appendChild(el('span', last ? { 'class': 'here', 'aria-current': 'page' } : { 'class': 'up' }, c[0]));
      });
      var tag = this.getAttribute('tag');
      if (tag) nav.appendChild(el('span', { 'class': 'tag' }, tag));
    }
  }

  // ---------------------------------------------------------------- <eitri-rail>
  function railItem(it, active) {
    var a = el('a', { href: it.href, 'data-tool': it.tool || it.key, 'class': it.neutral ? 'neutral' : null, 'aria-current': it.key === active ? 'page' : null });
    a.appendChild(glyph(it.glyph || it.key));
    var tip = el('span', { 'class': 'tip' });
    tip.appendChild(el('span', { 'class': 'long' }, it.name));
    tip.appendChild(el('span', { 'class': 'short' }, it.short || it.name));
    if (it.line) tip.appendChild(el('small', {}, it.line));
    a.appendChild(tip);
    return a;
  }
  class Rail extends HTMLElement {
    connectedCallback() {
      if (this._done) return;
      this._done = true;
      this.setAttribute('role', 'navigation');
      if (!this.hasAttribute('aria-label')) this.setAttribute('aria-label', 'Tools');
      var c = cfg(), active = this.getAttribute('active'), self = this;
      (c.rail || []).forEach(function (it) {
        self.appendChild(railItem(it, active));
        if (it.sep) self.appendChild(el('span', { 'class': 'div', 'aria-hidden': 'true' }));
      });
      if ((c.railBottom || []).length) {
        this.appendChild(el('span', { 'class': 'grow', 'aria-hidden': 'true' }));
        this.appendChild(el('span', { 'class': 'div', 'aria-hidden': 'true' }));
        c.railBottom.forEach(function (it) { self.appendChild(railItem(Object.assign({ neutral: true }, it), active)); });
      }
    }
  }

  // ---------------------------------------------------------------- the command palette
  var dlg, input, list, items = [], sel = 0, opener = null;
  function buildPalette() {
    dlg = el('dialog', { 'class': 'palette', 'aria-label': 'Command palette' });
    var q = el('div', { 'class': 'q' });
    q.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/></svg>';
    input = el('input', { type: 'text', role: 'combobox', 'aria-expanded': 'true', 'aria-controls': 'pal-list', 'aria-autocomplete': 'list',
      'aria-label': 'Go to a tool or a page', placeholder: 'Go to a tool, a page…', autocomplete: 'off', spellcheck: 'false' });
    q.appendChild(input);
    list = el('ul', { id: 'pal-list', role: 'listbox', 'aria-label': 'Results' });
    var foot = el('div', { 'class': 'foot' });
    [['↑ ↓', 'move'], ['↵', 'open'], ['esc', 'close']].forEach(function (k) { var s = el('span'); s.appendChild(el('kbd', {}, k[0])); s.appendChild(document.createTextNode(' ' + k[1])); foot.appendChild(s); });
    dlg.append(q, list, foot);
    document.body.appendChild(dlg);
    input.addEventListener('input', function () { sel = 0; render(); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Home') { e.preventDefault(); sel = 0; mark(); }
      else if (e.key === 'End') { e.preventDefault(); sel = items.length - 1; mark(); }
      else if (e.key === 'Enter') { e.preventDefault(); go(items[sel]); }
    });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
    dlg.addEventListener('close', function () { if (opener && opener.focus) opener.focus(); });
  }
  function match(it, q) {
    if (!q) return true;
    var hay = (it.n + ' ' + (it.d || '') + ' ' + (it.kw || '') + ' ' + (it.tool || '')).toLowerCase();
    return q.toLowerCase().split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
  }
  function render() {
    var all = typeof cfg().palette === 'function' ? cfg().palette() : [];
    items = all.filter(function (it) { return match(it, input.value.trim()); });
    list.textContent = '';
    var last = null;
    items.forEach(function (it, i) {
      if (it.g !== last) { list.appendChild(el('li', { 'class': 'grp', role: 'presentation' }, it.g)); last = it.g; }
      var li = el('li', { id: 'pal-' + i, role: 'option', 'data-tool': it.tool || null });
      li.appendChild(glyph(it.glyph || it.tool || 'all'));
      li.appendChild(el('span', { 'class': 'n' }, it.n));
      li.appendChild(el('span', { 'class': 'd' }, it.d || ''));
      li.addEventListener('mousemove', function () { if (sel !== i) { sel = i; mark(); } });
      li.addEventListener('click', function () { go(it); });
      list.appendChild(li);
    });
    if (!items.length) list.appendChild(el('li', { 'class': 'empty', role: 'presentation' }, 'Nothing matches.'));
    mark();
  }
  function mark() {
    list.querySelectorAll('[role="option"]').forEach(function (li) { li.setAttribute('aria-selected', String(li.id === 'pal-' + sel)); });
    var cur = document.getElementById('pal-' + sel);
    if (cur) { input.setAttribute('aria-activedescendant', cur.id); cur.scrollIntoView({ block: 'nearest' }); } else input.removeAttribute('aria-activedescendant');
  }
  function move(d) { if (items.length) { sel = (sel + d + items.length) % items.length; mark(); } }
  function go(it) {
    if (!it) return;
    dlg.close();
    if (typeof it.run === 'function') it.run(); else if (it.href) location.href = it.href;
  }
  function palette() {
    if (!dlg) buildPalette();
    if (dlg.open) return;
    opener = document.activeElement;
    input.value = ''; sel = 0; render();
    dlg.showModal();
    input.focus();
  }
  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && !e.altKey && (e.key === 'k' || e.key === 'K') && cfg().palette) { e.preventDefault(); palette(); }
  });

  // ---------------------------------------------------------------- menus: a button that opens a role="menu" list
  var openMenu = null;
  function closeMenu(focusBack) {
    if (!openMenu) return;
    var m = openMenu; openMenu = null;
    m.list.hidden = true;
    m.button.setAttribute('aria-expanded', 'false');
    if (focusBack) m.button.focus();
  }
  function menu(button, listEl) {
    button.setAttribute('aria-haspopup', 'menu');
    button.setAttribute('aria-expanded', 'false');
    listEl.setAttribute('role', 'menu');
    listEl.classList.add('menu');
    listEl.hidden = true;
    if (!listEl.id) listEl.id = 'menu-' + Math.random().toString(36).slice(2, 8);
    button.setAttribute('aria-controls', listEl.id);
    document.body.appendChild(listEl);                       // out of any clipping container
    var entries = function () { return Array.prototype.slice.call(listEl.querySelectorAll('[role="menuitem"]')); };
    function place() {
      var r = button.getBoundingClientRect(), w = listEl.offsetWidth || 200, h = listEl.offsetHeight || 120;
      var left = Math.min(Math.max(8, r.right - w), innerWidth - w - 8), top = r.bottom + 4;
      if (top + h > innerHeight - 8) top = Math.max(8, r.top - h - 4);
      listEl.style.position = 'fixed'; listEl.style.left = left + 'px'; listEl.style.top = top + 'px';
    }
    function open(focusLast) {
      if (openMenu && openMenu.list !== listEl) closeMenu(false);
      openMenu = { button: button, list: listEl };
      listEl.hidden = false; place();
      button.setAttribute('aria-expanded', 'true');
      var e = entries(); if (e.length) (focusLast ? e[e.length - 1] : e[0]).focus();
    }
    button.addEventListener('click', function (ev) { ev.stopPropagation(); if (openMenu && openMenu.list === listEl) closeMenu(true); else open(false); });
    button.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowDown') { ev.preventDefault(); open(false); } else if (ev.key === 'ArrowUp') { ev.preventDefault(); open(true); }
    });
    listEl.addEventListener('keydown', function (ev) {
      var e = entries(), i = e.indexOf(document.activeElement);
      if (ev.key === 'ArrowDown') { ev.preventDefault(); e[(i + 1) % e.length].focus(); }
      else if (ev.key === 'ArrowUp') { ev.preventDefault(); e[(i - 1 + e.length) % e.length].focus(); }
      else if (ev.key === 'Home') { ev.preventDefault(); e[0].focus(); }
      else if (ev.key === 'End') { ev.preventDefault(); e[e.length - 1].focus(); }
      else if (ev.key === 'Escape') { ev.preventDefault(); closeMenu(true); }
      else if (ev.key === 'Tab') { closeMenu(true); }        // back on the button first, so Tab moves on from there
    });
    // capture: the menu closes and focus is back on its button before the item's own handler runs (a dialog it opens returns focus there)
    listEl.addEventListener('click', function (ev) { if (ev.target.closest('[role="menuitem"]')) closeMenu(true); }, true);
    return { open: open, close: function () { closeMenu(false); } };
  }
  document.addEventListener('click', function (e) { if (openMenu && !openMenu.list.contains(e.target)) closeMenu(false); });
  window.addEventListener('blur', function () { closeMenu(false); });
  window.addEventListener('resize', function () { closeMenu(false); });
  document.addEventListener('scroll', function () { closeMenu(false); }, true);

  // ---------------------------------------------------------------- toasts and dialogs
  var toasts;
  function region() {                                       // the live region exists before its first message, so that one is announced
    if (!toasts && document.body) { toasts = el('div', { 'class': 'toasts', role: 'status', 'aria-live': 'polite' }); document.body.appendChild(toasts); }
  }
  whenParsed(region);
  function toast(text, o) {
    region();
    var t = el('div', { 'class': 'toast' + (o && o.bad ? ' bad' : '') }, text);
    toasts.appendChild(t);
    setTimeout(function () { t.remove(); }, (o && o.ms) || 4200);
    return t;
  }
  function dialog(o, withInput) {
    return new Promise(function (resolve) {
      var back = document.activeElement;                    // focus goes back here when the dialog closes
      var d = el('dialog', { 'class': 'dialog', 'aria-labelledby': 'dlg-h' });
      var f = el('form', { 'class': 'in', method: 'dialog' });
      f.appendChild(el('h2', { id: 'dlg-h' }, o.title));
      if (o.body) { f.appendChild(el('p', { id: 'dlg-p' }, o.body)); d.setAttribute('aria-describedby', 'dlg-p'); }
      var inp = null;
      if (withInput) {
        f.appendChild(el('label', { 'class': 'lbl', 'for': 'dlg-in' }, o.label || 'Name'));
        inp = el('input', { 'class': 'input', id: 'dlg-in', maxlength: String(o.max || 80), autocomplete: 'off' });
        inp.style.width = '100%';
        inp.value = o.value || '';
        f.appendChild(inp);
      }
      var acts = el('div', { 'class': 'acts' });
      var no = el('button', { type: 'button', 'class': 'btn' }, o.cancel || 'Cancel');
      var yes = el('button', { type: 'submit', 'class': 'btn ' + (o.danger ? 'danger' : 'primary') }, o.ok || 'OK');
      acts.append(no, yes); f.appendChild(acts); d.appendChild(f); document.body.appendChild(d);
      var result = null, done = false;
      function finish(v) { if (done) return; done = true; result = v; d.close(); }
      no.addEventListener('click', function () { finish(null); });
      f.addEventListener('submit', function (e) { e.preventDefault(); finish(withInput ? inp.value : true); });
      d.addEventListener('click', function (e) { if (e.target === d) finish(null); });
      d.addEventListener('close', function () { d.remove(); if (back && back.isConnected) back.focus(); resolve(done ? result : null); });
      d.showModal();
      (inp || (o.danger ? no : yes)).focus();
      if (inp) inp.select();
    });
  }

  if (window.customElements) {
    if (!customElements.get('eitri-topbar')) customElements.define('eitri-topbar', Topbar);
    if (!customElements.get('eitri-rail')) customElements.define('eitri-rail', Rail);
  }
  window.eitriShell = {
    el: el, palette: palette, menu: menu, toast: toast,
    confirm: function (o) { return dialog(o, false).then(function (v) { return v === true; }); },
    ask: function (o) { return dialog(o, true); },
    mac: MAC
  };
})();
