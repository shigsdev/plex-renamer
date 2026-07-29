/* Music duplicate cleanup — front-end. Vanilla JS, safe DOM building.
   Scans a library, streams tag-read progress, then lists duplicate songs
   (matched by artist+title+duration). Per group: pick the keeper (radio,
   pre-set to the suggestion) and approve removal (checkbox). Removal reuses the
   shared quarantine endpoints (/api/dedupe/{remove,undo,empty}). */

let GROUPS = [];

const $ = (s) => document.querySelector(s);

function h(tag, attrs = {}, kids = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const kid of [].concat(kids))
    if (kid != null) node.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
  return node;
}

function human(n) {
  for (const u of ["B", "KB", "MB", "GB", "TB"]) {
    if (n < 1024) return `${Math.round(n)}${u}`;
    n /= 1024;
  }
  return `${n.toFixed(1)}PB`;
}

function setStatus(msg, busy = false) {
  const s = $("#status");
  s.textContent = msg;
  s.classList.toggle("busy", busy);
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return { ok: r.ok, data: await r.json() };
}

/* ---- scan (streamed: progress while reading tags, then the groups) ---- */
$("#scan").addEventListener("click", async () => {
  const directory = $("#dir").value.trim();
  if (!directory) { setStatus("Enter a music folder."); return; }
  $("#banner").classList.add("hidden");
  $("#scan").disabled = true;
  setStatus("Reading tags…", true);
  showProgress(0, 0, "Starting…");

  let final = null;
  try {
    const resp = await fetch("/api/music/scan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ directory }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      setStatus(err.error || "Scan failed."); $("#scan").disabled = false;
      $("#progress").classList.add("hidden"); return;
    }
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
        else showProgress(ev.i, ev.total, `Reading tags ${ev.i} of ${ev.total}…`);
      }
    }
  } catch (e) {
    setStatus(""); $("#scan").disabled = false;
    $("#progress").classList.add("hidden");
    showBanner(`Scan failed: ${e.message}`, "err");
    return;
  }

  $("#scan").disabled = false;
  $("#progress").classList.add("hidden");
  GROUPS = final.groups.map((g) => ({ ...g, _keep: g.suggested_keep, _approved: false }));
  renderSummary(final.summary);
  renderGroups();
  setStatus(`Scanned ${final.summary.scanned} files — found ${final.summary.groups} duplicated song(s).`);
  refreshQuarantine(final.quarantine);
  $("#summary").classList.remove("hidden");
  $("#controls").classList.toggle("hidden", GROUPS.length === 0);
});

function showProgress(i, total, text) {
  $("#progress").classList.remove("hidden");
  const pct = total ? Math.round((i / total) * 100) : 0;
  $("#pfill").style.width = pct + "%";
  $("#ptext").textContent = text || "";
}

function renderSummary(s) {
  $("#summary").replaceChildren(
    h("span", { class: "pill guess", text: `${s.groups} duplicated songs` }),
    h("span", { class: "pill skip", text: `${s.copies} copies total` }),
    h("span", { class: "pill date", text: `${human(s.reclaimable_bytes)} reclaimable` }),
  );
}

/* ---- render one card per duplicated song ---- */
function renderGroups() {
  const container = $("#results");
  container.replaceChildren();

  if (GROUPS.length) {
    const masterCb = h("input", { type: "checkbox", class: "groupcheck" });
    const approved = GROUPS.filter((g) => g._approved).length;
    masterCb.checked = approved === GROUPS.length;
    masterCb.indeterminate = approved > 0 && approved < GROUPS.length;
    masterCb.addEventListener("change", () => {
      for (const g of GROUPS) { g._keep = g.suggested_keep; g._approved = masterCb.checked; }
      renderGroups();
    });
    container.appendChild(h("label", { class: "selectall" }, [
      masterCb, h("span", { text: `Select all (${GROUPS.length})` }),
    ]));
  }

  for (const g of GROUPS) container.appendChild(h("div", { class: "moviecard" }, [buildGroup(g)]));
  updateApproved();
}

function buildGroup(g) {
  const box = h("div", { class: "dupgroup" });
  const head = h("label", { class: "dupapprove" }, [
    (() => {
      const cb = h("input", { type: "checkbox" });
      cb.checked = g._approved;
      cb.addEventListener("change", () => { g._approved = cb.checked; box.classList.toggle("approved", cb.checked); updateApproved(); });
      return cb;
    })(),
    h("span", { class: "se", text: `${g.artist} — ${g.title}` }),
    h("span", { class: "muted", text: ` ${g.length} · approve removal of the non-kept copies` }),
  ]);
  box.appendChild(head);

  const name = `keep-${g.artist}-${g.title}-${g.duration}`.replace(/[^a-z0-9]/gi, "_");
  g.copies.forEach((c, i) => {
    const radio = h("input", { type: "radio", name });
    radio.checked = i === g._keep;
    radio.addEventListener("change", () => { g._keep = i; updateApproved(); });
    const path = (c.folder ? c.folder + "\\" : "") + c.filename;
    const row = h("label", { class: "copyrow" }, [
      radio,
      h("span", { class: "fmt", text: c.fmt }),
      h("span", { class: "res", text: c.bitrate ? `${c.bitrate}k` : "?" }),
      h("span", { class: "size", text: human(c.size) }),
      h("span", { class: "fpath", text: path, title: c.path }),
    ]);
    box.appendChild(row);
  });
  box.classList.toggle("approved", g._approved);
  return box;
}

