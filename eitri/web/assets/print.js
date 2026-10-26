// Export for 3D printing: the editor's print dialog.
//
//   import('./assets/print.js').then(m => m.openPrintDialog({ title: 'V8 engine kit' }))
//
// openPrintDialog(opts) -> the dialog's root element (one dialog at a time; a second call refocuses it)
//   opts.title     the project title for the headline ("<title> -> 43 printable parts, 2 plates")
//   opts.onClose   called after the dialog closes
// closePrintDialog() closes it.
//
// It talks to the project's routes, all relative to the editor page (/p/<id>/):
//   POST print/plan    settings -> parts (orientation, size, plate, checks, thumb) + plate pictures
//   POST print/export  settings + formats -> files, problems by part, a zip
//   GET  print/file    thumbnails, plate pictures, exported files
// Loads assets/print.css itself; builds on the design tokens and assets/app.css (.btn, .input, .badge, ...).

const ICONS = 'assets/icons.svg';
const STORE = 'eitri.print.settings';

const PRESETS = [
  { id: 'generic', label: 'Generic 220 x 220', bed: [220, 220], height: 250 },
  { id: 'mk4', label: 'Prusa MK4 · 250 x 210', bed: [250, 210], height: 220 },
  { id: 'x1', label: 'Bambu X1 / P1 · 256 x 256', bed: [256, 256], height: 256 },
  { id: 'a1mini', label: 'Bambu A1 mini · 180 x 180', bed: [180, 180], height: 180 },
  { id: 'voron', label: 'Voron 2.4 · 350 x 350', bed: [350, 350], height: 340 },
];

const PLAN_STAGES = [
  { label: 'Building the model', at: 0 },
  { label: 'Finding printable parts', at: 1.6 },
  { label: 'Choosing orientations', at: 3.6 },
  { label: 'Laying out plates', at: 7.5 },
  { label: 'Rendering previews', at: 10 },
];
const EXPORT_STAGES = [
  { label: 'Meshing every part', at: 0 },
  { label: 'Checking each mesh is watertight', at: 2.5 },
  { label: 'Writing STL and 3MF files', at: 6 },
  { label: 'Packing the zip', at: 10 },
];

const RISK = {
  none: { cls: 'ok', text: 'no supports', bars: 0 },
  low: { cls: 'low', text: 'overhang low', bars: 1 },
  medium: { cls: 'warn', text: 'overhang medium', bars: 2 },
  high: { cls: 'bad', text: 'overhang high', bars: 3 },
};

let S = null;           // the open dialog's state

// ------------------------------------------------------------------ helpers

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = (name, cls = '') => `<svg class="i ${cls}" aria-hidden="true"><use href="${ICONS}#i-${name}"/></svg>`;
const num = (v, d = 1) => {
  if (v == null || !isFinite(v)) return '-';
  const r = Math.round(v * 10 ** d) / 10 ** d;
  return String(r);
};
const dims = (s) => (s && s.length === 3 ? `${num(s[0])} × ${num(s[1])} × ${num(s[2])}` : '');
const plural = (n, w, ws = w + 's') => `${n} ${n === 1 ? w : ws}`;
const $ = (sel, root = S && S.root) => root && root.querySelector(sel);
const $$ = (sel, root = S && S.root) => (root ? [...root.querySelectorAll(sel)] : []);

function ensureCss() {
  if (document.querySelector('link[data-print-css]')) return;
  const l = document.createElement('link');
  l.rel = 'stylesheet';
  l.href = 'assets/print.css';
  l.dataset.printCss = '1';
  document.head.appendChild(l);
}

function loadSaved() {
  try { return JSON.parse(localStorage.getItem(STORE) || 'null') || {}; } catch { return {}; }
}
function save() {
  try {
    const { bedW, bedD, height, minWall, spacing, includeBought } = S.settings;
    localStorage.setItem(STORE, JSON.stringify({ bedW, bedD, height, minWall, spacing, includeBought, formats: S.formats }));
  } catch { /* storage is a convenience */ }
}

async function post(path, body) {
  let res;
  try {
    res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) {
    return { ok: false, error: 'Could not reach the eitri server. Is it still running?' };
  }
  let data = null;
  try { data = await res.json(); } catch { /* not json */ }
  if (!data) return { ok: false, error: `The server answered ${res.status} ${res.statusText || ''}`.trim() };
  if (data.ok === false || !res.ok) return { ok: false, error: data.error || `The server answered ${res.status}` };
  return data;
}

function boughtWhat(p) {
  const m = /looks like an? ([a-z]+)/i.exec(p.bought_reason || '');
  if (m) return m[1];
  const mat = /^(\w+) part/i.exec(p.bought_reason || '');
  return mat ? mat[1].toLowerCase() : 'off the shelf';
}

function issueLevel(p) {             // 3 problem, 2 high overhang / thin, 1 medium overhang, 0 fine
  if (p.problem) return 3;
  if (p.thin || p.overhang_risk === 'high') return 2;
  if (p.overhang_risk === 'medium') return 1;
  return 0;
}

function printable(p) { return p.included && !p.problem; }

// ------------------------------------------------------------------ open / close

export function openPrintDialog(opts = {}) {
  if (S) { S.dialog.focus(); return S.root; }
  ensureCss();
  const saved = loadSaved();
  S = {
    opts,
    title: opts.title || '',
    settings: {
      bedW: saved.bedW ?? 220, bedD: saved.bedD ?? 220, height: saved.height ?? 250,
      minWall: saved.minWall ?? 0.8, spacing: saved.spacing ?? 5, includeBought: !!saved.includeBought,
    },
    formats: { stl: true, '3mf': true, ...(saved.formats || {}) },
    include: new Set(), exclude: new Set(),
    plan: null, error: null, seq: 0, busy: null, timer: null,
    plate: 1, hover: null, pinned: null,
    filter: 'all', sort: 'issues',
    exporting: false, result: null, view: 'plan',
    restoreFocus: document.activeElement,
    imgSize: {},
  };
  S.root = document.createElement('div');
  S.root.className = 'scrim pd-scrim';
  S.root.innerHTML = shell();
  document.body.appendChild(S.root);
  S.dialog = $('.pd');
  bind();
  renderSettings();
  renderAll();
  S.ro = new ResizeObserver(() => layoutPlate());
  S.ro.observe($('.pd-stage-wrap'));
  setTimeout(() => S?.dialog.focus({ preventScroll: true }), 30);
  plan(true);
  return S.root;
}

