'use strict';
// promptfoo javascript assertion. promptfoo does not catch a throw from a file://
// assertion, so grade() returns a JUDGE_ERROR GradingResult for every failure.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const claude = require('./judge/claude');
const { registerSchema, correctnessSchema } = require('./judge/schema');
const { aggregateFindings, aggregateVerdicts, judgeError } = require('./judge/aggregate');
const { render } = require('./judge/render');
const { loadCatalog, extractWith } = require('./judge/catalog');

const SUITE_ROOT = path.resolve(__dirname, '..');
const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');

const KINDS = {
  register: {
    prefix: 'reg',
    catalog: { idPrefix: 'CR', script: 'tools/extract-register-catalog.py' },
    schema: (catalog) => registerSchema(catalog.ids),
    values: ({ output, catalog }) => ({ catalog: catalog.text, reply: output }),
    aggregate: ({ prefix, passes, catalog }) => aggregateFindings({ prefix, passes, ids: catalog.ids, perRule: true }),
  },
  correctness: {
    prefix: 'cor',
    catalog: null,
    schema: () => correctnessSchema(),
    values: ({ output }) => ({ reply: output }),
    aggregate: ({ prefix, passes }) => aggregateVerdicts({ prefix, passes }),
  },
};

const SETTINGS = {
  model: { env: 'JUDGE_MODEL', fallback: 'opus', parse: String },
  effort: { env: 'JUDGE_EFFORT', fallback: 'medium', parse: String },
  passes: { env: 'JUDGE_PASSES', fallback: 3, parse: positiveInt },
  max_procs: { env: 'JUDGE_MAX_PROCS', fallback: 36, parse: positiveInt },
  retries: { env: 'JUDGE_RETRIES', fallback: 0, parse: nonNegativeInt },
  timeout_ms: { env: 'JUDGE_TIMEOUT_MS', fallback: 600000, parse: positiveInt },
  catalog: { env: 'JUDGE_CATALOG', fallback: null, parse: String },
  mode: { env: 'JUDGE_MODE', fallback: 'whole', parse: String },
};

function positiveInt(v) {
  const n = Number(v);
  if (!Number.isInteger(n) || n < 1) throw new Error(`expected a positive integer, got ${JSON.stringify(v)}`);
  return n;
}

function nonNegativeInt(v) {
  const n = Number(v);
  if (!Number.isInteger(n) || n < 0) throw new Error(`expected a non-negative integer, got ${JSON.stringify(v)}`);
  return n;
}

function resolveSettings(config, env) {
  const out = {};
  for (const [name, spec] of Object.entries(SETTINGS)) {
    if (env[spec.env] !== undefined && env[spec.env] !== '') out[name] = { value: spec.parse(env[spec.env]), source: 'env' };
    else if (config[name] !== undefined && config[name] !== null) out[name] = { value: spec.parse(config[name]), source: 'config' };
    else out[name] = { value: spec.fallback, source: 'default' };
  }
  return out;
}

const catalogs = new Map();
function defaultLoadCatalog(kind, settings) {
  const pinned = settings.catalog.value;
  const key = `${kind.catalog.script}|${pinned || ''}`;
  if (!catalogs.has(key)) {
    const promise = loadCatalog({
      pinned,
      extract: extractWith(path.join(SUITE_ROOT, kind.catalog.script)),
      cachePath: path.join(SUITE_ROOT, 'corpus', 'catalog.md'),
      prefix: kind.catalog.idPrefix,
    });
    promise.catch(() => catalogs.delete(key));
    catalogs.set(key, promise);
  }
  return catalogs.get(key);
}

function defaultCallJudge(settings, env) {
  return claude.makeJudge({
    semaphore: claude.sharedSemaphore(settings.max_procs.value),
    retries: settings.retries.value,
    timeoutMs: settings.timeout_ms.value,
    env: claude.childEnv({ parent: env, settingsEnv: claude.readSettingsEnv(path.join(os.homedir(), '.claude', 'settings.json')) }),
  });
}

// promptfoo flattens every assertion's componentResults into one list per row, so each
// entry carries the judge kind that produced it.
function withMeta(result, meta) {
  const tag = (c) => ({ ...c, metadata: { ...c.metadata, judge_kind: meta.kind } });
  return { ...result, componentResults: [{ pass: true, score: 1, reason: 'judge run metadata', metadata: meta }, ...result.componentResults].map(tag) };
}

async function grade(output, context, deps = {}) {
  const env = deps.env || process.env;
  const config = (context && context.config) || {};
  const kind = KINDS[config.kind];
  const prefix = kind ? kind.prefix : 'judge';
  const meta = { role: 'judge', kind: config.kind === undefined ? null : config.kind };
  try {
    if (!kind) throw new Error(`unknown judge kind ${JSON.stringify(config.kind)}`);
    if (typeof output !== 'string') throw new Error(`output is ${output === null ? 'null' : typeof output}, not a string`);
    const settings = resolveSettings(config, env);
    meta.settings = settings;
    claude.checkAlias(settings.model.value);
    claude.checkEffort(settings.effort.value);
    if (typeof config.prompt !== 'string') throw new Error('config.prompt is required');
    const promptPath = path.resolve(SUITE_ROOT, config.prompt);
    const template = fs.readFileSync(promptPath, 'utf8');
    meta.prompt_path = path.relative(SUITE_ROOT, promptPath);
    meta.prompt_sha256 = sha(template);

    const catalog = kind.catalog ? await (deps.loadCatalog || ((s) => defaultLoadCatalog(kind, s)))(settings) : null;
    meta.catalog_sha256 = catalog ? catalog.sha256 : null;
    meta.catalog_source = catalog ? catalog.source : null;
    meta.catalog_path = catalog ? catalog.path : null;

    const schema = kind.schema(catalog);
    meta.schema_sha256 = sha(JSON.stringify(schema));
    const prompt = render(template, kind.values({ output, catalog, context, config }));

    const callJudge = deps.callJudge || defaultCallJudge(settings, env);
    const p = settings.passes.value;
    const results = await Promise.all(Array.from({ length: p }, () => callJudge({ prompt, schema, model: settings.model.value, effort: settings.effort.value })));
    meta.costs = results.map((r) => r.cost || 0);
    meta.cost_total = meta.costs.reduce((a, b) => a + b, 0);
    meta.attempts = results.map((r) => r.attempts);
    meta.models = [...new Set(results.flatMap((r) => r.models || []))].sort();
    meta.peak_inflight = deps.peakInflight ? deps.peakInflight() : claude.peakInflight();

    const failed = results.findIndex((r) => !r.ok);
    if (failed >= 0) return withMeta(judgeError({ prefix, message: `pass ${failed}: ${results[failed].error}` }), meta);
    return withMeta(kind.aggregate({ prefix, passes: results.map((r) => r.value), catalog, context, config }), meta);
  } catch (e) {
    return withMeta(judgeError({ prefix, message: e.message }), meta);
  }
}

module.exports = async (output, context) => grade(output, context);
module.exports.grade = grade;
module.exports.resolveSettings = resolveSettings;
module.exports.KINDS = KINDS;
