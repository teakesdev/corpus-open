// Behaviour tests for the "Next amendment" tab of the real matterkit/webui_static/app.js, in the same
// vm and DOM shim as run.mjs. Run: node tests/webui_js/amend.mjs        (Node 18 or newer; no packages)
// Everything is synthetic. The server's list replies are answered for the test; every write is held until
// the test answers it by hand, so the order things finish in is the test's to choose.
import test from "node:test";
import assert from "node:assert/strict";
import { A, B, Ev, boot, byText, caseHash, fixtureHandler, ok } from "./dom.mjs";

const same = (a, b, msg) => assert.ok(a === b, msg);
const notSame = (a, b, msg) => assert.ok(a !== b, msg);

const ID1 = "am_" + "1".repeat(12), ID2 = "am_" + "2".repeat(12);
const when = "2026-10-31T21:00:00+00:00";
const item = (over = {}) => ({
  id: ID1, target: "complaint", text: "Attribute all three ER injuries to the jail beating", paragraphs: "¶¶ 37, 172",
  reason: "The ER notes tie them to one event", status: "proposed", status_label: "Proposed", created_by: "human:tester",
  created_at: when, updated_at: null, links: { facts: [], documents: [] },
  history: [{ from_status: null, to_status: "proposed", note: null, recorded_by: "human:tester", created_at: when }], ...over,
});
const FACTS = [{ id: "a_1", text: "Plaintiff was treated at the ER", status: "DOCUMENTED FACT", unsupported: false, sources: [], created_by: "human:t" },
               { id: "a_2", text: "An officer struck him in the cell", status: "ALLEGATION", unsupported: true, sources: [], created_by: "human:t" }];

// A page whose reads are answered from `cfg`: the queued items, how many are open, and whether the list is held.
async function open(cfg = {}) {
  cfg.items ??= [];
  cfg.hold = false;
  const handler = (req) => {
    if (req.method !== "GET") return undefined;
    if (/\/status$/.test(req.path)) return ok({ case: A, status: { assertions: 2, documents_total: 1, unverified_authorities: 0, amendments_open: cfg.open ?? 0 } });
    if (/\/facts$/.test(req.path)) return ok({ facts: FACTS });
    if (/\/amendments(\?|$)/.test(req.path)) {
      if (cfg.hold) return undefined;
      const all = /status=all/.test(req.path);
      return ok({ amendments: cfg.items.filter((i) => all || i.status === "proposed" || i.status === "approved"), counts: {} });
    }
    return fixtureHandler(req);
  };
  const env = await boot({ handler });
  env.cfg = cfg;
  return env;
}
const tab = (c) => caseHash(c, "amend");
const posts = (env) => env.find("POST", /\/amendments$/);
const moves = (env) => env.find("POST", /\/amendments\/am_[0-9a-f]{12}\/status$/);
const addButton = (env) => byText(env.main(), "button", "Add to the list");
const form = (env) => addButton(env).closest("form");
const fill = (env, text) => { const box = env.document.getElementById("amend-text"); box.value = text; return box; };
const ADDED = { amendment: item() };
// the shim has no descendant selectors: look inside an element in two steps
const actions = (li) => li.querySelector(".amend-actions").querySelectorAll("button");
const errorOf = (li) => li.querySelector(".error").textContent;

// --- the tab is there, and says what is queued ---

test("the tab lists the open changes with their status, paragraphs, reason, facts and documents", async () => {
  const env = await open({ items: [item({ links: { facts: [{ id: "a_1", text: "Plaintiff was treated at the ER", label: "DOCUMENTED FACT" }],
    documents: [{ id: "d1", rel_path: "Evidence/Complaint.pdf", locator: "p. 2" }] } })] });
  await env.go(tab(A));
  const text = env.main().textContent;
  for (const want of ["Next amendment", "Proposed", "¶¶ 37, 172", "Attribute all three ER injuries", "The ER notes tie them",
    "Documented fact", "Plaintiff was treated at the ER", "Evidence/Complaint.pdf, p. 2", "Added by tester", "History (1)"]) {
    assert.ok(text.includes(want), `missing: ${want}`);
  }
  assert.equal(env.main().querySelector(".amend").getAttribute("data-status"), "proposed");
});

