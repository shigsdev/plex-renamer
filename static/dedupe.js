/* Duplicate Cleanup — front-end. Vanilla JS, safe DOM building (no innerHTML).
   Each group: pick the keeper (radio, pre-set to the suggestion) and opt the
   group in for removal (checkbox). Nothing is removed without per-group approval.
   "Move to Recycle Bin" streams progress like the renamer's apply. */

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

/* ---- scan ---- */
$("#scan").addEventListener("click", async () => {
  const directory = $("#dir").value.trim();
  if (!directory) { setStatus("Enter at least one folder."); return; }
  setStatus("Scanning for duplicates… (walks every drive — can take a minute)", true);
  const { ok, data } = await postJSON("/api/dedupe/scan", { directory });
  if (!ok) { setStatus(data.error || "Scan failed."); return; }
  GROUPS = data.groups.map((g) => ({ ...g, _keep: g.suggested_keep, _approved: false }));
  renderSummary(data.summary);
  renderGroups();
  setStatus(`Found ${data.summary.groups} duplicate episode(s).`);
  $("#summary").classList.remove("hidden");
  $("#controls").classList.remove("hidden");
});

function renderSummary(s) {
  $("#summary").replaceChildren(
    h("span", { class: "pill guess", text: `${s.groups} duplicate episodes` }),
    h("span", { class: "pill skip", text: `${s.copies} copies total` }),
    h("span", { class: "pill date", text: `${human(s.reclaimable_bytes)} reclaimable` }),
  );
}

/* ---- render groups, grouped by show ---- */
function renderGroups() {
  const container = $("#results");
  container.replaceChildren();
  const byShow = new Map();
  for (const g of GROUPS) {
    if (!byShow.has(g.show)) byShow.set(g.show, []);
    byShow.get(g.show).push(g);
  }
  for (const [show, groups] of [...byShow.entries()].sort()) {
    const details = h("details", { open: "" }, [
      h("summary", {}, [
        h("strong", { text: show }),
        h("span", { class: "muted", text: ` ${groups.length} duplicated episode(s) · ${groups[0].library}` }),
      ]),
    ]);
    for (const g of groups) details.appendChild(buildGroup(g));
    container.appendChild(details);
  }
  updateApproved();
}

function buildGroup(g) {
  const box = h("div", { class: "dupgroup" });
  const head = h("label", { class: "dupapprove" }, [
    (() => {
      const cb = h("input", { type: "checkbox" });
      cb.checked = g._approved;
      cb.disabled = !window.TRASH_OK;
      cb.addEventListener("change", () => { g._approved = cb.checked; box.classList.toggle("approved", cb.checked); updateApproved(); });
      return cb;
    })(),
    h("span", { class: "se", text: g.se }),
    h("span", { class: "muted", text: " — approve removal of the non-kept copies" }),
  ]);
  box.appendChild(head);

  const name = `keep-${g.show}-${g.se}`.replace(/[^a-z0-9]/gi, "_");
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
      h("span", { class: "fname", text: c.filename }),
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
      else if (mode === "suggested") { g._keep = g.suggested_keep; g._approved = window.TRASH_OK; }
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
  $("#ptext").textContent = `Moving ${i} of ${total} to Recycle Bin… (${pct}%)`;
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
  if (!confirm(`Move ${items.length} duplicate file(s) to the Recycle Bin? You can restore them from there.`)) return;

  $("#banner").classList.add("hidden");
  $("#remove").disabled = true;
  showProgress(0, items.length);
  setStatus("Removing…", true);

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
  $("#ptext").textContent = `Finished — ${final.removed} of ${items.length} moved.`;
  let msg = `✓ Done. Moved ${final.removed} file(s) to the Recycle Bin, freeing ${human(final.freed_bytes)}.`;
  if (final.failed.length) msg += `  ${final.failed.length} failed (${final.failed.map((f) => f.error).join(", ")}).`;
  msg += "  Restore any from the Recycle Bin if needed.";
  showBanner(msg, final.failed.length ? "err" : "ok");

  // drop the copies that were removed; if a group has 1 copy left, it's resolved
  const removed = new Set(items.map((i) => i.path));
  for (const g of GROUPS) g.copies = g.copies.filter((c) => !removed.has(c.path));
  GROUPS = GROUPS.filter((g) => g.copies.length > 1);
  renderGroups();
});
