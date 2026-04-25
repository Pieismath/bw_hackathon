// Paper Replication Machine — MVP UI controller.
// Fetches /api/demo or runs /api/extract on uploaded PDFs, then renders the
// bundle across Overview / Spec / Verification / Critique / Backtest /
// Robustness / Diagnosis / Provenance.

const state = {
  bundle: null,
  activeTab: 'overview',
  // Pristine spec from A1 (or demo). renderSpec uses originalSpec ⊕ dial overrides.
  originalSpec: null,
  // Cached papers list (for the Run config paper selector).
  papers: [],
  selectedPaperId: null,
  running: false,
};

// All ReplicationSpec field paths the dial form can override. Used by
// applyDials() to merge form state into a spec clone, and by renderSpec()
// to mark fields that diverge from the original as "(overridden)".
const DIAL_FIELD_PATHS = [
  'universe.min_price',
  'universe.exchanges',
  'start_date',
  'end_date',
  'signal.lookback_months',
  'signal.skip_months',
  'rebalance.holding_period_months',
  'rebalance.execution_lag_days',
  'portfolio.n_buckets',
  'portfolio.long_bucket',
  'portfolio.short_bucket',
  'portfolio.weighting',
  'portfolio.use_nyse_breakpoints',
];

// ---------- helpers ----------

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function fmtPct(x, digits = 2) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  return (x * 100).toFixed(digits) + '%';
}
function fmtNum(x, digits = 2) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  return Number(x).toFixed(digits);
}
function fmtDate(s) {
  return s ? String(s) : '—';
}
function nowHHMMSS() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, '0').slice(0, 3)}`;
}
function el(tag, attrs = {}, children = []) {
  const n = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  });
  (Array.isArray(children) ? children : [children]).forEach((c) => {
    if (c == null) return;
    n.append(c.nodeType ? c : document.createTextNode(c));
  });
  return n;
}

// ---------- dial form state ----------

// Snapshot the current dial-form values. The result is the source of truth
// for both the spec override merge and the transaction_cost_bps request kwarg.
function readDialForm() {
  const exchangesEl = $('#dial-exchanges');
  const exchanges = exchangesEl
    ? Array.from(exchangesEl.querySelectorAll('input[type="checkbox"]:checked')).map((c) => c.value)
    : [];
  const weightingEl = document.querySelector('input[name="weighting"]:checked');
  return {
    min_price: numOrNull($('#dial-min-price').value),
    start_date: $('#dial-start-date').value || null,
    end_date: $('#dial-end-date').value || null,
    exchanges,
    lookback_months: numOrNull($('#dial-lookback').value),
    skip_months: numOrNull($('#dial-skip').value),
    holding_period_months: numOrNull($('#dial-holding').value),
    n_buckets: numOrNull($('#dial-buckets').value),
    weighting: weightingEl ? weightingEl.value : 'equal',
    long_bucket: numOrNull($('#dial-long-bucket').value),
    short_bucket: numOrNull($('#dial-short-bucket').value),
    transaction_cost_bps: numOrNull($('#dial-tcost').value) ?? 0,
    execution_lag_days: numOrNull($('#dial-lag').value),
    use_nyse_breakpoints: $('#dial-nyse-bp').checked,
  };
}

function numOrNull(v) {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// Seed the dial form from a freshly loaded spec. Called whenever a paper
// finishes A1 extraction or the demo bundle lands.
function seedDialFormFromSpec(spec) {
  if (!spec) return;
  const u = spec.universe || {};
  const s = spec.signal || {};
  const p = spec.portfolio || {};
  const r = spec.rebalance || {};

  if (u.min_price != null) $('#dial-min-price').value = u.min_price;
  if (spec.start_date) $('#dial-start-date').value = spec.start_date;
  if (spec.end_date) $('#dial-end-date').value = spec.end_date;

  // Exchanges checkboxes — checked iff present in spec.universe.exchanges
  const specExchanges = (u.exchanges || []).map((x) => String(x).toUpperCase());
  $('#dial-exchanges').querySelectorAll('input[type="checkbox"]').forEach((cb) => {
    cb.checked = specExchanges.includes(cb.value);
  });

  // Methodology
  if (s.lookback_months != null && [3, 6, 9, 12].includes(s.lookback_months)) {
    $('#dial-lookback').value = String(s.lookback_months);
  }
  if (s.skip_months != null && [0, 1].includes(s.skip_months)) {
    $('#dial-skip').value = String(s.skip_months);
  }
  if (r.holding_period_months != null && [3, 6, 9, 12].includes(r.holding_period_months)) {
    $('#dial-holding').value = String(r.holding_period_months);
  }
  if (p.n_buckets != null && [5, 10, 20].includes(p.n_buckets)) {
    $('#dial-buckets').value = String(p.n_buckets);
  } else if (p.n_buckets != null) {
    // Non-canonical bucket count — leave dropdown at default but
    // adjust long/short max to match the actual spec value.
  }
  // Update the long/short bucket max bound to match n_buckets.
  const nb = numOrNull($('#dial-buckets').value) ?? p.n_buckets ?? 10;
  $('#dial-long-bucket').max = String(nb);
  $('#dial-short-bucket').max = String(nb);

  if (p.weighting === 'equal' || p.weighting === 'value') {
    const radio = document.querySelector(`input[name="weighting"][value="${p.weighting}"]`);
    if (radio) radio.checked = true;
  }
  if (p.long_bucket != null) $('#dial-long-bucket').value = p.long_bucket;
  if (p.short_bucket != null) $('#dial-short-bucket').value = p.short_bucket;

  // Execution
  if (r.execution_lag_days != null && [0, 1, 2, 5, 10].includes(r.execution_lag_days)) {
    $('#dial-lag').value = String(r.execution_lag_days);
  }
  // transaction_cost_bps lives outside ReplicationSpec — leave at user default.

  $('#dial-nyse-bp').checked = !!p.use_nyse_breakpoints;
}

// Pure: take a spec, return a deep clone with the dial form values merged in.
// Does NOT touch the supporting_quote on each section — quotes belong to A1.
function applyDials(spec) {
  if (!spec) return spec;
  const dial = readDialForm();
  const out = JSON.parse(JSON.stringify(spec));

  // Universe
  out.universe = out.universe || {};
  if (dial.min_price != null) out.universe.min_price = dial.min_price;
  out.universe.exchanges = dial.exchanges; // tuple in pydantic, list in JSON

  // Window
  if (dial.start_date) out.start_date = dial.start_date;
  if (dial.end_date) out.end_date = dial.end_date;

  // Signal
  out.signal = out.signal || {};
  if (dial.lookback_months != null) out.signal.lookback_months = dial.lookback_months;
  if (dial.skip_months != null) out.signal.skip_months = dial.skip_months;

  // Portfolio
  out.portfolio = out.portfolio || {};
  if (dial.n_buckets != null) out.portfolio.n_buckets = dial.n_buckets;
  if (dial.long_bucket != null) out.portfolio.long_bucket = dial.long_bucket;
  if (dial.short_bucket != null) out.portfolio.short_bucket = dial.short_bucket;
  if (dial.weighting) out.portfolio.weighting = dial.weighting;
  out.portfolio.use_nyse_breakpoints = !!dial.use_nyse_breakpoints;

  // Rebalance
  out.rebalance = out.rebalance || {};
  if (dial.holding_period_months != null) out.rebalance.holding_period_months = dial.holding_period_months;
  if (dial.execution_lag_days != null) out.rebalance.execution_lag_days = dial.execution_lag_days;

  // Cross-field invariants the Pydantic validator enforces. If A1 extracted
  // rebalance.frequency='annual' (or anything non-monthly) on a paper, the
  // spec rejects holding_period_months entirely. Drop the field in that case
  // so the engine doesn't 500; the user can always change frequency to
  // 'monthly' in the dial later when a dial for it exists.
  if (out.rebalance.frequency && out.rebalance.frequency !== 'monthly') {
    delete out.rebalance.holding_period_months;
  }

  return out;
}

// Compute the list of (path, original_value, new_value) pairs that diverge.
function computeOverrides() {
  if (!state.originalSpec) return [];
  const merged = applyDials(state.originalSpec);
  const out = [];
  DIAL_FIELD_PATHS.forEach((path) => {
    const before = readPath(state.originalSpec, path);
    const after = readPath(merged, path);
    if (!sameValue(before, after)) out.push({ path, before, after });
  });
  return out;
}

function readPath(obj, path) {
  return path.split('.').reduce((o, k) => (o == null ? o : o[k]), obj);
}

function sameValue(a, b) {
  if (a === b) return true;
  if (a == null && b == null) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    const aa = a.map(String).sort();
    const bb = b.map(String).sort();
    return aa.every((x, i) => x === bb[i]);
  }
  return String(a) === String(b);
}

// Wire form change events. Any dial change re-renders the Spec tab so the user
// can see the override take effect immediately. Quotes stay verbatim — only
// the field values shift.
function wireDialForm() {
  const card = $('#runconfig-card');
  if (!card) return;
  const onChange = () => {
    // Keep long/short bucket bounds in sync with n_buckets.
    const nb = numOrNull($('#dial-buckets').value) ?? 10;
    $('#dial-long-bucket').max = String(nb);
    $('#dial-short-bucket').max = String(nb);
    if (numOrNull($('#dial-long-bucket').value) > nb) $('#dial-long-bucket').value = nb;
    if (numOrNull($('#dial-short-bucket').value) > nb) $('#dial-short-bucket').value = nb;

    if (state.originalSpec) renderSpec(state.bundle ? state.bundle.verified_spec : null);
    refreshOverrideChip();
  };
  card.querySelectorAll('input, select').forEach((el) => {
    el.addEventListener('change', onChange);
    if (el.tagName === 'INPUT' && (el.type === 'number' || el.type === 'date')) {
      el.addEventListener('input', onChange);
    }
  });

  // Collapse / expand. Both the chevron button and the card-head act as toggles.
  const toggle = $('#runconfig-toggle');
  const head = $('#runconfig-head');
  const setExpanded = () => {
    const expanded = !card.classList.contains('collapsed');
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  };
  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    card.classList.toggle('collapsed');
    setExpanded();
  });
  head.addEventListener('click', (e) => {
    if (e.target.closest('.runconfig-toggle')) return;
    card.classList.toggle('collapsed');
    setExpanded();
  });

  // Paper selector + Run pipeline
  $('#runconfig-paper-select').addEventListener('change', (e) => {
    state.selectedPaperId = e.target.value || null;
    refreshRunButton();
  });
  $('#run-pipeline-btn').addEventListener('click', runPipelineFromDials);

  // Override chip click -> toggle override list visibility
  $('#override-chip').addEventListener('click', () => {
    const list = $('#override-list');
    if (list) list.classList.toggle('hidden');
  });
}

function refreshOverrideChip() {
  const overrides = computeOverrides();
  const chip = $('#override-chip');
  const runChip = $('#runconfig-chip');
  if (!chip) return;
  if (overrides.length === 0) {
    chip.classList.add('hidden');
    chip.classList.remove('override-chip');
    if (runChip) runChip.innerHTML = '<span class="dot gray"></span>Defaults';
  } else {
    chip.classList.remove('hidden');
    chip.classList.add('override-chip');
    chip.textContent = `Dial overrides active: ${overrides.length} field${overrides.length === 1 ? '' : 's'}`;
    if (runChip) runChip.innerHTML = `<span class="dot amber"></span>${overrides.length} override${overrides.length === 1 ? '' : 's'}`;
  }
}

function refreshRunButton() {
  const btn = $('#run-pipeline-btn');
  if (!btn) return;
  const hasPaper = !!state.selectedPaperId;
  btn.disabled = !hasPaper || state.running;
  btn.title = !hasPaper ? 'Upload or pick a paper first' : 'Run A1 → A2 → A3 → backtest → robustness → diagnosis';
}

async function runPipelineFromDials() {
  if (!state.selectedPaperId) {
    log('SYS', 'No paper selected. Upload a PDF or pick from the list.', 'sys');
    return;
  }
  if (state.running) return;
  state.running = true;
  refreshRunButton();
  try {
    log('SYS', `Run pipeline → ${state.selectedPaperId} (dial overrides will be applied at each stage).`, 'sys');
    await runExtraction(state.selectedPaperId);
  } finally {
    state.running = false;
    refreshRunButton();
  }
}

// ---------- log ----------

function log(tag, msg, klass = 'sys') {
  const row = el('div', { class: 'log-row' }, [
    el('span', { class: 'ts' }, `[${nowHHMMSS()}]`),
    el('span', { class: `tag ${klass}` }, `[${tag}]`),
    el('span', { class: 'msg' }, msg),
  ]);
  const logEl = $('#log');
  logEl.appendChild(row);
  logEl.scrollTop = logEl.scrollHeight;
}

// ---------- tab switching ----------

function showTab(name) {
  state.activeTab = name;
  $$('.tab').forEach((t) => t.classList.toggle('hidden', t.dataset.tab !== name));
  $$('.sidenav-links a').forEach((a) => a.classList.toggle('active', a.dataset.tab === name));
}

$$('.sidenav-links a').forEach((a) => {
  a.addEventListener('click', (ev) => {
    ev.preventDefault();
    showTab(a.dataset.tab);
  });
});

// ---------- demo button + status ----------

function setStatus(text, color = 'gray') {
  $('#status-text').textContent = text;
  const pill = $('#status-pill .dot');
  pill.className = `dot ${color}`;
}
function setVerdict(report) {
  const el = $('#verdict-badge');
  if (!report) { el.textContent = 'Verdict: —'; el.style.color = ''; return; }
  const conf = report.overall_confidence;
  const label = conf.charAt(0).toUpperCase() + conf.slice(1);
  el.textContent = `Verdict: ${label}`;
  el.style.color = conf === 'high' ? 'var(--success)' : conf === 'medium' ? 'var(--warning)' : 'var(--danger)';
}

$('#demo-btn').addEventListener('click', async () => {
  log('SYS', 'Loading demo bundle from /api/demo…', 'sys');
  setStatus('Loading demo bundle…', 'amber');
  try {
    const resp = await fetch('/api/demo');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const bundle = await resp.json();
    applyBundle(bundle);
    log('SYS', `Demo bundle loaded (paper_id=${bundle.paper_id}).`, 'sys');
  } catch (e) {
    log('SYS', `ERROR: ${e.message}`, 'sys');
    setStatus('Error', 'red');
  }
});

$('#reset-btn').addEventListener('click', () => {
  state.bundle = null;
  state.originalSpec = null;
  state.selectedPaperId = null;
  $('#spec-body').innerHTML = '';
  $('#verification-body').innerHTML = '';
  $('#critique-body').innerHTML = '';
  $('#backtest-body').innerHTML = '';
  $('#robustness-body').innerHTML = '';
  $('#diagnosis-body').innerHTML = '';
  $('#provenance-body').innerHTML = '';
  resetKPIs();
  $('#paper-title').textContent = 'No paper';
  $('#ingest-chip').innerHTML = '<span class="dot amber"></span>Awaiting upload';
  $('#metric-chip').innerHTML = '<span class="dot gray"></span>No run';
  $('#log').innerHTML = '';
  setStatus('Idle — no paper loaded', 'gray');
  setVerdict(null);
  refreshOverrideChip();
  refreshRunButton();
  showTab('overview');
});

// ---------- file upload ----------

$('#pick-btn').addEventListener('click', () => $('#file-input').click());
$('#dropzone').addEventListener('click', () => $('#file-input').click());
$('#dropzone').addEventListener('dragover', (e) => { e.preventDefault(); $('#dropzone').classList.add('dragover'); });
$('#dropzone').addEventListener('dragleave', () => $('#dropzone').classList.remove('dragover'));
$('#dropzone').addEventListener('drop', (e) => {
  e.preventDefault();
  $('#dropzone').classList.remove('dragover');
  if (e.dataTransfer.files.length) uploadPaper(e.dataTransfer.files[0]);
});
$('#file-input').addEventListener('change', (e) => {
  if (e.target.files.length) uploadPaper(e.target.files[0]);
});

async function uploadPaper(file) {
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    log('SYS', 'Only .pdf accepted.', 'sys');
    return;
  }
  log('SYS', `Uploading ${file.name} (${(file.size / 1024).toFixed(1)} KB)…`, 'sys');
  setStatus('Uploading PDF…', 'amber');
  const form = new FormData();
  form.append('file', file);
  try {
    const up = await fetch('/api/papers', { method: 'POST', body: form });
    if (!up.ok) throw new Error(`upload HTTP ${up.status}`);
    const paper = await up.json();
    log('A1', `Paper ${paper.paper_id} stored. Kicking off extraction…`, 'a1');
    $('#paper-title').textContent = paper.paper_id;
    $('#ingest-chip').innerHTML = '<span class="dot green pulse"></span>Ingested';
    await runExtraction(paper.paper_id);
  } catch (e) {
    log('SYS', `ERROR: ${e.message}`, 'sys');
    setStatus('Error', 'red');
  }
  refreshPapers();
}

async function safeJson(resp) {
  // Server may return HTML ("Internal Server Error") when something escapes
  // the handler. Read as text, try to parse; otherwise surface the raw body.
  const text = await resp.text();
  try {
    return { ok: true, body: JSON.parse(text) };
  } catch {
    return { ok: false, body: { error: text.slice(0, 400) || `HTTP ${resp.status}` } };
  }
}

async function runExtraction(paperId) {
  setStatus('Running A1 + A2 (may take 30–90s on first run)…', 'amber');
  log('A1', 'Calling /api/extract (Sonnet + Haiku, cached on disk)…', 'a1');
  let verifiedSpec = null;
  try {
    const r = await fetch('/api/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paper_id: paperId }),
    });
    const parsed = await safeJson(r);
    if (!r.ok || !parsed.ok) {
      const msg = parsed.body.error || `HTTP ${r.status}`;
      const hint = parsed.body.hint || 'Is ANTHROPIC_API_KEY set? Check server logs for the full traceback.';
      log('A1', `FAILED: ${msg}`, 'a1');
      log('SYS', hint, 'sys');
      setStatus('Extraction failed', 'red');
      return;
    }
    const body = parsed.body;
    verifiedSpec = body.verified_spec;
    log('A1', `Extracted spec. Verification → ${verifiedSpec.report.overall_confidence}.`, 'a1');
    log('A2', `${verifiedSpec.report.n_checks} quote checks; ${verifiedSpec.report.n_failed_high} high-severity failures.`, 'a2');
    applyBundle({
      paper_id: body.paper_id,
      paper_title: body.paper_title,
      verified_spec: verifiedSpec,
      critique: null,
      backtest: null,
      robustness: null,
      diagnosis: null,
      paper_claim: null,
    });
  } catch (e) {
    log('SYS', `ERROR: ${e.message}`, 'sys');
    setStatus('Error', 'red');
    return;
  }

  // Chain the rest of the pipeline. Each stage degrades gracefully — a 503
  // (missing parquet cache) or 500 (missing API key) logs a hint and the
  // pipeline continues so the user sees whatever DID complete.
  await runCritique(paperId);
  const bt = await runBacktest(paperId, verifiedSpec);
  if (bt) {
    await runRobustness(paperId, verifiedSpec);
    await runDiagnosis(paperId, verifiedSpec, bt);
  }
  setStatus('Pipeline complete', 'green');
}

async function runCritique(paperId) {
  setStatus('Running A3 adversarial reviewer…', 'amber');
  log('A3', 'Calling /api/critique (Sonnet, forced three criticisms)…', 'a3');
  try {
    const r = await fetch('/api/critique', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paper_id: paperId }),
    });
    const parsed = await safeJson(r);
    if (!r.ok || !parsed.ok) {
      log('A3', `SKIPPED: ${parsed.body.error || `HTTP ${r.status}`}`, 'a3');
      if (parsed.body.hint) log('SYS', parsed.body.hint, 'sys');
      return;
    }
    if (!state.bundle) return;
    state.bundle.critique = parsed.body.critique;
    renderCritique(state.bundle.critique);
    log('A3', `${parsed.body.critique.criticisms.length} criticisms recorded.`, 'a3');
  } catch (e) {
    log('A3', `ERROR: ${e.message}`, 'a3');
  }
}

async function runBacktest(paperId, verified) {
  setStatus('Running backtest engine…', 'amber');
  log('ENGINE', 'Calling /api/backtest (deterministic canonical engine)…', 'sys');
  try {
    const dialed = applyDials(verified.spec);
    const dial = readDialForm();
    const reqBody = { spec: dialed, transaction_cost_bps: dial.transaction_cost_bps };
    console.log('[runBacktest] outgoing body', reqBody);
    const r = await fetch('/api/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqBody),
    });
    const parsed = await safeJson(r);
    if (!r.ok || !parsed.ok) {
      log('ENGINE', `SKIPPED: ${parsed.body.error || `HTTP ${r.status}`}`, 'sys');
      if (parsed.body.hint) log('SYS', parsed.body.hint, 'sys');
      return null;
    }
    const bt = parsed.body.backtest;
    if (!state.bundle) return null;
    state.bundle.backtest = bt;
    state.bundle.window_info = parsed.body.window_info || null;
    renderBacktest(bt, state.bundle.paper_claim);
    renderKPIs(bt);
    renderProvenance(bt);
    if (parsed.body.clip_note) log('SYS', parsed.body.clip_note, 'sys');
    log('ENGINE', `n=${bt.n_periods}, μ=${fmtPct(bt.mean_return, 3)}, t=${fmtNum(bt.alpha_tstat, 2)}.`, 'sys');
    return bt;
  } catch (e) {
    log('ENGINE', `ERROR: ${e.message}`, 'sys');
    return null;
  }
}

async function runRobustness(paperId, verified) {
  setStatus('Running robustness battery (15–30 min on first run)…', 'amber');
  log('D3', 'Calling /api/robustness (6 families + D3 judgment)…', 'a3');
  try {
    const dialed = applyDials(verified.spec);
    const reqBody = { spec: dialed };
    console.log('[runRobustness] outgoing body', reqBody);
    const r = await fetch('/api/robustness', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqBody),
    });
    const parsed = await safeJson(r);
    if (!r.ok || !parsed.ok) {
      log('D3', `SKIPPED: ${parsed.body.error || `HTTP ${r.status}`}`, 'a3');
      if (parsed.body.hint) log('SYS', parsed.body.hint, 'sys');
      return;
    }
    if (!state.bundle) return;
    state.bundle.robustness = parsed.body;
    renderRobustness(state.bundle.robustness);
    if (parsed.body.clip_note) log('SYS', parsed.body.clip_note, 'sys');
    const sc = parsed.body.scorecard;
    log('D3', `${sc.n_surviving}/${sc.n_tests} surviving · signal=${parsed.body.judgment.signal_type}.`, 'a3');
  } catch (e) {
    log('D3', `ERROR: ${e.message}`, 'a3');
  }
}

async function runDiagnosis(paperId, verified, bt) {
  // D2 needs a real paper_claim to be useful — without one, the gap is
  // zero and D2 early-exits. Skip unless one was loaded (e.g. demo bundle).
  if (!state.bundle || !state.bundle.paper_claim) {
    log('D2', 'SKIPPED: no paper_claim on bundle (only useful with a claim to compare against).', 'a3');
    return;
  }
  const claimMonthly = state.bundle.paper_claim.monthly_return;
  const claimTstat = state.bundle.paper_claim.tstat ?? null;
  setStatus('Running D2 divergence diagnostician…', 'amber');
  log('D2', 'Calling /api/diagnose (LLM-proposed mutations × engine reruns)…', 'a3');
  try {
    const dialed = applyDials(verified.spec);
    const dial = readDialForm();
    const reqBody = {
      spec: dialed,
      paper_claim_monthly_return: claimMonthly,
      paper_claim_tstat: claimTstat,
      transaction_cost_bps: dial.transaction_cost_bps,
    };
    console.log('[runDiagnosis] outgoing body', reqBody);
    const r = await fetch('/api/diagnose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqBody),
    });
    const parsed = await safeJson(r);
    if (!r.ok || !parsed.ok) {
      log('D2', `SKIPPED: ${parsed.body.error || `HTTP ${r.status}`}`, 'a3');
      if (parsed.body.hint) log('SYS', parsed.body.hint, 'sys');
      return;
    }
    if (!state.bundle) return;
    state.bundle.diagnosis = parsed.body;
    renderDiagnosis(state.bundle.diagnosis, state.bundle.paper_claim);
    if (parsed.body.clip_note) log('SYS', parsed.body.clip_note, 'sys');
    log('D2', `${parsed.body.n_experiments} experiments · primary=${parsed.body.diagnosis.primary_cause}.`, 'a3');
  } catch (e) {
    log('D2', `ERROR: ${e.message}`, 'a3');
  }
}

// ---------- papers list ----------

async function refreshPapers() {
  try {
    const r = await fetch('/api/papers');
    const body = await r.json();
    state.papers = body.papers || [];
    const wrap = $('#papers-list');
    wrap.innerHTML = '';
    state.papers.forEach((p) => {
      const isSelected = state.selectedPaperId === p.paper_id;
      const row = el('div', { class: 'paper-row' }, [
        el('span', {}, `${p.paper_id}.pdf · ${(p.size_bytes / 1024).toFixed(1)} KB`),
        el('div', { class: 'actions' }, [
          el('button', {
            onclick: () => {
              state.selectedPaperId = p.paper_id;
              const sel = $('#runconfig-paper-select');
              if (sel) sel.value = p.paper_id;
              refreshRunButton();
              refreshPapers();
              showTab('overview');
              log('SYS', `Selected ${p.paper_id}. Adjust dials, then click Run pipeline.`, 'sys');
            },
          }, isSelected ? 'Selected' : 'Select'),
        ]),
      ]);
      wrap.appendChild(row);
    });

    // Sync the Run-config paper selector dropdown.
    const sel = $('#runconfig-paper-select');
    if (sel) {
      const cur = state.selectedPaperId || sel.value || '';
      sel.innerHTML = '';
      sel.appendChild(el('option', { value: '' }, '— no paper selected —'));
      state.papers.forEach((p) => {
        sel.appendChild(el('option', { value: p.paper_id }, p.paper_id));
      });
      if (cur && state.papers.some((p) => p.paper_id === cur)) sel.value = cur;
    }
    refreshRunButton();
  } catch (e) {
    /* ignore */
  }
}

// ---------- bundle render ----------

function applyBundle(bundle) {
  state.bundle = bundle;
  // Pristine A1 output drives both the dial form seeding and the override diff.
  // Take a deep clone so further dial reads don't mutate the bundle.
  if (bundle.verified_spec && bundle.verified_spec.spec) {
    state.originalSpec = JSON.parse(JSON.stringify(bundle.verified_spec.spec));
    // Only re-seed the dial form when the user is NOT in the middle of an
    // explicit "Run pipeline" invocation — otherwise we'd clobber their dials
    // with the freshly extracted A1 values right before they get applied.
    if (!state.running) seedDialFormFromSpec(state.originalSpec);
  }
  // If the bundle came in with a paper_id, default the selector to it.
  if (bundle.paper_id) {
    state.selectedPaperId = bundle.paper_id;
    const sel = $('#runconfig-paper-select');
    if (sel) {
      const opts = Array.from(sel.options).map((o) => o.value);
      if (!opts.includes(bundle.paper_id)) {
        sel.appendChild(el('option', { value: bundle.paper_id }, bundle.paper_id));
      }
      sel.value = bundle.paper_id;
    }
  }
  refreshOverrideChip();
  refreshRunButton();
  $('#paper-title').textContent = bundle.paper_title || bundle.paper_id || 'Paper';
  if (bundle.verified_spec) setVerdict(bundle.verified_spec.report);
  renderSpec(bundle.verified_spec);
  renderVerification(bundle.verified_spec);
  renderCritique(bundle.critique);
  renderBacktest(bundle.backtest, bundle.paper_claim);
  renderRobustness(bundle.robustness);
  renderDiagnosis(bundle.diagnosis, bundle.paper_claim);
  renderProvenance(bundle.backtest);
  renderKPIs(bundle.backtest);
  setStatus('Ready — bundle loaded', 'green');
}

function resetKPIs() {
  ['monthly', 'tstat', 'sharpe', 'dd', 'n', 'hit'].forEach((k) => ($(`#kpi-${k}`).textContent = '—'));
}

