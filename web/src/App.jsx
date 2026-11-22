import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const fmtMs = (ms) => {
  if (ms == null) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
};
const fmtInt = (n) => (n == null ? '—' : Math.round(n).toLocaleString('en-US'));
const short = (name) => (name || '').split('__').pop();

function useServerState() {
  const [state, setState] = useState({ mode: 'idle', lanes: {} });
  const [config, setConfig] = useState(null);
  const [online, setOnline] = useState(false);
  useEffect(() => {
    fetch('/api/config').then((r) => r.json()).then(setConfig).catch(() => {});
    const es = new EventSource('/api/events');
    es.onopen = () => setOnline(true);
    es.onerror = () => setOnline(false);
    es.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'state') setState(msg.state);
    };
    return () => es.close();
  }, []);
  return { state, config, online };
}

function useNow(active) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return undefined;
    let raf;
    const loop = () => { setNow(Date.now()); raf = requestAnimationFrame(loop); };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [active]);
  return now;
}

// ------------------------------------------------------------------------------------ board viewer
function Viewer({ laneId, seq, compact }) {
  const [tab, setTab] = useState('board');
  const [view, setView] = useState('top');
  const [svg, setSvg] = useState('');
  const [tip, setTip] = useState(null);
  const [xf, setXf] = useState({ k: 1, x: 0, y: 0 });
  const host = useRef(null);
  const drag = useRef(null);

  useEffect(() => {
    let dead = false;
    fetch(`/api/svg/${laneId}/${tab}?v=${seq}`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.text() : ''))
      .then((t) => { if (!dead && t) setSvg(t); });
    return () => { dead = true; };
  }, [laneId, tab, seq]);

  useEffect(() => {
    const el = host.current?.querySelector('svg');
    if (el && tab === 'board') el.setAttribute('data-view', view);
  }, [svg, view, tab]);

  const onWheel = useCallback((e) => {
    const rect = host.current.getBoundingClientRect();
    const px = e.clientX - rect.left - rect.width / 2;
    const py = e.clientY - rect.top - rect.height / 2;
    setXf((cur) => {
      const k = Math.min(12, Math.max(1, cur.k * (e.deltaY < 0 ? 1.18 : 1 / 1.18)));
      const r = k / cur.k;
      return k === 1 ? { k: 1, x: 0, y: 0 } : { k, x: px - (px - cur.x) * r, y: py - (py - cur.y) * r };
    });
  }, []);

  const onMove = (e) => {
    if (drag.current) {
      setXf((cur) => ({ ...cur, x: drag.current.x + e.clientX - drag.current.cx, y: drag.current.y + e.clientY - drag.current.cy }));
      return;
    }
    const t = e.target.closest?.('[data-net],[data-issue]');
    const root = host.current;
    root.querySelectorAll('.net-on').forEach((n) => n.classList.remove('net-on'));
    if (!t) { setTip(null); return; }
    const net = t.getAttribute('data-net');
    if (net) root.querySelectorAll(`[data-net="${CSS.escape(net)}"]`).forEach((n) => n.classList.add('net-on'));
    const rect = root.getBoundingClientRect();
    const title = t.querySelector('title')?.textContent || net || t.getAttribute('data-issue');
    setTip({ x: e.clientX - rect.left + 12, y: e.clientY - rect.top + 12, text: title });
  };

  return (
    <div className={`viewer ${compact ? 'compact' : ''}`}>
      <div className="viewer-bar">
        <div className="seg">
          <button className={tab === 'board' ? 'on' : ''} onClick={() => setTab('board')}>PCB</button>
          <button className={tab === 'schematic' ? 'on' : ''} onClick={() => setTab('schematic')}>Schematic</button>
        </div>
        {tab === 'board' && (
          <div className="seg">
            {['top', 'both', 'bottom'].map((v) => (
              <button key={v} className={view === v ? 'on' : ''} onClick={() => setView(v)}>{v === 'top' ? 'F.Cu' : v === 'bottom' ? 'B.Cu' : 'Both'}</button>
            ))}
          </div>
        )}
        <div className="legend">
          <span><i className="sw fcu" />F.Cu</span><span><i className="sw bcu" />B.Cu</span>
          <span><i className="sw fix" />patched</span><span><i className="sw err" />DFM error</span>
        </div>
        <button className="ghost" onClick={() => setXf({ k: 1, x: 0, y: 0 })} title="Reset zoom">Fit</button>
      </div>
      <div
        className="canvas"
        ref={host}
        onWheel={onWheel}
        onMouseDown={(e) => { drag.current = { cx: e.clientX, cy: e.clientY, x: xf.x, y: xf.y }; }}
        onMouseUp={() => { drag.current = null; }}
        onMouseLeave={() => { drag.current = null; setTip(null); }}
        onMouseMove={onMove}
      >
        <div className="svg-wrap" style={{ transform: `translate(${xf.x}px, ${xf.y}px) scale(${xf.k})` }} dangerouslySetInnerHTML={{ __html: svg }} />
        {!svg && <div className="empty">Waiting for the first render…</div>}
        {tip && <div className="tip" style={{ left: tip.x, top: tip.y }}>{tip.text}</div>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------ lane pieces
function Metric({ label, value, unit, big, tone, hint }) {
  return (
    <div className={`metric ${big ? 'big' : ''} ${tone || ''}`} title={hint}>
      <div className="m-label">{label}</div>
      <div className="m-value">{value}{unit && <span className="m-unit">{unit}</span>}</div>
    </div>
  );
}

function StepChart({ lane }) {
  const before = lane.auditBefore?.summary?.errors;
  const pts = useMemo(() => {
    if (before == null) return [];
    return [before, ...lane.steps.map((s) => s.after)];
  }, [before, lane.steps]);
  if (pts.length === 0) return <div className="stepchart empty-chart">Error count per patch step appears here</div>;
  const max = Math.max(...pts, 1);
  return (
    <div className="stepchart">
      {pts.map((v, i) => (
        <div className="bar-col" key={i} title={i === 0 ? `before: ${v} errors` : `${lane.steps[i - 1].op}: ${lane.steps[i - 1].before} → ${v}`}>
          <div className="bar-num">{v}</div>
          <div className={`bar ${v === 0 ? 'zero' : ''}`} style={{ height: `${Math.max(4, (v / max) * 100)}%` }} />
          <div className="bar-x">{i === 0 ? 'start' : i}</div>
        </div>
      ))}
    </div>
  );
}

function Ladder({ lane }) {
  const applied = lane.steps.length;
  const a = lane.audit;
  const o = lane.oracle;
  let r3 = { cls: 'idle', text: 'runs after the agent signs off' };
  if (o?.pending) r3 = { cls: 'run', text: 'KiCad DRC + ERC running…' };
  else if (o && o.available === false) r3 = { cls: 'idle', text: 'kicad-cli not installed' };
  else if (o?.drc) {
    const e = (o.drc.errors || 0) + (o.erc?.errors || 0);
    const ok = e === 0 && (o.drc.unconnected_items || 0) === 0;
    r3 = { cls: ok ? 'ok' : 'bad', text: `KiCad ${o.version || ''}: ${o.drc.errors} DRC errors, ${o.drc.unconnected_items} unconnected, ${o.erc?.errors ?? '—'} ERC errors` };
  } else if (o?.error) r3 = { cls: 'bad', text: o.error };
  return (
    <div className="ladder">
      <div className={`rung ${applied ? 'ok' : 'idle'}`}><b>1</b><span>Patch post-conditions</span><em>{applied ? `${applied} operations, every new primitive clearance-checked` : 'no patch yet'}</em></div>
      <div className={`rung ${!a ? 'idle' : a.pass ? 'ok' : 'bad'}`}><b>2</b><span>ClawBoard DFM / ERC audit</span><em>{a ? `${a.pass ? 'PASS' : 'FAIL'} · ${a.summary.errors} errors, ${a.summary.warnings} warnings · ${a.rules} rules` : 'not run yet'}</em></div>
      <div className={`rung ${r3.cls}`}><b>3</b><span>KiCad oracle (independent)</span><em>{r3.text}</em></div>
    </div>
  );
}

function Issues({ lane }) {
  const before = lane.auditBefore?.issues || [];
  const left = new Set(lane.audit?.remaining || []);
  if (!before.length) return null;
  return (
    <div className="issues">
      {before.map((i) => {
        const fixed = lane.audit && !left.has(i.id);
        return <span key={i.id} className={`chip ${fixed ? 'fixed' : i.severity}`} title={i.title}>{fixed ? '✓ ' : ''}{i.id}</span>;
      })}
    </div>
  );
}

function Trace({ lane }) {
  const ref = useRef(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [lane.trace.length, lane.live.active]);
  return (
    <div className="trace" ref={ref}>
      {lane.trace.map((e, i) => {
        if (e.kind === 'llm') {
          return (
            <div className="row llm" key={i}>
              <span className="t">{fmtMs(e.t)}</span>
              <span className="tag">LLM #{e.n}</span>
              <span className="txt">{e.text}</span>
              <span className="num">{fmtInt(e.tokens)} tok · <b>{fmtInt(e.tps)} tok/s</b> · first token {fmtMs(e.ttft)} · {fmtMs(e.ms)}{e.cached ? ` · ${fmtInt(e.cached)} cached` : ''}</span>
            </div>
          );
        }
        if (e.kind === 'tool') {
          return (
            <div className={`row tool ${e.status}`} key={i}>
              <span className="t">{fmtMs(e.t)}</span>
              <span className="tag">TOOL</span>
              <span className="txt"><code>{short(e.name)}</code>{e.text ? ` — ${e.text}` : ''}</span>
              <span className="num">{e.status === 'running' ? 'running…' : fmtMs(e.ms)}</span>
            </div>
          );
        }
        if (e.kind === 'op') {
          return (
            <div className={`row op ${e.ok ? '' : 'bad'}`} key={i}>
              <span className="t" />
              <span className="tag">{e.ok ? '✓' : '✗'}</span>
              <span className="txt">{e.text}{e.resolved?.length ? <i className="res"> resolves {e.resolved.join(', ')}</i> : null}</span>
              <span className="num">{e.errors != null ? `${e.errors} errors left` : ''}</span>
            </div>
          );
        }
        return <div className="row note" key={i}><span className="t">{fmtMs(e.t)}</span><span className="tag">!</span><span className="txt">{e.text}</span></div>;
      })}
      {lane.live.active && (
        <div className="row live"><span className="t" /><span className="tag pulse">LLM</span><span className="txt mono">{lane.live.text.slice(-220) || 'waiting for first token…'}<i className="caret" /></span></div>
      )}
      {lane.status === 'done' && lane.final && <pre className="signoff">{lane.final}</pre>}
    </div>
  );
}

function Lane({ lane, compare, now }) {
  const running = lane.status === 'running' || lane.status === 'starting';
  const loop = lane.loopStartedAt ? (lane.endedAt || now) - lane.loopStartedAt : null;
  const m = lane.metrics;
  const liveLlm = lane.live.active ? now - lane.live.startedAt : 0;
  const tps = lane.live.active && m.liveTps ? m.liveTps : m.tps;
  return (
    <section className={`lane ${lane.id} ${lane.status}`}>
      <header className="lane-head">
        <div>
          <h2>{lane.label}<span className={`status ${lane.status}`}>{lane.status}</span></h2>
          <div className="sub"><code>{lane.model}</code> · {lane.host} · {lane.engine === 'openclaw' ? 'OpenClaw harness' : 'built-in agent loop'}</div>
        </div>
        {lane.status !== 'idle' && lane.endedAt && <a className="report" href={`/api/report/${lane.id}`} target="_blank" rel="noreferrer">report.json ↗</a>}
      </header>
      <div className="metrics">
        <Metric big tone="accent" label="Tokens / sec" value={fmtInt(tps)} hint="completion tokens / generation time (first token to end), summed over calls" />
        <Metric big label="Total elapsed" value={loop == null ? (running ? 'setup' : '—') : (loop / 1000).toFixed(running ? 1 : 2)} unit={loop == null ? '' : 's'} hint="first model request to agent sign-off: model time + tool time. Harness setup is shown separately." />
        <Metric label="LLM time" value={fmtMs(m.llmMs + liveLlm)} tone="llm" hint="sum of all model requests" />
        <Metric label="Tool time" value={fmtMs(m.toolMs)} hint="PCB tools - identical work on every lane" />
        <Metric label="First token" value={fmtMs(m.ttftMs)} />
        <Metric label="Tokens out / in" value={`${fmtInt(m.tokensOut)} / ${fmtInt(m.tokensIn)}`} hint={m.tokensEstimated ? 'endpoint sent no usage: estimated from characters' : 'from API usage'} />
        <Metric label="Cached input" value={m.tokensIn ? `${Math.round((m.cachedIn / m.tokensIn) * 100)}%` : '—'} hint="share of prompt tokens served from the provider prompt cache" />
        <Metric label="LLM calls" value={m.calls} />
        <Metric label="Harness setup" value={fmtMs(m.setupMs)} hint="run start to the first model request: session, MCP server and prompt assembly inside the agent harness" />
      </div>
      {lane.error && <div className="error-box">{lane.error}</div>}
      <div className={`lane-body ${compare ? 'stack' : 'split'}`}>
        <div className="g-view"><Viewer laneId={lane.id} seq={lane.svgSeq} compact={compare} /></div>
        <div className="g-side">
          <div className="panel"><h3>Errors after each patch step <small>measured</small></h3><StepChart lane={lane} /></div>
          <div className="panel"><h3>Verification ladder <small>reported separately</small></h3><Ladder lane={lane} /></div>
        </div>
        <div className="g-issues"><Issues lane={lane} /></div>
        <div className="g-trace panel grow"><h3>Agent execution trace</h3><Trace lane={lane} /></div>
      </div>
    </section>
  );
}

function Verdict({ lanes }) {
  const c = lanes.cerebras;
  const b = lanes.baseline;
  if (!c || !b || !c.endedAt || !b.endedAt || c.status !== 'done' || b.status !== 'done') return null;
  const llm = b.metrics.llmMs / Math.max(c.metrics.llmMs, 1);
  const wall = (b.endedAt - (b.loopStartedAt || b.startedAt)) / Math.max(c.endedAt - (c.loopStartedAt || c.startedAt), 1);
  const tps = c.metrics.tps / Math.max(b.metrics.tps, 0.001);
  const ok = (l) => (l.audit?.pass ? 'audit PASS' : `audit FAIL (${l.audit?.summary?.errors ?? '?'} errors)`);
  return (
    <div className="verdict">
      <div className="v-main"><b>{llm.toFixed(1)}×</b> less time waiting on the model</div>
      <div className="v-sub">
        <span>{tps.toFixed(1)}× tokens/sec</span><span>{wall.toFixed(1)}× faster end to end</span>
        <span>Cerebras: {ok(c)}</span><span>Baseline: {ok(b)}</span>
      </div>
    </div>
  );
}

function GatewayBadge({ gw, show }) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (gw?.status !== 'starting') return undefined;
    const id = setInterval(() => tick((n) => n + 1), 500);
    return () => clearInterval(id);
  }, [gw?.status]);
  if (!show || !gw) return null;
  let text = 'OpenClaw gateway: stopped';
  let cls = '';
  if (gw.status === 'starting') { text = `OpenClaw gateway: warming up ${Math.round((Date.now() - gw.startedAt) / 1000)} s…`; cls = 'warn'; }
  else if (gw.status === 'ready') { text = `OpenClaw gateway: ready`; cls = 'livetag'; }
  else if (gw.status === 'error') { text = `OpenClaw gateway: ${gw.error}`; cls = 'errtxt'; }
  return <><span className="sep" /><span className={cls} title={gw.readyMs ? `started in ${(gw.readyMs / 1000).toFixed(0)} s` : ''}>{text}</span></>;
}

