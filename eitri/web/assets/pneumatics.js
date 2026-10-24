// The pneumatics editor: the schematic the kernel drew, a component list, and an inspector per component kind (cylinder, valve,
// regulator / FRL, supply, flow control) and per tube; components can be dragged (LAYOUT hints). Built on domain_editor.js.
import { createDiagramEditor, el, esc, num } from './domain_editor.js';

const CYL_FIELDS = [['bore_mm', 'bore (mm)'], ['rod_mm', 'rod (mm)'], ['stroke_mm', 'stroke (mm)'], ['load_N', 'load extend (N)'], ['load_retract_N', 'load retract (N)'],
  ['speed_mm_s', 'speed extend (mm/s)'], ['speed_retract_mm_s', 'speed retract (mm/s)'], ['cycles_per_min', 'cycles / min'], ['spring_N', 'spring (N, single)'], ['friction_fraction', 'friction allowance (0-1)']];
const FLOW_FIELDS = [['C', 'C (L/(s·bar))'], ['b', 'b (0-1)']];

export function createEditor(host, api) {
  const spec = {
    kind: 'pneumatics', dataKey: 'pneumatics', svgUrl: 'domain.svg', draggable: new Set(['component']),
    hint: () => 'click a component or tube to edit it · drag components to place them · scroll to zoom · drag the background to pan',
    downloads: [{ fmt: 'bom', label: 'BOM', title: 'Bill of materials (CSV)' }, { fmt: 'cylinders', label: 'cylinders', title: 'Cylinder sizing table (CSV)' }, { fmt: 'svg', label: 'SVG', title: 'The schematic as SVG' }],
    selectEdit: e => e.component ? { kind: 'component', id: e.component } : e.tube ? { kind: 'tube', id: e.tube } : e.name && e.op === 'add_tube' ? { kind: 'tube', id: e.name } : null,
    inspector(sel, data, state, h) {
      if (sel.kind === 'component') return componentPanel(sel.id, data, h);
      if (sel.kind === 'tube') return tubePanel(sel.id, data, h);
      return null;
    },
    summary(data, state, h) {
      const p = el('div', { class: 'hp' });
      p.appendChild(el('div', { class: 'hh' }, '<div><b>Components</b><div class="hsub">click one here or in the schematic to edit it</div></div>'));
      const list = el('div', { class: 'hnets' });
      for (const c of data.components || []) {
        if (c.kind === 'silencer') continue;
        const r = el('div', { class: 'hnr pneu' });
        const st = (c.strokes || []).map(s => s.status).includes('fail') ? 'fail' : (c.strokes || []).map(s => s.status).includes('warn') ? 'warn' : '';
        r.append(el('b', {}, esc(c.name)), el('span', { class: 'hsub' }, esc(short(c))), el('span', { class: 'hdrop ' + st }, st || ''));
        r.style.cursor = 'pointer'; r.onclick = () => h.pick({ kind: 'component', id: c.name });
        list.appendChild(r);
      }
      for (const t of data.tubes || []) {
        const r = el('div', { class: 'hnr pneu' });
        r.append(el('b', {}, esc(t.name)), el('span', { class: 'hsub' }, `${esc(t.a)} → ${esc(t.b)} · ${t.id_mm} mm · ${t.length_m} m`), el('span', {}, ''));
        r.style.cursor = 'pointer'; r.onclick = () => h.pick({ kind: 'tube', id: t.name });
        list.appendChild(r);
      }
      p.appendChild(list);
      return p;
    },
  };
  return createDiagramEditor(host, api, spec);

  function short(c) {
    if (c.kind === 'cylinder') return `Ø${c.bore_mm}×${c.stroke_mm} ${c.acting}` + (c.pressure_at != null ? ` · ${c.pressure_at} bar` : '');
    if (c.kind === 'valve') return `${c.cls} ${c.actuation}/${c.returns}`;
    if (c.kind === 'frl' || c.kind === 'regulator') return `${c.kind} ${c.set_bar} bar`;
    if (c.kind === 'supply') return `${c.pressure_bar} bar` + (c.flow_l_min ? ` · ${c.flow_l_min} L/min` : '');
    if (c.kind === 'flow_control') return `meter-${c.meter}`;
    return c.kind;
  }
  function componentPanel(name, data, h) {
    const c = (data.components || []).find(x => x.name === name); if (!c) return null;
    const p = el('div', { class: 'hp' });
    p.appendChild(h.head(`${c.kind === 'frl' ? 'FRL' : c.kind[0].toUpperCase() + c.kind.slice(1)} ${c.name}`, short(c) + (c.ports.length ? ` · ports ${c.ports.join(', ')}` : '')));
    p.insertAdjacentHTML('beforeend', h.chips(c.name));
    const set = (k, v) => h.run({ op: 'set_component', component: c.name, [k]: v });
    if (c.kind === 'cylinder') {
      const act = h.select(c.acting, [['double', 'double-acting'], ['single', 'single-acting (spring return)']]); act.onchange = () => set('acting', act.value);
      p.appendChild(h.field('acting', act));
      for (const [k, label] of CYL_FIELDS) p.appendChild(h.numField(label, c[k] ?? '', {}, v => set(k, v)));
      for (const s of c.strokes || []) if (s.usable_N != null) p.appendChild(el('div', { class: 'hsub' }, `${s.name}: ${s.usable_N.toFixed(0)} N usable vs ${s.load_N} N` + (s.speed_available != null ? ` · ${s.speed_available.toFixed(0)}/${s.speed_required} mm/s` : '')));
    } else if (c.kind === 'valve') {
      const cls = h.select(c.cls, (data.valve_classes || ['2/2', '3/2', '4/2', '5/2', '5/3']).map(x => [x, x])); cls.onchange = () => set('cls', cls.value);
      const act = h.select(c.actuation, ['solenoid', 'manual', 'pilot', 'lever', 'pedal', 'roller'].map(x => [x, x])); act.onchange = () => set('actuation', act.value);
      const ret = h.select(c.returns, ['spring', 'solenoid', 'detent'].map(x => [x, x])); ret.onchange = () => set('returns', ret.value);
      p.append(h.field('class', cls), h.field('actuation', act), h.field('return', ret));
      if (c.cls === '3/2' || c.cls === '2/2') { const n = h.select(c.normally, [['closed', 'normally closed'], ['open', 'normally open']]); n.onchange = () => set('normally', n.value); p.appendChild(h.field('rest', n)); }
      if (c.cls === '5/3') { const n = h.select(c.center, [['closed', 'closed centre'], ['exhaust', 'exhaust centre'], ['pressure', 'pressure centre']]); n.onchange = () => set('center', n.value); p.appendChild(h.field('centre', n)); }
    } else if (c.kind === 'frl' || c.kind === 'regulator') {
      p.appendChild(h.numField('set pressure (bar)', c.set_bar, { min: 0.1 }, v => { if (v > 0) set('set_bar', v); }));
    } else if (c.kind === 'supply') {
      p.appendChild(h.numField('pressure (bar)', c.pressure_bar, { min: 0.1 }, v => { if (v > 0) set('pressure_bar', v); }));
      p.appendChild(h.numField('capacity (L/min)', c.flow_l_min ?? '', { placeholder: 'not given' }, v => set('flow_l_min', v)));
    } else if (c.kind === 'flow_control') {
      const m = h.select(c.meter, [['out', 'meter-out'], ['in', 'meter-in'], ['both', 'plain throttle']]); m.onchange = () => set('meter', m.value);
      p.appendChild(h.field('meter', m));
    }
    if (['valve', 'frl', 'regulator', 'flow_control', 'silencer'].includes(c.kind)) for (const [k, label] of FLOW_FIELDS) p.appendChild(h.numField(label, c[k] ?? '', { placeholder: 'estimated' }, v => set(k, v)));
    const pn = h.input(c.part_number || '', { placeholder: 'part number' }); h.commitOn(pn, v => set('part_number', v)); p.appendChild(h.field('part no.', pn));
    return p;
  }
  function tubePanel(name, data, h) {
    const t = (data.tubes || []).find(x => x.name === name); if (!t) return null;
    const p = el('div', { class: 'hp' });
    p.appendChild(h.head(`Tube ${t.name}`, `${t.a} → ${t.b}`));
    p.insertAdjacentHTML('beforeend', h.chips(t.name));
    p.appendChild(h.numField('bore (mm)', t.id_mm, { min: 1 }, v => { if (v > 0) h.run({ op: 'set_tube', tube: t.name, id_mm: v }); }));
    p.appendChild(h.numField('length (m)', t.length_m, { min: 0.01 }, v => { if (v > 0) h.run({ op: 'set_tube', tube: t.name, length_m: v }); }));
    p.appendChild(h.numField('fittings', t.fittings, { min: 0, step: 1 }, v => { if (v != null) h.run({ op: 'set_tube', tube: t.name, fittings: v }); }));
    p.appendChild(h.numField('C (L/(s·bar))', t.C ?? '', { placeholder: 'estimated from bore and length' }, v => h.run({ op: 'set_tube', tube: t.name, C: v })));
    const rm = el('button', { class: 'small danger' }, 'Remove tube'); rm.onclick = () => h.run({ op: 'remove_tube', tube: t.name }).then(r => { if (r && r.ok) h.pick(null); });
    p.appendChild(rm);
    return p;
  }
}
