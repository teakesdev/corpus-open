// Matter Kit local UI. Every bit of case text is set with textContent; nothing is parsed as HTML.
// The server checks the token, the host and who may do what; this file only draws and asks.

const TOKEN_KEY = "matterkit-token";
const LABELS = [
  ["DOCUMENTED FACT", "Documented fact", "A record in this case shows it. Pick the document and the page."],
  ["ALLEGATION", "Allegation", "Someone says it (a complaint, a letter, a witness), but no record shows it yet."],
  ["INFERENCE", "Inference", "You worked it out from documented facts. Say which ones."],
  ["HYPOTHESIS", "Hypothesis", "A theory about motive or cause. Keep it out of anything you send until a record supports it."],
  ["UNKNOWN", "Unknown", "Something you don't know yet. Writing it down beats assuming."],
];
const LABEL_NAME = Object.fromEntries(LABELS.map(([k, name]) => [k, name]));
const TABS = [
  ["overview", "Overview"], ["documents", "Documents"], ["facts", "Facts"],
  ["deadlines", "Deadlines"], ["brief", "Brief check"], ["packet", "Packet"],
];
const LAYER_NAMES = { "30s": "30-second view", "3min": "3-minute view", "15min": "15-minute view" };

let token = null;
const state = { session: null, cases: [], caseId: null, tab: "overview", layer: "3min", factFilter: "all" };
const $ = (id) => document.getElementById(id);

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

// --- DOM: build nodes from parts; strings always become text ---
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
const clear = (n) => { n.replaceChildren(); return n; };

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.textContent = ""; }, 5000);
}

function storage(fn) { try { return fn(window.sessionStorage); } catch { return null; } }

async function api(method, path, body, raw) {
  const headers = { "X-Matter-Token": token };
  let payload;
  if (raw !== undefined) payload = raw;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let r;
  try {
    r = await fetch("/api/v0" + path, { method, headers, body: payload, credentials: "omit", cache: "no-store" });
  } catch {
    throw new ApiError(0, "Matter Kit isn't answering. Is the terminal window that started it still open?");
  }
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) throw new ApiError(r.status, (data && data.error) || `Matter Kit answered with error ${r.status}.`);
  return data;
}

// --- small formatters ---
function fmtDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m) return iso || "";
  return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
function daysFromToday(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((new Date(+m[1], +m[2] - 1, +m[3]) - today) / 86400000);
}
function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
function who(createdBy) {
  if (!createdBy) return "not recorded";
  if (createdBy.startsWith("human:")) return createdBy.slice(6);
  return createdBy.replace("; directed-by:", ", directed by ");
}
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const VERIFICATION = {
  "verified-official": "checked against an official source",
  "secondary-source": "from a secondary source",
  "unverified": "not checked",
};