export function closePrintDialog() {
  if (!S) return;
  const st = S;
  clearTimeout(st.timer);
  st.busy?.stop();
  st.ro?.disconnect();
  document.removeEventListener('keydown', st.onKey, true);
  st.root.classList.add('pd-leaving');
  setTimeout(() => st.root.remove(), 160);
  S = null;
  try { st.restoreFocus?.focus?.(); } catch { /* gone */ }
  st.opts.onClose?.();
}

// ------------------------------------------------------------------ the static shell

function shell() {
  const st = S.settings;
  const presetOpts = PRESETS.map((p) => `<option value="${p.id}">${esc(p.label)}</option>`).join('');
  return `
<div class="dialog pd" role="dialog" aria-modal="true" aria-labelledby="pd-title" tabindex="-1">
  <header class="pd-head">
    <div class="pd-mark">${icon('printer', 'lg')}</div>
    <div class="pd-titles">
      <div class="eyebrow" id="pd-title">Export for 3D printing</div>
      <div class="pd-headline" aria-live="polite"></div>
    </div>
    <div class="pd-status" aria-live="polite"></div>
    <button class="btn ghost icon pd-close" title="Close (Esc)" aria-label="Close">${icon('x')}</button>
  </header>

  <section class="pd-settings" aria-label="Printer settings">
    <label class="pd-field pd-f-preset">
      <span class="pd-label">Printer</span>
      <span class="pd-select-wrap">
        <select class="select pd-preset">${presetOpts}<option value="custom">Custom bed</option></select>
        ${icon('chevron-down', 'sm pd-select-caret')}
      </span>
    </label>
    <div class="pd-field pd-f-bed">
      <span class="pd-label">Bed W × D</span>
      <span class="pd-pair">
        ${numInput('bedW', st.bedW, 'Bed width', 20, 2000, 1)}
        <span class="pd-times">×</span>
        ${numInput('bedD', st.bedD, 'Bed depth', 20, 2000, 1)}
      </span>
    </div>
    <label class="pd-field pd-f-num">
      <span class="pd-label">Build height</span>
      ${numInput('height', st.height, 'Build height', 1, 2000, 1)}
    </label>
    <label class="pd-field pd-f-num pd-f-wall">
      <span class="pd-label">Min wall <span class="pd-hint" title="Walls thinner than this get flagged: 2 perimeters of a 0.4 mm nozzle is 0.8 mm">${icon('info', 'sm')}</span></span>
      ${numInput('minWall', st.minWall, 'Minimum wall', 0, 20, 0.05)}
      <span class="pd-sub">2 perimeters of a 0.4 mm nozzle</span>
    </label>
    <label class="pd-field pd-f-num">
      <span class="pd-label">Spacing</span>
      ${numInput('spacing', st.spacing, 'Spacing between parts', 0, 100, 0.5)}
    </label>
    <label class="pd-field pd-f-switch">
      <span class="pd-label">Bought parts</span>
      <span class="pd-switch-row">
        <input type="checkbox" class="pd-switch" data-k="includeBought" ${st.includeBought ? 'checked' : ''}>
        <span class="pd-switch-text">Include screws, nuts, bearings</span>
      </span>
    </label>
  </section>

  <div class="pd-body">
    <div class="pd-main">
      <section class="pd-list-col" aria-label="Parts">
        <div class="pd-list-bar">
          <div class="pd-seg" role="tablist" aria-label="Filter parts">
            <button class="pd-seg-btn" data-filter="all" role="tab">All <span class="pd-count" data-c="all"></span></button>
            <button class="pd-seg-btn" data-filter="issues" role="tab">Issues <span class="pd-count" data-c="issues"></span></button>
            <button class="pd-seg-btn" data-filter="plate" role="tab">This plate <span class="pd-count" data-c="plate"></span></button>
          </div>
          <span class="pd-select-wrap pd-sort-wrap" title="Sort">
            ${icon('sliders-horizontal', 'sm pd-sort-icon')}
            <select class="select pd-sort" aria-label="Sort parts">
              <option value="issues">Issues first</option>
              <option value="plate">By plate</option>
              <option value="size">Largest first</option>
              <option value="name">By name</option>
            </select>
            ${icon('chevron-down', 'sm pd-select-caret')}
          </span>
        </div>
        <div class="pd-list" tabindex="-1"></div>
      </section>
      <section class="pd-plate-col" aria-label="Plate preview">
        <div class="pd-tabs" role="tablist" aria-label="Plates"></div>
        <div class="pd-stage-wrap">
          <div class="pd-stage"></div>
        </div>
        <div class="pd-plate-foot"></div>
      </section>
    </div>
    <div class="pd-done" hidden></div>
    <div class="pd-busy" hidden></div>
  </div>

  <footer class="pd-foot"></footer>
</div>`;
}

function numInput(k, v, label, min, max, step) {
  return `<span class="pd-num"><input class="input mono" type="number" inputmode="decimal" data-k="${k}" value="${esc(v)}"
    min="${min}" max="${max}" step="${step}" aria-label="${esc(label)}"><span class="pd-unit">mm</span></span>`;
}

// ------------------------------------------------------------------ events

