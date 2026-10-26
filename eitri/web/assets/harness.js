// The wire-harness editor: what the viewer shows for a harness project instead of the 3D view.
//
// It draws the diagram the kernel wrote (out/<name>.harness.svg, laid out by eitri.harness.diagram) and makes it
// interactive: click a wire or a connector to edit it in the inspector, drag connectors to place them, add a wire
// between two pins, click a failing check (in the side panel) to light up the wires it is about. Every change is an
// entry of the model file's EDITS list, sent through the same /edit path as CAD direct edits, so the agent sees it
// (list_edits) and carries on from it; positions are LAYOUT hints in the model file, not wiring.

const NS = 'http://www.w3.org/2000/svg';
const AWG = ['4/0', '2/0', '1/0', '2', '4', '6', '8', '10', '12', '14', '16', '18', '20', '22', '24', '26', '28', '30'];
const METRIC = ['0.14mm2', '0.25mm2', '0.5mm2', '0.75mm2', '1mm2', '1.5mm2', '2.5mm2', '4mm2', '6mm2'];
const COLORS = { BK: 'black', BN: 'brown', RD: 'red', OG: 'orange', YE: 'yellow', GN: 'green', BU: 'blue', VT: 'violet', GY: 'grey', WH: 'white', PK: 'pink', TQ: 'turquoise', SR: 'silver', GD: 'gold' };
const HEX = { BK: '#1c1c1c', BN: '#7b4a2a', RD: '#d62828', OG: '#f77f00', YE: '#f4d03f', GN: '#2e9e4f', BU: '#2b6cd4', VT: '#7d3fc4', GY: '#8d99ae', WH: '#f7f7f2', PK: '#f28fb5', TQ: '#1fb5ad', SR: '#c0c4c8', GD: '#c9a227' };

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const el = (tag, attrs = {}, html) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) { if (k === 'class') e.className = v; else if (v !== false && v != null) e.setAttribute(k, v === true ? '' : v); } if (html != null) e.innerHTML = html; return e; };
// a wire's path: a cubic Bezier (4 points), or a straight lead, a cubic and a straight lead (6 points; eitri/diagram.py _bez)
const LEAD_T = 0.12;
const cubic = (p, t) => { const u = 1 - t; return [0, 1].map(i => u * u * u * p[0][i] + 3 * u * u * t * p[1][i] + 3 * u * t * t * p[2][i] + t * t * t * p[3][i]); };
const lerp = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k];
const bez = (p, t) => p.length === 6
  ? (t <= LEAD_T ? lerp(p[0], p[1], t / LEAD_T) : t >= 1 - LEAD_T ? lerp(p[4], p[5], (t - 1 + LEAD_T) / LEAD_T) : cubic(p.slice(1, 5), (t - LEAD_T) / (1 - 2 * LEAD_T)))
  : cubic(p, t);
const f1 = q => `${q[0].toFixed(1)},${q[1].toFixed(1)}`;
const pathD = p => p.length === 6
  ? `M${f1(p[0])} L${f1(p[1])} C${f1(p[2])} ${f1(p[3])} ${f1(p[4])} L${f1(p[5])}`
  : `M${f1(p[0])} C${f1(p[1])} ${f1(p[2])} ${f1(p[3])}`;
const nodeOf = e => e.connector || e.splice;
const ref = e => e.connector ? `${e.connector}.${e.pin}` : e.splice;
const num = v => (v === '' || v == null) ? null : Number(v);

/**
 * host: the element to build the editor in. api: { send(edit) -> Promise<{ok, error, state}> (a direct edit),
 * call(path, payload) -> Promise (any other rebuilding request), hint(text) }.
 * Returns { update(state), highlight(check), clear(), selected() }.
 */
