"""
Renders a Project's dependency graph into a self-contained, interactive HTML report:
a lazily-expanding tree (stays responsive regardless of project size, unlike a fully-rendered
Mermaid graph) plus a "Part 1 / Part 2" entry-point picker - checking a resource in the tree
computes the full reachability closure from it, so you can see exactly what a fast-load bundle
rooted at e.g. a sign-in scene would need to include.
"""

import json
import os
from typing import Any, Dict

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>GD Orphans - Dependency Tree</title>
<style>
:root {
  --bg: #ffffff; --fg: #1a1a1a; --muted: #666666; --line: #dddddd;
  --accent: #2563eb; --panel-bg: #f5f5f7; --chip-bg: #e5e7eb;
  --root-bg: #16a34a22; --root-border: #16a34a; --included-bg: #2563eb14;
  --hover-bg: #00000008; --divider-bg: #f59e0b22; --divider-fg: #92400e;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1a1a1c; --fg: #e6e6e6; --muted: #9a9a9a; --line: #3a3a3e;
    --accent: #60a5fa; --panel-bg: #232326; --chip-bg: #3a3a3e;
    --root-bg: #22c55e33; --root-border: #22c55e; --included-bg: #60a5fa22;
    --hover-bg: #ffffff0c; --divider-bg: #f59e0b2e; --divider-fg: #fbbf24;
  }
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body {
  display: flex; flex-direction: column;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px; background: var(--bg); color: var(--fg);
}
#topbar { flex: 0 0 auto; padding: 10px 18px; border-bottom: 1px solid var(--line); }
#topbar h1 { font-size: 15px; margin: 0 0 2px 0; }
#topbar .meta { color: var(--muted); font-size: 12px; }
#layout { flex: 1 1 auto; display: flex; min-height: 0; }
#sidebar {
  flex: 0 0 360px; overflow-y: auto; padding: 14px; border-right: 1px solid var(--line);
  background: var(--panel-bg);
}
#main { flex: 1 1 auto; overflow-y: auto; padding: 10px 22px 40px 22px; }
h2.side-title { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 18px 0 8px 0; }
h2.side-title:first-child { margin-top: 0; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 10px; font-size: 12px; color: var(--muted); }
input[type=text], select {
  width: 100%; padding: 6px 8px; font-family: inherit; font-size: 13px;
  background: var(--bg); color: var(--fg); border: 1px solid var(--line); border-radius: 6px;
}
select { margin-top: 6px; }
.stat-grid { display: grid; grid-template-columns: 1fr auto; gap: 3px 8px; font-size: 12px; }
.stat-grid .label { color: var(--muted); }
.stat-grid .value { text-align: right; font-weight: 600; }
a.link { color: var(--accent); cursor: pointer; text-decoration: none; }
a.link:hover { text-decoration: underline; }
.muted { color: var(--muted); font-size: 12px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.chip {
  background: var(--chip-bg); border-radius: 12px; padding: 3px 8px; font-size: 12px;
  display: inline-flex; align-items: center; gap: 6px;
}
.chip-remove { cursor: pointer; color: var(--muted); font-weight: bold; }
.chip-remove:hover { color: var(--fg); }
button {
  font-family: inherit; font-size: 12px; padding: 6px 10px; border-radius: 6px;
  border: 1px solid var(--line); background: var(--bg); color: var(--fg); cursor: pointer;
}
button:hover { background: var(--hover-bg); }
.btn-row { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
textarea#part1-textarea {
  width: 100%; height: 140px; margin-top: 8px; font-family: inherit; font-size: 11px;
  background: var(--bg); color: var(--fg); border: 1px solid var(--line); border-radius: 6px;
}
ul.children { list-style: none; margin: 0; padding-left: 1.15rem; border-left: 1px dotted var(--line); }
ul.root-children { padding-left: 0; border-left: none; }
li.node { position: relative; }
.row {
  display: flex; align-items: center; gap: 6px; padding: 2px 4px; border-radius: 4px;
  cursor: default; white-space: nowrap;
}
.row:hover { background: var(--hover-bg); }
.row.is-root { background: var(--root-bg); box-shadow: inset 2px 0 0 var(--root-border); }
.row.is-included { background: var(--included-bg); }
.toggle { width: 14px; display: inline-block; text-align: center; color: var(--muted); cursor: pointer; user-select: none; transition: transform .1s; }
.toggle.expanded { transform: rotate(90deg); }
.toggle.leaf { cursor: default; opacity: .4; }
.icon { width: 1.3em; text-align: center; }
.name { font-weight: 500; }
.type { color: var(--muted); font-size: 11px; }
.size { color: var(--muted); font-size: 11px; min-width: 4.5em; text-align: right; }
.cumsize { color: var(--fg); font-weight: 600; font-size: 11px; min-width: 5em; text-align: right; }
.usedby { color: var(--accent); font-size: 11px; cursor: default; }
li.node.cyclic .row, li.node.missing .row { color: var(--muted); font-style: italic; }
.section-divider {
  margin: 18px 0 8px 0; padding: 6px 10px; border-radius: 6px;
  background: var(--divider-bg); color: var(--divider-fg); font-weight: 600; font-size: 12.5px;
}
</style>
</head>
<body>
<header id="topbar">
  <h1>GD Orphans — Dependency Tree</h1>
  <div class="meta">Generated <span id="meta-generated"></span> from <span id="meta-path"></span></div>
</header>
<div id="layout">
  <aside id="sidebar">
    <h2 class="side-title">Legend</h2>
    <div class="legend" id="legend"></div>

    <h2 class="side-title">Find resources</h2>
    <input type="text" id="search-input" placeholder="Search by name or path…">
    <select id="type-filter"><option value="">All types</option></select>

    <h2 class="side-title">Part 1 — fast-load bundle</h2>
    <div class="muted">Check resources in the tree (or search results) to mark them as entry points. The closure of everything they depend on is computed below.</div>
    <div class="chips" id="part1-chips"></div>
    <div class="stat-grid">
      <div class="label">Part 1 files</div><div class="value"><span id="part1-count">0</span></div>
      <div class="label">Part 1 size</div><div class="value"><span id="part1-size">0 B</span></div>
      <div class="label">Part 2 (remaining) files</div><div class="value"><span id="part2-count">0</span></div>
      <div class="label">Part 2 (remaining) size</div><div class="value"><span id="part2-size">0 B</span></div>
    </div>
    <div class="btn-row">
      <button id="btn-download">Download Part 1 list (.txt)</button>
      <button id="btn-show-list">Show / copy list</button>
    </div>
    <textarea id="part1-textarea" readonly hidden></textarea>

    <h2 class="side-title">Project stats</h2>
    <div class="stat-grid">
      <div class="label">Total resources</div><div class="value"><span id="stat-total-count">0</span></div>
      <div class="label">Total size</div><div class="value"><span id="stat-total-size">0 B</span></div>
      <div class="label">Reachable from project</div><div class="value"><span id="stat-reachable-count">0</span></div>
      <div class="label">Reachable size</div><div class="value"><span id="stat-reachable-size">0 B</span></div>
      <div class="label"><a class="link" id="link-orphans">Unreferenced (orphans)</a></div><div class="value"><span id="stat-orphan-count">0</span></div>
      <div class="label">Orphan size</div><div class="value"><span id="stat-orphan-size">0 B</span></div>
    </div>
  </aside>
  <main id="main">
    <div id="tree-root"></div>
    <div id="search-results" hidden></div>
  </main>
</div>
<script>
const DATA = __DATA_JSON__;
const resources = DATA.resources;
const uids = Object.keys(resources);

const TYPE_ORDER = ["scene","script","resource","image","3D model","font","sound","shader",
  "material","translations","config","GDExtension","baked lightmap","DialogCollection",
  "binary?","Project"];
function typeRank(t) { const i = TYPE_ORDER.indexOf(t); return i === -1 ? TYPE_ORDER.length : i; }

const ICONS = {
  scene: "🎬", script: "📜", resource: "🧩", image: "🖼️", "3D model": "🧊",
  font: "🔤", sound: "🔊", shader: "🎨", material: "🧵", translations: "🌐",
  config: "⚙️", GDExtension: "🔌", "baked lightmap": "💡", DialogCollection: "💬",
  "binary?": "📦", Project: "🗂️",
};
function iconFor(t) { return ICONS[t] || "❔"; }

function byTypeThenName(a, b) {
  const ra = resources[a], rb = resources[b];
  const tr = typeRank(ra.type) - typeRank(rb.type);
  if (tr !== 0) return tr;
  return ra.name.localeCompare(rb.name);
}

function formatBytes(n) {
  if (n < 1000) return n.toFixed(0) + " B";
  if (n < 1e6) return (n / 1e3).toFixed(2) + " KB";
  if (n < 1e9) return (n / 1e6).toFixed(2) + " MB";
  return (n / 1e9).toFixed(2) + " GB";
}
function sumSize(uidIterable) {
  let total = 0;
  for (const uid of uidIterable) { const s = resources[uid] && resources[uid].size; if (s) total += s; }
  return total;
}

const referencedBy = {};
for (const uid of uids) referencedBy[uid] = 0;
for (const uid of uids) for (const ref of resources[uid].refs) referencedBy[ref] = (referencedBy[ref] || 0) + 1;

function closureFrom(roots) {
  const seen = new Set();
  const stack = Array.from(roots);
  while (stack.length) {
    const uid = stack.pop();
    if (seen.has(uid)) continue;
    seen.add(uid);
    const r = resources[uid];
    if (!r) continue;
    for (const ref of r.refs) if (!seen.has(ref)) stack.push(ref);
  }
  return seen;
}

const reachable = closureFrom([DATA.rootUid]);

const cumulativeSizeCache = new Map();
function cumulativeSize(uid) {
  // Total size of this resource plus everything it transitively depends on, deduplicated -
  // e.g. a tiny .tscn that pulls in a large mesh should show the mesh's weight, not just its own.
  if (cumulativeSizeCache.has(uid)) return cumulativeSizeCache.get(uid);
  const total = sumSize(closureFrom([uid]));
  cumulativeSizeCache.set(uid, total);
  return total;
}

const orphanClusterSizeCache = new Map();
function orphanClusterSize(uid) {
  // Same idea as cumulativeSize, but confined to other unreferenced resources: an orphan's
  // dependency closure often touches something still used by the live project (a shared
  // script, a common material), and once it does, a plain reachability walk would balloon
  // into that whole live subgraph. None of those bytes would actually be freed by deleting
  // this orphan, so don't walk past (or count) anything that's still reachable from the project.
  if (orphanClusterSizeCache.has(uid)) return orphanClusterSizeCache.get(uid);
  const seen = new Set();
  const stack = [uid];
  while (stack.length) {
    const cur = stack.pop();
    if (seen.has(cur) || reachable.has(cur)) continue;
    seen.add(cur);
    const r = resources[cur];
    if (!r) continue;
    for (const ref of r.refs) if (!seen.has(ref) && !reachable.has(ref)) stack.push(ref);
  }
  const total = sumSize(seen);
  orphanClusterSizeCache.set(uid, total);
  return total;
}
function byOrphanClusterSizeDesc(a, b) { return orphanClusterSize(b) - orphanClusterSize(a); }

const orphanUids = uids.filter(u => !reachable.has(u) && resources[u].size != null).sort(byOrphanClusterSizeDesc);

const part1Roots = new Set();
const nodeRegistry = new Map(); // uid -> Set<row element>
let part1Closure = new Set();

function h(tag, attrs, ...children) {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}

function applyHighlight(uid, row, checkbox) {
  const isRoot = part1Roots.has(uid);
  const isIncluded = part1Closure.has(uid);
  row.classList.toggle("is-root", isRoot);
  row.classList.toggle("is-included", !isRoot && isIncluded);
  if (checkbox) checkbox.checked = isRoot;
}

function renderNode(uid, ancestors) {
  const li = h("li", { class: "node" });
  const res = resources[uid];

  if (!res) {
    li.classList.add("missing");
    li.append(h("div", { class: "row" }, h("span", { class: "icon", text: "❔" }), h("span", { class: "name", text: "missing: " + uid })));
    return li;
  }
  if (ancestors.includes(uid)) {
    li.classList.add("cyclic");
    li.append(h("div", { class: "row" }, h("span", { class: "icon", text: "↩" }), h("span", { class: "name", text: res.name + " (cyclic reference)" })));
    return li;
  }

  const hasChildren = res.refs.length > 0;
  const toggle = h("span", { class: "toggle" + (hasChildren ? "" : " leaf"), text: hasChildren ? "▶" : "•" });
  const checkbox = h("input", { type: "checkbox" });
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) part1Roots.add(uid); else part1Roots.delete(uid);
    refreshPart1();
  });

  const row = h("div", { class: "row", title: uid + "\n" + res.path },
    toggle, checkbox,
    h("span", { class: "icon", text: iconFor(res.type) }),
    h("span", { class: "name", text: res.name }),
    h("span", { class: "type", text: res.type }),
    h("span", { class: "size", text: res.size != null ? formatBytes(res.size) : "—" }),
  );
  if (hasChildren) {
    const isOrphan = !reachable.has(uid);
    const total = isOrphan ? orphanClusterSize(uid) : cumulativeSize(uid);
    const tooltip = isOrphan
      ? "Total size of this resource plus dependencies that are ALSO unreferenced from project.godot (excludes anything still used elsewhere in the project)"
      : "Total size of this resource plus everything it transitively depends on (deduplicated)";
    row.append(h("span", { class: "cumsize", text: "Σ " + formatBytes(total), title: tooltip }));
  }
  const usedBy = referencedBy[uid] || 0;
  if (usedBy > 0) row.append(h("span", { class: "usedby", text: "⤴" + usedBy, title: "Referenced by " + usedBy + " other resource(s) in the project" }));

  const childrenEl = h("ul", { class: "children" });
  childrenEl.hidden = true;
  li.append(row, childrenEl);

  if (hasChildren) {
    const doToggle = () => {
      if (li.dataset.built !== "1") {
        const sorted = res.refs.slice().sort(byTypeThenName);
        for (const child of sorted) childrenEl.append(renderNode(child, ancestors.concat([uid])));
        li.dataset.built = "1";
      }
      childrenEl.hidden = !childrenEl.hidden;
      toggle.classList.toggle("expanded", !childrenEl.hidden);
    };
    toggle.addEventListener("click", doToggle);
    row.addEventListener("click", (e) => {
      if (e.target === checkbox || e.target === toggle) return;
      doToggle();
    });
  }

  if (!nodeRegistry.has(uid)) nodeRegistry.set(uid, new Set());
  nodeRegistry.get(uid).add(row);
  applyHighlight(uid, row, checkbox);

  return li;
}