function bind() {
  const root = S.root;
  S.onKey = (e) => {
    if (!S) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closePrintDialog(); return; }
    if (e.key === 'Tab') trapFocus(e);
    if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && e.target.closest?.('.pd-tabs')) {
      const n = S.plan?.plates || 0;
      if (n) { setPlate(((S.plate - 1 + (e.key === 'ArrowRight' ? 1 : -1) + n) % n) + 1); $(`.pd-tab[data-plate="${S.plate}"]`)?.focus(); }
    }
  };
  document.addEventListener('keydown', S.onKey, true);

  root.addEventListener('mousedown', (e) => { S && (S.downOnScrim = e.target === root); });
  root.addEventListener('click', (e) => {
    if (!S) return;
    if (e.target === root && S.downOnScrim && !S.exporting) { closePrintDialog(); return; }
    const t = e.target.closest('button, [data-act]');
    if (!t || !root.contains(t)) return;
    if (t.classList.contains('pd-close')) return closePrintDialog();
    if (t.dataset.filter) { S.filter = t.dataset.filter; renderList(); $('.pd-list').scrollTop = 0; drawOverlay(); return; }
    if (t.dataset.plate) { setPlate(+t.dataset.plate); return; }
    const act = t.dataset.act;
    if (act === 'retry') plan(true);
    else if (act === 'export') doExport();
    else if (act === 'back') showPlan();
    else if (act === 'close') closePrintDialog();
    else if (act === 'copy') copyFolder(t);
    else if (act === 'all-in') { S.exclude.clear(); schedule(150); }
    else if (act === 'toggle-files') t.closest('.pd-files')?.classList.toggle('open');
  });

  root.addEventListener('change', (e) => {
    const t = e.target;
    if (t.classList.contains('pd-preset')) {
      const p = PRESETS.find((x) => x.id === t.value);
      if (p) {
        Object.assign(S.settings, { bedW: p.bed[0], bedD: p.bed[1], height: p.height });
        $('[data-k="bedW"]').value = p.bed[0]; $('[data-k="bedD"]').value = p.bed[1]; $('[data-k="height"]').value = p.height;
        validate(); schedule(80);
      }
    } else if (t.dataset.k === 'includeBought') {
      S.settings.includeBought = t.checked; schedule(120);
    } else if (t.classList.contains('pd-sort')) {
      S.sort = t.value; renderList();
    } else if (t.dataset.part != null) {
      togglePart(t.dataset.part, t.checked);
    } else if (t.dataset.fmt) {
      S.formats[t.dataset.fmt] = t.checked; save(); renderFooter();
    }
  });

  root.addEventListener('input', (e) => {
    const k = e.target.dataset?.k;
    if (!k || e.target.type !== 'number') return;
    const v = parseFloat(e.target.value);
    S.settings[k] = isFinite(v) ? v : NaN;
    syncPreset();
    if (validate()) schedule(700); else { clearTimeout(S.timer); renderStatus(); }
  });

  // hover: list -> plate
  const list = $('.pd-list');
  list.addEventListener('mouseover', (e) => {
    const row = e.target.closest('.pd-row');
    if (row && row.dataset.name !== S.hover) hoverPart(row.dataset.name, true);
  });
  list.addEventListener('mouseleave', () => hoverPart(null));
  list.addEventListener('click', (e) => {
    const row = e.target.closest('.pd-row');
    if (!row || e.target.closest('label, input')) return;
    S.pinned = S.pinned === row.dataset.name ? null : row.dataset.name;
    const p = partByName(S.pinned);
    if (p?.plate && printable(p)) setPlate(p.plate);
    markRows(); drawOverlay();
  });

  // hover: plate -> list
  const stage = $('.pd-stage');
  stage.addEventListener('mousemove', (e) => {
    const name = hitTest(e);
    if (name !== S.hover) hoverPart(name, false);
    stage.style.cursor = name ? 'pointer' : '';
  });
  stage.addEventListener('mouseleave', () => hoverPart(null));
  stage.addEventListener('click', (e) => {
    const name = hitTest(e);
    S.pinned = name && S.pinned !== name ? name : null;
    markRows(); drawOverlay();
    if (name) {
      const row = $(`.pd-row[data-name="${CSS.escape(name)}"]`);
      row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  });
}

