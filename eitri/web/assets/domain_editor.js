// A diagram editor shared by the non-CAD domains (mechanism, pneumatics, firmware): the stage with pan and zoom, the SVG the
// kernel wrote (every element carries data-kind / data-id), selection and check highlighting, drag of nodes into LAYOUT hints,
// and the floating inspector whose contents each domain supplies. Edits go through the page's direct-edit path (api.send), so
// the agent sees them (list_edits) and carries on from them. The harness editor (harness.js) predates this and keeps its own copy.

export const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
export const el = (tag, attrs = {}, html) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) { if (k === 'class') e.className = v; else if (v !== false && v != null) e.setAttribute(k, v === true ? '' : v); } if (html != null) e.innerHTML = html; return e; };
export const num = v => (v === '' || v == null) ? null : Number(v);

/**
 * host: the element to build in. api: { send(edit), call(path, payload), hint(text) }.
 * spec: { kind, dataKey (the state field with the domain data), svgUrl ('domain.svg'), emptyText, hint, draggable: kind set,
 *         tools: [{ t, label, title }], downloads: [{ fmt, label, title }], onTool(t), onPick(kind, id, pin, ev) -> bool (wiring modes),
 *         inspector(sel, data, state, h) -> element | null, summary(data, state, h) -> element | null, selectEdit(edit) -> sel | null }
 */