function refreshPart1() {
  part1Closure = part1Roots.size ? closureFrom(part1Roots) : new Set();
  for (const [uid, rows] of nodeRegistry) {
    for (const row of rows) applyHighlight(uid, row, row.querySelector("input[type=checkbox]"));
  }
  renderPart1Summary();
}

function part1PathList() {
  return Array.from(part1Closure).map(u => resources[u].path).sort().join("\n");
}

function renderPart1Summary() {
  const chipsEl = document.getElementById("part1-chips");
  chipsEl.innerHTML = "";
  if (part1Roots.size === 0) {
    chipsEl.append(h("div", { class: "muted", text: "No entry points selected yet." }));
  } else {
    for (const uid of part1Roots) {
      const res = resources[uid];
      const chip = h("span", { class: "chip" }, iconFor(res.type) + " " + res.name);
      const x = h("span", { class: "chip-remove", text: "×" });
      x.addEventListener("click", () => { part1Roots.delete(uid); refreshPart1(); });
      chip.append(x);
      chipsEl.append(chip);
    }
  }
  document.getElementById("part1-count").textContent = part1Closure.size;
  document.getElementById("part1-size").textContent = formatBytes(sumSize(part1Closure));
  const remaining = Array.from(reachable).filter(u => !part1Closure.has(u));
  document.getElementById("part2-count").textContent = remaining.length;
  document.getElementById("part2-size").textContent = formatBytes(sumSize(remaining));
  const ta = document.getElementById("part1-textarea");
  if (!ta.hidden) ta.value = part1PathList();
}

