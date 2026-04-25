// Paper Replication Machine — MVP UI controller.
// Fetches /api/demo or runs /api/extract on uploaded PDFs, then renders the
// bundle across Overview / Spec / Verification / Critique / Backtest /
// Robustness / Diagnosis / Lineage.

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

// ---------- terminology map ----------
//
// Translate snake_case schema field names into professional finance prose
// for any user-facing surface. Anything that *displays* a field key should
// route it through term() so we never leak technical identifiers into the
// briefing.

const terminologyMap = {
  // Headline + result metrics
  monthly_return:           'Monthly Excess Return',
  mean_return:              'Monthly Excess Return',
  annualized_return:        'Annualised Return',
  volatility:               'Annualised Volatility',
  'volatility (ann)':       'Annualised Volatility',
  sharpe:                   'Sharpe Ratio',
  sharpe_ratio:             'Sharpe Ratio',
  t_stat:                   't-statistic (Newey-West)',
  tstat:                    't-statistic (Newey-West)',
  alpha_tstat:              't-statistic (Newey-West)',
  newey_west_lag:           'Newey-West Lag',
  max_drawdown:             'Maximum Drawdown',
  hit_rate:                 'Hit Rate',
  n_periods:                'Observation Periods',
  baseline_mean_return:     'Baseline Monthly Excess Return',
  baseline_tstat:           'Baseline t-statistic',
  baseline_n_periods:       'Baseline Observation Periods',
  return_convention:        'Return Convention',
  spec_hash:                'Specification Hash',
  turnover:                 'Turnover (one-sided, monthly)',
  'turnover (one-sided, /mo)': 'Turnover (one-sided, monthly)',
  transaction_cost_bps:     'Transaction Cost (bps)',
  // Headline claim block
  metric:                   'Reported Metric',
  window_label:             'Sample Window',
  paper_location:           'Paper Citation',
  // Header block
  paper_id:                 'Paper Identifier',
  paper_title:              'Paper Title',
  window:                   'Sample Window',
  base_currency:            'Base Currency',
  notes:                    'Notes',
  // Universe
  region:                   'Region',
  asset_class:              'Asset Class',
  min_price:                'Minimum Price Filter',
  exchanges:                'Exchanges',
  name:                     'Name',
  // Signal
  kind:                     'Signal Type',
  formula:                  'Signal Formula',
  lookback_months:          'Lookback Period',
  skip_months:              'Implementation Lag',
  direction:                'Signal Direction',
  frequency:                'Frequency',
  // Portfolio
  construction:             'Weighting Methodology',
  n_buckets:                'Portfolio Quantiles',
  long_bucket:              'Long Leg',
  short_bucket:             'Short Leg',
  weighting:                'Weighting Scheme',
  long_short:               'Long/Short Construction',
  gross_exposure:           'Gross Exposure',
  use_nyse_breakpoints:     'Uses NYSE Breakpoints',
  // Rebalance
  execution_lag_days:       'Execution Lag (days)',
  holding_period_months:    'Holding Period (months)',
  signal_date_convention:   'Signal Date Convention',
  execution_date_convention:'Execution Date Convention',
  // Robustness
  lag_half_life_days:       'Signal Decay Half-Life (days)',
  cost_threshold_bps:       'Transaction Cost Tolerance (bps)',
  capacity_estimate_usd:    'Estimated Capacity (USD)',
  // Engine flags
  data_quality_flags:       'Data Integrity Notes',
  fragility_signals:        'Fragility Signals',
  // Headline claim object name
  headline_claim:           'Paper Reported Result',
  // Window comparison
  'window (engine)':        'Replication Window',
  'window (paper)':         'Paper Window',
};

// Lookup a label for a field key. Falls back to a humanised version of the
// raw key — split snake_case into Title Case — so unmapped fields still
// look prose-like rather than code-like.
function term(key) {
  if (key == null) return '';
  const k = String(key);
  if (Object.prototype.hasOwnProperty.call(terminologyMap, k)) return terminologyMap[k];
  // Humanise as fallback: replace _ and . with spaces and Title Case.
  return k
    .replace(/[._]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .map((w) => w.length ? w[0].toUpperCase() + w.slice(1) : w)
    .join(' ');
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
  const universeNameEl = $('#dial-universe-name');
  return {
    universe_name: universeNameEl ? universeNameEl.value : '',
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
  // Universe-name override routes the engine to a different data source
  // (defeatbeta stocks vs. Ken French factors). When the dropdown is at
  // its blank default we leave A1's value untouched.
  if (dial.universe_name) out.universe.name = dial.universe_name;
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

function readPaperClaimOverride() {
  // Optional manual headline-claim override. Returns null when no value
  // is supplied. Forms a flat paper_claim shape compatible with the rest
  // of the frontend (legacy bundle.paper_claim consumed by runDiagnosis,
  // verdict strip, paper-vs-replication table).
  const retEl = document.getElementById('claim-override-return');
  const tEl = document.getElementById('claim-override-tstat');
  const winEl = document.getElementById('claim-override-window');
  if (!retEl) return null;
  const retPct = parseFloat(retEl.value);
  if (Number.isNaN(retPct)) return null;
  const tstat = parseFloat(tEl ? tEl.value : '');
  return {
    monthly_return: retPct / 100,            // % → decimal
    tstat: Number.isNaN(tstat) ? null : tstat,
    window: (winEl && winEl.value.trim()) || 'user-supplied override',
    paper_location: 'manual override (Run config panel)',
    overridden_by_user: true,
  };
}

async function runPipelineFromDials() {
  if (!state.selectedPaperId) {
    log('SYS', 'No paper selected. Upload a PDF or pick from the list.', 'sys');
    return;
  }
  if (state.running) return;
  state.running = true;
  refreshRunButton();
  // Drive the same stepper the upload-path uses. The dials flow skips B
  // (data mapper) — applyDials is its client-side analog — so mark it done
  // immediately. Each per-stage helper below toggles its own stepper state.
  resetStepper();
  setStage('parse', 'done');
  setStage('b', 'done');
  const t0 = performance.now();
  try {
    log('SYS', `Run pipeline → ${state.selectedPaperId} (dial overrides will be applied at each stage).`, 'sys');
    await runExtraction(state.selectedPaperId);
    collapseStepper(performance.now() - t0);
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
  // Keep stale sidenav-links lookup as a no-op fallback (sidebar removed).
  $$('.sidenav-links a').forEach((a) => a.classList.toggle('active', a.dataset.tab === name));
  // Highlight the stepper step(s) whose stage maps to this tab.
  $$('.ps-step').forEach((s) => {
    const stage = s.getAttribute('data-stage');
    const mapped = STEPPER_STAGE_TO_TAB[stage];
    s.classList.toggle('ps-current', mapped === name);
  });
}

// Stage → tab mapping. Some stages share a target tab (b → spec because
// data-mapping output renders in the Methodology tab; d3 → robustness
// because the CIO verdict is rendered alongside the scorecard). Both
// stepper boxes still light up and remain clickable.
const STEPPER_STAGE_TO_TAB = {
  parse: 'overview',
  a1: 'spec',
  a2: 'verification',
  a3: 'critique',
  b: 'spec',
  engine: 'backtest',
  d2: 'diagnosis',
  battery: 'robustness',
  d3: 'robustness',
};

// Click handler — only fires when the step is in 'done' or 'failed' state
// (the disabled attribute is removed in setStage when the stage finishes).
function onStepperClick(ev) {
  const btn = ev.currentTarget;
  const state = btn.getAttribute('data-state');
  if (state !== 'done' && state !== 'failed') return;
  const stage = btn.getAttribute('data-stage');
  const tab = STEPPER_STAGE_TO_TAB[stage];
  if (tab) showTab(tab);
}
$$('.ps-step').forEach((s) => s.addEventListener('click', onStepperClick));

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

// Reset the CIO Summary strip to its dash-placeholder state. The strip
// itself stays visible (it's the user's primary verdict surface — hiding
// it on reset / fresh load loses the navigation reference). Only the
// value cells get blanked back to dashes.
function resetVerdictStrip() {
  const strip = $('#verdict-strip');
  if (strip) strip.hidden = false;
  const blanks = [
    'vs-paper-title', 'vs-claim-value', 'vs-claim-tstat', 'vs-claim-loc',
    'vs-impl-value', 'vs-impl-tstat', 'vs-tag',
    'vs-paper-window', 'vs-engine-window', 'vs-gap-attr',
  ];
  blanks.forEach((id) => { const n = document.getElementById(id); if (n) n.textContent = '—'; });
  // Summary block is always blank on reset — :empty CSS rule hides the
  // block entirely until a bundle populates it.
  const summary = document.getElementById('vs-summary');
  if (summary) summary.textContent = '';
  const conf = $('#vs-confidence'); if (conf) conf.innerHTML = '';
  const verdictCol = $('#vs-col-verdict'); if (verdictCol) verdictCol.removeAttribute('data-tag');
  const pop = $('#vs-why-popover'); if (pop) { pop.hidden = true; pop.textContent = ''; }
  const dbt = $('#vs-dbt-toggle'); if (dbt) dbt.hidden = true;
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
  state.lastSeededPaperId = null;
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
  resetVerdictStrip();
  const stepper = $('#pipeline-stepper'); if (stepper) stepper.hidden = true;
  const dbtPanel = $('#dbt-panel'); if (dbtPanel) dbtPanel.hidden = true;
  refreshOverrideChip();
  refreshRunButton();
  showTab('overview');
});

// ---------- file upload ----------

// pick-btn lives INSIDE the dropzone, so a button click bubbles up to the
// dropzone click handler — both call $('#file-input').click(), opening
// the file picker twice in rapid succession (browser cancels the first,
// shows the second; user perceives it as "had to click twice"). Fix:
// stop propagation on the button, and short-circuit the dropzone handler
// when the click target is the button (or descendant of it).
$('#pick-btn').addEventListener('click', (e) => {
  e.stopPropagation();
  $('#file-input').click();
});
$('#dropzone').addEventListener('click', (e) => {
  if (e.target.closest('#pick-btn')) return;     // already handled above
  if (e.target.closest('#dropzone-confirm')) return; // confirm overlay click
  $('#file-input').click();
});
$('#dropzone').addEventListener('dragover', (e) => { e.preventDefault(); $('#dropzone').classList.add('dragover'); });
$('#dropzone').addEventListener('dragleave', () => $('#dropzone').classList.remove('dragover'));
$('#dropzone').addEventListener('drop', (e) => {
  e.preventDefault();
  $('#dropzone').classList.remove('dragover');
  if (e.dataTransfer.files.length) uploadPaper(e.dataTransfer.files[0]);
});
$('#file-input').addEventListener('change', (e) => {
  if (e.target.files.length) uploadPaper(e.target.files[0]);
  // Reset input value so the SAME filename can be re-uploaded later
  // (otherwise the change event won't fire because the value didn't change).
  e.target.value = '';
});

// Confirmation animation — flashes a green check on the dropzone briefly
// after a successful upload so the user knows the file went through
// without having to read the activity log.
function showUploadConfirmation(filename, sizeKB) {
  const overlay = $('#dropzone-confirm');
  const dz = $('#dropzone');
  if (!overlay || !dz) return;
  $('#dz-confirm-title').textContent = `Submitted — ${filename}`;
  $('#dz-confirm-sub').textContent = `${sizeKB.toFixed(1)} KB · pipeline starting…`;
  overlay.hidden = false;
  // Re-trigger the CSS animation by toggling a class on the next frame.
  dz.classList.remove('confirmed');
  void dz.offsetWidth; // force reflow so the animation restarts
  dz.classList.add('confirmed');
  // Hold the confirmation visible briefly, then fade out smoothly. The
  // .fading-out class drives a CSS opacity animation (~0.6s); after it
  // finishes, hide the overlay and clear all transient classes so the
  // dropzone returns to its idle state.
  setTimeout(() => {
    overlay.classList.add('fading-out');
    setTimeout(() => {
      overlay.hidden = true;
      overlay.classList.remove('fading-out');
      dz.classList.remove('confirmed');
    }, 600);
  }, 1700);
}

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
    log('A1', `Paper ${paper.paper_id} stored. Kicking off full pipeline…`, 'a1');
    $('#paper-title').textContent = paper.paper_id;
    $('#ingest-chip').innerHTML = '<span class="dot green pulse"></span>Ingested';
    showUploadConfirmation(file.name, file.size / 1024);
    // Kick off the full pipeline (A1→A2→A3→B1+B2→engine→D2→battery→D3) in
    // the background; show real-time progress via the stepper.
    await runFullPipeline(paper.paper_id);
  } catch (e) {
    log('SYS', `ERROR: ${e.message}`, 'sys');
    setStatus('Error', 'red');
  }
  refreshPapers();
}

async function runFullPipeline(paperId) {
  setStatus('Pipeline running…', 'amber');
  resetStepper();
  setStage('parse', 'done');
  try {
    const r = await fetch('/api/pipeline/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paper_id: paperId }),
    });
    const parsed = await safeJson(r);
    if (!r.ok || !parsed.ok) {
      log('SYS', `pipeline/start failed: ${parsed.body.error || r.status}`, 'sys');
      setStatus('Pipeline failed', 'red');
      return;
    }
    const jobId = parsed.body.job_id;
    log('SYS', `pipeline started, job=${jobId}`, 'sys');
    const finalPayload = await trackPipelineJob(jobId);
    const liveBundle = finalPayload?.result;
    if (finalPayload?.error) {
      log('SYS', `pipeline failed at ${finalPayload.stage}: ${finalPayload.error}`, 'sys');
      setStatus('Pipeline failed', 'red');
      // Render whatever stages did complete so the user sees the extracted
      // spec / critique / mapping even though e.g. the engine raised.
      if (liveBundle) {
        log('SYS', `Showing partial results from ${liveBundle.paper_id}.`, 'sys');
        applyBundle(liveBundle);
      }
      return;
    }
    setStatus('Pipeline complete', 'green');
    // Prefer the live-run bundle the worker stowed in PIPELINE_JOBS[job].result —
    // applying it directly avoids the static outputs/jt_*.json fallback that
    // /api/report/regenerate reads (which would render JT after a testqt run).
    if (liveBundle) {
      log('SYS', `Rendering live ${liveBundle.paper_id} pipeline output.`, 'sys');
      applyBundle(liveBundle);
      if (liveBundle.window_info?.substituted) {
        log('SYS', liveBundle.window_info.message, 'sys');
      }
    } else {
      // No live bundle (older pipeline path) — fall back to the static report
      await fetch('/api/report/regenerate', { method: 'POST' });
      await loadPhase5Report();
    }
  } catch (e) {
    log('SYS', `ERROR: ${e.message}`, 'sys');
    setStatus('Error', 'red');
  }
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
  setStage('a1', 'active');
  setStage('a2', 'active');
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
      setStage('a1', 'failed');
      setStage('a2', 'failed');
      return;
    }
    const body = parsed.body;
    verifiedSpec = body.verified_spec;
    log('A1', `Extracted spec. Verification → ${verifiedSpec.report.overall_confidence}.`, 'a1');
    log('A2', `${verifiedSpec.report.n_checks} quote checks; ${verifiedSpec.report.n_failed_high} high-severity failures.`, 'a2');
    // body.paper_claim is /api/extract's projection of spec.headline_claim onto
    // the legacy flat shape D2 frontend reads (monthly_return, tstat, window).
    // Null when A1 found no headline number to extract — D2 will skip.
    if (body.paper_claim) {
      log('A1', `Headline claim → ${fmtPct(body.paper_claim.monthly_return, 3)}/mo (t=${fmtNum(body.paper_claim.tstat, 2)}) over ${body.paper_claim.window}.`, 'a1');
    } else {
      log('A1', 'No headline claim extracted (paper has no single designated number) — D2 will skip.', 'a1');
    }
    // Manual override: when A1 misses the headline number (common for
    // papers reporting Sharpe ratios or non-standard metrics), the user
    // can supply it via the Overview "Paper headline" inputs. Override
    // wins over A1's extraction so the rest of the chain (D2 / D3 /
    // verdict strip / paper-vs-replication table) has a real comparison
    // target instead of a placeholder zero.
    const claimOverride = readPaperClaimOverride();
    // Defensive fallback: if /api/extract didn't return a flat paper_claim
    // but the verified spec has a headline_claim, project it ourselves so
    // every downstream stage (D2, factor_compare, verdict strip) gets the
    // same claim the Spec tab is already displaying.
    let projectedClaim = body.paper_claim;
    if (!projectedClaim && verifiedSpec && verifiedSpec.spec && verifiedSpec.spec.headline_claim) {
      const hc = verifiedSpec.spec.headline_claim;
      if (hc.monthly_return != null) {
        projectedClaim = {
          monthly_return: hc.monthly_return,
          tstat: hc.t_stat,
          window: hc.window_label,
          paper_location: hc.paper_location,
        };
      }
    }
    const effectiveClaim = claimOverride || projectedClaim || null;
    if (claimOverride) {
      log('A1', `Headline override applied: ${(claimOverride.monthly_return * 100).toFixed(3)}%/mo · t=${claimOverride.tstat ?? '—'} · window=${claimOverride.window}.`, 'a1');
    }
    applyBundle({
      paper_id: body.paper_id,
      paper_title: body.paper_title,
      verified_spec: verifiedSpec,
      critique: null,
      backtest: null,
      robustness: null,
      diagnosis: null,
      paper_claim: effectiveClaim,
    });
    setStage('a1', 'done');
    setStage('a2', 'done');
  } catch (e) {
    log('SYS', `ERROR: ${e.message}`, 'sys');
    setStatus('Error', 'red');
    setStage('a1', 'failed');
    setStage('a2', 'failed');
    return;
  }

  // Chain the rest of the pipeline. Each stage degrades gracefully — a 503
  // (missing parquet cache) or 500 (missing API key) logs a hint and the
  // pipeline continues so the user sees whatever DID complete.
  await runCritique(paperId);
  // Free, deterministic falsification check — runs even when the engine
  // can't (paper window pre-1994). Compares paper claim to the realized
  // KF factor over the paper's exact window.
  await runFactorCompare(verifiedSpec);
  const bt = await runBacktest(paperId, verifiedSpec);
  if (bt) {
    await runRobustness(paperId, verifiedSpec);
    await runDiagnosis(paperId, verifiedSpec, bt);
  }
  setStatus('Pipeline complete', 'green');
}

