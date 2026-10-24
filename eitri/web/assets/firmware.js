// The firmware editor: the pinout the kernel drew, the pinout table (edit a pin's board pin, direction, role and name; add or
// remove pins; switch the board), the sources and the generated eitri_pins.h as read-only code panes, and the compile result.
// Built on domain_editor.js; the side panel's checks card lists the check results.
import { createDiagramEditor, el, esc } from './domain_editor.js';

export function createEditor(host, api) {
  let showFile = null;
  const spec = {
    kind: 'firmware', dataKey: 'firmware', svgUrl: 'domain.svg',
    hint: () => 'click a pin to edit it · the sources are read-only (Claude writes them) · scroll to zoom',
    downloads: [{ fmt: 'pinout', label: 'pinout', title: 'Pinout table (CSV)' }, { fmt: 'header', label: 'eitri_pins.h', title: 'The generated pin header' }, { fmt: 'log', label: 'compile log', title: 'arduino-cli output' }],
    selectEdit: e => e.net ? { kind: 'pin', id: e.net } : null,
    inspector(sel, data, state, h) {
      if (sel.kind === 'pin') return pinPanel(sel.id, data, h);
      return null;
    },
    summary(data, state, h) { return mainPanel(data, h); },
  };
  return createDiagramEditor(host, api, spec);

  function findPin(data, id) { return (data.pins || []).find(p => p.canonical === id || p.pin === id || p.net === id); }

  function pinPanel(id, data, h) {
    const p = findPin(data, id); if (!p) return null;
    const panel = el('div', { class: 'hp' });
    panel.appendChild(h.head(`Pin ${p.canonical || p.pin}`, `${p.symbol}` + (p.number != null ? ` = ${p.number}` : '') + (p.peripheral ? ` · ${p.peripheral}` : '')));
    panel.insertAdjacentHTML('beforeend', h.chips(p.canonical || p.pin));
    const pinSel = h.select(p.canonical || p.pin, (data.board_pins || []).filter(b => !b.forbidden).map(b => [b.name, b.name + (b.pwm ? ' · pwm' : '') + (b.analog_in ? ' · adc' : '') + (b.digital_out ? '' : ' · in only')]));
    pinSel.onchange = () => h.run({ op: 'set_pin', net: p.net, pin: pinSel.value });
    const dir = h.select(p.direction, [['out', 'out'], ['in', 'in'], ['inout', 'inout']]); dir.onchange = () => h.run({ op: 'set_pin', net: p.net, direction: dir.value });
    const role = h.select(p.role, (data.roles || []).map(r => [r, r])); role.onchange = () => h.run({ op: 'set_pin', net: p.net, role: role.value });
    const net = h.input(p.net); h.commitOn(net, v => { v = v.trim(); if (v && v !== p.net) h.run({ op: 'set_pin', net: p.net, rename: v }).then(r => { if (r && r.ok) h.pick({ kind: 'pin', id: p.canonical || p.pin }); }); });
    const pull = el('input', { type: 'checkbox' }); pull.checked = !!p.pullup; pull.onchange = () => h.run({ op: 'set_pin', net: p.net, pullup: pull.checked });
    panel.append(h.field('board pin', pinSel), h.field('direction', dir), h.field('role', role), h.field('name (PIN_…)', net), h.field('pull-up', pull));
    panel.appendChild(el('div', { class: 'hsub' }, p.uses && p.uses.length ? 'used: ' + p.uses.map(esc).join(', ') : 'not used in the sources'));
    const rm = el('button', { class: 'small danger' }, 'Remove pin'); rm.onclick = () => h.run({ op: 'remove_pin', net: p.net }).then(r => { if (r && r.ok) h.pick(null); });
    panel.appendChild(rm);
    return panel;
  }

  function mainPanel(data, h) {
    const panel = el('div', { class: 'hp' });
    panel.appendChild(el('div', { class: 'hh' }, `<div><b>Pinout</b><div class="hsub">${esc(data.board_name || data.board)} · ${esc(data.fqbn || '')}</div></div>`));
    const board = h.select(data.board, (data.boards || ['uno', 'nano', 'esp32']).map(b => [b, b])); board.onchange = () => h.run({ op: 'set_board', board: board.value });
    panel.appendChild(h.field('board', board));
    const t = el('div', { class: 'hpins fw' }, '<div class="hph fw"><span>pin</span><span>name</span><span>role</span><span></span></div>');
    for (const p of data.pins || []) {
      const r = el('div', { class: 'hpr fw' });
      r.append(el('b', { class: p.status === 'fail' ? 'bad' : '' }, esc(p.canonical || p.pin)), el('span', {}, esc(p.symbol)), el('span', { class: 'hsub' }, esc(`${p.direction} ${p.role}`)), el('span', { class: 'hdrop ' + (p.status || '') }, p.status === 'pass' ? '✓' : p.status === 'fail' ? '✗' : p.status === 'warn' ? '!' : ''));
      r.style.cursor = 'pointer'; r.onclick = () => h.pick({ kind: 'pin', id: p.canonical || p.pin });
      t.appendChild(r);
    }
    panel.appendChild(t);
    const add = el('button', { class: 'small' }, '+ pin');
    add.onclick = () => {
      const used = new Set((data.pins || []).map(p => p.canonical));
      const free = (data.board_pins || []).find(b => !b.forbidden && b.digital_out && !used.has(b.name));
      const name = (prompt('Name of the new pin (the code uses PIN_<name>):') || '').trim(); if (!name) return;
      h.run({ op: 'add_pin', pin: free ? free.name : 'D4', net: name, direction: 'out', role: 'digital_out' });
    };
    panel.appendChild(add);
    const c = data.compile || {};
    panel.appendChild(el('div', { class: 'hsub' }, '<b>Compile</b> ' + (c.available === false ? esc(c.message) : c.ok ? `ok · flash ${c.flash_pct ?? '?'}% · RAM ${c.ram_pct ?? '?'}%` : c.available ? `${(c.errors || []).length} error(s)` : 'not compiled yet')));
    for (const e of (c.errors || []).slice(0, 5)) panel.appendChild(el('div', { class: 'hc fail' }, `<b>ERROR</b> ${esc(e.file)}:${e.line}<br><span>${esc(e.msg)}</span>`));
    // the code panes (read-only)
    const tabs = el('div', { class: 'hrow' });
    const files = data.sources || [];
    if (!showFile || !files.find(f => f.name === showFile)) showFile = files.length ? files[0].name : null;
    for (const f of files) { const b = el('button', { class: 'small' + (f.name === showFile ? ' on' : '') }, esc(f.name)); b.onclick = () => { showFile = f.name; h.pick(null); }; tabs.appendChild(b); }
    if (data.compile_log) { const b = el('button', { class: 'small' + (showFile === '__log' ? ' on' : '') }, 'log'); b.onclick = () => { showFile = '__log'; h.pick(null); }; tabs.appendChild(b); }
    panel.appendChild(tabs);
    const cur = files.find(f => f.name === showFile);
    const pre = el('pre', { class: 'hcode', 'data-file': showFile || '' });
    pre.textContent = showFile === '__log' ? data.compile_log : cur ? cur.code : '';
    panel.appendChild(pre);
    if (cur && cur.generated) panel.appendChild(el('div', { class: 'hsub' }, 'generated from the pinout on every build'));
    return panel;
  }
}