function trapFocus(e) {
  const f = $$('button:not([disabled]), input:not([disabled]), select, a[href], [tabindex="0"]').filter((x) => x.offsetParent !== null);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  else if (!S.root.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
}

function syncPreset() {
  const { bedW, bedD } = S.settings;
  const p = PRESETS.find((x) => x.bed[0] === bedW && x.bed[1] === bedD);
  const sel = $('.pd-preset');
  if (sel) sel.value = p ? p.id : 'custom';
}

function validate() {
  const st = S.settings;
  const rules = {
    bedW: (v) => v >= 20 && v <= 2000, bedD: (v) => v >= 20 && v <= 2000, height: (v) => v > 0 && v <= 2000,
    minWall: (v) => v >= 0 && v <= 20, spacing: (v) => v >= 0 && v <= 100,
  };
  let ok = true;
  for (const [k, f] of Object.entries(rules)) {
    const good = isFinite(st[k]) && f(st[k]);
    $(`[data-k="${k}"]`)?.classList.toggle('pd-invalid', !good);
    ok = ok && good;
  }
  S.invalid = !ok;
  return ok;
}

function togglePart(name, on) {
  const p = partByName(name);
  if (on) {
    S.exclude.delete(name);
    if (p?.bought && !S.settings.includeBought) S.include.add(name);
  } else {
    S.include.delete(name);
    S.exclude.add(name);
  }
  const row = $(`.pd-row[data-name="${CSS.escape(name)}"]`);
  row?.classList.toggle('pd-pending', true);
  schedule(450);
}

function partByName(name) { return name ? S.plan?.parts.find((p) => p.name === name) : null; }

function setPlate(n) {
  if (!S.plan || n < 1 || n > S.plan.plates || n === S.plate) return;
  S.plate = n;
  renderPlate();
  if (S.filter === 'plate') renderList();
}

function hoverPart(name, fromList) {
  S.hover = name;
  const p = partByName(name);
  if (fromList && p && printable(p) && p.plate && p.plate !== S.plate) setPlate(p.plate);
  markRows();
  drawOverlay();
}

function markRows() {
  for (const r of $$('.pd-row')) {
    r.classList.toggle('pd-hot', r.dataset.name === S.hover);
    r.classList.toggle('pd-pinned', r.dataset.name === S.pinned);
  }
}

// ------------------------------------------------------------------ planning

function body() {
  const st = S.settings;
  return {
    bed: [st.bedW, st.bedD], bed_height: st.height, min_wall: st.minWall, spacing: st.spacing,
    include_bought: st.includeBought, include: [...S.include], exclude: [...S.exclude], orient: true,
  };
}

function schedule(ms) {
  clearTimeout(S.timer);
  S.dirty = true;
  renderStatus();
  S.timer = setTimeout(() => { if (S && !S.invalid) plan(false); }, ms);
}

async function plan(first) {
  if (!S) return;
  clearTimeout(S.timer);
  S.dirty = false;
  const seq = ++S.seq;
  S.error = null;
  S.planning = true;
  save();
  if (!S.busy) S.busy = stages(first || !S.plan ? 'Planning the print' : 'Re-planning', PLAN_STAGES);
  renderStatus();
  if (first || !S.plan) renderSkeleton();
  const res = await post('print/plan', body());
  if (!S || seq !== S.seq) return;                       // a newer request superseded this one
  S.planning = false;
  S.busy?.finish();
  S.busy = null;
  if (!res.ok) {
    S.error = res.error || 'Planning failed';
    renderAll();
    return;
  }
  S.plan = res;
  if (S.plate > res.plates) S.plate = Math.max(1, res.plates);
  if (S.pinned && !partByName(S.pinned)) S.pinned = null;
  renderAll();
}

function stages(title, list) {
  const el = $('.pd-busy');
  const t0 = performance.now();
  el.hidden = false;
  el.classList.toggle('pd-busy-over', !!S.plan && S.view === 'plan');
  el.innerHTML = `
    <div class="pd-busy-card" role="status">
      <div class="pd-busy-head">
        <span class="spinner"></span>
        <span class="pd-busy-title">${esc(title)}</span>
        <span class="pd-busy-time mono">0 s</span>
      </div>
      <ol class="pd-steps">${list.map((s, i) => `<li data-i="${i}">${icon('check', 'sm pd-step-done')}<span class="pd-step-dot"></span><span>${esc(s.label)}</span></li>`).join('')}</ol>
      <div class="progress"><i></i></div>
    </div>`;
  const bar = el.querySelector('.progress > i');
  const time = el.querySelector('.pd-busy-time');
  const items = [...el.querySelectorAll('.pd-steps li')];
  const tick = () => {
    const t = (performance.now() - t0) / 1000;
    let cur = 0;
    list.forEach((s, i) => { if (t >= s.at) cur = i; });
    items.forEach((li, i) => { li.className = i < cur ? 'done' : i === cur ? 'now' : ''; });
    bar.style.width = `${Math.min(94, 100 * (1 - Math.exp(-t / 7)))}%`;
    time.textContent = `${Math.floor(t)} s`;
  };
  tick();
  const iv = setInterval(tick, 200);
  return {
    stop() { clearInterval(iv); el.hidden = true; el.innerHTML = ''; },
    finish() {
      clearInterval(iv);
      items.forEach((li) => (li.className = 'done'));
      bar.style.width = '100%';
      el.classList.add('pd-busy-out');
      setTimeout(() => { if (!S || S.busy) return; el.hidden = true; el.innerHTML = ''; el.classList.remove('pd-busy-out'); }, 180);
    },
  };
}

// ------------------------------------------------------------------ rendering

function renderAll() {
  renderHeader();
  renderStatus();
  renderList();
  renderPlate();
  renderFooter();
}

function counts() {
  const parts = S.plan?.parts || [];
  const inc = parts.filter(printable);
  return {
    parts: inc.length,
    plates: S.plan?.plates || 0,
    issues: parts.filter((p) => p.included && issueLevel(p) >= 2).length,
    out: parts.filter((p) => !p.included).length,
  };
}

function renderHeader() {
  const el = $('.pd-headline');
  const title = S.title || S.plan?.model || 'This model';
  const arrow = `<span class="pd-arrow">${icon('arrow-left', 'pd-arrow-i')}</span>`;
  if (!S.plan) {
    el.innerHTML = `<span class="pd-ttl">${esc(title)}</span>${arrow}` +
      (S.error ? `<span class="pd-hl pd-hl-err">no plan yet</span>` : `<span class="pd-hl-wait skeleton"></span>`);
    return;
  }
  const c = counts();
  el.innerHTML = `<span class="pd-ttl">${esc(title)}</span>${arrow}<span class="pd-hl"><b>${c.parts}</b> printable ${c.parts === 1 ? 'part' : 'parts'}, <b>${c.plates}</b> ${c.plates === 1 ? 'plate' : 'plates'}</span>`;
  el.title = `${title}: ${S.plan.headline || ''}`;
}

function renderStatus() {
  const el = $('.pd-status');
  if (!el) return;
  if (S.invalid) el.innerHTML = `<span class="pd-st bad">${icon('circle-alert', 'sm')}Check the highlighted setting</span>`;
  else if (S.planning) el.innerHTML = `<span class="pd-st"><span class="spinner"></span>Planning</span>`;
  else if (S.dirty) el.innerHTML = `<span class="pd-st"><span class="dot live"></span>Settings changed</span>`;
  else if (S.error) el.innerHTML = `<span class="pd-st bad">${icon('circle-x', 'sm')}Plan failed</span>`;
  else if (S.plan) el.innerHTML = `<span class="pd-st ok">${icon('circle-check', 'sm')}Plan up to date</span>`;
  else el.innerHTML = '';
}

function renderSettings() {
  syncPreset();
  validate();
}

function renderSkeleton() {
  $('.pd-list').innerHTML = Array.from({ length: 7 }, (_, i) => `
    <div class="pd-row pd-skel" style="animation-delay:${i * 40}ms">
      <span class="skeleton" style="width:16px;height:16px;border-radius:4px"></span>
      <span class="skeleton pd-thumb"></span>
      <span class="pd-skel-lines"><span class="skeleton" style="width:${40 + (i * 23) % 40}%;height:12px"></span><span class="skeleton" style="width:${55 + (i * 17) % 30}%;height:10px"></span></span>
    </div>`).join('');
  $('.pd-tabs').innerHTML = `<span class="skeleton" style="width:150px;height:40px"></span><span class="skeleton" style="width:150px;height:40px"></span>`;
  $('.pd-stage').innerHTML = `<div class="skeleton pd-stage-skel"></div>`;
  $('.pd-plate-foot').innerHTML = '';
  $$('.pd-count').forEach((c) => (c.textContent = ''));
}

function sortParts(list) {
  const by = {
    issues: (a, b) => issueLevel(b) - issueLevel(a) || (a.plate || 99) - (b.plate || 99) || a.name.localeCompare(b.name, undefined, { numeric: true }),
    plate: (a, b) => (a.plate || 99) - (b.plate || 99) || a.name.localeCompare(b.name, undefined, { numeric: true }),
    size: (a, b) => (b.volume || 0) - (a.volume || 0),
    name: (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }),
  }[S.sort];
  return [...list].sort(by);
}

