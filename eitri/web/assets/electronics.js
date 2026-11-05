// The electronics editor: the schematic the kernel drew, a parts table and a nets table (the summary panel), and an inspector per
// part (its values, its pins and the nets they are on: move a pin to another net or open it) and per net (rename, its pins, I2C
// settings on an SDA net). Parts can be dragged (LAYOUT hints). Built on domain_editor.js; edits go to the model file's EDITS list.
import { createDiagramEditor, el, esc } from './domain_editor.js';

const FIELDS = {
  resistor: [['ohm', 'resistance (Ω)'], ['power_W', 'rating (W)']],
  led: [['vf', 'forward voltage (V)'], ['if_max', 'max current (A)'], ['if_rated', 'rated current (A)']],
  capacitor: [['farad', 'capacitance (F)'], ['v_rated', 'voltage rating (V)']],
  supply: [['volts', 'voltage (V)'], ['current_A', 'rating (A)']],
  motor: [['volts', 'voltage (V)'], ['current_A', 'current (A)']],
  solenoid: [['volts', 'voltage (V)'], ['current_A', 'current (A)']],
  load: [['current_A', 'current (A)']],
  servo: [['current_A', 'draw (A)']],
};
const COLORS = ['red', 'green', 'blue', 'white'];
const amps = a => a == null ? '' : a >= 1 ? `${+a.toPrecision(3)} A` : a >= 1e-3 ? `${+(a * 1e3).toPrecision(3)} mA` : `${+(a * 1e6).toPrecision(3)} µA`;

