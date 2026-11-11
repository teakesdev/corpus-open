// Behaviour tests for the real matterkit/webui_static/app.js, run in a vm with the DOM shim in dom.mjs.
// Run: node tests/webui_js/run.mjs        (needs Node 18 or newer; no packages)
// Everything here is synthetic. Each test boots its own page and answers the server's replies by hand,
// so the order requests finish in is the test's to choose.
import test from "node:test";
import assert from "node:assert/strict";
import { A, B, Ev, boot, byText, caseHash, fixtureHandler } from "./dom.mjs";

// node identity without assert.equal, which would print both whole DOM trees when it fails
const same = (a, b, msg) => assert.ok(a === b, msg);
const notSame = (a, b, msg) => assert.ok(a !== b, msg);
const factPosts = (env) => env.find("POST", /\/facts$/);
const FACT = (text, unsupported = true) => ({ fact: { id: "f1", text, status: "ALLEGATION", unsupported, sources: [], created_by: "human:tester" } });

const submitButton = (env) => byText(env.main(), "button", "Add fact");
const addFactForm = (env) => submitButton(env).closest("form");
// fill in the Facts form on screen: some text, and one of the five "how sure" choices
function fillFact(env, text) {
  const box = env.document.getElementById("fact-text");
  box.value = text;
  addFactForm(env).querySelectorAll("input[name=label]").find((r) => r.value === "ALLEGATION").click();
  return box;
}

// --- F6: a fact saved while the user has moved on ---

test("a fact saved on one case leaves another case's unsaved form alone", async () => {
  const env = await boot();
  await env.go(caseHash(A, "facts"));
  fillFact(env, "Alpha fact");
  submitButton(env).click();
  await env.flush();
  const post = env.only("POST", /\/facts$/);

  await env.go(caseHash(B, "facts"));
  const betaBox = env.document.getElementById("fact-text");
  betaBox.value = "UNSAVED BETA WORK";

  post.respond(FACT("Alpha fact"));
  await env.flush();

  assert.ok(post.url.includes(A.id) && !post.url.includes(B.id), "the write must name Alpha");
  same(env.document.getElementById("fact-text"), betaBox, "Beta's form was rebuilt by Alpha's answer");
  assert.equal(betaBox.value, "UNSAVED BETA WORK");
  assert.match(env.main().textContent, /Beta v\. County/);
  assert.equal(env.toast(), "Fact added to Alpha v. City. It has no source yet.");
});

test("a fact saved on the case still showing rebuilds the page and names the case", async () => {
  const env = await boot();
  await env.go(caseHash(A, "facts"));
  const box = fillFact(env, "Alpha fact");
  submitButton(env).click();
  await env.flush();
  env.only("POST", /\/facts$/).respond(FACT("Alpha fact", false));
  await env.flush();

  notSame(env.document.getElementById("fact-text"), box, "the form should reset after a save");
  assert.equal(env.document.getElementById("fact-text").value, "");
  assert.equal(env.toast(), "Fact added to Alpha v. City.");
});

test("the submit button is off while a fact is being saved, and a second submit sends nothing", async () => {
  const env = await boot();
  await env.go(caseHash(A, "facts"));
  fillFact(env, "Alpha fact");
  const btn = submitButton(env);
  assert.equal(btn.disabled, false);
  btn.click();
  await env.flush();
  assert.equal(btn.disabled, true, "the button should be disabled while the request is out");

  btn.click();  // a disabled button does nothing
  addFactForm(env).dispatchEvent(new Ev("submit"));  // Enter in a source box submits the form without the button
  await env.flush();
  assert.equal(factPosts(env).length, 1, "a second submit must not send a second request");
});

test("a fact that fails to save shows the error and can be sent again", async () => {
  const env = await boot();
  await env.go(caseHash(A, "facts"));
  const box = fillFact(env, "Alpha fact");
  const btn = submitButton(env);
  btn.click();
  await env.flush();
  assert.equal(btn.disabled, true);

  env.only("POST", /\/facts$/).respond({ error: "Could not save that fact." }, 500);
  await env.flush();

  same(env.document.getElementById("fact-text"), box, "a failure must not rebuild the form");
  assert.equal(box.value, "Alpha fact");
  assert.equal(addFactForm(env).querySelector(".error").textContent, "Could not save that fact.");
  assert.equal(btn.disabled, false, "the button should come back so the user can retry");
  btn.click();
  await env.flush();
  assert.equal(factPosts(env).length, 2);
});