export function createHarnessEditor(host, api) {
  host.innerHTML = '';
  const root = el('div', { id: 'hedit' });
  root.innerHTML = `
    <div class="htools">
      <button data-t="select" class="on" title="Select (V): click a wire or a connector to edit it; drag a connector to move it">Select</button>
      <button data-t="wire" title="Add wire (W): click a pin or splice, then the pin or splice it goes to">Add wire</button>
      <button data-f="fit" title="Fit the diagram to the window (F)">Fit</button>
      <span class="hhint" id="hhint"></span><span style="flex:1"></span>
      <span class="hdl">Download <a data-dl="bom" href="download?fmt=bom" download title="Bill of materials (CSV)">BOM</a> <a data-dl="cutlist" href="download?fmt=cutlist" download title="Cut list (CSV)">cut list</a>
        <a data-dl="wireviz" href="download?fmt=wireviz" download title="WireViz-compatible YAML (interop only)">WireViz</a> <a data-dl="svg" href="download?fmt=svg" download title="The diagram as SVG">SVG</a></span>
    </div>
    <div class="hstage"><div class="hpaper"></div><div class="hempty">Building the first diagram…</div></div>
    <div class="hinspector"></div>`;
  host.appendChild(root);
  const stage = root.querySelector('.hstage'), paper = root.querySelector('.hpaper'), empty = root.querySelector('.hempty');
  const inspector = root.querySelector('.hinspector'), hintEl = root.querySelector('#hhint');

  let state = null, data = null, shownRev = null, mode = 'select', sel = null, hl = null, inspKey = '', fitted = false, svgBusy = false;
  let view = { x: 20, y: 20, k: 1 }, wirePick = null, selectLast = false;
  const setView = () => { paper.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`; };
  const hint = t => { hintEl.textContent = t || ''; };
  const defaultHint = () => hint(mode === 'wire' ? (wirePick ? `from ${wirePick}: click the pin or splice it goes to (Esc cancels)` : 'click the pin or splice the wire starts at (Esc cancels)')
    : 'click a wire or connector to edit it · drag connectors to place them · scroll to zoom · drag the background to pan');

  // ---------------------------------------------------------------- view: fit, zoom, pan
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

  // ---------------------------------------------------------------- selection and highlight
  const groups = (kind, id) => [...paper.querySelectorAll(`g[data-kind="${kind}"]`)].filter(g => g.dataset.id === id);
  function paintSelection() {
    paper.querySelectorAll('.sel').forEach(g => g.classList.remove('sel'));
    if (sel) groups(sel.kind, sel.id).forEach(g => g.classList.add('sel'));
  }
  function paintHighlight() {
    paper.querySelectorAll('.hl').forEach(g => g.classList.remove('hl'));
    const svg = paper.querySelector('svg');
    if (svg) svg.classList.toggle('dimmed', !!(hl && hl.refs.length));
    if (!hl) return;
    for (const g of paper.querySelectorAll('g[data-id]')) if (hl.refs.includes(g.dataset.id)) g.classList.add('hl');
  }
  function select(s) { sel = s; hl = null; paintSelection(); paintHighlight(); renderInspector(true); }

  // ---------------------------------------------------------------- the diagram
  async function loadSvg(rev) {
    if (svgBusy) return; svgBusy = true;
    try {
      const url = api.diagramUrl || 'harness.svg';                  // api.diagramUrl: a read-only share page names a route it may load
      const txt = await fetch(url + (url.includes('?') ? '&' : '?') + 'r=' + rev, { cache: 'no-store' }).then(r => r.ok ? r.text() : Promise.reject(new Error('no diagram yet')));
      paper.innerHTML = txt; shownRev = rev; empty.style.display = 'none';
      const svg = paper.querySelector('svg'); if (svg) { svg.removeAttribute('width'); svg.removeAttribute('height'); svg.style.width = data.scene.width + 'px'; svg.style.height = data.scene.height + 'px'; }
      if (!fitted) { fitted = true; fit(); }
      paintSelection(); paintHighlight();
    } catch (e) { /* the next poll tries again */ }
    svgBusy = false;
  }

  // ---------------------------------------------------------------- pointer: select, drag a node, pan, add a wire
  let drag = null;
  const toDiagram = ev => { const r = paper.getBoundingClientRect(); return [(ev.clientX - r.left) / view.k, (ev.clientY - r.top) / view.k]; };
  stage.addEventListener('pointerdown', ev => {
    if (ev.button !== 0) return;
    const g = ev.target.closest && ev.target.closest('g[data-kind]');
    if (mode === 'wire') {                                              // pick the two ends
      if (!g) return;
      const pin = ev.target.dataset && ev.target.dataset.pin, kind = g.dataset.kind;
      let ep = null;
      if (kind === 'splice') ep = g.dataset.id; else if (kind === 'conn' && pin != null) ep = `${g.dataset.id}.${pin}`;
      if (!ep) { hint('click on a pin row of a connector, or on a splice'); return; }
      if (!wirePick) { wirePick = ep; paintPick(); defaultHint(); return; }
      if (wirePick === ep) { wirePick = null; paintPick(); defaultHint(); return; }
      const a = wirePick; wirePick = null; paintPick();
      const sc = data.scene, wires = (data.wires || []).length;
      selectLast = true;
      api.send({ op: 'add_wire', a, b: ep, gauge: 18, color: 'BK', length_mm: 300 }).then(r => { if (r && r.ok) { setMode('select'); } else selectLast = false; });
      defaultHint(); return;
    }
    if (g && !api.readOnly && (g.dataset.kind === 'conn' || g.dataset.kind === 'splice')) {
      drag = { kind: g.dataset.kind, id: g.dataset.id, g, x0: ev.clientX, y0: ev.clientY, dx: 0, dy: 0, moved: false };
    } else if (g && (g.dataset.kind === 'wire' || api.readOnly)) {                // a read-only view: select, never drag (nothing can be saved)
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
    drag.dx = Math.round(mx / view.k / 5) * 5; drag.dy = Math.round(my / view.k / 5) * 5;       // snap to a 5 px grid
    drag.g.setAttribute('transform', `translate(${drag.dx},${drag.dy})`);
    moveWires(drag.id, drag.dx, drag.dy);
  });
  stage.addEventListener('pointerup', ev => {
    const d = drag; drag = null; if (!d) return;
    try { stage.releasePointerCapture(ev.pointerId); } catch (e) { /* already released */ }
    if (d.pan) { if (!d.moved) select(null); return; }
    if (!d.moved || (!d.dx && !d.dy)) { select({ kind: d.kind, id: d.id }); return; }
    const n = data.scene.nodes[d.id]; if (!n) return;
    const pos = d.kind === 'splice' ? [n.x + n.w / 2 + d.dx, n.y + n.h / 2 + d.dy] : [n.x + d.dx, n.y + d.dy];
    api.call('harness/layout', { positions: { [d.id]: pos } }).then(r => { if (r && !r.ok) { api.hint('the move was not saved: ' + r.error); paintRestore(d); } });
  });
  function paintRestore(d) { d.g.removeAttribute('transform'); moveWires(d.id, 0, 0); }
  function paintPick() {
    paper.querySelectorAll('.pick').forEach(e => e.classList.remove('pick'));
    if (!wirePick) return;
    const [c, p] = wirePick.split('.');
    for (const g of paper.querySelectorAll('g[data-kind]')) if (g.dataset.id === c) { g.classList.add('pick'); if (p != null) for (const r of g.querySelectorAll('rect.pinhit')) if (r.dataset.pin === p) r.classList.add('pick'); }
  }

  // while a node is dragged its wires follow: each end moves with its node, the control points with their ends
  function moveWires(node, dx, dy) {
    const sc = data.scene;
    for (const w of data.wires || []) {
      const A = nodeOf(w.a) === node, B = nodeOf(w.b) === node; if (!A && !B) continue;
      const s = sc.wires[w.id]; if (!s) continue;
      const half = s.pts.length / 2;                             // the first half of the points moves with end A, the rest with B
      const p = s.pts.map((q, i) => [q[0] + ((i < half ? A : B) ? dx : 0), q[1] + ((i < half ? A : B) ? dy : 0)]);
      for (const g of groups('wire', w.id)) {
        for (const path of g.querySelectorAll('path')) path.setAttribute('d', pathD(p));
        const [lx, ly] = bez(p, s.t), [ox, oy] = s.label || bez(s.pts, s.t);
        for (const e of g.querySelectorAll('rect, text')) e.setAttribute('transform', `translate(${(lx - bez(s.pts, s.t)[0]).toFixed(1)},${(ly - bez(s.pts, s.t)[1]).toFixed(1)})`);
      }
    }
  }

  // ---------------------------------------------------------------- keyboard
  window.addEventListener('keydown', ev => {
    if (!state || state.kind !== 'harness' || /^(INPUT|TEXTAREA|SELECT)$/.test((ev.target || {}).tagName || '')) return;
    if (ev.key === 'Escape') { wirePick = null; paintPick(); if (mode === 'wire') setMode('select'); else select(null); }
    else if ((ev.key === 'Delete' || ev.key === 'Backspace') && sel && sel.kind === 'wire') { ev.preventDefault(); api.send({ op: 'remove_wire', wire: sel.id }).then(r => { if (r && r.ok) select(null); }); }
    else if (ev.key === 'v' && !ev.metaKey && !ev.ctrlKey) setMode('select');
    else if (ev.key === 'w' && !ev.metaKey && !ev.ctrlKey) setMode('wire');
    else if (ev.key === 'f' && !ev.metaKey && !ev.ctrlKey) fit();
  });
  function setMode(m) {
    mode = m; wirePick = null; paintPick();
    root.querySelectorAll('.htools [data-t]').forEach(b => b.classList.toggle('on', b.dataset.t === m));
    stage.classList.toggle('wiring', m === 'wire'); defaultHint();
  }
  root.querySelectorAll('.htools [data-t]').forEach(b => b.onclick = () => setMode(b.dataset.t));

  // ---------------------------------------------------------------- the inspector
  const checksFor = id => ((state && state.checks) || []).filter(c => (c.refs || []).includes(id));
  function chips(id) {
    const cs = checksFor(id); if (!cs.length) return '';
    return `<div class="hchecks">${cs.map(c => `<div class="hc ${c.severity || (c.ok ? 'pass' : 'fail')}" title="${esc(c.name)}"><b>${(c.severity || (c.ok ? 'pass' : 'fail')).toUpperCase()}</b> ${esc(c.name.replace(/^(ampacity|voltage drop|connector rating)\s+/, '$1 '))}<br><span>${esc(c.measured)}</span></div>`).join('')}</div>`;
  }
  const field = (label, input) => { const r = el('label', { class: 'hf' }); r.appendChild(el('span', {}, esc(label))); r.appendChild(input); return r; };
  const input = (value, attrs = {}) => { const i = el('input', { type: 'text', ...attrs }); i.value = value ?? ''; return i; };
  const select_ = (value, options, attrs = {}) => { const s = el('select', attrs); for (const [v, t] of options) { const o = el('option', { value: v }, esc(t)); if (String(v) === String(value)) o.selected = true; s.appendChild(o); } return s; };
  const commitOn = (inp, fn) => { inp.addEventListener('change', () => fn(inp.value)); inp.addEventListener('keydown', e => { if (e.key === 'Enter') inp.blur(); }); };
  // an edit goes out through the page's direct-edit path; when it lands the new state is shown at once (not at the next poll)
  const run = edit => api.send(edit).then(r => { if (r && r.ok && r.state && r.state.harness) update(r.state); return r; });
  let typing = false;                                                                // text typed in the inspector and not committed yet
  inspector.addEventListener('input', () => { typing = true; });
  inspector.addEventListener('change', () => { typing = false; });

  function renderInspector(force) {
    if (!data) { inspector.innerHTML = ''; return; }
    const key = JSON.stringify([sel, sel && selData()]) + (sel ? '' : JSON.stringify([data.nets, state.net_stats])) + JSON.stringify((state.checks || []).map(c => c.severity + c.measured));
    if (!force && key === inspKey) return;
    if (!force && typing && inspector.contains(document.activeElement)) return;     // never rebuild under the cursor while someone is typing
    inspKey = key; inspector.innerHTML = '';
    if (!sel) return inspector.appendChild(netsPanel());
    const d = selData();
    if (!d) { sel = null; return inspector.appendChild(netsPanel()); }
    inspector.appendChild(sel.kind === 'wire' ? wirePanel(d) : sel.kind === 'conn' ? connPanel(d) : splicePanel(d));
  }
  const selData = () => !sel ? null : sel.kind === 'wire' ? (data.wires || []).find(w => w.id === sel.id) : sel.kind === 'conn' ? (data.connectors || []).find(c => c.name === sel.id) : (data.splices || []).find(s => s.name === sel.id);
  const head = (title, sub, onClose) => { const h = el('div', { class: 'hh' }, `<div><b>${esc(title)}</b><div class="hsub">${esc(sub)}</div></div>`); const x = el('button', { class: 'small', title: 'Close (Esc)' }, '✕'); x.onclick = () => select(null); h.appendChild(x); return h; };

  function wirePanel(w) {
    const p = el('div', { class: 'hp' });
    p.appendChild(head(`Wire ${w.id}`, `${ref(w.a)} → ${ref(w.b)}${w.cable ? ` · ${w.cable} core ${w.core}` : ''}`));
    p.insertAdjacentHTML('beforeend', chips(w.id));
    const g = input(w.gauge, { list: 'hgauges', size: 8 });
    commitOn(g, v => { v = v.trim(); if (v) run({ op: 'set_wire', wire: w.id, gauge: /^-?\d+$/.test(v) ? parseInt(v) : v }); });
    const [base, stripe] = String(w.color).split('/');
    const cb = select_(base, Object.entries(COLORS).map(([c, n]) => [c, `${c} ${n}`])), cs = select_(stripe || '', [['', 'no stripe'], ...Object.entries(COLORS).map(([c, n]) => [c, `${c} ${n}`])]);
    const setColor = () => run({ op: 'set_wire', wire: w.id, color: cs.value && cs.value !== cb.value ? `${cb.value}/${cs.value}` : cb.value });
    cb.onchange = cs.onchange = setColor;
    const sw = el('span', { class: 'hsw' }); sw.style.background = HEX[base] || '#888'; if (stripe) sw.style.backgroundImage = `repeating-linear-gradient(135deg, ${HEX[stripe]} 0 3px, transparent 3px 6px)`;
    const colorRow = el('div', { class: 'hrow' }); colorRow.append(sw, cb, cs);
    const len = input(w.length_mm, { type: 'number', min: 1, step: 10 }); commitOn(len, v => run({ op: 'set_wire', wire: w.id, length_mm: Number(v) }));
    const net = select_(w.net || '', [['', '(no net)'], ...(data.nets || []).map(n => [n.name, n.name]), ['__new', 'new net…']]);
    net.onchange = () => {
      if (net.value === '__new') { const name = (prompt('Name of the new net (e.g. VBAT, GND, SIG):') || '').trim(); if (!name) { net.value = w.net || ''; return; }
        run({ op: 'set_net', net: name }).then(r => { if (r && r.ok) run({ op: 'set_wire', wire: w.id, net: name }); }); }
      else run({ op: 'set_wire', wire: w.id, net: net.value });
    };
    const n = (data.nets || []).find(x => x.name === w.net);
    const cur = input(w.current_A != null && !(n && n.current_A === w.current_A) ? w.current_A : '', { type: 'number', step: 'any', placeholder: n && n.current_A != null ? `${n.current_A} (the net's)` : 'A' });
    commitOn(cur, v => run({ op: 'set_wire', wire: w.id, current_A: num(v) }));
    const lab = input(w.label, { placeholder: w.id }); commitOn(lab, v => run({ op: 'set_wire', wire: w.id, label: v }));
    p.append(field('gauge', g), field('colour', colorRow), field('length (mm)', len), field('net', net), field('current (A)', cur), field('label', lab));
    const del = el('button', { class: 'small danger' }, 'Remove wire'); del.onclick = () => run({ op: 'remove_wire', wire: w.id }).then(r => { if (r && r.ok) select(null); });
    p.appendChild(del);
    return p;
  }

  function connPanel(c) {
    const p = el('div', { class: 'hp' });
    p.appendChild(head(`Connector ${c.name}`, `${c.type} · ${c.pins.length} pins${c.rating_a != null ? ` · ${c.rating_a} A per pin` : c.known ? ' · wire-limited' : ' · unknown type: no rating'}${c.pitch_mm ? ` · ${c.pitch_mm} mm pitch` : ''}`));
    p.insertAdjacentHTML('beforeend', chips(c.name));
    const name = input(c.name); commitOn(name, v => { v = v.trim(); if (v && v !== c.name) run({ op: 'set_connector', connector: c.name, name: v }).then(r => { if (r && r.ok) sel = { kind: 'conn', id: v }; }); });
    const type = input(c.type, { list: 'htypes' }); commitOn(type, v => { if (v.trim() && v.trim() !== c.type) run({ op: 'set_connector', connector: c.name, type: v.trim() }); });
    const pn = input(c.part_number); commitOn(pn, v => run({ op: 'set_connector', connector: c.name, part_number: v }));
    const gen = select_(c.gender || '', [['', '—'], ['male', 'male'], ['female', 'female']]); gen.onchange = () => run({ op: 'set_connector', connector: c.name, gender: gen.value });
    const rate = input(c.rating_a ?? '', { type: 'number', step: 'any', placeholder: 'A per pin' }); commitOn(rate, v => run({ op: 'set_connector', connector: c.name, rating_a: num(v) }));
    p.append(field('name', name), field('type', type), field('part no.', pn), field('gender', gen), field('rating (A/pin)', rate));
    const used = new Set(); for (const w of data.wires || []) for (const e of [w.a, w.b]) if (e.connector === c.name) used.add(e.pin);
    const tbl = el('div', { class: 'hpins' }, '<div class="hph"><span>pin</span><span>function</span><span title="not connected">NC</span><span></span></div>');
    for (const pin of c.pins) {
      const r = el('div', { class: 'hpr' });
      const pi = input(pin, { size: 4 }), li = input((c.labels || {})[pin] || '', { size: 8, placeholder: '—' });
      const rename = () => { const to = pi.value.trim() || pin; if (to !== pin || li.value !== ((c.labels || {})[pin] || '')) run({ op: 'rename_pin', connector: c.name, pin, to, label: li.value.trim() }); };
      pi.addEventListener('change', rename); li.addEventListener('change', rename);
      for (const i of [pi, li]) i.addEventListener('keydown', e => { if (e.key === 'Enter') i.blur(); });
      const nc = el('input', { type: 'checkbox', title: used.has(pin) ? 'this pin has a wire' : 'declare this pin not connected' }); nc.checked = (c.nc || []).includes(pin); nc.disabled = used.has(pin);
      nc.onchange = () => run({ op: 'nc', pin: `${c.name}.${pin}`, nc: nc.checked });
      const rm = el('button', { class: 'small', title: used.has(pin) ? 'remove or move its wire first' : 'remove this pin' }, '✕'); rm.disabled = used.has(pin) || c.pins.length < 2;
      rm.onclick = () => run({ op: 'remove_pin', connector: c.name, pin });
      r.append(pi, li, nc, rm); tbl.appendChild(r);
    }
    p.appendChild(tbl);
    const add = el('button', { class: 'small' }, '+ pin'); add.onclick = () => {
      let k = c.pins.length + 1; while (c.pins.includes(String(k))) k++;
      run({ op: 'add_pin', connector: c.name, pin: String(k) });
    };
    p.appendChild(add);
    return p;
  }

  function splicePanel(s) {
    const p = el('div', { class: 'hp' });
    const ws = (data.wires || []).filter(w => nodeOf(w.a) === s.name || nodeOf(w.b) === s.name);
    p.appendChild(head(`Splice ${s.name}`, `${s.kind} splice · ${ws.length} wire(s): ${ws.map(w => w.id).join(', ')}`));
    p.insertAdjacentHTML('beforeend', chips(s.name));
    p.appendChild(el('div', { class: 'hsub' }, 'Drag it to place it. Click one of its wires to edit that wire.'));
    return p;
  }

  function netsPanel() {
    const p = el('div', { class: 'hp' });
    p.appendChild(el('div', { class: 'hh' }, '<div><b>Nets</b><div class="hsub">what the checks grade the wires against</div></div>'));
    const stats = Object.fromEntries((state.net_stats || []).map(n => [n.name, n]));
    const tbl = el('div', { class: 'hnets' }, '<div class="hnh"><span>net</span><span>V</span><span>A</span><span>drop ≤ %</span><span>drop now</span></div>');
    for (const n of data.nets || []) {
      const st = stats[n.name] || {}, r = el('div', { class: 'hnr' });
      const v = input(n.voltage_V ?? '', { type: 'number', step: 'any', size: 4 }), a = input(n.current_A ?? '', { type: 'number', step: 'any', size: 4 }), m = input(n.max_drop_pct ?? 3, { type: 'number', step: 'any', size: 4 });
      commitOn(v, x => run({ op: 'set_net', net: n.name, voltage_V: num(x) })); commitOn(a, x => run({ op: 'set_net', net: n.name, current_A: num(x) })); commitOn(m, x => run({ op: 'set_net', net: n.name, max_drop_pct: num(x) ?? 3 }));
      const drop = el('span', { class: 'hdrop ' + (st.status || '') }, st.drop_pct != null ? `${st.drop_pct.toFixed(2)}%` : '—');
      r.append(el('b', {}, esc(n.name)), v, a, m, drop); tbl.appendChild(r);
    }
    if (!(data.nets || []).length) tbl.appendChild(el('div', { class: 'hsub' }, 'No nets yet: wires without a net are not checked for current or voltage drop. Select a wire to give it one.'));
    p.appendChild(tbl);
    const tot = (data.wires || []).reduce((s, w) => s + w.length_mm, 0);
    p.appendChild(el('div', { class: 'hsub' }, `${(data.connectors || []).length} connectors · ${(data.splices || []).length} splices · ${(data.wires || []).length} wires · ${(tot / 1000).toFixed(2)} m of wire`));
    return p;
  }

  // datalists shared by the forms
  const dl = el('div', { style: 'display:none' }, `<datalist id="hgauges">${[...AWG, ...METRIC].map(g => `<option value="${g}">`).join('')}</datalist><datalist id="htypes"></datalist>`);
  root.appendChild(dl);

  // ---------------------------------------------------------------- updates from the page's poll
  function update(s) {
    state = s; data = s.harness;
    root.querySelectorAll('[data-dl]').forEach(a => { a.style.display = s.ok_revision ? '' : 'none'; });
    if (!data || !s.ok_revision) { empty.style.display = 'block'; if (s.last_error) empty.textContent = 'The last build failed — ' + s.last_error.split('\n')[0]; return; }
    const dt = dl.querySelector('#htypes'); if (dt && !dt.children.length) dt.innerHTML = (data.catalog || []).map(t => `<option value="${esc(t)}">`).join('');
    if (shownRev !== s.ok_revision) loadSvg(s.ok_revision).then(() => { if (selectLast && data.wires.length) { selectLast = false; select({ kind: 'wire', id: data.wires[data.wires.length - 1].id }); } });
    if (sel && !selData()) sel = null;
    renderInspector(false);
    if (!hintEl.textContent) defaultHint();
  }

  return {
    update,
    selected: () => sel,
    /** The selection as the ids the chat sends with a message ("wire:W3", "conn:J2", "splice:PDB"). */
    selectionIds: () => sel ? [`${sel.kind}:${sel.id}`] : [],
    /** Light up the wires and connectors a check is about (a second click on the same check clears it). */
    highlight(check) {
      if (hl && hl.name === check.name) hl = null; else hl = { name: check.name, refs: check.refs || [] };
      if (hl) sel = null, paintSelection(), renderInspector(true);
      paintHighlight(); return !!hl;
    },
    clear() { hl = null; paintHighlight(); },
    /** Select what a direct edit is about (a wire or a connector), from the side panel's edit list. */
    selectTarget(e) {
      if (e.wire) select({ kind: 'wire', id: e.wire });
      else if (e.connector || e.name) select({ kind: 'conn', id: e.connector || e.name });
      else if (e.pin) select({ kind: 'conn', id: String(e.pin).split('.')[0] });
    },
    select,
    highlighted: () => hl && hl.name,
    fit,
  };
}