// --- a small Markdown reader for the packet and brief-check report: headings, lists, quotes,
// bold, code and tables. It builds nodes; it never hands a string to an HTML parser. ---
function inline(s) {
  const out = [];
  // _italic_ only at word edges, so file names such as incident_report.md stay whole
  for (const part of s.split(/(\*\*[^*]+\*\*|`[^`]+`|\[UNSUPPORTED\]|(?<!\w)_(?=\S).+?(?<=\S)_(?!\w))/)) {
    if (!part) continue;
    const bold = /^\*\*([^*]+)\*\*$/.exec(part);
    const code = /^`([^`]+)`$/.exec(part);
    const em = /^_(.+)_$/.exec(part);
    const text = bold ? bold[1] : part;
    if (text === "[UNSUPPORTED]") out.push(el("span", { class: "nosource", text: "No source" }));
    else if (bold) out.push(el("strong", { text }));
    else if (code) out.push(el("code", { text: code[1] }));
    else if (em) out.push(el("em", { text: em[1] }));
    else out.push(text);
  }
  return out;
}
function mdTable(rows) {
  const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const body = rows.slice(1).filter((r) => !/^\s*\|?[\s:|-]+\|?\s*$/.test(r));  // drop the |---| rule
  return el("div", { class: "wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, cells(rows[0]).map((c) => el("th", { scope: "col" }, inline(c))))),
    el("tbody", {}, body.map((r) => el("tr", {}, cells(r).map((c) => el("td", {}, inline(c))))))));
}

function renderMarkdown(text) {
  const root = el("div", { class: "doc" });
  let list = null;
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*\|/.test(line)) {  // a run of | lines is one table
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      i--;
      list = null;
      root.append(mdTable(rows));
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    const li = /^\s*[-*]\s+(.*)$/.exec(line);
    const quote = /^>\s?(.*)$/.exec(line);
    if (li) {
      if (!list) root.append(list = el("ul"));
      list.append(el("li", {}, inline(li[1])));
      continue;
    }
    list = null;
    if (h) root.append(el(`h${h[1].length + 1}`, {}, inline(h[2])));
    else if (quote) root.append(el("blockquote", {}, inline(quote[1])));
    else if (line.trim()) root.append(el("p", {}, inline(line)));
  }
  return root;
}

// --- routing: #/ is the case list; #/case/<id>/<tab> is one case ---
function go(hash) { if (location.hash === hash) render(); else location.hash = hash; }
function route() {
  const h = location.hash;
  if (!h.startsWith("#/")) return;
  const [kind, id, tab] = h.slice(2).split("/");
  if (kind === "case" && /^[0-9a-f]{16}$/.test(id || "")) {
    state.caseId = id;
    state.tab = TABS.some(([t]) => t === tab) ? tab : "overview";
  } else {
    state.caseId = null;
  }
  render();
}
const caseHref = (id, tab = "overview") => `#/case/${id}/${tab}`;
const currentCase = () => state.cases.find((c) => c.id === state.caseId);

async function loadCases() {
  state.cases = (await api("GET", "/cases")).cases;
  renderSidebar();
}

function renderSidebar() {
  const s = state.session;
  clear($("brand")).append("Matter Kit", el("small", { text: `Folder: ${s.workspace}` }), el("small", { text: `Working as ${s.operator}` }));
  const kids = (pid) => state.cases.filter((c) => c.parent_id === pid);
  const tree = (pid) => {
    const items = kids(pid);
    if (!items.length) return null;
    return el("ul", pid ? {} : { class: "case-list" }, items.map((c) => el("li", {},
      el("a", { href: caseHref(c.id), "aria-current": c.id === state.caseId ? "page" : null, text: c.title }),
      tree(c.id))));
  };
  clear($("cases")).append(tree(null) || el("p", { class: "muted", text: "No cases yet." }));
  renderNewCase(clear($("new-case")), false);
}

function renderNewCase(box, open) {
  if (!open) {
    box.append(el("button", { class: "btn quiet", type: "button", text: "New case", onclick: () => renderNewCase(clear(box), true) }));
    return;
  }
  const input = el("input", { type: "text", id: "new-case-name", maxlength: 80, autocomplete: "off" });
  const err = el("p", { class: "error", role: "alert" });
  const form = el("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = "";
      try {
        const { case: made } = await api("POST", "/cases", { name: input.value.trim() });
        await loadCases();
        toast(`Created ${made.title}.`);
        go(caseHref(made.id));
      } catch (ex) { err.textContent = ex.message; }
    },
  },
  el("label", { for: "new-case-name", text: "Case folder name" }),
  input,
  el("p", { class: "hint", text: "For example: Smith v. City of Example. Letters, numbers, spaces and . , ( ) & ' - are fine." }),
  err,
  el("div", { class: "row" }, el("button", { class: "btn", type: "submit", text: "Create case" }),
    el("button", { class: "btn quiet", type: "button", text: "Cancel", onclick: () => renderNewCase(clear(box), false) })));
  box.append(form);
  input.focus();
}

