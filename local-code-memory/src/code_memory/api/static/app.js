/* Local Code Memory - vanilla-JS single page app. No build step, no framework:
 * this is a local dev tool served by the same FastAPI process it talks to. */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  async function api(path, opts) {
    const res = await fetch("/api" + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const ct = res.headers.get("content-type") || "";
    return ct.includes("application/json") ? res.json() : res.text();
  }
  const get = (path) => api(path);
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

  // -- tabs ---------------------------------------------------------
  function showTab(name) {
    $$(".tab").forEach((el) => el.classList.toggle("hidden", el.id !== "tab-" + name));
    $$(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.tab === name));
    if (name === "overview") loadOverview();
    if (name === "docs") loadDocs();
    if (name === "endpoints") loadEndpoints();
    if (name === "sql") loadSql();
    if (name === "spark") loadSpark();
    if (name === "ask") loadTasks();
  }
  $$(".nav-item").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));

  // -- project / scan -------------------------------------------------
  async function refreshHealth() {
    try {
      const h = await get("/health");
      $("#project-root").value = h.project_root;
      return h;
    } catch (e) { return null; }
  }

  $("#btn-set-project").addEventListener("click", async () => {
    const root = $("#project-root").value.trim();
    if (!root) return;
    try {
      await post("/project", { root });
      showTab("overview");
    } catch (e) { alert("Could not open project: " + e.message); }
  });

  function setScanStatus(text, cls) {
    const el = $("#scan-status");
    el.textContent = text;
    el.className = "status" + (cls ? " " + cls : "");
  }

  async function pollJob(jobId, onDone) {
    for (;;) {
      const job = await get("/jobs/" + jobId);
      if (job.status === "running") { await new Promise((r) => setTimeout(r, 1200)); continue; }
      return job;
    }
  }

  $("#btn-scan").addEventListener("click", async () => {
    const mode = $("#scan-mode").value;
    $("#btn-scan").disabled = true;
    setScanStatus("scanning…", "running");
    try {
      const { job_id } = await post("/scan", { mode });
      const job = await pollJob(job_id);
      if (job.status === "done") {
        const r = job.result;
        setScanStatus(`done: ${r.files} files, ${r.graph ? r.graph.node_count : 0} nodes`, "done");
        loadOverview();
      } else {
        setScanStatus("failed: " + job.error, "error");
      }
    } catch (e) {
      setScanStatus("error: " + e.message, "error");
    } finally {
      $("#btn-scan").disabled = false;
    }
  });

  // -- overview -------------------------------------------------------
  async function loadOverview() {
    const ov = await get("/overview");
    $("#overview-empty").classList.toggle("hidden", ov.scanned);
    $("#overview-body").classList.toggle("hidden", !ov.scanned);
    if (!ov.scanned) return;

    const inv = ov.inventory;
    const g = (ov.manifest && ov.manifest.graph) || {};
    const stats = [
      ["Files", inv.file_count],
      ["Java LOC", inv.java_loc],
      ["Graph nodes", g.nodes ?? "-"],
      ["Graph edges", g.edges ?? "-"],
      ["Warnings", (inv.warnings || []).length],
    ];
    $("#overview-stats").innerHTML = stats.map(([l, n]) =>
      `<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");

    const b = inv.build || {};
    const kv = [
      ["Build system", b.build_system],
      ["Java", b.java_version],
      ["Spring Boot", b.spring_boot_version || "-"],
      ["Spark", b.spark_version || "-"],
      ["DB drivers", (b.database_drivers || []).join(", ") || "-"],
      ["Git", `${inv.git_branch || "-"} @ ${inv.git_commit || "-"}`],
    ];
    $("#overview-build").innerHTML = kv.map(([k, v]) =>
      `<div class="k">${k}</div><div class="v">${escapeHtml(String(v))}</div>`).join("");
  }

  // -- context docs ---------------------------------------------------
  async function loadDocs() {
    const files = await get("/context");
    $("#docs-list").innerHTML = files.map((f) =>
      `<li data-name="${f.name}" class="${f.present ? "" : "missing"}">${f.name}</li>`).join("");
    $$("#docs-list li").forEach((li) => li.addEventListener("click", () => selectDoc(li)));
    const first = $("#docs-list li:not(.missing)");
    if (first) selectDoc(first);
  }
  async function selectDoc(li) {
    $$("#docs-list li").forEach((el) => el.classList.remove("active"));
    li.classList.add("active");
    if (li.classList.contains("missing")) {
      $("#docs-content").textContent = "Not generated yet.";
      return;
    }
    $("#docs-content").textContent = await get("/context/" + li.dataset.name);
  }

  // -- search ---------------------------------------------------------
  $("#btn-search").addEventListener("click", runSearch);
  $("#search-query").addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(); });
  async function runSearch() {
    const query = $("#search-query").value.trim();
    if (!query) return;
    const box = $("#search-results");
    box.innerHTML = "<div class=\"empty\">Searching…</div>";
    const items = await post("/search", { query, k: 15 });
    if (!items.length) { box.innerHTML = "<div class=\"empty\">No results.</div>"; return; }
    box.innerHTML = items.map((it) => `
      <div class="result-item" data-symbol="${escapeAttr(it.fqn || it.node_id)}">
        <div class="top">
          <span class="fqn">${escapeHtml(it.fqn || it.node_id)}</span>
          <span class="score">${it.score}</span>
        </div>
        <div class="meta">
          <span class="tag">${it.kind}</span>
          ${it.file ? `${escapeHtml(it.file)}:${it.line || ""}` : ""}
          &middot; via ${it.sources.join(", ")}
        </div>
      </div>`).join("");
    $$("#search-results .result-item").forEach((el) =>
      el.addEventListener("click", () => {
        showTab("graph");
        $("#graph-symbol").value = el.dataset.symbol;
        lookupGraph();
      }));
  }

  // -- graph explorer ---------------------------------------------
  $("#btn-graph").addEventListener("click", lookupGraph);
  $("#graph-symbol").addEventListener("keydown", (e) => { if (e.key === "Enter") lookupGraph(); });
  async function lookupGraph() {
    const symbol = $("#graph-symbol").value.trim();
    if (!symbol) return;
    const box = $("#graph-body");
    box.innerHTML = "<div class=\"empty\">Looking up…</div>";
    try {
      const data = await get("/graph/node/" + encodeURIComponent(symbol));
      const n = data.node || {};
      const loc = n.location ? `${n.location.relative_path}:${n.location.line_start}` : "";
      const edges = data.neighbors.map((nb) => `
        <li>
          <span class="tag">${nb.edge}</span> ${escapeHtml(nb.id)}
          <span class="conf conf-${nb.confidence}">${nb.confidence || ""}</span>
        </li>`).join("");
      box.innerHTML = `
        <div class="card">
          <h3>${n.kind || "node"}</h3>
          <div class="kv">
            <div class="k">id</div><div class="v">${escapeHtml(n.id || symbol)}</div>
            <div class="k">location</div><div class="v">${escapeHtml(loc) || "-"}</div>
          </div>
        </div>
        <div class="card"><h3>Neighbours (${data.neighbors.length})</h3>
          <ul class="edge-list">${edges || "<li>none</li>"}</ul>
        </div>`;
    } catch (e) {
      box.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
    }
  }

  // -- endpoints / sql / spark --------------------------------------
  async function loadEndpoints() {
    const eps = await get("/endpoints");
    const box = $("#endpoints-body");
    if (!eps.length) { box.className = "empty"; box.textContent = "No HTTP endpoints detected."; return; }
    box.className = "";
    box.innerHTML = `<table class="data"><thead><tr>
        <th>Method</th><th>Path</th><th>Handler</th><th>Flow</th></tr></thead><tbody>
      ${eps.map((e) => `<tr>
          <td><span class="tag">${e.http_method}</span></td>
          <td><code>${escapeHtml(e.path)}</code></td>
          <td><code>${escapeHtml((e.handler || "").split("#")[1] || e.handler || "")}</code></td>
          <td>${e.flow.map((f) => shortId(f)).join(" &rarr; ")}</td>
        </tr>`).join("")}
      </tbody></table>`;
  }

  async function loadSql() {
    const data = await get("/sql");
    const box = $("#sql-body");
    if (!data.statements.length) { box.className = "empty"; box.textContent = "No SQL detected."; return; }
    box.className = "";
    box.innerHTML = `
      <div class="card"><h3>Tables</h3>
        <table class="data"><thead><tr><th>Table</th><th>Read by</th><th>Written by</th></tr></thead>
        <tbody>${data.tables.map((t) => `<tr>
            <td><code>${escapeHtml(t.name)}</code></td>
            <td>${t.read_by.length}</td><td>${t.written_by.length}</td></tr>`).join("")}
        </tbody></table>
      </div>
      <div class="card"><h3>Statements (${data.statements.length})</h3>
        <table class="data"><thead><tr><th>Type</th><th>Reads</th><th>Writes</th></tr></thead>
        <tbody>${data.statements.map((s) => `<tr>
            <td><span class="tag">${s.type}</span></td>
            <td>${s.reads.join(", ") || "-"}</td>
            <td>${s.writes.join(", ") || "-"}</td></tr>`).join("")}
        </tbody></table>
      </div>`;
  }

  async function loadSpark() {
    const jobs = await get("/spark");
    const box = $("#spark-body");
    if (!jobs.length) { box.className = "empty"; box.textContent = "No Spark jobs detected."; return; }
    box.className = "";
    box.innerHTML = jobs.map((j) => `
      <div class="card">
        <h3>${escapeHtml(j.fqn)}</h3>
        <div class="kv">
          <div class="k">transformations</div><div class="v">${j.transformations.join(", ") || "-"}</div>
          <div class="k">actions</div><div class="v">${j.actions.join(", ") || "-"}</div>
          <div class="k">reads</div><div class="v">${j.reads_tables.map(shortId).join(", ") || "-"}</div>
          <div class="k">writes</div><div class="v">${j.writes_tables.map(shortId).join(", ") || "-"}</div>
        </div>
      </div>`).join("");
  }

  // -- ask / tasks ------------------------------------------------
  $("#btn-ask").addEventListener("click", async () => {
    const task = $("#ask-task").value.trim();
    if (!task) return;
    const statusEl = $("#ask-status");
    $("#btn-ask").disabled = true;
    statusEl.textContent = "running…"; statusEl.className = "status running";
    try {
      const { job_id } = await post("/tasks", {
        task, ask: $("#ask-ask").checked, patch: $("#ask-patch").checked,
        mode: $("#ask-mode").value,
      });
      const job = await pollJob(job_id);
      if (job.status === "done") {
        statusEl.textContent = "done"; statusEl.className = "status done";
        await loadTasks();
        const list = $("#tasks-list li");
        if (list) selectTask(list);
      } else {
        statusEl.textContent = "failed: " + job.error; statusEl.className = "status error";
      }
    } catch (e) {
      statusEl.textContent = "error: " + e.message; statusEl.className = "status error";
    } finally {
      $("#btn-ask").disabled = false;
    }
  });

  async function loadTasks() {
    const tasks = await get("/tasks");
    $("#tasks-list").innerHTML = tasks.map((t) => `
      <li data-id="${t.id}">${t.id}<span class="sub">${escapeHtml(t.task || "")}</span></li>`).join("")
      || "<li class=\"missing\">no tasks yet</li>";
    $$("#tasks-list li[data-id]").forEach((li) => li.addEventListener("click", () => selectTask(li)));
  }
  async function selectTask(li) {
    $$("#tasks-list li").forEach((el) => el.classList.remove("active"));
    li.classList.add("active");
    const data = await get("/tasks/" + li.dataset.id);
    const detail = $("#task-detail");
    const parts = [];
    // advice.md already opens with its own "# Advice" heading (rendered by
    // mdLite), so don't wrap it in a second "Advice" section title.
    if (data.files["advice.md"]) parts.push(mdLite(data.files["advice.md"]));
    if (data.files["patch.diff"]) parts.push(section("Patch (not applied)", data.files["patch.diff"], true));
    if (data.files["task.md"]) parts.push(section("Task", data.files["task.md"]));
    if (data.files["source_context.md"]) parts.push(section("Source context", data.files["source_context.md"], true));
    if (data.files["call_graph.md"]) parts.push(section("Call graph", data.files["call_graph.md"], true));
    detail.innerHTML = parts.join("") || "<div class=\"empty\">No files.</div>";
  }
  function section(title, text, pre) {
    const body = pre ? `<pre>${escapeHtml(text)}</pre>` : mdLite(text);
    return `<h2>${title}</h2>${body}`;
  }
  // Very small markdown-ish renderer: headings, bold, code, lists - enough for
  // advice.md, which we generate ourselves.
  function mdLite(text) {
    const lines = escapeHtml(text).split("\n");
    let html = "", inList = false;
    for (const line of lines) {
      const m = line.match(/^(#{1,4})\s+(.*)/);
      if (m) { if (inList) { html += "</ul>"; inList = false; } html += `<h3>${m[2]}</h3>`; continue; }
      if (/^[-*]\s+/.test(line)) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += `<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`;
        continue;
      }
      if (inList) { html += "</ul>"; inList = false; }
      if (line.trim()) html += `<p>${inline(line)}</p>`;
    }
    if (inList) html += "</ul>";
    return html;
  }
  function inline(s) {
    return s.replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(?:^|(?<=\s))_([^_]+)_(?=\s|$)/g, "<i>$1</i>");
  }

  // -- helpers -------------------------------------------------------
  function shortId(id) { return escapeHtml(String(id).split(":").slice(1).join(":") || id); }
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function escapeAttr(s) { return escapeHtml(s).replace(/'/g, "&#39;"); }

  // -- boot ------------------------------------------------------
  (async () => {
    const h = await refreshHealth();
    if (h && h.scanned) loadOverview();
  })();
})();