async function runFactorCompare(verifiedSpec) {
  const spec = verifiedSpec && verifiedSpec.spec;
  if (!spec) return;
  log('KF', 'Calling /api/factor_compare (Ken French realized factor vs claim)…', 'sys');
  try {
    const r = await fetch('/api/factor_compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec, headline_claim: spec.headline_claim || null }),
    });
    const parsed = await safeJson(r);
    if (!r.ok || !parsed.ok) {
      log('KF', `SKIPPED: ${parsed.body.error || `HTTP ${r.status}`}`, 'sys');
      return;
    }
    const fc = parsed.body.comparison;
    if (!state.bundle) return;
    state.bundle.factor_compare = fc;
    // Re-render the Backtest tab so the KF banner appears even before the
    // engine runs. (renderBacktest no-ops the bt-specific blocks if bt is null.)
    renderBacktest(state.bundle.backtest, state.bundle.paper_claim);
    const verdictWord = {
      supported: 'supported',
      directional: 'same direction, magnitude differs',
      falsified: 'falsified',
      out_of_range: 'window out of KF coverage',
      no_factor_match: 'no factor match',
    }[fc.verdict] || fc.verdict;
    log('KF', `${fc.factor_name} over ${fc.used_start ?? '—'}→${fc.used_end ?? '—'} (n=${fc.n_months}): ${verdictWord}.`, 'sys');
  } catch (e) {
    log('KF', `ERROR: ${e.message}`, 'sys');
  }
}

async function runCritique(paperId) {
  setStatus('Running A3 adversarial reviewer…', 'amber');
  log('A3', 'Calling /api/critique (Sonnet, forced three criticisms)…', 'a3');
  setStage('a3', 'active');
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
      setStage('a3', 'failed');
      return;
    }
    if (!state.bundle) { setStage('a3', 'failed'); return; }
    state.bundle.critique = parsed.body.critique;
    renderCritique(state.bundle.critique);
    log('A3', `${parsed.body.critique.criticisms.length} criticisms recorded.`, 'a3');
    setStage('a3', 'done');
  } catch (e) {
    log('A3', `ERROR: ${e.message}`, 'a3');
    setStage('a3', 'failed');
  }
}

async function runBacktest(paperId, verified) {
  setStatus('Running backtest engine…', 'amber');
  log('ENGINE', 'Calling /api/backtest (deterministic canonical engine)…', 'sys');
  setStage('engine', 'active');
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
      setStage('engine', 'failed');
      return null;
    }
    const bt = parsed.body.backtest;
    if (!state.bundle) { setStage('engine', 'failed'); return null; }
    state.bundle.backtest = bt;
    state.bundle.window_info = parsed.body.window_info || null;
    renderBacktest(bt, state.bundle.paper_claim);
    renderKPIs(bt);
    renderLineage(state.bundle);
    if (parsed.body.clip_note) log('SYS', parsed.body.clip_note, 'sys');
    log('ENGINE', `n=${bt.n_periods}, μ=${fmtPct(bt.mean_return, 3)}, t=${fmtNum(bt.alpha_tstat, 2)}.`, 'sys');
    setStage('engine', 'done');
    return bt;
  } catch (e) {
    log('ENGINE', `ERROR: ${e.message}`, 'sys');
    setStage('engine', 'failed');
    return null;
  }
}

async function runRobustness(paperId, verified) {
  setStatus('Running robustness battery (15–30 min on first run)…', 'amber');
  log('D3', 'Calling /api/robustness (6 families + D3 judgment)…', 'a3');
  // Battery + D3 are bundled into the same /api/robustness call; light up
  // both stepper pills so the user sees what's running.
  setStage('battery', 'active');
  setStage('d3', 'active');
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
      setStage('battery', 'failed');
      setStage('d3', 'failed');
      return;
    }
    if (!state.bundle) { setStage('battery', 'failed'); setStage('d3', 'failed'); return; }
    state.bundle.robustness = parsed.body;
    renderRobustness(state.bundle.robustness);
    renderLineage(state.bundle);
    // Refresh the top verdict strip with the LIVE D3 numbers — otherwise it
    // keeps showing whatever /api/report cached earlier (a postfix run with
    // a different spec), and the headline disagrees with the engine output
    // the user is actually looking at.
    refreshVerdictStripFromLive(parsed.body, state.bundle);
    if (parsed.body.clip_note) log('SYS', parsed.body.clip_note, 'sys');
    const sc = parsed.body.scorecard;
    setStage('battery', 'done');
    if (parsed.body.judgment) {
      log('D3', `${sc.n_surviving}/${sc.n_tests} surviving · signal=${parsed.body.judgment.signal_type}.`, 'a3');
      setStage('d3', 'done');
    } else {
      log('D3', `${sc.n_surviving}/${sc.n_tests} surviving · ${parsed.body.judgment_error || 'judgment unavailable'} (scorecard rendered).`, 'a3');
      // Battery succeeded; D3 (the LLM wrapper) didn't — flag the wrapper as
      // failed so the stepper reflects what actually happened.
      setStage('d3', 'failed');
    }
  } catch (e) {
    log('D3', `ERROR: ${e.message}`, 'a3');
    setStage('battery', 'failed');
    setStage('d3', 'failed');
  }
}

async function runDiagnosis(paperId, verified, bt) {
  // D2 needs a real paper_claim to be useful — without one, the gap is
  // zero and D2 early-exits. Skip unless one was loaded (e.g. demo bundle).
  if (!state.bundle || !state.bundle.paper_claim) {
    log('D2', 'SKIPPED: no paper_claim on bundle (only useful with a claim to compare against).', 'a3');
    setStage('d2', 'done');
    return;
  }
  const claimMonthly = state.bundle.paper_claim.monthly_return;
  const claimTstat = state.bundle.paper_claim.tstat ?? null;
  // Self-consistency guard: monthly_return == 0 paired with a non-zero
  // t-stat is mathematically impossible (t = mean·√N/σ). Treat as
  // "no usable claim" rather than firing D2 against a phantom zero.
  if (claimMonthly === 0 && claimTstat !== null && claimTstat !== 0) {
    log('D2', 'SKIPPED: paper_claim has monthly_return=0 with non-zero t-stat (impossible — A1 hallucinated a placeholder zero).', 'a3');
    setStage('d2', 'done');
    return;
  }
  setStatus('Running D2 divergence diagnostician…', 'amber');
  log('D2', 'Calling /api/diagnose (LLM-proposed mutations × engine reruns)…', 'a3');
  setStage('d2', 'active');
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
      setStage('d2', 'failed');
      return;
    }
    if (!state.bundle) { setStage('d2', 'failed'); return; }
    state.bundle.diagnosis = parsed.body;
    renderDiagnosis(state.bundle.diagnosis, state.bundle.paper_claim);
    renderLineage(state.bundle);
    if (parsed.body.clip_note) log('SYS', parsed.body.clip_note, 'sys');
    if (parsed.body.diagnosis) {
      log('D2', `${parsed.body.n_experiments} experiments · primary=${parsed.body.diagnosis.primary_cause}.`, 'a3');
      setStage('d2', 'done');
    } else {
      // /api/diagnose returns 200 with diagnosis=null when D2 LLM hits 529 etc.
      log('D2', `${parsed.body.diagnosis_error || 'diagnosis unavailable'}.`, 'a3');
      setStage('d2', 'failed');
    }
  } catch (e) {
    log('D2', `ERROR: ${e.message}`, 'a3');
    setStage('d2', 'failed');
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
              // The Run config card is hidden, so re-running on a
              // previously uploaded paper goes through here: select the
              // paper, sync the (hidden) dropdown so runPipelineFromDials
              // sees it, and kick the pipeline directly.
              state.selectedPaperId = p.paper_id;
              const sel = $('#runconfig-paper-select');
              if (sel) sel.value = p.paper_id;
              refreshRunButton();
              refreshPapers();
              log('SYS', `Re-running pipeline on ${p.paper_id}…`, 'sys');
              runPipelineFromDials();
            },
          }, isSelected ? 'Re-run' : 'Run'),
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
    // Re-seed the dial form whenever the paper changes (so the freshly
    // extracted A1 values become the new "defaults" — applyDials becomes a
    // no-op and no field shows up as "(overridden)"). For repeated calls
    // with the same paper mid-pipeline-run, keep the user's in-flight dial
    // edits intact instead of clobbering them.
    const paperId = state.originalSpec.paper_id;
    if (paperId !== state.lastSeededPaperId) {
      seedDialFormFromSpec(state.originalSpec);
      state.lastSeededPaperId = paperId;
    } else if (!state.running) {
      seedDialFormFromSpec(state.originalSpec);
    }
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
  renderLineage(bundle);
  renderKPIs(bundle.backtest);
  // Drive the headline verdict strip from the live bundle (not the static
  // outputs/jt_*.json report). Falls back gracefully when only some stages
  // have completed (paper_title/spec available but no backtest yet).
  renderVerdictStrip(buildLiveReport(bundle));
  // A bundle that arrives in one shot (demo / cached postfix run / direct
  // /api/report fetch) skips the per-stage setStage() chain, so the stepper
  // buttons stay disabled and the user can't click into Replication / Stress
  // Tests / etc. Mark every stage that has data on the bundle as done so the
  // stepper acts as nav from the moment the bundle paints. The Backtest tab
  // (and its equity chart) is then reachable via the "Replication" step.
  const bundleStageDone = {
    parse: true,
    a1: !!bundle.verified_spec,
    a2: !!bundle.verified_spec,
    a3: !!bundle.critique,
    b: !!bundle.verified_spec,
    engine: !!bundle.backtest,
    d2: !!bundle.diagnosis,
    battery: !!bundle.robustness,
    d3: !!(bundle.robustness && bundle.robustness.judgment),
  };
  Object.entries(bundleStageDone).forEach(([stage, done]) => { if (done) setStage(stage, 'done'); });
  setStatus('Ready — bundle loaded', 'green');
}

// Build the {headline, ...} report shape that renderVerdictStrip consumes,
// from a live pipeline bundle. Mirrors the report-synthesizer's headline
// derivation so the verdict strip shows live numbers (not the cached JT
// values from /api/report).
function buildLiveReport(bundle) {
  const spec = bundle?.verified_spec?.spec;
  const bt = bundle?.backtest;
  const robustness = bundle?.robustness;
  const judgment = robustness?.judgment;
  const scorecard = robustness?.scorecard;
  const wi = bundle?.window_info;
  // Defensive fallback: if the legacy flat paper_claim wasn't populated by
  // the server (or got dropped somewhere in the pipeline), reconstruct it
  // from spec.headline_claim — which is always present when A1 extracted
  // a headline number. The Spec tab reads headline_claim directly, so any
  // paper that shows a Headline Claim section there should also light up
  // the verdict strip.
  let claim = bundle?.paper_claim;
  if (!claim && spec?.headline_claim) {
    const hc = spec.headline_claim;
    if (hc.monthly_return != null) {
      claim = {
        monthly_return: hc.monthly_return,
        tstat: hc.t_stat,
        window: hc.window_label,
        paper_location: hc.paper_location,
      };
    }
  }

  // Headline numbers — prefer D3's implementable_alpha when it exists,
  // otherwise fall back to the baseline backtest's mean_return.
  let implAlpha = null;
  let tstat = null;
  let confidence = 'unknown';
  let signalType = '—';
  let summaryClause = '';
  let gapAttribution = '';
  if (judgment) {
    implAlpha = judgment.implementable_alpha;
    confidence = judgment.confidence || 'unknown';
    signalType = judgment.signal_type || '—';
    gapAttribution = judgment.gap_attribution || '';
    summaryClause = (judgment.summary || '').split(/\.\s/)[0] || '';
    tstat = basisMatchedTstat(scorecard, implAlpha);
  } else if (bt) {
    implAlpha = bt.mean_return;
    tstat = bt.alpha_tstat;
    confidence = 'medium';
    signalType = bt.alpha_tstat != null && Math.abs(bt.alpha_tstat) >= 1.5 ? 'real' : 'noise';
  }

  const proxyMode = detectProxyMode(bt);
  const noTarget = detectNoTarget(claim);
  const tradeableLabel = tradeableLabel_(implAlpha, confidence, tstat, { proxyMode, noTarget });
  const paperWindow = (wi && wi.paper_start)
    ? `${wi.paper_start} to ${wi.paper_end}`
    : (spec ? `${spec.start_date} to ${spec.end_date}` : '—');
  const engineWindow = (wi && wi.engine_start)
    ? `${wi.engine_start} to ${wi.engine_end}`
    : (bt ? `${bt.start_date} to ${bt.end_date}` : '—');

  return {
    headline: {
      paper_id: bundle.paper_id || (spec && spec.paper_id) || '—',
      paper_title: bundle.paper_title || (spec && spec.paper_title) || '—',
      paper_window: paperWindow,
      engine_window: engineWindow,
      claim: claim
        ? { value: claim.monthly_return, tstat: claim.tstat, paper_location: claim.paper_location || '' }
        : { value: null, tstat: null, paper_location: 'no headline claim extracted' },
      verdict: {
        implementable_alpha: implAlpha,
        tstat_estimate: tstat,
        tradeable_label: tradeableLabel,
        signal_type: signalType,
        gap_attribution: gapAttribution,
        confidence,
        summary_first_clause: summaryClause,
      },
    },
  };
}

// Mirrors src/agents/synthesis/report_synthesizer._tradeable_label,
// extended with PROXY_ONLY / NO_TARGET structural gates so that running
// a 12-month-momentum proxy isn't mislabeled as the paper "failing".
function tradeableLabel_(implAlpha, confidence, tstat, opts = {}) {
  if (opts.proxyMode) return 'PROXY ONLY';
  if (opts.noTarget) return 'NO PAPER TARGET';
  if (implAlpha === null || implAlpha === undefined) return 'PENDING';
  if (implAlpha < 0) return 'UNDER WATER';
  if (tstat !== null && tstat !== undefined && Math.abs(tstat) < 1.5) return 'NOT TRADEABLE AT SCALE';
  if (implAlpha < 0.0050 || confidence === 'low') return 'BORDERLINE';
  return 'TRADEABLE';
}

// Mirrors src/agents/synthesis/report_synthesizer._basis_matched_tstat
function basisMatchedTstat(scorecard, implAlpha) {
  if (!scorecard || implAlpha === null || implAlpha === undefined) return null;
  // Live RobustnessScorecard puts rows under `.tests` with field
  // `headline_tstat`; the synthesized E1 report uses `.rows` /
  // `.scenarios` with `tstat`. Accept either shape.
  const rows = scorecard.tests || scorecard.rows || scorecard.scenarios || [];
  let bestT = null;
  let bestDiff = 0.0025; // 25 bps tolerance
  for (const r of rows) {
    const m = r.headline_metric ?? r.headline ?? r.mean_return;
    const t = r.headline_tstat ?? r.tstat ?? r.alpha_tstat;
    if (m === null || m === undefined || t === null || t === undefined) continue;
    const diff = Math.abs(m - implAlpha);
    if (diff < bestDiff) { bestDiff = diff; bestT = t; }
  }
  return bestT;
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
  const overridePathSet = new Set(computeOverrides().map((o) => o.path));

  // Override list panel (above the prose, hidden by default).
  const overrideList = el('div', { class: 'override-list hidden', id: 'override-list' });
  const overrides = computeOverrides();
  if (overrides.length) {
    overrides.forEach((o) => {
      overrideList.appendChild(el('div', {}, `${o.path}: ${formatDialValue(o.before)} → ${formatDialValue(o.after)}`));
    });
  }
  root.appendChild(overrideList);

  // 1. Briefing-memo prose at the top — one paragraph composed from the spec.
  root.appendChild(buildSpecMemo(spec));

  // 2. Paper Reported Result — kept as a card up top because it's its own thing.
  if (spec.headline_claim) {
    root.appendChild(buildClaimMemo(spec.headline_claim));
  }

  // 3. Five extraction lanes — Header / Universe / Signal / Portfolio / Rebalance.
  const lanes = el('div', { class: 'spec-lanes' });
  lanes.appendChild(buildSpecLane({
    label: 'Paper',
    fields: [
      ['paper_id', spec.paper_id, false],
      ['title', spec.paper_title, false],
      ['window', `${fmtDate(spec.start_date)} → ${fmtDate(spec.end_date)}`,
        overridePathSet.has('start_date') || overridePathSet.has('end_date')],
      ['currency', spec.base_currency, false],
    ],
    note: spec.notes || null,
  }));
  lanes.appendChild(buildSpecLane({
    label: 'Universe',
    fields: [
      ['name', spec.universe.name, false],
      ['region', spec.universe.region, false],
      ['asset class', spec.universe.asset_class, false],
      ['min price', spec.universe.min_price == null ? '—' : `$${spec.universe.min_price}`, overridePathSet.has('universe.min_price')],
      ['exchanges', (spec.universe.exchanges || []).join(', ') || '—', overridePathSet.has('universe.exchanges')],
    ],
    quote: quotes.universe,
  }));
  lanes.appendChild(buildSpecLane({
    label: 'Signal',
    fields: [
      ['kind', spec.signal.kind, false],
      ['formula', spec.signal.formula, false],
      ['lookback', spec.signal.lookback_months == null ? '—' : `${spec.signal.lookback_months}m`, overridePathSet.has('signal.lookback_months')],
      ['skip', `${spec.signal.skip_months}m`, overridePathSet.has('signal.skip_months')],
      ['direction', spec.signal.direction, false],
      ['frequency', spec.signal.frequency, false],
    ],
    quote: quotes.signal,
  }));
  lanes.appendChild(buildSpecLane({
    label: 'Portfolio',
    fields: [
      ['construction', spec.portfolio.construction, false],
      ['n buckets', spec.portfolio.n_buckets, overridePathSet.has('portfolio.n_buckets')],
      ['long bucket', spec.portfolio.long_bucket, overridePathSet.has('portfolio.long_bucket')],
      ['short bucket', spec.portfolio.short_bucket ?? '—', overridePathSet.has('portfolio.short_bucket')],
      ['weighting', spec.portfolio.weighting, overridePathSet.has('portfolio.weighting')],
      ['long-short', spec.portfolio.long_short ? 'yes' : 'no', false],
      ['gross exposure', spec.portfolio.gross_exposure, false],
      ['NYSE breakpoints', spec.portfolio.use_nyse_breakpoints ? 'yes' : 'no', overridePathSet.has('portfolio.use_nyse_breakpoints')],
    ],
    quote: quotes.portfolio,
  }));
  lanes.appendChild(buildSpecLane({
    label: 'Rebalance',
    fields: [
      ['frequency', spec.rebalance.frequency, false],
      ['execution lag', `${spec.rebalance.execution_lag_days}d`, overridePathSet.has('rebalance.execution_lag_days')],
      ['holding period', spec.rebalance.holding_period_months == null ? '—' : `${spec.rebalance.holding_period_months}m`, overridePathSet.has('rebalance.holding_period_months')],
      ['signal convention', spec.rebalance.signal_date_convention, false],
      ['execution convention', spec.rebalance.execution_date_convention, false],
    ],
    quote: quotes.rebalance,
  }));
  root.appendChild(lanes);

  // 4. Ambiguities — annotation cards (one per ambiguity), reads as the agent
  // showing its work. Always taken from original A1 output.
  const ambSource = baseSpec;
  if (ambSource.ambiguities && ambSource.ambiguities.length) {
    root.appendChild(buildAmbiguitiesPanel(ambSource.ambiguities));
  }
}