test("the overview says how many changes are queued, and links to the tab; nothing when none", async () => {
  const env = await open({ open: 2 });
  await env.go(caseHash(A, "overview"));
  assert.match(env.main().textContent, /2 changes queued for the next amended pleading/);
  const link = env.main().querySelectorAll("a").find((a) => a.textContent === "Review them");
  assert.equal(link.getAttribute("href"), tab(A));

  const none = await open({ open: 0 });
  await none.go(caseHash(A, "overview"));
  assert.doesNotMatch(none.main().textContent, /queued for the next amended pleading/);
  const one = await open({ open: 1 });
  await one.go(caseHash(A, "overview"));
  assert.match(one.main().textContent, /1 change queued for the next amended pleading/);
});

test("case text is set as text, never parsed", async () => {
  const evil = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
  const env = await open({ items: [item({ text: evil, paragraphs: evil, reason: evil })] });
  await env.go(tab(A));
  assert.equal(env.main().querySelectorAll("img").length + env.main().querySelectorAll("script").length, 0);
  assert.ok(env.main().querySelector(".amend-text").textContent === evil);
});

// --- the add form ---

test("adding a change sends only what was typed and picked, and names the case when it is saved", async () => {
  const env = await open();
  await env.go(tab(A));
  fill(env, "Attribute all three ER injuries to the jail beating");
  env.document.getElementById("amend-paras").value = "¶¶ 37, 172";
  env.document.getElementById("amend-reason").value = "ER notes";
  form(env).querySelectorAll("input[name=amend-fact]").find((c) => c.value === "a_1").checked = true;
  const row = form(env).querySelector(".source-row");
  row.querySelector("select").value = "d1";
  row.querySelector("input").value = " p. 2 ";
  addButton(env).click();
  await env.flush();
  const post = env.only("POST", /\/amendments$/);
  assert.ok(post.url.includes(A.id) && !post.url.includes(B.id));
  assert.deepEqual(post.json(), { text: "Attribute all three ER injuries to the jail beating", paragraphs: "¶¶ 37, 172", reason: "ER notes",
    facts: ["a_1"], documents: [{ document_id: "d1", locator: "p. 2" }] });
  post.respond(ADDED);
  await env.flush();
  assert.equal(env.toast(), "Change added to Alpha v. City.");
  assert.equal(env.document.getElementById("amend-text").value, "", "the form should reset after a save");
});

test("an empty change is not sent", async () => {
  const env = await open();
  await env.go(tab(A));
  fill(env, "   ");
  addButton(env).click();
  await env.flush();
  assert.equal(posts(env).length, 0);
  assert.match(form(env).querySelector(".error").textContent, /Say what should change/);
});

test("a change saved on one case leaves another case's unsaved form alone", async () => {
  const env = await open();
  await env.go(tab(A));
  fill(env, "Alpha change");
  addButton(env).click();
  await env.flush();
  const post = env.only("POST", /\/amendments$/);

  await env.go(tab(B));
  const betaBox = fill(env, "UNSAVED BETA WORK");
  post.respond(ADDED);
  await env.flush();

  assert.ok(post.url.includes(A.id) && !post.url.includes(B.id), "the write must name Alpha");
  same(env.document.getElementById("amend-text"), betaBox, "Beta's form was rebuilt by Alpha's answer");
  assert.equal(betaBox.value, "UNSAVED BETA WORK");
  assert.match(env.main().textContent, /Beta v\. County/);
  assert.equal(env.toast(), "Change added to Alpha v. City.");
});