function renderMainTree() {
  const container = document.getElementById("tree-root");
  container.innerHTML = "";
  const rootUl = h("ul", { class: "children root-children" });
  const rootLi = renderNode(DATA.rootUid, []);
  rootUl.append(rootLi);
  container.append(rootUl);
  const rootToggle = rootLi.querySelector(".row .toggle");
  if (rootToggle) rootToggle.click();

  if (orphanUids.length) {
    container.append(h("div", { class: "section-divider" },
      "⚠ Unreferenced resources (" + orphanUids.length + ", " + formatBytes(sumSize(orphanUids)) + ") — not reachable from project.godot, sorted by total size (largest first)"));
    const orphanUl = h("ul", { class: "children root-children" });
    for (const uid of orphanUids) orphanUl.append(renderNode(uid, []));
    container.append(orphanUl);
  }
}

const searchInput = document.getElementById("search-input");
const typeSelect = document.getElementById("type-filter");
const allTypes = Array.from(new Set(uids.map(u => resources[u].type))).sort((a, b) => typeRank(a) - typeRank(b));
for (const t of allTypes) typeSelect.append(h("option", { value: t, text: iconFor(t) + " " + t }));

function updateSearch() {
  const q = searchInput.value.trim().toLowerCase();
  const typeFilter = typeSelect.value;
  const mainTreeEl = document.getElementById("tree-root");
  const searchEl = document.getElementById("search-results");
  if (!q && !typeFilter) {
    searchEl.hidden = true;
    mainTreeEl.hidden = false;
    return;
  }
  mainTreeEl.hidden = true;
  searchEl.hidden = false;
  searchEl.innerHTML = "";
  const matches = uids.filter(u => {
    const r = resources[u];
    if (typeFilter && r.type !== typeFilter) return false;
    if (q && !r.name.toLowerCase().includes(q) && !r.path.toLowerCase().includes(q)) return false;
    return true;
  }).sort(byTypeThenName);
  const MAX = 300;
  searchEl.append(h("div", { class: "muted", text: matches.length + " match(es)" + (matches.length > MAX ? (" — showing first " + MAX) : "") }));
  const ul = h("ul", { class: "children root-children" });
  for (const uid of matches.slice(0, MAX)) ul.append(renderNode(uid, []));
  searchEl.append(ul);
}
searchInput.addEventListener("input", updateSearch);
typeSelect.addEventListener("change", updateSearch);