export function createDiagramEditor(host, api, spec) {
  host.innerHTML = '';
  const root = el('div', { id: 'hedit', class: 'dedit ' + spec.kind });
  root.innerHTML = `
    <div class="htools">
      ${(spec.tools || [{ t: 'select', label: 'Select', title: 'Select: click a thing to edit it' }]).map((t, i) => `<button data-t="${t.t}" class="${i === 0 ? 'on' : ''}" title="${esc(t.title)}">${esc(t.label)}</button>`).join('')}
      <button data-f="fit" title="Fit the diagram to the window (F)">Fit</button>
      <span class="hhint" id="hhint"></span><span style="flex:1"></span>
      <span class="hdl">Download ${(spec.downloads || []).map(d => `<a data-dl="${d.fmt}" href="download?fmt=${d.fmt}" download title="${esc(d.title)}">${esc(d.label)}</a>`).join(' ')}</span>
    </div>
    <div class="hstage"><div class="hpaper"></div><div class="hempty">${esc(spec.emptyText || 'Building the first diagram…')}</div></div>
    <div class="hinspector"></div>`;
  host.appendChild(root);
  const stage = root.querySelector('.hstage'), paper = root.querySelector('.hpaper'), empty = root.querySelector('.hempty');
  const inspector = root.querySelector('.hinspector'), hintEl = root.querySelector('#hhint');
  let state = null, data = null, shownRev = null, mode = (spec.tools || [{ t: 'select' }])[0].t, sel = null, hl = null, inspKey = '', fitted = false, svgBusy = false;
  let view = { x: 20, y: 20, k: 1 }, typing = false;
  const setView = () => { paper.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`; };
  const hint = t => { hintEl.textContent = t || ''; };
  const defaultHint = () => hint((spec.hint && spec.hint(mode)) || 'click a thing to edit it · scroll to zoom · drag the background to pan');

  let autoFit = true;                                                 // refit when the stage changes size, until the view is moved by hand
  function fit() {
    const sc = data && data.scene; if (!sc) return;
    const w = stage.clientWidth, h = stage.clientHeight; if (!w || !h) return;
    const top = 64;                                                   // the tool strip floats over the top edge (the inspector is docked beside the stage)
    const k = Math.min((w - 48) / sc.width, (h - top - 28) / sc.height, 1.6);
    view = { k: Math.max(0.2, k), x: Math.max(24, (w - sc.width * k) / 2), y: Math.max(top, top + (h - top - 28 - sc.height * k) / 2) };
    setView();
  }
  if (window.ResizeObserver) new ResizeObserver(() => { if (autoFit && fitted) fit(); }).observe(stage);
  stage.addEventListener('wheel', ev => {
    ev.preventDefault(); autoFit = false;
    const r = stage.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top;
    const k2 = Math.min(4, Math.max(0.15, view.k * Math.exp(-ev.deltaY * (ev.ctrlKey ? 0.01 : 0.0015))));
    view.x = mx - (mx - view.x) * (k2 / view.k); view.y = my - (my - view.y) * (k2 / view.k); view.k = k2; setView();
  }, { passive: false });
  root.querySelector('[data-f="fit"]').onclick = () => { autoFit = true; fit(); };

  const groups = (kind, id) => [...paper.querySelectorAll(`g[data-kind="${kind}"]`)].filter(g => g.dataset.id === id);
  function paintSelection() { paper.querySelectorAll('.sel').forEach(g => g.classList.remove('sel')); if (sel) groups(sel.kind, sel.id).forEach(g => g.classList.add('sel')); }
  function paintHighlight() {
    paper.querySelectorAll('.hl').forEach(g => g.classList.remove('hl'));
    const svg = paper.querySelector('svg'); if (svg) svg.classList.toggle('dimmed', !!(hl && hl.refs.length));
    if (!hl) return;
    for (const g of paper.querySelectorAll('g[data-id]')) if (hl.refs.includes(g.dataset.id)) g.classList.add('hl');
  }
  function select(s) { sel = s; hl = null; paintSelection(); paintHighlight(); renderInspector(true); }

  async function loadSvg(rev) {
    if (svgBusy) return; svgBusy = true;
    try {
      const url = api.diagramUrl || spec.svgUrl || 'domain.svg';    // api.diagramUrl: a read-only share page names a route it may load
      const txt = await fetch(url + (url.includes('?') ? '&' : '?') + 'r=' + rev, { cache: 'no-store' }).then(r => r.ok ? r.text() : Promise.reject(new Error('no diagram yet')));
      paper.innerHTML = txt; shownRev = rev; empty.style.display = 'none';
      const svg = paper.querySelector('svg'); if (svg) { svg.removeAttribute('width'); svg.removeAttribute('height'); svg.style.width = data.scene.width + 'px'; svg.style.height = data.scene.height + 'px'; }
      if (!fitted) { fitted = true; fit(); }
      paintSelection(); paintHighlight();
    } catch (e) { /* the next poll tries again */ }
    svgBusy = false;
  }

  let drag = null;
  stage.addEventListener('pointerdown', ev => {
    if (ev.button !== 0) return;
    const g = ev.target.closest && ev.target.closest('g[data-kind]');
    if (mode !== (spec.tools || [{ t: 'select' }])[0].t) {                 // a domain tool (wiring, placing): the domain decides
      if (spec.onPick && spec.onPick(mode, g ? g.dataset.kind : null, g ? g.dataset.id : null, ev.target.dataset ? ev.target.dataset.pin : null, ev)) return;
    }
    if (g && !api.readOnly && spec.draggable && spec.draggable.has(g.dataset.kind)) {      // a read-only view selects, never drags
      drag = { kind: g.dataset.kind, id: g.dataset.id, g, x0: ev.clientX, y0: ev.clientY, dx: 0, dy: 0, moved: false };
    } else if (g) {
      select({ kind: g.dataset.kind, id: g.dataset.id }); return;
    } else {
      drag = { pan: true, x0: ev.clientX, y0: ev.clientY, vx: view.x, vy: view.y, moved: false }; autoFit = false;
    }
    stage.setPointerCapture(ev.pointerId);
  });
  stage.addEventListener('pointermove', ev => {
    if (!drag) return;
    const mx = ev.clientX - drag.x0, my = ev.clientY - drag.y0;
    if (!drag.moved && Math.hypot(mx, my) < 4) return;
    drag.moved = true;
    if (drag.pan) { view.x = drag.vx + mx; view.y = drag.vy + my; setView(); return; }
    drag.dx = Math.round(mx / view.k / 5) * 5; drag.dy = Math.round(my / view.k / 5) * 5;
    drag.g.setAttribute('transform', `translate(${drag.dx},${drag.dy})`);
  });
  stage.addEventListener('pointerup', ev => {
    const d = drag; drag = null; if (!d) return;
    try { stage.releasePointerCapture(ev.pointerId); } catch (e) { /* released */ }
    if (d.pan) { if (!d.moved) select(null); return; }
    if (!d.moved || (!d.dx && !d.dy)) { select({ kind: d.kind, id: d.id }); return; }
    const n = data.scene.nodes && data.scene.nodes[d.id]; if (!n) { d.g.removeAttribute('transform'); return; }
    api.call('domain/layout', { positions: { [d.id]: [n.x + d.dx, n.y + d.dy] } }).then(r => { if (r && !r.ok) { api.hint('the move was not saved: ' + r.error); d.g.removeAttribute('transform'); } });
  });
  window.addEventListener('keydown', ev => {
    if (!state || !state.kind || state.kind === 'cad' || /^(INPUT|TEXTAREA|SELECT)$/.test((ev.target || {}).tagName || '')) return;
    if (ev.key === 'Escape') { if (mode !== (spec.tools || [{ t: 'select' }])[0].t) setMode((spec.tools || [{ t: 'select' }])[0].t); else select(null); }
    else if (ev.key === 'f' && !ev.metaKey && !ev.ctrlKey) fit();
    else if (spec.onKey) spec.onKey(ev, sel, h);
  });
  function setMode(m) { mode = m; root.querySelectorAll('.htools [data-t]').forEach(b => b.classList.toggle('on', b.dataset.t === m)); stage.classList.toggle('wiring', m !== (spec.tools || [{ t: 'select' }])[0].t); if (spec.onTool) spec.onTool(m); defaultHint(); }
  root.querySelectorAll('.htools [data-t]').forEach(b => b.onclick = () => setMode(b.dataset.t));

  // ---- inspector helpers the domains build their panels with
  const field = (label, input) => { const r = el('label', { class: 'hf' }); r.appendChild(el('span', {}, esc(label))); r.appendChild(input); return r; };
  const input = (value, attrs = {}) => { const i = el('input', { type: 'text', ...attrs }); i.value = value ?? ''; return i; };
  const select_ = (value, options, attrs = {}) => { const s = el('select', attrs); for (const [v, t] of options) { const o = el('option', { value: v }, esc(t)); if (String(v) === String(value)) o.selected = true; s.appendChild(o); } return s; };
  const commitOn = (inp, fn) => { inp.addEventListener('change', () => fn(inp.value)); inp.addEventListener('keydown', e => { if (e.key === 'Enter') inp.blur(); }); };
  const run = edit => api.send(edit).then(r => { if (r && r.ok && r.state && r.state[spec.dataKey]) update(r.state); return r; });
  const checksFor = id => ((state && state.checks) || []).filter(c => (c.refs || []).includes(id));
  const chips = id => { const cs = checksFor(id); if (!cs.length) return ''; return `<div class="hchecks">${cs.map(c => `<div class="hc ${c.severity || (c.ok ? 'pass' : 'fail')}" title="${esc(c.name)}"><b>${(c.severity || (c.ok ? 'pass' : 'fail')).toUpperCase()}</b> ${esc(c.name)}<br><span>${esc(c.measured)}</span></div>`).join('')}</div>`; };
  const head = (title, sub) => { const hd = el('div', { class: 'hh' }, `<div><b>${esc(title)}</b><div class="hsub">${esc(sub)}</div></div>`); const x = el('button', { class: 'small', title: 'Close (Esc)' }, '✕'); x.onclick = () => select(null); hd.appendChild(x); return hd; };
  const h = { field, input, select: select_, commitOn, run, chips, head, el, esc, num, select: select_, pick: select, setMode, hint: api.hint, numField: (label, value, attrs, fn) => { const i = input(value ?? '', { type: 'number', step: 'any', ...attrs }); commitOn(i, v => fn(num(v))); return field(label, i); } };
  inspector.addEventListener('input', () => { typing = true; });
  inspector.addEventListener('change', () => { typing = false; });

  function renderInspector(force) {
    if (!data) { inspector.innerHTML = ''; return; }
    const key = JSON.stringify([sel, state.ok_revision, (state.checks || []).map(c => c.severity + c.measured)]);
    if (!force && key === inspKey) return;
    if (!force && typing && inspector.contains(document.activeElement)) return;
    inspKey = key; inspector.innerHTML = '';
    let panel = null;
    if (sel) panel = spec.inspector(sel, data, state, h);
    if (!panel) { sel = null; panel = spec.summary ? spec.summary(data, state, h) : null; }
    if (panel) inspector.appendChild(panel);
  }

  function update(s) {
    state = s; data = s[spec.dataKey] || s.domain;
    root.querySelectorAll('[data-dl]').forEach(a => { a.style.display = s.ok_revision ? '' : 'none'; });
    if (!data || !s.ok_revision) { empty.style.display = 'block'; if (s.last_error) empty.textContent = 'The last build failed — ' + s.last_error.split('\n')[0]; return; }
    if (spec.onData) spec.onData(data, s);
    if (shownRev !== s.ok_revision) loadSvg(s.ok_revision);
    renderInspector(false);
    if (!hintEl.textContent) defaultHint();
  }

  return {
    update, fit, select, mode: () => mode, setMode,
    selected: () => sel,
    selectionIds: () => sel ? [`${sel.kind}:${sel.id}`] : [],
    highlight(check) { if (hl && hl.name === check.name) hl = null; else hl = { name: check.name, refs: check.refs || [] }; if (hl) { sel = null; paintSelection(); renderInspector(true); } paintHighlight(); return !!hl; },
    clear() { hl = null; paintHighlight(); },
    highlighted: () => hl && hl.name,
    selectTarget(e) { const s = spec.selectEdit ? spec.selectEdit(e) : null; if (s) select(s); },
    refresh: () => renderInspector(true),
    state: () => state, data: () => data,
  };
}
