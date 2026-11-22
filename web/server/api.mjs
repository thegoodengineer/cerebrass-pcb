// ClawBoard dashboard backend: run control, live events (SSE), LLM metering, benchmark lanes.
//
// Mounted into the Vite dev server (see vite.config.js) so everything lives on localhost:3000.
// API keys are read from the environment / .env on the server and are never sent to the browser.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { handleMock } from './mock.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO = path.resolve(HERE, '..', '..');
// Working copies are rewritten many times per second: keep them out of the repo (and out of any
// cloud-synced folder) unless CLAWBOARD_RUNS_DIR says otherwise.
const RUNS = process.env.CLAWBOARD_RUNS_DIR ? path.resolve(process.env.CLAWBOARD_RUNS_DIR) : path.join(os.tmpdir(), 'clawboard-runs');
const PUBLIC = path.join(REPO, 'web', 'public');
const SAMPLE = path.join(REPO, 'sample_board');
const TOOLS_DIR = path.join(REPO, 'pcb-tools');

// ---------------------------------------------------------------------------------- env
function loadDotEnv() {
  const file = path.join(REPO, '.env');
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)\s*$/i);
    if (!m || line.trim().startsWith('#')) continue;
    let v = m[2].trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    if (process.env[m[1]] === undefined) process.env[m[1]] = v;
  }
}
loadDotEnv();

const PY = process.env.CLAWBOARD_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
let ORIGIN = `http://127.0.0.1:${process.env.PORT || 3000}`;

function laneDefs() {
  return {
    cerebras: {
      id: 'cerebras',
      label: 'Cerebras',
      sub: 'Wafer-scale inference',
      baseUrl: (process.env.CEREBRAS_BASE_URL || 'https://api.cerebras.ai/v1').replace(/\/$/, ''),
      model: process.env.CEREBRAS_MODEL || 'moonshotai-kimi-k3.1-pro-trial',
      key: process.env.CEREBRAS_API_KEY || '',
      extraBody: { prompt_cache_key: 'clawboard-pcb-v1' },
      simProfile: 'fast',
    },
    baseline: {
      id: 'baseline',
      label: process.env.BASELINE_LABEL || 'Standard GPU endpoint',
      sub: 'OpenAI-compatible baseline',
      baseUrl: (process.env.BASELINE_BASE_URL || '').replace(/\/$/, ''),
      model: process.env.BASELINE_MODEL || '',
      key: process.env.BASELINE_API_KEY || '',
      extraBody: {},
      simProfile: 'slow',
    },
  };
}

function hostOf(url) {
  try { return new URL(url).host; } catch { return ''; }
}

function openclawInfo() {
  const r = spawnSync(process.platform === 'win32' ? 'where' : 'which', ['openclaw'], { encoding: 'utf8' });
  const found = r.status === 0 && r.stdout.trim().length > 0;
  const nodeMajor = Number(process.versions.node.split('.')[0]);
  return { found, nodeOk: nodeMajor >= 24, node: process.versions.node };
}

function publicConfig() {
  const d = laneDefs();
  const oc = openclawInfo();
  return {
    lanes: Object.fromEntries(Object.values(d).map((l) => [l.id, {
      label: l.label, sub: l.sub, model: l.model || '(not set)', host: hostOf(l.baseUrl) || '(not set)', ready: Boolean(l.key && l.baseUrl && l.model),
    }])),
    openclaw: { ...oc, gateway: gatewayInfo() },
    defaultEngine: oc.found && oc.nodeOk ? 'openclaw' : 'direct',
    sampleReady: fs.existsSync(path.join(SAMPLE, 'sample_board.kicad_pcb')),
  };
}

// ---------------------------------------------------------------------------------- state + SSE
const clients = new Set();
const lastGoodSvg = new Map();
const state = { runId: null, mode: 'idle', simulate: false, engine: null, startedAt: null, idleSeq: 0, lanes: {} };
const runtime = {}; // per-lane handles that must not be serialised (child processes, timers)
let dirty = false;

function snapshot() {
  state.gateway = gatewayInfo();
  return JSON.stringify({ type: 'state', state });
}
function touch() { dirty = true; }
setInterval(() => {
  if (!dirty) return;
  dirty = false;
  const msg = `data: ${snapshot()}\n\n`;
  for (const res of clients) res.write(msg);
}, 80).unref();

function trace(lane, entry) {
  lane.trace.push({ t: Date.now() - lane.startedAt, ...entry });
  if (lane.trace.length > 240) lane.trace.splice(0, lane.trace.length - 240);
  touch();
}

function newLane(def, simulate, engine, runId) {
  return {
    id: def.id, label: def.label, sub: def.sub, runId,
    model: simulate ? `${def.model || 'baseline-model'}` : def.model,
    host: simulate ? 'simulated (no network)' : hostOf(def.baseUrl),
    simulate, engine, status: 'starting', startedAt: Date.now(), loopStartedAt: null, endedAt: null, error: null,
    projectDir: path.join(RUNS, def.id, 'board'),
    metrics: { llmMs: 0, toolMs: 0, setupMs: null, ttftMs: null, tokensOut: 0, tokensIn: 0, cachedIn: 0, calls: 0, genMs: 0, tps: 0, liveTps: 0, tokensEstimated: false },
    live: { active: false, text: '', startedAt: 0, firstTokenAt: 0, tokens: 0 },
    trace: [], requests: [], auditBefore: null, audit: null, steps: [], issues: [], oracle: null, svgSeq: 0, final: '',
  };
}

