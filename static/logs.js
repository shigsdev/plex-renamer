/* Activity Log — read-only view of undo_logs/. Vanilla JS, safe DOM building. */

const $ = (s) => document.querySelector(s);

function h(tag, attrs = {}, kids = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const kid of [].concat(kids))
    if (kid != null) node.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
  return node;
}

function human(n) {
  if (!n) return "";
  for (const u of ["B", "KB", "MB", "GB", "TB"]) {
    if (n < 1024) return `${Math.round(n)}${u}`;
    n /= 1024;
  }
  return `${n.toFixed(1)}PB`;
}

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString();
}

const STATUS_CLASS = {
  active: "high", undone: "skip", restored: "skip", emptied: "guess",
};

async function load() {
  $("#status").textContent = "Loading…";
  const r = await fetch("/api/logs");
  const data = await r.json();
  render(data);
  $("#status").textContent = "";
}

function render(data) {
  const renames = data.batches.filter((b) => b.type === "rename");
  const removals = data.batches.filter((b) => b.type === "removal");
  $("#summary").replaceChildren(
    h("span", { class: "pill high", text: `${renames.length} rename batches` }),
    h("span", { class: "pill date", text: `${removals.length} removal batches` }),
    h("span", { class: "pill skip", text: `${data.master.total_renames} total renames logged` }),
  );

  const container = $("#results");
  container.replaceChildren();
  if (!data.batches.length) {
    container.appendChild(h("p", { class: "muted", text: "No activity yet." }));
    return;
  }
  for (const b of data.batches) container.appendChild(batchEl(b));
}

function batchEl(b) {
  const verb = b.type === "rename" ? "renamed" : "quarantined";
  const meta = [`${b.count} ${verb}`, b.size ? human(b.size) : "", when(b.when)]
    .filter(Boolean).join(" · ");
  const details = h("details", {}, [
    h("summary", {}, [
      h("span", { class: `badge ${STATUS_CLASS[b.status] || "skip"}`, text: b.status }),
      h("strong", { text: ` ${b.id} ` }),
      h("span", { class: "muted", text: meta }),
    ]),
  ]);
  const table = h("table");
  const tbody = h("tbody");
  for (const e of b.entries) {
    tbody.appendChild(h("tr", {}, [
      h("td", { class: "c-old", text: e.from }),
      h("td", { text: "→" }),
      h("td", { class: "c-new-cell", text: e.to }),
      h("td", { class: "size", text: human(e.size) }),
    ]));
  }
  table.appendChild(tbody);
  details.appendChild(table);
  return details;
}

$("#refresh").addEventListener("click", load);
load();
