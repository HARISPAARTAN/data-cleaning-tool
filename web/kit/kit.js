// portfolio-kit kit.js: storage, filter bus, formatters, CSV, sorting, renderers, tabs, device frames and the flow player. ES module, no dependencies.
export { createFlowState } from './flow-state.js';

const DASH = '\u2013';
const MINUS = '\u2212';
const NBSP = '\u00A0';

export function createStore(ns, { version = 1, storage } = {}) {
  if (typeof ns !== 'string' || ns === '') throw new TypeError('createStore: ns must be a non-empty string');
  const key = `pf.${ns}.v${version}`;
  let backend = null;
  try {
    backend = storage ?? globalThis.localStorage ?? null;
    backend?.setItem(`${key}.probe`, '1');
    backend?.removeItem(`${key}.probe`);
  } catch {
    backend = null;
  }
  let mem = null;
  let inMemory = !backend;
  const clear = () => {
    mem = null;
    inMemory = !backend;
    try {
      backend?.removeItem(key);
    } catch {
      /* nothing to remove */
    }
  };
  return {
    key,
    available: Boolean(backend),
    load() {
      try {
        const raw = inMemory ? mem : backend.getItem(key);
        return raw === null || raw === undefined ? null : JSON.parse(raw);
      } catch {
        return null;
      }
    },
    save(obj) {
      let text;
      try {
        text = JSON.stringify(obj);
      } catch {
        return false;
      }
      if (text === undefined) {
        clear();
        return !inMemory;
      }
      if (backend) {
        try {
          backend.setItem(key, text);
          inMemory = false;
          mem = null;
          return true;
        } catch {
          /* full or blocked: keep the value in memory */
        }
      }
      mem = text;
      inMemory = true;
      return false;
    },
    clear,
  };
}

const same = (a, b) => a === b || (typeof a === 'object' && typeof b === 'object' && a !== null && b !== null && JSON.stringify(a) === JSON.stringify(b));

const isPlain = (v) => v !== null && typeof v === 'object' && [Object.prototype, null].includes(Object.getPrototypeOf(v));
const keep = (v) => (Array.isArray(v) ? Object.freeze([...v]) : isPlain(v) ? Object.freeze({ ...v }) : v);

export function createFilterBus() {
  let state = Object.freeze({});
  const subs = new Set();
  const emit = (change) => {
    for (const fn of [...subs]) fn(state, change);
  };
  const put = (dim, input) => {
    const value = keep(input);
    const has = Object.hasOwn(state, dim);
    if (value === null || value === undefined ? !has : has && same(state[dim], value)) return;
    const next = { ...state };
    if (value === null || value === undefined) delete next[dim];
    else next[dim] = value;
    state = Object.freeze(next);
    emit({ dim, value: value ?? null });
  };
  return {
    state: () => state,
    get: (dim) => (Object.hasOwn(state, dim) ? state[dim] : null),
    set: put,
    toggle: (dim, value) => put(dim, Object.hasOwn(state, dim) && same(state[dim], value) ? null : value),
    clear(dim) {
      if (dim !== undefined) return put(dim, null);
      if (Object.keys(state).length === 0) return undefined;
      state = Object.freeze({});
      return emit({ dim: null, value: null });
    },
    subscribe(fn) {
      subs.add(fn);
      return () => {
        subs.delete(fn);
      };
    },
  };
}

const formats = new Map();
const nf = (opts) => {
  const k = JSON.stringify(opts);
  if (!formats.has(k)) formats.set(k, new Intl.NumberFormat('en-GB', opts));
  return formats.get(k);
};
const toNumber = (v) => {
  if (v === null || v === undefined || typeof v === 'boolean' || (typeof v === 'string' && v.trim() === '')) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};