// ---------------------------------------------------------------------------------- prompts + tools
function systemPrompt() {
  return fs.readFileSync(path.join(REPO, 'openclaw_config', 'workspace', 'AGENTS.md'), 'utf8');
}
function taskPrompt(projectDir) {
  return `Audit and repair the KiCad project in this directory:\n\nproject_dir: ${projectDir}\n\n` +
    'Target: JLCPCB 2-layer, zero DFM/ERC errors and warnings. Follow your workflow, batch every fix into one patch, then sign off.';
}
function toolSchemas() {
  return JSON.parse(fs.readFileSync(path.join(TOOLS_DIR, 'tools.schema.json'), 'utf8')).tools;
}

// MCP client (stdio, newline-delimited JSON-RPC) - the same server OpenClaw talks to.
function startMcp(lane) {
  const child = spawn(PY, [path.join(TOOLS_DIR, 'mcp_server.py')], {
    cwd: REPO, stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
  });
  let buf = '';
  let nextId = 1;
  const pending = new Map();
  child.stdout.on('data', (d) => {
    buf += d.toString('utf8');
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (!line) continue;
      try {
        const msg = JSON.parse(line);
        const p = pending.get(msg.id);
        if (p) { pending.delete(msg.id); msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result); }
      } catch { /* ignore non-protocol output */ }
    }
  });
  let stderr = '';
  child.stderr.on('data', (d) => { stderr = (stderr + d.toString('utf8')).slice(-2000); });
  child.on('exit', () => { for (const p of pending.values()) p.reject(new Error(`pcb tool server exited. ${stderr}`)); pending.clear(); });
  const request = (method, params) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
  const ready = request('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'clawboard-dashboard', version: '1.0.0' } })
    .then(() => child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n'));
  return {
    child,
    async call(name, args) {
      await ready;
      const res = await request('tools/call', { name, arguments: args });
      const text = res?.content?.[0]?.text || '{}';
      try { return JSON.parse(text); } catch { return { error: 'tool returned non-JSON output' }; }
    },
    stop() { try { child.kill(); } catch { /* already gone */ } },
  };
}

function runPy(args, timeoutMs = 240000) {
  return new Promise((resolve) => {
    const child = spawn(PY, args, { cwd: REPO, env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } });
    let out = '';
    let err = '';
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.stdout.on('data', (d) => { out += d.toString('utf8'); });
    child.stderr.on('data', (d) => { err += d.toString('utf8'); });
    child.on('close', () => { clearTimeout(timer); try { resolve(JSON.parse(out)); } catch { resolve({ error: (err || out || 'no output').slice(-600) }); } });
    child.on('error', (e) => { clearTimeout(timer); resolve({ error: String(e.message || e) }); });
  });
}

// ---------------------------------------------------------------------------------- project state tailing
function readJson(file, fallback = null) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; }
}

async function prepareProject(lane) {
  await fs.promises.rm(path.join(RUNS, lane.id), { recursive: true, force: true, maxRetries: 5, retryDelay: 60 });
  await fs.promises.mkdir(lane.projectDir, { recursive: true });
  await fs.promises.cp(SAMPLE, lane.projectDir, { recursive: true, filter: (src) => !src.includes(`${path.sep}.clawboard`) && !src.endsWith('.tmp') });
  const st = path.join(lane.projectDir, '.clawboard');
  await fs.promises.mkdir(st, { recursive: true });
  const suffix = lane.id === 'cerebras' ? '' : `_${lane.id}`;
  await fs.promises.mkdir(PUBLIC, { recursive: true });
  await fs.promises.writeFile(path.join(st, 'config.json'), JSON.stringify({
    svg_out: path.join(PUBLIC, `board_preview${suffix}.svg`),
    sch_svg_out: path.join(PUBLIC, `schematic_preview${suffix}.svg`),
  }));
}

