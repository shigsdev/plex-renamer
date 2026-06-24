/* Plex Renamer — front-end. Vanilla JS, no build step.
   Holds the scanned rows in memory; the user toggles approve / edits target
   names / bulk-approves a tier, then Apply POSTs the approved set.
   All dynamic content is built with createElement/textContent (never
   innerHTML), so a filename containing markup can't inject anything. */

let ROWS = [];

const $ = (s) => document.querySelector(s);

/* tiny DOM helper: h("span", {class:"x"}, "text" | childNode | [..]) */
function h(tag, attrs = {}, kids = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function")
      node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const kid of [].concat(kids))
    if (kid != null) node.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
  return node;
}

function setStatus(msg, busy = false) {
  const s = $("#status");
  s.textContent = msg;
  s.classList.toggle("busy", busy);
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { ok: r.ok, data: await r.json() };
}

/* ---- scanning ---- */
$("#scan").addEventListener("click", async () => {
  const directory = $("#dir").value.trim();
  if (!directory) { setStatus("Enter a directory first."); return; }
  const season = $("#season").value.trim();
  setStatus("Scanning…", true);
  const { ok, data } = await postJSON("/api/scan", { directory, forced_season: season || "auto" });
  if (!ok) { setStatus(data.error || "Scan failed."); return; }
  ROWS = data.rows;
  renderSummary(data.summary);
  renderRows();
  setStatus(`Scanned ${data.rows.length} files needing attention.`);
  $("#summary").classList.remove("hidden");
  $("#controls").classList.remove("hidden");
});

function renderSummary(s) {
  const box = $("#summary");
  box.replaceChildren(
    h("span", { class: "pill high", text: `${s.high} high` }),
    h("span", { class: "pill medium", text: `${s.medium} medium` }),
    h("span", { class: "pill guess", text: `${s.guess} guess` }),
    h("span", { class: "pill date", text: `${s.date} daily-date` }),
    h("span", { class: "pill skip", text: `${s.extra} extras` }),
    h("span", { class: "pill skip", text: `${s.none} undetermined` }),
  );
}

/* ---- rendering rows, grouped by show ---- */
function renderRows() {
  const hideSkipped = $("#hideSkipped").checked;
  const container = $("#results");
  container.replaceChildren();

  const groups = new Map();
  for (const row of ROWS) {
    if (hideSkipped && !row.changed) continue;
    if (!groups.has(row.show)) groups.set(row.show, []);
    groups.get(row.show).push(row);
  }

  // master "select all" across every renamable row
  const allChanged = ROWS.filter((r) => r.changed);
  if (allChanged.length) {
    const masterCb = h("input", { type: "checkbox", class: "groupcheck" });
    syncGroupCheckbox(masterCb, allChanged);
    masterCb.addEventListener("change", () => {
      for (const r of allChanged) r._approved = masterCb.checked;
      renderRows();
    });
    container.appendChild(h("label", { class: "selectall" }, [
      masterCb, h("span", { text: `Select all (${allChanged.length})` }),
    ]));
  }

  for (const [show, rows] of [...groups.entries()].sort()) {
    const changedRows = rows.filter((r) => r.changed);
    const groupCb = h("input", { type: "checkbox", class: "groupcheck", title: "Select all in this show" });
    groupCb.disabled = changedRows.length === 0;
    syncGroupCheckbox(groupCb, changedRows);
    // clicking the checkbox in a <summary> must not toggle the disclosure
    groupCb.addEventListener("click", (e) => e.stopPropagation());
    groupCb.addEventListener("change", () => {
      for (const r of changedRows) r._approved = groupCb.checked;
      renderRows();
    });
    const details = h("details", { open: "" }, [
      h("summary", {}, [
        groupCb,
        h("strong", { text: show }),
        h("span", { class: "muted", text: ` ${changedRows.length} change(s), ${rows.length} shown` }),
      ]),
    ]);

    const thead = h("thead", {}, h("tr", {}, [
      h("th", {}), h("th", { text: "conf" }), h("th", { text: "current name" }),
      h("th", { text: "proposed name" }), h("th", { text: "why" }), h("th", {}),
    ]));
    const tbody = h("tbody");
    for (const row of rows) tbody.appendChild(buildRow(row));
    details.appendChild(h("table", {}, [thead, tbody]));
    container.appendChild(details);
  }
  updateApproved();
  const dc = $("#dateCount");
  if (dc) dc.textContent = ROWS.filter((r) => r.kind === "date").length;
}

/* ---- bulk daily-show episode lookup (TVDB/TMDB) ---- */
async function lookupAllDates() {
  const dates = ROWS.filter((r) => r.kind === "date");
  if (!dates.length) { setStatus("No daily-show (date) rows to look up."); return; }
  const btn = $("#lookupAll");
  if (btn) btn.disabled = true;
  let found = 0;
  for (let i = 0; i < dates.length; i++) {
    const r = dates[i];
    setStatus(`Looking up episode numbers… ${i + 1}/${dates.length}`, true);
    try {
      const { data } = await postJSON("/api/lookup", { show: r.show, airdate: r.airdate, path: r.path });
      if (data.found) { r.new_name = data.new_name; found++; }
    } catch { /* keep going */ }
  }
  if (btn) btn.disabled = false;
  setStatus(`Episode lookup: matched ${found} of ${dates.length} daily file(s).`);
  renderRows();
}