// --- main area ---
async function render() {
  renderSidebar();
  const main = clear($("main"));
  if (!state.caseId) return renderHome(main);
  const c = currentCase();
  if (!c) {
    main.append(el("h1", { class: "center", text: "That case isn't in this workspace." }),
      el("p", {}, el("a", { href: "#/", text: "Back to the case list" })));
    return;
  }
  const parent = c.parent_id && state.cases.find((p) => p.id === c.parent_id);
  main.append(el("header", { class: "caption" },
    el("h1", { text: c.title }),
    el("p", { text: parent ? `Subcase of ${parent.title}. Folder: ${c.rel}` : `Folder: ${c.rel}` })));
  main.append(el("nav", { class: "tabs", "aria-label": "Case sections" }, TABS.map(([t, name]) =>
    el("a", { href: caseHref(c.id, t), "aria-current": t === state.tab ? "page" : null, text: name }))));
  const banner = el("div");
  const panel = el("div");
  main.append(banner, panel);
  pastDueBanner(banner, c.id);
  const views = { overview: viewOverview, documents: viewDocuments, facts: viewFacts,
                  deadlines: viewDeadlines, brief: viewBrief, packet: viewPacket };
  try {
    await views[state.tab](panel, c);
  } catch (ex) {
    panel.append(el("p", { class: "error", role: "alert", text: ex.message }));
  }
}

function renderHome(main) {
  main.append(el("header", { class: "caption" }, el("h1", { text: "Your cases" })));
  if (!state.cases.length) {
    main.append(el("p", { class: "lede", text: "There are no cases in this folder yet. Use New case to start one. Matter Kit makes a folder for it with places for filings, evidence, letters and research." }));
    return;
  }
  main.append(el("p", { class: "lede", text: "Pick a case to see what needs attention, add documents and facts, or check a draft before you file it." }),
    el("ul", { class: "attention" }, state.cases.filter((c) => !c.parent_id).map((c) => {
      const subs = state.cases.filter((s) => s.parent_id === c.id).length;
      return el("li", {}, el("a", { href: caseHref(c.id), text: c.title }),
        c.rel !== c.title ? el("span", { class: "muted", text: `Folder: ${c.rel}` }) : null,
        subs ? el("span", { class: "muted", text: plural(subs, "subcase", "subcases") }) : null);
    })));
}

async function pastDueBanner(box, caseId) {
  try {
    const { deadlines } = await api("GET", `/cases/${caseId}/deadlines`);
    const late = deadlines.filter((d) => d.overdue);
    if (!late.length || state.caseId !== caseId) return;
    box.append(el("div", { class: "pastdue", role: "alert" },
      el("strong", { text: late.length === 1 ? "A deadline has passed" : `${late.length} deadlines have passed` }),
      el("ul", {}, late.map((d) => el("li", { text: `${d.label}: was due ${fmtDate(d.due_date)}` }))),
      el("p", { class: "hint", text: "If it was met, extended or no longer applies, record that so this case's list stays accurate." })));
  } catch { /* the deadlines tab shows the error */ }
}

// --- overview: what needs attention ---
async function viewOverview(panel, c) {
  const [{ status }, { deadlines }, { facts }] = await Promise.all([
    api("GET", `/cases/${c.id}/status`), api("GET", `/cases/${c.id}/deadlines`), api("GET", `/cases/${c.id}/facts`)]);
  const next = deadlines.find((d) => !d.overdue);
  const nosource = facts.filter((f) => f.unsupported).length;
  const items = [];
  if (next) {
    const n = daysFromToday(next.due_date);
    items.push(el("li", {}, el("span", {}, el("b", { text: next.label }), ` is due ${fmtDate(next.due_date)}`,
      n != null ? ` (${n === 0 ? "today" : plural(n, "day", "days") + " from now"})` : ""),
    el("a", { href: caseHref(c.id, "deadlines"), text: "All deadlines" })));
  } else {
    items.push(el("li", {}, el("span", { class: "muted", text: "No upcoming deadlines are recorded." })));
  }
  items.push(el("li", {}, el("span", {},
    nosource ? el("span", { class: "nosource", text: plural(nosource, "fact has no source", "facts have no source") })
      : `${plural(status.assertions, "fact", "facts")}, each with a source`,
    nosource ? ` out of ${plural(status.assertions, "fact", "facts")}` : ""),
  el("a", { href: caseHref(c.id, "facts"), text: nosource ? "Find them" : "Facts" })));
  const verifyOut = el("span");
  items.push(el("li", {}, el("span", {}, `${plural(status.documents_total, "document", "documents")} registered. `, verifyOut),
    el("button", { class: "btn quiet", type: "button", text: "Check files for changes", onclick: (e) => runVerify(e.target, verifyOut, c) })));
  if (status.unverified_authorities) {
    items.push(el("li", {}, el("span", { text: `${plural(status.unverified_authorities, "cited authority hasn't", "cited authorities haven't")} been checked against a source yet` })));
  }
  panel.append(el("section", {}, el("h2", { text: "What needs attention" }), el("ul", { class: "attention" }, items)));
  const parent = c.parent_id && state.cases.find((p) => p.id === c.parent_id);
  const related = [parent ? [parent, "Main case"] : null,
    ...state.cases.filter((s) => s.parent_id === c.id).map((s) => [s, "Subcase"])].filter(Boolean);
  if (related.length) {
    panel.append(el("section", {}, el("h2", { text: "Related cases" }),
      el("ul", { class: "attention" }, related.map(([r, kind]) =>
        el("li", {}, el("a", { href: caseHref(r.id), text: r.title }), el("span", { class: "muted", text: kind }))))));
  }
}