function startTail(lane) {
  const st = path.join(lane.projectDir, '.clawboard');
  const evFile = path.join(st, 'events.jsonl');
  let offset = 0;
  let svgStamp = 0;
  const openTools = new Map();
  const tick = () => {
    try {
      const stat = fs.statSync(evFile);
      if (stat.size > offset) {
        const fd = fs.openSync(evFile, 'r');
        const buf = Buffer.alloc(stat.size - offset);
        fs.readSync(fd, buf, 0, buf.length, offset);
        fs.closeSync(fd);
        const text = buf.toString('utf8');
        const last = text.lastIndexOf('\n');
        if (last >= 0) {
          offset += Buffer.byteLength(text.slice(0, last + 1));
          for (const line of text.slice(0, last).split('\n')) {
            if (!line.trim()) continue;
            let ev;
            try { ev = JSON.parse(line); } catch { continue; }
            if (ev.phase === 'start') {
              openTools.set(ev.tool, Date.now());
              trace(lane, { kind: 'tool', name: ev.tool, status: 'running' });
            } else if (ev.phase === 'op') {
              trace(lane, { kind: 'op', ok: ev.ok, text: ev.brief, errors: ev.errors, resolved: ev.resolved });
            } else if (ev.phase === 'end') {
              const open = [...lane.trace].reverse().find((e) => e.kind === 'tool' && e.name === ev.tool && e.status === 'running');
              if (open) { open.status = ev.ok ? 'ok' : 'error'; open.ms = ev.ms; open.text = ev.brief; }
              lane.metrics.toolMs += ev.ms || 0;
              touch();
            }
          }
        }
      }
    } catch { /* file not there yet */ }
    const audit = readJson(path.join(st, 'audit.json'));
    if (audit) {
      if (!lane.auditBefore && !readJson(path.join(st, 'steps.json'))) lane.auditBefore = { summary: audit.summary, issues: audit.issues.map((i) => ({ id: i.id, title: i.title, severity: i.severity, rule: i.rule })) };
      const sig = JSON.stringify(audit.summary) + audit.issues.length;
      if (sig !== lane._auditSig) {
        lane._auditSig = sig;
        lane.audit = { pass: audit.pass, summary: audit.summary, rules: audit.rules_checked?.length || 0, remaining: audit.issues.map((i) => i.id) };
        touch();
      }
    }
    const steps = readJson(path.join(st, 'steps.json'));
    if (steps && steps.length !== lane.steps.length) { lane.steps = steps.map((s) => ({ op: s.op, before: s.errors_before, after: s.errors_after, resolved: s.resolved })); touch(); }
    try {
      const m = fs.statSync(path.join(st, 'board_preview.svg')).mtimeMs;
      if (m !== svgStamp) { svgStamp = m; lane.svgSeq += 1; touch(); }
    } catch { /* not rendered yet */ }
  };
  runtime[lane.id].tail = setInterval(tick, 110);
  runtime[lane.id].tailTick = tick;
}

// ---------------------------------------------------------------------------------- LLM metering
const estTokens = (s) => Math.ceil((s || '').length / 3.8);

// Reads an OpenAI-compatible chat.completions response (SSE or JSON), updates live metrics, returns the message.
async function meterResponse(lane, upstream, t0, onBytes) {
  const req = { n: lane.requests.length + 1, started_at: new Date(t0).toISOString(), status: upstream.status };
  lane.requests.push(req);
  lane.metrics.calls += 1;
  if (lane.metrics.setupMs === null) {
    lane.metrics.setupMs = Math.max(0, t0 - lane.startedAt);
    lane.loopStartedAt = t0;
    runtime[lane.id]?.firstCall?.();
  }
  lane.live = { active: true, text: '', startedAt: t0, firstTokenAt: 0, tokens: 0 };
  touch();
  const message = { role: 'assistant', content: '' };
  const calls = [];
  let usage = null;
  let reasoning = '';
  let finish = null;
  const ctype = upstream.headers.get('content-type') || '';
  const onDelta = (text) => {
    if (!text) return;
    if (!lane.live.firstTokenAt) lane.live.firstTokenAt = Date.now();
    lane.live.text = (lane.live.text + text).slice(-600);
    lane.live.tokens += text.length / 3.8;
    const gen = (Date.now() - lane.live.firstTokenAt) / 1000;
    lane.metrics.liveTps = gen > 0.03 ? lane.live.tokens / gen : 0;
    touch();
  };
  const handleChunk = (obj) => {
    if (obj.usage) usage = obj.usage;
    if (obj.time_info) req.time_info = obj.time_info;
    const ch = obj.choices && obj.choices[0];
    if (!ch) return;
    if (ch.finish_reason) finish = ch.finish_reason;
    const d = ch.delta || ch.message || {};
    if (typeof d.content === 'string' && d.content) { message.content += d.content; onDelta(d.content); }
    const r = d.reasoning_content || d.reasoning || d.reasoning_text;
    if (typeof r === 'string' && r) { reasoning += r; onDelta(r); }
    for (const tc of d.tool_calls || []) {
      const i = tc.index ?? calls.length;
      calls[i] = calls[i] || { id: '', type: 'function', function: { name: '', arguments: '' } };
      if (tc.id) calls[i].id = tc.id;
      if (tc.function?.name) calls[i].function.name += tc.function.name;
      if (tc.function?.arguments) { calls[i].function.arguments += tc.function.arguments; onDelta(tc.function.arguments); }
    }
  };
  if (!upstream.ok) {
    const body = await upstream.text();
    if (onBytes) onBytes(Buffer.from(body));
    lane.live.active = false;
    req.error = body.slice(0, 500);
    throw new Error(`LLM endpoint returned HTTP ${upstream.status}: ${body.slice(0, 300)}`);
  }
  if (ctype.includes('text/event-stream')) {
    const reader = upstream.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      if (onBytes) onBytes(value);
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (!line.startsWith('data:')) continue;
        const data = line.slice(5).trim();
        if (data === '[DONE]' || !data) continue;
        try { handleChunk(JSON.parse(data)); } catch { /* partial / keep-alive */ }
      }
    }
  } else {
    const body = await upstream.text();
    if (onBytes) onBytes(Buffer.from(body));
    try { handleChunk(JSON.parse(body)); } catch { /* not JSON */ }
  }
  const t1 = Date.now();
  const first = lane.live.firstTokenAt || t1;
  const outText = message.content + reasoning + calls.map((c) => c.function.arguments + c.function.name).join('');
  const outTokens = usage?.completion_tokens ?? estTokens(outText);
  const inTokens = usage?.prompt_tokens ?? 0;
  const cached = usage?.prompt_tokens_details?.cached_tokens ?? usage?.cached_tokens ?? 0;
  const apiGen = Number(req.time_info?.completion_time);
  const genMs = apiGen > 0 ? apiGen * 1000 : Math.max(t1 - first, 1);
  req.generation_time_source = apiGen > 0 ? 'provider time_info.completion_time' : 'client: first streamed token to end';
  if (req.time_info?.queue_time) lane.metrics.queueMs = (lane.metrics.queueMs || 0) + req.time_info.queue_time * 1000;
  Object.assign(req, {
    ttft_ms: first - t0, duration_ms: t1 - t0, generation_ms: genMs,
    prompt_tokens: inTokens, completion_tokens: outTokens, cached_tokens: cached,
    tokens_source: usage ? 'api usage' : 'estimated from characters',
    tps_generation: +(outTokens / (genMs / 1000)).toFixed(1), tps_end_to_end: +(outTokens / ((t1 - t0) / 1000)).toFixed(1),
    finish_reason: finish, tool_calls: calls.filter(Boolean).map((c) => c.function.name),
  });
  const m = lane.metrics;
  if (m.ttftMs === null) m.ttftMs = req.ttft_ms;
  m.llmMs += req.duration_ms;
  m.genMs += genMs;
  m.tokensOut += outTokens;
  m.tokensIn += inTokens;
  m.cachedIn += cached;
  if (!usage) m.tokensEstimated = true;
  m.tps = m.genMs > 0 ? m.tokensOut / (m.genMs / 1000) : 0;
  m.liveTps = req.tps_generation;
  lane.live.active = false;
  const names = req.tool_calls.map((n) => n.split('__').pop());
  trace(lane, {
    kind: 'llm', n: req.n, ms: req.duration_ms, ttft: req.ttft_ms, tokens: outTokens, tokensIn: inTokens, cached, tps: req.tps_generation,
    text: names.length ? `calls ${names.join(', ')}` : (message.content || '').trim().split('\n')[0].slice(0, 120),
  });
  const clean = calls.filter(Boolean);
  if (clean.length) message.tool_calls = clean;
  if (!message.content) message.content = clean.length ? null : '';
  return { message, usage, reasoning };
}