function renderKPIs(bt) {
  if (!bt) { resetKPIs(); return; }
  const monthly = $('#kpi-monthly');
  monthly.textContent = fmtPct(bt.mean_return, 3);
  monthly.className = 'val ' + (bt.mean_return >= 0 ? 'pos' : 'neg');
  $('#kpi-tstat').textContent = fmtNum(bt.alpha_tstat, 2);
  $('#kpi-sharpe').textContent = fmtNum(bt.sharpe_ratio, 2);
  const dd = $('#kpi-dd');
  dd.textContent = fmtPct(bt.max_drawdown, 1);
  dd.className = 'val neg';
  $('#kpi-n').textContent = bt.n_periods;
  $('#kpi-hit').textContent = fmtPct(bt.hit_rate, 1);
  $('#metric-chip').innerHTML = '<span class="dot green"></span>Run complete';
}

// ---------- Spec tab ----------

function renderSpec(verified) {
  const root = $('#spec-body');
  root.innerHTML = '';
  if (!verified && !state.originalSpec) {
    root.appendChild(emptyState('no_sim', 'No spec loaded. Run extraction or load the demo bundle.'));
    return;
  }
  // The displayed spec is originalSpec ⊕ dial overrides. Quotes always come
  // from the verified payload (= the A1/A2 pristine record).
  const baseVerified = verified || (state.bundle && state.bundle.verified_spec) || null;
  const baseSpec = state.originalSpec || (baseVerified && baseVerified.spec);
  if (!baseSpec) {
    root.appendChild(emptyState('no_sim', 'No spec loaded. Run extraction or load the demo bundle.'));
    return;
  }
  const spec = applyDials(baseSpec);
  const quotes = baseVerified
    ? {
        universe: baseVerified.spec.universe.supporting_quote,
        signal: baseVerified.spec.signal.supporting_quote,
        portfolio: baseVerified.spec.portfolio.supporting_quote,
        rebalance: baseVerified.spec.rebalance.supporting_quote,
      }
    : { universe: baseSpec.universe.supporting_quote, signal: baseSpec.signal.supporting_quote, portfolio: baseSpec.portfolio.supporting_quote, rebalance: baseSpec.rebalance.supporting_quote };

  // Overrides set: which displayed fields should carry the "(overridden)" badge.
  const overridePathSet = new Set(computeOverrides().map((o) => o.path));

  // Override list panel (above the sections, hidden by default).
  const overrideList = el('div', { class: 'override-list hidden', id: 'override-list' });
  const overrides = computeOverrides();
  if (overrides.length) {
    overrides.forEach((o) => {
      overrideList.appendChild(el('div', {}, `${o.path}: ${formatDialValue(o.before)} → ${formatDialValue(o.after)}`));
    });
  }
  root.appendChild(overrideList);

  root.appendChild(specBlock('Header', {
    'paper_id': [spec.paper_id, false],
    'paper_title': [spec.paper_title, false],
    'window': [`${fmtDate(spec.start_date)} → ${fmtDate(spec.end_date)}`, overridePathSet.has('start_date') || overridePathSet.has('end_date')],
    'base_currency': [spec.base_currency, false],
    'notes': [spec.notes || '—', false],
  }));

  root.appendChild(specBlock('Universe', {
    'name': [spec.universe.name, false],
    'region': [spec.universe.region, false],
    'asset_class': [spec.universe.asset_class, false],
    'min_price': [spec.universe.min_price ?? '—', overridePathSet.has('universe.min_price')],
    'exchanges': [(spec.universe.exchanges || []).join(', ') || '—', overridePathSet.has('universe.exchanges')],
  }, quotes.universe));

  root.appendChild(specBlock('Signal', {
    'name': [spec.signal.name, false],
    'kind': [spec.signal.kind, false],
    'formula': [spec.signal.formula, false],
    'lookback_months': [spec.signal.lookback_months ?? '—', overridePathSet.has('signal.lookback_months')],
    'skip_months': [spec.signal.skip_months, overridePathSet.has('signal.skip_months')],
    'direction': [spec.signal.direction, false],
    'frequency': [spec.signal.frequency, false],
  }, quotes.signal));

  root.appendChild(specBlock('Portfolio', {
    'construction': [spec.portfolio.construction, false],
    'n_buckets': [spec.portfolio.n_buckets, overridePathSet.has('portfolio.n_buckets')],
    'long_bucket': [spec.portfolio.long_bucket, overridePathSet.has('portfolio.long_bucket')],
    'short_bucket': [spec.portfolio.short_bucket ?? '—', overridePathSet.has('portfolio.short_bucket')],
    'weighting': [spec.portfolio.weighting, overridePathSet.has('portfolio.weighting')],
    'long_short': [spec.portfolio.long_short ? 'yes' : 'no', false],
    'gross_exposure': [spec.portfolio.gross_exposure, false],
    'use_nyse_breakpoints': [spec.portfolio.use_nyse_breakpoints ? 'yes' : 'no', overridePathSet.has('portfolio.use_nyse_breakpoints')],
  }, quotes.portfolio));

  root.appendChild(specBlock('Rebalance', {
    'frequency': [spec.rebalance.frequency, false],
    'execution_lag_days': [spec.rebalance.execution_lag_days, overridePathSet.has('rebalance.execution_lag_days')],
    'holding_period_months': [spec.rebalance.holding_period_months ?? '—', overridePathSet.has('rebalance.holding_period_months')],
    'signal_date_convention': [spec.rebalance.signal_date_convention, false],
    'execution_date_convention': [spec.rebalance.execution_date_convention, false],
  }, quotes.rebalance));

  // Ambiguities — always taken from the original A1 output. Dials don't add
  // or remove ambiguities; they just override values.
  const ambSource = baseSpec;
  if (ambSource.ambiguities && ambSource.ambiguities.length) {
    const head = el('div', { class: 'spec-section-head' }, [
      el('h3', {}, `Ambiguities (${ambSource.ambiguities.length})`),
    ]);
    const list = el('div', { class: 'spec-section' });
    list.appendChild(head);

    const table = el('table', { class: 'data' });
    const thead = el('thead', {}, [
      el('tr', {}, [
        el('th', {}, 'Parameter'),
        el('th', {}, 'Default'),
        el('th', {}, 'Alternatives'),
        el('th', {}, 'Priority'),
        el('th', {}, 'Reason'),
      ]),
    ]);
    table.appendChild(thead);
    const tbody = el('tbody');
    ambSource.ambiguities.forEach((f) => {
      const sevClass = f.sensitivity_priority === 'high' ? 'fail' : f.sensitivity_priority === 'medium' ? 'warn' : 'info';
      tbody.appendChild(el('tr', {}, [
        el('td', {}, f.parameter),
        el('td', {}, f.default_chosen),
        el('td', {}, (f.alternatives || []).join(', ') || '—'),
        el('td', {}, [el('span', { class: `pill ${sevClass}` }, capitalize(f.sensitivity_priority))]),
        el('td', {}, f.reason),
      ]));
    });
    table.appendChild(tbody);
    list.appendChild(table);
    root.appendChild(list);
  }
}

