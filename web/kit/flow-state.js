// portfolio-kit flow state: the pure state machine behind FlowPlayer (no DOM, unit-tested in Node).
export const STATUSES = Object.freeze(['idle', 'running', 'ok', 'failed', 'skipped']);
const FINAL = new Set(['ok', 'failed', 'skipped']);

export function createFlowState({ steps, edges } = {}) {
  if (!Array.isArray(steps) || steps.length === 0) throw new TypeError('createFlowState: steps must be a non-empty array');
  const list = steps.map((s, i) => {
    if (!s || typeof s.id !== 'string' || s.id === '') throw new TypeError(`createFlowState: step ${i} needs a non-empty string id`);
    return { id: s.id, label: s.label == null ? s.id : String(s.label), detail: s.detail == null ? '' : String(s.detail) };
  });
  const pos = new Map(list.map((s, i) => [s.id, i]));
  if (pos.size !== list.length) throw new TypeError('createFlowState: step ids must be unique');
  const links = (edges ?? list.slice(1).map((s, i) => [list[i].id, s.id])).map((e) => {
    if (!Array.isArray(e) || !pos.has(e[0]) || !pos.has(e[1])) throw new TypeError(`createFlowState: edge ${JSON.stringify(e)} names an unknown step`);
    return [e[0], e[1]];
  });
  const subs = new Set();
  let status;
  let detail;
  let cur;
  const init = () => {
    status = list.map(() => 'idle');
    detail = list.map((s) => s.detail);
    cur = -1;
  };
  const at = (ref) => {
    const i = typeof ref === 'number' ? ref : pos.get(ref);
    if (!Number.isInteger(i) || i < 0 || i >= list.length) throw new RangeError(`flow state: unknown step ${String(ref)}`);
    return i;
  };
  const emit = (type, i = cur) => {
    const change = { type, id: i < 0 ? null : list[i].id };
    for (const fn of [...subs]) fn(api, change);
  };
  const api = {
    steps: () => list.map((s, i) => ({ id: s.id, label: s.label, detail: detail[i] })),
    edges: () => links.map(([a, b]) => [a, b]),
    current: () => (cur < 0 ? null : { id: list[cur].id, index: cur, label: list[cur].label, detail: detail[cur], status: status[cur] }),
    statuses: () => Object.fromEntries(list.map((s, i) => [s.id, status[i]])),
    done: () => status.every((s) => FINAL.has(s)),
    start() {
      init();
      cur = 0;
      status[0] = 'running';
      emit('start');
    },
    next() {
      if (cur < 0) {
        api.start();
        return true;
      }
      const finished = status[cur] === 'running';
      if (finished) status[cur] = 'ok';
      let n = status.findIndex((s, i) => i > cur && s === 'idle');
      if (n < 0) n = status.indexOf('idle');
      if (n < 0) {
        if (finished) emit('next');
        return false;
      }
      cur = n;
      status[n] = 'running';
      emit('next');
      return true;
    },
    show(ref, { status: st = 'ok', detail: text } = {}) {
      const i = at(ref);
      if (!STATUSES.includes(st)) throw new RangeError(`flow state: unknown status ${String(st)}`);
      status[i] = st;
      if (text !== undefined) detail[i] = String(text);
      cur = i;
      emit('show');
    },
    fail(ref, text) {
      const i = at(ref);
      status[i] = 'failed';
      if (text !== undefined) detail[i] = String(text);
      cur = i;
      emit('fail');
    },
    skip(ref) {
      const i = at(ref);
      status[i] = 'skipped';
      emit('skip', i);
    },
    reset() {
      init();
      emit('reset');
    },
    subscribe(fn) {
      subs.add(fn);
      return () => {
        subs.delete(fn);
      };
    },
  };
  init();
  return api;
}
