// A small hand-written DOM, just enough to run the real matterkit/webui_static/app.js under Node.
// No npm packages. Anything app.js touches that is missing here throws, so the shim grows with the app
// instead of quietly returning undefined. Set MATTERKIT_APP_JS to run a different copy of app.js.
import vm from "node:vm";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const STATIC_DIR = path.resolve(HERE, "../../matterkit/webui_static");

export class Ev {
  constructor(type, init = {}) { this.type = type; this.defaultPrevented = false; Object.assign(this, init); }
  preventDefault() { this.defaultPrevented = true; }
}

export class Node {
  remove() {
    const p = this.parentNode;
    if (p) { p.childNodes.splice(p.childNodes.indexOf(this), 1); this.parentNode = null; }
  }
}
export class Text extends Node {
  constructor(data) { super(); this.data = String(data); this.parentNode = null; }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
}

// --- selectors: tag, .class, #id, [attr], [attr=value], :checked, in any combination. No combinators. ---
function compound(sel) {
  const tests = [];
  let rest = sel.trim();
  const take = (re) => { const m = re.exec(rest); if (m) rest = rest.slice(m[0].length); return m; };
  let m;
  if ((m = take(/^[a-zA-Z][\w-]*/))) { const t = m[0].toUpperCase(); tests.push((e) => e.tagName === t); }
  while (rest) {
    if ((m = take(/^\.([\w-]+)/))) { const c = m[1]; tests.push((e) => e.className.split(/\s+/).includes(c)); }
    else if ((m = take(/^#([\w-]+)/))) { const id = m[1]; tests.push((e) => e.getAttribute("id") === id); }
    else if ((m = take(/^\[([\w-]+)(?:=("?)([^\]"]*)\2)?\]/))) {
      const [, name, , val] = m;
      tests.push((e) => (val === undefined ? e.hasAttribute(name) : e.getAttribute(name) === val));
    } else if (take(/^:checked/)) tests.push((e) => e.checked);
    else throw new Error(`shim: selector not supported: ${sel}`);
  }
  return (e) => tests.every((t) => t(e));
}

export class Element extends Node {
  constructor(tag, doc) {
    super();
    this.tagName = tag.toUpperCase();
    this.ownerDocument = doc;
    this.childNodes = [];
    this.parentNode = null;
    this.attrs = new Map();
    this.listeners = new Map();
    this._value = undefined;
    this._checked = false;
    this.files = [];
  }
  get className() { return this.attrs.get("class") ?? ""; }
  set className(v) { this.attrs.set("class", String(v)); }
  get classList() {
    const self = this;
    const names = () => self.className.split(/\s+/).filter(Boolean);
    return {
      add: (n) => { if (!names().includes(n)) self.className = [...names(), n].join(" "); },
      remove: (n) => { self.className = names().filter((x) => x !== n).join(" "); },
      contains: (n) => names().includes(n),
    };
  }
  setAttribute(k, v) { this.attrs.set(k, String(v)); if (k === "checked") this._checked = true; }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  hasAttribute(k) { return this.attrs.has(k); }
  removeAttribute(k) { this.attrs.delete(k); }

  get checked() { return this._checked; }
  set checked(v) { this._checked = !!v; }
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.tagName === "OPTION") return this.attrs.get("value") ?? this.textContent;
    if (this.tagName === "SELECT") {
      const opts = this.querySelectorAll("option");
      const pick = opts.find((o) => o.hasAttribute("selected")) || opts.find((o) => !o.disabled);
      return pick ? pick.value : "";
    }
    return this.attrs.get("value") ?? "";
  }
  set value(v) { this._value = String(v); }

  get textContent() { return this.childNodes.map((k) => k.textContent).join(""); }
  set textContent(v) {
    for (const k of [...this.childNodes]) k.remove();
    if (String(v) !== "") this.append(new Text(v));
  }
  append(...kids) {
    for (const k of kids) {
      const n = k instanceof Node ? k : new Text(k);
      n.remove();
      n.parentNode = this;
      this.childNodes.push(n);
    }
  }
  replaceChildren(...kids) { for (const k of [...this.childNodes]) k.remove(); this.append(...kids); }

  matches(sel) { return compound(sel)(this); }
  closest(sel) { for (let n = this; n instanceof Element; n = n.parentNode) if (n.matches(sel)) return n; return null; }
  querySelectorAll(sel) {
    const test = compound(sel), out = [];
    const walk = (n) => { for (const k of n.childNodes) if (k instanceof Element) { if (test(k)) out.push(k); walk(k); } };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] ?? null; }

  addEventListener(type, fn) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]); }
  removeEventListener(type, fn) { this.listeners.set(type, (this.listeners.get(type) ?? []).filter((f) => f !== fn)); }
  dispatchEvent(ev) {
    ev.target ??= this;
    for (let n = this; n instanceof Element; n = n.parentNode) {
      for (const fn of [...(n.listeners.get(ev.type) ?? [])]) fn.call(n, ev);
    }
    return !ev.defaultPrevented;
  }
  focus() { this.ownerDocument.activeElement = this; }
  click() {
    if (this.disabled) return;  // a disabled control ignores clicks, as in a browser
    if (this.tagName === "INPUT" && this.getAttribute("type") === "radio") {
      const root = this.closest("form") ?? this.ownerDocument.body;
      for (const r of root.querySelectorAll(`input[name=${this.getAttribute("name")}]`)) r._checked = false;
      this._checked = true;
    }
    const ev = new Ev("click");
    if (!this.dispatchEvent(ev)) return;
    if (this.tagName === "BUTTON" && (this.getAttribute("type") ?? "submit") === "submit") this.closest("form")?.dispatchEvent(new Ev("submit"));
    if (this.tagName === "A") this.ownerDocument.env.followLink(this);
  }
}
for (const name of ["disabled", "hidden", "required", "multiple"]) {
  Object.defineProperty(Element.prototype, name, {
    get() { return this.attrs.has(name); },
    set(v) { if (v) this.attrs.set(name, ""); else this.attrs.delete(name); },
  });
}