function specBlock(title, fields, supportingQuote) {
  const section = el('div', { class: 'spec-section' });
  section.appendChild(el('div', { class: 'spec-section-head' }, [
    el('h3', {}, title),
  ]));
  const dl = el('dl', { class: 'spec-section-body' });
  Object.entries(fields).forEach(([k, raw]) => {
    // raw can be either a bare value (legacy) or a [value, isOverridden] tuple.
    const [val, overridden] = Array.isArray(raw) ? raw : [raw, false];
    dl.appendChild(el('dt', { class: overridden ? 'overridden' : '' }, k));
    dl.appendChild(el('dd', {}, String(val)));
  });
  section.appendChild(dl);
  if (supportingQuote) {
    section.appendChild(el('div', { class: 'quote' }, [
      el('span', { class: 'meta' }, `Supporting quote · page ${supportingQuote.page} · confidence ${fmtNum(supportingQuote.match_confidence, 2)} · ${supportingQuote.verified ? 'verified' : 'unverified'}`),
      document.createTextNode('“' + supportingQuote.text + '”'),
    ]));
  }
  return section;
}

function formatDialValue(v) {
  if (v == null) return '—';
  if (Array.isArray(v)) return v.join(',') || '∅';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  return String(v);
}

function capitalize(s) {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ---------- Verification tab ----------

function renderVerification(verified) {
  const root = $('#verification-body');
  root.innerHTML = '';
  if (!verified) {
    root.appendChild(emptyState('verified_user', 'No verification report yet.'));
    return;
  }
  const r = verified.report;
  const summary = el('div', { class: 'spec-section' });
  summary.appendChild(el('div', { class: 'spec-section-head' }, [
    el('h3', {}, 'A2 summary'),
    el('span', { class: 'pill ' + (r.overall_confidence === 'high' ? 'ok' : r.overall_confidence === 'medium' ? 'warn' : 'fail') }, capitalize(r.overall_confidence)),
  ]));
  const dl = el('dl', { class: 'spec-section-body' });
  [
    ['overall_confidence', r.overall_confidence],
    ['checks', r.n_checks],
    ['failed (high)', r.n_failed_high],
    ['failed (medium)', r.n_failed_medium],
    ['failed (low)', r.n_failed_low],
    ['retries', r.retry_count],
  ].forEach(([k, v]) => {
    dl.appendChild(el('dt', {}, k));
    dl.appendChild(el('dd', {}, String(v)));
  });
  summary.appendChild(dl);
  root.appendChild(summary);

  // Checks table
  const block = el('div', { class: 'spec-section' });
  block.appendChild(el('div', { class: 'spec-section-head' }, [
    el('h3', {}, 'Quote checks'),
  ]));
  const table = el('table', { class: 'data' });
  table.appendChild(el('thead', {}, [
    el('tr', {}, [
      el('th', {}, 'Field'),
      el('th', {}, 'Severity'),
      el('th', {}, 'Status'),
      el('th', {}, 'Page'),
      el('th', {}, 'Conf'),
      el('th', {}, 'Supports'),
      el('th', {}, 'Verdict'),
    ]),
  ]));
  const tbody = el('tbody');
  r.checks.forEach((c) => {
    const sevClass = c.severity === 'high' ? 'fail' : c.severity === 'medium' ? 'warn' : 'info';
    const supportClass = c.support_check ? (c.support_check.supports === 'yes' ? 'ok' : c.support_check.supports === 'partial' ? 'warn' : 'fail') : 'info';
    tbody.appendChild(el('tr', {}, [
      el('td', {}, c.field_path),
      el('td', {}, [el('span', { class: `pill ${sevClass}` }, capitalize(c.severity))]),
      el('td', {}, c.verification_status),
      el('td', { class: 'num' }, c.verified_page ?? '—'),
      el('td', { class: 'num' }, fmtNum(c.verification_confidence, 2)),
      el('td', {}, c.support_check ? [el('span', { class: `pill ${supportClass}` }, capitalize(c.support_check.supports))] : '—'),
      el('td', {}, [el('span', { class: 'pill ' + (c.failed ? 'fail' : 'ok') }, c.failed ? 'Fail' : 'Pass')]),
    ]));
  });
  table.appendChild(tbody);
  block.appendChild(table);
  root.appendChild(block);

  // Support reasons
  r.checks.forEach((c) => {
    if (!c.support_check) return;
    const card = el('div', { class: 'spec-section' });
    card.appendChild(el('div', { class: 'spec-section-head' }, [
      el('h3', {}, `${c.field_path} · ${capitalize(c.support_check.supports)}`),
    ]));
    card.appendChild(el('div', { class: 'quote' }, [
      el('span', { class: 'meta' }, `Haiku judgment`),
      document.createTextNode(c.support_check.reason),
    ]));
    card.appendChild(el('div', { class: 'quote' }, [
      el('span', { class: 'meta' }, `Quote · page ${c.quote.page}`),
      document.createTextNode('“' + c.quote.text + '”'),
    ]));
    root.appendChild(card);
  });
}

// ---------- Critique tab ----------

function renderCritique(critique) {
  const root = $('#critique-body');
  root.innerHTML = '';
  if (!critique) {
    root.appendChild(emptyState('rate_review', 'No A3 critique on this bundle. Use the demo bundle or run /api/critique.'));
    return;
  }
  critique.criticisms.forEach((c, i) => {
    const card = el('div', { class: `critique-card sev-${c.severity}` }, [
      el('div', { class: 'head' }, [
        el('span', { class: `pill ${c.severity === 'high' ? 'fail' : c.severity === 'medium' ? 'warn' : 'info'}` }, capitalize(c.severity)),
        el('span', { class: 'pill info' }, c.category.replace(/_/g, ' ')),
        el('h4', {}, `Criticism ${i + 1}`),
      ]),
      el('p', { class: 'desc' }, c.description),
      el('div', { class: 'remedy' }, c.proposed_remediation),
    ]);
    if (c.evidence_quote) {
      card.appendChild(el('div', { class: 'quote' }, [
        el('span', { class: 'meta' }, `Evidence · page ${c.evidence_quote.page}`),
        document.createTextNode('“' + c.evidence_quote.text + '”'),
      ]));
    }
    root.appendChild(card);
  });
}

// ---------- Backtest tab ----------

function renderBacktest(bt, paperClaim) {
  const root = $('#backtest-body');
  root.innerHTML = '';
  if (!bt) {
    root.appendChild(emptyState('trending_up', 'No backtest result. Use the demo bundle or run /api/backtest.'));
    return;
  }

  // Window substitution banner — loud warning when the engine ran on a
  // different window than the paper claimed (either fully-substituted OOS
  // or partially-clipped to the data panel).
  const wi = state.bundle && state.bundle.window_info;
  if (wi && (wi.substituted || wi.clipped)) {
    const severity = wi.substituted ? 'fail' : 'warn';
    const headline = wi.substituted
      ? 'Post-publication out-of-sample run — paper\'s original window is not in the data panel.'
      : 'Window clipped to the data panel — engine ran a strict subset of the paper\'s window.';
    const banner = el('div', { class: `flag-banner window-banner sev-${severity}` });
    banner.appendChild(el('div', { class: 'head' }, `Window substitution — ${wi.overlap_kind.replace(/_/g, ' ')}`));
    banner.appendChild(el('div', { class: 'banner-headline' }, headline));
    const grid = el('div', { class: 'window-grid' });
    grid.appendChild(el('div', { class: 'window-cell' }, [
      el('span', { class: 'lbl' }, 'Paper window (claimed)'),
      el('span', { class: 'val mono' }, `${wi.paper_start} → ${wi.paper_end}`),
    ]));
    grid.appendChild(el('div', { class: 'window-cell hi' }, [
      el('span', { class: 'lbl' }, 'Engine ran on'),
      el('span', { class: 'val mono' }, `${wi.engine_start} → ${wi.engine_end}`),
    ]));
    grid.appendChild(el('div', { class: 'window-cell' }, [
      el('span', { class: 'lbl' }, 'Data panel available'),
      el('span', { class: 'val mono' }, `${wi.data_panel_start} → ${wi.data_panel_end}`),
    ]));
    banner.appendChild(grid);
    if (wi.substituted) {
      banner.appendChild(el('div', { class: 'banner-foot' },
        'This is NOT a replication of the paper\'s result — it is an out-of-sample test on data that did not exist when the paper was written. Any gap vs. the paper\'s claim reflects regime / universe drift, not just methodology.'
      ));
    }
    root.appendChild(banner);
  }

  // Data quality flags
  if (bt.data_quality_flags && bt.data_quality_flags.length) {
    const banner = el('div', { class: 'flag-banner' });
    banner.appendChild(el('div', { class: 'head' }, `Data quality flags (${bt.data_quality_flags.length})`));
    bt.data_quality_flags.forEach((f) => banner.appendChild(el('div', {}, `• ${f}`)));
    root.appendChild(banner);
  }

  // Comparison
  if (paperClaim) {
    const block = el('div', { class: 'spec-section' });
    block.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Paper claim vs. replication')]));
    const gap = bt.mean_return - paperClaim.monthly_return;
    const gapPct = paperClaim.monthly_return !== 0 ? (bt.mean_return / paperClaim.monthly_return - 1) * 100 : 0;
    const t = el('table', { class: 'data' });
    t.appendChild(el('thead', {}, [
      el('tr', {}, [
        el('th', {}, 'Metric'),
        el('th', {}, 'Paper'),
        el('th', {}, 'Replication'),
        el('th', {}, 'Delta'),
      ]),
    ]));
    const tb = el('tbody', {}, [
      el('tr', {}, [
        el('td', {}, 'Monthly long-short'),
        el('td', { class: 'num' }, fmtPct(paperClaim.monthly_return, 3)),
        el('td', { class: 'num' }, fmtPct(bt.mean_return, 3)),
        el('td', { class: 'num ' + (gap >= 0 ? 'pos' : 'neg') }, (gap >= 0 ? '+' : '') + fmtPct(gap, 3) + ` (${gapPct.toFixed(0)}%)`),
      ]),
      el('tr', {}, [
        el('td', {}, 't-stat (Newey-West)'),
        el('td', { class: 'num' }, fmtNum(paperClaim.tstat, 2)),
        el('td', { class: 'num' }, fmtNum(bt.alpha_tstat, 2)),
        el('td', { class: 'num' }, fmtNum(bt.alpha_tstat - paperClaim.tstat, 2)),
      ]),
      el('tr', {}, [
        el('td', {}, 'Sample window'),
        el('td', { class: 'num' }, paperClaim.window),
        el('td', { class: 'num' }, `${fmtDate(bt.start_date)} → ${fmtDate(bt.end_date)}`),
        el('td', {}, '—'),
      ]),
    ]);
    t.appendChild(tb);
    block.appendChild(t);
    root.appendChild(block);
  }

  // Metrics summary
  const m = el('div', { class: 'spec-section' });
  m.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Engine metrics')]));
  const dl = el('dl', { class: 'spec-section-body' });
  [
    ['spec_hash', bt.spec_hash],
    ['window (engine)', `${fmtDate(bt.start_date)} → ${fmtDate(bt.end_date)}`],
    ['window (paper)', wi ? `${wi.paper_start} → ${wi.paper_end}${wi.substituted ? ' (substituted)' : wi.clipped ? ' (clipped)' : ''}` : '—'],
    ['n_periods', bt.n_periods],
    ['mean_return', fmtPct(bt.mean_return, 3)],
    ['annualized_return', fmtPct(bt.annualized_return, 2)],
    ['volatility (ann)', fmtPct(bt.volatility, 2)],
    ['sharpe', fmtNum(bt.sharpe_ratio, 2)],
    ['alpha_tstat', fmtNum(bt.alpha_tstat, 2)],
    ['max_drawdown', fmtPct(bt.max_drawdown, 1)],
    ['hit_rate', fmtPct(bt.hit_rate, 1)],
    ['turnover (one-sided, /mo)', fmtNum(bt.turnover, 3)],
    ['transaction_cost_bps', fmtNum(bt.transaction_cost_bps, 1)],
    ['newey_west_lag', bt.newey_west_lag],
    ['return_convention', bt.return_convention],
  ].forEach(([k, v]) => {
    dl.appendChild(el('dt', {}, k));
    dl.appendChild(el('dd', {}, String(v)));
  });
  m.appendChild(dl);
  root.appendChild(m);

  // Equity curve — interactive (brush-to-zoom + hover tooltip + drawdown panel)
  const chart = el('div', { class: 'spec-section' });
  chart.appendChild(el('div', { class: 'spec-section-head' }, [
    el('h3', {}, 'Cumulative return (long–short)'),
    el('div', { class: 'chart-toolbar' }, [
      el('span', { class: 'hint' }, 'drag to zoom · dbl-click to reset'),
      el('button', { class: 'btn-mini', id: 'eq-reset' }, 'Reset zoom'),
    ]),
  ]));
  const wrap = el('div', { class: 'chart-wrap chart-wrap-tall' });
  const eq = new EquityChart(bt.returns);
  wrap.appendChild(eq.root);
  chart.appendChild(wrap);
  root.appendChild(chart);
  $('#eq-reset').addEventListener('click', () => eq.resetZoom());

  // Recent returns table (last 24)
  if (bt.returns && bt.returns.length) {
    const tbl = el('div', { class: 'spec-section' });
    tbl.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, `Return observations · last 24 of ${bt.returns.length}`)]));
    const t = el('table', { class: 'data' });
    t.appendChild(el('thead', {}, [
      el('tr', {}, [
        el('th', {}, 'Period'),
        el('th', {}, 'Return'),
        el('th', {}, 'Rebalance ID'),
      ]),
    ]));
    const tb = el('tbody');
    const slice = bt.returns.slice(-24);
    slice.forEach((o) => {
      tb.appendChild(el('tr', {}, [
        el('td', {}, o.period_end),
        el('td', { class: 'num ' + (o.ret >= 0 ? 'pos' : 'neg') }, fmtPct(o.ret, 3)),
        el('td', {}, o.rebalance_id || '—'),
      ]));
    });
    t.appendChild(tb);
    tbl.appendChild(t);
    root.appendChild(tbl);
  }
}