test("a fact that fails after the user moved on does not touch the page they are on, and still says so", async () => {
  const env = await boot();
  await env.go(caseHash(A, "facts"));
  fillFact(env, "Alpha fact");
  submitButton(env).click();
  await env.flush();
  const post = env.only("POST", /\/facts$/);

  await env.go(caseHash(B, "facts"));
  const betaBox = env.document.getElementById("fact-text");
  betaBox.value = "UNSAVED BETA WORK";
  const before = env.main().childNodes[0];
  post.respond({ error: "Could not save that fact." }, 500);
  await env.flush();

  same(env.document.getElementById("fact-text"), betaBox, "Beta's form was rebuilt by Alpha's failure");
  assert.equal(betaBox.value, "UNSAVED BETA WORK");
  same(env.main().childNodes[0], before, "the page was rebuilt");
  assert.equal(env.main().querySelector(".error").textContent, "", "Alpha's error must not appear on Beta's form");
  assert.match(env.toast(), /Alpha v\. City/);
  assert.match(env.toast(), /Could not save that fact\./);
});

// --- F6: documents name the case ---

test("upload and scan messages name the case", async () => {
  const env = await boot();
  await env.go(caseHash(A, "documents"));
  const results = env.main().querySelector("ul.uploads");
  const picker = env.document.getElementById("upload-files");

  picker.files = [{ name: "Letter.pdf" }, { name: "Notes.txt" }];
  picker.dispatchEvent(new Ev("change"));
  await env.flush();
  assert.equal(results.childNodes[0].textContent, "Adding Letter.pdf to Alpha v. City…");
  env.find("PUT", /\/files\?/)[0].respond({ rel_path: "Evidence/Letter.pdf" });
  await env.flush();
  env.find("PUT", /\/files\?/)[1].respond({ error: "A file with that name is already there." }, 409);
  await env.flush();
  assert.equal(results.childNodes[0].textContent, "Added Evidence/Letter.pdf to Alpha v. City.");
  assert.equal(results.childNodes[1].textContent, "Notes.txt was not added to Alpha v. City: A file with that name is already there.");

  byText(env.main(), "button", "Look for new files").click();
  await env.flush();
  env.only("POST", /\/scan$/).respond({ added: 2 });
  await env.flush();
  assert.match(env.main().textContent, /Added 2 files found in Alpha v\. City's folder\./);
});

// --- F6: packet loads that finish out of order ---

const layerButton = (env, name) => byText(env.main(), "button", name);
const pressed = (env, name) => layerButton(env, name).getAttribute("aria-pressed");
const exportsNothing = (env) => assert.deepEqual([env.clipboard, env.downloads], [[], []]);

test("packet: a slow answer for an earlier layer cannot replace the layer now showing", async () => {
  const env = await boot();
  await env.go(caseHash(A, "packet"));
  const slow = env.only("GET", /layer=3min$/);
  layerButton(env, "30-second view").click();
  await env.flush();
  const fast = env.only("GET", /layer=30s$/);

  fast.respond({ text: "# Short packet\n\nOnly the short view." });
  await env.flush();
  slow.respond({ text: "# Long packet\n\nThe three minute view." });
  await env.flush();

  const shown = env.main().textContent;
  assert.match(shown, /Only the short view\./);
  assert.doesNotMatch(shown, /three minute view/);
  assert.equal(pressed(env, "30-second view"), "true");
  assert.equal(pressed(env, "3-minute view"), "false");

  byText(env.main(), "button", "Copy text").click();
  await env.flush();
  assert.deepEqual(env.clipboard, ["# Short packet\n\nOnly the short view."]);
  byText(env.main(), "button", "Save as a file").click();
  assert.equal(env.downloads.length, 1);
  assert.equal(env.downloads[0].download, "counsel-packet-30s.md");
  assert.equal(env.downloads[0].text, "# Short packet\n\nOnly the short view.\n");
});

test("packet: Copy and Save are off while a layer builds", async () => {
  const env = await boot();
  await env.go(caseHash(A, "packet"));
  const copy = byText(env.main(), "button", "Copy text");
  const save = byText(env.main(), "button", "Save as a file");
  assert.equal(copy.disabled && save.disabled, true, "nothing to export before the first answer");
  env.only("GET", /layer=3min$/).respond({ text: "# Long packet" });
  await env.flush();
  assert.equal(copy.disabled || save.disabled, false);

  layerButton(env, "15-minute view").click();
  await env.flush();
  assert.equal(copy.disabled && save.disabled, true, "the 3-minute text must not be exportable while 15-minute builds");
  copy.click();
  save.click();
  await env.flush();
  exportsNothing(env);
});

test("packet: a layer that fails after an earlier success exports nothing stale", async () => {
  const env = await boot();
  await env.go(caseHash(A, "packet"));
  env.only("GET", /layer=3min$/).respond({ text: "# Long packet\n\nOld text." });
  await env.flush();

  layerButton(env, "30-second view").click();
  await env.flush();
  env.only("GET", /layer=30s$/).respond({ error: "Could not build that view." }, 500);
  await env.flush();

  assert.match(env.main().querySelector(".error").textContent, /Could not build that view\./);
  assert.doesNotMatch(env.main().textContent, /Old text/);
  byText(env.main(), "button", "Copy text").click();
  byText(env.main(), "button", "Save as a file").click();
  await env.flush();
  exportsNothing(env);
  assert.equal(byText(env.main(), "button", "Copy text").disabled, true);
  assert.equal(byText(env.main(), "button", "Save as a file").disabled, true);
});

test("packet: switching layers keeps the same buttons, so keyboard focus survives", async () => {
  const env = await boot();
  await env.go(caseHash(A, "packet"));
  const short = layerButton(env, "30-second view");
  short.click();
  await env.flush();
  same(layerButton(env, "30-second view"), short, "the layer buttons were rebuilt, which drops keyboard focus");
});

// --- F8: report tables ---

const rows = (table) => table.querySelectorAll("tr").map((tr) => tr.childNodes);

test("markdown table: an escaped pipe stays inside its cell", async () => {
  const env = await boot();
  const md = [
    "| Line | Cite or sentence | Problem | Code |",
    "|---:|---|---|---|",
    "| 12 | alpha \\| beta, 3 U.S.C. § 4 | No cite | `NO_CITE` |",
    "| 13 | ends with a backslash\\\\| then more | Odd | `X` |",
  ].join("\n");
  const table = env.call("renderMarkdown", md).querySelector("table");
  const [head, one, two] = table.querySelectorAll("tr");
  const cells = (tr) => tr.childNodes;
  assert.equal(cells(head).length, 4);
  assert.equal(cells(one).length, 4, "the pipe inside the cite shifted the columns");
  assert.equal(cells(one)[1].textContent, "alpha | beta, 3 U.S.C. § 4");
  assert.equal(cells(one)[2].textContent, "No cite");
  assert.equal(cells(one)[3].querySelector("code").textContent, "NO_CITE");
  assert.equal(cells(two).length, 4);
  assert.equal(cells(two)[1].textContent, "ends with a backslash\\| then more");
  assert.equal(table.querySelectorAll("tr").length, 3, "the |---| rule row should not render");
});

test("markdown table: <br> inside a cell is a line break node, never text", async () => {
  const env = await boot();
  const md = "| Line | Proposition | Cited source | Quotes |\n|---:|---|---|---|\n| 5 | one<br>two<br>three | `a.pdf` | — |";
  const body = env.call("renderMarkdown", md).querySelector("tbody");
  const cell = body.querySelectorAll("td")[1];
  assert.deepEqual(cell.childNodes.map((n) => (n.tagName ?? n.data)), ["one", "BR", "two", "BR", "three"]);
  assert.doesNotMatch(body.textContent, /<br/i);
  assert.equal(body.querySelectorAll("td").length, 4);
});

test("markdown table: other markup stays text, and rows keep the header's width", async () => {
  const env = await boot();
  const md = [
    "| One | Two | Three |",
    "|---|---|---|",
    "| <b>bold?</b> <script>x</script> | b |",
    "| a | b | c | d \\| e |",
  ].join("\n");
  const table = env.call("renderMarkdown", md).querySelector("table");
  assert.equal(table.querySelectorAll("b").length + table.querySelectorAll("script").length, 0, "no element may be built from markup in a cell");
  const [head, short, long] = rows(table);
  assert.equal(head.length, 3);
  assert.equal(short.length, 3, "a short row is padded");
  assert.equal(short[0].textContent, "<b>bold?</b> <script>x</script>");
  assert.equal(short[2].textContent, "");
  assert.equal(long.length, 3, "an over-long row folds its extra pieces into the last cell");
  assert.equal(long[2].textContent, "c | d | e");
});

test("markdown table: the report's own shape renders end to end", async () => {
  const env = await boot();
  const md = [
    "# Brief check: draft.txt", "",
    "| Line | Proposition | Cited source | Quotes |", "|---:|---|---|---|",
    "| 7 | The notice said “pay \\| vacate” | Notice.pdf p. 2<br>fact `ab12` | QUOTE FOUND: “pay \\| vacate” |", "",
  ].join("\n");
  const root = env.call("renderMarkdown", md);
  const tds = root.querySelectorAll("td");
  assert.equal(tds.length, 4);
  assert.equal(tds[1].textContent, "The notice said “pay | vacate”");
  assert.equal(tds[2].querySelectorAll("br").length, 1);
  assert.equal(tds[3].textContent, "QUOTE FOUND: “pay | vacate”");
});

// --- cite check by default: the new-case form's two first-run questions ---

function openNewCase(env) {
  byText(env.document.body, "button", "New case").click();
  return env.document.getElementById("new-case-name").closest("form");
}

test("new case: asks for a key only when there is none, and search stays off unless ticked", async () => {
  const env = await boot();
  await env.flush();
  const form = openNewCase(env);
  assert.ok(env.document.getElementById("new-case-key"), "no key on this computer, so the form should offer the key field");
  const search = env.document.getElementById("new-case-search");
  assert.equal(search.checked, false, "search must start unticked");
  assert.match(form.textContent, /only bare citations/);
  env.document.getElementById("new-case-name").value = "Gamma v. Town";
  form.dispatchEvent(new Ev("submit"));
  await env.flush();
  env.only("POST", /^\/cases$/).respond({ case: { id: "c".repeat(16), title: "Gamma v. Town", rel: "Gamma v. Town", parent_id: null } });
  await env.flush();
  assert.equal(env.find("POST", /courtlistener-key/).length, 0, "an empty key box sends nothing");
  assert.equal(env.find("POST", /\/caselaw$/).length, 0, "search was not ticked, so nothing turns it on");
});

test("new case: a typed key and a ticked search box are each sent once, after the case exists", async () => {
  const env = await boot();
  await env.flush();
  const form = openNewCase(env);
  env.document.getElementById("new-case-name").value = "Gamma v. Town";
  env.document.getElementById("new-case-key").value = "synthetic-key-0000";
  env.document.getElementById("new-case-search").checked = true;
  form.dispatchEvent(new Ev("submit"));
  await env.flush();
  assert.equal(env.find("POST", /courtlistener-key/).length, 0, "nothing else goes before the case is made");
  env.only("POST", /^\/cases$/).respond({ case: { id: "c".repeat(16), title: "Gamma v. Town", rel: "Gamma v. Town", parent_id: null } });
  await env.flush();
  const key = env.only("POST", /^\/courtlistener-key$/);
  assert.deepEqual(key.json(), { key: "synthetic-key-0000" });
  key.respond({ key: "settings" });
  await env.flush();
  const on = env.only("POST", new RegExp(`^/cases/${"c".repeat(16)}/caselaw$`));
  assert.deepEqual(on.json(), { on: true });
});

test("new case: no key field when this computer already has one", async () => {
  const handler = (req) => (req.method === "GET" && req.path === "/session"
    ? { status: 200, body: { workspace: "/synthetic/workspace", operator: "tester", folders: ["Evidence"], layers: ["30s"], courtlistener_key: "settings" } }
    : fixtureHandler(req));
  const env = await boot({ handler });
  await env.flush();
  openNewCase(env);
  assert.equal(env.document.getElementById("new-case-key"), null);
  assert.ok(env.document.getElementById("new-case-search"));
});

// --- cite check by default: the Case law lookups panel ---

const scope = (state, extra = {}) => ({ on: state === "on" || state === "default", state, by_default: state === "default",
  used_today: 0, daily_limit: 100, turned_on_by: null, turned_on_at: null, ...extra });
const caselawState = ({ cite = "default", statutes = "default", search = "off", key = null } = {}) => ({
  on: scope(cite).on && scope(search).on, search_on: scope(search).on, cite_check_on: scope(cite).on,
  statute_check_on: scope(statutes).on, scopes: { search: scope(search), "cite-lookup": scope(cite) },
  statutes: scope(statutes, { daily_limit: 50 }), saved: { searches: 0, cites: 0 }, key,
  key_url: "https://www.courtlistener.com/profile/api/", what_is_sent: "Search words are sent.",
  by_default: "Cite checking is on for every case unless a person turns it off." });

async function openCaselaw(state) {
  const handler = (req) => (req.method === "GET" && req.path === `/cases/${A.id}/caselaw`
    ? { status: 200, body: { caselaw: state } } : fixtureHandler(req));
  const env = await boot({ handler });
  await env.go(caseHash(A, "caselaw"));
  await env.flush();
  return env;
}

test("case law panel: a case nobody has chosen for reads on by default, with each check and two plain buttons", async () => {
  const env = await openCaselaw(caselawState());
  const main = env.main();
  assert.ok(byText(main, "h2", "Cite checking: on by default"));
  const items = main.querySelectorAll("li").map((li) => li.textContent);
  assert.ok(items.includes("Case cites, to CourtListener: on by default · 0 used today of 100 a day"), items.join(" | "));
  assert.ok(items.includes("Statute and code cites, to Corpus (corpuslaw.us): on by default · 0 used today of 50 a day"));
  assert.ok(items.includes("Case law search, to CourtListener: off"));
  assert.ok(byText(main, "button", "Turn off citation checks"));
  assert.ok(byText(main, "button", "Turn on case law search"));
  assert.match(main.textContent, /not looked up until a CourtListener key is saved/);
  assert.match(main.textContent, /--scope statute-lookup/);
});

test("case law panel: turning citation checks off sends on:false once and redraws as off", async () => {
  const env = await openCaselaw(caselawState({ key: "settings" }));
  assert.doesNotMatch(env.main().textContent, /not looked up until/, "a key is saved");
  const btn = byText(env.main(), "button", "Turn off citation checks");
  btn.click();
  btn.click();  // disabled while the request is out
  await env.flush();
  const post = env.only("POST", new RegExp(`^/cases/${A.id}/caselaw$`));
  assert.deepEqual(post.json(), { on: false });
  post.respond({ caselaw: caselawState({ cite: "declined", key: "settings" }) });
  await env.flush();
  assert.ok(byText(env.main(), "h2", "Cite checking: off for this case (a person turned it off)"));
  assert.equal(env.main().querySelectorAll("button").filter((b) => /^Turn off/.test(b.textContent)).length, 0);
  assert.ok(byText(env.main(), "button", "Turn on case law search and citation checks"));
  assert.equal(env.toast(), "Case cite checks are off for this case.");
});

test("case law panel: turning search on sends on:true and says what search sends", async () => {
  const env = await openCaselaw(caselawState());
  assert.match(env.main().textContent, /Search words are sent\./);
  byText(env.main(), "button", "Turn on case law search").click();
  await env.flush();
  const post = env.only("POST", new RegExp(`^/cases/${A.id}/caselaw$`));
  assert.deepEqual(post.json(), { on: true });
  post.respond({ caselaw: caselawState({ cite: "on", search: "on" }) });
  await env.flush();
  assert.ok(byText(env.main(), "h2", "Cite checking: on"));
  assert.ok(byText(env.main(), "button", "Turn off citation checks and search"));
  assert.equal(env.main().querySelectorAll("button").filter((b) => /^Turn on/.test(b.textContent)).length, 0);
  assert.equal(env.toast(), "Case law search is on for this case.");
});