async function runVerify(btn, out, c) {
  btn.disabled = true;
  out.className = "muted";
  out.textContent = "Checking…";
  try {
    const r = await api("POST", `/cases/${c.id}/verify`, {});
    if (!r.changed && !r.missing) {
      out.className = "done";
      out.textContent = `All ${r.ok} are unchanged since they were added.`;
    } else {
      out.className = "error";
      out.textContent = `${r.changed} changed and ${r.missing} missing: ${r.failures.map((f) => f.rel_path).join(", ")}`;
    }
  } catch (ex) { out.className = "error"; out.textContent = ex.message; }
  btn.disabled = false;
}

// --- documents ---
async function viewDocuments(panel, c) {
  const { documents } = await api("GET", `/cases/${c.id}/documents`);
  const folders = state.session.folders;
  const folder = el("select", { id: "upload-folder" }, folders.map((f) => el("option", { value: f, text: f.replace("_", " "), selected: f === "Evidence" || null })));
  const picker = el("input", { type: "file", multiple: true, id: "upload-files", hidden: true });
  const results = el("ul", { class: "uploads", "aria-live": "polite" });
  const drop = el("div", { class: "drop" },
    el("p", {}, "Drag files here, or ", el("button", { class: "btn quiet", type: "button", text: "choose files", onclick: () => picker.click() }), "."),
    el("div", { class: "row" }, el("label", { for: "upload-folder", text: "Put them in" }), folder),
    el("p", { class: "hint", text: "Matter Kit copies each file into the case folder and records a fingerprint of it, so you can later prove it hasn't changed. It never replaces a file that's already there." }),
    picker, results);
  const send = async (files) => {
    for (const f of files) {
      const line = el("li", { text: `Adding ${f.name}…` });
      results.append(line);
      try {
        const r = await api("PUT", `/cases/${c.id}/files?folder=${encodeURIComponent(folder.value)}&name=${encodeURIComponent(f.name)}`, undefined, f);
        line.className = "done";
        line.textContent = `Added ${r.rel_path}`;
      } catch (ex) { line.className = "error"; line.textContent = `${f.name}: ${ex.message}`; }
    }
    renderDocTable(table, (await api("GET", `/cases/${c.id}/documents`)).documents);
  };
  picker.addEventListener("change", () => { send([...picker.files]); picker.value = ""; });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); send([...e.dataTransfer.files]); });

  const scanOut = el("span", { "aria-live": "polite" });
  const verifyOut = el("span", { "aria-live": "polite" });
  const table = el("div", { class: "wrap" });
  panel.append(el("section", {}, el("h2", { text: "Add documents" }), drop,
    el("div", { class: "row" },
      el("button", { class: "btn quiet", type: "button", text: "Look for new files", onclick: async (e) => {
        e.target.disabled = true;
        try {
          const r = await api("POST", `/cases/${c.id}/scan`, {});
          scanOut.className = "done";
          scanOut.textContent = r.added ? `Added ${plural(r.added, "file", "files")} found in the case folder.` : "No new files found.";
          renderDocTable(table, (await api("GET", `/cases/${c.id}/documents`)).documents);
        } catch (ex) { scanOut.className = "error"; scanOut.textContent = ex.message; }
        e.target.disabled = false;
      } }), scanOut),
    el("p", { class: "hint", text: "Copied files into the case folder yourself? Look for new files registers them." })),
  el("section", {}, el("h2", { text: "Documents in this case" }),
    el("div", { class: "row" }, el("button", { class: "btn quiet", type: "button", text: "Check files for changes", onclick: (e) => runVerify(e.target, verifyOut, c) }), verifyOut),
    table));
  renderDocTable(table, documents);
}