// ---------- Interactive equity + drawdown chart ----------

const SVG_NS = 'http://www.w3.org/2000/svg';
const REGIMES = [
  { from: '2001-01-31', to: '2001-03-31', label: 'Dot-com reversal' },
  { from: '2009-03-31', to: '2009-06-30', label: '2009 momentum crash' },
];

function svgEl(tag, attrs = {}) {
  const n = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
  return n;
}

class EquityChart {
  constructor(returns) {
    this.returns = returns || [];
    this.cum = [];
    let s = 0;
    this.returns.forEach((r) => { s += r.ret; this.cum.push(s); });
    let peak = 0;
    this.dd = this.cum.map((v) => { peak = Math.max(peak, v); return v - peak; });
    this.zoom = [0, Math.max(this.cum.length - 1, 0)];
    this.W = 800; this.H = 320;
    this.padL = 44; this.padR = 14; this.padT = 16;
    this.eqH = 200;
    this.gap = 14;
    this.ddH = 60;
    this.root = el('div', { class: 'eq-chart-root' });
    this.svg = svgEl('svg', {
      viewBox: `0 0 ${this.W} ${this.H}`,
      preserveAspectRatio: 'none',
      class: 'eq-svg',
    });
    this.root.appendChild(this.svg);
    this.tooltip = el('div', { class: 'eq-tooltip hidden' });
    this.root.appendChild(this.tooltip);
    this.bindEvents();
    this.render();
  }

