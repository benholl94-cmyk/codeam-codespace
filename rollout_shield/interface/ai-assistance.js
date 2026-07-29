// rollout-shield AI-assistance tab — vanilla JS frontend.
// Polls /api/ai/* endpoints and renders the AI layer's state.

(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  async function fetchJSON(url) {
    const resp = await fetch(url, { headers: { "Accept": "application/json" } });
    if (!resp.ok) {
      throw new Error(`GET ${url}: ${resp.status}`);
    }
    return resp.json();
  }

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

  // ---------- renderers ----------

  async function renderLeaderboard() {
    try {
      const data = await fetchJSON("/api/ai/leaderboard");
      const tbody = $("#leaderboard-table tbody");
      tbody.innerHTML = "";
      const entries = data.entries || [];
      $("#leaderboard-empty").style.display = entries.length ? "none" : "";
      if (data.best) {
        const note = document.createElement("p");
        note.className = "muted small";
        note.innerHTML = `top model: <code>${escapeHtml(data.best.model_id)}</code> (avg score ${data.best.score.toFixed(4)})`;
        $("#leaderboard-empty").parentElement.insertBefore(note, $("#leaderboard-empty").nextSibling);
      }
      const models = [...new Set(entries.map((e) => e.model_id))];
      for (const mid of models) {
        const perBench = entries
          .filter((e) => e.model_id === mid)
          .sort((a, b) => a.benchmark_name.localeCompare(b.benchmark_name));
        const avg = data.scores && data.scores[mid] != null ? data.scores[mid] : 0;
        const bstr = perBench.map((e) => `${e.benchmark_name}=${e.score.toFixed(2)}`).join(", ");
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><code>${escapeHtml(mid)}</code></td>
          <td>${avg.toFixed(4)}</td>
          <td><span class="muted small">${escapeHtml(bstr)}</span></td>
        `;
        tbody.appendChild(tr);
      }
    } catch (exc) {
      console.error("renderLeaderboard failed:", exc);
    }
  }

  async function renderCycles() {
    try {
      const data = await fetchJSON("/api/ai/cycles?limit=50");
      const tbody = $("#cycles-table tbody");
      tbody.innerHTML = "";
      const cycles = data.cycles || [];
      $("#cycles-empty").style.display = cycles.length ? "none" : "";
      for (const c of cycles) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${c.cycle}</td>
          <td>${formatTs(c.ts)}</td>
          <td>${escapeHtml(c.prompt.slice(0, 60))}${c.prompt.length > 60 ? "…" : ""}</td>
          <td><code>${escapeHtml(c.selected_model || "—")}</code></td>
          <td><span class="strategy-pill">${escapeHtml(c.router_strategy)}</span></td>
          <td>x${(c.parallel_speedup || 0).toFixed(2)}</td>
        `;
        tbody.appendChild(tr);
      }
    } catch (exc) {
      console.error("renderCycles failed:", exc);
    }
  }

  async function renderArtifacts() {
    try {
      const data = await fetchJSON("/api/ai/first-of-kind?limit=50");
      const root = $("#artifacts-list");
      root.innerHTML = "";
      const artifacts = data.artifacts || [];
      if (!artifacts.length) {
        const p = document.createElement("p");
        p.className = "muted small";
        p.textContent = "No first-of-kind artifacts yet — generate one above.";
        root.appendChild(p);
        return;
      }
      for (const a of artifacts) {
        const card = document.createElement("div");
        card.className = "artifact-card";
        card.innerHTML = `
          <h4>${escapeHtml(a.id)} <span class="muted">(${escapeHtml(a.kind)})</span></h4>
          <div class="meta">
            <span class="strategy-pill">${escapeHtml(a.route_strategy)}</span>
            model <code>${escapeHtml(a.model_id)}</code> ·
            prompt digest <code>${escapeHtml(a.prompt_digest)}</code> ·
            ${formatTs(a.ts)}
          </div>
          <pre>${escapeHtml(a.text)}</pre>
        `;
        root.appendChild(card);
      }
    } catch (exc) {
      console.error("renderArtifacts failed:", exc);
    }
  }

  async function renderModels() {
    try {
      const data = await fetchJSON("/api/ai/models");
      const tbody = $("#models-table tbody");
      tbody.innerHTML = "";
      for (const m of data.models || []) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><code>${escapeHtml(m.id)}</code></td>
          <td>${escapeHtml(m.name)}</td>
          <td>${escapeHtml(m.family)}</td>
          <td>${escapeHtml(m.description)}</td>
        `;
        tbody.appendChild(tr);
      }
    } catch (exc) {
      console.error("renderModels failed:", exc);
    }
  }

  async function renderLatestArtifact(prompt, kind) {
    const root = $("#latest-artifact");
    root.innerHTML = '<p class="muted small">generating…</p>';
    try {
      const data = await fetchJSON("/api/ai/first-of-kind?limit=1");
      const latest = (data.artifacts || [])[0];
      if (!latest) {
        root.innerHTML = '<p class="muted small">no artifact found</p>';
        return;
      }
      root.innerHTML = `
        <div class="artifact-card">
          <h4>${escapeHtml(latest.id)} <span class="muted">(${escapeHtml(latest.kind)})</span></h4>
          <div class="meta">
            <span class="strategy-pill">${escapeHtml(latest.route_strategy)}</span>
            model <code>${escapeHtml(latest.model_id)}</code> ·
            prompt digest <code>${escapeHtml(latest.prompt_digest)}</code>
          </div>
          <pre>${escapeHtml(latest.text)}</pre>
        </div>
      `;
    } catch (exc) {
      root.innerHTML = `<p class="muted small">error: ${exc}</p>`;
    }
  }

  // ---------- generate button ----------

  $("#generate-btn").addEventListener("click", async () => {
    const promptEl = $("#prompt-input");
    const kindEl = $("#kind-select");
    const prompt = (promptEl.value || "").trim();
    const kind = kindEl.value || "poem";
    if (!prompt) {
      promptEl.focus();
      return;
    }
    promptEl.disabled = true;
    kindEl.disabled = true;
    $("#generate-btn").disabled = true;
    try {
      // The dashboard doesn't run the router; the CLI does. The
      // user must run the CLI for now to generate an artifact.
      // We just refresh the list to show the latest after the user
      // runs `rollout-shield ai first-of-kind <prompt> --kind <kind>`.
      await renderArtifacts();
      await renderLatestArtifact(prompt, kind);
    } finally {
      promptEl.disabled = false;
      kindEl.disabled = false;
      $("#generate-btn").disabled = false;
    }
  });

  // ---------- boot ----------

  async function refreshAll() {
    await Promise.all([
      renderLeaderboard(),
      renderCycles(),
      renderArtifacts(),
      renderModels(),
    ]);
  }

  refreshAll();
  setInterval(refreshAll, 15000);
})();
