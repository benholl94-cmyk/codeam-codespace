// rollout-shield dashboard — vanilla JS frontend.
// Polls /api/* endpoints and renders into the panes. No external deps.

(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const STATUS_PILL = $("#status-pill");

  // -------- tab switching --------

  function activateTab(name) {
    $$(".tab-pane").forEach((p) => p.classList.remove("active"));
    $$(".tabs button").forEach((b) => b.classList.remove("active"));
    const pane = $("#tab-" + name);
    const btn = document.querySelector(`.tabs button[data-tab="${name}"]`);
    if (pane) pane.classList.add("active");
    if (btn) btn.classList.add("active");
  }

  $$(".tabs button").forEach((b) => {
    b.addEventListener("click", () => activateTab(b.dataset.tab));
  });

  // -------- fetch helpers --------

  async function fetchJSON(url) {
    const resp = await fetch(url, { headers: { "Accept": "application/json" } });
    if (!resp.ok) {
      throw new Error(`GET ${url}: ${resp.status}`);
    }
    return resp.json();
  }

  // -------- renderers --------

  function setStatusPill(status) {
    STATUS_PILL.classList.remove("pill-healthy", "pill-degraded", "pill-unhealthy", "pill-unknown");
    STATUS_PILL.classList.add("pill-" + (status || "unknown"));
    STATUS_PILL.textContent = status || "unknown";
  }

  async function renderOverview() {
    const s = await fetchJSON("/api/status");
    $("#state-root").textContent = s.state_root;
    $("#footer-state-root").textContent = s.state_root;
    $("#generated-at").textContent = "generated at " + new Date(s.generated_at * 1000).toLocaleString();
    $("#agents-count").textContent = s.agents.total;
    const list = $("#agents-list");
    list.innerHTML = "";
    for (const aid of s.agents.ids) {
      const li = document.createElement("li");
      li.textContent = aid;
      list.appendChild(li);
    }
    $("#claims-count").textContent = s.claims_count;
    $("#alerts-count").textContent = s.alerts_count;
    const lh = s.latest_health;
    const lhEl = $("#latest-health");
    if (lh) {
      const ts = lh.ts ? new Date(lh.ts * 1000).toLocaleString() : "(no ts)";
      lhEl.innerHTML = "";
      const status = document.createElement("div");
      status.className = "big-number";
      status.textContent = lh.status || "unknown";
      lhEl.appendChild(status);
      const details = document.createElement("div");
      details.className = "muted small";
      details.textContent = `${lh.ok || 0}/${lh.total || 0} checks ok · ${ts}`;
      lhEl.appendChild(details);
      setStatusPill(lh.status);
    } else {
      lhEl.textContent = "(no health data yet)";
      setStatusPill("unknown");
    }
  }

  async function renderClaims() {
    const data = await fetchJSON("/api/claims?limit=100");
    const tbody = $("#claims-table tbody");
    tbody.innerHTML = "";
    for (const c of (data.claims || [])) {
      const tr = document.createElement("tr");
      const sig = (c.signing && c.signing.signature) ? c.signing.signature.slice(0, 12) + "…" : "—";
      tr.innerHTML = `
        <td><code>${escapeHtml(c.id)}</code></td>
        <td>${escapeHtml(c.type)}</td>
        <td>${escapeHtml(c.agent_id)}</td>
        <td>${formatTs(c.ts)}</td>
        <td>${c.parent ? `<code>${escapeHtml(c.parent)}</code>` : "—"}</td>
        <td><code title="${escapeHtml(c.signing?.signature || "")}">${escapeHtml(sig)}</code></td>
      `;
      tbody.appendChild(tr);
    }
  }

  async function renderAlerts() {
    const data = await fetchJSON("/api/alerts?limit=100");
    const tbody = $("#alerts-table tbody");
    tbody.innerHTML = "";
    for (const a of (data.alerts || [])) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><code>${escapeHtml(a.id)}</code></td>
        <td><span class="sev sev-${escapeHtml(a.severity || "warning")}">${escapeHtml(a.severity || "warning")}</span></td>
        <td>${escapeHtml(a.source || "")}</td>
        <td>${escapeHtml(a.message || "")}</td>
        <td>${formatTs(a.ts)}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  async function renderReputation() {
    const data = await fetchJSON("/api/reputation");
    const tbody = $("#reputation-table tbody");
    tbody.innerHTML = "";
    const agents = (data.agents || {});
    const rows = Object.entries(agents).map(([aid, entry]) => ({
      agent_id: aid,
      score: entry.score || 0,
      events: (entry.history || []).length,
      last_event_ts: ((entry.history || []).slice(-1)[0] || {}).ts || null,
    }));
    rows.sort((a, b) => b.score - a.score);
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><code>${escapeHtml(r.agent_id)}</code></td>
        <td>${r.score.toFixed(2)}</td>
        <td>${r.events}</td>
        <td>${formatTs(r.last_event_ts)}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  async function renderKeys() {
    const data = await fetchJSON("/api/keys");
    const tbody = $("#keys-table tbody");
    tbody.innerHTML = "";
    for (const k of (data.keys || [])) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><code>${escapeHtml(k.id)}</code></td>
        <td>${escapeHtml(k.agent_id || "")}</td>
        <td>${escapeHtml(k.algorithm || "")}</td>
        <td><code title="${escapeHtml(k.fingerprint || "")}">${escapeHtml((k.fingerprint || "").slice(0, 24))}…</code></td>
        <td>${formatTs(k.created_at)}</td>
        <td>${k.hardware_anchored ? "yes" : "no"}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  // -------- utils --------

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatTs(ts) {
    if (!ts) return "—";
    return new Date(ts * 1000).toLocaleString();
  }

  // -------- refresh --------

  async function refreshAll() {
    try {
      await renderOverview();
      await Promise.all([renderClaims(), renderAlerts(), renderReputation(), renderKeys()]);
    } catch (exc) {
      console.error("refresh failed:", exc);
    }
  }

  $("#refresh").addEventListener("click", refreshAll);

  // -------- boot --------

  refreshAll();
  setInterval(refreshAll, 15000);  // refresh every 15s
})();