// ---------- Methodology tab — briefing memo helpers ----------

// Compose a one-paragraph English summary of the spec. Reads like a research-
// desk note: what universe, what signal, how portfolios are formed, and what
// the cadence is. Used at the top of the Methodology tab so a trader can
// grasp the strategy in one read before drilling into fields.
function buildSpecMemo(spec) {
  const u = spec.universe;
  const s = spec.signal;
  const p = spec.portfolio;
  const r = spec.rebalance;
  const universe = `${u.region || 'global'} ${u.asset_class || 'equity'}${u.name ? ` (${u.name})` : ''}${u.min_price != null ? ` with a $${u.min_price} price floor` : ''}${(u.exchanges || []).length ? ` on ${u.exchanges.join('/')}` : ''}`;
  const direction = s.direction === 'long_high' ? 'top-ranked' : s.direction === 'long_low' ? 'bottom-ranked' : 'ranked';
  const signalDesc = s.kind === 'past_return'
    ? `trailing ${s.lookback_months}-month return${s.skip_months ? ` (skip ${s.skip_months}m)` : ''}`
    : s.kind === 'variance_ratio'
      ? `variance ratio over a ${s.lookback_months || '—'}-month window`
      : s.formula || s.kind;
  const portfolioDesc = p.long_short
    ? `goes long the ${direction} ${p.construction || 'bucket'} and short the opposite, ${p.weighting}-weighted${p.long_bucket && p.n_buckets ? ` (long ${p.long_bucket}/${p.n_buckets}, short ${p.short_bucket ?? '—'}/${p.n_buckets})` : ''}`
    : `holds the ${direction} ${p.construction || 'bucket'}, ${p.weighting}-weighted`;
  const cadenceDesc = `Rebalanced ${r.frequency}${r.holding_period_months ? ` with ${r.holding_period_months}-month holding period` : ''}${r.execution_lag_days ? `, ${r.execution_lag_days}-day execution lag` : ''}`;
  const window = `${fmtDate(spec.start_date)} → ${fmtDate(spec.end_date)}`;

  const wrap = el('div', { class: 'spec-memo' });
  wrap.appendChild(el('div', { class: 'spec-memo-eyebrow' }, 'Agent extraction · human review'));
  wrap.appendChild(el('h2', { class: 'spec-memo-title' }, spec.paper_title || spec.paper_id || '—'));
  wrap.appendChild(el('p', { class: 'spec-memo-prose' },
    `Strategy on ${universe}. The signal is the ${signalDesc}; the portfolio ${portfolioDesc}. ${cadenceDesc}. Paper window ${window}.`
  ));
  return wrap;
}

// Paper-reported result card — the headline_claim block. Different from the
// extraction lanes because this is what the paper says, not what the agent
// extracted from the methodology.
function buildClaimMemo(hc) {
  const wrap = el('div', { class: 'spec-claim-memo' });
  wrap.appendChild(el('div', { class: 'spec-memo-eyebrow' }, 'Paper Reported Result'));
  const row = el('div', { class: 'spec-claim-row' });
  row.appendChild(el('div', { class: 'spec-claim-cell' }, [
    el('div', { class: 'spec-claim-label' }, 'Monthly long-short'),
    el('div', { class: 'spec-claim-value' }, fmtPct(hc.monthly_return, 3) + '/mo'),
  ]));
  if (hc.t_stat != null) {
    row.appendChild(el('div', { class: 'spec-claim-cell' }, [
      el('div', { class: 'spec-claim-label' }, 't-stat'),
      el('div', { class: 'spec-claim-value' }, fmtNum(hc.t_stat, 2)),
    ]));
  }
  row.appendChild(el('div', { class: 'spec-claim-cell wide' }, [
    el('div', { class: 'spec-claim-label' }, 'Window'),
    el('div', { class: 'spec-claim-value mono' }, hc.window_label || '—'),
  ]));
  wrap.appendChild(row);
  if (hc.paper_location) {
    wrap.appendChild(el('div', { class: 'spec-claim-loc' }, hc.paper_location));
  }
  if (hc.supporting_quote) {
    wrap.appendChild(buildMarginQuote(hc.supporting_quote));
  }
  return wrap;
}

// One extraction lane. Renders as a left-rail accent with a small-caps
// section label, inline chip-style fields, and a margin-annotation quote
// underneath. Designed to read like a trader's review rather than a form.
function buildSpecLane({ label, fields, quote, note }) {
  const lane = el('div', { class: 'spec-lane' });
  lane.appendChild(el('div', { class: 'spec-lane-label' }, label));
  const body = el('div', { class: 'spec-lane-body' });
  const chips = el('div', { class: 'spec-chip-row' });
  fields.forEach(([k, v, overridden]) => {
    if (v == null || v === '') return;
    const chip = el('span', { class: 'spec-chip' + (overridden ? ' overridden' : '') });
    chip.appendChild(el('span', { class: 'spec-chip-key' }, k));
    chip.appendChild(el('span', { class: 'spec-chip-val' }, String(v)));
    chips.appendChild(chip);
  });
  body.appendChild(chips);
  if (note) {
    body.appendChild(el('div', { class: 'spec-lane-note' }, note));
  }
  if (quote) {
    body.appendChild(buildMarginQuote(quote));
  }
  lane.appendChild(body);
  return lane;
}

// Margin-annotation quote — a quote from the paper rendered as a left-bordered
// italic block, like a handwritten margin note. Severity color from the
// quote's verification state when available.
function buildMarginQuote(q) {
  const sev = q.verified ? 'ok' : (q.match_confidence != null && q.match_confidence < 0.9) ? 'warn' : 'info';
  const wrap = el('div', { class: `spec-margin-quote sev-${sev}` });
  wrap.appendChild(el('div', { class: 'spec-margin-meta' },
    `page ${q.page} · confidence ${fmtNum(q.match_confidence, 2)} · ${q.verified ? 'verified' : 'unverified'}`
  ));
  wrap.appendChild(el('div', { class: 'spec-margin-text' }, '“' + q.text + '”'));
  return wrap;
}