function renderList() {
  const el = $('.pd-list');
  for (const b of $$('.pd-seg-btn')) b.setAttribute('aria-selected', String(b.dataset.filter === S.filter));
  if (S.error && !S.plan) {
    el.innerHTML = '';
    return;
  }
  if (!S.plan) return;
  const parts = S.plan.parts;
  const inPlan = parts.filter((p) => p.included);
  const out = parts.filter((p) => !p.included);
  const isIssue = (p) => issueLevel(p) >= 2;
  const onPlate = (p) => printable(p) && p.plate === S.plate;
  const setCount = (k, n) => { const c = $(`.pd-count[data-c="${k}"]`); if (c) c.textContent = n; };
  setCount('all', inPlan.length);
  setCount('issues', inPlan.filter(isIssue).length);
  setCount('plate', inPlan.filter(onPlate).length);

  if (!parts.length) {
    el.innerHTML = emptyList('Nothing printable in this model', 'The model has no solid bodies to print. Sketches and surfaces are skipped; give the parts thickness and rebuild.');
    return;
  }
  let shown = inPlan;
  if (S.filter === 'issues') shown = inPlan.filter(isIssue);
  if (S.filter === 'plate') shown = inPlan.filter(onPlate);
  shown = sortParts(shown);

  let html = '';
  if (!inPlan.length) {
    html += emptyList('Nothing to print yet', 'Every part is left out. Tick the parts you want to print below, or switch on bought parts.', out.some((p) => S.exclude.has(p.name)) ? `<button class="btn sm" data-act="all-in">${icon('rotate-ccw', 'sm')}Put every part back</button>` : '');
  } else if (!shown.length) {
    html += `<div class="pd-list-note">${icon('circle-check', 'sm')}${S.filter === 'issues' ? 'No thin walls, high overhangs or problems.' : 'No parts on this plate.'}</div>`;
  } else {
    html += `<div class="pd-group"><span class="eyebrow">To print</span><span class="pd-group-n">${plural(shown.length, 'part')}</span></div>`;
    html += shown.map(row).join('');
  }
  if (out.length && S.filter === 'all') {
    html += `<div class="pd-group pd-group-out"><span class="eyebrow">Left out</span><span class="pd-group-n">${plural(out.length, 'part')}</span></div>`;
    html += sortParts(out).map(row).join('');
  }
  el.innerHTML = html;
  markRows();
}

function emptyList(title, text, action = '') {
  return `<div class="pd-empty">${icon('package-open', 'xl')}<div class="pd-empty-t">${esc(title)}</div><div class="pd-empty-x">${esc(text)}</div>${action}</div>`;
}

function row(p) {
  const out = !p.included;
  const badges = [];
  if (p.included && !p.problem) {
    const r = RISK[p.overhang_risk] || RISK.none;
    const tip = p.overhang_area ? `${num(p.overhang_area, 0)} mm² overhang, about ${num(p.support_volume / 1000, 1)} cm³ of support` : 'No overhangs past 45°: prints without supports';
    badges.push(`<span class="badge pd-risk ${r.cls}" title="${esc(tip)}"><span class="pd-bars" data-n="${r.bars}"><i></i><i></i><i></i></span>${r.text}</span>`);
  }
  if (p.included && p.thin) badges.push(`<span class="badge warn" title="Thinnest wall found ${num(p.min_wall, 2)} mm; your threshold is ${num(S.plan.settings.min_wall, 2)} mm">${icon('triangle-alert', 'sm')}wall ${num(p.min_wall, 2)} mm &lt; ${num(S.plan.settings.min_wall, 2)}</span>`);
  if (p.bought) badges.push(`<span class="badge neutral" title="${esc(p.bought_reason)}">${icon('nut', 'sm')}bought: ${esc(boughtWhat(p))}</span>`);
  if (out && !p.bought) badges.push(`<span class="badge neutral">${icon('eye-off', 'sm')}left out</span>`);

  const thumb = p.thumb
    ? `<span class="pd-thumb" title="As it sits on the bed: ${esc(p.orientation)}"><img src="${esc(p.thumb)}" alt="" loading="lazy" decoding="async"></span>`
    : `<span class="pd-thumb pd-thumb-none" style="--c:${esc(p.color || '#888')}">${icon(p.bought ? 'nut' : 'box')}</span>`;
  const plate = printable(p) && p.plate ? `<span class="pd-plate-chip" title="On plate ${p.plate}">P${p.plate}</span>` : '';
  const meta = p.included
    ? `<span class="pd-orient" title="Orientation on the bed">${icon('rotate-3d', 'sm')}${esc(p.orientation === 'as is' ? 'as modelled' : p.orientation)}${p.turned ? ', turned 90°' : ''}</span><span class="pd-sep"></span><span class="mono pd-dims">${dims(p.size)} mm</span>`
    : `<span class="pd-orient">${esc(p.bought ? (p.bought_reason || 'bought part') : 'excluded from the print')}</span>`;
  return `
  <div class="pd-row${out ? ' pd-out' : ''}${p.problem ? ' pd-problem' : ''}" data-name="${esc(p.name)}">
    <label class="pd-check" title="${p.included ? 'Leave this part out' : 'Print this part'}">
      <input type="checkbox" data-part="${esc(p.name)}" ${p.included ? 'checked' : ''} aria-label="Print ${esc(p.name)}">
      <span class="pd-box">${icon('check', 'sm')}</span>
    </label>
    ${thumb}
    <div class="pd-info">
      <div class="pd-name-line"><span class="pd-swatch" style="background:${esc(p.color || '#888')}"></span><span class="pd-name mono">${esc(p.name)}</span>${plate}</div>
      <div class="pd-meta">${meta}</div>
      ${badges.length ? `<div class="pd-badges">${badges.join('')}</div>` : ''}
      ${p.problem ? `<div class="pd-why">${icon('circle-x', 'sm')}<span>${esc(p.problem)}</span></div>` : ''}
    </div>
  </div>`;
}

