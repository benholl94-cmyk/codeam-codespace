// vanilla JS — mirrors ai-assistance.js
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}

async function fetchJSON(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return await r.json();
}

function fmtNum(n) {
  if (n == null) return '…';
  return Number(n).toLocaleString();
}

function statusPill(s) {
  const p = el('span', { class: `ft-pill ${s || 'started'}` }, s || 'started');
  return p;
}

function fmtMetrics(metrics) {
  if (!metrics) return '';
  return el('div', { class: 'ft-metrics' },
    ...Object.entries(metrics).map(([k, v]) =>
      el('span', { class: 'ft-metric' }, `${k}=${typeof v === 'number' ? v.toFixed(3) : v}`)
    ));
}

async function loadStats() {
  try {
    const s = await fetchJSON('/api/finetuning/stats');
    $('#ft-datasets-count').textContent = fmtNum(s.datasets);
    $('#ft-adapters-count').textContent = fmtNum(s.adapters);
    $('#ft-promoted-count').textContent = fmtNum(s.promoted);
    $('#ft-runs-count').textContent = fmtNum(s.runs);
    $('#ft-status-pill').textContent = 'healthy';
    $('#ft-status-pill').className = 'pill pill-ok';
  } catch (e) {
    $('#ft-status-pill').textContent = 'unreachable';
    $('#ft-status-pill').className = 'pill pill-err';
  }
}

async function loadDatasets() {
  try {
    const r = await fetchJSON('/api/finetuning/datasets');
    const tbody = $('#ft-datasets-table tbody');
    tbody.innerHTML = '';
    for (const d of r.datasets || []) {
      const tr = el('tr');
      tr.appendChild(el('td', {}, d.dataset_id));
      tr.appendChild(el('td', {}, d.name));
      tr.appendChild(el('td', {}, d.format));
      tr.appendChild(el('td', {}, String(d.n_samples)));
      tr.appendChild(el('td', {}, String(d.train_size)));
      tr.appendChild(el('td', {}, String(d.val_size)));
      tr.appendChild(el('td', {}, (d.content_sha256 || '').slice(0, 12) + '…'));
      tbody.appendChild(tr);
    }
  } catch (e) { /* swallow */ }
}

async function loadAdapters() {
  try {
    const r = await fetchJSON('/api/finetuning/adapters');
    const tbody = $('#ft-adapters-table tbody');
    tbody.innerHTML = '';
    for (const a of r.adapters || []) {
      const tr = el('tr');
      tr.appendChild(el('td', {}, a.adapter_id));
      tr.appendChild(el('td', {}, a.base_model_id || '?'));
      tr.appendChild(el('td', {}, a.recipe_name));
      tr.appendChild(el('td', {}, a.backend));
      const stTd = el('td', {});
      stTd.appendChild(statusPill(a.status));
      tr.appendChild(stTd);
      const evalTd = el('td', {});
      evalTd.appendChild(fmtMetrics(a.eval_metrics));
      tr.appendChild(evalTd);
      const actTd = el('td', {});
      const promoteBtn = el('button', {
        class: 'ft-pill', title: 'promote as routable model'
      }, 'promote');
      promoteBtn.addEventListener('click', async () => {
        try {
          await fetchJSON(`/api/finetuning/adapters/${a.adapter_id}/promote`, { method: 'POST' });
          await loadAll();
        } catch (e) { alert(`promote failed: ${e.message}`); }
      });
      actTd.appendChild(promoteBtn);
      if (a.status === 'promoted') {
        const u = el('button', { class: 'ft-pill', title: 'unpromote' }, 'unpromote');
        u.addEventListener('click', async () => {
          try {
            await fetchJSON(`/api/finetuning/adapters/${a.adapter_id}/unpromote`, { method: 'POST' });
            await loadAll();
          } catch (e) { alert(`unpromote failed: ${e.message}`); }
        });
        actTd.appendChild(u);
      }
      tr.appendChild(actTd);
      tbody.appendChild(tr);
    }
  } catch (e) { /* swallow */ }
}

async function loadRuns() {
  try {
    const r = await fetchJSON('/api/finetuning/runs');
    const tbody = $('#ft-runs-table tbody');
    tbody.innerHTML = '';
    for (const run of r.runs || []) {
      const tr = el('tr');
      tr.appendChild(el('td', {}, run.run_id));
      tr.appendChild(el('td', {}, run.base_model_id || '?'));
      tr.appendChild(el('td', {}, run.recipe_name));
      tr.appendChild(el('td', {}, run.backend));
      const stTd = el('td', {});
      stTd.appendChild(statusPill(run.status));
      tr.appendChild(stTd);
      tr.appendChild(el('td', {}, run.adapter_id || '-'));
      const actTd = el('td', {});
      if (!['eval_passed', 'eval_failed', 'promoted', 'aborted'].includes(run.status)) {
        const a = el('button', { class: 'ft-pill' }, 'abort');
        a.addEventListener('click', async () => {
          try {
            await fetchJSON(`/api/finetuning/runs/${run.run_id}/abort`, { method: 'POST' });
            await loadAll();
          } catch (e) { alert(`abort failed: ${e.message}`); }
        });
        actTd.appendChild(a);
      }
      tr.appendChild(actTd);
      tbody.appendChild(tr);
    }
  } catch (e) { /* swallow */ }
}

async function loadAll() {
  await Promise.all([loadStats(), loadDatasets(), loadAdapters(), loadRuns()]);
  $('#ft-updated').textContent = 'updated ' + new Date().toLocaleTimeString();
}

$('#ft-refresh').addEventListener('click', loadAll);

$('#ft-dataset-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const body = {
    path: fd.get('path'),
    name: fd.get('name'),
    format: fd.get('format'),
    split: parseFloat(fd.get('split')),
  };
  $('#ft-dataset-result').textContent = 'registering…';
  try {
    const rec = await fetchJSON('/api/finetuning/datasets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    $('#ft-dataset-result').textContent = `registered ${rec.dataset_id} n=${rec.n_samples}`;
    await loadAll();
  } catch (e) {
    $('#ft-dataset-result').textContent = `error: ${e.message}`;
  }
});

$('#ft-run-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const body = {
    dataset_id: fd.get('dataset_id'),
    base_model_id: fd.get('base_model_id'),
    recipe_name: fd.get('recipe'),
    backend: fd.get('backend'),
    register: fd.get('register') === 'on',
  };
  const epochs = fd.get('epochs');
  if (epochs) body.epochs = parseInt(epochs, 10);
  const ms = fd.get('max_steps');
  if (ms) body.max_steps = parseInt(ms, 10);
  const et = fd.get('eval_threshold');
  if (et) body.eval_threshold = parseFloat(et);
  $('#ft-run-result').textContent = 'starting…';
  try {
    const rec = await fetchJSON('/api/finetuning/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    $('#ft-run-result').textContent = `started ${rec.run_id} status=${rec.status}`;
    await loadAll();
  } catch (e) {
    $('#ft-run-result').textContent = `error: ${e.message}`;
  }
});

loadAll();
setInterval(loadAll, 15000);