function upstreamFor(lane) {
  const def = laneDefs()[lane.id];
  if (lane.simulate) return { url: `${ORIGIN}/mock/${def.simProfile}/v1`, key: 'simulated', model: lane.model, extraBody: {} };
  return { url: def.baseUrl, key: def.key, model: def.model, extraBody: def.extraBody };
}

// Busy endpoints answer 429 / 503 ("queue exceeded", rate limit). Those are retried with a short
// back-off; the waiting is recorded on the lane (retries, retryMs) and is not counted as model time.
async function fetchUpstream(lane, url, init) {
  for (let attempt = 1; ; attempt++) {
    const t0 = Date.now();
    const res = await fetch(url, init);
    if (![429, 503, 529].includes(res.status) || attempt >= 30) return { res, t0 };
    const body = (await res.text()).slice(0, 200);
    const hinted = Number(res.headers.get('retry-after'));
    const wait = Math.min(20000, hinted > 0 ? hinted * 1000 : Math.min(2000, 400 + 200 * attempt));
    lane.metrics.retries = (lane.metrics.retries || 0) + 1;
    lane.metrics.retryMs = (lane.metrics.retryMs || 0) + wait + (Date.now() - t0);
    trace(lane, { kind: 'note', text: `endpoint busy (HTTP ${res.status}: ${body.replace(/\s+/g, ' ').slice(0, 90)}) - retry ${attempt} in ${(wait / 1000).toFixed(1)} s` });
    await new Promise((r) => setTimeout(r, wait));
    if (runtime[lane.id]?.abort.signal.aborted) return { res, t0 };
  }
}

async function chat(lane, body) {
  const up = upstreamFor(lane);
  const { res, t0 } = await fetchUpstream(lane, `${up.url}/chat/completions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${up.key}`, 'user-agent': 'clawboard-dashboard/1.0' },
    body: JSON.stringify({ ...body, ...up.extraBody, model: up.model }),
    signal: runtime[lane.id].abort.signal,
  });
  return meterResponse(lane, res, t0, null);
}

// ---------------------------------------------------------------------------------- engines
async function runDirect(lane) {
  const mcp = startMcp(lane);
  runtime[lane.id].mcp = mcp;
  const tools = toolSchemas().map((t) => ({ type: 'function', function: { name: t.name, description: t.description, parameters: t.inputSchema } }));
  const messages = [{ role: 'system', content: systemPrompt() }, { role: 'user', content: taskPrompt(lane.projectDir) }];
  for (let turn = 0; turn < 10; turn++) {
    const { message } = await chat(lane, {
      messages, tools, tool_choice: 'auto', temperature: 0.2, max_completion_tokens: 4096, stream: true, stream_options: { include_usage: true },
    });
    messages.push(message);
    if (!message.tool_calls?.length) { lane.final = message.content || ''; break; }
    for (const tc of message.tool_calls) {
      const name = tc.function.name.split('__').pop();
      let out;
      try {
        const args = JSON.parse(tc.function.arguments || '{}');
        out = await mcp.call(name, args);
      } catch (e) {
        out = { error: `could not run ${name}: ${e.message}` };
        trace(lane, { kind: 'note', text: out.error });
      }
      runtime[lane.id].tailTick?.();
      messages.push({ role: 'tool', tool_call_id: tc.id, content: JSON.stringify(out) });
    }
  }
}