export function createEditor(host, api) {
  const spec = {
    kind: 'electronics', dataKey: 'electronics', svgUrl: 'domain.svg', draggable: new Set(['part']),
    hint: () => 'click a part or a wire to edit it · drag parts to place them · scroll to zoom · drag the background to pan',
    downloads: [{ fmt: 'bom', label: 'BOM', title: 'Bill of materials (CSV)' }, { fmt: 'netlist', label: 'netlist', title: 'Every net and its pins (CSV)' },
      { fmt: 'sources', label: 'sources', title: 'Where every number the checks used comes from' }, { fmt: 'svg', label: 'SVG', title: 'The schematic as SVG' }],
    selectEdit: e => e.part ? { kind: 'part', id: e.part } : e.net ? { kind: 'net', id: e.net } : e.name && e.op === 'add_part' ? { kind: 'part', id: e.name } : null,
    inspector(sel, data, state, h) {
      if (sel.kind === 'part') return partPanel(sel.id, data, state, h);
      if (sel.kind === 'net') return netPanel(sel.id, data, state, h);
      return null;
    },
    summary(data, state, h) { return mainPanel(data, state, h); },
  };
  return createDiagramEditor(host, api, spec);

  function verdict(state, id) {
    const cs = ((state && state.checks) || []).filter(c => (c.refs || []).includes(id));
    return cs.some(c => c.severity === 'fail' || c.ok === false) ? 'bad' : cs.some(c => c.severity === 'warn') ? 'warn' : '';
  }

  function mainPanel(data, state, h) {
    const p = el('div', { class: 'hp' });
    p.appendChild(el('div', { class: 'hh' }, `<div><b>Circuit</b><div class="hsub">${(data.parts || []).length} parts · ${(data.nets || []).length} nets · click a row or the schematic to edit</div></div>`));
    p.appendChild(el('h4', { class: 'hsec' }, 'Parts'));
    const parts = el('div', { class: 'hnets', 'data-table': 'parts' });
    parts.appendChild(el('div', { class: 'hnh elec' }, '<span>ref</span><span>part</span><span>current</span><span></span>'));
    for (const q of data.parts || []) {
      const v = verdict(state, q.name);
      const r = el('div', { class: 'hnr elec', 'data-part': q.name });
      r.append(el('b', { class: v }, esc(q.name)), el('span', { class: 'hsub' }, esc(`${q.kind} ${q.value}`)), el('span', { class: 'hsub' }, esc(amps(q.current_A))),
        el('span', { class: 'hdrop ' + (v === 'bad' ? 'fail' : v) }, v === 'bad' ? '✗' : v === 'warn' ? '!' : ''));
      r.style.cursor = 'pointer'; r.onclick = () => h.pick({ kind: 'part', id: q.name });
      parts.appendChild(r);
    }
    p.appendChild(parts);
    p.appendChild(el('h4', { class: 'hsec' }, 'Nets'));
    const nets = el('div', { class: 'hnets', 'data-table': 'nets' });
    nets.appendChild(el('div', { class: 'hnh elec' }, '<span>net</span><span>pins</span><span>kind</span><span></span>'));
    for (const n of data.nets || []) {
      const v = verdict(state, n.name);
      const r = el('div', { class: 'hnr elec', 'data-net': n.name });
      r.append(el('b', { class: v }, esc(n.name)), el('span', { class: 'hsub' }, esc(n.pins.join(' '))), el('span', { class: 'hsub' }, esc(n.kind + (n.volts != null && n.kind === 'rail' ? ` ${n.volts} V` : ''))),
        el('span', { class: 'hdrop ' + (v === 'bad' ? 'fail' : v) }, v === 'bad' ? '✗' : v === 'warn' ? '!' : ''));
      r.style.cursor = 'pointer'; r.onclick = () => h.pick({ kind: 'net', id: n.name });
      nets.appendChild(r);
    }
    p.appendChild(nets);
    const row = el('div', { class: 'hrow' });
    for (const [kind, label, extra] of [['resistor', '+ resistor', { ohm: 1000 }], ['led', '+ LED', { color: 'red' }], ['diode', '+ diode', { part: '1N4007' }], ['capacitor', '+ capacitor', { farad: 1e-7 }], ['switch', '+ switch', {}]]) {
      const b = el('button', { class: 'small' }, label);
      b.onclick = () => {
        const base = { resistor: 'R', led: 'LED', diode: 'D', capacitor: 'C', switch: 'SW' }[kind];
        let i = 1; const names = new Set((data.parts || []).map(q => q.name)); while (names.has(base + i)) i++;
        h.run({ op: 'add_part', kind, name: base + i, ...extra }).then(r => { if (r && r.ok) h.pick({ kind: 'part', id: base + i }); });
      };
      row.appendChild(b);
    }
    p.appendChild(row);
    return p;
  }

  function netSelect(data, value, h) {
    const opts = [['', '— open —']].concat((data.nets || []).map(n => [n.name, n.name + (n.kind === 'rail' ? ` (${n.volts} V)` : n.kind === 'gnd' ? ' (ground)' : '')])).concat([['__new', 'new net…']]);
    return h.select(value || '', opts);
  }

  function partPanel(name, data, state, h) {
    const q = (data.parts || []).find(x => x.name === name); if (!q) return null;
    const p = el('div', { class: 'hp' });
    p.appendChild(h.head(`${q.name} · ${q.kind}`, `${q.value}${q.title && q.title !== q.kind ? ' · ' + q.title : ''}${q.current_A ? ' · ' + amps(q.current_A) : ''}`));
    p.insertAdjacentHTML('beforeend', h.chips(q.name));
    const set = (k, v) => h.run({ op: 'set_part', part: q.name, [k]: v });
    for (const [k, label] of FIELDS[q.kind] || []) p.appendChild(h.numField(label, q.spec[k] ?? '', { min: 0 }, v => { if (v > 0) set(k, v); }));
    if (q.kind === 'led') { const c = h.select(q.spec.color || 'red', COLORS.map(x => [x, x])); c.onchange = () => set('color', c.value); p.appendChild(h.field('colour (typical part)', c)); }
    if (q.kind === 'module' && q.spec.address != null) {
      const a = h.input('0x' + Number(q.spec.address).toString(16)); h.commitOn(a, v => set('address', v.trim())); p.appendChild(h.field('I2C address', a));
    }
    const pn = h.input(q.part_number || '', { placeholder: 'part number' }); h.commitOn(pn, v => set('part_number', v)); p.appendChild(h.field('part no.', pn));
    const nm = h.input(q.name); h.commitOn(nm, v => { v = v.trim(); if (v && v !== q.name) h.run({ op: 'set_part', part: q.name, rename: v }).then(r => { if (r && r.ok) h.pick({ kind: 'part', id: v }); }); });
    p.appendChild(h.field('name', nm));
    p.appendChild(el('h4', { class: 'hsec' }, 'Pins'));
    const shown = q.kind === 'board' || q.kind === 'module' ? q.pins.filter(x => x.net || x.required) : q.pins;
    for (const pin of shown) {
      const sel = netSelect(data, pin.net, h);
      sel.onchange = () => {
        const ref = `${q.name}.${pin.name}`;
        if (!sel.value) { h.run({ op: 'disconnect', pin: ref }); return; }
        let net = sel.value;
        if (net === '__new') { net = (prompt('Name of the new net:') || '').trim(); if (!net) { sel.value = pin.net || ''; return; } }
        h.run({ op: 'connect', pin: ref, net });
      };
      p.appendChild(h.field(`${pin.name}${pin.mode ? ' (' + pin.mode + ')' : ''}`, sel));
      if (pin.mode) {
        const m = h.select(pin.mode, [['io', 'io (either)'], ['out', 'output'], ['in', 'input'], ['in+pu', 'input, pull-up']]);
        m.onchange = () => h.run({ op: 'set_gpio', pin: `${q.name}.${pin.name}`, mode: m.value === 'in+pu' ? 'in' : m.value, pullup: m.value === 'in+pu' });
        p.appendChild(h.field(`${pin.name} mode`, m));
      }
    }
    if (q.kind === 'board' || q.kind === 'module') {
      const open = q.pins.filter(x => !x.net && !x.required);
      if (open.length) {
        const add = h.select('', [['', `connect another pin (${open.length} open)…`]].concat(open.map(x => [x.name, x.name + (x.roles.length ? ' · ' + x.roles.join(', ') : '')])));
        add.onchange = () => { if (!add.value) return; const net = (prompt(`Net for ${q.name}.${add.value} (an existing net or a new name):`) || '').trim(); if (net) h.run({ op: 'connect', pin: `${q.name}.${add.value}`, net }); };
        p.appendChild(add);
      }
    }
    if (['resistor', 'led', 'diode', 'capacitor', 'switch'].includes(q.kind)) {
      const rm = el('button', { class: 'small danger' }, 'Remove part'); rm.onclick = () => h.run({ op: 'remove_part', part: q.name }).then(r => { if (r && r.ok) h.pick(null); });
      p.appendChild(rm);
    }
    return p;
  }

  function netPanel(name, data, state, h) {
    const n = (data.nets || []).find(x => x.name === name); if (!n) return null;
    const p = el('div', { class: 'hp' });
    p.appendChild(h.head(`Net ${n.name}`, `${n.kind}${n.volts != null ? ' · ' + n.volts + ' V' : ''} · ${n.pins.length} pins`));
    p.insertAdjacentHTML('beforeend', h.chips(n.name));
    const nm = h.input(n.name); h.commitOn(nm, v => { v = v.trim(); if (v && v !== n.name) h.run({ op: 'set_net', net: n.name, rename: v }).then(r => { if (r && r.ok) h.pick({ kind: 'net', id: v }); }); });
    p.appendChild(h.field('name', nm));
    const bus = (data.buses || []).find(b => b.sda === n.name);
    if (bus) {
      const sp = h.select(String(bus.speed_hz), [['100000', '100 kHz (Standard-mode)'], ['400000', '400 kHz (Fast-mode)'], ['1000000', '1 MHz (Fast-mode Plus)']]);
      sp.onchange = () => h.run({ op: 'set_net', net: n.name, speed_hz: Number(sp.value) });
      p.appendChild(h.field('I2C clock', sp));
      p.appendChild(h.numField('bus capacitance (pF)', '', { placeholder: 'estimated' }, v => h.run({ op: 'set_net', net: n.name, capacitance_pF: v })));
      p.appendChild(el('div', { class: 'hsub' }, 'devices: ' + bus.devices.map(esc).join(', ')));
    }
    p.appendChild(el('h4', { class: 'hsec' }, 'Pins'));
    for (const ref of n.pins) {
      const r = el('div', { class: 'hrow' });
      r.appendChild(el('span', {}, esc(ref)));
      const x = el('button', { class: 'small', title: 'Take this pin off the net' }, 'open'); x.onclick = () => h.run({ op: 'disconnect', pin: ref });
      r.appendChild(x);
      p.appendChild(r);
    }
    return p;
  }
}