// ------------------------------------------------------------------ plate preview

function plateCounts() {
  const n = S.plan?.plates || 0;
  const c = Array.from({ length: n }, () => 0);
  for (const p of S.plan?.parts || []) if (printable(p) && p.plate) c[p.plate - 1]++;
  return c;
}

function renderPlate() {
  const tabs = $('.pd-tabs'), stage = $('.pd-stage'), foot = $('.pd-plate-foot');
  $('.pd-main').classList.toggle('pd-errored', !!(S.error && !S.plan));
  if (S.error && !S.plan) {
    tabs.innerHTML = '';
    foot.innerHTML = '';
    stage.innerHTML = `
      <div class="pd-error" role="alert">
        <div class="pd-error-i">${icon('circle-x', 'xl')}</div>
        <div class="pd-empty-t">Could not plan the print</div>
        <pre class="pd-error-msg">${esc(S.error)}</pre>
        <div class="pd-error-x">The model may not build, or the server hit an error. Fix the model or the settings and try again.</div>
        <button class="btn primary" data-act="retry">${icon('rotate-ccw')}Try again</button>
      </div>`;
    $('.pd-list').innerHTML = '';
    return;
  }
  if (!S.plan) return;
  const n = S.plan.plates;
  const pc = plateCounts();
  const imgs = S.plan.plate_images || [];
  if (S.error) {
    // keep the last plan on screen, but say the re-plan failed
    stage.dataset.err = '1';
  }
  tabs.innerHTML = n
    ? imgs.map((src, i) => `
      <button class="pd-tab" role="tab" data-plate="${i + 1}" aria-selected="${i + 1 === S.plate}" tabindex="${i + 1 === S.plate ? 0 : -1}">
        <img src="${esc(src)}" alt="" class="pd-tab-img">
        <span class="pd-tab-txt"><b>Plate ${i + 1}</b><span>${plural(pc[i], 'part')}</span></span>
      </button>`).join('')
    : '';
  const W = S.plan.settings.bed[0], D = S.plan.settings.bed[1];
  if (!n) {
    stage.innerHTML = `<div class="pd-bed-empty" style="aspect-ratio:${W}/${D}">${icon('layers', 'xl')}<div class="pd-empty-t">Empty bed</div><div class="pd-empty-x">No parts to lay out at ${num(W)} × ${num(D)} mm.</div></div>`;
    foot.innerHTML = '';
    S.imgSize = {};
    return;
  }
  const src = imgs[S.plate - 1];
  stage.innerHTML = `
    <div class="pd-plate" style="visibility:hidden">
      <img class="pd-plate-img" src="${esc(src)}" alt="Plate ${S.plate}: ${pc[S.plate - 1]} parts on a ${num(W)} x ${num(D)} mm bed">
      <svg class="pd-overlay" xmlns="http://www.w3.org/2000/svg"></svg>
      <div class="pd-tip" hidden></div>
    </div>`;
  const img = stage.querySelector('img');
  const ready = () => {
    S.imgSize = { w: img.naturalWidth, h: img.naturalHeight };
    layoutPlate();
    stage.querySelector('.pd-plate').style.visibility = '';
    drawOverlay();
  };
  if (img.complete && img.naturalWidth) ready(); else img.addEventListener('load', ready, { once: true });
  img.addEventListener('error', () => {
    stage.innerHTML = `<div class="pd-bed-empty" style="aspect-ratio:${W}/${D}">${icon('triangle-alert', 'xl')}<div class="pd-empty-t">Plate picture missing</div><div class="pd-empty-x">Re-plan to draw it again.</div><button class="btn sm" data-act="retry">${icon('rotate-ccw', 'sm')}Re-plan</button></div>`;
  }, { once: true });

  const st = S.plan.settings;
  foot.innerHTML = `
    <span class="pd-legend">${icon('grid-3x3', 'sm')}<span class="mono">${num(W)} × ${num(D)} mm</span> bed, 10 mm grid</span>
    <span class="pd-legend">${icon('arrow-up-from-line', 'sm')}<span class="mono">${num(st.bed_height)} mm</span> build height</span>
    <span class="pd-legend pd-legend-hint">${icon('mouse-pointer-2', 'sm')}Hover a part to find it</span>`;
}