const num = (v, opts, tail = (s) => s) => {
  const n = toNumber(v);
  return n === null ? DASH : tail(nf(opts).format(n).replace('-', MINUS));
};
const COMPACT = [[1e12, 't'], [1e9, 'b'], [1e6, 'm'], [1e3, 'k']];
const fixed = (digits, signDisplay = 'negative') => ({ minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay });
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad2 = (n) => String(n).padStart(2, '0');
function wallClock(v) {
  let p;
  if (v instanceof Date) {
    if (Number.isNaN(v.getTime())) return null;
    p = [v.getUTCFullYear(), v.getUTCMonth() + 1, v.getUTCDate(), v.getUTCHours(), v.getUTCMinutes()];
  } else {
    const m = typeof v === 'string' ? /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/.exec(v) : null;
    if (!m) return null;
    p = m.slice(1).map((x) => Number(x ?? 0));
  }
  const [y, mo, d, hh, mi] = p;
  const probe = new Date(Date.UTC(y, mo - 1, d));
  return probe.getUTCMonth() === mo - 1 && probe.getUTCDate() === d && hh < 24 && mi < 60 ? p : null;
}

export const fmt = {
  int: (n) => num(n, fixed(0)),
  dec: (n, digits = 1) => num(n, fixed(digits)),
  money: (n, currency = 'EUR', digits = 0) => num(n, { style: 'currency', currency, ...fixed(digits) }),
  compact: (n) => {
    const v = toNumber(n);
    if (v === null) return DASH;
    let i = COMPACT.findIndex(([limit]) => Math.abs(v) >= limit);
    if (i < 0) return num(v, { maximumFractionDigits: 1, signDisplay: 'negative' });
    let text = nf({ maximumFractionDigits: 1, signDisplay: 'negative', useGrouping: false }).format(v / COMPACT[i][0]);
    if (Math.abs(Number(text)) >= 1000 && i > 0) {
      i -= 1;
      text = nf({ maximumFractionDigits: 1, signDisplay: 'negative', useGrouping: false }).format(v / COMPACT[i][0]);
    }
    return text.replace('-', MINUS) + COMPACT[i][1];
  },
  pct: (x, digits = 1) => num(x, { style: 'percent', ...fixed(digits) }, (s) => s.replace('%', `${NBSP}%`)),
  pp: (x, digits = 1) => num(x, { style: 'percent', ...fixed(digits, 'exceptZero') }, (s) => s.replace('%', `${NBSP}pp`)),
  signed: (n, digits = 0) => num(n, fixed(digits, 'exceptZero')),
  date(iso) {
    const p = wallClock(iso);
    return p ? `${pad2(p[2])} ${MONTHS[p[1] - 1]} ${p[0]}` : DASH;
  },
  dateTime(iso) {
    const p = wallClock(iso);
    return p ? `${pad2(p[2])} ${MONTHS[p[1] - 1]} ${p[0]} ${pad2(p[3])}:${pad2(p[4])}` : DASH;
  },
};