// ---- OpenClaw: one warm Gateway, one HTTP request per agent turn -------------------------------
// `openclaw agent exec` works too, but every invocation re-loads OpenClaw's plugin registry
// (60-100 s on a laptop). The Gateway loads once; its OpenAI-compatible endpoint then starts an
// agent turn in about a second, which is what a live demo needs.
const GW = globalThis.__clawboardGateway || (globalThis.__clawboardGateway = {
  status: 'stopped', port: Number(process.env.CLAWBOARD_GATEWAY_PORT || 18931), token: randomUUID().replaceAll('-', ''),
  child: null, promise: null, startedAt: 0, readyMs: null, error: null, log: '',
});

function openclawEntry() {
  // Run the CLI's JS entry with this Node directly: no shell, so no quoting problems on Windows.
  const r = spawnSync(process.platform === 'win32' ? 'where' : 'which', ['openclaw'], { encoding: 'utf8' });
  const first = (r.stdout || '').split(/\r?\n/).find((l) => l.trim());
  if (r.status !== 0 || !first) return null;
  try {
    if (process.platform !== 'win32') return fs.realpathSync(first.trim());
    const pkgDir = path.join(path.dirname(first.trim()), 'node_modules', 'openclaw');
    const pkg = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf8'));
    return path.join(pkgDir, typeof pkg.bin === 'string' ? pkg.bin : pkg.bin.openclaw);
  } catch { return null; }
}

function writeOpenClawRuntimeConfig() {
  // openclaw_config/openclaw.json with (a) absolute paths, (b) one agent per benchmark lane whose
  // provider is the local metering pass-through, (c) the Gateway's HTTP endpoint switched on.
  const fwd = (v) => v.replaceAll('\\', '/');
  const text = fs.readFileSync(path.join(REPO, 'openclaw_config', 'openclaw.json'), 'utf8')
    .replaceAll('${CLAWBOARD_HOME}', fwd(REPO)).replaceAll('${CLAWBOARD_PYTHON}', fwd(PY));
  const cfg = JSON.parse(text);
  const ws = path.join(RUNS, 'openclaw-workspace');
  fs.mkdirSync(ws, { recursive: true });
  fs.copyFileSync(path.join(REPO, 'openclaw_config', 'workspace', 'AGENTS.md'), path.join(ws, 'AGENTS.md'));
  const providers = {};
  const entries = {};
  for (const id of ['cerebras', 'baseline']) {
    providers[`clawboard-${id}`] = {
      baseUrl: `${ORIGIN}/llm/${id}/v1`, apiKey: 'metered-by-clawboard', api: 'openai-completions',
      models: [{ id: 'metered', name: `ClawBoard ${id} lane`, reasoning: false, input: ['text'], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 131072, maxTokens: 8192 }],
    };
    entries[`clawboard-${id}`] = { name: `ClawBoard (${id})`, workspace: fwd(ws), model: { primary: `clawboard-${id}/metered` } };
  }
  cfg.models = { mode: 'merge', providers };
  // two agents in one roster: every request names its agent explicitly, so ownership is "explicit"
  cfg.agents = { ...(cfg.agents || {}), ownership: 'explicit', defaults: { ...(cfg.agents?.defaults || {}), workspace: fwd(ws), model: { primary: 'clawboard-cerebras/metered' } }, entries };
  cfg.gateway = { mode: 'local', port: GW.port, bind: 'loopback', auth: { mode: 'token', token: GW.token }, http: { endpoints: { chatCompletions: { enabled: true } } } };
  const file = path.join(RUNS, 'openclaw.runtime.json');
  fs.writeFileSync(file, JSON.stringify(cfg, null, 2));
  return file;
}

function gatewayInfo() {
  return { status: GW.status, readyMs: GW.readyMs, error: GW.error, startedAt: GW.startedAt || null };
}