  resetZoom() {
    this.zoom = [0, Math.max(this.cum.length - 1, 0)];
    this.render();
  }

  bindEvents() {
    let dragStart = null;
    const toLocal = (ev) => {
      const rect = this.svg.getBoundingClientRect();
      return ((ev.clientX - rect.left) / rect.width) * this.W;
    };

    this.svg.addEventListener('mousedown', (ev) => {
      if (ev.button !== 0) return;
      ev.preventDefault();
      dragStart = toLocal(ev);
      if (this.brushEl) {
        this.brushEl.setAttribute('x', dragStart);
        this.brushEl.setAttribute('width', 0);
        this.brushEl.classList.remove('hidden');
      }
    });
    this.svg.addEventListener('mousemove', (ev) => {
      if (dragStart != null) {
        const px = toLocal(ev);
        const x0 = Math.min(dragStart, px);
        const x1 = Math.max(dragStart, px);
        this.brushEl.setAttribute('x', x0);
        this.brushEl.setAttribute('width', Math.max(x1 - x0, 0));
        return;
      }
      const px = toLocal(ev);
      this.showTooltipAt(px, ev.clientX, ev.clientY);
    });
    window.addEventListener('mouseup', (ev) => {
      if (dragStart == null) return;
      const px = toLocal(ev);
      const x0 = Math.min(dragStart, px);
      const x1 = Math.max(dragStart, px);
      dragStart = null;
      this.brushEl.classList.add('hidden');
      if (x1 - x0 < 6) return;
      const [lo, hi] = this.zoom;
      const span = hi - lo;
      const innerW = this.W - this.padL - this.padR;
      const i0 = Math.max(0, Math.round(lo + ((x0 - this.padL) / innerW) * span));
      const i1 = Math.min(this.cum.length - 1, Math.round(lo + ((x1 - this.padL) / innerW) * span));
      if (i1 - i0 >= 2) {
        this.zoom = [i0, i1];
        this.render();
      }
    });
    this.svg.addEventListener('dblclick', () => this.resetZoom());
    this.svg.addEventListener('mouseleave', () => {
      this.tooltip.classList.add('hidden');
      this.cursor && this.cursor.classList.add('hidden');
      this.dot && this.dot.classList.add('hidden');
    });
  }

  showTooltipAt(px, clientX, clientY) {
    if (!this.cum.length) return;
    const [lo, hi] = this.zoom;
    const span = hi - lo;
    const innerW = this.W - this.padL - this.padR;
    let i = Math.round(lo + ((px - this.padL) / innerW) * span);
    i = Math.max(lo, Math.min(hi, i));
    const cumv = this.cum[i];
    const ddv = this.dd[i];
    const r = this.returns[i];
    const xpx = this.x(i);
    this.cursor.setAttribute('x1', xpx);
    this.cursor.setAttribute('x2', xpx);
    this.cursor.classList.remove('hidden');
    this.dot.setAttribute('cx', xpx);
    this.dot.setAttribute('cy', this.y(cumv));
    this.dot.classList.remove('hidden');
    this.tooltip.classList.remove('hidden');
    const rect = this.root.getBoundingClientRect();
    const tx = clientX - rect.left + 12;
    const ty = clientY - rect.top + 12;
    this.tooltip.style.left = `${Math.min(tx, rect.width - 200)}px`;
    this.tooltip.style.top = `${Math.min(ty, rect.height - 96)}px`;
    this.tooltip.innerHTML = `
      <div class="tt-date">${r.period_end}</div>
      <div class="tt-row"><span>Monthly</span><span class="${r.ret >= 0 ? 'pos' : 'neg'}">${(r.ret * 100).toFixed(2)}%</span></div>
      <div class="tt-row"><span>Cumulative</span><span class="${cumv >= 0 ? 'pos' : 'neg'}">${(cumv * 100).toFixed(1)}%</span></div>
      <div class="tt-row"><span>Drawdown</span><span class="neg">${(ddv * 100).toFixed(1)}%</span></div>
    `;
  }

  x(i) {
    const [lo, hi] = this.zoom;
    const innerW = this.W - this.padL - this.padR;
    return this.padL + ((i - lo) / Math.max(hi - lo, 1)) * innerW;
  }
  y(v) {
    return this.padT + this.eqH * (1 - (v - this.yMin) / Math.max(this.yMax - this.yMin, 1e-9));
  }
  yDD(v) {
    const top = this.padT + this.eqH + this.gap;
    return top + this.ddH * (1 - (v - this.ddMin) / Math.max(0 - this.ddMin, 1e-9));
  }

