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
  if (!report) { el.textContent = 'VERDICT: —'; return; }
  const conf = report.overall_confidence.toUpperCase();
  el.textContent = `VERDICT: ${conf}`;
  el.style.color = conf === 'HIGH' ? 'var(--secondary)' : conf === 'MEDIUM' ? 'var(--tertiary)' : 'var(--error)';
}

$('#demo-btn').addEventListener('click', async () => {
  log('SYS', 'Loading demo bundle from /api/demo…', 'sys');
  setStatus('LOADING · demo bundle', 'amber');
  try {
    const resp = await fetch('/api/demo');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const bundle = await resp.json();
    applyBundle(bundle);
    log('SYS', `Demo bundle loaded (paper_id=${bundle.paper_id}).`, 'sys');
  } catch (e) {
    log('SYS', `ERROR: ${e.message}`, 'sys');
    setStatus('ERROR', 'red');
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
  $('#paper-title').textContent = 'NO PAPER';
  $('#ingest-chip').innerHTML = '<span class="dot amber"></span>AWAITING INGESTION';
  $('#metric-chip').innerHTML = '<span class="dot gray"></span>NO RUN';
  $('#log').innerHTML = '';
  setStatus('IDLE · no paper loaded', 'gray');
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
  setStatus('UPLOADING · PDF', 'amber');
  const form = new FormData();
  form.append('file', file);
  try {
    const up = await fetch('/api/papers', { method: 'POST', body: form });
    if (!up.ok) throw new Error(`upload HTTP ${up.status}`);
    const paper = await up.json();
    log('A1', `Paper ${paper.paper_id} stored. Kicking off extraction…`, 'a1');
    $('#paper-title').textContent = paper.paper_id.toUpperCase();
    $('#ingest-chip').innerHTML = '<span class="dot green pulse"></span>INGESTED';
    await runExtraction(paper.paper_id);
  } catch (e) {
    log('SYS', `ERROR: ${e.message}`, 'sys');
    setStatus('ERROR', 'red');
  }
  refreshPapers();
}

async function runExtraction(paperId) {
  setStatus('RUNNING · A1 + A2', 'amber');
  try {
    const r = await fetch('/api/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paper_id: paperId }),
    });
    const body = await r.json();
    if (!r.ok) {
      log('A1', `FAILED: ${body.error || 'unknown'}`, 'a1');
      log('SYS', body.hint || 'Check server logs.', 'sys');
      setStatus('FAILED · extraction', 'red');
      return;
    }
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
    setStatus('ERROR', 'red');
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
          }, 'RUN'),
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
  $('#paper-title').textContent = (bundle.paper_title || bundle.paper_id || 'PAPER').toUpperCase();
  if (bundle.verified_spec) setVerdict(bundle.verified_spec.report);
  renderSpec(bundle.verified_spec);
  renderVerification(bundle.verified_spec);
  renderCritique(bundle.critique);
  renderBacktest(bundle.backtest, bundle.paper_claim);
  renderProvenance(bundle.backtest);
  renderKPIs(bundle.backtest);
  setStatus('READY · bundle loaded', 'green');
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
  $('#metric-chip').innerHTML = '<span class="dot green"></span>RUN COMPLETE';
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
      el('h3', {}, `AMBIGUITIES (${spec.ambiguities.length})`),
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
        el('td', {}, [el('span', { class: `pill ${sevClass}` }, f.sensitivity_priority.toUpperCase())]),
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
    el('h3', {}, title.toUpperCase()),
  ]));
  const dl = el('dl', { class: 'spec-section-body' });
  Object.entries(fields).forEach(([k, v]) => {
    dl.appendChild(el('dt', {}, k));
    dl.appendChild(el('dd', {}, String(v)));
  });
  section.appendChild(dl);
  if (supportingQuote) {
    section.appendChild(el('div', { class: 'quote' }, [
      el('span', { class: 'meta' }, `SUPPORTING QUOTE · page ${supportingQuote.page} · confidence ${fmtNum(supportingQuote.match_confidence, 2)} · ${supportingQuote.verified ? 'VERIFIED' : 'UNVERIFIED'}`),
      document.createTextNode('“' + supportingQuote.text + '”'),
    ]));
  }
  return section;
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
    el('h3', {}, 'A2 SUMMARY'),
    el('span', { class: 'pill ' + (r.overall_confidence === 'high' ? 'ok' : r.overall_confidence === 'medium' ? 'warn' : 'fail') }, r.overall_confidence.toUpperCase()),
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
    el('h3', {}, 'QUOTE CHECKS'),
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
      el('td', {}, [el('span', { class: `pill ${sevClass}` }, c.severity.toUpperCase())]),
      el('td', {}, c.verification_status),
      el('td', { class: 'num' }, c.verified_page ?? '—'),
      el('td', { class: 'num' }, fmtNum(c.verification_confidence, 2)),
      el('td', {}, c.support_check ? [el('span', { class: `pill ${supportClass}` }, c.support_check.supports.toUpperCase())] : '—'),
      el('td', {}, [el('span', { class: 'pill ' + (c.failed ? 'fail' : 'ok') }, c.failed ? 'FAIL' : 'PASS')]),
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
      el('h3', {}, `${c.field_path.toUpperCase()} · ${c.support_check.supports.toUpperCase()}`),
    ]));
    card.appendChild(el('div', { class: 'quote' }, [
      el('span', { class: 'meta' }, `HAIKU JUDGMENT`),
      document.createTextNode(c.support_check.reason),
    ]));
    card.appendChild(el('div', { class: 'quote' }, [
      el('span', { class: 'meta' }, `QUOTE · page ${c.quote.page}`),
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
        el('span', { class: `pill ${c.severity === 'high' ? 'fail' : c.severity === 'medium' ? 'warn' : 'info'}` }, c.severity.toUpperCase()),
        el('span', { class: 'pill info' }, c.category.toUpperCase().replace('_', ' ')),
        el('h4', {}, `CRITICISM ${i + 1}`),
      ]),
      el('p', { class: 'desc' }, c.description),
      el('div', { class: 'remedy' }, c.proposed_remediation),
    ]);
    if (c.evidence_quote) {
      card.appendChild(el('div', { class: 'quote' }, [
        el('span', { class: 'meta' }, `EVIDENCE · page ${c.evidence_quote.page}`),
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
    banner.appendChild(el('div', { class: 'head' }, `DATA QUALITY FLAGS (${bt.data_quality_flags.length})`));
    bt.data_quality_flags.forEach((f) => banner.appendChild(el('div', {}, `• ${f}`)));
    root.appendChild(banner);
  }

  // Comparison
  if (paperClaim) {
    const block = el('div', { class: 'spec-section' });
    block.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'PAPER CLAIM vs REPLICATION')]));
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
  m.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'ENGINE METRICS')]));
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
  chart.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, 'CUMULATIVE RETURN (LONG-SHORT)')]));
  const wrap = el('div', { class: 'chart-wrap' });
  wrap.appendChild(buildEquitySVG(bt.returns));
  chart.appendChild(wrap);
  root.appendChild(chart);

  // Recent returns table (last 24)
  if (bt.returns && bt.returns.length) {
    const tbl = el('div', { class: 'spec-section' });
    tbl.appendChild(el('div', { class: 'spec-section-head' }, [el('h3', {}, `RETURN OBSERVATIONS · LAST 24 OF ${bt.returns.length}`)]));
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
    el('div', { class: 'tier' }, `LEVEL 2 · ${engine.source_tier.toUpperCase()}`),
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
    el('div', { class: 'tier' }, `LEVEL 1 · PRIMARY`),
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
log('SYS', 'UI ready. Click LOAD DEMO or drop a PDF.', 'sys');
setStatus('IDLE · no paper loaded', 'gray');