function renderDocTable(box, documents) {
  clear(box);
  if (!documents.length) { box.append(el("p", { class: "muted", text: "No documents yet. Add the complaint, orders, letters and evidence you have." })); return; }
  box.append(el("table", {}, el("thead", {}, el("tr", {}, ["Document", "Kind", "Size"].map((h) => el("th", { scope: "col", text: h })))),
    el("tbody", {}, documents.map((d) => el("tr", {}, el("td", { text: d.rel_path }), el("td", { text: d.kind }), el("td", { class: "num", text: fmtBytes(d.bytes) }))))));
}

// --- facts ---
async function viewFacts(panel, c) {
  const [{ facts }, { documents }] = await Promise.all([api("GET", `/cases/${c.id}/facts`), api("GET", `/cases/${c.id}/documents`)]);
  panel.append(el("section", {}, el("h2", { text: "Add a fact" }), factForm(c, documents)));
  const list = el("ul", { class: "facts" });
  const filters = [["all", "All"], ["nosource", "No source yet"], ...LABELS.map(([k, name]) => [k, name])];
  const bar = el("div", { class: "segmented", role: "group", "aria-label": "Show" });
  const draw = () => {
    clear(bar).append(...filters.map(([k, name]) => el("button", { type: "button", "aria-pressed": String(state.factFilter === k), text: name,
      onclick: () => { state.factFilter = k; draw(); } })));
    const shown = facts.filter((f) => state.factFilter === "all" || (state.factFilter === "nosource" ? f.unsupported : f.status === state.factFilter));
    clear(list).append(...(shown.length ? shown.map(factItem)
      : [el("li", { class: "muted", text: facts.length ? "No facts match." : "No facts yet. Start with what a document shows." })]));
  };
  panel.append(el("section", {}, el("h2", { text: `Facts in this case (${facts.length})` }),
    el("ul", { class: "legend", "aria-label": "How sure each fact is" }, LABELS.map(([k, name]) =>
      el("li", { class: "fact", "data-label": k }, el("b", { text: name })))),
    bar, list));
  draw();
}

function factItem(f) {
  const src = f.sources.map((s) => s.rel_path + (s.locator ? `, ${s.locator}` : "")).join("; ");
  return el("li", { class: "fact", "data-label": f.status },
    el("p", { class: "fact-text", text: f.text }),
    el("p", { class: "fact-meta" }, el("span", { class: "label", text: LABEL_NAME[f.status] || f.status }),
      f.unsupported ? el("span", { class: "nosource", text: "No source yet" }) : src),
    el("p", { class: "fact-meta", text: `Added by ${who(f.created_by)}` }));
}