test("the add button is off while a change is being saved, and a second submit sends nothing", async () => {
  const env = await open();
  await env.go(tab(A));
  fill(env, "Alpha change");
  const btn = addButton(env);
  btn.click();
  await env.flush();
  assert.equal(btn.disabled, true);
  btn.click();
  form(env).dispatchEvent(new Ev("submit"));  // Enter in a text box submits without the button
  await env.flush();
  assert.equal(posts(env).length, 1, "a second submit must not send a second request");
});

test("a change that fails to save shows the error and can be sent again", async () => {
  const env = await open();
  await env.go(tab(A));
  const box = fill(env, "Alpha change");
  const btn = addButton(env);
  btn.click();
  await env.flush();
  env.only("POST", /\/amendments$/).respond({ error: "Could not save that change." }, 500);
  await env.flush();
  same(env.document.getElementById("amend-text"), box, "a failure must not rebuild the form");
  assert.equal(box.value, "Alpha change");
  assert.equal(form(env).querySelector(".error").textContent, "Could not save that change.");
  assert.equal(btn.disabled, false);
  btn.click();
  await env.flush();
  assert.equal(posts(env).length, 2);
});

test("a change that fails after the user moved on does not touch the page they are on, and still says so", async () => {
  const env = await open();
  await env.go(tab(A));
  fill(env, "Alpha change");
  addButton(env).click();
  await env.flush();
  const post = env.only("POST", /\/amendments$/);

  await env.go(tab(B));
  const betaBox = fill(env, "UNSAVED BETA WORK");
  const before = env.main().childNodes[0];
  post.respond({ error: "Could not save that change." }, 500);
  await env.flush();

  same(env.document.getElementById("amend-text"), betaBox, "Beta's form was rebuilt by Alpha's failure");
  assert.equal(betaBox.value, "UNSAVED BETA WORK");
  same(env.main().childNodes[0], before, "the page was rebuilt");
  assert.equal(env.main().querySelector(".error").textContent, "", "Alpha's error must not appear on Beta's form");
  assert.match(env.toast(), /Alpha v\. City/);
  assert.match(env.toast(), /Could not save that change\./);
});

// --- the status buttons ---

test("each status offers the moves that make sense, and Approve posts the status and the note", async () => {
  const env = await open({ items: [item(), item({ id: ID2, status: "approved", status_label: "Approved", text: "Second" })] });
  await env.go(tab(A));
  const buttons = (li) => actions(li).map((b) => b.textContent);
  const [first, second] = env.main().querySelectorAll(".amend");
  assert.deepEqual(buttons(first), ["Approve", "Mark “in the draft”", "Drop"]);
  assert.deepEqual(buttons(second), ["Mark “in the draft”", "Drop", "Back to proposed"]);
  first.querySelector("input[type=text]").value = " checked against the ER notes ";
  actions(first).find((b) => b.textContent === "Approve").click();
  await env.flush();
  const move = env.only("POST", /\/amendments\/am_1{12}\/status$/);
  assert.ok(move.url.includes(A.id));
  assert.deepEqual(move.json(), { status: "approved", note: "checked against the ER notes" });
});

test("a finished or dropped change can be reopened, and only that", async () => {
  const env = await open({ items: [item({ status: "incorporated", status_label: "In the draft" }), item({ id: ID2, status: "dropped", status_label: "Dropped" })] });
  env.cfg.open = 0;
  await env.go(tab(A));
  const toggle = byText(env.main(), "button", "All changes");
  toggle.click();
  await env.flush();
  const labels = env.main().querySelectorAll(".amend").map((li) => actions(li).map((b) => b.textContent));
  assert.deepEqual(labels, [["Reopen"], ["Reopen"]]);
});

