/* Duplicate Cleanup — front-end. Vanilla JS, safe DOM building (no innerHTML).
   Each group: pick the keeper (radio, pre-set to the suggestion) and opt the
   group in for removal (checkbox). Nothing is removed without per-group approval.
   Removal quarantines copies (reversible); streams progress like apply. */

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

/* ---- drive picker ---- */
function selectedPaths() {
  const picked = [...document.querySelectorAll(".drivepick input.libcb:checked")]
    .map((c) => c.dataset.path)
    .filter(Boolean);
  const manual = ($("#dir")?.value || "").split(/[\n;]+/).map((s) => s.trim().replace(/^"|"$/g, "")).filter(Boolean);
  return [...new Set([...picked, ...manual])];
}

function driveCard(d) {
  const libCbs = d.libraries.map((lib) => {
    const cb = h("input", { type: "checkbox", class: "libcb", "data-path": lib.path });
    cb.addEventListener("change", syncDriveChecks);
    return h("label", { class: "libitem" }, [cb, h("span", { text: lib.name })]);
  });
  const driveCb = h("input", { type: "checkbox", class: "drivecb" });
  driveCb.addEventListener("change", () => {
    libCbs.forEach((l) => { l.querySelector("input").checked = driveCb.checked; });
  });
  const n = d.libraries.length;
  return h("div", { class: "drivecard" }, [
    h("label", { class: "drivehdr" }, [
      driveCb,
      h("strong", { text: `${d.drive}:` }),
      h("span", { class: "muted", text: ` ${n} librar${n === 1 ? "y" : "ies"}` }),
    ]),
    h("div", { class: "libs" }, libCbs),
  ]);
}

// keep each drive's master checkbox in sync with its library checkboxes
function syncDriveChecks() {
  document.querySelectorAll(".drivecard").forEach((card) => {
    const libs = [...card.querySelectorAll("input.libcb")];
    const master = card.querySelector("input.drivecb");
    const on = libs.filter((c) => c.checked).length;
    master.checked = on === libs.length && libs.length > 0;
    master.indeterminate = on > 0 && on < libs.length;
  });
}

async function loadDrives() {
  const box = $("#drivepick");
  let drives = [];
  try {
    const r = await fetch("/api/dedupe/drives");
    drives = (await r.json()).drives || [];
  } catch (e) {
    box.replaceChildren(h("span", { class: "status", text: "Could not detect drives — paste paths below." }));
    return;
  }
  drives = drives.filter((d) => d.libraries.length);
  if (!drives.length) {
    box.replaceChildren(h("span", { class: "status", text: "No TV/Movie library folders found — paste paths below." }));
    return;
  }
  const selAll = h("button", { class: "mini ghost", type: "button" });
  selAll.textContent = "Select all";
  selAll.addEventListener("click", () => {
    box.querySelectorAll("input[type=checkbox]").forEach((c) => { c.checked = true; });
    syncDriveChecks();
  });
  box.replaceChildren(
    h("div", { class: "pickhead" }, [
      h("strong", { text: "Pick drives / libraries to compare:" }), selAll,
    ]),
    h("div", { class: "drivegrid" }, drives.map(driveCard)),
  );
}

/* ---- scan ---- */
$("#scan").addEventListener("click", async () => {
  const paths = selectedPaths();
  if (!paths.length) { setStatus("Pick at least one drive or library above (or paste a path)."); return; }
  setStatus("Scanning for duplicates… (walks every drive — can take a minute)", true);
  const crossOnly = $("#crossonly")?.checked || false;
  const { ok, data } = await postJSON("/api/dedupe/scan",
    { directory: paths.join("\n"), cross_drive_only: crossOnly });
  if (!ok) { setStatus(data.error || "Scan failed."); return; }
  GROUPS = data.groups.map((g) => ({ ...g, _keep: g.suggested_keep, _approved: false }));
  renderSummary(data.summary);
  renderGroups();
  setStatus(`Found ${data.summary.groups} duplicate item(s).`);
  $("#summary").classList.remove("hidden");
  $("#controls").classList.remove("hidden");
});

function renderSummary(s) {
  $("#summary").replaceChildren(
    h("span", { class: "pill guess", text: `${s.groups} duplicate items` }),
    h("span", { class: "pill skip", text: `${s.copies} copies total` }),
    h("span", { class: "pill date", text: `${human(s.reclaimable_bytes)} reclaimable` }),
  );
}

/* ---- render groups: TV shows (grouped by show) + Movies (one box each) ---- */
function renderGroups() {
  const container = $("#results");
  container.replaceChildren();

  // master "select all" across every duplicate group (TV + movies)
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

  const tv = GROUPS.filter((g) => g.kind !== "movie");
  const movies = GROUPS.filter((g) => g.kind === "movie");
  if (tv.length) renderTvSection(container, tv);
  if (movies.length) renderMovieSection(container, movies);
  updateApproved();
}