function factForm(c, documents) {
  const err = el("p", { class: "error", role: "alert" });
  const text = el("textarea", { id: "fact-text", required: true });
  const sources = el("div");
  const addSource = () => {
    const row = el("div", { class: "source-row" },
      el("select", { "aria-label": "Document" }, el("option", { value: "", text: "Pick a document…" }),
        documents.map((d) => el("option", { value: d.id, text: d.rel_path }))),
      el("input", { type: "text", "aria-label": "Page or place in the document", placeholder: "Page or ¶", maxlength: 200 }),
      el("button", { class: "btn quiet", type: "button", text: "Remove", onclick: () => row.remove() }));
    sources.append(row);
  };
  if (documents.length) addSource();
  const form = el("form", { class: "form-panel",
    onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = "";
      const status = form.querySelector("input[name=label]:checked");
      if (!status) { err.textContent = "Pick how sure this fact is."; return; }
      const picked = [...sources.querySelectorAll(".source-row")].map((r) => ({
        document_id: r.querySelector("select").value, locator: r.querySelector("input").value.trim() })).filter((s) => s.document_id);
      try {
        const { fact } = await api("POST", `/cases/${c.id}/facts`, { text: text.value, status: status.value, sources: picked });
        toast(fact.unsupported ? "Fact added. It has no source yet." : "Fact added.");
        render();
      } catch (ex) { err.textContent = ex.message; }
    } },
  el("div", { class: "field" }, el("label", { for: "fact-text", text: "What happened, or what is true?" }), text,
    el("p", { class: "hint", text: "One fact at a time, in plain words. Include the date and who, if you know them." })),
  el("fieldset", {}, el("legend", { text: "How sure is it?" }), LABELS.map(([k, name, help]) =>
    el("label", { class: "choice" }, el("input", { type: "radio", name: "label", value: k }), el("span", {}, el("b", { text: name }), ` ${help}`)))),
  el("fieldset", {}, el("legend", { text: "Where does it come from?" }),
    documents.length ? sources : el("p", { class: "hint", text: "Add documents to this case first; then you can point to them here." }),
    documents.length ? el("button", { class: "btn quiet", type: "button", text: "Add another source", onclick: addSource }) : null),
  err, el("button", { class: "btn", type: "submit", text: "Add fact" }));
  return form;
}

// --- deadlines (read-only in this version) ---
async function viewDeadlines(panel, c) {
  const { deadlines } = await api("GET", `/cases/${c.id}/deadlines?all=1`);
  const box = el("div", { class: "wrap" });
  if (!deadlines.length) box.append(el("p", { class: "muted", text: "No deadlines are recorded for this case." }));
  else box.append(el("table", {}, el("thead", {}, el("tr", {}, ["Due", "What", "How sure", "Rule", "State"].map((h) => el("th", { scope: "col", text: h })))),
    el("tbody", {}, deadlines.map((d) => el("tr", { class: d.overdue ? "overdue" : null },
      el("td", { class: "num", text: fmtDate(d.due_date) + (d.overdue ? " (passed)" : "") }),
      el("td", { text: d.label }),
      el("td", { text: `${d.confidence === "hard" ? "Firm date" : "Estimate"}, ${VERIFICATION[d.verification] || d.verification}` }),
      el("td", { text: d.rule_source || "" }),
      el("td", { text: d.state }))))));
  panel.append(el("section", {}, el("h2", { text: "Deadlines" }),
    el("p", { class: "lede", text: "Dates here are only as good as their source. Check every date against the court's order or rule before you rely on it. Adding and changing deadlines isn't in this version yet." }), box));
}

// --- brief check ---
async function viewBrief(panel, c) {
  const { drafts } = await api("GET", `/cases/${c.id}/drafts`);
  const out = el("div", { "aria-live": "polite" });
  if (!drafts.length) {
    panel.append(el("section", {}, el("h2", { text: "Check a draft" }), el("p", { class: "lede", text: "There are no drafts in this case yet. Save your draft as a .txt or .md file in the Filings folder, then come back." })));
    return;
  }
  // drafts usually live under Filings/; list those first and start on the first one that can be checked
  const ordered = [...drafts.filter((d) => d.path.startsWith("Filings/")), ...drafts.filter((d) => !d.path.startsWith("Filings/"))];
  const first = ordered.find((d) => d.supported);
  const pick = el("select", { id: "draft" }, ordered.map((d) => el("option", { value: d.path, disabled: !d.supported || null,
    selected: d === first || null, text: d.supported ? d.path : `${d.path} (Word or PDF: save a .txt copy to check it)` })));
  const mode = (v, name, help) => el("label", { class: "choice" }, el("input", { type: "radio", name: "mode", value: v, checked: v === "brief" || null }),
    el("span", {}, el("b", { text: name }), ` ${help}`));
  const form = el("form", { class: "form-panel",
    onsubmit: async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      clear(out).append(el("p", { class: "muted", text: "Checking…" }));
      try {
        const r = await api("POST", `/cases/${c.id}/brief-check`, { path: pick.value, mode: form.querySelector("input[name=mode]:checked").value });
        clear(out).append(el("p", { class: "verdict", "data-exit": String(r.exit_code), text: r.summary }), renderMarkdown(r.report));
      } catch (ex) { clear(out).append(el("p", { class: "error", role: "alert", text: ex.message })); }
      btn.disabled = false;
    } },
  el("div", { class: "field" }, el("label", { for: "draft", text: "Draft" }), pick),
  el("fieldset", {}, el("legend", { text: "What kind of paper is it?" }),
    mode("brief", "Brief or motion", "Every factual sentence needs a cite to the record."),
    mode("complaint", "Complaint", "Allegations don't need record cites; every cite and quotation is still checked.")),
  el("button", { class: "btn", type: "submit", text: "Check this draft" }));
  panel.append(el("section", {}, el("h2", { text: "Check a draft" }),
    el("p", { class: "lede", text: "Brief check looks at every cite, quotation and fact in your draft and tells you which ones aren't tied to a document in this case. Passing doesn't mean the law is right or still good law." }),
    form), el("section", {}, out));
}

