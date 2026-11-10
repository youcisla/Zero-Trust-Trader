/* Fixture tests for the dashboard's pure helpers.
 *
 * The block between the BEGIN/END markers in web/index.html is DOM-free on
 * purpose: it is loaded here with `node:vm` and asserted on with no browser.
 * Everything that decides what the page *says* lives there.
 */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const helpers = source.split('// BEGIN fixture-tested state helpers')[1]
  .split('// END fixture-tested state helpers')[0];

const EXPORTS = ['scanCurrent', 'transportFresh', 'protectionLabel', 'parseFunnel', 'lastFunnel',
  'ledgerRows', 'pipeline', 'edgeCounts', 'edgeStates', 'computeDrawdown', 'bonferroniMirror',
  'sessionState', 'derive', 'fmtDur', 'todayStats', 'num2', 'signed', 'backoffMs'];

function load() {
  return vm.runInNewContext(helpers + '\n({' + EXPORTS.join(',') + '})', {});
}
const h = load();

/* ---- structural ------------------------------------------------------ */

test('every embedded inline script parses', () => {
  let n = 0;
  for (const match of source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    if (!match[1].trim()) continue;
    new vm.Script(match[1]);
    n += 1;
  }
  assert.ok(n >= 1, 'the app script is embedded');
});