/* ---- bulk ---- */
document.querySelectorAll("[data-bulk]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const mode = btn.dataset.bulk;
    for (const g of GROUPS) {
      if (mode === "clear") g._approved = false;
      else if (mode === "suggested") { g._keep = g.suggested_keep; g._approved = true; }
    }
    renderGroups();
  });
});

function removalItems() {
  const items = [];
  for (const g of GROUPS) {
    if (!g._approved) continue;
    g.copies.forEach((c, i) => { if (i !== g._keep) items.push({ path: c.path, size: c.size }); });
  }
  return items;
}

function updateApproved() {
  const items = removalItems();
  $("#approvedCount").textContent = items.length;
  $("#approvedSize").textContent = human(items.reduce((a, b) => a + (b.size || 0), 0));
}

function showBanner(text, kind) {
  const b = $("#banner");
  b.className = `banner ${kind}`;
  b.textContent = text;
  b.classList.remove("hidden");
  b.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---- remove (streamed) — shares the video deduper's quarantine endpoint ---- */
$("#remove").addEventListener("click", async () => {
  const items = removalItems();
  if (!items.length) { setStatus("No songs approved."); return; }
  if (!confirm(`Move ${items.length} duplicate file(s) to quarantine? This is reversible — use "Undo last removal" or restore later.`)) return;

  $("#banner").classList.add("hidden");
  $("#remove").disabled = true;
  showProgress(0, items.length, `Quarantining 0 of ${items.length}…`);
  setStatus("Quarantining…", true);

  let final = null;
  try {
    const resp = await fetch("/api/dedupe/remove", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }),
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
        if (ev.done) final = ev; else showProgress(ev.i, ev.total, `Quarantining ${ev.i} of ${ev.total}…`);
      }
    }
  } catch (e) {
    setStatus(""); showBanner(`Removal failed: ${e.message}`, "err"); $("#remove").disabled = false; return;
  }

  $("#remove").disabled = false;
  setStatus("");
  $("#progress").classList.add("hidden");
  let msg = `✓ Done. Quarantined ${final.removed} file(s) (${human(final.moved_bytes)}). Reversible via “Undo last removal”. Empty the quarantine to free the space.`;
  if (final.failed.length) msg += `  ${final.failed.length} failed (${final.failed.map((f) => f.error).join(", ")}).`;
  showBanner(msg, final.failed.length ? "err" : "ok");

  const removed = new Set(items.map((i) => i.path));
  for (const g of GROUPS) g.copies = g.copies.filter((c) => !removed.has(c.path));
  GROUPS = GROUPS.filter((g) => g.copies.length > 1);
  renderGroups();
  refreshQuarantine(final.removed
    ? { files: (window.QUARANTINE.files || 0) + final.removed, bytes: (window.QUARANTINE.bytes || 0) + final.moved_bytes }
    : window.QUARANTINE);
});

/* ---- quarantine status + undo + empty (shared endpoints) ---- */
function refreshQuarantine(stats) {
  window.QUARANTINE = stats || { files: 0, bytes: 0 };
  const n = window.QUARANTINE.files || 0;
  $("#quarstatus").textContent = n
    ? `Quarantine holds ${n} file(s) (${human(window.QUARANTINE.bytes || 0)}) — restorable until emptied.`
    : "Quarantine is empty.";
  $("#undo").disabled = !n;
  $("#empty").disabled = !n;
}

$("#undo").addEventListener("click", async () => {
  if (!confirm("Restore the most recent quarantine batch back to its original location?")) return;
  setStatus("Restoring…", true);
  const { ok, data } = await postJSON("/api/dedupe/undo", {});
  setStatus("");
  if (!ok) { showBanner(data.error || "Nothing to undo.", "err"); return; }
  showBanner(`↩ Restored ${data.restored} file(s)${data.failed ? `, ${data.failed} failed` : ""}. Re-scan to see them.`, data.failed ? "err" : "ok");
  refreshQuarantine(data.quarantine);
});

$("#empty").addEventListener("click", async () => {
  const n = window.QUARANTINE.files || 0;
  if (!confirm(`Permanently delete ${n} quarantined file(s)? This FREES the space and canNOT be undone.`)) return;
  setStatus("Emptying quarantine…", true);
  const { ok, data } = await postJSON("/api/dedupe/empty", {});
  setStatus("");
  if (!ok) { showBanner("Empty failed.", "err"); return; }
  showBanner(`🗑 Permanently deleted ${data.removed} file(s), freed ${human(data.freed_bytes)}.`, "ok");
  refreshQuarantine(data.quarantine);
});

refreshQuarantine(window.QUARANTINE);