function syncGroupCheckbox(cb, changedRows) {
  const approved = changedRows.filter((r) => r._approved).length;
  cb.checked = changedRows.length > 0 && approved === changedRows.length;
  cb.indeterminate = approved > 0 && approved < changedRows.length;
}

function buildRow(row) {
  const cb = h("input", { type: "checkbox", class: "approve" });
  const input = h("input", { type: "text", class: "newname" });
  const badge = h("span", {
    class: `badge ${row.changed ? row.confidence : "skip"}`,
    text: row.changed ? row.confidence : row.kind,
  });
  const actions = h("td", { class: "c-act" });

  if (row.changed) {
    input.value = row.new_name;
    input.addEventListener("input", () => { row.new_name = input.value; });
    cb.checked = !!row._approved;
    cb.addEventListener("change", () => { row._approved = cb.checked; updateApproved(); });
    if (row.kind === "date" && window.HAS_KEY) actions.appendChild(lookupButton(row, input));
  } else {
    input.placeholder = row.kind === "extra" ? "(extra — left alone)" : "(no match — left alone)";
    input.disabled = true;
    cb.disabled = true;
  }

  const tr = h("tr", row.changed ? {} : { class: "skiprow" }, [
    h("td", { class: "c-approve" }, cb),
    h("td", { class: "c-conf" }, badge),
    h("td", { class: "c-old", text: row.old_name }),
    h("td", { class: "c-new" }, input),
    h("td", { class: "c-note", text: row.note }),
    actions,
  ]);
  return tr;
}

function lookupButton(row, input) {
  const prov = (window.PROVIDER || "tmdb").toUpperCase();
  const b = h("button", { class: "mini", title: `Look up real S##E## by air date (${prov})`, text: "🔍 ep #" });
  b.addEventListener("click", async () => {
    b.disabled = true; b.textContent = "…";
    const { data } = await postJSON("/api/lookup", { show: row.show, airdate: row.airdate, path: row.path });
    if (data.found) {
      row.new_name = data.new_name;
      input.value = data.new_name;
      b.textContent = `S${data.season}E${data.episode}`;
    } else {
      b.textContent = "not found";
    }
  });
  return b;
}

/* ---- bulk approve ---- */
document.querySelectorAll("[data-approve]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const tier = btn.dataset.approve;
    for (const row of ROWS) {
      if (!row.changed) continue;
      if (tier === "none") row._approved = false;
      else if (tier === "all") row._approved = true;
      else if (tier === "high") row._approved = row.confidence === "high";
      else if (tier === "medium")
        row._approved = row._approved || ["high", "medium"].includes(row.confidence);
    }
    renderRows();
  });
});

$("#hideSkipped").addEventListener("change", renderRows);
$("#lookupAll")?.addEventListener("click", lookupAllDates);

function updateApproved() {
  $("#approvedCount").textContent = ROWS.filter((r) => r.changed && r._approved).length;
}

/* ---- apply (streamed progress) ---- */
function showProgress(i, total) {
  const sec = $("#progress");
  sec.classList.remove("hidden");
  const pct = total ? Math.round((i / total) * 100) : 0;
  $("#pfill").style.width = pct + "%";
  $("#ptext").textContent = `Renaming ${i} of ${total}… (${pct}%)`;
}

function showBanner(text, kind) {
  const b = $("#banner");
  b.className = `banner ${kind}`;   // ok | err
  b.textContent = text;
  b.classList.remove("hidden");
  b.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("#apply").addEventListener("click", async () => {
  const items = ROWS.filter((r) => r.changed && r._approved)
    .map((r) => ({ path: r.path, new_name: r.new_name }));
  if (!items.length) { setStatus("Nothing approved."); return; }
  if (!confirm(`Rename ${items.length} file(s)? This can be undone.`)) return;

  $("#banner").classList.add("hidden");
  $("#apply").disabled = true;
  showProgress(0, items.length);
  setStatus("Renaming…", true);

  let final = null;
  try {
    const resp = await fetch("/api/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.done) final = ev;
        else showProgress(ev.i, ev.total);
      }
    }
  } catch (e) {
    setStatus("");
    showBanner(`Apply failed: ${e.message}`, "err");
    $("#apply").disabled = false;
    return;
  }

  $("#apply").disabled = false;
  setStatus("");
  $("#pfill").style.width = "100%";
  $("#ptext").textContent = `Finished — ${final.applied} of ${items.length} renamed.`;

  let msg = `✓ Done. Renamed ${final.applied} file(s).`;
  if (final.failed.length) msg += `  ${final.failed.length} skipped/failed (${final.failed.map((f) => f.error).join(", ")}).`;
  if (final.undo_log) msg += `  Reversible via “Undo last batch” (log: ${final.undo_log}).`;
  showBanner(msg, final.failed.length ? "err" : "ok");

  // drop the rows that actually succeeded so the list reflects reality
  if (final.applied) {
    const failedPaths = new Set();   // we only have basenames for failures; keep them visible
    const approvedPaths = items.map((i) => i.path);
    ROWS = ROWS.filter((r) => !(approvedPaths.includes(r.path) && r._approved) ||
                              final.failed.some((f) => r.old_name.includes(f.src)));
  }
  renderRows();
});

/* ---- undo ---- */
$("#undo").addEventListener("click", async () => {
  if (!confirm("Undo the most recent rename batch?")) return;
  setStatus("Undoing…", true);
  const { ok, data } = await postJSON("/api/undo", {});
  setStatus(ok ? `Reverted ${data.reverted} (${data.failed} failed) from ${data.log}.`
               : (data.error || "Undo failed."));
});