export function toCsv(rows, columns, { delimiter = ',', bom = true, escapeFormulas = true } = {}) {
  const cell = (v) => {
    let s = v === null || v === undefined ? '' : v instanceof Date ? v.toISOString() : String(v);
    if (escapeFormulas && typeof v !== 'number' && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
    return s.includes(delimiter) || /["\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [columns.map((c) => cell(c.label ?? c.key)), ...rows.map((row) => columns.map((c) => cell(row[c.key])))];
  return `${bom ? '\uFEFF' : ''}${lines.map((l) => l.join(delimiter)).join('\r\n')}\r\n`;
}

const collator = new Intl.Collator('en-GB', { numeric: true, sensitivity: 'base' });
const sortValue = (v) => (v instanceof Date ? v.getTime() : typeof v === 'boolean' ? Number(v) : v);
const isBlank = (v) => v === null || v === undefined || v === '' || (typeof v === 'number' && Number.isNaN(v));

export function sortRows(rows, key, dir = 'asc') {
  const sign = dir === 'desc' ? -1 : 1;
  return rows
    .map((row, i) => ({ row, i, v: sortValue(row[key]) }))
    .sort((a, b) => {
      const ba = isBlank(a.v);
      const bb = isBlank(b.v);
      if (ba || bb) return ba === bb ? a.i - b.i : ba ? 1 : -1;
      const c = typeof a.v === 'number' && typeof b.v === 'number' ? a.v - b.v : collator.compare(String(a.v), String(b.v));
      return sign * Math.sign(c) || a.i - b.i;
    })
    .map((e) => e.row);
}

const SVG_NS = 'http://www.w3.org/2000/svg';
const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
const r1 = (n) => Math.round(n * 10) / 10;

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = String(v);
    else if (k === 'onclick') el.addEventListener('click', v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  el.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return el;
}

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

let bannerObserver = null;

export function mountBanner({ repoUrl, text = 'Demo with invented data for a fictional company' } = {}) {
  document.querySelector('body > .pf-banner')?.remove();
  bannerObserver?.disconnect();
  const el = h('aside', { class: 'pf-banner', 'aria-label': 'Demo notice' }, h('span', { text }), repoUrl ? h('a', { href: repoUrl, text: 'Source code' }) : null);
  document.body.prepend(el);
  document.body.classList.add('pf-has-banner');
  if (typeof ResizeObserver === 'function') {
    bannerObserver = new ResizeObserver(() => document.documentElement.style.setProperty('--pf-banner-h', `${el.offsetHeight}px`));
    bannerObserver.observe(el);
  }
  return el;
}

const toggles = new WeakMap();

export function mountThemeToggle(el) {
  toggles.get(el)?.abort();
  const ac = new AbortController();
  toggles.set(el, ac);
  const theme = globalThis.pfTheme;
  const label = h('span');
  const btn = h('button', { type: 'button', class: 'pf-btn pf-btn--ghost pf-btn--sm pf-theme-toggle' }, h('span', { 'aria-hidden': 'true', text: '◐' }), h('span', { class: 'pf-visually-hidden', text: 'Theme: ' }), label);
  const sync = () => {
    label.textContent = theme?.get() === 'light' ? 'Light' : 'Dark';
  };
  if (theme) btn.addEventListener('click', () => theme.toggle());
  else btn.disabled = true;
  document.addEventListener('pf:theme', sync, { signal: ac.signal });
  sync();
  el.replaceChildren(btn);
  return btn;
}

const MARKS = { up: '▲', down: '▼', neutral: '●' };

export function renderKpi(el, data) {
  const update = ({ label = '', value, delta, caption } = {}) => {
    const kind = MARKS[delta?.kind] ? delta.kind : 'neutral';
    el.replaceChildren(
      h(
        'div',
        { class: 'pf-kpi' },
        h('div', { class: 'pf-kpi__label pf-label', text: label }),
        h('div', { class: 'pf-kpi__value', text: value ?? DASH }),
        delta ? h('div', { class: `pf-kpi__delta pf-kpi__delta--${kind}` }, h('span', { 'aria-hidden': 'true', text: `${MARKS[kind]} ` }), String(delta.text ?? '')) : null,
        caption ? h('div', { class: 'pf-kpi__caption', text: caption }) : null,
      ),
    );
  };
  update(data);
  return { update };
}

const PILL_KINDS = new Set(['ok', 'warn', 'danger', 'info', 'muted', 'accent']);

export const pill = (text, kind = 'muted') => h('span', { class: `pf-pill pf-pill--${PILL_KINDS.has(kind) ? kind : 'muted'}`, text });

export function renderTable(el, { columns, rows = [], pageSize = 0, caption = '', emptyText = 'No rows to show' }) {
  let data = [...rows];
  let view = data;
  let sortKey = null;
  let sortDir = 'asc';
  let page = 0;
  const alignClass = (c) => (c.align === 'right' ? 'pf-num' : null);
  const headRow = h('tr');
  const body = h('tbody');
  const wrap = h('div', { class: 'pf-table-wrap', tabindex: 0, role: 'region', 'aria-label': caption || 'Table' }, h('table', { class: 'pf-table' }, caption ? h('caption', { text: caption }) : null, h('thead', {}, headRow), body));
  const pages = () => (pageSize > 0 ? Math.max(1, Math.ceil(view.length / pageSize)) : 1);
  const go = (p) => {
    page = Math.min(Math.max(0, p), pages() - 1);
    draw();
  };
  const info = h('span', { class: 'pf-pager__info', 'aria-live': 'polite' });
  const prev = h('button', { type: 'button', class: 'pf-btn pf-btn--ghost pf-btn--sm', onclick: () => go(page - 1) }, 'Previous');
  const next = h('button', { type: 'button', class: 'pf-btn pf-btn--ghost pf-btn--sm', onclick: () => go(page + 1) }, 'Next');
  const pager = pageSize > 0 ? h('div', { class: 'pf-pager' }, info, prev, next) : null;
  const api = {
    update(list) {
      data = [...list];
      view = sortKey ? sortRows(data, sortKey, sortDir) : data;
      page = 0;
      draw();
    },
    sort(key, dir = 'asc') {
      sortKey = key;
      sortDir = dir === 'desc' ? 'desc' : 'asc';
      view = sortRows(data, key, sortDir);
      page = 0;
      draw();
    },
    rows: () => [...view],
  };
  const heads = columns.map((c) => {
    const label = c.label ?? c.key;
    const th = h('th', { scope: 'col', class: alignClass(c), 'data-key': c.key }, c.sortable === false ? label : h('button', { type: 'button', class: 'pf-table__sort', onclick: () => api.sort(c.key, sortKey === c.key && sortDir === 'asc' ? 'desc' : 'asc') }, label));
    headRow.append(th);
    return th;
  });
  const cell = (c, row) => {
    const out = c.format ? c.format(row[c.key], row) : row[c.key];
    const td = h('td', { class: alignClass(c) });
    if (out !== null && typeof out === 'object' && 'nodeType' in out) td.append(out);
    else td.textContent = out === null || out === undefined ? DASH : String(out);
    return td;
  };
  function draw() {
    for (const th of heads) {
      if (th.dataset.key === sortKey) th.setAttribute('aria-sort', sortDir === 'asc' ? 'ascending' : 'descending');
      else th.removeAttribute('aria-sort');
    }
    const start = pageSize > 0 ? page * pageSize : 0;
    const shown = pageSize > 0 ? view.slice(start, start + pageSize) : view;
    body.replaceChildren(...(shown.length ? shown.map((row) => h('tr', {}, ...columns.map((c) => cell(c, row)))) : [h('tr', {}, h('td', { class: 'pf-table__empty', colspan: columns.length, text: emptyText }))]));
    if (!pager) return;
    info.textContent = view.length ? `${fmt.int(start + 1)}${DASH}${fmt.int(start + shown.length)} of ${fmt.int(view.length)}` : '0 of 0';
    prev.setAttribute('aria-disabled', String(page === 0));
    next.setAttribute('aria-disabled', String(page >= pages() - 1));
  }
  el.replaceChildren(wrap, ...(pager ? [pager] : []));
  draw();
  return api;
}

export function download(filename, text, mime = 'text/csv;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = h('a', { href: url, download: filename, hidden: true });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const fitters = new WeakMap();

export function fitFrame(frame) {
  fitters.get(frame)?.();
  const box = frame.parentElement;
  if (!box) return () => {};
  const fit = () => {
    const f = getComputedStyle(frame);
    const b = getComputedStyle(box);
    const inner = parseFloat(f.getPropertyValue('--w')) + 2 * parseFloat(f.getPropertyValue('--b'));
    const room = box.clientWidth - parseFloat(b.paddingLeft) - parseFloat(b.paddingRight) - parseFloat(f.borderLeftWidth) - parseFloat(f.borderRightWidth);
    const scale = Math.floor(Math.min(1, room / inner) * 1000) / 1000;
    if (Number.isFinite(scale) && scale > 0) frame.style.setProperty('--scale', String(scale));
  };
  let raf = 0;
  const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(fit);
  }) : null;
  ro?.observe(box);
  fit();
  const stop = () => {
    cancelAnimationFrame(raf);
    ro?.disconnect();
    fitters.delete(frame);
  };
  fitters.set(frame, stop);
  return stop;
}

const tabsets = new WeakMap();

export function initTabs(list, { onChange } = {}) {
  tabsets.get(list)?.abort();
  const ac = new AbortController();
  tabsets.set(list, ac);
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  const select = (ref, focus = false, notify = true) => {
    const tab = typeof ref === 'number' ? tabs[ref] : typeof ref === 'string' ? tabs.find((t) => t.id === ref) : ref;
    if (!tab) return;
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      const panel = document.getElementById(t.getAttribute('aria-controls') ?? '');
      if (panel) panel.hidden = !on;
    }
    if (focus) tab.focus();
    if (notify) onChange?.(tab.id, tab);
  };
  list.addEventListener('click', (e) => {
    const tab = e.target.closest('[role="tab"]');
    if (tabs.includes(tab)) select(tab);
  }, { signal: ac.signal });
  list.addEventListener('keydown', (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const i = tabs.indexOf(e.target);
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
    if (i < 0 || to === undefined) return;
    e.preventDefault();
    select(tabs[(to + tabs.length) % tabs.length], true);
  }, { signal: ac.signal });
  select(tabs.find((t) => t.getAttribute('aria-selected') === 'true') ?? tabs[0], false, false);
  return { select: (ref) => select(ref) };
}

const GLYPHS = { idle: '●', running: '●', ok: '✓', failed: '✕', skipped: DASH };
const WORDS = { idle: 'Waiting', running: 'Running', ok: 'Done', failed: 'Failed', skipped: 'Skipped' };
const KINDS = { idle: 'muted', running: 'accent', ok: 'ok', failed: 'danger', skipped: 'muted' };
const SUFFIX = { ok: ' (done)', failed: ' (failed)', skipped: ' (skipped)' };
const players = new WeakMap();
let flowCount = 0;

export class FlowPlayer {
  constructor(el, { state, orientation = 'auto', stepMs = 900, autoplay = false, controls = true } = {}) {
    if (!state || typeof state.subscribe !== 'function') throw new TypeError('FlowPlayer: pass { state } from createFlowState');
    players.get(el)?.destroy();
    players.set(el, this);
    flowCount += 1;
    Object.assign(this, { el, state, orientation, stepMs, uid: `pf-flow-${flowCount}`, timer: 0, raf: 0, rof: 0, width: 0, playing: false, quiet: false, last: state.current()?.id ?? null });
    this.diagram = h('div', { class: 'pf-flow__diagram' });
    this.list = h('ol', { class: 'pf-flow__steps', role: 'list' });
    this.live = h('p', { class: 'pf-visually-hidden', 'aria-live': 'polite' });
    this.playBtn = controls ? h('button', { type: 'button', class: 'pf-btn pf-btn--primary pf-btn--sm', onclick: () => (this.playing ? this.pause() : this.play()) }, 'Play') : null;
    const bar = controls ? h('div', { class: 'pf-flow__controls' }, this.playBtn, h('button', { type: 'button', class: 'pf-btn pf-btn--ghost pf-btn--sm', onclick: () => this.skipToEnd() }, 'Skip animation')) : null;
    this.items = new Map(state.steps().map((step, i) => {
      const badge = h('span');
      const detail = h('span', { class: 'pf-flow__detail' });
      const li = h('li', { class: 'pf-flow__step' }, h('span', { class: 'pf-flow__idx', 'aria-hidden': 'true', text: i + 1 }), h('span', { class: 'pf-flow__name', text: step.label }), badge, detail);
      return [step.id, { li, badge, detail }];
    }));
    this.list.append(...[...this.items.values()].map((item) => item.li));
    el.classList.add('pf-flow');
    el.replaceChildren(...[this.diagram, bar, this.list, this.live].filter(Boolean));
    this.layout();
    document.fonts?.ready.then(() => {
      if (players.get(el) === this) this.layout();
    });
    this.off = state.subscribe((_, change) => this.sync(change));
    if (typeof ResizeObserver === 'function') {
      this.ro = new ResizeObserver(() => {
        cancelAnimationFrame(this.rof);
        this.rof = requestAnimationFrame(() => {
          if (Math.abs(this.el.clientWidth - this.width) > 1) this.layout();
        });
      });
      this.ro.observe(el);
    }
    if (autoplay && !reducedMotion()) this.play();
  }

  layout() {
    cancelAnimationFrame(this.raf);
    this.width = this.el.clientWidth;
    const W = Math.max(280, Math.round(this.width || 720));
    const row = this.orientation === 'row' || (this.orientation !== 'column' && W >= 720);
    const steps = this.state.steps();
    const edges = this.state.edges();
    const depth = new Map(steps.map((st) => [st.id, 0]));
    for (let k = 0; k < steps.length; k += 1) {
      for (const [a, b] of edges) if (depth.get(a) + 1 > depth.get(b) && depth.get(a) + 1 < steps.length) depth.set(b, depth.get(a) + 1);
    }
    const layers = [];
    for (const st of steps) (layers[depth.get(st.id)] ??= []).push(st);
    const cols = layers.filter(Boolean);
    const lanes = Math.max(...cols.map((c) => c.length));
    const [PAD, GAP, BH] = [8, 28, 48];
    const across = row ? cols.length : lanes;
    const down = row ? lanes : cols.length;
    const bw = Math.max(72, Math.min(row ? 200 : 300, (W - 2 * PAD - (across - 1) * GAP) / across));
    const used = 2 * PAD + across * bw + (across - 1) * GAP;
    const vbW = Math.max(W, used);
    const off = (vbW - used) / 2;
    const pos = new Map();
    cols.forEach((col, ci) => col.forEach((st, li) => {
      const lane = li + (lanes - col.length) / 2;
      pos.set(st.id, row ? { x: off + PAD + ci * (bw + GAP), y: PAD + lane * (BH + GAP) } : { x: off + PAD + lane * (bw + GAP), y: PAD + ci * (BH + GAP) });
    }));
    const svg = svgEl('svg', { viewBox: `0 0 ${r1(vbW)} ${2 * PAD + down * BH + (down - 1) * GAP}`, 'aria-hidden': 'true', focusable: 'false' });
    const defs = svgEl('defs');
    const marker = svgEl('marker', { id: `${this.uid}-arrow`, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto' });
    marker.append(svgEl('path', { d: 'M0 0L10 5L0 10z', class: 'pf-flow__arrow' }));
    defs.append(marker);
    svg.append(defs);
    this.edgeEls = edges.map(([a, b]) => {
      const p = pos.get(a);
      const q = pos.get(b);
      const [x1, y1, x2, y2] = (row ? [p.x + bw, p.y + BH / 2, q.x, q.y + BH / 2] : [p.x + bw / 2, p.y + BH, q.x + bw / 2, q.y]).map(r1);
      const mx = r1((x1 + x2) / 2);
      const my = r1((y1 + y2) / 2);
      const d = row ? `M${x1} ${y1}C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}` : `M${x1} ${y1}C${x1} ${my} ${x2} ${my} ${x2} ${y2}`;
      const path = svgEl('path', { d, class: 'pf-flow__edge', 'marker-end': `url(#${this.uid}-arrow)` });
      svg.append(path);
      return { a, b, el: path };
    });
    this.nodes = new Map(steps.map((st) => {
      const { x, y } = pos.get(st.id);
      const g = svgEl('g', { class: 'pf-flow__node', 'data-id': st.id, transform: `translate(${r1(x)} ${r1(y)})` });
      const title = svgEl('title');
      title.textContent = st.label;
      const glyph = svgEl('text', { class: 'pf-flow__glyph', x: 16, y: BH / 2, 'text-anchor': 'middle' });
      const name = svgEl('text', { class: 'pf-flow__name', x: 30, y: BH / 2 });
      name.textContent = st.label;
      g.append(title, svgEl('rect', { width: r1(bw), height: BH, rx: 10 }), glyph, name);
      svg.append(g);
      return [st.id, { g, glyph, name, label: st.label }];
    }));
    this.dot = svgEl('circle', { class: 'pf-flow__dot', r: 5, cx: 0, cy: 0, visibility: 'hidden' });
    svg.append(this.dot);
    this.diagram.replaceChildren(svg);
    for (const { name, label } of this.nodes.values()) {
      let text = label;
      while (text.length > 3 && name.getComputedTextLength() > bw - 38) {
        text = text.slice(0, -1);
        name.textContent = `${text}…`;
      }
    }
    this.paint();
  }

  paint() {
    const st = this.state.statuses();
    const cur = this.state.current();
    for (const step of this.state.steps()) {
      const status = st[step.id];
      const node = this.nodes.get(step.id);
      node.g.setAttribute('data-status', status);
      node.glyph.textContent = GLYPHS[status];
      const item = this.items.get(step.id);
      item.li.setAttribute('data-status', status);
      item.badge.className = `pf-pill pf-pill--${KINDS[status]}`;
      item.badge.textContent = WORDS[status];
      item.detail.textContent = step.detail;
      if (cur?.id === step.id) item.li.setAttribute('aria-current', 'step');
      else item.li.removeAttribute('aria-current');
    }
    for (const e of this.edgeEls) e.el.toggleAttribute('data-done', st[e.a] === 'ok' && ['running', 'ok', 'failed'].includes(st[e.b]));
    if (this.playBtn) this.playBtn.textContent = this.playing ? 'Pause' : this.state.done() ? 'Replay' : 'Play';
  }

  sync(change) {
    this.paint();
    if (this.quiet) return;
    const steps = this.state.steps();
    const cur = this.state.current();
    const id = change.type === 'skip' ? change.id : cur?.id;
    const i = steps.findIndex((x) => x.id === id);
    this.live.textContent = i < 0 ? `Ready: ${steps.length} steps` : `Step ${i + 1} of ${steps.length}: ${steps[i].label}${SUFFIX[this.state.statuses()[id]] ?? ''}`;
    if (cur && this.last && cur.id !== this.last) this.animate(this.last, cur.id);
    this.last = cur?.id ?? null;
  }

  animate(from, to) {
    cancelAnimationFrame(this.raf);
    this.dot.setAttribute('visibility', 'hidden');
    const edge = this.edgeEls.find((e) => e.a === from && e.b === to);
    if (!edge || reducedMotion()) return;
    const len = edge.el.getTotalLength();
    const ms = Math.min(600, this.stepMs * 0.6);
    const t0 = performance.now();
    const tick = (now) => {
      const k = Math.min(1, Math.max(0, (now - t0) / ms));
      const p = edge.el.getPointAtLength(len * k);
      this.dot.setAttribute('cx', r1(p.x));
      this.dot.setAttribute('cy', r1(p.y));
      this.dot.setAttribute('visibility', k < 1 ? 'visible' : 'hidden');
      if (k < 1) this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  play() {
    if (reducedMotion()) {
      this.skipToEnd();
      return;
    }
    if (this.playing) return;
    if (this.state.done()) this.state.reset();
    this.playing = true;
    if (this.state.current()) this.paint();
    else this.state.start();
    const tick = () => {
      if (this.state.next()) this.timer = setTimeout(tick, this.stepMs);
      else this.pause();
    };
    this.timer = setTimeout(tick, this.stepMs);
  }

  pause() {
    clearTimeout(this.timer);
    this.timer = 0;
    this.playing = false;
    this.paint();
  }

  skipToEnd() {
    clearTimeout(this.timer);
    cancelAnimationFrame(this.raf);
    this.playing = false;
    this.quiet = true;
    try {
      if (!this.state.current()) this.state.start();
      while (this.state.next());
    } finally {
      this.quiet = false;
    }
    this.dot.setAttribute('visibility', 'hidden');
    this.last = this.state.current()?.id ?? null;
    this.sync({ type: 'next', id: this.last });
  }

  destroy() {
    clearTimeout(this.timer);
    cancelAnimationFrame(this.raf);
    cancelAnimationFrame(this.rof);
    this.playing = false;
    this.off?.();
    this.ro?.disconnect();
    this.el.replaceChildren();
    this.el.classList.remove('pf-flow');
    if (players.get(this.el) === this) players.delete(this.el);
  }
}