// ------------------------------------------------------------------------------------ app
export default function App() {
  const { state, config, online } = useServerState();
  const [simulate, setSimulate] = useState(false);
  const [engine, setEngine] = useState('auto');
  const [err, setErr] = useState('');
  const lanes = Object.values(state.lanes || {});
  const busy = lanes.some((l) => l.status === 'running' || l.status === 'starting');
  const now = useNow(busy);
  const compare = state.mode === 'benchmark';

  useEffect(() => {
    if (config && !config.lanes.cerebras.ready) setSimulate(true);
  }, [config]);

  const post = async (url, body) => {
    setErr('');
    const r = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) setErr(j.error || `request failed (${r.status})`);
  };
  const run = (mode) => post('/api/run', { mode, simulate, engine });

  const sim = lanes.length ? state.simulate : simulate;
  const engineLabel = (state.engine || (engine === 'auto' ? config?.defaultEngine : engine)) === 'openclaw' ? 'OpenClaw harness' : 'built-in loop';

  return (
    <div className={`app ${sim ? 'is-sim' : ''}`}>
      <header className="top">
        <div className="brand">
          <div className="logo" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M7 22V10h8l4 4h6" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" /><circle cx="25" cy="14" r="2.6" /><circle cx="7" cy="22" r="2.6" /></svg></div>
          <div>
            <h1>ClawBoard</h1>
            <p>Autonomous PCB review · OpenClaw + Kimi K3.1 Pro on Cerebras</p>
          </div>
        </div>
        <div className="controls">
          <label className="toggle" title="Scripted model with synthetic timing, for rehearsing without an API key. Never a benchmark result.">
            <input type="checkbox" checked={simulate} disabled={busy} onChange={(e) => setSimulate(e.target.checked)} /><span>Simulated</span>
          </label>
          <select value={engine} disabled={busy} onChange={(e) => setEngine(e.target.value)} title="Agent harness">
            <option value="auto">Engine: auto ({config?.defaultEngine === 'openclaw' ? 'OpenClaw' : 'built-in'})</option>
            <option value="openclaw" disabled={!(config?.openclaw.found && config?.openclaw.nodeOk)}>OpenClaw harness</option>
            <option value="direct">Built-in loop</option>
          </select>
          <button className="btn" disabled={busy} onClick={() => run('single')}>▶ Run audit &amp; fix</button>
          <button className="btn hero" disabled={busy} onClick={() => run('benchmark')}>⚡ Cerebras vs. {config?.lanes.baseline.ready ? config.lanes.baseline.label : 'Standard GPU'}</button>
          {busy ? <button className="btn ghost" onClick={() => post('/api/stop')}>Stop</button> : <button className="btn ghost" onClick={() => post('/api/reset')}>Reset</button>}
        </div>
      </header>

      <div className="strip">
        <span className={`dot ${online ? 'on' : ''}`} />{online ? 'live' : 'reconnecting…'}
        <span className="sep" />{sim ? <b className="simtag">SIMULATED — scripted model, synthetic timing</b> : <b className="livetag">LIVE endpoints</b>}
        <span className="sep" />{engineLabel}
        {config && !sim && <><span className="sep" />Cerebras: {config.lanes.cerebras.ready ? `${config.lanes.cerebras.model}` : 'CEREBRAS_API_KEY not set'}</>}
        {config && compare && !sim && <><span className="sep" />Baseline: {config.lanes.baseline.ready ? `${config.lanes.baseline.model} @ ${config.lanes.baseline.host}` : 'not configured'}</>}
        {config && !config.openclaw.found && <><span className="sep" /><span className="warn">openclaw CLI not found — using the built-in loop</span></>}
        <GatewayBadge gw={state.gateway} show={config?.openclaw.found && config?.openclaw.nodeOk} now={now} />
        {config && config.openclaw.found && !config.openclaw.nodeOk && <><span className="sep" /><span className="warn">OpenClaw needs Node ≥ 24.16 (this is {config.openclaw.node})</span></>}
        {err && <><span className="sep" /><span className="errtxt">{err}</span></>}
      </div>

      {compare && <Verdict lanes={state.lanes} />}

      <main className={`lanes ${compare ? 'two' : 'one'}`}>
        {lanes.length === 0 && (
          <section className="lane idle">
            <header className="lane-head"><div><h2>sample_board <span className="status idle">as designed</span></h2><div className="sub">ESP32-S3 · USB-C · BME280 · DRV8833 — six classic bugs, marked in red</div></div></header>
            <div className="lane-body intro"><div className="g-view"><Viewer laneId="cerebras" seq={`idle-${state.idleSeq || 0}`} /></div>
              <div className="g-trace"><div className="panel grow hint">
                <h3>How this works</h3>
                <ol>
                  <li><b>Run audit &amp; fix</b> — the agent inspects the KiCad project, runs the DFM/ERC audit, patches schematic and board in one call, and re-verifies.</li>
                  <li><b>Cerebras vs. Standard GPU</b> — the same task on two endpoints side by side, with live tokens/sec and timers.</li>
                  <li>Patched copper glows green; open DFM errors pulse red. Scroll to zoom, drag to pan, hover a net to trace it.</li>
                </ol>
                <p className="fine">Tool time is identical on every lane; the gap you see is model inference. Every number links to a per-request report.</p>
              </div></div></div>
          </section>
        )}
        {lanes.map((l) => <Lane key={l.id} lane={l} compare={compare} now={now} />)}
      </main>
      {sim && <div className="watermark" aria-hidden="true">SIMULATED</div>}
    </div>
  );
}