document.getElementById("link-orphans").addEventListener("click", (e) => {
  e.preventDefault();
  searchInput.value = "";
  typeSelect.value = "";
  updateSearch();
  const divider = document.querySelector(".section-divider");
  if (divider) divider.scrollIntoView({ behavior: "smooth", block: "start" });
});

document.getElementById("btn-download").addEventListener("click", () => {
  const blob = new Blob([part1PathList()], { type: "text/plain" });
  const a = h("a", { href: URL.createObjectURL(blob), download: "part1_files.txt" });
  document.body.append(a);
  a.click();
  a.remove();
});
document.getElementById("btn-show-list").addEventListener("click", () => {
  const ta = document.getElementById("part1-textarea");
  ta.hidden = !ta.hidden;
  if (!ta.hidden) { ta.value = part1PathList(); ta.focus(); ta.select(); }
});

document.getElementById("legend").append(...allTypes.map(t => h("span", {}, iconFor(t) + " " + t)));
document.getElementById("meta-generated").textContent = DATA.generatedAt;
document.getElementById("meta-path").textContent = DATA.projectPath;
document.getElementById("stat-total-count").textContent = uids.length;
document.getElementById("stat-total-size").textContent = formatBytes(sumSize(uids));
document.getElementById("stat-reachable-count").textContent = reachable.size;
document.getElementById("stat-reachable-size").textContent = formatBytes(sumSize(reachable));
document.getElementById("stat-orphan-count").textContent = orphanUids.length;
document.getElementById("stat-orphan-size").textContent = formatBytes(sumSize(orphanUids));

renderMainTree();
renderPart1Summary();
</script>
</body>
</html>
"""


def write_html_report(data: Dict[str, Any], html_path: str) -> None:
    """
    Renders `data` (generatedAt/projectPath/rootUid/resources, as built by
    `Project.generate_html_report`) into HTML_TEMPLATE and writes it to `html_path`.
    """
    # `</` can't appear inside embedded JSON or the browser would parse it as the closing
    # </script> tag - escape it the same way JSON.stringify's callers commonly do.
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__DATA_JSON__", payload)

    dirpath = os.path.dirname(html_path)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)
    with open(html_path, "w") as f:
        f.write(html)