// --- the page: the ids index.html declares, so a getElementById for one it doesn't have fails here too ---
function buildBody(doc) {
  const html = fs.readFileSync(path.join(STATIC_DIR, "index.html"), "utf-8");
  const body = new Element("body", doc);
  for (const m of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)) {
    const e = new Element(m[1], doc);
    e.setAttribute("id", m[2]);
    body.append(e);
  }
  return body;
}

export const ok = (body) => ({ status: 200, body });
export const failure = (status, error) => ({ status, body: { error } });

export const A = { id: "a".repeat(16), title: "Alpha v. City", rel: "Alpha v. City", parent_id: null };
export const B = { id: "b".repeat(16), title: "Beta v. County", rel: "Beta v. County", parent_id: null };
export const caseHash = (c, tab = "overview") => `#/case/${c.id}/${tab}`;

// Answers the reads every page makes. Returning undefined holds a request until the test answers it by hand.
export function fixtureHandler(req) {
  if (req.method !== "GET") return undefined;
  if (req.path === "/session") return ok({ workspace: "/synthetic/workspace", operator: "tester", folders: ["Evidence", "Filings"], layers: ["30s", "3min", "15min"] });
  if (req.path === "/cases") return ok({ cases: [A, B] });
  const m = /^\/cases\/[0-9a-f]{16}\/(deadlines|documents|facts)(\?|$)/.exec(req.path);
  if (m?.[1] === "deadlines") return ok({ deadlines: [] });
  if (m?.[1] === "documents") return ok({ documents: [{ id: "d1", rel_path: "Evidence/Complaint.pdf", kind: "pdf", bytes: 2048 }] });
  if (m?.[1] === "facts") return ok({ facts: [] });
  return undefined;
}

