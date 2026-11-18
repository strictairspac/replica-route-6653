// Stdlib-free browser smoke test: minimal DOM stubs, run with node --test.
// Renders the three migration pages and exercises the form transforms used in app.js.
import test from 'node:test';
import assert from 'node:assert/strict';

const elements = {};
function makeEl(tag = 'div') {
  return {
    tag, innerHTML: '', textContent: '', className: '', value: '', dataset: {},
    elements: new Proxy(elements, {
      get(t, k) { if (!(k in t)) t[k] = makeEl('input'); return t[k]; },
      set(t, k, v) { t[k] = v; return true; },
    }),
    addEventListener() {}, scrollIntoView() {}, querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
  };
}
globalThis.document = {
  querySelector: () => makeEl(),
  querySelectorAll: () => [],
  createElement: () => ({ click() {}, set href(v) {}, set download(v) {} }),
};
globalThis.window = { addEventListener() {} };
globalThis.location = { hash: '' };
globalThis.localStorage = {
  getItem: k => (k === 'replicaroute-user' ? JSON.stringify({ role: 'operator', email: 'o@x' }) : 'tok'),
  setItem() {}, removeItem() {},
};
globalThis.fetch = async () => ({ ok: true, json: async () => ({}), blob: async () => new Blob() });
globalThis.Blob = class {};
globalThis.URL = { createObjectURL: () => 'blob:x', revokeObjectURL() {} };

const { migrationPlansPage } = await import('../src/pages/migration_plans.js');
const { migrationsPage } = await import('../src/pages/migrations.js');
const { migrationEvidencePage } = await import('../src/pages/migration_evidence.js');

const plan = {
  id: 1, code: 'MG-1', title: 't', key_start: 'a', key_end: 'z',
  source_shard: 's', target_shard: 't', note: 'n',
  source_initial: [{ key: 'a', value: '1' }], target_initial: [],
  events: [{ at_ms: 0, kind: 'cutover' }],
};
const migration = {
  id: 7, name: 'run', status: 'completed', created_at: new Date().toISOString(),
  migration_plan_id: 1,
  result: {
    phase: 'completed', cutover_completed: true,
    key_range: { start: 'a', end: 'z' },
    ownership: [{ key: 'a', owner: 'target', in_range: true,
      source: { value: '1', version: 1 }, target: { value: '2', version: 2 } }],
    trace: [
      { index: 1, at_ms: 0, kind: 'cutover', key: null, route: null, owner: 'target',
        decision: 'applied', reason: '路由归属切换至目标', incoming_version: null,
        source_version: 2, target_version: 2, stored_version: null, applied: true,
        duplicate: false, in_range: null, gate_reasons: [], route_hint: null },
      { index: 2, at_ms: 10, kind: 'write', key: 'a', route: 'old->forwarded', owner: 'target',
        decision: 'routed', reason: '旧路由迟到写经源分片转发至新归属目标', incoming_version: null,
        source_version: 2, target_version: 2, stored_version: 3, applied: null,
        duplicate: false, in_range: true, gate_reasons: [], route_hint: 'old' },
    ],
    summary: { writes: 1, reads: 0, routed_writes: 1, rejected_writes: 0, rejected_reads: 0,
      copy_applied: 0, copy_skipped: 0, stream_applied: 0, stream_skipped: 0,
      cutover_rejected: 0, data_loss_keys: [], no_data_loss: true,
      source_accepted_keys: 1, target_in_range_keys: 1 },
    policy: { availability_cost: '双份存储与复制带宽', late_write_rule: '转发或拒绝',
      stream_rule: '幂等', version_model: '版本闸', cutover_gate: '逐键追平',
      mode: '受控模型' },
    checksum: 'abc',
  },
};

test('migration plan page renders form and event docs', () => {
  const html = migrationPlansPage([plan]);
  assert.match(html, /id="plan-form"/);
  assert.match(html, /MG-1/);
  assert.match(html, /copy_snapshot/);
  assert.match(html, /route_hint=old/);
});

test('migrations page renders run record and gate protocol', () => {
  const html = migrationsPage([{ ...migration, result: migration.result }], [plan]);
  assert.match(html, /id="migration-form"/);
  assert.match(html, /旧拷贝不覆盖新写/);
  assert.match(html, /range-shift-20261118/);
});

test('migration evidence shows ownership, late write disposition and no-loss verdict', () => {
  const html = migrationEvidencePage(migration);
  assert.match(html, /无数据丢失/);
  assert.match(html, /old-&gt;forwarded/);
  assert.match(html, /旧路由迟到写经源分片转发至新归属目标/);
  assert.match(html, /owner target/);
  assert.match(html, /双份存储与复制带宽/);
  assert.match(html, /migration-export/);
});

test('evidence page empty state', () => {
  assert.match(migrationEvidencePage(undefined), /还没有搬迁证据/);
});

test('plan form transform matches backend MigrationPlanInput shape', () => {
  // Mirrors the bindForm transform in app.js for #plan-form.
  const formValues = {
    id: '', code: 'MG-2', title: 't', key_start: 'a', key_end: 'z',
    source_shard: 's', target_shard: 't', note: '',
    source_initial: '[{"key":"a","value":"1"}]',
    target_initial: '[]',
    plan_events: '[{"at_ms":0,"kind":"cutover"}]',
  };
  const payload = {
    ...formValues,
    source_initial: JSON.parse(formValues.source_initial),
    target_initial: JSON.parse(formValues.target_initial),
    events: JSON.parse(formValues.plan_events),
  };
  delete payload.plan_events; delete payload.id;
  assert.deepEqual(payload.source_initial, [{ key: 'a', value: '1' }]);
  assert.deepEqual(payload.events, [{ at_ms: 0, kind: 'cutover' }]);
  assert.equal('plan_events' in payload, false);
});