// --- counsel packet ---
async function viewPacket(panel, c) {
  const out = el("div");
  const bar = el("div", { class: "segmented", role: "group", "aria-label": "Packet length" });
  let text = "";
  const load = async () => {
    clear(bar).append(...state.session.layers.map((l) => el("button", { type: "button", "aria-pressed": String(state.layer === l), text: LAYER_NAMES[l] || l,
      onclick: () => { state.layer = l; load(); } })));
    clear(out).append(el("p", { class: "muted", text: "Building…" }));
    try {
      text = (await api("GET", `/cases/${c.id}/packet?layer=${encodeURIComponent(state.layer)}`)).text;
      clear(out).append(renderMarkdown(text));
    } catch (ex) { clear(out).append(el("p", { class: "error", role: "alert", text: ex.message })); }
  };
  const save = () => {
    const a = el("a", { href: URL.createObjectURL(new Blob([text + "\n"], { type: "text/markdown" })), download: `counsel-packet-${state.layer}.md` });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); toast("Packet copied."); } catch { toast("Copying isn't allowed here. Use Save instead."); }
  };
  panel.append(el("section", {}, el("h2", { text: "Counsel packet" }),
    el("p", { class: "lede", text: "A summary a lawyer can read quickly. Every claim keeps its label, and anything without a source says so." }),
    el("div", { class: "row" }, bar),
    el("div", { class: "row" }, el("button", { class: "btn quiet", type: "button", text: "Copy text", onclick: copy }),
      el("button", { class: "btn quiet", type: "button", text: "Save as a file", onclick: save })),
    out));
  await load();
}

// --- start ---
function noToken(msg) {
  document.body.replaceChildren(el("div", { class: "center" },
    el("h1", { text: "Open Matter Kit from its link" }),
    el("p", { text: msg || "For your privacy, this page only works from the link Matter Kit printed in the terminal window when it started. Copy that whole link into your browser." }),
    el("p", { class: "muted", text: "Not legal advice. Matter Kit organizes your case. It doesn't file or send anything." })));
}

async function boot() {
  const m = /^#t=([A-Za-z0-9_-]+)$/.exec(location.hash);
  if (m) {
    storage((s) => s.setItem(TOKEN_KEY, m[1]));
    token = m[1];
    history.replaceState(null, "", location.pathname + "#/");
  } else {
    token = storage((s) => s.getItem(TOKEN_KEY));
  }
  if (!token) return noToken();
  try {
    state.session = await api("GET", "/session");
    await loadCases();
  } catch (ex) {
    if (ex.status === 403) storage((s) => s.removeItem(TOKEN_KEY));
    return noToken(ex.status === 403 ? "This link is from an earlier session. Use the link printed in the terminal the last time Matter Kit started." : ex.message);
  }
  $("skip").addEventListener("click", (e) => { e.preventDefault(); $("main").focus(); });
  window.addEventListener("hashchange", route);
  if (!location.hash.startsWith("#/")) history.replaceState(null, "", location.pathname + "#/");
  route();
}

boot();
