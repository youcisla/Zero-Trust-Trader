const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const helpers = source.split('// BEGIN fixture-tested state helpers')[1].split('// END fixture-tested state helpers')[0];
function load(fetch) {
  return vm.runInNewContext(helpers + '\n({scanCurrent, transportFresh, protectionLabel, fetchLog, parseFunnel, lastFunnel})', {fetch});
}
const h = load();

test('every embedded inline script parses', () => {
  for (const match of source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    new vm.Script(match[1]);
  }
});

test('an open connection becomes stale without another message', () => {
  assert.equal(h.transportFresh(null, 10000), false);
  assert.equal(h.transportFresh(1000, 6000), true);
  assert.equal(h.transportFresh(1000, 6001), false);
});

test('dry run, applied, refusing and stale observations have distinct labels', () => {
  const data = {engine: {ok: true, applied: false, positions: [{symbol: 'FIXTURE'}]}};
  assert.match(h.protectionLabel(data, 1, true), /dry run/);
  data.engine.applied = true;
  assert.match(h.protectionLabel(data, 1, true), /apply enabled/);
  assert.match(h.protectionLabel(data, 31, true), /stale/);
  assert.match(h.protectionLabel(data, 1, false), /transport stale/);
  data.engine.ok = false;
  assert.match(h.protectionLabel(data, 1, true), /refusing/);
  assert.match(h.protectionLabel(null, null, true), /unknown/);
});

test('only completed coherent snapshots on live transport may show a candidate', () => {
  assert.equal(h.scanCurrent({scan: {current: true}}), true);
  assert.equal(h.scanCurrent({scan: {current: true}, transport_live: false}), false);
  assert.equal(h.scanCurrent({scan: {current: false}}), false);
  assert.equal(h.scanCurrent({tier3: {survivors: ['FIXTURE']}}), false);
});

test('ledger parser handles scan identity and never skips a newer failed row', () => {
  const row = '2026-11-09T12:00:00Z | universe=50 tested=50 strategies=1 total_tests=50 triggering=1 deduped=1 shortlist=1 passed_both=1 trials=50 cumulative=10000 scan_id=fixture permutations=1000 | PROPOSAL | hmac=fixture';
  assert.equal(h.parseFunnel(row).scan_id, 'fixture');
  assert.equal(h.parseFunnel(row).passed, 1);
  assert.equal(h.parseFunnel(row).permutations, 1000);
  assert.equal(h.lastFunnel([row, '2026-11-09T12:10:00Z | scan_id=newer | FAILED preflight']), null);
});

test('HTTP errors fail log export, including the shared copy-all path', async () => {
  const client = load(async () => ({ok: false, status: 404}));
  await assert.rejects(client.fetchLog('funnel'), /HTTP 404/);
});

test('truncated exports explicitly describe the newest bounded excerpt', async () => {
  const client = load(async () => ({ok: true, text: async () => 'newest line',
    headers: {get: () => 'true'}}));
  assert.match(await client.fetchLog('funnel'), /EXCERPT.*newest complete lines[\s\S]*newest line/);
});

test('compact navigation has labels independent of hidden spans', () => {
  assert.match(source, /aria-label=\$\{label\}/);
  assert.match(source, /aria-label="Architecture"/);
  assert.match(source, /aria-label="Last report"/);
});