function ensureGateway() {
  if (GW.status === 'ready' && GW.child) return Promise.resolve();
  if (GW.promise) return GW.promise;
  GW.promise = new Promise((resolve, reject) => {
    const fail = (msg) => { GW.status = 'error'; GW.error = msg; GW.promise = null; touch(); reject(new Error(msg)); };
    const entry = openclawEntry();
    if (!entry) return fail('openclaw CLI not found on PATH');
    fs.mkdirSync(RUNS, { recursive: true });
    const cfg = writeOpenClawRuntimeConfig();
    const stateDir = path.join(RUNS, '.openclaw-state');
    fs.mkdirSync(stateDir, { recursive: true });
    GW.status = 'starting';
    GW.startedAt = Date.now();
    GW.readyMs = null;
    GW.error = null;
    GW.log = '';
    touch();
    const child = spawn(process.execPath, [entry, 'gateway', '--port', String(GW.port), '--bind', 'loopback', '--auth', 'token', '--token', GW.token, '--force'], {
      cwd: RUNS, stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, OPENCLAW_CONFIG_PATH: cfg, OPENCLAW_STATE_DIR: stateDir, OPENCLAW_GATEWAY_TOKEN: GW.token, CLAWBOARD_HOME: REPO },
    });
    GW.child = child;
    let warm = false;
    let httpUpAt = 0;
    const keep = (d) => {
      const text = d.toString('utf8');
      if (text.includes('post-ready.startup-maintenance')) warm = true;
      GW.log = (GW.log + text).slice(-4000);
    };
    child.stdout.on('data', keep);
    child.stderr.on('data', keep);
    child.on('error', (e) => fail(`could not start the OpenClaw gateway: ${e.message}`));
    child.on('exit', (code) => {
      const starting = GW.status === 'starting';
      GW.child = null;
      GW.promise = null;
      GW.status = 'stopped';
      if (starting) fail(`OpenClaw gateway exited during start (code ${code}): ${GW.log.trim().split('\n').slice(-3).join(' | ').slice(0, 400)}`);
      touch();
    });
    const poll = async () => {
      if (GW.status !== 'starting') return;
      try {
        const r = await fetch(`http://127.0.0.1:${GW.port}/v1/models`, { headers: { authorization: `Bearer ${GW.token}` }, signal: AbortSignal.timeout(2500) });
        if (r.status < 500) {
          // The port opens well before the plugin registry is loaded; a turn sent in that window
          // just queues. Wait for the gateway's own "start-up finished" log line (or 2 minutes).
          httpUpAt = httpUpAt || Date.now();
          if (warm || Date.now() - httpUpAt > 120000) {
            GW.status = 'ready';
            GW.readyMs = Date.now() - GW.startedAt;
            touch();
            return resolve();
          }
        }
      } catch { /* not listening yet */ }
      if (Date.now() - GW.startedAt > 360000) { try { child.kill(); } catch { /* gone */ } return fail('OpenClaw gateway did not become ready within 6 minutes'); }
      setTimeout(poll, 1500);
    };
    setTimeout(poll, 4000);
  });
  return GW.promise;
}

function stopGateway() {
  try { GW.child?.kill(); } catch { /* already gone */ }
  GW.child = null;
  GW.promise = null;
  GW.status = 'stopped';
}
if (!globalThis.__clawboardGatewayHooks) {
  globalThis.__clawboardGatewayHooks = true;
  process.on('exit', stopGateway);
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { stopGateway(); process.exit(0); });
}

async function runOpenClaw(lane) {
  if (GW.status !== 'ready') trace(lane, { kind: 'note', text: 'Starting the OpenClaw gateway (it loads its plugins once; about a minute). The run timer starts when it is ready.' });
  await ensureGateway();
  if (runtime[lane.id].after) {
    // The Gateway prepares agent sessions one at a time. Starting both lanes at once would make the
    // second one wait behind the first one's setup, so it is released when the first lane has sent
    // its first model request. Each lane is timed from its own first model request either way.
    trace(lane, { kind: 'note', text: 'Queued: starts when the other lane has finished its harness setup.' });
    await Promise.race([runtime[lane.id].after, new Promise((r) => setTimeout(r, 90000))]);
  }
  lane.startedAt = Date.now(); // gateway warm-up and queueing are not part of the measured run
  lane.trace = lane.trace.filter((e) => e.kind !== 'note');
  touch();
  const res = await fetch(`http://127.0.0.1:${GW.port}/v1/chat/completions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${GW.token}`, 'x-openclaw-agent-id': `clawboard-${lane.id}` },
    body: JSON.stringify({ model: `openclaw/clawboard-${lane.id}`, stream: false, messages: [{ role: 'user', content: taskPrompt(lane.projectDir) }] }),
    signal: runtime[lane.id].abort.signal,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`OpenClaw gateway returned HTTP ${res.status}: ${text.slice(0, 300)}`);
  const j = JSON.parse(text);
  lane.final = j.choices?.[0]?.message?.content || '';
  lane.openclaw = { id: j.id, usage: j.usage };
  if (!lane.requests.length) throw new Error('OpenClaw answered without calling the model through the metered provider - check the agent/model routing');
}