test("a status change saved on one case leaves another case's page alone, and names the case", async () => {
  const env = await open({ items: [item()] });
  await env.go(tab(A));
  byText(env.main(), "button", "Approve").click();
  await env.flush();
  const move = env.only("POST", /\/status$/);

  await env.go(tab(B));
  const betaBox = fill(env, "UNSAVED BETA WORK");
  const before = env.main().childNodes[0];
  const requests = env.requests.length;
  move.respond({ amendment: item({ status: "approved", status_label: "Approved" }) });
  await env.flush();

  assert.ok(move.url.includes(A.id) && !move.url.includes(B.id));
  same(env.document.getElementById("amend-text"), betaBox, "Beta's form was rebuilt by Alpha's answer");
  assert.equal(betaBox.value, "UNSAVED BETA WORK");
  same(env.main().childNodes[0], before);
  assert.equal(env.requests.length, requests, "a gone view must not reload its list");
  assert.equal(env.toast(), "Marked Approved in Alpha v. City.");
});

test("a status change that fails shows its error under the change and can be tried again", async () => {
  const env = await open({ items: [item()] });
  await env.go(tab(A));
  const approve = byText(env.main(), "button", "Approve");
  approve.click();
  await env.flush();
  assert.equal(actions(env.main().querySelector(".amend")).every((b) => b.disabled), true, "every move is off while one is out");
  approve.click();
  await env.flush();
  assert.equal(moves(env).length, 1, "a second click must not send a second request");

  env.only("POST", /\/status$/).respond({ error: "That change was changed by someone else just now." }, 400);
  await env.flush();
  assert.equal(errorOf(env.main().querySelector(".amend")), "That change was changed by someone else just now.");
  assert.equal(approve.disabled, false);
  approve.click();
  await env.flush();
  assert.equal(moves(env).length, 2);
});

test("a status change that fails after the user moved on still says so", async () => {
  const env = await open({ items: [item()] });
  await env.go(tab(A));
  byText(env.main(), "button", "Drop").click();
  await env.flush();
  const move = env.only("POST", /\/status$/);
  await env.go(tab(B));
  const before = env.main().childNodes[0];
  move.respond({ error: "Could not save that change." }, 500);
  await env.flush();
  same(env.main().childNodes[0], before, "the page was rebuilt");
  assert.match(env.toast(), /Alpha v\. City/);
  assert.match(env.toast(), /Could not save that change\./);
});

test("after a status change the list reloads and keyboard focus stays in the list", async () => {
  const env = await open({ items: [item()] });
  await env.go(tab(A));
  byText(env.main(), "button", "Approve").click();
  await env.flush();
  env.cfg.items = [item({ status: "approved", status_label: "Approved" })];
  env.only("POST", /\/status$/).respond({ amendment: env.cfg.items[0] });
  await env.flush();
  assert.equal(env.main().querySelector(".amend").getAttribute("data-status"), "approved");
  assert.match(env.document.activeElement.textContent, /Changes still to make \(1\)/, "focus was lost with the button that was pressed");
});

// --- the open / all toggle ---

test("switching between open and all keeps the newest answer when replies arrive out of order", async () => {
  const env = await open({ items: [item(), item({ id: ID2, status: "dropped", status_label: "Dropped", text: "Dropped one" })] });
  await env.go(tab(A));
  env.cfg.hold = true;
  byText(env.main(), "button", "All changes").click();
  await env.flush();
  const slowAll = env.only("GET", /amendments\?status=all$/);
  byText(env.main(), "button", "Still to do").click();
  await env.flush();
  const open2 = env.find("GET", /amendments\?status=open$/).filter((r) => !r.settled)[0];

  open2.respond({ amendments: [item()], counts: {} });
  await env.flush();
  slowAll.respond({ amendments: [item(), item({ id: ID2, status: "dropped", status_label: "Dropped", text: "Dropped one" })], counts: {} });
  await env.flush();

  assert.match(env.main().textContent, /Changes still to make \(1\)/);
  assert.doesNotMatch(env.main().textContent, /Dropped one/);
  assert.equal(byText(env.main(), "button", "Still to do").getAttribute("aria-pressed"), "true");
  assert.equal(byText(env.main(), "button", "All changes").getAttribute("aria-pressed"), "false");
});
