// Paper Replication Machine — MVP UI controller.
// Fetches /api/demo or runs /api/extract on uploaded PDFs, then renders the
// bundle across Overview / Spec / Verification / Critique / Backtest / Provenance.

const state = {
  bundle: null,
  activeTab: 'overview',
};

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
  $('#spec-body').innerHTML = '';
  $('#verification-body').innerHTML = '';
  $('#critique-body').innerHTML = '';
  $('#backtest-body').innerHTML = '';
  $('#provenance-body').innerHTML = '';
  resetKPIs();
  $('#paper-title').textContent = 'No paper';
  $('#ingest-chip').innerHTML = '<span class="dot amber"></span>Awaiting upload';
  $('#metric-chip').innerHTML = '<span class="dot gray"></span>No run';
  $('#log').innerHTML = '';
  setStatus('Idle — no paper loaded', 'gray');
  setVerdict(null);
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
  log('A1', 'Calling /api/extract (Opus + Haiku, cached on disk)…', 'a1');
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
    log('A1', `Extracted spec. Verification → ${body.verified_spec.report.overall_confidence}.`, 'a1');
    log('A2', `${body.verified_spec.report.n_checks} quote checks; ${body.verified_spec.report.n_failed_high} high-severity failures.`, 'a2');
    applyBundle({
      paper_id: body.paper_id,
      paper_title: body.paper_title,
      verified_spec: body.verified_spec,
      critique: null,
      backtest: null,
      paper_claim: null,
    });
  } catch (e) {
    log('SYS', `ERROR: ${e.message}`, 'sys');
    setStatus('Error', 'red');
  }
}

// ---------- papers list ----------

async function refreshPapers() {
  try {
    const r = await fetch('/api/papers');
    const body = await r.json();
    const wrap = $('#papers-list');
    wrap.innerHTML = '';
    body.papers.forEach((p) => {
      const row = el('div', { class: 'paper-row' }, [
        el('span', {}, `${p.paper_id}.pdf · ${(p.size_bytes / 1024).toFixed(1)} KB`),
        el('div', { class: 'actions' }, [
          el('button', {
            onclick: () => runExtraction(p.paper_id),
          }, 'Run'),
        ]),
      ]);
      wrap.appendChild(row);
    });
  } catch (e) {
    /* ignore */
  }
}

// ---------- bundle render ----------