  render() {
    const s = this.svg;
    while (s.firstChild) s.removeChild(s.firstChild);
    if (this.cum.length < 2) return;
    const [lo, hi] = this.zoom;
    const slice = this.cum.slice(lo, hi + 1);
    const ddSlice = this.dd.slice(lo, hi + 1);
    const yMaxRaw = Math.max(...slice, 0);
    const yMinRaw = Math.min(...slice, 0);
    const span = (yMaxRaw - yMinRaw) || 0.01;
    this.yMax = yMaxRaw + span * 0.05;
    this.yMin = yMinRaw - span * 0.05;
    this.ddMin = Math.min(...ddSlice, -0.01);
    const ddTop = this.padT + this.eqH + this.gap;

    REGIMES.forEach((rg) => {
      const i0 = this.returns.findIndex((r) => r.period_end >= rg.from);
      let i1 = this.returns.findIndex((r) => r.period_end > rg.to);
      if (i1 === -1) i1 = this.returns.length;
      if (i0 < 0) return;
      const left = Math.max(i0, lo);
      const right = Math.min(i1 - 1, hi);
      if (right <= left) return;
      const x0 = this.x(left);
      const x1 = this.x(right);
      s.appendChild(svgEl('rect', {
        x: x0, y: this.padT, width: Math.max(x1 - x0, 1),
        height: this.eqH + this.gap + this.ddH,
        fill: 'rgba(224, 108, 117, 0.08)',
        stroke: 'rgba(224, 108, 117, 0.25)',
        'stroke-dasharray': '2 3',
      }));
      const lbl = svgEl('text', {
        x: x0 + 4, y: this.padT + 12,
        fill: 'rgba(224, 108, 117, 0.85)',
        'font-family': 'JetBrains Mono',
        'font-size': '9.5',
      });
      lbl.textContent = rg.label;
      s.appendChild(lbl);
    });

    const ticks = this.niceTicks(this.yMin, this.yMax, 4);
    ticks.forEach((t) => {
      const yp = this.y(t);
      s.appendChild(svgEl('line', {
        x1: this.padL, x2: this.W - this.padR,
        y1: yp, y2: yp,
        stroke: '#232a36', 'stroke-width': '1',
      }));
      const lbl = svgEl('text', {
        x: this.padL - 6, y: yp + 3,
        fill: '#6b7280', 'font-family': 'JetBrains Mono',
        'font-size': '9.5', 'text-anchor': 'end',
      });
      lbl.textContent = `${(t * 100).toFixed(0)}%`;
      s.appendChild(lbl);
    });

    s.appendChild(svgEl('line', {
      x1: this.padL, x2: this.W - this.padR,
      y1: this.y(0), y2: this.y(0),
      stroke: '#414755', 'stroke-dasharray': '2 4',
    }));

    const pts = slice.map((v, k) => `${this.x(lo + k)},${this.y(v)}`).join(' L ');
    s.appendChild(svgEl('path', {
      d: `M ${this.x(lo)},${this.y(0)} L ${pts} L ${this.x(hi)},${this.y(0)} Z`,
      fill: 'rgba(87, 199, 133, 0.12)',
    }));
    s.appendChild(svgEl('path', {
      d: `M ${pts}`,
      fill: 'none', stroke: '#57c785', 'stroke-width': '1.6',
    }));

    s.appendChild(svgEl('rect', {
      x: this.padL, y: ddTop,
      width: this.W - this.padL - this.padR, height: this.ddH,
      fill: '#0f1217', stroke: '#232a36',
    }));
    const ddPts = ddSlice.map((v, k) => `${this.x(lo + k)},${this.yDD(v)}`).join(' L ');
    s.appendChild(svgEl('path', {
      d: `M ${this.x(lo)},${this.yDD(0)} L ${ddPts} L ${this.x(hi)},${this.yDD(0)} Z`,
      fill: 'rgba(224, 108, 117, 0.18)',
    }));
    s.appendChild(svgEl('path', {
      d: `M ${ddPts}`,
      fill: 'none', stroke: '#e06c75', 'stroke-width': '1.2',
    }));
    s.appendChild(this._text(this.padL - 6, ddTop + 9, '0%', { anchor: 'end', color: '#6b7280' }));
    s.appendChild(this._text(this.padL - 6, ddTop + this.ddH - 2, `${(this.ddMin * 100).toFixed(0)}%`, { anchor: 'end', color: '#6b7280' }));
    s.appendChild(this._text(this.padL + 4, ddTop + 11, 'Drawdown', { color: '#6b7280' }));

    const nTicks = Math.min(5, Math.max(hi - lo, 1));
    for (let i = 0; i <= nTicks; i++) {
      const idx = Math.round(lo + ((hi - lo) * i) / Math.max(nTicks, 1));
      const xp = this.x(idx);
      s.appendChild(svgEl('line', {
        x1: xp, x2: xp,
        y1: ddTop + this.ddH, y2: ddTop + this.ddH + 3,
        stroke: '#414755',
      }));
      s.appendChild(this._text(xp, ddTop + this.ddH + 14, this.returns[idx].period_end, {
        anchor: i === 0 ? 'start' : i === nTicks ? 'end' : 'middle',
        color: '#8b90a0',
      }));
    }

    this.cursor = svgEl('line', {
      x1: 0, x2: 0, y1: this.padT, y2: ddTop + this.ddH,
      stroke: '#5b9dff', 'stroke-width': '1', 'stroke-dasharray': '2 3',
      class: 'hidden',
    });
    s.appendChild(this.cursor);
    this.dot = svgEl('circle', { cx: 0, cy: 0, r: 3, fill: '#57c785', stroke: '#0f1217', 'stroke-width': '1.5', class: 'hidden' });
    s.appendChild(this.dot);
    this.brushEl = svgEl('rect', {
      x: 0, y: this.padT, width: 0, height: this.eqH,
      fill: 'rgba(91, 157, 255, 0.18)', stroke: 'rgba(91, 157, 255, 0.6)',
      class: 'hidden',
    });
    s.appendChild(this.brushEl);
  }

  _text(x, y, str, { anchor = 'start', color = '#8b90a0' } = {}) {
    const t = svgEl('text', {
      x, y, fill: color,
      'font-family': 'JetBrains Mono', 'font-size': '9.5',
      'text-anchor': anchor,
    });
    t.textContent = str;
    return t;
  }

  niceTicks(lo, hi, n) {
    const span = hi - lo;
    if (span <= 0) return [lo];
    const raw = span / n;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
    const start = Math.ceil(lo / step) * step;
    const out = [];
    for (let v = start; v <= hi + 1e-12; v += step) out.push(Number(v.toFixed(10)));
    return out;
  }
}

// ---------- Robustness tab (Phase 4) ----------

const FAMILY_ORDER = ['lag', 'costs', 'subperiod', 'liquidity', 'data_quality', 'capacity'];
const FAMILY_LABEL = {
  lag: 'Execution lag',
  costs: 'Transaction costs',
  subperiod: 'Subperiod stability',
  liquidity: 'Liquidity filter',
  data_quality: 'Data quality',
  capacity: 'Capacity (AUM)',
};

function renderRobustness(robustness) {
  const root = $('#robustness-body');
  root.innerHTML = '';
  if (!robustness || !robustness.scorecard) {
    root.appendChild(emptyState('shield', 'No robustness report. Load the demo bundle or POST /api/robustness.'));
    return;
  }
  const { scorecard, judgment } = robustness;

  // Verdict banner
  if (judgment) {
    const verdict = el('div', { class: 'verdict-card' }, [
      el('div', { class: 'verdict-head' }, [
        el('span', { class: `pill ${judgment.confidence === 'high' ? 'ok' : judgment.confidence === 'medium' ? 'warn' : 'fail'}` }, `Confidence: ${capitalize(judgment.confidence)}`),
        el('span', { class: 'pill info' }, signalTypeLabel(judgment.signal_type)),
        el('span', { class: 'pill info' }, `Survived ${judgment.surviving_count}/${judgment.n_tests}`),
        el('span', { class: 'pill info' }, `Gap: ${gapAttributionLabel(judgment.gap_attribution)}`),
      ]),
      el('div', { class: 'verdict-headline' }, [
        el('span', { class: 'lbl' }, 'Implementable α / mo'),
        el('span', { class: 'val ' + (judgment.implementable_alpha >= 0 ? 'pos' : 'neg') }, fmtPct(judgment.implementable_alpha, 3)),
      ]),
      el('p', { class: 'verdict-summary' }, judgment.summary),
      el('div', { class: 'quote' }, [
        el('span', { class: 'meta' }, 'Implementable α basis'),
        document.createTextNode(judgment.implementable_alpha_basis),
      ]),
      el('div', { class: 'quote' }, [
        el('span', { class: 'meta' }, `Gap attribution · ${gapAttributionLabel(judgment.gap_attribution)}`),
        document.createTextNode(judgment.gap_attribution_evidence),
      ]),
    ]);
    root.appendChild(verdict);
  }

  // Scorecard KPIs
  const kpiBlock = el('div', { class: 'spec-section' });
  kpiBlock.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Scorecard summary')]));
  const kpis = el('div', { class: 'kpi-grid kpi-grid-6' }, [
    kpi('Baseline mo. return', fmtPct(scorecard.baseline_mean_return, 3), scorecard.baseline_mean_return >= 0 ? 'pos' : 'neg'),
    kpi('Baseline t-stat', fmtNum(scorecard.baseline_tstat, 2)),
    kpi('n periods', scorecard.baseline_n_periods),
    kpi('Tests / surviving', `${scorecard.n_surviving} / ${scorecard.n_tests}`),
    kpi('Lag half-life (d)', scorecard.lag_half_life_days != null ? fmtNum(scorecard.lag_half_life_days, 1) : '—'),
    kpi('Cost threshold (bps)', scorecard.cost_threshold_bps != null && Number.isFinite(scorecard.cost_threshold_bps) ? fmtNum(scorecard.cost_threshold_bps, 1) : '—'),
  ]);
  kpiBlock.appendChild(kpis);
  if (scorecard.capacity_estimate_usd != null) {
    kpiBlock.appendChild(el('div', { class: 'kpi-footnote' }, `Capacity @ 50bps impact: ${fmtUSD(scorecard.capacity_estimate_usd)}`));
  }
  root.appendChild(kpiBlock);

  // Fragility signals
  if (scorecard.fragility_signals && scorecard.fragility_signals.length) {
    const frag = el('div', { class: 'flag-banner flag-amber' }, [
      el('div', { class: 'head' }, `Fragility signals (${scorecard.fragility_signals.length})`),
      ...scorecard.fragility_signals.map((s) => el('div', {}, `• ${s}`)),
    ]);
    root.appendChild(frag);
  }

  // Primary failure modes from judgment
  if (judgment && judgment.primary_failure_modes && judgment.primary_failure_modes.length) {
    const fm = el('div', { class: 'spec-section' });
    fm.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Primary failure modes (D3)')]));
    const list = el('div', { class: 'tag-row' });
    judgment.primary_failure_modes.forEach((m) => list.appendChild(el('span', { class: 'pill warn' }, m.replace(/_/g, ' '))));
    fm.appendChild(list);
    root.appendChild(fm);
  }

  // Tests grouped by family
  const grouped = new Map();
  FAMILY_ORDER.forEach((f) => grouped.set(f, []));
  (scorecard.tests || []).forEach((t) => {
    if (!grouped.has(t.family)) grouped.set(t.family, []);
    grouped.get(t.family).push(t);
  });

  // Decay charts (lag + costs) — show side-by-side BEFORE the per-family tables.
  const lagTests = (grouped.get('lag') || []).slice().sort((a, b) =>
    (a.parameter_swept?.signal_lag_days ?? 0) - (b.parameter_swept?.signal_lag_days ?? 0)
  );
  const costTests = (grouped.get('costs') || []).slice().sort((a, b) =>
    (a.parameter_swept?.transaction_cost_bps ?? 0) - (b.parameter_swept?.transaction_cost_bps ?? 0)
  );
  if (lagTests.length >= 2 || costTests.length >= 2) {
    const decayBlock = el('div', { class: 'spec-section' });
    decayBlock.appendChild(el('div', { class: 'spec-section-head' }, [
      el('h3', {}, 'Decay curves'),
      el('span', { class: 'pill info' }, 'mean monthly return vs swept parameter'),
    ]));
    const grid = el('div', { class: 'decay-grid' });
    if (lagTests.length >= 2) {
      grid.appendChild(buildDecayCard({
        title: 'Execution lag',
        xLabel: 'signal_lag_days',
        marker: scorecard.lag_half_life_days,
        markerLabel: scorecard.lag_half_life_days != null ? `half-life ${fmtNum(scorecard.lag_half_life_days, 1)}d` : null,
        points: lagTests.map((t) => ({
          x: t.parameter_swept?.signal_lag_days ?? 0,
          y: t.headline_metric,
          surviving: t.surviving,
          name: t.name,
        })),
      }));
    }
    if (costTests.length >= 2) {
      grid.appendChild(buildDecayCard({
        title: 'Transaction costs',
        xLabel: 'transaction_cost_bps',
        marker: scorecard.cost_threshold_bps,
        markerLabel: scorecard.cost_threshold_bps != null && Number.isFinite(scorecard.cost_threshold_bps) ? `α=0 @ ${fmtNum(scorecard.cost_threshold_bps, 1)}bps` : null,
        points: costTests.map((t) => ({
          x: t.parameter_swept?.transaction_cost_bps ?? 0,
          y: t.headline_metric,
          surviving: t.surviving,
          name: t.name,
        })),
      }));
    }
    decayBlock.appendChild(grid);
    root.appendChild(decayBlock);
  }

  grouped.forEach((tests, fam) => {
    if (!tests.length) return;
    const surv = tests.filter((t) => t.surviving).length;
    const block = el('div', { class: 'spec-section' });
    block.appendChild(el('div', { class: 'spec-section-head' }, [
      el('h3', {}, `${FAMILY_LABEL[fam] || capitalize(fam)} · ${tests.length} runs`),
      el('span', { class: `pill ${surv === tests.length ? 'ok' : surv === 0 ? 'fail' : 'warn'}` }, `${surv}/${tests.length} surviving`),
    ]));
    const t = el('table', { class: 'data' });
    t.appendChild(el('thead', {}, [
      el('tr', {}, [
        el('th', {}, 'Test'),
        el('th', {}, 'Swept'),
        el('th', {}, 'Mean/AUM'),
        el('th', {}, 't-stat'),
        el('th', {}, 'n'),
        el('th', {}, 'Status'),
        el('th', {}, 'Notes'),
      ]),
    ]));
    const tb = el('tbody');
    tests.forEach((r) => {
      // capacity rows put AUM in headline_metric; everything else is a monthly return.
      const headlineCell = fam === 'capacity'
        ? fmtUSD(r.headline_metric)
        : fmtPct(r.headline_metric, 3);
      const headlineClass = fam === 'capacity' ? '' : (r.headline_metric >= 0 ? 'pos' : 'neg');
      tb.appendChild(el('tr', {}, [
        el('td', {}, r.name),
        el('td', { class: 'num muted' }, formatSwept(r.parameter_swept)),
        el('td', { class: 'num ' + headlineClass }, headlineCell),
        el('td', { class: 'num' }, r.headline_tstat != null ? fmtNum(r.headline_tstat, 2) : '—'),
        el('td', { class: 'num' }, r.n_periods),
        el('td', {}, [el('span', { class: 'pill ' + (r.surviving ? 'ok' : 'fail') }, r.surviving ? 'survive' : 'fail')]),
        el('td', { class: 'muted' }, r.notes || '—'),
      ]));
    });
    t.appendChild(tb);
    block.appendChild(t);
    root.appendChild(block);
  });
}