// ---------------------------------------------------------------------------------- run lifecycle
function writeReport(lane) {
  const st = path.join(lane.projectDir, '.clawboard');
  const report = {
    run_id: lane.runId, lane: lane.id, label: lane.label,
    mode: lane.simulate ? 'SIMULATED (scripted model, synthetic timing - not a measurement)' : 'live',
    engine: lane.engine === 'openclaw' ? 'OpenClaw agent harness (Gateway /v1/chat/completions, one agent turn)' : 'built-in OpenAI-compatible loop',
    endpoint_host: lane.host, model: lane.model,
    started_at: new Date(lane.startedAt).toISOString(), finished_at: new Date(lane.endedAt).toISOString(),
    agent_loop_ms: lane.loopStartedAt ? lane.endedAt - lane.loopStartedAt : null,
    wall_ms: lane.endedAt - lane.startedAt, llm_ms: lane.metrics.llmMs, tool_ms: lane.metrics.toolMs, harness_setup_ms: lane.metrics.setupMs,
    totals: {
      llm_calls: lane.metrics.calls, provider_queue_ms: Math.round(lane.metrics.queueMs || 0), busy_retries: lane.metrics.retries || 0, busy_retry_wait_ms: lane.metrics.retryMs || 0, prompt_tokens: lane.metrics.tokensIn, cached_prompt_tokens: lane.metrics.cachedIn,
      completion_tokens: lane.metrics.tokensOut, first_token_ms: lane.metrics.ttftMs,
      tps_generation: +lane.metrics.tps.toFixed(1), tokens_estimated: lane.metrics.tokensEstimated,
    },
    definitions: {
      tps_generation: 'completion tokens / time from first streamed token to end of response, summed over all calls',
      tps_end_to_end: 'completion tokens / full request duration including time to first token',
      agent_loop_ms: 'first model request to agent sign-off: model time + tool time. This is the headline elapsed time',
      wall_ms: 'run start to agent sign-off; includes harness setup and tool time, excludes gateway warm-up and the KiCad oracle',
      harness_setup_ms: 'run start to the first model request (session, MCP server and prompt assembly inside the agent harness)',
    },
    requests: lane.requests,
    verification: {
      rung1_patch_steps: readJson(path.join(st, 'steps.json'), []),
      rung2_audit_before: lane.auditBefore, rung2_audit_after: readJson(path.join(st, 'audit.json')),
      rung3_kicad_oracle: lane.oracle || 'pending or not available',
    },
    agent_sign_off: lane.final, status: lane.status, error: lane.error,
  };
  fs.writeFileSync(path.join(RUNS, lane.id, 'report.json'), JSON.stringify(report, null, 2));
}

async function runLane(lane, after = null) {
  let firstCall;
  const firstCallPromise = new Promise((r) => { firstCall = r; });
  runtime[lane.id] = { abort: new AbortController(), firstCall, firstCallPromise, after };
  try {
    await prepareProject(lane);
    startTail(lane);
    lane.status = 'running';
    lane.startedAt = Date.now();
    touch();
    if (lane.engine === 'openclaw') await runOpenClaw(lane); else await runDirect(lane);
    lane.status = 'done';
  } catch (e) {
    lane.status = runtime[lane.id].abort.signal.aborted ? 'stopped' : 'error';
    lane.error = String(e.message || e).slice(0, 500);
    trace(lane, { kind: 'note', text: lane.error });
  }
  lane.endedAt = Date.now();
  lane.live.active = false;
  runtime[lane.id].firstCall?.();
  runtime[lane.id].tailTick?.();
  clearInterval(runtime[lane.id].tail);
  runtime[lane.id].mcp?.stop();
  touch();
  writeReport(lane);
  if (lane.status === 'done') {
    // rung 3: independent oracle, outside the timed loop
    lane.oracle = { pending: true };
    touch();
    const res = await runPy([path.join(TOOLS_DIR, 'cli.py'), 'oracle', lane.projectDir]);
    lane.oracle = res;
    touch();
    writeReport(lane);
  }
}

function stopAll() {
  for (const [id, rt] of Object.entries(runtime)) {
    try { rt.abort?.abort(); } catch { /* noop */ }
    try { rt.child?.kill(); } catch { /* noop */ }
    rt.mcp?.stop();
    clearInterval(rt.tail);
    delete runtime[id];
  }
}

async function startRun({ mode = 'single', simulate = false, engine = 'auto' }) {
  if (Object.values(state.lanes).some((l) => l.status === 'running' || l.status === 'starting')) throw new Error('a run is already in progress');
  const defs = laneDefs();
  const conf = publicConfig();
  if (!conf.sampleReady) throw new Error('sample_board is missing - run: python pcb-tools/make_sample_board.py');
  const ids = mode === 'benchmark' ? ['cerebras', 'baseline'] : ['cerebras'];
  if (!simulate) {
    for (const id of ids) {
      if (!conf.lanes[id].ready) throw new Error(`${defs[id].label}: endpoint not configured (set ${id === 'cerebras' ? 'CEREBRAS_API_KEY' : 'BASELINE_BASE_URL, BASELINE_MODEL and BASELINE_API_KEY'} in .env) - or use Simulated mode`);
    }
  }
  const eng = engine === 'auto' ? conf.defaultEngine : engine;
  if (eng === 'openclaw' && !(conf.openclaw.found && conf.openclaw.nodeOk)) throw new Error('OpenClaw is not runnable here (needs the openclaw CLI and Node >= 24.16). Choose the built-in loop or run setup.');
  stopAll();
  state.runId = randomUUID().slice(0, 8);
  state.mode = mode;
  state.simulate = simulate;
  state.engine = eng;
  state.startedAt = Date.now();
  state.lanes = {};
  for (const id of ids) state.lanes[id] = newLane(defs[id], simulate, eng, state.runId);
  touch();
  let prev = null;
  for (const id of ids) {
    runLane(state.lanes[id], eng === 'openclaw' ? prev : null);
    prev = runtime[id].firstCallPromise;
  }
  return { runId: state.runId };
}

async function resetAll() {
  stopAll();
  state.runId = null;
  state.mode = 'idle';
  state.lanes = {};
  state.idleSeq = (state.idleSeq || 0) + 1;
  touch();
  // show the pristine (broken) sample in the viewer
  const lane = newLane(laneDefs().cerebras, false, null, null);
  try {
    await prepareProject(lane);
    await runPy([path.join(TOOLS_DIR, 'cli.py'), 'audit', lane.projectDir]);
  } catch { /* viewer just stays empty */ }
  state.idleSeq += 1;
  touch();
}