function applyBundle(bundle) {
  state.bundle = bundle;
  $('#paper-title').textContent = bundle.paper_title || bundle.paper_id || 'Paper';
  if (bundle.verified_spec) setVerdict(bundle.verified_spec.report);
  renderSpec(bundle.verified_spec);
  renderVerification(bundle.verified_spec);
  renderCritique(bundle.critique);
  renderBacktest(bundle.backtest, bundle.paper_claim);
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
  if (!verified) {
    root.appendChild(emptyState('no_sim', 'No spec loaded. Run extraction or load the demo bundle.'));
    return;
  }
  const spec = verified.spec;

  root.appendChild(specBlock('Header', {
    'paper_id': spec.paper_id,
    'paper_title': spec.paper_title,
    'window': `${fmtDate(spec.start_date)} → ${fmtDate(spec.end_date)}`,
    'base_currency': spec.base_currency,
    'notes': spec.notes || '—',
  }));

  root.appendChild(specBlock('Universe', {
    'name': spec.universe.name,
    'region': spec.universe.region,
    'asset_class': spec.universe.asset_class,
    'min_price': spec.universe.min_price ?? '—',
    'exchanges': (spec.universe.exchanges || []).join(', ') || '—',
  }, spec.universe.supporting_quote));

  root.appendChild(specBlock('Signal', {
    'name': spec.signal.name,
    'kind': spec.signal.kind,
    'formula': spec.signal.formula,
    'lookback_months': spec.signal.lookback_months ?? '—',
    'skip_months': spec.signal.skip_months,
    'direction': spec.signal.direction,
    'frequency': spec.signal.frequency,
  }, spec.signal.supporting_quote));

  root.appendChild(specBlock('Portfolio', {
    'construction': spec.portfolio.construction,
    'n_buckets': spec.portfolio.n_buckets,
    'long_bucket': spec.portfolio.long_bucket,
    'short_bucket': spec.portfolio.short_bucket ?? '—',
    'weighting': spec.portfolio.weighting,
    'long_short': spec.portfolio.long_short ? 'yes' : 'no',
    'gross_exposure': spec.portfolio.gross_exposure,
  }, spec.portfolio.supporting_quote));

  root.appendChild(specBlock('Rebalance', {
    'frequency': spec.rebalance.frequency,
    'execution_lag_days': spec.rebalance.execution_lag_days,
    'holding_period_months': spec.rebalance.holding_period_months ?? '—',
    'signal_date_convention': spec.rebalance.signal_date_convention,
    'execution_date_convention': spec.rebalance.execution_date_convention,
  }, spec.rebalance.supporting_quote));

  // Ambiguities
  if (spec.ambiguities && spec.ambiguities.length) {
    const head = el('div', { class: 'spec-section-head' }, [
      el('h3', {}, `Ambiguities (${spec.ambiguities.length})`),
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
    spec.ambiguities.forEach((f) => {
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
  Object.entries(fields).forEach(([k, v]) => {
    dl.appendChild(el('dt', {}, k));
    dl.appendChild(el('dd', {}, String(v)));
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
    ['window', `${fmtDate(bt.start_date)} → ${fmtDate(bt.end_date)}`],
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

  // Equity curve
  const chart = el('div', { class: 'spec-section' });
  chart.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'Cumulative return (long–short)')]));
  const wrap = el('div', { class: 'chart-wrap' });
  wrap.appendChild(buildEquitySVG(bt.returns));
  chart.appendChild(wrap);
  root.appendChild(chart);

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

function buildEquitySVG(returns) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 800 260');
  svg.setAttribute('preserveAspectRatio', 'none');
  if (!returns || !returns.length) return svg;

  // Cumulative sum
  const cum = [];
  let s = 0;
  returns.forEach((r) => { s += r.ret; cum.push(s); });
  const n = cum.length;
  const max = Math.max(...cum);
  const min = Math.min(...cum, 0);
  const w = 800;
  const h = 260;
  const pad = 24;
  const x = (i) => pad + ((w - pad * 2) * i) / (n - 1);
  const y = (v) => pad + (h - pad * 2) * (1 - (v - min) / (max - min || 1));

  // Zero line
  const zero = document.createElementNS(ns, 'line');
  zero.setAttribute('x1', pad);
  zero.setAttribute('x2', w - pad);
  zero.setAttribute('y1', y(0));
  zero.setAttribute('y2', y(0));
  zero.setAttribute('stroke', '#414755');
  zero.setAttribute('stroke-dasharray', '2 4');
  svg.appendChild(zero);

  // Area path
  const areaPoints = cum.map((v, i) => `${x(i)},${y(v)}`).join(' L ');
  const areaD = `M ${pad},${y(0)} L ${areaPoints} L ${w - pad},${y(0)} Z`;
  const area = document.createElementNS(ns, 'path');
  area.setAttribute('d', areaD);
  area.setAttribute('fill', 'rgba(111, 221, 120, 0.1)');
  svg.appendChild(area);

  // Line path
  const lineD = `M ${cum.map((v, i) => `${x(i)},${y(v)}`).join(' L ')}`;
  const line = document.createElementNS(ns, 'path');
  line.setAttribute('d', lineD);
  line.setAttribute('fill', 'none');
  line.setAttribute('stroke', '#6fdd78');
  line.setAttribute('stroke-width', '1.4');
  svg.appendChild(line);

  // Start / end labels
  const startLbl = document.createElementNS(ns, 'text');
  startLbl.setAttribute('x', pad);
  startLbl.setAttribute('y', h - 4);
  startLbl.setAttribute('fill', '#8b90a0');
  startLbl.setAttribute('font-family', 'JetBrains Mono');
  startLbl.setAttribute('font-size', '10');
  startLbl.textContent = returns[0].period_end;
  svg.appendChild(startLbl);

  const endLbl = document.createElementNS(ns, 'text');
  endLbl.setAttribute('x', w - pad);
  endLbl.setAttribute('y', h - 4);
  endLbl.setAttribute('fill', '#8b90a0');
  endLbl.setAttribute('font-family', 'JetBrains Mono');
  endLbl.setAttribute('font-size', '10');
  endLbl.setAttribute('text-anchor', 'end');
  endLbl.textContent = returns[returns.length - 1].period_end;
  svg.appendChild(endLbl);

  const maxLbl = document.createElementNS(ns, 'text');
  maxLbl.setAttribute('x', pad);
  maxLbl.setAttribute('y', y(max) - 4);
  maxLbl.setAttribute('fill', '#6fdd78');
  maxLbl.setAttribute('font-family', 'JetBrains Mono');
  maxLbl.setAttribute('font-size', '10');
  maxLbl.textContent = `peak ${(max * 100).toFixed(1)}%`;
  svg.appendChild(maxLbl);

  return svg;
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

refreshPapers();
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

async function loadPhase5Report() {
  try {
    const r = await fetch('/api/report');
    if (!r.ok) {
      log('SYS', 'No Phase 5 report available (run pipelines first).', 'sys');
      return null;
    }
    const data = await r.json();
    phase5.report = data;
    renderVerdictStrip(data);
    renderDiagnosisTab(data);
    renderRobustnessTab(data);
    renderDbtPanel(data);
    log('SYS', `Phase 5 report loaded: ${data.headline?.paper_id ?? '—'}`, 'sys');
    return data;
  } catch (e) {
    log('SYS', `Failed to load /api/report: ${e.message}`, 'err');
    return null;
  }
}

// ----- Verdict strip -----

function renderVerdictStrip(report) {
  const h = report.headline;
  if (!h) return;
  const strip = $('#verdict-strip');
  strip.hidden = false;

  $('#vs-paper-title').textContent = h.paper_title;
  $('#vs-confidence').textContent =
    `confidence: ${h.verdict.confidence} · ${h.verdict.signal_type}`;

  // Left column — paper claim
  const c = h.claim;
  $('#vs-claim-value').textContent = fmtPctSigned(c.value, 3) + '/mo';
  $('#vs-claim-tstat').textContent = fmtTstat(c.tstat);
  $('#vs-claim-loc').textContent = c.paper_location || '';

  // Right column — implementable verdict
  const v = h.verdict;
  $('#vs-impl-value').textContent = fmtPctSigned(v.implementable_alpha, 3) + '/mo';
  $('#vs-impl-tstat').textContent = fmtTstat(v.tstat_estimate);
  $('#vs-tag').textContent = v.tradeable_label || '—';
  $('#vs-col-verdict').setAttribute('data-tag', v.tradeable_label || '');

  // Window row
  $('#vs-paper-window').textContent = h.paper_window || h.sample_paper || '—';
  $('#vs-engine-window').textContent = h.engine_window || h.sample_engine || '—';
  $('#vs-gap-attr').textContent = (v.gap_attribution || '—').replace(/_/g, ' ');

  // Summary line
  $('#vs-summary').textContent = v.summary_first_clause || '';

  // DBT toggle
  if (report.generalization) {
    $('#vs-dbt-toggle').hidden = false;
    const passed = report.generalization.architectural_test_passed;
    $('#vs-dbt-label').innerHTML = `Generalization (DBT 1985) ${passed ? '✓' : '✗'}`;
  }

  // Top-right verdict badge in topnav (color-coded via [data-tag])
  const badge = $('#verdict-badge');
  badge.textContent = `Verdict: ${v.tradeable_label}`;
  badge.setAttribute('data-tag', v.tradeable_label || '');
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

function renderCostSlider() {
  const slider = $('#cost-slider');
  const ticks = $('#cost-slider-ticks');
  if (phase5.costCurve.length === 0) return;
  const xs = phase5.costCurve.map((p) => p.bps);
  slider.min = String(Math.min(...xs));
  slider.max = String(Math.max(...xs));
  slider.step = '0.5';
  slider.value = String(xs[0]);

  ticks.innerHTML = '';
  xs.forEach((x) => ticks.appendChild(el('span', {}, String(x.toFixed(0)))));

  const update = () => updateCostReadout(parseFloat(slider.value));
  slider.oninput = update;
  update();
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

function updateCostReadout(bps) {
  const p = interpCostCurve(bps);
  if (!p) return;
  $('#cost-bps-readout').textContent = `${bps.toFixed(1)}`;
  const ret = $('#cost-ret-readout');
  ret.textContent = fmtPctSigned(p.mean_return, 3) + '/mo';
  ret.className = 'cost-readout-value ' + (p.mean_return >= 0 ? 'alpha-pos' : 'alpha-neg');
  $('#cost-tstat-readout').textContent = (p.tstat == null) ? '—' : (p.tstat >= 0 ? '+' : '') + p.tstat.toFixed(2);
  const s = $('#cost-surv-readout');
  s.textContent = p.surviving ? 'YES' : 'NO';
  s.className = 'cost-readout-value ' + (p.surviving ? 'surv-yes' : 'surv-no');
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

// ----- bootstrap -----

setupDbtPanelToggle();
loadPhase5Report();