function kpi(label, value, extraClass = '') {
  return el('div', { class: 'kpi' }, [
    el('label', {}, label),
    el('span', { class: 'val ' + extraClass }, String(value)),
  ]);
}

function signalTypeLabel(t) {
  return {
    structural: 'Structural premium',
    information_based: 'Information-based',
    microstructure: 'Microstructure / reversal',
    stale: 'Stale / dead',
    not_evaluated: 'Not evaluated',
  }[t] || t;
}

function gapAttributionLabel(g) {
  return {
    data_limitation: 'Data limitation',
    methodology_fragility: 'Methodology fragility',
    post_publication_decay: 'Post-publication decay',
    unexplained: 'Unexplained',
  }[g] || g;
}

function formatSwept(obj) {
  if (!obj || typeof obj !== 'object') return '—';
  return Object.entries(obj).map(([k, v]) => `${k}=${v}`).join(', ');
}

function fmtUSD(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  if (!Number.isFinite(x)) return '∞';
  const abs = Math.abs(x);
  if (abs >= 1e9) return `$${(x / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(x / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(x / 1e3).toFixed(1)}K`;
  return `$${x.toFixed(0)}`;
}

// Build a small decay card (lag/cost sweep) — line + points, with hover dots
// and an optional vertical marker for half-life / cost-threshold.
function buildDecayCard({ title, xLabel, points, marker, markerLabel }) {
  const card = el('div', { class: 'decay-card' });
  card.appendChild(el('div', { class: 'decay-head' }, [
    el('span', { class: 'decay-title' }, title),
    el('span', { class: 'decay-x' }, xLabel),
  ]));
  const W = 360, H = 160, padL = 38, padR = 14, padT = 12, padB = 24;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys, 0), yMax = Math.max(...ys, 0);
  const ySpan = (yMax - yMin) || 0.001;
  const yLo = yMin - ySpan * 0.1;
  const yHi = yMax + ySpan * 0.1;
  const x = (v) => padL + ((v - xMin) / Math.max(xMax - xMin, 1e-9)) * (W - padL - padR);
  const y = (v) => padT + (1 - (v - yLo) / (yHi - yLo)) * (H - padT - padB);

  const s = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'decay-svg' });

  // Y grid (3 ticks)
  const yticks = [yLo, (yLo + yHi) / 2, yHi];
  // Replace mid tick with 0 if it lies in range — more useful baseline
  if (yLo < 0 && yHi > 0) yticks[1] = 0;
  yticks.forEach((t) => {
    const yp = y(t);
    s.appendChild(svgEl('line', {
      x1: padL, x2: W - padR, y1: yp, y2: yp,
      stroke: t === 0 ? '#414755' : '#232a36',
      'stroke-dasharray': t === 0 ? '2 4' : '',
    }));
    const lbl = svgEl('text', {
      x: padL - 5, y: yp + 3,
      fill: '#6b7280', 'font-family': 'JetBrains Mono',
      'font-size': '9', 'text-anchor': 'end',
    });
    lbl.textContent = `${(t * 100).toFixed(2)}%`;
    s.appendChild(lbl);
  });

  // Marker line (cost threshold / lag half-life)
  if (marker != null && Number.isFinite(marker) && marker >= xMin && marker <= xMax) {
    const xp = x(marker);
    s.appendChild(svgEl('line', {
      x1: xp, x2: xp, y1: padT, y2: H - padB,
      stroke: '#e0a458', 'stroke-width': '1', 'stroke-dasharray': '3 3',
    }));
    if (markerLabel) {
      const lbl = svgEl('text', {
        x: xp + 4, y: padT + 9,
        fill: '#e0a458', 'font-family': 'JetBrains Mono',
        'font-size': '9.5',
      });
      lbl.textContent = markerLabel;
      s.appendChild(lbl);
    }
  }

  // Connecting line
  const sortedPts = points.slice().sort((a, b) => a.x - b.x);
  const lineD = `M ${sortedPts.map((p) => `${x(p.x)},${y(p.y)}`).join(' L ')}`;
  s.appendChild(svgEl('path', {
    d: lineD, fill: 'none', stroke: '#5b9dff', 'stroke-width': '1.4',
  }));

  // Points colored by surviving
  sortedPts.forEach((p) => {
    const c = svgEl('circle', {
      cx: x(p.x), cy: y(p.y), r: 3.5,
      fill: p.surviving ? '#57c785' : '#e06c75',
      stroke: '#0f1217', 'stroke-width': '1.2',
    });
    const title = svgEl('title');
    title.textContent = `${p.name}: x=${p.x}, mean=${(p.y * 100).toFixed(2)}%, ${p.surviving ? 'survive' : 'fail'}`;
    c.appendChild(title);
    s.appendChild(c);
    // Value label above point
    const t = svgEl('text', {
      x: x(p.x), y: y(p.y) - 6,
      fill: p.surviving ? '#57c785' : '#e06c75',
      'font-family': 'JetBrains Mono', 'font-size': '9',
      'text-anchor': 'middle',
    });
    t.textContent = `${(p.y * 100).toFixed(2)}%`;
    s.appendChild(t);
  });

  // X-axis ticks (one per point)
  sortedPts.forEach((p) => {
    const xp = x(p.x);
    s.appendChild(svgEl('line', {
      x1: xp, x2: xp, y1: H - padB, y2: H - padB + 3,
      stroke: '#414755',
    }));
    const t = svgEl('text', {
      x: xp, y: H - padB + 13,
      fill: '#8b90a0', 'font-family': 'JetBrains Mono',
      'font-size': '9', 'text-anchor': 'middle',
    });
    t.textContent = String(p.x);
    s.appendChild(t);
  });

  card.appendChild(s);
  return card;
}

// ---------- Diagnosis tab (D2) ----------

function renderDiagnosis(payload, paperClaim) {
  const root = $('#diagnosis-body');
  root.innerHTML = '';
  if (!payload) {
    root.appendChild(emptyState('biotech', 'No divergence diagnosis. Load the demo bundle or POST /api/diagnose.'));
    return;
  }
  // /api/diagnose and the demo bundle wrap the payload as {diagnosis, n_experiments}.
  const diagnosis = payload.diagnosis || payload;
  if (!diagnosis.primary_cause) {
    root.appendChild(emptyState('biotech', 'No divergence diagnosis. Load the demo bundle or POST /api/diagnose.'));
    return;
  }

  // Primary cause banner
  const causeClass = diagnosis.confidence === 'high' ? 'ok' : diagnosis.confidence === 'medium' ? 'warn' : 'fail';
  const banner = el('div', { class: 'verdict-card' }, [
    el('div', { class: 'verdict-head' }, [
      el('span', { class: `pill ${causeClass}` }, `Confidence: ${capitalize(diagnosis.confidence)}`),
      el('span', { class: 'pill info' }, `Kind: ${primaryCauseKindLabel(diagnosis.primary_cause_kind)}`),
      el('span', { class: 'pill info' }, `${diagnosis.experiments_run} experiment${diagnosis.experiments_run === 1 ? '' : 's'}`),
      diagnosis.early_exit ? el('span', { class: 'pill warn' }, 'Early exit') : null,
    ]),
    el('div', { class: 'verdict-headline' }, [
      el('span', { class: 'lbl' }, 'Primary cause'),
      el('span', { class: 'val' }, diagnosis.primary_cause),
    ]),
    el('p', { class: 'verdict-summary' }, diagnosis.primary_cause_summary),
    el('div', { class: 'quote' }, [
      el('span', { class: 'meta' }, 'Evidence (engine-grounded)'),
      document.createTextNode(diagnosis.primary_cause_evidence),
    ]),
  ]);
  root.appendChild(banner);

  // Residual gap summary
  const resid = el('div', { class: 'spec-section' });
  resid.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Residual gap')]));
  const dl = el('dl', { class: 'spec-section-body' });
  [
    ['residual_abs_gap (mo)', fmtPct(diagnosis.residual_abs_gap, 3)],
    ['paper claim (mo)', paperClaim ? fmtPct(paperClaim.monthly_return, 3) : '—'],
    ['likely cause', diagnosis.residual_gap_likely_cause],
  ].forEach(([k, v]) => {
    dl.appendChild(el('dt', {}, k));
    dl.appendChild(el('dd', {}, String(v)));
  });
  resid.appendChild(dl);
  root.appendChild(resid);

  // Gap-closure waterfall — visualize how each mutation changed the gap
  if ((diagnosis.mutation_results || []).length) {
    const wf = el('div', { class: 'spec-section' });
    wf.appendChild(el('div', { class: 'spec-section-head' }, [
      el('h3', {}, 'Gap closure (waterfall)'),
      el('span', { class: 'pill info' }, 'green = closed gap · red = widened'),
    ]));
    wf.appendChild(buildGapWaterfall(diagnosis.mutation_results, paperClaim));
    root.appendChild(wf);
  }

  // Mutation results
  const mut = el('div', { class: 'spec-section' });
  const mutCount = (diagnosis.mutation_results || []).length;
  mut.appendChild(el('div', { class: 'spec-section-head' }, [
    el('h3', {}, `Experiments (${mutCount})`),
    diagnosis.early_exit ? el('span', { class: 'pill warn' }, 'Early exit — gap below threshold') : null,
  ]));
  if (mutCount === 0) {
    mut.appendChild(el('div', { class: 'quote' }, [
      el('span', { class: 'meta' }, 'No mutations were run'),
      document.createTextNode(diagnosis.early_exit_reason || 'D2 early-exited without proposing any spec mutations. This usually means the paper-vs-replication gap was already below the early-exit threshold.'),
    ]));
    root.appendChild(mut);
    return;
  }
  const t = el('table', { class: 'data' });
  t.appendChild(el('thead', {}, [
    el('tr', {}, [
      el('th', {}, 'Field'),
      el('th', {}, 'From'),
      el('th', {}, 'To'),
      el('th', {}, 'Pre gap'),
      el('th', {}, 'Post gap'),
      el('th', {}, 'Δ (pre−post)'),
      el('th', {}, 'Pre μ'),
      el('th', {}, 'Post μ'),
      el('th', {}, 'Expected'),
      el('th', {}, 'Outcome'),
    ]),
  ]));
  const tb = el('tbody');
  (diagnosis.mutation_results || []).forEach((m) => {
    const p = m.proposal;
    const delta = m.gap_delta;
    const outcome = delta > 0 ? 'closed' : delta < 0 ? 'widened' : 'flat';
    const outcomeClass = delta > 0 ? 'ok' : delta < 0 ? 'fail' : 'info';
    tb.appendChild(el('tr', {}, [
      el('td', { class: 'mono' }, p.parameter),
      el('td', { class: 'mono muted' }, m.from_value_human),
      el('td', { class: 'mono' }, p.to_value),
      el('td', { class: 'num' }, fmtPct(m.pre_abs_gap, 3)),
      el('td', { class: 'num' }, fmtPct(m.post_abs_gap, 3)),
      el('td', { class: 'num ' + (delta >= 0 ? 'pos' : 'neg') }, (delta >= 0 ? '+' : '') + fmtPct(delta, 3)),
      el('td', { class: 'num' }, fmtPct(m.pre_mean_return, 3)),
      el('td', { class: 'num' }, fmtPct(m.post_mean_return, 3)),
      el('td', {}, [el('span', { class: 'pill info' }, p.expected_direction)]),
      el('td', {}, [el('span', { class: `pill ${outcomeClass}` }, outcome)]),
    ]));
  });
  t.appendChild(tb);
  mut.appendChild(t);
  root.appendChild(mut);

  // Rationale cards for each mutation
  (diagnosis.mutation_results || []).forEach((m) => {
    const card = el('div', { class: 'spec-section' });
    card.appendChild(el('div', { class: 'spec-section-head' }, [
      el('h3', {}, `${m.proposal.parameter} → ${m.proposal.to_value}`),
      el('span', { class: 'pill ' + (m.closed_sign_flip ? 'ok' : 'info') }, m.closed_sign_flip ? 'sign flipped' : 'no flip'),
    ]));
    card.appendChild(el('div', { class: 'quote' }, [
      el('span', { class: 'meta' }, 'D2 rationale'),
      document.createTextNode(m.proposal.rationale),
    ]));
    if (m.notes) {
      card.appendChild(el('div', { class: 'quote' }, [
        el('span', { class: 'meta' }, 'Run notes'),
        document.createTextNode(m.notes),
      ]));
    }
    root.appendChild(card);
  });

  // Alternatives ruled out
  if (diagnosis.alternatives_ruled_out && diagnosis.alternatives_ruled_out.length) {
    const rr = el('div', { class: 'spec-section' });
    rr.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Alternatives ruled out')]));
    const row = el('div', { class: 'tag-row' });
    diagnosis.alternatives_ruled_out.forEach((a) => row.appendChild(el('span', { class: 'pill info mono' }, a)));
    rr.appendChild(row);
    root.appendChild(rr);
  }
}

