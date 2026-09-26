'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const judgeModule = require('../graders/judge');
const { grade, resolveSettings } = judgeModule;

const CATALOG = { text: '- `CR-a` one\n- `CR-b` two\n', sha256: 'catsha', ids: ['CR-a', 'CR-b'], source: 'pinned', path: '/pinned.md' };
const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');

function fakeJudge(values) {
  const calls = [];
  let i = 0;
  const fn = async (req) => {
    calls.push(req);
    const v = values[Math.min(i, values.length - 1)];
    i += 1;
    return typeof v === 'function' ? v(req) : v;
  };
  fn.calls = calls;
  return fn;
}

const okv = (value, cost = 0.01) => ({ ok: true, value, cost, attempts: 1, models: ['claude-opus-5-5[1m]'] });

function ctx(config, output = 'the reply') {
  return { config, vars: { prompt: 'Q' }, providerResponse: { output }, test: {} };
}

test('the module exports a function for promptfoo and grade for injection', () => {
  assert.equal(typeof judgeModule, 'function');
  assert.equal(typeof grade, 'function');
});

test('settings resolve env over config over defaults, and name the source', () => {
  const s = resolveSettings({ passes: 3, model: 'sonnet' }, { JUDGE_PASSES: '5' });
  assert.deepEqual(s.passes, { value: 5, source: 'env' });
  assert.deepEqual(s.model, { value: 'sonnet', source: 'config' });
  assert.deepEqual(s.effort, { value: 'medium', source: 'default' });
  assert.deepEqual(s.retries, { value: 0, source: 'default' });
});

test('a register row runs P passes over the rendered catalog and reply', async () => {
  const callJudge = fakeJudge([okv({ findings: [] })]);
  const r = await grade('the reply', ctx({ kind: 'register', prompt: 'prompts/register-comply-json.txt', passes: 3 }), { callJudge, loadCatalog: async () => CATALOG, env: {}, peakInflight: () => 7 });
  assert.equal(callJudge.calls.length, 3);
  for (const c of callJudge.calls) {
    assert.ok(c.prompt.includes(CATALOG.text));
    assert.ok(c.prompt.includes('the reply'));
    assert.equal(c.model, 'opus');
    assert.equal(c.effort, 'medium');
    assert.deepEqual(c.schema.properties.findings.items.properties.rule.enum, ['CR-a', 'CR-b']);
  }
  assert.equal(r.pass, true);
  assert.equal(r.namedScores.reg_clean_maj, 1);
  assert.equal(r.namedScores['R:CR-a'], 0);
  const meta = r.componentResults[0].metadata;
  assert.equal(meta.role, 'judge');
  assert.equal(meta.kind, 'register');
  assert.equal(meta.catalog_sha256, 'catsha');
  assert.equal(meta.catalog_source, 'pinned');
  const tpl = fs.readFileSync(path.join(__dirname, '..', 'prompts', 'register-comply-json.txt'), 'utf8');
  assert.equal(meta.prompt_sha256, sha(tpl));
  assert.equal(meta.schema_sha256, sha(JSON.stringify(callJudge.calls[0].schema)));
  assert.deepEqual(meta.settings.passes, { value: 3, source: 'config' });
  assert.deepEqual(meta.costs, [0.01, 0.01, 0.01]);
  assert.equal(meta.peak_inflight, 7);
  assert.deepEqual(meta.models, ['claude-opus-5-5[1m]']);
});

test('every component result names the judge kind that produced it', async () => {
  const callJudge = fakeJudge([okv({ findings: [{ rule: 'CR-a', span: 'I', why: 'w' }] })]);
  const r = await grade('I think', ctx({ kind: 'register', prompt: 'prompts/register-comply-json.txt', passes: 3 }), { callJudge, loadCatalog: async () => CATALOG, env: {}, peakInflight: () => 0 });
  assert.ok(r.componentResults.length > 1);
  for (const c of r.componentResults) assert.equal(c.metadata.judge_kind, 'register');
  const e = await grade(null, ctx({ kind: 'correctness', prompt: 'prompts/correctness-judge-json.txt' }), { callJudge, env: {}, peakInflight: () => 0 });
  for (const c of e.componentResults) assert.equal(c.metadata.judge_kind, 'correctness');
});