function renderTvSection(container, tv) {
  container.appendChild(h("h2", { class: "sechead", text: `TV Shows (${tv.length})` }));
  const byShow = new Map();
  for (const g of tv) {
    if (!byShow.has(g.show)) byShow.set(g.show, []);
    byShow.get(g.show).push(g);
  }
  for (const [show, groups] of [...byShow.entries()].sort()) {
    const groupCb = h("input", { type: "checkbox", class: "groupcheck", title: "Approve all episodes for this show" });
    const approved = groups.filter((g) => g._approved).length;
    groupCb.checked = approved === groups.length;
    groupCb.indeterminate = approved > 0 && approved < groups.length;
    groupCb.addEventListener("click", (e) => e.stopPropagation());
    groupCb.addEventListener("change", () => {
      for (const g of groups) { g._keep = g.suggested_keep; g._approved = groupCb.checked; }
      renderGroups();
    });
    const details = h("details", { open: "" }, [
      h("summary", {}, [
        groupCb,
        h("strong", { text: show }),
        h("span", { class: "muted", text: ` ${groups.length} duplicated episode(s) · ${groups[0].library}` }),
      ]),
    ]);
    for (const g of groups) details.appendChild(buildGroup(g));
    container.appendChild(details);
  }
}

function renderMovieSection(container, movies) {
  container.appendChild(h("h2", { class: "sechead", text: `Movies (${movies.length})` }));
  // Each movie is its own duplicate set — no series wrapper. One card per movie.
  for (const g of [...movies].sort((a, b) => a.show.localeCompare(b.show))) {
    container.appendChild(h("div", { class: "moviecard" }, [buildGroup(g)]));
  }
}

function buildGroup(g) {
  const box = h("div", { class: "dupgroup" });
  // TV: bold "S01E01". Movie: bold "The Matrix (1999)".
  const heading = g.kind === "movie"
    ? (g.year ? `${g.show} (${g.year})` : g.show)
    : g.se;
  const head = h("label", { class: "dupapprove" }, [
    (() => {
      const cb = h("input", { type: "checkbox" });
      cb.checked = g._approved;
      cb.addEventListener("change", () => { g._approved = cb.checked; box.classList.toggle("approved", cb.checked); updateApproved(); });
      return cb;
    })(),
    h("span", { class: "se", text: heading }),
    h("span", { class: "muted", text: " — approve removal of the non-kept copies" }),
  ]);
  box.appendChild(head);

  const name = `keep-${g.kind}-${g.show}-${g.se}`.replace(/[^a-z0-9]/gi, "_");
  g.copies.forEach((c, i) => {
    const radio = h("input", { type: "radio", name });
    radio.checked = i === g._keep;
    radio.addEventListener("change", () => { g._keep = i; updateApproved(); });
    const res = c.resolution ? `${c.resolution}p` : "?";
    const row = h("label", { class: "copyrow" }, [
      radio,
      h("span", { class: "drive", text: `[${c.drive}]` }),
      h("span", { class: "res", text: res }),
      h("span", { class: "size", text: human(c.size) }),
      h("span", { class: "fpath", text: c.path.replace(/\//g, "\\"), title: c.path }),
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

/* ---- remove (streamed) ---- */
function showProgress(i, total) {
  $("#progress").classList.remove("hidden");
  const pct = total ? Math.round((i / total) * 100) : 0;
  $("#pfill").style.width = pct + "%";
  $("#ptext").textContent = `Quarantining ${i} of ${total}… (${pct}%)`;
}

function showBanner(text, kind) {
  const b = $("#banner");
  b.className = `banner ${kind}`;
  b.textContent = text;
  b.classList.remove("hidden");
  b.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("#remove").addEventListener("click", async () => {
  const items = removalItems();
  if (!items.length) { setStatus("No groups approved."); return; }
  if (!confirm(`Move ${items.length} duplicate file(s) to quarantine? This is reversible — use "Undo last removal" or restore later.`)) return;

  $("#banner").classList.add("hidden");
  $("#remove").disabled = true;
  showProgress(0, items.length);
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
        if (ev.done) final = ev; else showProgress(ev.i, ev.total);
      }
    }
  } catch (e) {
    setStatus("");
    showBanner(`Removal failed: ${e.message}`, "err");
    $("#remove").disabled = false;
    return;
  }

  $("#remove").disabled = false;
  setStatus("");
  $("#pfill").style.width = "100%";
  $("#ptext").textContent = `Finished — ${final.removed} of ${items.length} quarantined.`;
  let msg = `✓ Done. Quarantined ${final.removed} file(s) (${human(final.moved_bytes)}). Reversible via “Undo last removal”. Empty the quarantine to free the space.`;
  if (final.failed.length) msg += `  ${final.failed.length} failed (${final.failed.map((f) => f.error).join(", ")}).`;
  showBanner(msg, final.failed.length ? "err" : "ok");

  // drop the copies that were removed; if a group has 1 copy left, it's resolved
  const removed = new Set(items.map((i) => i.path));
  for (const g of GROUPS) g.copies = g.copies.filter((c) => !removed.has(c.path));
  GROUPS = GROUPS.filter((g) => g.copies.length > 1);
  renderGroups();
  refreshQuarantine(final.removed
    ? { files: (window.QUARANTINE.files || 0) + final.removed, bytes: (window.QUARANTINE.bytes || 0) + final.moved_bytes }
    : window.QUARANTINE);
});

/* ---- quarantine status + undo + empty ---- */
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
loadDrives();