// ---------------------------------------------------------------------------------- HTTP
function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}
function json(res, code, obj) {
  res.writeHead(code, { 'content-type': 'application/json', 'cache-control': 'no-store' });
  res.end(JSON.stringify(obj));
}

// OpenAI-compatible pass-through used by the OpenClaw engine: forwards to the lane's real endpoint
// and meters the stream on the way through.
async function handleProxy(req, res, laneId, rest) {
  const lane = state.lanes[laneId];
  if (!lane) return json(res, 404, { error: { message: `no active lane '${laneId}'` } });
  const up = upstreamFor(lane);
  const raw = req.method === 'POST' ? await readBody(req) : undefined;
  let body = raw;
  const isChat = rest.endsWith('/chat/completions');
  if (isChat) {
    try {
      const j = JSON.parse(raw);
      if (j.stream) j.stream_options = { ...(j.stream_options || {}), include_usage: true };
      j.model = up.model;
      body = JSON.stringify({ ...j, ...up.extraBody });
    } catch { /* forward untouched */ }
  }
  const init = { method: req.method, headers: { 'content-type': 'application/json', authorization: `Bearer ${up.key}`, 'user-agent': 'clawboard-dashboard/1.0' }, body };
  const { res: upstream, t0 } = isChat ? await fetchUpstream(lane, `${up.url}${rest}`, init) : { res: await fetch(`${up.url}${rest}`, init), t0: Date.now() };
  res.writeHead(upstream.status, { 'content-type': upstream.headers.get('content-type') || 'application/json', 'cache-control': 'no-store' });
  if (!isChat) return res.end(Buffer.from(await upstream.arrayBuffer()));
  try { await meterResponse(lane, upstream, t0, (chunk) => res.write(chunk)); } catch { /* error body already forwarded */ }
  res.end();
}

export function attach(middlewares, opts = {}) {
  if (opts.origin) ORIGIN = opts.origin;
  if (Object.keys(state.lanes).length === 0) setTimeout(resetAll, 50);
  const oc = openclawInfo();
  if (oc.found && oc.nodeOk && !process.env.CLAWBOARD_NO_PREWARM) setTimeout(() => ensureGateway().catch(() => {}), 400);
  middlewares.use(async (req, res, next) => {
    const url = new URL(req.url, 'http://localhost');
    const p = url.pathname;
    try {
      if (p === '/api/events') {
        res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', connection: 'keep-alive', 'x-accel-buffering': 'no' });
        res.write(`data: ${snapshot()}\n\n`);
        clients.add(res);
        const ka = setInterval(() => res.write(': keep-alive\n\n'), 15000);
        req.on('close', () => { clearInterval(ka); clients.delete(res); });
        return;
      }
      if (p === '/api/config') return json(res, 200, publicConfig());
      if (p === '/api/state') return json(res, 200, state);
      if (p === '/api/run' && req.method === 'POST') {
        const body = JSON.parse((await readBody(req)) || '{}');
        try { return json(res, 200, await startRun(body)); } catch (e) { return json(res, 400, { error: e.message }); }
      }
      if (p === '/api/stop' && req.method === 'POST') {
        stopAll();
        for (const l of Object.values(state.lanes)) if (l.status === 'running') { l.status = 'stopped'; l.endedAt = Date.now(); }
        touch();
        return json(res, 200, { ok: true });
      }
      if (p === '/api/reset' && req.method === 'POST') { await resetAll(); return json(res, 200, { ok: true }); }
      let m = p.match(/^\/api\/svg\/([a-z]+)\/(board|schematic)$/);
      if (m) {
        const file = path.join(RUNS, m[1], 'board', '.clawboard', `${m[2]}_preview.svg`);
        let text = '';
        try { text = fs.readFileSync(file, 'utf8'); } catch { /* not rendered yet */ }
        // the tools overwrite this file in place: never serve a half-written frame
        if (text.trimEnd().endsWith('</svg>')) lastGoodSvg.set(file, text); else text = lastGoodSvg.get(file) || '';
        if (!text) { res.writeHead(404); return res.end(); }
        res.writeHead(200, { 'content-type': 'image/svg+xml', 'cache-control': 'no-store' });
        return res.end(text);
      }
      m = p.match(/^\/api\/report\/([a-z]+)$/);
      if (m) {
        const file = path.join(RUNS, m[1], 'report.json');
        if (!fs.existsSync(file)) return json(res, 404, { error: 'no report yet' });
        res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
        return res.end(fs.readFileSync(file));
      }
      m = p.match(/^\/llm\/([a-z]+)\/v1(\/.*)$/);
      if (m) return await handleProxy(req, res, m[1], m[2]);
      m = p.match(/^\/mock\/([a-z]+)\/v1(\/.*)$/);
      if (m) return await handleMock(req, res, m[1], m[2], { readBody, runPy, cli: path.join(TOOLS_DIR, 'cli.py') });
    } catch (e) {
      if (!res.headersSent) return json(res, 500, { error: String(e.message || e) });
      return res.end();
    }
    next();
  });
}