function primaryCauseKindLabel(k) {
  return {
    single_field: 'Single field',
    coupled: 'Coupled fields',
    data_window: 'Data window',
    other: 'Other',
  }[k] || k;
}

// Visualize each D2 mutation as a horizontal bar from pre_abs_gap → post_abs_gap.
// Green segment = portion of the gap closed; red = gap widened beyond pre.
function buildGapWaterfall(muts, paperClaim) {
  const wrap = el('div', { class: 'waterfall-wrap' });
  const W = 760, rowH = 34, padL = 220, padR = 70, padT = 18, padB = 22;
  const H = padT + padB + rowH * muts.length + 8;
  const allGaps = muts.flatMap((m) => [m.pre_abs_gap, m.post_abs_gap]);
  const maxGap = Math.max(...allGaps, 0.001);
  const x = (g) => padL + (g / maxGap) * (W - padL - padR);

  const s = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'waterfall-svg' });

  // Top header — axis at gap=0
  s.appendChild(svgEl('line', {
    x1: padL, x2: padL, y1: padT - 4, y2: H - padB + 4,
    stroke: '#414755', 'stroke-width': '1',
  }));
  // gridlines at 25/50/75/100% of maxGap
  [0.25, 0.5, 0.75, 1.0].forEach((frac) => {
    const xp = padL + frac * (W - padL - padR);
    s.appendChild(svgEl('line', {
      x1: xp, x2: xp, y1: padT - 4, y2: H - padB + 4,
      stroke: '#232a36', 'stroke-dasharray': '2 4',
    }));
    const lbl = svgEl('text', {
      x: xp, y: padT - 6,
      fill: '#6b7280', 'font-family': 'JetBrains Mono',
      'font-size': '9.5', 'text-anchor': 'middle',
    });
    lbl.textContent = `${(frac * maxGap * 100).toFixed(2)}%`;
    s.appendChild(lbl);
  });
  s.appendChild(svgEl('text', {
    x: padL, y: padT - 6,
    fill: '#6b7280', 'font-family': 'JetBrains Mono',
    'font-size': '9.5', 'text-anchor': 'start',
  })).textContent = '0';
  s.appendChild(svgEl('text', {
    x: 6, y: padT - 6,
    fill: '#6b7280', 'font-family': 'Inter',
    'font-size': '10', 'text-anchor': 'start',
  })).textContent = 'Mutation';
  s.appendChild(svgEl('text', {
    x: padL + 6, y: H - 4,
    fill: '#6b7280', 'font-family': 'Inter',
    'font-size': '10',
  })).textContent = '|paper − replication| (monthly return)';

  muts.forEach((m, i) => {
    const yTop = padT + i * rowH + 4;
    const yMid = yTop + rowH / 2 - 2;
    const xPre = x(m.pre_abs_gap);
    const xPost = x(m.post_abs_gap);
    const closed = m.pre_abs_gap - m.post_abs_gap;
    const closedPct = m.pre_abs_gap > 0 ? (closed / m.pre_abs_gap) * 100 : 0;

    // Label (parameter → to_value)
    const lblP = svgEl('text', {
      x: padL - 10, y: yMid - 2,
      fill: '#e4e6eb', 'font-family': 'JetBrains Mono',
      'font-size': '11', 'text-anchor': 'end',
    });
    lblP.textContent = m.proposal.parameter;
    s.appendChild(lblP);
    const lblV = svgEl('text', {
      x: padL - 10, y: yMid + 10,
      fill: '#8b90a0', 'font-family': 'JetBrains Mono',
      'font-size': '10', 'text-anchor': 'end',
    });
    lblV.textContent = `${m.from_value_human} → ${m.proposal.to_value}`;
    s.appendChild(lblV);

    // Pre-gap bar (always drawn, full extent, in muted blue)
    s.appendChild(svgEl('rect', {
      x: padL, y: yMid - 7, width: Math.max(xPre - padL, 0), height: 14,
      fill: 'rgba(91, 157, 255, 0.18)', stroke: 'rgba(91, 157, 255, 0.5)',
    }));

    if (closed >= 0) {
      // Gap closed: green segment from xPost → xPre (the closed portion)
      s.appendChild(svgEl('rect', {
        x: xPost, y: yMid - 7,
        width: Math.max(xPre - xPost, 1), height: 14,
        fill: 'rgba(87, 199, 133, 0.55)', stroke: '#57c785',
      }));
    } else {
      // Gap widened: red segment from xPre → xPost
      s.appendChild(svgEl('rect', {
        x: xPre, y: yMid - 7,
        width: Math.max(xPost - xPre, 1), height: 14,
        fill: 'rgba(224, 108, 117, 0.55)', stroke: '#e06c75',
      }));
    }

    // Markers for pre and post
    s.appendChild(svgEl('line', {
      x1: xPre, x2: xPre, y1: yMid - 9, y2: yMid + 9,
      stroke: '#5b9dff', 'stroke-width': '1.6',
    }));
    s.appendChild(svgEl('line', {
      x1: xPost, x2: xPost, y1: yMid - 9, y2: yMid + 9,
      stroke: closed >= 0 ? '#57c785' : '#e06c75', 'stroke-width': '1.6',
    }));

    // Right-side caption: post_abs_gap and % closed
    const captionX = W - padR + 4;
    const cap1 = svgEl('text', {
      x: captionX, y: yMid - 1,
      fill: closed >= 0 ? '#57c785' : '#e06c75',
      'font-family': 'JetBrains Mono', 'font-size': '11',
    });
    cap1.textContent = `${(m.post_abs_gap * 100).toFixed(2)}%`;
    s.appendChild(cap1);
    const cap2 = svgEl('text', {
      x: captionX, y: yMid + 10,
      fill: '#8b90a0', 'font-family': 'JetBrains Mono', 'font-size': '9.5',
    });
    cap2.textContent = `${closedPct >= 0 ? '−' : '+'}${Math.abs(closedPct).toFixed(0)}% gap`;
    s.appendChild(cap2);
  });

  wrap.appendChild(s);

  // Legend / summary line
  const headlinePaper = paperClaim ? `paper claim ${(paperClaim.monthly_return * 100).toFixed(2)}%/mo` : null;
  const legend = el('div', { class: 'waterfall-legend' }, [
    el('span', {}, [
      el('span', { class: 'legend-swatch swatch-pre' }), ' pre-gap',
    ]),
    el('span', {}, [
      el('span', { class: 'legend-swatch swatch-closed' }), ' closed',
    ]),
    el('span', {}, [
      el('span', { class: 'legend-swatch swatch-widened' }), ' widened',
    ]),
    headlinePaper ? el('span', { class: 'muted' }, headlinePaper) : null,
  ]);
  wrap.appendChild(legend);
  return wrap;
}

// ---------- Provenance tab ----------

function renderProvenance(bt) {
  const root = $('#provenance-body');
  root.innerHTML = '';
  if (!bt || !bt.provenance) {
    root.appendChild(emptyState('hub', 'No provenance chain to show.'));
    return;
  }
  // Our demo stops at 2 levels, but real runs chain deeper.
  const engine = bt.provenance;
  const engineNode = el('div', { class: 'prov-node synthesized' }, [
    el('div', { class: 'tier' }, `Level 2 · ${capitalize(engine.source_tier)}`),
    el('div', { class: 'source' }, engine.source_id),
    el('div', { class: 'note' }, [
      `record_id: ${engine.record_id}`,
      el('br'), `as_of_date: ${engine.as_of_date ?? '—'}`,
      el('br'), `retrieved_at: ${engine.retrieved_at}`,
      ...(engine.notes ? [el('br'), `notes: ${engine.notes}`] : []),
    ]),
  ]);
  // Parent we don't have the full body for; derive from flags.
  const dataNode = el('div', { class: 'prov-node' }, [
    el('div', { class: 'tier' }, `Level 1 · Primary`),
    el('div', { class: 'source' }, 'defeatbeta_yahoo (derived parent)'),
    el('div', { class: 'note' }, [
      `parent_ids: ${(engine.parent_ids || []).join(', ') || '—'}`,
      ...(bt.data_quality_flags || []).map((f) => el('div', {}, `flag: ${f}`)),
    ]),
  ]);
  root.appendChild(dataNode);
  root.appendChild(engineNode);
}

// ---------- empty state ----------

function emptyState(icon, msg) {
  return el('div', { class: 'empty-state' }, [
    el('span', { class: 'material-symbols-outlined' }, icon),
    msg,
  ]);
}

// ---------- boot ----------

wireDialForm();
refreshPapers();
refreshOverrideChip();
refreshRunButton();
log('SYS', 'UI ready. Click Load demo or drop a PDF.', 'sys');
setStatus('Idle — no paper loaded', 'gray');
