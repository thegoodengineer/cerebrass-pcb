// SIMULATED model endpoint (OpenAI-compatible, streaming).
//
// Lets you rehearse the dashboard and exercise the whole agent loop without an API key.
// The "model" is a script that follows the same workflow the real agent is asked to follow,
// and the timing is synthetic: nothing produced in this mode is a benchmark result. The UI
// and every report label it SIMULATED.
const PROFILES = {
  fast: { ttftMs: 170, tps: 1700 },
  slow: { ttftMs: 1100, tps: 48 },
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const tok = (s) => Math.max(1, Math.ceil((s || '').length / 4));

function textOf(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return content.map((c) => (typeof c === 'string' ? c : c.text || '')).join('');
  return '';
}

function findProjectDir(messages) {
  for (const m of messages) {
    const t = textOf(m.content);
    const hit = t.match(/project_dir:\s*(.+)/);
    if (hit) return hit[1].trim().replace(/^["']|["']$/g, '');
  }
  return null;
}

function lastToolJson(messages) {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== 'tool') continue;
    try { return JSON.parse(textOf(messages[i].content)); } catch { return null; }
  }
  return null;
}

let lastRequest = null; // shape of the most recent request, for debugging harness integrations

export async function handleMock(req, res, profileName, rest, ctx) {
  const profile = PROFILES[profileName] || PROFILES.fast;
  if (rest === '/debug/last') {
    res.writeHead(200, { 'content-type': 'application/json' });
    return res.end(JSON.stringify(lastRequest));
  }
  if (!rest.endsWith('/chat/completions')) {
    res.writeHead(200, { 'content-type': 'application/json' });
    return res.end(JSON.stringify({ object: 'list', data: [{ id: 'simulated', object: 'model' }] }));
  }
  const body = JSON.parse(await ctx.readBody(req));
  const messages = body.messages || [];
  const toolNames = (body.tools || []).map((t) => t.function?.name).filter(Boolean);
  const dir = findProjectDir(messages);
  const step = messages.filter((m) => m.role === 'tool').length;
  const last = lastToolJson(messages);
  const arrivals = step === 0 ? [] : (lastRequest?.arrivals || []);
  arrivals.push(Date.now());
  lastRequest = {
    arrivals, keys: Object.keys(body), model: body.model, stream: body.stream, toolNames,
    messages: messages.map((m) => ({ role: m.role, chars: textOf(m.content).length, head: textOf(m.content).slice(0, 160), tool_calls: m.tool_calls?.map((c) => c.function?.name), tool_call_id: m.tool_call_id })),
    toolSchemaChars: JSON.stringify(body.tools || []).length,
    sampleTool: (body.tools || []).find((t) => /kicad|pcb|clawboard|mcp/i.test(JSON.stringify(t).slice(0, 400))) || null,
  };
  const pick = (base) => toolNames.find((n) => n === base || n.endsWith(`__${base}`) || n.endsWith(`.${base}`)) || base;

  let say = '';
  let call = null;
  if (step === 0) {
    say = 'Starting with a structural read of the project so I know the parts, nets and what is still unrouted.';
    call = { name: pick('inspect_kicad_project'), args: { project_dir: dir } };
  } else if (step === 1) {
    say = 'ESP32-S3 module, USB-C input, AMS1117 regulator, BME280 on I2C and a DRV8833 motor driver. Running the JLCPCB DFM and ERC rule set.';
    call = { name: pick('run_dfm_and_erc_audit'), args: { project_dir: dir, fab_house: 'jlcpcb' } };
  } else if (last && Array.isArray(last.issues) && last.issues.length) {
    const plan = await ctx.runPy([ctx.cli, 'plan', dir]);
    const ops = plan.operations || [];
    if (!ops.length) {
      say = `FAIL - the fix planner returned no operations (${plan.error || 'unknown reason'}). Nothing was changed.`;
    } else {
      say = `${last.summary?.errors ?? '?'} errors. Splitting the CC pins onto their own 5.1k pull-downs, adding the missing 100nF at the MCU supply pin and 4.7k I2C pull-ups, ` +
        'moving the stray decoupler next to the sensor, widening VBUS to 0.5 mm, clearing the via and routing UART_TX - all in one patch.';
      call = { name: pick('apply_pcb_patch'), args: { project_dir: dir, operations: ops } };
    }
  } else if (step < 8 && last && last.audit && last.audit.errors > 0) {
    say = 'Some issues remain after the patch; re-running the audit for full detail.';
    call = { name: pick('run_dfm_and_erc_audit'), args: { project_dir: dir, fab_house: 'jlcpcb' } };
  } else if (last && last.audit && last.audit.pass) {
    // sign-off is built from the tool result, never assumed
    const fixed = (last.results || []).filter((r) => r.ok && r.resolved?.length).map((r) => `- ${r.resolved.join(', ')}: ${r.result.split(';')[0]}`);
    say = `PASS - audit reports ${last.audit.errors} errors, ${last.audit.warnings} warnings after ${last.applied} operations\n${fixed.slice(0, 8).join('\n')}\n` +
      'Not verified here: KiCad DRC/ERC oracle (runs after the agent).';
  } else if (last && last.pass === true) {
    say = 'PASS - the audit reports no errors. Not verified here: KiCad DRC/ERC oracle (runs after the agent).';
  } else {
    say = `FAIL - could not confirm a clean audit. Last tool result: ${JSON.stringify(last || {}).slice(0, 200)}`;
  }

  const id = `chatcmpl-sim-${Date.now().toString(36)}`;
  const base = { id, object: 'chat.completion.chunk', created: Math.floor(Date.now() / 1000), model: body.model || 'simulated' };
  res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', connection: 'keep-alive' });
  const send = (delta, finish = null, extra = {}) => res.write(`data: ${JSON.stringify({ ...base, choices: [{ index: 0, delta, finish_reason: finish }], ...extra })}\n\n`);
  let out = 0;
  const pace = async (text) => {
    const n = tok(text);
    out += n;
    await sleep((n / profile.tps) * 1000);
  };
  await sleep(profile.ttftMs);
  send({ role: 'assistant', content: '' });
  for (let i = 0; i < say.length; i += 14) {
    const piece = say.slice(i, i + 14);
    await pace(piece);
    send({ content: piece });
  }
  if (call) {
    const args = JSON.stringify(call.args);
    send({ tool_calls: [{ index: 0, id: `call_sim_${step}`, type: 'function', function: { name: call.name, arguments: '' } }] });
    for (let i = 0; i < args.length; i += 22) {
      const piece = args.slice(i, i + 22);
      await pace(piece);
      send({ tool_calls: [{ index: 0, function: { arguments: piece } }] });
    }
    send({}, 'tool_calls');
  } else {
    send({}, 'stop');
  }
  const promptTokens = tok(JSON.stringify(messages)) + tok(JSON.stringify(body.tools || []));
  const staticPrefix = tok(textOf(messages[0]?.content)) + tok(JSON.stringify(body.tools || []));
  res.write(`data: ${JSON.stringify({ ...base, choices: [], usage: {
    prompt_tokens: promptTokens, completion_tokens: out, total_tokens: promptTokens + out,
    prompt_tokens_details: { cached_tokens: step === 0 ? 0 : Math.floor(staticPrefix / 128) * 128 },
  } })}\n\n`);
  res.write('data: [DONE]\n\n');
  res.end();
}