test('the env pass count beats the config', async () => {
  const callJudge = fakeJudge([okv({ findings: [] })]);
  const r = await grade('x', ctx({ kind: 'register', prompt: 'prompts/register-comply-json.txt', passes: 3 }), { callJudge, loadCatalog: async () => CATALOG, env: { JUDGE_PASSES: '5' }, peakInflight: () => 0 });
  assert.equal(callJudge.calls.length, 5);
  assert.equal(r.namedScores.reg_passes, 5);
});

test('one failed pass makes the whole row a judge error', async () => {
  const callJudge = fakeJudge([okv({ findings: [] }), { ok: false, error: 'timeout', attempts: 1, cost: 0 }, okv({ findings: [] })]);
  const r = await grade('x', ctx({ kind: 'register', prompt: 'prompts/register-comply-json.txt', passes: 3 }), { callJudge, loadCatalog: async () => CATALOG, env: {}, peakInflight: () => 0 });
  assert.equal(r.pass, false);
  assert.match(r.reason, /^JUDGE_ERROR: pass 1: timeout/);
  assert.equal(r.namedScores.reg_judge_error, 1);
  assert.equal('reg_judged' in r.namedScores, false);
  assert.equal(r.componentResults[0].metadata.role, 'judge');
});

test('correctness rows grade the reply alone with the verdict schema', async () => {
  const callJudge = fakeJudge([okv({ verdict: 'pass', reason: 'ok' }), okv({ verdict: 'fail', reason: 'no' }), okv({ verdict: 'pass', reason: 'ok' })]);
  const r = await grade('the reply', ctx({ kind: 'correctness', prompt: 'prompts/correctness-judge-json.txt', passes: 3 }), { callJudge, loadCatalog: async () => { throw new Error('catalog not needed'); }, env: {}, peakInflight: () => 0 });
  assert.equal(r.pass, true);
  assert.equal(r.namedScores.cor_pass_votes, 2);
  assert.ok(callJudge.calls[0].prompt.includes('the reply'));
  assert.deepEqual(callJudge.calls[0].schema.properties.verdict.enum, ['pass', 'fail']);
  assert.equal(r.componentResults[0].metadata.catalog_sha256, null);
});

test('grade never throws', async () => {
  const deps = { callJudge: fakeJudge([okv({ findings: [] })]), loadCatalog: async () => CATALOG, env: {}, peakInflight: () => 0 };
  const cases = [
    [undefined, ctx({ kind: 'register', prompt: 'prompts/register-comply-json.txt' })],
    ['x', ctx({ kind: 'nonsense' })],
    ['x', ctx({ kind: 'register', prompt: 'prompts/no-such-file.txt' })],
    ['x', { vars: {} }],
    ['x', ctx({ kind: 'register', prompt: 'prompts/register-comply-json.txt' })],
  ];
  const results = [];
  for (const [out, c] of cases.slice(0, 4)) results.push(await grade(out, c, deps));
  results.push(await grade('x', cases[4][1], { ...deps, loadCatalog: async () => { throw new Error('extract failed'); } }));
  results.push(await grade('x', cases[4][1], { ...deps, env: { JUDGE_MODEL: 'gpt-6' } }));
  for (const r of results) {
    assert.equal(r.pass, false);
    assert.match(r.reason, /^JUDGE_ERROR/);
  }
});

test('an empty reply is graded, not refused', async () => {
  const callJudge = fakeJudge([okv({ findings: [] })]);
  const r = await grade('', ctx({ kind: 'register', prompt: 'prompts/register-comply-json.txt', passes: 1 }, ''), { callJudge, loadCatalog: async () => CATALOG, env: {}, peakInflight: () => 0 });
  assert.equal(callJudge.calls.length, 1);
  assert.equal(r.pass, true);
});