export async function boot({ handler = fixtureHandler, hash = "#t=synthetic-token" } = {}) {
  const requests = [], timers = new Map(), downloads = [], clipboard = [], blobs = new Map();
  let nextTimer = 0, nextBlob = 0, currentHash = hash;
  const winListeners = new Map();
  const store = new Map();

  const doc = { activeElement: null };
  const env = { document: doc, requests, downloads, clipboard, timers, clipboardFails: false };
  doc.env = env;
  doc.createElement = (tag) => new Element(tag, doc);
  doc.createTextNode = (s) => new Text(s);
  doc.body = buildBody(doc);
  doc.getElementById = (id) => doc.body.querySelector(`#${id}`);

  const location = {
    pathname: "/",
    get hash() { return currentHash; },
    set hash(v) {
      const h = v.startsWith("#") ? v : `#${v}`;
      if (h === currentHash) return;
      currentHash = h;
      queueMicrotask(() => (winListeners.get("hashchange") ?? []).forEach((f) => f(new Ev("hashchange"))));
    },
  };
  const history = { replaceState(_s, _t, url) { const i = url.indexOf("#"); currentHash = i < 0 ? "" : url.slice(i); } };

  class Blob {
    constructor(parts = [], opts = {}) { this.text = parts.join(""); this.type = opts.type ?? ""; }
  }
  const URLish = {
    createObjectURL(blob) { const u = `blob:synthetic/${++nextBlob}`; blobs.set(u, blob); return u; },
    revokeObjectURL(u) { blobs.delete(u); },
  };

  function fetch(url, init = {}) {
    const req = { url, method: init.method ?? "GET", headers: init.headers ?? {}, body: init.body, settled: false };
    req.path = url.replace(/^\/api\/v0/, "");
    req.json = () => JSON.parse(req.body);
    const done = new Promise((resolve, reject) => {
      req.respond = (body, status = 200) => {
        req.settled = true;
        resolve({ ok: status < 400, status, json: async () => JSON.parse(JSON.stringify(body)) });
      };
      req.fail = () => { req.settled = true; reject(new TypeError("network down")); };
    });
    requests.push(req);
    const answer = handler(req);
    if (answer !== undefined) queueMicrotask(() => req.respond(answer.body, answer.status));
    return done;
  }

  const sandbox = {
    document: doc, location, history, fetch, Blob, URL: URLish, Node, console,
    navigator: { clipboard: { writeText: (t) => (env.clipboardFails ? Promise.reject(new Error("denied")) : (clipboard.push(t), Promise.resolve())) } },
    setTimeout: (fn) => { timers.set(++nextTimer, fn); return nextTimer; },
    clearTimeout: (id) => { timers.delete(id); },
  };
  sandbox.window = {
    location, sessionStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => { store.set(k, String(v)); }, removeItem: (k) => { store.delete(k); },
    },
    addEventListener: (t, f) => winListeners.set(t, [...(winListeners.get(t) ?? []), f]),
  };
  const ctx = vm.createContext(sandbox);

  env.flush = async () => { for (let i = 0; i < 3; i++) await new Promise((r) => setImmediate(r)); };
  env.go = async (h) => { location.hash = h; await env.flush(); };
  env.run = (code) => vm.runInContext(code, ctx);
  env.call = (name, ...args) => vm.runInContext(name, ctx)(...args);
  env.runTimers = () => { const fns = [...timers.values()]; timers.clear(); fns.forEach((f) => f()); };
  env.followLink = (a) => {
    const href = a.getAttribute("href");
    if (a.hasAttribute("download")) downloads.push({ download: a.getAttribute("download"), href, text: blobs.get(href)?.text });
    else if (href?.startsWith("#")) location.hash = href;
  };
  env.main = () => doc.getElementById("main");
  env.toast = () => doc.getElementById("toast").textContent;
  // the requests (settled or not) that match; `only` insists on exactly one
  env.find = (method, re) => requests.filter((r) => r.method === method && re.test(r.path));
  env.only = (method, re) => {
    const hits = env.find(method, re);
    if (hits.length !== 1) throw new Error(`expected one ${method} ${re}, found ${hits.length}: ${requests.map((r) => `${r.method} ${r.path}`).join(" | ")}`);
    return hits[0];
  };

  const file = process.env.MATTERKIT_APP_JS || path.join(STATIC_DIR, "app.js");
  vm.runInContext(fs.readFileSync(file, "utf-8"), ctx, { filename: file });
  await env.flush();
  return env;
}

// --- finders for tests ---
export const all = (root, sel) => root.querySelectorAll(sel);
export function byText(root, tag, text) {
  const hits = root.querySelectorAll(tag).filter((e) => e.textContent === text);
  if (hits.length !== 1) throw new Error(`expected one <${tag}> reading "${text}", found ${hits.length}`);
  return hits[0];
}