function layoutPlate() {
  if (!S) return;
  const wrap = $('.pd-stage-wrap'), plate = $('.pd-plate');
  const { w, h } = S.imgSize || {};
  if (!wrap || !plate || !w) return;
  const aw = wrap.clientWidth - 2, ah = wrap.clientHeight - 2;
  const k = Math.min(aw / w, ah / h, 1.6);
  plate.style.width = `${Math.floor(w * k)}px`;
  plate.style.height = `${Math.floor(h * k)}px`;
  const svg = plate.querySelector('svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
}

// image geometry of plate_picture(): pad 26 px, one scale for the longer bed side over (px - 2 pad), +Y up
function footprint(p) {
  const { w, h } = S.imgSize;
  const [W, D] = S.plan.settings.bed;
  const pad = 26;
  const scale = (Math.max(w, h) - 2 * pad) / Math.max(W, D);
  const [a, b] = p.turned ? [p.size[1], p.size[0]] : [p.size[0], p.size[1]];
  return { x: pad + (p.x - a / 2 + W / 2) * scale, y: pad + (D / 2 - p.y - b / 2) * scale, w: a * scale, h: b * scale };
}

function bedRect() {
  const { w, h } = S.imgSize;
  const [W, D] = S.plan.settings.bed;
  const pad = 26, scale = (Math.max(w, h) - 2 * pad) / Math.max(W, D);
  return { x: pad, y: pad, w: W * scale, h: D * scale };
}

function platePartsHere() {
  return (S.plan?.parts || []).filter((p) => printable(p) && p.plate === S.plate && p.size?.length === 3);
}

function hitTest(e) {
  const svg = $('.pd-overlay');
  if (!svg || !S.imgSize.w) return null;
  const r = svg.getBoundingClientRect();
  const x = ((e.clientX - r.left) / r.width) * S.imgSize.w, y = ((e.clientY - r.top) / r.height) * S.imgSize.h;
  let best = null, area = Infinity;
  for (const p of platePartsHere()) {
    const f = footprint(p);
    if (x >= f.x - 2 && x <= f.x + f.w + 2 && y >= f.y - 2 && y <= f.y + f.h + 2 && f.w * f.h < area) { best = p.name; area = f.w * f.h; }
  }
  return best;
}

function drawOverlay() {
  const svg = $('.pd-overlay');
  if (!svg || !S.imgSize.w) return;
  const { w, h } = S.imgSize;
  const here = platePartsHere();
  const issues = S.filter === 'issues' ? here.filter((p) => issueLevel(p) >= 2) : [];
  const focus = [S.hover, S.pinned].map(partByName).filter((p) => p && printable(p) && p.plate === S.plate);
  const uniq = [...new Map(focus.map((p) => [p.name, p])).values()];
  let out = '';
  for (const p of issues) {
    const f = footprint(p);
    out += `<rect class="pd-ov-issue" x="${f.x - 3}" y="${f.y - 3}" width="${f.w + 6}" height="${f.h + 6}" rx="4"/>`;
  }
  if (uniq.length) {
    const holes = uniq.map((p) => { const f = footprint(p); return `M${f.x - 4},${f.y - 4}h${f.w + 8}v${f.h + 8}h${-(f.w + 8)}z`; }).join('');
    const bed = bedRect();
    out += `<path class="pd-ov-dim" fill-rule="evenodd" d="M${bed.x},${bed.y}h${bed.w}v${bed.h}h${-bed.w}z${holes}"/>`;
    for (const p of uniq) {
      const f = footprint(p);
      out += `<rect class="pd-ov-ring${p.name === S.pinned ? ' pinned' : ''}" x="${f.x - 4}" y="${f.y - 4}" width="${f.w + 8}" height="${f.h + 8}" rx="5"/>`;
    }
  }
  svg.innerHTML = out;
  // a name tag over the focused part
  const tip = $('.pd-tip');
  const p = partByName(S.hover) || partByName(S.pinned);
  if (tip && p && printable(p) && p.plate === S.plate) {
    const f = footprint(p);
    tip.innerHTML = `<b class="mono">${esc(p.name)}</b><span>${esc(p.orientation === 'as is' ? 'as modelled' : p.orientation)} · ${num(p.size[2])} mm tall</span>`;
    tip.hidden = false;
    const below = f.y < h * 0.16;
    tip.style.left = `${((f.x + f.w / 2) / w) * 100}%`;
    tip.style.top = `${((below ? f.y + f.h + 8 : f.y - 8) / h) * 100}%`;
    tip.classList.toggle('below', below);
  } else if (tip) tip.hidden = true;
}

// ------------------------------------------------------------------ footer + export

function renderFooter() {
  const el = $('.pd-foot');
  if (S.view === 'done') {
    el.innerHTML = `
      <button class="btn ghost" data-act="back">${icon('arrow-left')}Back to the plan</button>
      <span class="pd-grow"></span>
      <button class="btn" data-act="close">Done</button>`;
    return;
  }
  const c = counts();
  const noFmt = !S.formats.stl && !S.formats['3mf'];
  const disabled = !S.plan || S.planning || S.dirty || S.exporting || !c.parts || noFmt || S.invalid;
  const summary = S.plan?.summary ? S.plan.summary.split('; ') : [];
  const why = !S.plan ? '' : !c.parts ? 'Nothing to export: tick at least one part' : noFmt ? 'Pick a file format' : '';
  el.innerHTML = `
    <div class="pd-summary" title="${esc(S.plan?.summary || '')}">
      ${S.error && S.plan ? `<span class="pd-sum-err">${icon('circle-x', 'sm')}Re-plan failed: ${esc(S.error)} <button class="btn sm ghost" data-act="retry">Retry</button></span>` :
        summary.length ? `${icon('list-checks', 'sm')}<span><b>${esc(summary[0])}</b>${summary.slice(1).map((s) => `<span class="pd-sum-sep">·</span>${esc(s)}`).join('')}</span>` :
        S.plan || S.error ? '' : `<span class="skeleton" style="width:340px;height:12px"></span>`}
    </div>
    <div class="pd-formats" role="group" aria-label="File formats">
      ${fmtChip('stl', 'STL')}${fmtChip('3mf', '3MF')}
    </div>
    <button class="btn primary lg pd-export" data-act="export" ${disabled ? 'disabled' : ''} title="${esc(why)}">
      ${S.exporting ? '<span class="spinner"></span>Writing files' : `${icon('download')}Export files`}
    </button>`;
}

function fmtChip(k, label) {
  return `<label class="pd-fmt"><input type="checkbox" data-fmt="${k}" ${S.formats[k] ? 'checked' : ''}><span class="pd-box">${icon('check', 'sm')}</span>${label}</label>`;
}

async function doExport() {
  if (!S || S.exporting || !S.plan) return;
  S.exporting = true;
  renderFooter();
  S.busy = stages('Exporting files', EXPORT_STAGES);
  $('.pd-busy').classList.add('pd-busy-over');
  const formats = Object.keys(S.formats).filter((k) => S.formats[k]);
  const res = await post('print/export', { ...body(), formats });
  if (!S) return;
  S.busy?.finish();
  S.busy = null;
  S.exporting = false;
  S.result = res;
  S.view = 'done';
  renderDone();
  renderFooter();
}

function showPlan() {
  S.view = 'plan';
  $('.pd-done').hidden = true;
  $('.pd-main').hidden = false;
  $('.pd-settings').classList.remove('pd-locked');
  renderFooter();
  layoutPlate();
}

function renderDone() {
  const r = S.result;
  const el = $('.pd-done');
  $('.pd-main').hidden = true;
  $('.pd-settings').classList.add('pd-locked');
  el.hidden = false;
  if (!r.ok) {
    el.innerHTML = `
      <div class="pd-done-card">
        <div class="pd-done-mark bad">${icon('circle-x', 'xl')}</div>
        <h3 class="pd-done-t">The export failed</h3>
        <p class="pd-done-x">Nothing was written. The server said:</p>
        <pre class="pd-error-msg">${esc(r.error)}</pre>
        <div class="pd-done-actions"><button class="btn primary lg" data-act="export">${icon('rotate-ccw')}Try again</button></div>
      </div>`;
    return;
  }
  // problems the export found (by part, or "plate N"), plus the parts the plan already could not place
  const planProbs = (S.plan?.parts || []).filter((p) => p.included && p.problem && !(r.problems || {})[p.name]).map((p) => [p.name, p.problem]);
  const probs = [...Object.entries(r.problems || {}), ...planProbs];
  const total = r.parts_written + probs.filter(([k]) => !/^plate \d+$/.test(k)).length;
  const files = r.files || [];
  const plateFiles = files.filter((f) => f.includes('/plates/'));
  const partFiles = files.filter((f) => f.includes('/parts/'));
  const fileUrl = (f) => `print/file?path=${encodeURIComponent(f)}`;
  const base = (f) => f.split('/').pop();
  const byPlate = {};
  for (const f of plateFiles) {
    const m = /plate(\d+)\.(\w+)$/.exec(f);
    if (m) (byPlate[m[1]] ||= []).push({ f, ext: m[2].toUpperCase() });
  }
  const none = r.parts_written === 0;
  const state = none ? 'bad' : probs.length ? 'warn' : 'ok';
  const headline = none ? 'No files were written'
    : probs.length ? `${r.parts_written} of ${total} parts written, ${plural(probs.length, 'problem')}`
    : `${plural(r.parts_written, 'part')} written`;
  const sub = none ? 'Every part failed its mesh check. See why below.'
    : probs.length ? 'The parts below were not written. Fix them in the model and export again.'
    : `${plural(Object.keys(byPlate).length, 'plate')}, each part as its own file too. Every mesh was checked watertight and read back before it was saved.`;
  const pc = plateCounts();
  const imgs = S.plan?.plate_images || [];
  el.innerHTML = `
    <div class="pd-done-card">
      <div class="pd-done-top">
        <div class="pd-done-mark ${state}">${icon(state === 'ok' ? 'circle-check' : state === 'warn' ? 'triangle-alert' : 'circle-x', 'xl')}</div>
        <div class="pd-done-head">
          <h3 class="pd-done-t">${esc(headline)}</h3>
          <p class="pd-done-x">${sub}</p>
        </div>
        ${r.zip && !none ? `<a class="btn primary lg pd-zip" href="${esc(r.zip)}" download>${icon('download')}Download all (.zip)</a>` : ''}
      </div>
      ${probs.length ? `
      <div class="pd-probs" role="alert">
        <div class="pd-probs-h">${icon('octagon-alert', 'sm')}Not written</div>
        ${probs.map(([name, why]) => `<div class="pd-prob"><span class="mono pd-prob-n">${esc(name)}</span><span class="pd-prob-w">${esc(why)}</span></div>`).join('')}
      </div>` : ''}
      ${Object.keys(byPlate).length ? `
      <div class="pd-plates-out">
        ${Object.entries(byPlate).map(([n, fs]) => `
          <div class="pd-plate-card">
            ${imgs[n - 1] ? `<img src="${esc(imgs[n - 1])}" alt="Plate ${n}">` : ''}
            <div class="pd-plate-card-b">
              <div><b>Plate ${n}</b><span>${plural(pc[n - 1] ?? 0, 'part')}</span></div>
              <span class="pd-plate-card-dl">${fs.map(({ f, ext }) => `<a class="btn sm" href="${esc(fileUrl(f))}" download title="Download ${esc(base(f))}">${icon('download', 'sm')}${ext}</a>`).join('')}</span>
            </div>
          </div>`).join('')}
      </div>` : ''}
      <div class="pd-folder">
        <span class="pd-label">Saved to</span>
        <div class="pd-folder-row">
          ${icon('folder-open', 'sm')}<code class="pd-path" title="${esc(r.folder)}">${esc(r.folder)}</code>
          <button class="btn sm ghost" data-act="copy" title="Copy the path">${icon('copy', 'sm')}Copy</button>
        </div>
      </div>
      ${files.length ? `
      <div class="pd-files">
        <button class="pd-files-h" data-act="toggle-files">${icon('chevron-right', 'sm pd-files-caret')}<span>${plural(files.length, 'file')}</span><span class="faint">${partFiles.length} per part, ${plateFiles.length} per plate${files.some((f) => f.endsWith('.json')) ? ', the plan as JSON' : ''}</span></button>
        <div class="pd-files-list">${files.map((f) => `<a href="${esc(fileUrl(f))}" download class="mono">${esc(f.replace(/^files\//, ''))}</a>`).join('')}</div>
      </div>` : ''}
    </div>`;
}

async function copyFolder(btn) {
  const path = S.result?.folder || '';
  try { await navigator.clipboard.writeText(path); } catch {
    const r = document.createRange(); r.selectNodeContents($('.pd-path')); const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  }
  btn.innerHTML = `${icon('check', 'sm')}Copied`;
  setTimeout(() => { if (btn.isConnected) btn.innerHTML = `${icon('copy', 'sm')}Copy`; }, 1400);
}