// Ambiguities panel — one annotation card per ambiguity. Reads as prose:
// "agent chose X because the paper doesn't specify; alternatives are Y, Z."
function buildAmbiguitiesPanel(ambiguities) {
  const wrap = el('div', { class: 'spec-ambiguities' });
  wrap.appendChild(el('div', { class: 'spec-memo-eyebrow' },
    `Ambiguities · ${ambiguities.length} field${ambiguities.length === 1 ? '' : 's'} the agent had to interpret`
  ));
  wrap.appendChild(el('p', { class: 'spec-ambiguities-intro' },
    'The paper did not explicitly specify these parameters. The agent picked a default and surfaced the alternatives — review before sizing up.'
  ));
  ambiguities.forEach((f) => {
    const sev = f.sensitivity_priority === 'high' ? 'fail' : f.sensitivity_priority === 'medium' ? 'warn' : 'info';
    const card = el('div', { class: `spec-ambig-card sev-${sev}` });
    card.appendChild(el('div', { class: 'spec-ambig-head' }, [
      el('span', { class: `spec-ambig-dot sev-${sev}` }),
      el('span', { class: 'spec-ambig-param' }, f.parameter),
      el('span', { class: 'spec-ambig-priority' }, capitalize(f.sensitivity_priority || 'medium') + ' priority'),
    ]));
    card.appendChild(el('div', { class: 'spec-ambig-body' }, [
      'Agent chose ',
      el('strong', {}, formatDialValue(f.default_chosen)),
      (f.alternatives && f.alternatives.length)
        ? `; alternatives: ${f.alternatives.join(', ')}.`
        : '.',
    ]));
    if (f.reason) {
      card.appendChild(el('div', { class: 'spec-ambig-reason' }, f.reason));
    }
    wrap.appendChild(card);
  });
  return wrap;
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
    dl.appendChild(el('dt', { class: overridden ? 'overridden' : '' }, term(k)));
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
  // KF factor-comparison banner removed from the UI — it was a
  // falsification test against the published Ken French MOM factor that
  // confused users by showing a number wildly different from the engine's
  // Implementable Alpha (different universe + construction + window). The
  // /api/factor_compare endpoint and runFactorCompare fetcher are kept so
  // the bundle still carries the data for any future surface that wants
  // it; this Backtest-tab banner is the only place that rendered it.
  if (!bt) {
    root.appendChild(emptyState('trending_up', 'No backtest result. Use the demo bundle or run /api/backtest.'));
    return;
  }

  // Structural-mode banner. Loudest possible warning when the engine ran
  // a proxy strategy or no comparison target exists — both invalidate
  // any "this strategy performs at X" claim. Goes above everything else.
  const proxyMode = detectProxyMode(bt);
  const noTarget = detectNoTarget(paperClaim);
  if (proxyMode) {
    const banner = el('div', { class: 'flag-banner sev-fail window-banner' });
    banner.appendChild(el('div', { class: 'head' }, 'PROXY ONLY — this is NOT a replication of the paper\'s strategy'));
    banner.appendChild(el('div', { class: 'banner-headline' },
      'A1 extracted signal.kind=\'custom\' (the paper\'s signal is e.g. variance ratio, learned model, or fundamental ratio that is not yet implemented in src/engine/signals.py). The engine substituted a 12-month past-return momentum proxy and ran the entire backtest + robustness battery on the proxy.'));
    banner.appendChild(el('div', { class: 'banner-foot' },
      'Every number on this tab — mean_return, sharpe, alpha_tstat, max_drawdown, decay_by_age, the equity curve, the robustness scorecard, the implementable-alpha verdict — is a result for the proxy strategy. The paper itself may perform very differently. To get a real replication, implement the paper\'s signal in src/engine/signals.py and re-run.'));
    root.appendChild(banner);
  }
  if (!proxyMode && noTarget) {
    const statOnly = detectStatisticalOnlyPaper(state.bundle);
    const banner = el('div', { class: 'flag-banner sev-warn' });
    if (statOnly) {
      banner.appendChild(el('div', { class: 'head' }, 'Statistical-test paper — no tradeable headline claim'));
      banner.appendChild(el('div', {},
        'This paper reports variance-ratio statistics (e.g. VR(k) testing for mean reversion vs. random walk), not a tradeable monthly long-short return. The replication runs the implicit contrarian/momentum strategy implied by signal.direction; the measured return is vs. zero (the random-walk null), NOT vs. a paper-quoted number. Examples: Lo-MacKinlay 1988, Poterba-Summers 1988. The "paper monthly L/S return" override input doesn\'t apply — the paper doesn\'t make that claim.'));
    } else {
      banner.appendChild(el('div', { class: 'head' }, 'No comparison target — verdict is vs zero, not vs the paper'));
      banner.appendChild(el('div', {},
        'A1 did not extract a usable headline claim. The implementable-alpha verdict, gap-attribution narrative, and D2 diagnosis cannot be computed because there\'s nothing to compare against. Common cause: paper reports a Sharpe ratio, regression alpha, or non-standard headline metric instead of a clean monthly long-short return.'));
    }
    root.appendChild(banner);
  }

  // Window substitution banner — loud warning when the engine ran on a
  // different window than the paper claimed (either fully-substituted OOS
  // or partially-clipped to the data panel).
  const wi = state.bundle && state.bundle.window_info;
  if (wi && (wi.substituted || wi.clipped)) {
    // Both substitution and clipping render as 'warn' (yellow). An OOS
    // run isn't a failure — it's still a valid test of the spec, just
    // on data the paper didn't see. The banner copy already explains
    // the caveat; severity 'fail' (red) was overstating the badness.
    const severity = 'warn';
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
    banner.appendChild(el('div', { class: 'head' }, `Data Integrity Notes (${bt.data_quality_flags.length})`));
    bt.data_quality_flags.forEach((f) => banner.appendChild(el('div', {}, `• ${f}`)));
    root.appendChild(banner);
  }

  // Comparison. Skip the table entirely when the claim is the impossible
  // (monthly_return=0, tstat!=0) shape — A1 hallucinated a zero placeholder
  // and the gap row would read "−0.476% (0%)" which is misleading.
  const claimUsable = paperClaim && !(paperClaim.monthly_return === 0 && paperClaim.tstat != null && paperClaim.tstat !== 0);
  if (paperClaim && !claimUsable) {
    const note = el('div', { class: 'flag-banner' });
    note.appendChild(el('div', { class: 'head' }, 'Paper claim not extracted'));
    note.appendChild(el('div', {}, '• A1 emitted monthly_return=0 paired with a non-zero t-stat (mathematically impossible). The paper reports a real headline number A1 failed to capture; the comparison table is hidden until headline_claim is supplied manually.'));
    root.appendChild(note);
  }
  if (claimUsable) {
    const block = el('div', { class: 'spec-section' });
    block.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Paper Reported vs. Replication')]));
    const gap = bt.mean_return - paperClaim.monthly_return;
    const gapPct = paperClaim.monthly_return !== 0 ? (bt.mean_return / paperClaim.monthly_return - 1) * 100 : 0;
    const t = el('table', { class: 'data data-paper-vs' });
    t.appendChild(el('thead', {}, [
      el('tr', {}, [
        el('th', {}, 'Metric'),
        el('th', {}, 'Paper Reported'),
        el('th', {}, 'Replication'),
        el('th', {}, 'Delta'),
      ]),
    ]));
    const tb = el('tbody', {}, [
      el('tr', {}, [
        el('td', {}, 'Monthly Excess Return'),
        el('td', { class: 'num' }, fmtPct(paperClaim.monthly_return, 3)),
        el('td', { class: 'num' }, fmtPct(bt.mean_return, 3)),
        el('td', { class: 'num ' + (gap >= 0 ? 'pos' : 'neg') }, (gap >= 0 ? '+' : '') + fmtPct(gap, 3) + ` (${gapPct.toFixed(0)}%)`),
      ]),
      el('tr', {}, [
        el('td', {}, 't-statistic (Newey-West)'),
        el('td', { class: 'num' }, fmtNum(paperClaim.tstat, 2)),
        el('td', { class: 'num' }, fmtNum(bt.alpha_tstat, 2)),
        el('td', { class: 'num' }, fmtNum(bt.alpha_tstat - paperClaim.tstat, 2)),
      ]),
      el('tr', {}, [
        el('td', {}, 'Sample Window'),
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
  m.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Replication Metrics')]));
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
    dl.appendChild(el('dt', {}, term(k)));
    dl.appendChild(el('dd', {}, String(v)));
  });
  m.appendChild(dl);
  root.appendChild(m);

  // Post-formation decay — gross per-tranche return grouped by months since
  // formation. The shape (monotone decay vs mid-life peak vs reversal)
  // answers the trader's "how long is the signal alive?" question directly.
  if (bt.decay_by_age && bt.decay_by_age.length) {
    const decay = el('div', { class: 'spec-section' });
    decay.appendChild(el('div', { class: 'spec-section-head' }, [
      el('h3', {}, 'Signal Decay by Formation Age'),
      el('span', { class: 'pill info' }, 'Per-tranche · gross of costs'),
    ]));
    decay.appendChild(buildDecayByAgeChart(bt.decay_by_age));
    // Narrative explainer — traders want the takeaway, not just the bars.
    decay.appendChild(buildDecayNarrative(bt.decay_by_age));
    root.appendChild(decay);
  }

  // Equity curve — interactive (brush-to-zoom + hover tooltip + drawdown panel)
  const chart = el('div', { class: 'spec-section' });
  chart.appendChild(el('div', { class: 'spec-section-head' }, [
    el('h3', {}, 'Cumulative Long–Short Return'),
    el('div', { class: 'chart-toolbar' }, [
      el('span', { class: 'hint' }, 'Drag to zoom · double-click to reset'),
      el('button', { class: 'btn-mini', id: 'eq-reset' }, 'Reset Zoom'),
    ]),
  ]));
  const wrap = el('div', { class: 'chart-wrap chart-wrap-tall' });
  const eq = new EquityChart(bt.returns, { showRegimes: shouldShowRegimes(state.bundle) });
  wrap.appendChild(eq.root);
  chart.appendChild(wrap);
  root.appendChild(chart);
  $('#eq-reset').addEventListener('click', () => eq.resetZoom());

  // Recent returns table (last 24)
  if (bt.returns && bt.returns.length) {
    const tbl = el('div', { class: 'spec-section' });
    tbl.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, `Return Observations · last 24 of ${bt.returns.length}`)]));
    const t = el('table', { class: 'data' });
    t.appendChild(el('thead', {}, [
      el('tr', {}, [
        el('th', {}, 'Period End'),
        el('th', {}, 'Excess Return'),
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

// ---------- Post-formation decay chart ----------
//
// Bars show mean per-tranche return at each age (months since formation),
// with ±1 standard-error whiskers. Positive bars up (green), negative down
// (red). A horizontal zero-line anchors the eye. The point of the chart is
// the SHAPE — traders read it as "signal peaks at month X, fades by month Y".

function buildDecayByAgeChart(points) {
  const n = points.length;
  // Density-aware sizing so 96 bars don't get crammed into 640 px. The
  // viewBox grows but is capped — `chart-wrap-scroll` lets the user scroll
  // horizontally if it overflows the container. Bars never go below ~5 px.
  const w = Math.max(640, Math.min(n * 10 + 80, 1400));
  const h = 200;
  const padL = 44, padR = 14, padT = 14, padB = 30;
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;
  const wrap = el('div', { class: 'chart-wrap chart-wrap-decay', style: 'position:relative;' });

  const means = points.map((p) => p.mean_ret);
  const ses = points.map((p) => p.std_error);
  const yMaxRaw = Math.max(0, ...means.map((m, i) => m + ses[i]));
  const yMinRaw = Math.min(0, ...means.map((m, i) => m - ses[i]));
  const yPad = Math.max((yMaxRaw - yMinRaw) * 0.1, 1e-4);
  const yMax = yMaxRaw + yPad;
  const yMin = yMinRaw - yPad;
  const yToPx = (y) => padT + innerH * (1 - (y - yMin) / (yMax - yMin));

  const slot = innerW / n;
  const barW = Math.min(slot * 0.65, 48);
  const xToPx = (i) => padL + slot * (i + 0.5) - barW / 2;
  const cxToPx = (i) => padL + slot * (i + 0.5);

  // Density gates — collapse decoration that becomes noise at high N.
  const showValueLabels = n <= 12;
  const showWhiskers = n <= 60;
  // Sparse x-axis: aim for ~10 visible labels across the chart.
  const xLabelStride = Math.max(1, Math.ceil(n / 12));

  const s = svgEl('svg', {
    xmlns: SVG_NS,
    viewBox: `0 0 ${w} ${h}`,
    width: '100%',
    preserveAspectRatio: 'none',
    class: 'decay-chart',
    style: 'cursor:crosshair;',
  });

  // Zero line
  const y0 = yToPx(0);
  s.appendChild(svgEl('line', {
    x1: padL, x2: padL + innerW, y1: y0, y2: y0,
    stroke: '#3a4354', 'stroke-width': '1', 'stroke-dasharray': '3,3',
  }));

  // Y-axis ticks (min, zero, max)
  [yMin + yPad, 0, yMax - yPad].forEach((val) => {
    const yy = yToPx(val);
    s.appendChild(svgEl('line', {
      x1: padL - 4, x2: padL, y1: yy, y2: yy, stroke: '#2a3240', 'stroke-width': '1',
    }));
    const lbl = svgEl('text', {
      x: padL - 6, y: yy + 3,
      fill: '#8b90a0', 'font-family': 'Inter', 'font-size': '10',
      'text-anchor': 'end',
    });
    lbl.textContent = (val * 100).toFixed(2) + '%';
    s.appendChild(lbl);
  });

  // Bars + (conditional) whiskers + (sparse) x labels + (conditional) value labels.
  points.forEach((p, i) => {
    const cx = cxToPx(i);
    const x = xToPx(i);
    const yVal = yToPx(p.mean_ret);
    const barTop = p.mean_ret >= 0 ? yVal : y0;
    const barHeight = Math.abs(yVal - y0);
    const color = p.mean_ret >= 0 ? 'rgba(111, 159, 107, 0.7)' : 'rgba(168, 57, 48, 0.7)';
    const stroke = p.mean_ret >= 0 ? '#6f9f6b' : '#a83930';

    const bar = svgEl('rect', {
      x, y: barTop, width: barW, height: Math.max(barHeight, 1),
      fill: color, stroke, 'stroke-width': '1',
    });
    s.appendChild(bar);

    if (showWhiskers && p.std_error > 0) {
      const yHi = yToPx(p.mean_ret + p.std_error);
      const yLo = yToPx(p.mean_ret - p.std_error);
      const opacity = n > 30 ? '0.45' : '1';
      s.appendChild(svgEl('line', {
        x1: cx, x2: cx, y1: yHi, y2: yLo,
        stroke: '#cbd1dc', 'stroke-width': '1', 'stroke-opacity': opacity,
      }));
      const cap = Math.min(4, barW * 0.4);
      [yHi, yLo].forEach((yy) => {
        s.appendChild(svgEl('line', {
          x1: cx - cap, x2: cx + cap, y1: yy, y2: yy,
          stroke: '#cbd1dc', 'stroke-width': '1', 'stroke-opacity': opacity,
        }));
      });
    }

    // Sparse x labels — first, last, and every Nth in between.
    const isEdge = i === 0 || i === n - 1;
    if (isEdge || (i % xLabelStride === 0)) {
      const xl = svgEl('text', {
        x: cx, y: h - padB + 14,
        fill: '#8b90a0', 'font-family': 'Inter', 'font-size': '10',
        'text-anchor': 'middle',
      });
      xl.textContent = String(p.age_months);
      s.appendChild(xl);
    }

    if (showValueLabels) {
      const vl = svgEl('text', {
        x: cx, y: (p.mean_ret >= 0 ? yVal - 4 : yVal + 12),
        fill: '#cbd1dc', 'font-family': 'Inter', 'font-size': '9.5',
        'text-anchor': 'middle',
      });
      vl.textContent = (p.mean_ret * 100).toFixed(2) + '%';
      s.appendChild(vl);
    }
  });

  // X-axis caption
  const xcap = svgEl('text', {
    x: padL + innerW / 2, y: h - 4,
    fill: '#9098a3', 'font-family': 'Inter', 'font-size': '10',
    'text-anchor': 'middle',
  });
  xcap.textContent = 'months since formation';
  s.appendChild(xcap);

  // Hover crosshair — a vertical line + focus dot snapping to the nearest
  // bar, plus a tooltip pill. This is how users get exact values when the
  // per-bar labels are suppressed at high N.
  const vLine = svgEl('line', {
    y1: padT, y2: h - padB,
    stroke: '#9098a3', 'stroke-width': '1', 'stroke-dasharray': '3 3',
    visibility: 'hidden', 'pointer-events': 'none',
  });
  const focus = svgEl('circle', {
    r: '4', fill: '#fff', stroke: '#cba974', 'stroke-width': '1.5',
    visibility: 'hidden', 'pointer-events': 'none',
  });
  s.appendChild(vLine);
  s.appendChild(focus);

  const tip = el('div', { class: 'decay-tip hidden' });
  wrap.appendChild(tip);
  function hideHover() {
    vLine.setAttribute('visibility', 'hidden');
    focus.setAttribute('visibility', 'hidden');
    tip.classList.add('hidden');
  }
  s.addEventListener('mousemove', (ev) => {
    const rect = s.getBoundingClientRect();
    const localX = ((ev.clientX - rect.left) / rect.width) * w;
    if (localX < padL || localX > padL + innerW) { hideHover(); return; }
    let bestI = 0, bestDx = Infinity;
    for (let i = 0; i < n; i++) {
      const dx = Math.abs(cxToPx(i) - localX);
      if (dx < bestDx) { bestI = i; bestDx = dx; }
    }
    const p = points[bestI];
    const cx = cxToPx(bestI);
    const cy = yToPx(p.mean_ret);
    vLine.setAttribute('x1', cx); vLine.setAttribute('x2', cx);
    focus.setAttribute('cx', cx); focus.setAttribute('cy', cy);
    vLine.setAttribute('visibility', 'visible');
    focus.setAttribute('visibility', 'visible');
    tip.innerHTML = '';
    tip.appendChild(el('div', { class: 'decay-tip-row' }, [
      el('span', { class: 'decay-tip-key' }, 'age:'),
      el('span', { class: 'decay-tip-val mono' }, `month ${p.age_months}`),
    ]));
    tip.appendChild(el('div', { class: 'decay-tip-row' }, [
      el('span', { class: 'decay-tip-key' }, 'mean:'),
      el('span', { class: 'decay-tip-val mono ' + (p.mean_ret >= 0 ? 'pos' : 'neg') }, fmtPct(p.mean_ret, 3)),
    ]));
    tip.appendChild(el('div', { class: 'decay-tip-row' }, [
      el('span', { class: 'decay-tip-key' }, '±SE:'),
      el('span', { class: 'decay-tip-val mono' }, (p.std_error * 100).toFixed(3) + '%'),
    ]));
    tip.appendChild(el('div', { class: 'decay-tip-row' }, [
      el('span', { class: 'decay-tip-key' }, 'n:'),
      el('span', { class: 'decay-tip-val mono' }, String(p.n_observations)),
    ]));
    const wrapRect = wrap.getBoundingClientRect();
    const tipX = (cx / w) * wrapRect.width;
    const tipY = (cy / h) * wrapRect.height;
    const offsetX = tipX > wrapRect.width / 2 ? -130 : 12;
    tip.style.left = `${Math.max(4, Math.min(wrapRect.width - 130, tipX + offsetX))}px`;
    tip.style.top = `${Math.max(4, tipY - 10)}px`;
    tip.classList.remove('hidden');
  });
  s.addEventListener('mouseleave', hideHover);

  wrap.appendChild(s);
  return wrap;
}

// Short interpretive line — point traders at the shape, not just bars.
function buildDecayNarrative(points) {
  if (!points.length) return document.createDocumentFragment();
  const means = points.map((p) => p.mean_ret);
  const maxIdx = means.reduce((best, m, i) => (m > means[best] ? i : best), 0);
  const minIdx = means.reduce((best, m, i) => (m < means[best] ? i : best), 0);
  const peak = points[maxIdx];
  const trough = points[minIdx];
  const last = points[points.length - 1];
  const first = points[0];
  const reversed = last.mean_ret < 0 && first.mean_ret > 0;
  const eroded = !reversed && last.mean_ret < peak.mean_ret * 0.5;

  let verdict;
  if (reversed) {
    verdict = `Signal flips sign by month ${last.age_months} — holding period is too long, mean-reversion dominates late.`;
  } else if (eroded) {
    verdict = `Peak at month ${peak.age_months} (${(peak.mean_ret * 100).toFixed(2)}%/mo) decays to ${(last.mean_ret * 100).toFixed(2)}%/mo by month ${last.age_months} — alpha is front-loaded.`;
  } else {
    verdict = `Return stays within ${(Math.abs(peak.mean_ret - trough.mean_ret) * 100).toFixed(2)}% across the holding period — signal is stable across ages.`;
  }

  return el('div', { class: 'flag-banner', style: 'border-color: var(--border-soft); color: var(--text-muted); background: transparent;' }, [
    el('div', { class: 'head', style: 'color: var(--accent);' }, 'How to read this'),
    el('div', {}, verdict),
    el('div', { style: 'margin-top:4px; font-size:11px;' },
      'Each bar is the cross-tranche mean gross return at that age (months since formation). Whiskers = ±1 SE. Peak age tells you the best holding horizon; reversal tells you where to cut.',
    ),
  ]);
}

// ---------- Interactive equity + drawdown chart ----------

const SVG_NS = 'http://www.w3.org/2000/svg';
const REGIMES = [
  { from: '2001-01-31', to: '2001-03-31', label: 'Dot-com reversal' },
  { from: '2009-03-31', to: '2009-06-30', label: '2009 momentum crash' },
];

// Regime markers (Dot-com reversal, 2009 momentum crash) only make sense
// for cross-sectional momentum strategies — they're meaningless or
// misleading on reversal, value, factor-of-factors, or non-equity papers.
// Allowlist of paper_id values that should show the markers.
const MOMENTUM_PAPER_IDS = new Set([
  'jegadeesh_titman_1993',
  'asness_moskowitz_pedersen_2013',
  'carhart_1997',
  'rouwenhorst_1998',
  'novy_marx_2012',
  'hong_lim_stein_2000',
  'moskowitz_grinblatt_1999',
]);

function shouldShowRegimes(bundle) {
  const pid = bundle?.verified_spec?.spec?.paper_id ?? bundle?.paper_id;
  return pid != null && MOMENTUM_PAPER_IDS.has(pid);
}

function svgEl(tag, attrs = {}) {
  const n = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
  return n;
}

class EquityChart {
  constructor(returns, opts = {}) {
    this.returns = returns || [];
    this.showRegimes = opts.showRegimes !== false;
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

    if (this.showRegimes) REGIMES.forEach((rg) => {
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
        fill: 'rgba(139, 35, 28, 0.05)',
        stroke: 'rgba(139, 35, 28, 0.18)',
        'stroke-dasharray': '2 3',
      }));
      const lbl = svgEl('text', {
        x: x0 + 4, y: this.padT + 12,
        fill: 'rgba(184, 151, 98, 0.80)',
        'font-family': 'Inter',
        'font-size': '9.5',
        'letter-spacing': '0.08em',
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
        stroke: '#232830', 'stroke-width': '1',
      }));
      const lbl = svgEl('text', {
        x: this.padL - 6, y: yp + 3,
        fill: '#9098a3', 'font-family': 'Inter',
        'font-size': '9.5', 'text-anchor': 'end',
      });
      lbl.textContent = `${(t * 100).toFixed(0)}%`;
      s.appendChild(lbl);
    });

    s.appendChild(svgEl('line', {
      x1: this.padL, x2: this.W - this.padR,
      y1: this.y(0), y2: this.y(0),
      stroke: '#3a4250', 'stroke-width': '1', 'stroke-dasharray': '2 4',
    }));

    const pts = slice.map((v, k) => `${this.x(lo + k)},${this.y(v)}`).join(' L ');
    s.appendChild(svgEl('path', {
      d: `M ${this.x(lo)},${this.y(0)} L ${pts} L ${this.x(hi)},${this.y(0)} Z`,
      fill: 'rgba(184, 151, 98, 0.06)',
    }));
    s.appendChild(svgEl('path', {
      d: `M ${pts}`,
      fill: 'none', stroke: '#cba974', 'stroke-width': '1',
    }));

    s.appendChild(svgEl('rect', {
      x: this.padL, y: ddTop,
      width: this.W - this.padL - this.padR, height: this.ddH,
      fill: '#0c0e12', stroke: '#232830',
    }));
    const ddPts = ddSlice.map((v, k) => `${this.x(lo + k)},${this.yDD(v)}`).join(' L ');
    s.appendChild(svgEl('path', {
      d: `M ${this.x(lo)},${this.yDD(0)} L ${ddPts} L ${this.x(hi)},${this.yDD(0)} Z`,
      fill: 'rgba(168, 57, 48, 0.12)',
    }));
    s.appendChild(svgEl('path', {
      d: `M ${ddPts}`,
      fill: 'none', stroke: '#a83930', 'stroke-width': '0.8',
    }));
    s.appendChild(this._text(this.padL - 6, ddTop + 9, '0%', { anchor: 'end', color: '#9098a3' }));
    s.appendChild(this._text(this.padL - 6, ddTop + this.ddH - 2, `${(this.ddMin * 100).toFixed(0)}%`, { anchor: 'end', color: '#9098a3' }));
    s.appendChild(this._text(this.padL + 4, ddTop + 11, 'Drawdown', { color: '#9098a3' }));

    const nTicks = Math.min(5, Math.max(hi - lo, 1));
    for (let i = 0; i <= nTicks; i++) {
      const idx = Math.round(lo + ((hi - lo) * i) / Math.max(nTicks, 1));
      const xp = this.x(idx);
      s.appendChild(svgEl('line', {
        x1: xp, x2: xp,
        y1: ddTop + this.ddH, y2: ddTop + this.ddH + 3,
        stroke: '#3a4250',
      }));
      s.appendChild(this._text(xp, ddTop + this.ddH + 14, this.returns[idx].period_end, {
        anchor: i === 0 ? 'start' : i === nTicks ? 'end' : 'middle',
        color: '#9098a3',
      }));
    }

    this.cursor = svgEl('line', {
      x1: 0, x2: 0, y1: this.padT, y2: ddTop + this.ddH,
      stroke: '#cba974', 'stroke-width': '1', 'stroke-dasharray': '2 3',
      class: 'hidden',
    });
    s.appendChild(this.cursor);
    this.dot = svgEl('circle', { cx: 0, cy: 0, r: 3, fill: '#6f9f6b', stroke: '#0c0e12', 'stroke-width': '1.5', class: 'hidden' });
    s.appendChild(this.dot);
    this.brushEl = svgEl('rect', {
      x: 0, y: this.padT, width: 0, height: this.eqH,
      fill: 'rgba(184, 151, 98, 0.16)', stroke: 'rgba(184, 151, 98, 0.55)',
      class: 'hidden',
    });
    s.appendChild(this.brushEl);
  }

  _text(x, y, str, { anchor = 'start', color = '#8b90a0' } = {}) {
    const t = svgEl('text', {
      x, y, fill: color,
      'font-family': 'Inter', 'font-size': '9.5',
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
  lag: 'Signal staleness',
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

  // D3-unavailable banner (Anthropic 529 / rate limit) — surface the
  // scorecard anyway so the user keeps the deterministic battery work.
  if (!judgment && robustness.judgment_error) {
    const banner = el('div', { class: 'flag-banner window-banner sev-warn' });
    banner.appendChild(el('div', { class: 'head' }, 'D3 narrative unavailable'));
    banner.appendChild(el('div', { class: 'banner-headline' },
      `${robustness.judgment_error}. The deterministic stress-test scorecard below is complete; only the LLM-written verdict is missing. Reload the page in a minute and click Run pipeline again to retry.`
    ));
    root.appendChild(banner);
  }

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
        el('span', { class: 'lbl' }, 'Implementable Alpha (per month)'),
        el('span', { class: 'val ' + (judgment.implementable_alpha >= 0 ? 'pos' : 'neg') }, fmtPct(judgment.implementable_alpha, 3)),
      ]),
      el('p', { class: 'verdict-summary' }, judgment.summary),
      el('div', { class: 'quote' }, [
        el('span', { class: 'meta' }, 'Implementable Alpha — Basis'),
        document.createTextNode(judgment.implementable_alpha_basis),
      ]),
      el('div', { class: 'quote' }, [
        el('span', { class: 'meta' }, `Gap Attribution · ${gapAttributionLabel(judgment.gap_attribution)}`),
        document.createTextNode(judgment.gap_attribution_evidence),
      ]),
    ]);
    root.appendChild(verdict);
  }

  // Scorecard KPIs
  const kpiBlock = el('div', { class: 'spec-section' });
  kpiBlock.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Scorecard Summary')]));
  const kpis = el('div', { class: 'kpi-grid kpi-grid-6' }, [
    kpi('Baseline Monthly Return', fmtPct(scorecard.baseline_mean_return, 3), scorecard.baseline_mean_return >= 0 ? 'pos' : 'neg'),
    kpi('Baseline t-statistic', fmtNum(scorecard.baseline_tstat, 2)),
    kpi('Observation Periods', scorecard.baseline_n_periods),
    kpi('Tests Surviving', `${scorecard.n_surviving} / ${scorecard.n_tests}`),
    kpi('Signal Decay Half-Life', scorecard.lag_half_life_days != null ? fmtNum(scorecard.lag_half_life_days, 1) + ' d' : '—'),
    kpi('Transaction Cost Tolerance', scorecard.cost_threshold_bps != null && Number.isFinite(scorecard.cost_threshold_bps) ? fmtNum(scorecard.cost_threshold_bps, 1) + ' bps' : '—'),
  ]);
  kpiBlock.appendChild(kpis);
  if (scorecard.capacity_estimate_usd != null) {
    kpiBlock.appendChild(el('div', { class: 'kpi-footnote' }, `Estimated capacity at 50bps impact: ${fmtUSD(scorecard.capacity_estimate_usd)}`));
  }
  root.appendChild(kpiBlock);

  // Fragility signals
  if (scorecard.fragility_signals && scorecard.fragility_signals.length) {
    const frag = el('div', { class: 'flag-banner flag-amber' }, [
      el('div', { class: 'head' }, `Fragility Signals (${scorecard.fragility_signals.length})`),
      ...scorecard.fragility_signals.map((s) => el('div', {}, `• ${s}`)),
    ]);
    root.appendChild(frag);
  }

  // Primary failure modes from judgment
  if (judgment && judgment.primary_failure_modes && judgment.primary_failure_modes.length) {
    const fm = el('div', { class: 'spec-section' });
    fm.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Primary Failure Modes')]));
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
  // Lag-family rows now sweep `signal.skip_months` rather than the legacy
  // `signal_lag_days`. Read whichever key the row carries so the chart works
  // for old AND new scorecard shapes (cached snapshots may still hold either).
  const lagXKey = (t) =>
    t.parameter_swept?.skip_months ??
    t.parameter_swept?.signal_lag_days ??
    0;
  const lagTests = (grouped.get('lag') || []).slice().sort((a, b) =>
    lagXKey(a) - lagXKey(b)
  );
  const lagUsesMonths = lagTests.some((t) => t.parameter_swept && 'skip_months' in t.parameter_swept);
  const costTests = (grouped.get('costs') || []).slice().sort((a, b) =>
    (a.parameter_swept?.transaction_cost_bps ?? 0) - (b.parameter_swept?.transaction_cost_bps ?? 0)
  );
  if (lagTests.length >= 2 || costTests.length >= 2) {
    const decayBlock = el('div', { class: 'spec-section' });
    decayBlock.appendChild(el('div', { class: 'spec-section-head' }, [
      el('h3', {}, 'Sensitivity Curves'),
      el('span', { class: 'pill info' }, 'Monthly excess return vs perturbed parameter'),
    ]));
    const grid = el('div', { class: 'decay-grid' });
    if (lagTests.length >= 2) {
      const lagTitle = lagUsesMonths ? 'Signal Staleness' : 'Execution Lag';
      const lagXLabel = lagUsesMonths ? 'Signal Skip (months)' : 'Signal Lag (days)';
      const lagMarkerUnit = lagUsesMonths ? 'm' : 'd';
      grid.appendChild(buildDecayCard({
        title: lagTitle,
        xLabel: lagXLabel,
        marker: scorecard.lag_half_life_days,
        markerLabel: scorecard.lag_half_life_days != null
          ? `Half-life ${fmtNum(scorecard.lag_half_life_days, 1)}${lagMarkerUnit}`
          : null,
        points: lagTests.map((t) => ({
          x: lagXKey(t),
          y: t.headline_metric,
          tstat: t.headline_tstat,
          surviving: t.surviving,
          name: t.name,
        })),
      }));
    }
    if (costTests.length >= 2) {
      grid.appendChild(buildDecayCard({
        title: 'Transaction Costs',
        xLabel: 'Transaction Cost (bps)',
        marker: scorecard.cost_threshold_bps,
        markerLabel: scorecard.cost_threshold_bps != null && Number.isFinite(scorecard.cost_threshold_bps) ? `α=0 at ${fmtNum(scorecard.cost_threshold_bps, 1)} bps` : null,
        points: costTests.map((t) => ({
          x: t.parameter_swept?.transaction_cost_bps ?? 0,
          y: t.headline_metric,
          tstat: t.headline_tstat,
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
        el('th', {}, 'Stress Test'),
        el('th', {}, 'Parameter Perturbation'),
        el('th', {}, 'Mean / AUM'),
        el('th', {}, 't-statistic'),
        el('th', {}, 'Periods'),
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
        el('td', {}, [el('span', { class: 'pill ' + (r.surviving ? 'ok' : 'fail') }, r.surviving ? 'survives' : 'fails')]),
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
  card.style.position = 'relative';
  card.appendChild(el('div', { class: 'decay-head' }, [
    el('span', { class: 'decay-title' }, title),
    el('span', { class: 'decay-x' }, xLabel),
  ]));

  // Degenerate-sweep fallback: if every y-value is the same (no variance
  // across the sweep), a line chart shows a flat line and collapses the
  // axis labels on top of each other — misleading and ugly. Replace with a
  // one-line explainer so the user understands *why* there's nothing to
  // show (almost always: intramonth lag is a no-op on monthly price data).
  if (points.length > 1) {
    const ysSpread = points.map((p) => p.y);
    const ySpan = Math.max(...ysSpread) - Math.min(...ysSpread);
    if (ySpan < 5e-5) {
      const flatVal = ysSpread[0];
      const sweep = points.map((p) => p.x).join(', ');
      card.appendChild(el('div', { class: 'decay-flat' }, [
        el('div', { class: 'decay-flat-val' }, (flatVal * 100).toFixed(2) + '%/mo'),
        el('div', { class: 'decay-flat-note' },
          `Flat across ${xLabel} ∈ {${sweep}} — sweep has no measurable effect on this data panel. ` +
          `Likely cause: monthly price data can't resolve intramonth ${xLabel.replace(/_/g, ' ')} shifts.`,
        ),
      ]));
      return card;
    }
  }

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
      stroke: t === 0 ? '#3a4250' : '#232830',
      'stroke-dasharray': t === 0 ? '2 4' : '',
    }));
    const lbl = svgEl('text', {
      x: padL - 5, y: yp + 3,
      fill: '#9098a3', 'font-family': 'Inter',
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
      stroke: '#b89762', 'stroke-width': '1', 'stroke-dasharray': '3 3',
    }));
    if (markerLabel) {
      const lbl = svgEl('text', {
        x: xp + 4, y: padT + 9,
        fill: '#b89762', 'font-family': 'Inter',
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
    d: lineD, fill: 'none', stroke: '#cba974', 'stroke-width': '1.4',
  }));

  // Points colored by surviving
  sortedPts.forEach((p) => {
    const c = svgEl('circle', {
      cx: x(p.x), cy: y(p.y), r: 3.5,
      fill: p.surviving ? '#6f9f6b' : '#a83930',
      stroke: '#0c0e12', 'stroke-width': '1.2',
    });
    const title = svgEl('title');
    title.textContent = `${p.name}: x=${p.x}, mean=${(p.y * 100).toFixed(2)}%, ${p.surviving ? 'survive' : 'fail'}`;
    c.appendChild(title);
    s.appendChild(c);
    // Value label above point
    const t = svgEl('text', {
      x: x(p.x), y: y(p.y) - 6,
      fill: p.surviving ? '#6f9f6b' : '#a83930',
      'font-family': 'Inter', 'font-size': '9',
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
      stroke: '#3a4250',
    }));
    const t = svgEl('text', {
      x: xp, y: H - padB + 13,
      fill: '#8b90a0', 'font-family': 'Inter',
      'font-size': '9', 'text-anchor': 'middle',
    });
    t.textContent = String(p.x);
    s.appendChild(t);
  });

  // Hover crosshair: vertical + horizontal dashed lines snapping to the
  // nearest data point's x, plus a tooltip pill showing exact x / y / t-stat.
  const vLine = svgEl('line', {
    y1: padT, y2: H - padB,
    stroke: '#9098a3', 'stroke-width': '1', 'stroke-dasharray': '3 3',
    visibility: 'hidden', 'pointer-events': 'none',
  });
  const hLine = svgEl('line', {
    x1: padL, x2: W - padR,
    stroke: '#9098a3', 'stroke-width': '1', 'stroke-dasharray': '3 3',
    visibility: 'hidden', 'pointer-events': 'none',
  });
  const focus = svgEl('circle', {
    r: '5', fill: '#cba974', stroke: '#fff', 'stroke-width': '1.5',
    visibility: 'hidden', 'pointer-events': 'none',
  });
  s.appendChild(vLine); s.appendChild(hLine); s.appendChild(focus);
  s.style.cursor = 'crosshair';

  const tip = el('div', { class: 'decay-tip hidden' });
  card.appendChild(tip);
  function nearest(localX) {
    let best = sortedPts[0], bestDx = Infinity;
    sortedPts.forEach((p) => {
      const dx = Math.abs(x(p.x) - localX);
      if (dx < bestDx) { best = p; bestDx = dx; }
    });
    return best;
  }
  function hideHover() {
    vLine.setAttribute('visibility', 'hidden');
    hLine.setAttribute('visibility', 'hidden');
    focus.setAttribute('visibility', 'hidden');
    tip.classList.add('hidden');
  }
  s.addEventListener('mousemove', (ev) => {
    const rect = s.getBoundingClientRect();
    const localX = ((ev.clientX - rect.left) / rect.width) * W;
    if (localX < padL || localX > W - padR) { hideHover(); return; }
    const p = nearest(localX);
    const px = x(p.x), py = y(p.y);
    vLine.setAttribute('x1', px); vLine.setAttribute('x2', px);
    hLine.setAttribute('y1', py); hLine.setAttribute('y2', py);
    focus.setAttribute('cx', px); focus.setAttribute('cy', py);
    vLine.setAttribute('visibility', 'visible');
    hLine.setAttribute('visibility', 'visible');
    focus.setAttribute('visibility', 'visible');
    tip.innerHTML = '';
    tip.appendChild(el('div', { class: 'decay-tip-row' }, [
      el('span', { class: 'decay-tip-key' }, `${xLabel}:`),
      el('span', { class: 'decay-tip-val mono' }, String(p.x)),
    ]));
    tip.appendChild(el('div', { class: 'decay-tip-row' }, [
      el('span', { class: 'decay-tip-key' }, 'return:'),
      el('span', { class: 'decay-tip-val mono ' + (p.y >= 0 ? 'pos' : 'neg') }, fmtPct(p.y, 3)),
    ]));
    if (p.tstat != null) {
      tip.appendChild(el('div', { class: 'decay-tip-row' }, [
        el('span', { class: 'decay-tip-key' }, 't-stat:'),
        el('span', { class: 'decay-tip-val mono' }, fmtNum(p.tstat, 2)),
      ]));
    }
    tip.appendChild(el('div', { class: 'decay-tip-row' }, [
      el('span', { class: 'decay-tip-key' }, 'survives:'),
      el('span', { class: 'decay-tip-val mono ' + (p.surviving ? 'pos' : 'neg') }, p.surviving ? 'yes' : 'no'),
    ]));
    const wrapRect = card.getBoundingClientRect();
    const tipX = (px / W) * wrapRect.width;
    const tipY = (py / H) * wrapRect.height;
    const offsetX = tipX > wrapRect.width / 2 ? -130 : 14;
    tip.style.left = `${Math.max(4, Math.min(wrapRect.width - 130, tipX + offsetX))}px`;
    tip.style.top = `${Math.max(4, tipY - 10)}px`;
    tip.classList.remove('hidden');
  });
  s.addEventListener('mouseleave', hideHover);

  card.appendChild(s);
  return card;
}

// ---------- Diagnosis tab (D2) ----------

function renderDiagnosis(payload, paperClaim) {
  const root = $('#diagnosis-body');
  root.innerHTML = '';
  if (!payload) {
    root.appendChild(diagnosisWaitingState(paperClaim));
    return;
  }
  // /api/diagnose and the demo bundle wrap the payload as {diagnosis, n_experiments}.
  const diagnosis = payload.diagnosis || payload;
  if (!diagnosis.primary_cause) {
    root.appendChild(diagnosisWaitingState(paperClaim));
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
    stroke: '#3a4250', 'stroke-width': '1',
  }));
  // gridlines at 25/50/75/100% of maxGap
  [0.25, 0.5, 0.75, 1.0].forEach((frac) => {
    const xp = padL + frac * (W - padL - padR);
    s.appendChild(svgEl('line', {
      x1: xp, x2: xp, y1: padT - 4, y2: H - padB + 4,
      stroke: '#232830', 'stroke-dasharray': '2 4',
    }));
    const lbl = svgEl('text', {
      x: xp, y: padT - 6,
      fill: '#9098a3', 'font-family': 'Inter',
      'font-size': '9.5', 'text-anchor': 'middle',
    });
    lbl.textContent = `${(frac * maxGap * 100).toFixed(2)}%`;
    s.appendChild(lbl);
  });
  s.appendChild(svgEl('text', {
    x: padL, y: padT - 6,
    fill: '#9098a3', 'font-family': 'Inter',
    'font-size': '9.5', 'text-anchor': 'start',
  })).textContent = '0';
  s.appendChild(svgEl('text', {
    x: 6, y: padT - 6,
    fill: '#9098a3', 'font-family': 'Inter',
    'font-size': '10', 'text-anchor': 'start',
  })).textContent = 'Mutation';
  s.appendChild(svgEl('text', {
    x: padL + 6, y: H - 4,
    fill: '#9098a3', 'font-family': 'Inter',
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
      fill: '#e4e6eb', 'font-family': 'Inter',
      'font-size': '11', 'text-anchor': 'end',
    });
    lblP.textContent = m.proposal.parameter;
    s.appendChild(lblP);
    const lblV = svgEl('text', {
      x: padL - 10, y: yMid + 10,
      fill: '#8b90a0', 'font-family': 'Inter',
      'font-size': '10', 'text-anchor': 'end',
    });
    lblV.textContent = `${m.from_value_human} → ${m.proposal.to_value}`;
    s.appendChild(lblV);

    // Pre-gap bar (always drawn, full extent, in muted blue)
    s.appendChild(svgEl('rect', {
      x: padL, y: yMid - 7, width: Math.max(xPre - padL, 0), height: 14,
      fill: 'rgba(184, 151, 98, 0.18)', stroke: 'rgba(184, 151, 98, 0.50)',
    }));

    if (closed >= 0) {
      // Gap closed: green segment from xPost → xPre (the closed portion)
      s.appendChild(svgEl('rect', {
        x: xPost, y: yMid - 7,
        width: Math.max(xPre - xPost, 1), height: 14,
        fill: 'rgba(111, 159, 107, 0.55)', stroke: '#6f9f6b',
      }));
    } else {
      // Gap widened: red segment from xPre → xPost
      s.appendChild(svgEl('rect', {
        x: xPre, y: yMid - 7,
        width: Math.max(xPost - xPre, 1), height: 14,
        fill: 'rgba(168, 57, 48, 0.55)', stroke: '#a83930',
      }));
    }

    // Markers for pre and post
    s.appendChild(svgEl('line', {
      x1: xPre, x2: xPre, y1: yMid - 9, y2: yMid + 9,
      stroke: '#cba974', 'stroke-width': '1.6',
    }));
    s.appendChild(svgEl('line', {
      x1: xPost, x2: xPost, y1: yMid - 9, y2: yMid + 9,
      stroke: closed >= 0 ? '#6f9f6b' : '#a83930', 'stroke-width': '1.6',
    }));

    // Right-side caption: post_abs_gap and % closed
    const captionX = W - padR + 4;
    const cap1 = svgEl('text', {
      x: captionX, y: yMid - 1,
      fill: closed >= 0 ? '#6f9f6b' : '#a83930',
      'font-family': 'Inter', 'font-size': '11',
    });
    cap1.textContent = `${(m.post_abs_gap * 100).toFixed(2)}%`;
    s.appendChild(cap1);
    const cap2 = svgEl('text', {
      x: captionX, y: yMid + 10,
      fill: '#8b90a0', 'font-family': 'Inter', 'font-size': '9.5',
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

// ---------- Lineage tab (was: Provenance) ----------
//
// Interactive drill-down across the full bundle: pipeline stages, data
// sources, every supporting quote (filterable + click-to-jump), data-quality
// flags, and (when present) the D2 mutation chain. Replaces the flat
// two-card provenance view that only consumed bt.provenance.

function renderLineage(bundle) {
  const root = $('#provenance-body');
  root.innerHTML = '';
  if (!bundle) {
    root.appendChild(emptyState('account_tree', 'No bundle loaded. Run extraction or load the demo.'));
    return;
  }

  const verified = bundle.verified_spec;
  const spec = verified ? verified.spec : null;
  const bt = bundle.backtest;
  const critique = bundle.critique;
  const diagnosis = bundle.diagnosis ? (bundle.diagnosis.diagnosis || bundle.diagnosis) : null;
  const robustness = bundle.robustness;

  // Toolbar — search across all quote bodies + counter
  const quotes = collectAllQuotes(bundle);
  const stats = el('span', { class: 'lineage-stats', id: 'lineage-stats' }, '');
  const search = el('input', {
    type: 'text', placeholder: 'Filter quotes by text, page, or field…',
    class: 'lineage-search', id: 'lineage-search',
    oninput: (ev) => filterLineageQuotes(ev.target.value, quotes.length),
  });
  root.appendChild(el('div', { class: 'lineage-toolbar' }, [search, stats]));

  // Section 1 — Pipeline stages (clickable, jump to source tab)
  root.appendChild(buildLineagePipelineSection(bundle, verified, bt, critique, diagnosis, robustness));

  // Section 2 — Data sources & quality flags (from bt.provenance)
  root.appendChild(buildLineageSourcesSection(bt));

  // Section 3 — Quotes index (the meat — every supporting quote across the bundle)
  root.appendChild(buildLineageQuotesSection(quotes));

  // Section 4 — D2 mutation chain (only when D2 ran)
  if (diagnosis && (diagnosis.mutation_results || []).length) {
    root.appendChild(buildLineageMutationsSection(diagnosis));
  }

  // Initialize counter
  filterLineageQuotes('', quotes.length);
}

// Collect every supporting quote across the bundle into one flat list with
// enough metadata that the UI can render and filter them, and offer a jump
// link back to the tab where the quote already lives.
function collectAllQuotes(bundle) {
  const out = [];
  const verified = bundle.verified_spec;
  const spec = verified ? verified.spec : null;

  if (spec) {
    const fields = ['universe', 'signal', 'portfolio', 'rebalance'];
    fields.forEach((f) => {
      const q = spec[f] && spec[f].supporting_quote;
      if (q) out.push({ source: 'spec', path: f, page: q.page, text: q.text, conf: q.match_confidence, verified: q.verified, tab: 'spec' });
    });
    if (spec.headline_claim && spec.headline_claim.supporting_quote) {
      const q = spec.headline_claim.supporting_quote;
      out.push({ source: 'spec', path: 'headline_claim', page: q.page, text: q.text, conf: q.match_confidence, verified: q.verified, tab: 'spec' });
    }
    (spec.ambiguities || []).forEach((a, i) => {
      if (a.paper_evidence) {
        const q = a.paper_evidence;
        out.push({ source: 'spec', path: `ambiguities[${i}] · ${a.parameter}`, page: q.page, text: q.text, conf: q.match_confidence, verified: q.verified, tab: 'spec' });
      }
    });
  }

  if (verified && verified.report && verified.report.checks) {
    verified.report.checks.forEach((c) => {
      const q = c.quote;
      if (q) out.push({
        source: 'verification', path: c.field_path,
        page: q.page, text: q.text, conf: q.match_confidence, verified: q.verified,
        tab: 'verification',
        verdict: c.failed ? 'fail' : (c.support_check && c.support_check.supports === 'partial' ? 'partial' : 'pass'),
      });
    });
  }

  if (bundle.critique && bundle.critique.criticisms) {
    bundle.critique.criticisms.forEach((c, i) => {
      if (c.evidence_quote && c.evidence_quote.text) {
        const q = c.evidence_quote;
        out.push({ source: 'critique', path: `criticism[${i}] · ${c.category}`, page: q.page, text: q.text, conf: q.match_confidence, verified: q.verified, tab: 'critique' });
      }
    });
  }

  return out;
}

function buildLineagePipelineSection(bundle, verified, bt, critique, diagnosis, robustness) {
  const stages = [];

  if (verified) {
    const r = verified.report;
    stages.push({
      stage: 'A1 + A2', tab: 'spec',
      summary: `Extracted ReplicationSpec · ${r.n_checks} quotes verified · confidence ${r.overall_confidence} · ${r.retry_count} retr${r.retry_count === 1 ? 'y' : 'ies'}`,
    });
  } else {
    stages.push({ stage: 'A1 + A2', tab: null, summary: 'Not run' });
  }

  if (critique && critique.criticisms) {
    stages.push({
      stage: 'A3', tab: 'critique',
      summary: `${critique.criticisms.length} criticisms · severity-mixed adversarial review`,
    });
  } else {
    stages.push({ stage: 'A3', tab: null, summary: 'Not run' });
  }

  if (bt) {
    stages.push({
      stage: 'Engine', tab: 'backtest',
      summary: `${bt.n_periods} periods · μ=${fmtPct(bt.mean_return, 3)} · t=${fmtNum(bt.alpha_tstat, 2)} · spec_hash ${bt.spec_hash || '—'}`,
    });
  } else {
    stages.push({ stage: 'Engine', tab: null, summary: 'Not run' });
  }

  if (diagnosis && diagnosis.primary_cause) {
    stages.push({
      stage: 'D2', tab: 'diagnosis',
      summary: `${diagnosis.experiments_run} experiments · primary=${diagnosis.primary_cause} · residual ${fmtPct(diagnosis.residual_abs_gap, 3)}`,
    });
  } else if (bundle.paper_claim) {
    stages.push({ stage: 'D2', tab: null, summary: 'Awaiting backtest (claim ready)' });
  } else {
    stages.push({ stage: 'D2', tab: null, summary: 'No paper claim — skipped' });
  }

  if (robustness && robustness.scorecard) {
    const sc = robustness.scorecard;
    const sig = robustness.judgment ? robustness.judgment.signal_type : '—';
    stages.push({
      stage: 'D3', tab: 'robustness',
      summary: `${sc.n_surviving}/${sc.n_tests} surviving · signal=${sig}`,
    });
  } else {
    stages.push({ stage: 'D3', tab: null, summary: 'Not run' });
  }

  const body = el('div', { class: 'section-body' });
  stages.forEach((s) => {
    const isActive = !!s.tab;
    const row = el('div', { class: 'lineage-stage-row' + (isActive ? '' : ' dim') }, [
      el('span', { class: 'stage-name' }, s.stage),
      el('span', { class: 'stage-summary' }, s.summary),
      isActive ? el('span', { class: 'stage-cta' }, `→ ${s.tab}`) : null,
    ]);
    if (isActive) row.addEventListener('click', () => showTab(s.tab));
    body.appendChild(row);
  });

  const section = el('details', { class: 'lineage-section', open: 'open' }, [
    el('summary', {}, [
      el('span', { class: 'summary-title' }, 'Pipeline stages'),
      el('span', { class: 'summary-meta' }, `${stages.filter((s) => !!s.tab).length}/${stages.length} ran`),
    ]),
    body,
  ]);
  return section;
}

function buildLineageSourcesSection(bt) {
  const body = el('div', { class: 'section-body' });
  if (!bt || !bt.provenance) {
    body.appendChild(el('div', { class: 'lineage-no-results' }, 'No backtest provenance yet. Run the engine to populate this section.'));
  } else {
    const engine = bt.provenance;
    body.appendChild(el('div', { class: 'lineage-card' }, [
      el('div', { class: 'card-head' }, [
        el('span', { class: 'card-tag' }, `Level 2 · ${capitalize(engine.source_tier)}`),
        el('span', { class: 'card-meta' }, engine.source_id),
      ]),
      el('div', { class: 'card-meta' }, [
        `record_id: ${engine.record_id}`,
        el('br'),
        `as_of: ${engine.as_of_date ?? '—'} · retrieved: ${engine.retrieved_at}`,
        ...(engine.notes ? [el('br'), engine.notes] : []),
      ]),
    ]));
    body.appendChild(el('div', { class: 'lineage-card' }, [
      el('div', { class: 'card-head' }, [
        el('span', { class: 'card-tag' }, 'Level 1 · Primary'),
        el('span', { class: 'card-meta' }, 'defeatbeta_yahoo (parent)'),
      ]),
      el('div', { class: 'card-meta' }, `parent_ids: ${(engine.parent_ids || []).join(', ') || '—'}`),
    ]));
    (bt.data_quality_flags || []).forEach((f) => {
      body.appendChild(el('div', { class: 'lineage-flag' }, `⚠ ${f}`));
    });
  }
  return el('details', { class: 'lineage-section', open: 'open' }, [
    el('summary', {}, [
      el('span', { class: 'summary-title' }, 'Data sources & quality flags'),
      el('span', { class: 'summary-meta' }, bt && bt.provenance ? '2 records' : '—'),
    ]),
    body,
  ]);
}

function buildLineageQuotesSection(quotes) {
  const body = el('div', { class: 'section-body', id: 'lineage-quotes-body' });
  if (!quotes.length) {
    body.appendChild(el('div', { class: 'lineage-no-results' }, 'No supporting quotes yet — run extraction or load the demo.'));
  } else {
    quotes.forEach((q, idx) => {
      const verdictPill = q.verdict
        ? el('span', { class: `pill ${q.verdict === 'pass' ? 'ok' : q.verdict === 'partial' ? 'warn' : 'fail'}` }, q.verdict)
        : null;
      const vBadge = q.verified
        ? el('span', { class: 'pill ok' }, 'verified')
        : el('span', { class: 'pill warn' }, 'unverified');
      const card = el('div', {
        class: 'lineage-card',
        'data-search': `${q.path} ${q.text} page ${q.page} ${q.source}`.toLowerCase(),
        id: `lineage-quote-${idx}`,
      }, [
        el('div', { class: 'card-head' }, [
          el('span', { class: 'card-tag' }, `${q.source} · ${q.path}`),
          el('span', { class: 'card-meta' }, `page ${q.page} · conf ${fmtNum(q.conf, 2)}`),
          vBadge,
          verdictPill,
        ]),
        el('div', { class: 'card-body' }, '“' + q.text + '”'),
        el('div', { class: 'card-foot' }, [
          el('button', {
            class: 'jump-link',
            onclick: () => showTab(q.tab),
          }, `→ open ${q.tab} tab`),
        ]),
      ]);
      body.appendChild(card);
    });
  }
  return el('details', { class: 'lineage-section', open: 'open' }, [
    el('summary', {}, [
      el('span', { class: 'summary-title' }, 'Quotes index'),
      el('span', { class: 'summary-meta' }, `${quotes.length} total`),
    ]),
    body,
  ]);
}

function buildLineageMutationsSection(diagnosis) {
  const body = el('div', { class: 'section-body' });
  body.appendChild(el('div', { class: 'card-meta', style: 'margin-bottom:8px;' },
    `Primary cause: ${diagnosis.primary_cause} · ${diagnosis.experiments_run} experiments · residual ${fmtPct(diagnosis.residual_abs_gap, 3)}`,
  ));
  (diagnosis.mutation_results || []).forEach((m, i) => {
    const closed = m.pre_abs_gap - m.post_abs_gap;
    const closedSign = closed > 0 ? 'closed' : closed < 0 ? 'widened' : 'unchanged';
    const pill = closed > 0 ? el('span', { class: 'pill ok' }, 'closed')
      : closed < 0 ? el('span', { class: 'pill fail' }, 'widened')
      : el('span', { class: 'pill info' }, 'unchanged');
    body.appendChild(el('div', { class: 'lineage-card' }, [
      el('div', { class: 'card-head' }, [
        el('span', { class: 'card-tag' }, `mutation[${i}] · ${m.proposal.parameter}`),
        el('span', { class: 'card-meta' }, `${m.from_value_human || '—'} → ${m.proposal.to_value}`),
        pill,
      ]),
      el('div', { class: 'card-meta' },
        `pre_gap=${fmtPct(m.pre_abs_gap, 3)} · post_gap=${fmtPct(m.post_abs_gap, 3)} · Δ=${fmtPct(Math.abs(closed), 3)} ${closedSign}`,
      ),
      el('div', { class: 'card-foot' }, [
        el('button', { class: 'jump-link', onclick: () => showTab('diagnosis') }, '→ open diagnosis tab'),
      ]),
    ]));
  });
  return el('details', { class: 'lineage-section', open: 'open' }, [
    el('summary', {}, [
      el('span', { class: 'summary-title' }, 'D2 mutation chain'),
      el('span', { class: 'summary-meta' }, `${(diagnosis.mutation_results || []).length} mutations`),
    ]),
    body,
  ]);
}

function filterLineageQuotes(query, total) {
  const q = (query || '').trim().toLowerCase();
  const cards = $$('#lineage-quotes-body .lineage-card');
  let shown = 0;
  cards.forEach((c) => {
    const hay = (c.getAttribute('data-search') || '');
    const matched = !q || hay.includes(q);
    c.classList.toggle('hidden', !matched);
    if (matched) shown++;
  });
  const stats = $('#lineage-stats');
  if (stats) stats.textContent = q ? `${shown} / ${total} quotes match` : `${total} quote${total === 1 ? '' : 's'} indexed`;
}

// ---------- empty state ----------

function emptyState(icon, msg) {
  return el('div', { class: 'empty-state' }, [
    el('span', { class: 'material-symbols-outlined' }, icon),
    msg,
  ]);
}

// Diagnosis tab empty state — splits on whether D2 has a claim to chew on.
// Without a claim, D2 is structurally pointless; with one but no diagnosis,
// the user just hasn't run the pipeline yet (or D2 is still fetching).
function diagnosisWaitingState(paperClaim) {
  if (!paperClaim) {
    return emptyState(
      'biotech',
      'No paper claim available. A1 did not extract a headline number from this paper, so D2 has no comparison target. Add one manually via /api/diagnose if you have one.',
    );
  }
  const wrap = el('div', { class: 'diagnosis-pending' }, [
    el('div', { class: 'spec-section' }, [
      el('div', { class: 'spec-section-head' }, [
        el('h3', {}, 'D2 ready · awaiting backtest'),
        el('span', { class: 'pill info' }, 'A1 extracted a headline claim'),
      ]),
      el('p', { class: 'verdict-summary' }, 'D2 will run automatically after the backtest completes. Below: the paper-reported target D2 will diagnose against.'),
    ]),
    el('div', { class: 'spec-section' }, [
      el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Paper claim (D2 target)')]),
      (() => {
        const dl = el('dl', { class: 'spec-section-body' });
        [
          ['monthly_return (paper)', fmtPct(paperClaim.monthly_return, 3)],
          ['t_stat (paper)', paperClaim.tstat == null ? '—' : fmtNum(paperClaim.tstat, 2)],
          ['window', paperClaim.window || '—'],
        ].forEach(([k, v]) => {
          dl.appendChild(el('dt', {}, k));
          dl.appendChild(el('dd', {}, String(v)));
        });
        return dl;
      })(),
    ]),
  ]);
  return wrap;
}

// ---------- boot ----------

wireDialForm();
refreshPapers();
refreshOverrideChip();
refreshRunButton();
log('SYS', 'UI ready. Click Load demo or drop a PDF.', 'sys');
setStatus('Idle — no paper loaded', 'gray');


// ============================================================
// Phase 5 — load report.json + render verdict strip + tabs
// ============================================================

const phase5 = {
  report: null,
  costCurve: [],
};

function fmtPctSigned(x, digits = 3) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  const v = (x * 100).toFixed(digits);
  return (x >= 0 ? '+' : '') + v + '%';
}
function fmtTstat(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return 't = —';
  const sign = x >= 0 ? '+' : '';
  return `t = ${sign}${Number(x).toFixed(2)}`;
}
function fmtMoney(x) {
  if (x === null || x === undefined) return '—';
  if (x >= 1e9) return `$${(x / 1e9).toFixed(2)}B`;
  if (x >= 1e6) return `$${(x / 1e6).toFixed(0)}M`;
  if (x >= 1e3) return `$${(x / 1e3).toFixed(0)}K`;
  return `$${Math.round(x)}`;
}

// Fetch the Phase 5 JT report and stash it on `phase5.report` for later
// callers (the DBT toggle, the "Load briefing" button). When `render`
// is true the verdict strip is also populated; when false the strip
// stays in its dash-placeholder state. Bootstrap calls with render=false
// so a hard refresh shows blank values until the user explicitly opts
// in via "Load briefing" or by uploading a paper.
async function loadPhase5Report({ render = true } = {}) {
  try {
    const r = await fetch('/api/report');
    if (!r.ok) {
      log('SYS', 'No Phase 5 report available (run pipelines first).', 'sys');
      return null;
    }
    const data = await r.json();
    phase5.report = data;
    if (render) {
      renderVerdictStrip(data);
      renderDbtPanel(data);
      log('SYS', `Phase 5 report loaded: ${data.headline?.paper_id ?? '—'}`, 'sys');
    }
    return data;
  } catch (e) {
    log('SYS', `Failed to load /api/report: ${e.message}`, 'err');
    return null;
  }
}

// ----- Verdict strip -----

function renderVerdictStrip(report) {
  const h = report && report.headline;
  if (!h) return;
  const strip = $('#verdict-strip');
  strip.hidden = false;

  $('#vs-paper-title').textContent = h.paper_title || '—';

  // Left column — paper claim. Write the value cells FIRST, before any
  // optional decorations (pills, popover, DBT toggle). If a downstream
  // helper throws, the user still sees the populated numbers — they're
  // the most important thing on the strip.
  // Guard against the impossible-but-emitted (value==0 with non-zero
  // t-stat) case: render as "not extracted". Statistical-test papers
  // (variance-ratio class — Lo-MacKinlay, Poterba-Summers, AQR streaks)
  // genuinely don't have a tradeable L/S claim — short "VR statistic"
  // tag instead of "—/mo".
  const c = h.claim || {};
  const claimSuspicious = c.value === 0 && c.tstat != null && c.tstat !== 0;
  const noClaim = c.value == null || claimSuspicious;
  const statOnlyClaim = noClaim && detectStatisticalOnlyPaper(state.bundle);
  if (statOnlyClaim) {
    $('#vs-claim-value').textContent = 'VR statistic';
    $('#vs-claim-tstat').textContent = '';
    $('#vs-claim-loc').textContent = 'Statistical test — no L/S claim';
  } else {
    $('#vs-claim-value').textContent = claimSuspicious ? 'not extracted' : (fmtPctSigned(c.value, 3) + '/mo');
    $('#vs-claim-tstat').textContent = claimSuspicious ? '' : fmtTstat(c.tstat);
    $('#vs-claim-loc').textContent = c.paper_location || '';
  }

  // Right column — implementable verdict
  const v = (h.verdict || {});
  $('#vs-impl-value').textContent = fmtPctSigned(v.implementable_alpha, 3) + '/mo';
  $('#vs-impl-tstat').textContent = fmtTstat(v.tstat_estimate);
  $('#vs-tag').textContent = v.tradeable_label || '—';
  $('#vs-col-verdict').setAttribute('data-tag', v.tradeable_label || '');

  // Confidence + signal-type pills — wrap in try/catch so a helper
  // failure here can never abort the value-cell writes above.
  try {
    $('#vs-confidence').innerHTML = renderConfidencePills(v.confidence, v.signal_type);
  } catch (e) {
    console && console.error && console.error('renderConfidencePills failed:', e);
    $('#vs-confidence').textContent = '';
  }

  // Window row (collapsed to single horizontal line of small-caps)
  $('#vs-paper-window').textContent = ' ' + (h.paper_window || h.sample_paper || '—');
  $('#vs-engine-window').textContent = ' ' + (h.engine_window || h.sample_engine || '—');
  $('#vs-gap-attr').textContent = ' ' + ((v.gap_attribution || '—').replace(/_/g, ' '));

  // Strip stays clean — the verdict tag + numbers above already say
  // enough. The verbose D3 prose ("Implementable alpha is roughly
  // 0.96%/mo (t≈1.84) under 10 bps round-trip costs…") was crowding the
  // top bar, so it now lives ONLY in the Rationale popover.
  $('#vs-summary').textContent = '';
  const popover = $('#vs-why-popover');
  if (popover) popover.textContent = v.summary_first_clause || '';

  // DBT toggle — only relevant for the JT cached report; live runs hide it
  if (report.generalization) {
    $('#vs-dbt-toggle').hidden = false;
    const passed = report.generalization.architectural_test_passed;
    $('#vs-dbt-label').innerHTML = `Generalization (DBT 1985) ${passed ? '✓' : '✗'}`;
  } else {
    $('#vs-dbt-toggle').hidden = true;
  }

  // Topnav: the redundant verdict badge stays hidden (verdict strip carries
  // this signal already; per Phase 5 polish brief).
}

// Refresh the verdict strip with LIVE /api/robustness data so the headline
// stays in sync with whatever the engine + D3 actually just produced. The
// /api/report path populates the strip from cached postfix outputs, which
// can be stale relative to the user's current dial settings or paper.
function refreshVerdictStripFromLive(robustnessPayload, bundle) {
  const strip = $('#verdict-strip');
  if (!strip) return;
  const sc = robustnessPayload && robustnessPayload.scorecard;
  const j  = robustnessPayload && robustnessPayload.judgment;
  if (!sc) return;

  // Paper title + claim columns from the bundle's verified spec / paper_claim.
  // Same fallback as buildLiveReport: derive paper_claim from spec.headline_claim
  // when the flat projection wasn't propagated through the chain.
  const verified = bundle && bundle.verified_spec && bundle.verified_spec.spec;
  let claim = bundle && bundle.paper_claim;
  if (!claim && verified && verified.headline_claim && verified.headline_claim.monthly_return != null) {
    const hc = verified.headline_claim;
    claim = {
      monthly_return: hc.monthly_return,
      tstat: hc.t_stat,
      window: hc.window_label,
      paper_location: hc.paper_location,
    };
    // Persist on the bundle so downstream consumers (D2, factor compare) see it too.
    if (bundle) bundle.paper_claim = claim;
  }
  const titleEl = $('#vs-paper-title');
  if (titleEl && verified && verified.paper_title) titleEl.textContent = verified.paper_title;

  if (claim) {
    const cv = $('#vs-claim-value');
    const ct = $('#vs-claim-tstat');
    const cl = $('#vs-claim-loc');
    // Self-consistency guard (monthly_return==0 with non-zero tstat is
    // mathematically impossible — A1 placeholder zero) AND statistical-
    // test paper-class shortcut: when the paper genuinely has no L/S
    // claim and uses variance_ratio, show a short tag instead of dashes.
    const suspicious = claim.monthly_return === 0 && claim.tstat != null && claim.tstat !== 0;
    const noClaim = claim.monthly_return == null || suspicious;
    const statOnly = noClaim && detectStatisticalOnlyPaper(state.bundle);
    if (statOnly) {
      if (cv) cv.textContent = 'VR statistic';
      if (ct) ct.textContent = '';
      if (cl) cl.textContent = 'Statistical test — no L/S claim';
    } else {
      if (cv) cv.textContent = suspicious ? 'not extracted' : (fmtPctSigned(claim.monthly_return, 3) + '/mo');
      if (ct) ct.textContent = suspicious ? '' : fmtTstat(claim.tstat);
      if (cl) cl.textContent = claim.paper_location || claim.window || '';
    }
  }

  // Implementable α + tag from D3 judgment, falling back to the scorecard
  // baseline if D3 is unavailable. The live RobustnessJudgment doesn't
  // carry a tstat directly — derive it by finding the scorecard row whose
  // headline_metric matches implementable_alpha (basisMatchedTstat).
  const alpha = j ? j.implementable_alpha : sc.baseline_mean_return;
  const tstat = j ? (j.tstat_estimate ?? basisMatchedTstat(sc, j.implementable_alpha)) : sc.baseline_tstat;
  const conf  = j ? j.confidence : 'medium';
  const sigType = j ? j.signal_type : '—';
  const gap = j ? j.gap_attribution : null;

  // Structural-validity gates: detect engine-proxy mode and missing
  // claim. When either is on, the alpha number is not a verdict on the
  // paper — relabel to make that explicit.
  const proxyMode = detectProxyMode(bundle && bundle.backtest);
  const noTarget = detectNoTarget(claim);

  const iv = $('#vs-impl-value');
  if (iv) {
    if (proxyMode) {
      iv.textContent = fmtPctSigned(alpha, 3) + '/mo (proxy)';
    } else if (noTarget) {
      iv.textContent = fmtPctSigned(alpha, 3) + '/mo (vs zero)';
    } else {
      iv.textContent = fmtPctSigned(alpha, 3) + '/mo';
    }
  }
  const it = $('#vs-impl-tstat');
  // tstat is now derived above (basisMatchedTstat from the scorecard)
  // even when D3's judgment lacks an explicit tstat_estimate — render it.
  if (it) it.textContent = fmtTstat(tstat);
  const tagLabel = tradeableLabel(alpha, tstat ?? 0, conf, { proxyMode, noTarget });
  const tag = $('#vs-tag');
  if (tag) tag.textContent = tagLabel;
  const verdictCol = $('#vs-col-verdict');
  if (verdictCol) verdictCol.setAttribute('data-tag', tagLabel);

  // Confidence + signal-type subline. When PROXY_ONLY or NO_TARGET, the
  // signal_type field is meaningless — D3 was judging the wrong thing.
  // Wrap in try/catch so a pill-rendering failure can never abort the
  // value-cell writes above.
  try {
    const cf = $('#vs-confidence');
    const statOnlyVS = detectStatisticalOnlyPaper(state.bundle);
    if (cf) {
      if (proxyMode) {
        cf.innerHTML = '<span class="vs-pill vs-pill-warn" title="The engine substituted a 12-month past-return proxy because the paper\'s signal kind isn\'t natively implemented yet. Every number is a verdict on the proxy, not the paper.">Proxy run</span>';
      } else if (noTarget && statOnlyVS) {
        cf.innerHTML = '<span class="vs-pill vs-pill-info" title="Variance-ratio / autocorrelation papers (Lo-MacKinlay, Poterba-Summers, …) report statistics, not a tradeable L/S return. The implementable alpha is the implicit strategy\'s return vs zero.">Statistical test</span>';
      } else if (noTarget) {
        cf.innerHTML = '<span class="vs-pill vs-pill-warn" title="A1 didn\'t extract a paper headline number (common for papers reporting Sharpe ratios or regression alphas). The implementable alpha is measured vs zero, not vs the paper\'s claim.">No paper claim</span>';
      } else {
        cf.innerHTML = renderConfidencePills(conf, sigType);
      }
    }
  } catch (e) {
    console && console.error && console.error('verdict-strip pill render failed:', e);
    const cf2 = $('#vs-confidence');
    if (cf2) cf2.textContent = '';
  }

  // Window + gap-attribution row from window_info / judgment
  const wi = robustnessPayload.window_info || {};
  const pwEl = $('#vs-paper-window');
  if (pwEl) pwEl.textContent = ' ' + (wi.paper_start && wi.paper_end ? `${wi.paper_start} → ${wi.paper_end}` : '—');
  const ewEl = $('#vs-engine-window');
  if (ewEl) ewEl.textContent = ' ' + (wi.engine_start && wi.engine_end ? `${wi.engine_start} → ${wi.engine_end}` : '—');
  const gaEl = $('#vs-gap-attr');
  if (gaEl) gaEl.textContent = ' ' + ((proxyMode || noTarget) ? 'not applicable (no faithful comparison)' : ((gap || '—').replace(/_/g, ' ')));

  // Why-popover summary. When proxy / no-target, override D3's narrative
  // with the structural caveat — D3 was writing about a strategy the
  // user didn't ask for.
  // Two-track summary: a tight one-liner for the strip (the strip CSS
  // also -webkit-line-clamps to 2 lines), and the long-form text in the
  // Rationale popover for the user who wants the full reasoning.
  let summaryShort, summaryLong;
  if (proxyMode) {
    summaryShort = 'PROXY ONLY — engine ran a 12-month momentum proxy, not the paper\'s signal.';
    summaryLong = 'The engine could not run the paper\'s actual signal (variance ratio, learned model, fundamental ratio, etc.) and substituted a 12-month past-return proxy. Every number on this dashboard is a verdict on the proxy, not on the paper. To get a real replication, implement the paper\'s signal in src/engine/signals.py.';
  } else if (noTarget && statOnlyVS) {
    summaryShort = 'Statistical-test paper — no tradeable headline; alpha measured vs zero (random-walk null).';
    summaryLong = 'Statistical-test paper (variance ratio / autocorrelation against the random-walk null) — Lo-MacKinlay 1988, Poterba-Summers 1988, etc. The paper reports VR(k) statistics, not a tradeable monthly long-short return. The implementable-alpha number is the engine\'s alpha for the implicit contrarian/momentum strategy implied by signal.direction, measured vs zero.';
  } else if (noTarget) {
    summaryShort = 'No paper headline extracted — alpha measured vs zero, not vs the paper.';
    summaryLong = 'A1 could not extract a paper headline number (common for papers reporting Sharpe ratios, regression alphas, or non-standard metrics). The implementable-alpha number is the engine\'s alpha vs zero, NOT vs the paper\'s claim. D2 cannot run without a comparison target.';
  } else if (j) {
    // D3 narrative goes ONLY in the Rationale popover — keep the strip
    // clean. The verdict tag + numbers in the columns above already say
    // enough; the long-form prose was creating top-bar noise.
    summaryLong = j.summary || j.implementable_alpha_basis || '';
    summaryShort = '';
  } else {
    summaryShort = '';
    summaryLong = 'D3 judgment unavailable — showing baseline scorecard.';
  }
  const sumEl = $('#vs-summary');
  if (sumEl) sumEl.textContent = summaryShort;
  const popover = $('#vs-why-popover');
  if (popover) popover.textContent = summaryLong;

  strip.hidden = false;
}

// "Why?" popover toggle for the implementable-alpha summary clause
function setupVerdictWhy() {
  const trigger = $('#vs-why');
  const popover = $('#vs-why-popover');
  if (!trigger || !popover) return;
  const open = () => {
    const r = trigger.getBoundingClientRect();
    popover.style.top = `${r.bottom + 6 + window.scrollY}px`;
    popover.style.left = `${Math.max(8, r.left - 80)}px`;
    popover.hidden = false;
  };
  const close = () => { popover.hidden = true; };
  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    popover.hidden ? open() : close();
  });
  document.addEventListener('click', (e) => {
    if (!popover.hidden && !popover.contains(e.target) && e.target !== trigger) close();
  });
}

// ----- Diagnosis tab -----

function renderDiagnosisTab(report) {
  const d = report.diagnosis;
  if (!d) return;

  $('#diagnosis-pre-fix-summary').textContent = d.pre_fix_summary || '';
  $('#diagnosis-confidence-pill').textContent = `D2 confidence: ${d.confidence ?? '—'}`;
  $('#diagnosis-experiments-chip').textContent =
    `${d.experiments_run ?? 0} experiment(s) · ${d.early_exit ? 'early-exited' : 'ran to cap'}`;
  $('#diagnosis-kind-chip').textContent = `kind: ${d.primary_cause_kind ?? '—'}`;

  // Primary cause card
  const pc = $('#diagnosis-primary-cause');
  pc.innerHTML = '';
  pc.appendChild(el('div', { class: 'callout' }, [
    el('strong', {}, `${d.primary_cause ?? '—'}`),
    el('div', { class: 'sub', style: 'margin-top:6px;' }, d.primary_cause_summary || ''),
    el('div', { class: 'sub', style: 'margin-top:8px;font-style:italic;color:var(--text-muted);' },
       d.primary_cause_evidence || ''),
  ]));

  // Mutation log table
  const log = $('#diagnosis-mutation-log');
  log.innerHTML = '';
  if (!d.mutation_results || d.mutation_results.length === 0) {
    log.appendChild(emptyState('science', 'No mutations in the cached log.'));
    return;
  }
  const t = el('table', { class: 'mut-table' });
  t.appendChild(el('thead', {}, el('tr', {}, [
    el('th', {}, '#'),
    el('th', {}, 'parameter'),
    el('th', {}, 'from → to'),
    el('th', {}, 'pre mean'),
    el('th', {}, 'post mean'),
    el('th', {}, 'Δ gap'),
    el('th', {}, 'sign-flip'),
  ])));
  const tb = el('tbody', {});
  d.mutation_results.forEach((m) => {
    tb.appendChild(el('tr', {}, [
      el('td', { class: 'num' }, String(m.n)),
      el('td', { class: 'code' }, m.parameter || '—'),
      el('td', {}, `${m.from_value ?? '—'} → ${m.to_value ?? '—'}`),
      el('td', { class: 'num' }, fmtPctSigned(m.pre_mean_return, 3)),
      el('td', { class: 'num' }, fmtPctSigned(m.post_mean_return, 3)),
      el('td', { class: 'num ' + (m.gap_delta > 0 ? 'closed-yes' : '') },
         fmtPctSigned(m.gap_delta, 3)),
      el('td', { class: m.closed_sign_flip ? 'closed-yes' : 'closed-no' },
         m.closed_sign_flip ? '✓' : '—'),
    ]));
  });
  t.appendChild(tb);
  log.appendChild(t);

  // Residual block
  const res = $('#diagnosis-residual');
  res.innerHTML = '';
  res.appendChild(el('div', { class: 'callout' }, [
    el('div', {}, [
      el('strong', {}, `|gap| = ${fmtPctSigned(d.residual_abs_gap, 3)}/mo`),
    ]),
    el('div', { class: 'sub', style: 'margin-top:8px;' },
       d.residual_gap_likely_cause || '—'),
  ]));
}

// ----- Robustness tab -----

function renderRobustnessTab(report) {
  const r = report.robustness;
  if (!r) return;

  $('#robustness-baseline-line').textContent =
    `Baseline ${fmtPctSigned(r.baseline_mean_return, 3)}/mo, ${fmtTstat(r.baseline_tstat)}, n=${r.baseline_n_periods}.`;
  $('#robustness-surviving-pill').textContent =
    `surviving ${r.n_surviving}/${r.n_tests}`;

  const j = r.judgment || {};
  $('#robustness-confidence-chip').textContent = `D3 confidence: ${j.confidence ?? '—'}`;

  // Judgment narrative
  const jbox = $('#robustness-judgment');
  jbox.innerHTML = '';
  jbox.appendChild(el('div', { class: 'callout' }, [
    el('div', { style: 'font-size:18px;font-weight:600;margin-bottom:4px;' },
       `Implementable α ≈ ${fmtPctSigned(j.implementable_alpha, 3)}/mo`),
    el('div', { class: 'sub', style: 'margin-bottom:8px;' },
       `signal_type: ${j.signal_type ?? '—'} · gap: ${(j.gap_attribution ?? '—').replace(/_/g, ' ')} · capacity: ${fmtMoney(j.capacity_estimate_usd)}`),
    el('div', { class: 'sub', style: 'font-size:11.5px;font-style:italic;' },
       j.implementable_alpha_basis || ''),
  ]));
  if ((j.primary_failure_modes || []).length > 0) {
    const list = el('ul', { class: 'fragility-list', style: 'margin-top:10px;' });
    j.primary_failure_modes.forEach((m) => list.appendChild(el('li', {}, m)));
    jbox.appendChild(list);
  }

  // Cost slider — store curve, set ticks, attach listener
  phase5.costCurve = r.cost_curve || [];
  $('#robustness-cost-chip').textContent = r.cost_threshold_bps == null
    ? `threshold: never reached (≤50bps)`
    : `threshold: ${Number(r.cost_threshold_bps).toFixed(1)} bps`;
  renderCostSlider();

  // Static cost curve table
  renderRowTable('cost-curve-table', phase5.costCurve, [
    ['bps', (p) => p.bps.toFixed(0)],
    ['mean return', (p) => fmtPctSigned(p.mean_return, 3) + '/mo'],
    ['t-stat', (p) => fmtTstat(p.tstat).replace('t = ', '')],
    ['surviving', (p) => p.surviving ? '✓' : '—'],
  ]);

  // Liquidity table
  renderRowTable('liquidity-table', r.liquidity_rows || [], [
    ['min_price', (p) => `$${p.min_price}`],
    ['mean return', (p) => fmtPctSigned(p.mean_return, 3) + '/mo'],
    ['t-stat', (p) => fmtTstat(p.tstat).replace('t = ', '')],
    ['surviving', (p) => p.surviving ? '✓' : '—'],
  ]);

  // Subperiod table
  renderRowTable('subperiod-table', r.subperiod_rows || [], [
    ['window', (p) => p.label || p.name],
    ['range', (p) => `${(p.start_date ?? '').slice(0,7)} → ${(p.end_date ?? '').slice(0,7)}`],
    ['mean', (p) => fmtPctSigned(p.mean_return, 3) + '/mo'],
    ['t-stat', (p) => fmtTstat(p.tstat).replace('t = ', '')],
    ['n', (p) => String(p.n_periods ?? '—')],
    ['surv', (p) => p.surviving ? '✓' : '—'],
  ]);

  // Capacity + fragility callout
  const cf = $('#capacity-fragility');
  cf.innerHTML = '';
  if (r.capacity_estimate_usd) {
    cf.appendChild(el('div', { class: 'callout' }, [
      el('strong', {}, `Capacity (50 bps impact): ${fmtMoney(r.capacity_estimate_usd)}`),
      el('div', { class: 'sub', style: 'margin-top:6px;' }, (r.capacity_rows[0]?.notes || '')),
    ]));
  }
  if ((r.fragility_signals || []).length > 0) {
    const list = el('ul', { class: 'fragility-list', style: 'margin-top:10px;' });
    r.fragility_signals.forEach((s) => list.appendChild(el('li', {}, s)));
    cf.appendChild(list);
  }
}

// ----- Cost slider: single source of truth -----
// Both the top-level quick-controls slider AND the Robustness-tab slider call
// setCostBps(bps) — which updates ALL displays (verdict strip, top-level
// readout, robustness-tab readout, verdict-tag recomputed).

function renderCostSlider() {
  // Detail slider in the Robustness tab
  const slider = $('#cost-slider');
  const ticks = $('#cost-slider-ticks');
  if (!slider || phase5.costCurve.length === 0) return;
  const xs = phase5.costCurve.map((p) => p.bps);
  slider.min = String(Math.min(...xs));
  slider.max = String(Math.max(...xs));
  slider.step = '0.5';
  slider.value = '10';

  ticks.innerHTML = '';
  xs.forEach((x) => ticks.appendChild(el('span', {}, String(x.toFixed(0)))));

  slider.oninput = () => setCostBps(parseFloat(slider.value), 'detail');
  // Top-level slider
  const qc = $('#qc-cost-slider');
  if (qc) {
    qc.min = slider.min; qc.max = slider.max; qc.step = slider.step;
    qc.value = '10';
    qc.oninput = () => setCostBps(parseFloat(qc.value), 'top');
  }
  // Initial display state at default 10 bps
  setCostBps(10, 'init');
}

function interpCostCurve(bps) {
  const c = phase5.costCurve;
  if (c.length === 0) return null;
  if (bps <= c[0].bps) return c[0];
  if (bps >= c[c.length - 1].bps) return c[c.length - 1];
  for (let i = 1; i < c.length; i++) {
    if (bps <= c[i].bps) {
      const a = c[i - 1], b = c[i];
      const f = (bps - a.bps) / (b.bps - a.bps);
      return {
        bps,
        mean_return: a.mean_return + f * (b.mean_return - a.mean_return),
        tstat: (a.tstat ?? 0) + f * ((b.tstat ?? 0) - (a.tstat ?? 0)),
        surviving: a.surviving && b.surviving,
        interpolated: true,
      };
    }
  }
  return c[c.length - 1];
}

function tradeableLabel(alpha, tstat, confidence, opts = {}) {
  // Structural-validity gates. None of the alpha-based labels below are
  // meaningful when the engine ran a proxy strategy or when there's no
  // paper-claim target to compare against — calling a 12-month-momentum
  // proxy "UNDER WATER" implies the paper failed, when the truth is the
  // tool never tested the paper's signal in the first place.
  if (opts.proxyMode) return 'PROXY ONLY';
  if (opts.noTarget) return 'NO PAPER TARGET';
  if (alpha == null) return 'UNKNOWN';
  if (alpha < 0) return 'UNDER WATER';
  if (tstat != null && Math.abs(tstat) < 1.5) return 'NOT TRADEABLE AT SCALE';
  if (alpha < 0.005 || confidence === 'low') return 'BORDERLINE';
  return 'TRADEABLE';
}

// Inspect a backtest's data_quality_flags to decide whether the engine
// ran the paper's signal or a proxy. Only the engine's own kind-fallback
// flag prefix counts — survivorship-bias and clipped-extremes flags do
// not change what was tested, only how reliable the test was.
function detectProxyMode(bt) {
  if (!bt || !bt.data_quality_flags) return false;
  return bt.data_quality_flags.some((f) =>
    typeof f === 'string' && f.startsWith('engine fallback: signal.kind=')
  );
}

// Render the Ken French factor-comparison banner at the top of the
// Backtest tab. Honest external check: did the published KF factor
// realize the paper's claim over the paper's window?
function renderFactorCompareBanner(root, fc) {
  const verdictMeta = {
    supported:       { sev: 'ok',   label: 'Supported by KF factor',          icon: 'verified' },
    directional:     { sev: 'warn', label: 'Same direction, magnitude differs', icon: 'compare_arrows' },
    falsified:       { sev: 'fail', label: 'Falsified by KF factor',          icon: 'block' },
    out_of_range:    { sev: 'info', label: 'Window outside KF coverage',      icon: 'event_busy' },
    no_factor_match: { sev: 'info', label: 'No KF factor mapping',            icon: 'help_outline' },
  };
  const meta = verdictMeta[fc.verdict] || verdictMeta.no_factor_match;
  const banner = el('div', { class: `flag-banner sev-${meta.sev}` });
  banner.appendChild(el('div', { class: 'head' }, [
    el('span', { class: 'material-symbols-outlined', style: 'font-size:16px;vertical-align:-3px;margin-right:6px;' }, meta.icon),
    `Ken French factor comparison — ${meta.label}`,
  ]));
  banner.appendChild(el('div', { class: 'banner-headline' }, fc.verdict_summary));
  if (fc.realized_monthly_return != null) {
    const grid = el('div', { class: 'window-grid' });
    grid.appendChild(el('div', { class: 'window-cell' }, [
      el('span', { class: 'lbl' }, 'Paper claimed'),
      el('span', { class: 'val mono' }, fc.claimed_monthly_return != null
        ? `${(fc.claimed_monthly_return * 100).toFixed(3)}%/mo` + (fc.claimed_tstat != null ? `  t=${fc.claimed_tstat.toFixed(2)}` : '')
        : '—'),
    ]));
    grid.appendChild(el('div', { class: 'window-cell hi' }, [
      el('span', { class: 'lbl' }, `KF ${fc.factor_name} realized`),
      el('span', { class: 'val mono' }, `${(fc.realized_monthly_return * 100).toFixed(3)}%/mo  t=${fc.realized_tstat.toFixed(2)}`),
    ]));
    grid.appendChild(el('div', { class: 'window-cell' }, [
      el('span', { class: 'lbl' }, 'Window used'),
      el('span', { class: 'val mono' }, `${fc.used_start ?? '—'} → ${fc.used_end ?? '—'} (n=${fc.n_months})`),
    ]));
    banner.appendChild(grid);
  }
  banner.appendChild(el('div', { class: 'banner-foot' },
    `Source: ${fc.factor_source}. This is a falsification test against the published factor — not a stock-level replication. Sharpe (ann.) of the KF factor in this window: ${fc.realized_sharpe_annualized != null ? fc.realized_sharpe_annualized.toFixed(2) : '—'}.`
  ));
  root.appendChild(banner);
}

function detectNoTarget(claim) {
  if (!claim) return true;
  if (claim.monthly_return == null) return true;
  // The "impossible zero with non-null tstat" shape (extraction_verifier
  // already drops these to None server-side, but stale caches and the
  // legacy demo bundle can still surface them).
  if (claim.monthly_return === 0 && claim.tstat != null && claim.tstat !== 0) return true;
  return false;
}

// Render the confidence + signal-type subline as two color-coded pills
// instead of crowded "confidence: medium · not_evaluated" prose. Each
// pill carries a tooltip explaining what the field means so users
// hovering can learn the vocabulary.
//   confidence — D3's confidence in its implementability verdict
//                (low / medium / high). Drives color: red / gold / green.
//   signal_type — D3's classification of the strategy's edge
//                (alpha_signal, beta_proxy, microstructure, structural,
//                 stale, not_evaluated). When `not_evaluated` (proxy run,
//                 no claim, or D3 declined to classify) we hide the pill
//                 entirely — it's noise, not information.
function renderConfidencePills(confidence, sigType) {
  const c = (confidence || '').toLowerCase();
  const confColor = c === 'high' ? 'good' : c === 'medium' ? 'warn' : c === 'low' ? 'bad' : 'mute';
  const confLabel = confidence || '—';
  const confPill = `<span class="vs-pill vs-pill-${confColor}" title="D3 confidence in the implementability verdict (low / medium / high). Driven by sample size, surviving stress tests, and gap-attribution clarity.">conf · ${escapeHtml(confLabel)}</span>`;
  // Hide signal-type when D3 didn't evaluate it.
  if (!sigType || sigType === 'not_evaluated' || sigType === '—') {
    return confPill;
  }
  const sigLabel = signalTypeLabel(sigType);
  const sigPill = `<span class="vs-pill vs-pill-info" title="D3's classification of the strategy's edge: where the return comes from. alpha_signal = genuine mispricing; beta_proxy = factor exposure; microstructure = trading-cost / reversal effect; structural = risk premium; stale = no longer works.">${escapeHtml(sigLabel)}</span>`;
  return confPill + sigPill;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[c]);
}

// Statistical-test papers (Lo-MacKinlay 1988, Poterba-Summers 1988, …)
// report variance-ratio / autocorrelation statistics, not a tradeable
// monthly long-short return. When A1 returns headline_claim=null AND the
// extracted spec uses signal.kind='variance_ratio', the user CAN'T enter a
// "paper monthly L/S return" override — the paper genuinely doesn't have
// one. Surface that explicitly instead of telling the user to enter a
// number that doesn't exist.
function detectStatisticalOnlyPaper(bundle) {
  try {
    const kind = bundle?.verified_spec?.spec?.signal?.kind;
    return kind === 'variance_ratio';
  } catch (_) { return false; }
}

function setCostBps(bps, originator) {
  const p = interpCostCurve(bps);
  if (!p) return;

  // 1. Detail slider readouts (Robustness tab)
  if ($('#cost-bps-readout')) $('#cost-bps-readout').textContent = bps.toFixed(1);
  const ret = $('#cost-ret-readout');
  if (ret) {
    ret.textContent = fmtPctSigned(p.mean_return, 3) + '/mo';
    ret.className = 'cost-readout-value ' + (p.mean_return >= 0 ? 'alpha-pos' : 'alpha-neg');
  }
  const ts = $('#cost-tstat-readout');
  if (ts) ts.textContent = (p.tstat == null) ? '—' : (p.tstat >= 0 ? '+' : '') + p.tstat.toFixed(2);
  const sv = $('#cost-surv-readout');
  if (sv) {
    sv.textContent = p.surviving ? 'YES' : 'NO';
    sv.className = 'cost-readout-value ' + (p.surviving ? 'surv-yes' : 'surv-no');
  }

  // 2. Top-level quick-controls readout
  const qcVal = $('#qc-bps-value');
  if (qcVal) qcVal.textContent = bps.toFixed(0);

  // 3. Verdict strip — IMPLEMENTABLE ALPHA reflects the slider position
  const conf = phase5.report?.headline?.verdict?.confidence;
  const newLabel = tradeableLabel(p.mean_return, p.tstat, conf);
  const impl = $('#vs-impl-value');
  if (impl) impl.textContent = fmtPctSigned(p.mean_return, 3) + '/mo';
  const implT = $('#vs-impl-tstat');
  if (implT) implT.textContent = (p.tstat == null) ? 't = —' : `t = ${p.tstat >= 0 ? '+' : ''}${p.tstat.toFixed(2)}`;
  const tag = $('#vs-tag');
  if (tag) tag.textContent = newLabel;
  const verCol = $('#vs-col-verdict');
  if (verCol) verCol.setAttribute('data-tag', newLabel);
  const badge = $('#verdict-badge');
  if (badge) {
    badge.textContent = `Verdict: ${newLabel}`;
    badge.setAttribute('data-tag', newLabel);
  }

  // 4. Sync the OTHER slider's position (avoid feedback loop)
  if (originator !== 'detail') {
    const detail = $('#cost-slider');
    if (detail && parseFloat(detail.value) !== bps) detail.value = String(bps);
  }
  if (originator !== 'top') {
    const qc = $('#qc-cost-slider');
    if (qc && parseFloat(qc.value) !== bps) qc.value = String(bps);
  }
}

// Generic table renderer for stress test rows
function renderRowTable(elementId, rows, columns) {
  const root = $('#' + elementId);
  if (!root) return;
  root.innerHTML = '';
  if (!rows || rows.length === 0) {
    root.appendChild(emptyState('table_view', 'No rows.'));
    return;
  }
  const t = el('table', { class: 'mut-table' });
  t.appendChild(el('thead', {}, el('tr', {},
    columns.map(([h]) => el('th', {}, h))
  )));
  const tb = el('tbody', {});
  rows.forEach((p) => {
    tb.appendChild(el('tr', {},
      columns.map(([_, fn]) => el('td', { class: 'num' }, fn(p)))
    ));
  });
  t.appendChild(tb);
  root.appendChild(t);
}

// ----- DBT panel -----

function renderDbtPanel(report) {
  const g = report.generalization;
  if (!g) return;
  $('#dbt-paper-title').textContent = g.paper_title;
  $('#dbt-arch-summary').textContent = g.architectural_test_summary || '';

  const body = $('#dbt-panel-body');
  body.innerHTML = '';
  if (g.architectural_test_passed) {
    body.appendChild(el('span', { class: 'dbt-arch-pass' }, '✓ Architectural test passed'));
  }
  body.appendChild(el('table', {}, el('tbody', {}, [
    ['paper sample (declared)', `${g.extracted_lookback_months}m / ${g.extracted_holding_months}m hold`],
    ['extracted direction', g.extracted_signal_direction],
    ['A2 confidence', g.a2_confidence],
    ['A3 high-severity', String(g.a3_n_high)],
    ['B1/B2 fidelity', g.b1_b2_overall_fidelity],
    ['engine baseline', `${fmtPctSigned(g.engine_mean_return, 3)}/mo · ${fmtTstat(g.engine_tstat)}`],
    ['D1 verdict', g.d1_verdict],
    ['D2 primary cause', g.d2_primary_cause],
    ['D2 cause kind', g.d2_primary_cause_kind],
    ['D2 residual |gap|', fmtPctSigned(g.d2_residual_abs_gap, 3)],
    ['D3 implementable α', fmtPctSigned(g.d3_implementable_alpha, 3) + '/mo'],
    ['D3 gap attribution', (g.d3_gap_attribution || '').replace(/_/g, ' ')],
    ['D3 confidence', g.d3_confidence],
  ].map(([k, v]) => el('tr', {}, [
    el('th', {}, k),
    el('td', { class: 'num' }, v ?? '—'),
  ])))));
}

function setupDbtPanelToggle() {
  const btn = $('#vs-dbt-toggle');
  const panel = $('#dbt-panel');
  if (!btn || !panel) return;
  btn.addEventListener('click', () => { panel.hidden = !panel.hidden; });
  $('#dbt-panel-close')?.addEventListener('click', () => { panel.hidden = true; });
}

// ============================================================
// Pipeline stepper — visualizes A1→A2→A3→B1/B2→Engine→D2→Battery→D3
// ============================================================

const PIPELINE_STAGES = ['parse', 'a1', 'a2', 'a3', 'b', 'engine', 'd2', 'battery', 'd3'];

function setStage(stage, state) {
  const el = document.querySelector(`.ps-step[data-stage="${stage}"]`);
  if (!el) return;
  el.setAttribute('data-state', state);
  // Enable the button as soon as the stage finishes (done or failed) so
  // the user can click it to revisit that stage's report. Pending /
  // active steps stay disabled.
  if (state === 'done' || state === 'failed') {
    el.removeAttribute('disabled');
  } else {
    el.setAttribute('disabled', '');
  }
}

function resetStepper() {
  const bar = $('#pipeline-stepper');
  if (!bar) return;
  PIPELINE_STAGES.forEach((s) => setStage(s, 'pending'));
}

// No-op kept for callsite compatibility — the user wants the stepper to
// stay fully visible after the pipeline completes so each stage's box
// remains a clickable shortcut to its report. Previously this collapsed
// the bar into a single "Pipeline complete" pill.
function collapseStepper(_elapsedMs) {
  // intentionally empty
}

// Demo path: animate through stages with synthetic timing so the user sees
// the pipeline visually executed, even though the underlying call is cached.
async function animateStepperForDemo(totalMs = 1600) {
  resetStepper();
  const t0 = performance.now();
  const stepMs = totalMs / PIPELINE_STAGES.length;
  for (const s of PIPELINE_STAGES) {
    setStage(s, 'active');
    await new Promise((res) => setTimeout(res, stepMs));
    setStage(s, 'done');
  }
  collapseStepper(performance.now() - t0);
}

// Real-upload path: poll /api/pipeline/status/{job_id} until terminal
async function trackPipelineJob(jobId) {
  resetStepper();
  const t0 = performance.now();
  const seen = new Set();
  let last = null;
  let lastBundleKeys = '';  // progressive-reveal: track which stages are present
  while (true) {
    let payload;
    try {
      const r = await fetch(`/api/pipeline/status/${encodeURIComponent(jobId)}`);
      if (!r.ok) break;
      payload = await r.json();
    } catch (e) {
      log('SYS', `pipeline status fetch failed: ${e.message}`, 'err');
      break;
    }
    const stage = payload.stage;
    const status = payload.status;
    // Mark all stages up to (but not including) the current one as done
    if (stage && !seen.has(stage)) {
      const idx = PIPELINE_STAGES.indexOf(stage);
      for (let i = 0; i < idx; i++) setStage(PIPELINE_STAGES[i], 'done');
      seen.add(stage);
      last = stage;
      setStage(stage, status === 'failed' ? 'failed' : 'active');
    } else if (stage && stage === last) {
      setStage(stage, status === 'failed' ? 'failed' : 'active');
    }
    // Progressive reveal: re-render the bundle whenever a new stage's payload
    // appears in result. Cheap key-set comparison avoids redundant DOM rebuilds.
    if (payload.result) {
      const keys = Object.keys(payload.result).sort().join(',');
      if (keys !== lastBundleKeys) {
        lastBundleKeys = keys;
        applyBundle(payload.result);
        log('SYS', `Stage data ready: ${keys}`, 'sys');
      }
    }
    if (payload.done) {
      // Mark all stages done (or failed if signal)
      PIPELINE_STAGES.forEach((s) => {
        const cur = document.querySelector(`.ps-step[data-stage="${s}"]`);
        if (cur && cur.getAttribute('data-state') !== 'failed') setStage(s, 'done');
      });
      collapseStepper(performance.now() - t0);
      return payload;
    }
    await new Promise((res) => setTimeout(res, 250));
  }
  collapseStepper(performance.now() - t0);
}

// Hook the demo button to also animate the stepper
function wireDemoButtonStepperHook() {
  const btn = $('#demo-btn');
  if (!btn) return;
  btn.addEventListener('click', () => { animateStepperForDemo(); }, { capture: true });
}

// ----- bootstrap -----

setupDbtPanelToggle();
setupVerdictWhy();
wireDemoButtonStepperHook();
// Blank the strip with dash placeholders BEFORE the silent report fetch
// kicks off — guarantees a fresh refresh never momentarily flashes the
// previous JT numbers.
resetVerdictStrip();
// Fetch the report in the background but do NOT auto-render the strip.
// The user explicitly opts in via "Load briefing" or by uploading a paper.
loadPhase5Report({ render: false });
