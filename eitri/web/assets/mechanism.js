// The mechanism editor: the kinematic diagram the kernel drew, with an inspector for joints (limits, home, actuator, required
// speed), links (length, mass) and targets (position, tolerance), and a summary of every joint's torque margin. Built on
// domain_editor.js; the side panel's Parameters section is the parameter panel.
import { createDiagramEditor, el, esc, num } from './domain_editor.js';

const ACT_FIELDS = [['stall_kgcm', 'stall torque (kg·cm)'], ['rated_kgcm', 'rated torque (kg·cm)'], ['stall_N', 'stall force (N)'], ['rated_N', 'rated force (N)'],
  ['speed_deg_s', 'speed (°/s)'], ['speed_mm_s', 'speed (mm/s)'], ['gear_ratio', 'gear ratio'], ['efficiency', 'efficiency (0-1)'], ['travel_deg', 'travel (°)'], ['travel_mm', 'travel (mm)'], ['mass_g', 'motor mass (g)']];

export function createEditor(host, api) {
  const spec = {
    kind: 'mechanism', dataKey: 'mechanism', svgUrl: 'domain.svg',
    hint: () => 'click a joint, link or target to edit it · scroll to zoom · drag the background to pan',
    downloads: [{ fmt: 'joints', label: 'joints', title: 'Joint / torque table (CSV)' }, { fmt: 'mass', label: 'mass', title: 'Mass rollup (CSV)' }, { fmt: 'svg', label: 'SVG', title: 'The diagram as SVG' }],
    selectEdit: e => e.joint ? { kind: 'joint', id: e.joint } : e.link ? { kind: 'link', id: e.link } : e.target ? { kind: 'target', id: e.target } : e.name && e.op === 'add_target' ? { kind: 'target', id: e.name } : null,
    inspector(sel, data, state, h) {
      if (sel.kind === 'joint') return jointPanel(sel.id, data, h);
      if (sel.kind === 'link') return linkPanel(sel.id, data, h);
      if (sel.kind === 'target') return targetPanel(sel.id, data, h);
      if (sel.kind === 'mass') return massPanel(sel.id, data, h);
      return null;
    },
    summary(data, state, h) {
      const p = el('div', { class: 'hp' });
      p.appendChild(el('div', { class: 'hh' }, '<div><b>Joints</b><div class="hsub">worst-pose load vs the actuator · click a joint in the diagram to edit it</div></div>'));
      const t = el('div', { class: 'hnets' }, '<div class="hnh mech"><span>joint</span><span>needs</span><span>stall</span><span>margin</span><span></span></div>');
      for (const j of data.joints || []) {
        if (j.type === 'fixed') continue;
        const r = el('div', { class: 'hnr mech' });
        const st = j.status || 'ungraded';
        r.append(el('b', {}, esc(j.name)), el('span', {}, j.required == null ? '—' : `${j.required.toFixed(2)} ${j.load_unit}`), el('span', {}, j.stall == null ? '—' : j.stall.toFixed(1)),
          el('span', { class: 'hdrop ' + st }, j.margin_stall == null ? st : j.margin_stall.toFixed(2) + 'x'), el('span', {}, ''));
        r.style.cursor = 'pointer'; r.onclick = () => h.pick({ kind: 'joint', id: j.name });
        t.appendChild(r);
      }
      p.appendChild(t);
      const targets = data.targets || [];
      if (targets.length) p.appendChild(el('div', { class: 'hsub' }, 'Targets: ' + targets.map(t => `${esc(t.name)} ${t.reachable ? '✓' : t.reachable === false ? '✗' : '?'}`).join(' · ')));
      const add = el('button', { class: 'small' }, '+ target'); add.onclick = () => { const name = (prompt('Name of the new target:') || '').trim(); if (!name) return; const tip = data.tip ? data.tip.at : [100, 0, 0]; h.run({ op: 'add_target', name, at: tip, tol_mm: 2 }); };
      p.appendChild(add);
      const m = data.masses || [];
      p.appendChild(el('div', { class: 'hsub' }, `${(data.links || []).length} links · ${(data.joints || []).filter(j => j.type !== 'fixed').length} moving joints · ${m.length ? m.map(x => `${esc(x.name)} ${x.mass_g} g`).join(', ') : 'no payload'}`));
      return p;
    },
  };
  const ed = createDiagramEditor(host, api, spec);
  return ed;

  function jointPanel(name, data, h) {
    const j = (data.joints || []).find(x => x.name === name); if (!j) return null;
    const p = el('div', { class: 'hp' });
    p.appendChild(h.head(`Joint ${j.name}`, `${j.type} · ${j.parent} → ${j.child} · axis (${j.axis.join(', ')})` + (j.required != null ? ` · needs ${j.required.toFixed(2)} ${j.load_unit}` : '')));
    p.insertAdjacentHTML('beforeend', h.chips(j.name));
    if (j.type !== 'fixed') {
      const lo = h.input(j.limit[0], { type: 'number', step: 'any' }), hi = h.input(j.limit[1], { type: 'number', step: 'any' });
      const setLim = () => { const a = num(lo.value), b = num(hi.value); if (a != null && b != null && a < b) h.run({ op: 'set_joint', joint: j.name, limit: [a, b] }); };
      h.commitOn(lo, setLim); h.commitOn(hi, setLim);
      const row = el('div', { class: 'hrow' }); row.append(lo, el('span', { class: 'hsub' }, '..'), hi, el('span', { class: 'hsub' }, esc(j.unit)));
      p.appendChild(h.field('limits', row));
      p.appendChild(h.numField('home', j.home, {}, v => { if (v != null) h.run({ op: 'set_joint', joint: j.name, home: v }); }));
      p.appendChild(h.numField(j.type === 'revolute' ? 'required °/s' : 'required mm/s', j.speed_required ?? '', { placeholder: 'none' }, v => h.run({ op: 'set_speed', joint: j.name, value: v })));
    }
    const a = j.actuator;
    p.appendChild(el('div', { class: 'hsub' }, a ? `<b>Actuator</b> ${esc(a.name)}` + (j.stall != null ? ` · ${j.stall.toFixed(1)} ${j.load_unit} stall at the joint` : '') : '<b>Actuator</b> none'));
    const nm = h.input(a ? a.name : '', { placeholder: 'name' }); h.commitOn(nm, v => { if (v.trim()) h.run({ op: 'set_actuator', joint: j.name, name: v.trim() }); });
    p.appendChild(h.field('name', nm));
    for (const [k, label] of ACT_FIELDS) {
      if (j.type === 'revolute' && /_N$|_mm/.test(k)) continue;
      if (j.type === 'prismatic' && /kgcm|_deg/.test(k)) continue;
      p.appendChild(h.numField(label, a ? (a[k] ?? '') : '', { placeholder: k === 'gear_ratio' || k === 'efficiency' ? '1' : k === 'mass_g' ? '0' : '' }, v => h.run({ op: 'set_actuator', joint: j.name, [k]: v })));
    }
    if (a) { const rm = el('button', { class: 'small danger' }, 'Remove actuator'); rm.onclick = () => h.run({ op: 'set_actuator', joint: j.name, remove: true }); p.appendChild(rm); }
    return p;
  }
  function linkPanel(name, data, h) {
    const l = (data.links || []).find(x => x.name === name); if (!l) return null;
    const p = el('div', { class: 'hp' });
    p.appendChild(h.head(`Link ${l.name}`, (l.fixed ? 'ground link' : `moved by ${l.parent_joint}`) + ` · direction (${l.direction.join(', ')})`));
    p.insertAdjacentHTML('beforeend', h.chips(l.name));
    p.appendChild(h.numField('length (mm)', l.length_mm, { min: 0 }, v => { if (v != null && v >= 0) h.run({ op: 'set_link', link: l.name, length_mm: v }); }));
    p.appendChild(h.numField('mass (g)', l.mass_g, { min: 0 }, v => { if (v != null && v >= 0) h.run({ op: 'set_link', link: l.name, mass_g: v }); }));
    p.appendChild(el('div', { class: 'hsub' }, `centre of mass at (${l.cg.join(', ')}) in the link's frame`));
    return p;
  }
  function targetPanel(name, data, h) {
    const t = (data.targets || []).find(x => x.name === name); if (!t) return null;
    const p = el('div', { class: 'hp' });
    p.appendChild(h.head(`Target ${t.name}`, t.reachable ? 'reached' : t.reachable === false ? 'NOT reached' : ''));
    p.insertAdjacentHTML('beforeend', h.chips(t.name));
    const xs = ['x', 'y', 'z'].map((k, i) => h.input(t.at[i], { type: 'number', step: 'any', title: k }));
    const setAt = () => { const at = xs.map(i => num(i.value)); if (at.every(v => v != null)) h.run({ op: 'set_target', target: t.name, at }); };
    xs.forEach(i => h.commitOn(i, setAt));
    const row = el('div', { class: 'hrow' }); row.append(...xs);
    p.appendChild(h.field('at (mm)', row));
    p.appendChild(h.numField('tolerance (mm)', t.tol_mm, { min: 0.1 }, v => { if (v > 0) h.run({ op: 'set_target', target: t.name, tol_mm: v }); }));
    const rm = el('button', { class: 'small danger' }, 'Remove target'); rm.onclick = () => h.run({ op: 'remove_target', target: t.name }).then(r => { if (r && r.ok) h.pick(null); });
    p.appendChild(rm);
    return p;
  }
  function massPanel(name, data, h) {
    const m = (data.masses || []).find(x => x.name === name); if (!m) return null;
    const p = el('div', { class: 'hp' });
    p.appendChild(h.head(m.kind === 'payload' ? 'Payload' : `Mass ${m.name}`, `${m.mass_g} g on ${m.link}`));
    if (m.kind === 'payload') p.appendChild(h.numField('mass (g)', m.mass_g, { min: 0 }, v => { if (v != null && v >= 0) h.run({ op: 'set_payload', mass_g: v }); }));
    return p;
  }
}