test('no CDN and no build step crept back in', () => {
  assert.doesNotMatch(source, /https?:\/\/[^"']*\.(js|css)/);
  assert.match(source, /\/vendor\/react\.production\.min\.js/);
  assert.match(source, /\/vendor\/echarts\.min\.js/);
});

test('the six routes exist and are reachable by hash', () => {
  for (const r of ['flow', 'overview', 'research', 'performance', 'trials', 'ops']) {
    assert.match(source, new RegExp('"' + r + '"'), 'route ' + r);
  }
});

/* ---- transport & honesty -------------------------------------------- */

test('reconnect backoff grows then caps', () => {
  assert.equal(h.backoffMs(1), 500);
  assert.equal(h.backoffMs(2), 1000);
  assert.equal(h.backoffMs(3), 2000);
  assert.equal(h.backoffMs(4), 4000);
  assert.equal(h.backoffMs(5), 5000);
  assert.equal(h.backoffMs(99), 5000);
  assert.equal(h.backoffMs(0), 500);
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

test('a candidate is only "current" when the artifacts say so', () => {
  assert.equal(h.scanCurrent({scan: {current: true}}), true);
  assert.equal(h.scanCurrent({scan: {current: false}}), false);
  assert.equal(h.scanCurrent({tier3: {survivors: ['FIXTURE']}}), false);
  assert.equal(h.scanCurrent(null), false);
});

/* ---- ledger ---------------------------------------------------------- */

const SD = '2026-11-10T10:29:41Z | universe=120 tested=119 strategies=2 total_tests=238 triggering=15 deduped=1 shortlist=4 passed_both=0 trials=238 cumulative=11080 scan_id=3844a179 permutations=1000 | STAND_DOWN sig=f9459ce5';
const WIN = '2026-11-01T05:00:00Z | universe=50 tested=47 total_tests=47 triggering=2 deduped=0 shortlist=2 passed_both=1 trials=47 cumulative=2252 scan_id=abc permutations=1000 | WINNER:ETHAUD/rsi,NZDUSD/rsi sig=deadbeef';
const FAIL = '2026-11-09T21:28:53Z | universe=0 tested=0 total_tests=0 | FAILED preflight sig=aa';
const UNSIGNED = '2026-11-01T06:00:00Z | universe=50 tested=47 total_tests=47 | STAND_DOWN';

test('the ledger parser reads every shape the engine writes', () => {
  const sd = h.parseFunnel(SD);
  assert.equal(sd.verdict, 'STAND_DOWN');
  assert.equal(sd.total_tests, 238);
  assert.equal(sd.cumulative, 11080);
  assert.equal(sd.signed, true);

  const w = h.parseFunnel(WIN);
  assert.equal(w.verdict, 'WINNER');
  assert.deepEqual(Array.from(w.winners), ['ETHAUD/rsi', 'NZDUSD/rsi']);

  const f = h.parseFunnel(FAIL);
  assert.equal(f.verdict, 'FAILED');
  assert.equal(f.detail, 'preflight');

  assert.equal(h.parseFunnel(UNSIGNED).signed, false);
  assert.equal(h.parseFunnel(''), null);
  assert.equal(h.parseFunnel('not a line'), null);
});

test('rows come from the Rust ledger when present, newest first', () => {
  const rows = [{ts: '2026-11-10T10:00:00Z', verdict: 'STAND_DOWN', total_tests: 238}];
  assert.deepEqual(JSON.parse(JSON.stringify(h.ledgerRows({ledger: {rows}}))), rows);
});

test('the old funnel tail is still a working fallback', () => {
  const rows = h.ledgerRows({funnel: [SD, WIN]});
  assert.equal(rows[0].verdict, 'WINNER', 'newest line first');
  assert.equal(rows[1].verdict, 'STAND_DOWN');
});

/* ---- pipeline & edges ------------------------------------------------ */

test('pipeline counts come from the artifacts, and absence stays absent', () => {
  const none = h.pipeline({});
  assert.equal(none.tested, null);
  assert.equal(none.tests, null);
  assert.equal(none.stoodDown, null);

  const d = {
    universe: {present: true, count: 120},
    tier2: {symbols_in: 120, symbols_tested: 119, total_tests: 238, triggering: 15, deduped: [1, 2], corr_max: 0.66},
    tier3: {results: [{}, {}, {}], passed_both_gates: [], trials_cumulative: 11080, z_crit_corrected: 4.56, stood_down: true},
  };
  const x = h.pipeline(d);
  assert.equal(x.in, 120);
  assert.equal(x.tests, 238);
  assert.equal(x.deduped, 2);
  assert.equal(x.shortlist, 3);
  assert.equal(x.passed, 0);
  assert.equal(x.stoodDown, true);
});

test('an edge with no count carries no label — never a fabricated zero', () => {
  const stale = h.edgeCounts(h.pipeline({}), {scan: {current: false}});
  assert.equal(stale['tier1>tier2'], null);
  assert.equal(stale['tier3>verdict'], null);
  assert.equal(stale['scheduler>preflight'], '15 min');
});

test('a real stand-down shows zero passed and says stand down', () => {
  const data = {
    scan: {current: true},
    tier2: {symbols_tested: 119, total_tests: 238, triggering: 15},
    tier3: {results: [{}], passed_both_gates: [], stood_down: true},
  };
  const c = h.edgeCounts(h.pipeline(data), data);
  assert.equal(c['tier1>tier2'], '119 in');
  assert.equal(c['tier2>dedupe'], '15 fired');
  assert.equal(c['tier3>verdict'], '0 passed');
  assert.equal(c['verdict>consent'], 'stand down');
});

test('the running stage lights exactly its own edge, and a zero-pass scan blocks the verdict edge', () => {
  const data = {
    scan: {current: true},
    tier2: {symbols_tested: 119, total_tests: 238, triggering: 15},
    tier3: {results: [{}], passed_both_gates: [], stood_down: true},
  };
  const s = h.edgeStates({status: 'running', stage: 'tier2'}, data);
  assert.equal(s.running, true);
  assert.equal(s.hot['tier1>tier2'], true);
  assert.equal(s.hot['dedupe>tier3'], undefined);
  assert.equal(s.blocked['tier3>verdict'], true);
});

test('a finished scan lights nothing', () => {
  const s = h.edgeStates({status: 'done', stage: 'verdict'}, {scan: {current: true}});
  assert.equal(Object.keys(s.hot).length, 0);
});

/* ---- numbers --------------------------------------------------------- */

test('drawdown is measured from the running peak', () => {
  const dd = h.computeDrawdown([[1, 100], [2, 120], [3, 90], [4, 130]]);
  assert.equal(dd[0][1], 0);
  assert.equal(dd[1][1], 0);
  assert.ok(Math.abs(dd[2][1] - (-25)) < 1e-9);
  assert.equal(dd[3][1], 0);
  assert.equal(h.computeDrawdown([]).length, 0);
});

test('the Bonferroni mirror matches the engine table', () => {
  assert.equal(h.bonferroniMirror(1), 1.96);
  // Values from README's own table, so the mirror is checked against the engine.
  const pairs = [[150, 3.59], [1791, 4.19], [5388, 4.43], [9081, 4.54], [40388, 4.85]];
  for (const [n, expect] of pairs) {
    const got = h.bonferroniMirror(n);
    assert.ok(Math.abs(got - expect) <= 0.06, `n=${n} got ${got}, expected ~${expect}`);
  }
  // Monotonic, and never weaker than the naive threshold.
  assert.ok(h.bonferroniMirror(40000) > h.bonferroniMirror(9000));
  assert.ok(h.bonferroniMirror(10) > 1.96);
  assert.equal(h.bonferroniMirror(0), null);
});

test('the session matrix matches run_funnel', () => {
  assert.deepEqual(Array.from(h.sessionState(6).families), ['donchian']);
  assert.deepEqual(Array.from(h.sessionState(9).families), ['donchian', 'bollinger']);
  assert.deepEqual(Array.from(h.sessionState(14).families), ['donchian', 'rsi', 'bollinger']);
  assert.equal(h.sessionState(20).open, false);
  assert.ok(h.sessionState(20).next > 0);
});

test('one duration spelling', () => {
  assert.equal(h.fmtDur(45), '45s');
  assert.equal(h.fmtDur(60), '1m');
  assert.equal(h.fmtDur(92), '1m 32s');
  assert.equal(h.fmtDur(120), '2m');
  assert.equal(h.fmtDur(3600), '1h');
  assert.equal(h.fmtDur(5400), '1h 30m');
  assert.equal(h.fmtDur(90000), '1d 1h');
  assert.equal(h.fmtDur(null), '—');
});

/* ---- derive ---------------------------------------------------------- */

test('derive keeps the hero honest across the three states', () => {
  const none = h.derive(null, false, 0);
  assert.equal(none.verdict, null);
  assert.equal(none.protection, 'no engine state — positions unknown');

  const down = h.derive({scan: {current: true}, tier3: {stood_down: true, trials_cumulative: 11080,
    z_crit_corrected: 4.56, survivors: []}, engine: {ok: true, positions: []}, engine_age_s: 1, ts: 1762700000}, true, 0);
  assert.equal(down.verdict, 'STAND_DOWN');
  assert.equal(down.stoodDown, true);

  const up = h.derive({scan: {current: true}, tier3: {stood_down: false, survivors: [{symbol: 'EURUSD'}],
    trials_cumulative: 9000, z_crit_corrected: 4.5}, engine: {ok: true, positions: []}, engine_age_s: 1}, true, 0);
  assert.equal(up.verdict, 'PROPOSAL');
  assert.deepEqual(up.survivors, ['EURUSD']);
});

test('historical is a fact about the artifacts, not a guess', () => {
  const stale = h.derive({scan: {current: false}, tier3: {stood_down: true}}, true, 0);
  assert.equal(stale.historical, true);
  assert.equal(stale.verdict, null, 'a stale artifact is never shown as the verdict');
});
