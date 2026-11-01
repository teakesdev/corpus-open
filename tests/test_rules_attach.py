"""Rules packs on a matter: attach, list, show, stale; deadlines and the MCP tool.

Synthetic rules and synthetic matters only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import rules  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY", "MATTER_DIR")}

LOCAL = "\n".join([
    "Rule 7.     MOTIONS",
    "(a)   Form. Every motion is in writing.",
    "(b)   Briefs.",
    "      (1)    Scope.",
    "      (2)    Copies.",
    "      (3)    Rebuttal is due seven days after the response.",
    "      (4)    Response time. A response is due fourteen days",
    "             after service of the motion.",
    "             (A)    The court may extend the time.",
    "      (5)    Length. Ten pages.",
    "(c)   Proposed orders.",
    "Rule 15.    AMENDED PLEADINGS",
    "A proposed amended pleading is attached to the motion.",
]) + "\n"

FEDERAL = "\n".join([
    "Rule 7. Pleadings Allowed; Form of Motions",
    "(a) Pleadings. Only these pleadings are allowed.",
    "Rule 15. Amended and Supplemental Pleadings",
    "(a) Amendments Before Trial.",
    "    (1) Amending as a Matter of Course. A party may amend once.",
    "    (2) Other Amendments. A party may amend with leave, freely given when justice so requires.",
    "(d) Supplemental Pleadings.",
]) + "\n"


def make_pack(root, name, text, **kw):
    src = os.path.join(root, f"{name}.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write(text)
    out = os.path.join(root, name)
    rules.build_pack(src, out, **kw)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = self._tmp.name
        self.dir = os.path.join(root, "case")
        self.cli("init", self.dir)
        self.local = make_pack(root, "local-pack", LOCAL, pack_id="example-local", version="2030-01-01",
                               title="Example Local Rules", cite_prefix="L.U.Civ.R.", current_as_of="2030-01-01")
        self.federal = make_pack(root, "federal-pack", FEDERAL, pack_id="example-frcp", version="2030-12-01",
                                 title="Example Federal Rules", cite_prefix="Fed. R. Civ. P.",
                                 current_as_of="2030-12-01")

    def cli(self, *argv, check=True):
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *argv],
                              capture_output=True, text=True, check=check, env=CLEAN_ENV)

    def attach(self, pack, *extra, check=True):
        return self.cli("rules", "attach", self.dir, pack, "--author", "agent:test-model",
                        "--directed-by", "Pat Example", *extra, check=check)

    def show(self, cite, *extra):
        return self.cli("rules", "show", self.dir, cite, "--today", "2031-01-01", *extra, check=False)

    def rows(self, sql, params=()):
        conn = sqlite3.connect(os.path.join(self.dir, ".matter", "matter.db"))
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def attachments(self):
        with open(os.path.join(self.dir, ".matter", "rules.json"), encoding="utf-8") as f:
            return json.load(f)

    def write_attachments(self, att):
        with open(os.path.join(self.dir, ".matter", "rules.json"), "w", encoding="utf-8") as f:
            json.dump(att, f)


class TestAttach(Base):
    def test_attach_copies_registers_records_and_verifies(self):
        out = self.attach(self.local)
        self.assertIn("example-local@2030-01-01 attached", out.stdout)
        rel = os.path.join("Research", "Rules", "example-local_2030-01-01")
        for name in ("manifest.json", "rules.md"):
            self.assertTrue(os.path.isfile(os.path.join(self.dir, rel, name)))
            self.assertEqual(len(self.rows("SELECT id FROM documents WHERE rel_path=?", (os.path.join(rel, name),))), 1)
        entry = self.attachments()["packs"][0]
        self.assertEqual((entry["id"], entry["version"], entry["path"]), ("example-local", "2030-01-01", rel))
        self.assertEqual(entry["attached_by"], "agent:test-model; directed-by:Pat Example")
        ev = self.rows("SELECT text, actors FROM events WHERE text LIKE 'rules pack%'")
        self.assertEqual(len(ev), 1)
        self.assertIn("agent:test-model", ev[0][1])
        self.assertIn("0 changed · 0 missing", self.cli("verify", self.dir).stdout)

    def test_attach_twice_changes_nothing(self):
        self.attach(self.local)
        out = self.attach(self.local)
        self.assertIn("already attached", out.stdout)
        self.assertEqual(len(self.attachments()["packs"]), 1)
        self.assertEqual(len(self.rows("SELECT id FROM events WHERE text LIKE 'rules pack%'")), 1)

    def test_new_version_needs_replace_and_keeps_history(self):
        self.attach(self.local)
        newer = make_pack(self._tmp.name, "local-2031", LOCAL, pack_id="example-local", version="2031-01-01",
                          title="Example Local Rules", cite_prefix="L.U.Civ.R.", current_as_of="2031-01-01")
        refused = self.attach(newer, check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--replace", refused.stderr)
        self.assertEqual(self.attachments()["packs"][0]["version"], "2030-01-01")
        self.attach(newer, "--replace")
        att = self.attachments()
        self.assertEqual([p["version"] for p in att["packs"]], ["2031-01-01"])
        self.assertEqual(att["history"][0]["version"], "2030-01-01")

    def test_second_pack_claiming_the_same_prefix_is_refused(self):
        self.attach(self.local)
        other = make_pack(self._tmp.name, "other", LOCAL, pack_id="other-local", version="1",
                          title="Other", cite_prefix="L.U.Civ.R.")
        out = self.attach(other, check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("same cites", out.stderr)

    def test_second_pack_with_the_same_anchor_prefix_is_refused(self):
        # anchor ids would name two packs' text, so `show lucivr-7` could answer from either
        self.attach(self.local)
        other = make_pack(self._tmp.name, "same-anchors", FEDERAL, pack_id="other", version="1", title="Other",
                          cite_prefix="Other R.", anchor_prefix="lucivr")
        out = self.attach(other, check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("anchor prefix 'lucivr'", out.stderr)
        self.assertEqual(len(self.attachments()["packs"]), 1)

    def test_hand_edited_attachments_are_checked_when_read(self):
        # .matter/rules.json is not a registered document, so its entries are checked every time it is read
        self.attach(self.local)
        other = make_pack(self._tmp.name, "same-anchors", FEDERAL, pack_id="other", version="1", title="Other",
                          cite_prefix="Other R.", anchor_prefix="lucivr")
        import shutil
        shutil.copytree(other, os.path.join(self.dir, "Research", "other"))
        att = self.attachments()
        pack = rules.load_pack(other)
        att["packs"].append({"id": "other", "version": "1", "path": os.path.join("Research", "other"),
                             "cite_prefix": "Other R.", "anchor_prefix": "lucivr",
                             "manifest_sha256": pack.manifest_sha256, "rules_md_sha256": pack.rules_md_sha256})
        self.write_attachments(att)
        listed = self.cli("rules", "list", self.dir, "--today", "2030-06-01").stdout
        self.assertIn("other@1", listed)
        self.assertIn("NOT USABLE: shares the anchor prefix 'lucivr' with example-local", listed)
        del att["packs"][1]["cite_prefix"]
        self.write_attachments(att)
        out = self.show("lucivr-7")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("refused:", out.stderr)
        self.assertIn("cite_prefix", out.stderr)

    def test_the_matter_folder_itself_is_not_a_pack(self):
        import shutil
        for name in ("manifest.json", "rules.md"):
            shutil.copy(os.path.join(self.local, name), self.dir)
        out = self.attach(self.dir, check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("refused:", out.stderr)
        self.assertNotIn("Traceback", out.stderr)

    def test_a_failed_event_leaves_the_pack_unattached(self):
        # the event row and .matter/rules.json are written together, or neither is
        from unittest import mock
        new_id = rules.store.new_id

        def failing(prefix=""):
            if prefix == "ev_":
                raise RuntimeError("event id failed")
            return new_id(prefix)
        with mock.patch.object(rules.store, "new_id", failing):
            with self.assertRaises(RuntimeError):
                rules.attach(self.dir, self.local, "agent:test-model")
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".matter", "rules.json")))
        self.assertEqual(self.rows("SELECT id FROM events WHERE text LIKE 'rules pack%'"), [])
        self.attach(self.local)  # the copied and registered files do not block a second try
        self.assertEqual(len(self.attachments()["packs"]), 1)

    def test_a_pack_already_inside_the_matter_is_registered_in_place(self):
        inside = os.path.join(self.dir, "Research", "my-rules")
        os.makedirs(os.path.dirname(inside), exist_ok=True)
        import shutil
        shutil.copytree(self.local, inside)
        self.attach(inside)
        self.assertEqual(self.attachments()["packs"][0]["path"], os.path.join("Research", "my-rules"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "Research", "Rules")))

    def test_a_tampered_pack_is_refused(self):
        with open(os.path.join(self.local, "rules.md"), "a", encoding="utf-8") as f:
            f.write("extra\n")
        out = self.attach(self.local, check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".matter", "rules.json")))

    def test_list_names_each_pack(self):
        self.attach(self.local)
        self.attach(self.federal)
        out = self.cli("rules", "list", self.dir, "--today", "2031-01-01").stdout
        self.assertIn("example-local@2030-01-01  L.U.Civ.R.", out)
        self.assertIn("example-frcp@2030-12-01  Fed. R. Civ. P.", out)

    def test_list_reads_dates_from_the_pack_as_stale_does(self):
        # SWE-2 r2: list read current_as_of from the rules.json entry, so a hand edit there showed a
        # state that stale, which reads the checked manifest, does not
        self.attach(self.local)  # current as of 2030-01-01
        att = self.attachments()
        att["packs"][0]["current_as_of"] = "2020-01-01"
        self.write_attachments(att)
        listed = self.cli("rules", "list", self.dir, "--today", "2030-06-01").stdout
        self.assertIn("current as of 2030-01-01 [ok]", listed)
        self.assertEqual(self.cli("rules", "stale", self.dir, "--today", "2030-06-01", check=False).returncode, 0)


class TestShow(Base):
    def setUp(self):
        super().setUp()
        self.attach(self.local)
        self.attach(self.federal)

    def test_subsection_text_with_its_children_and_the_version(self):
        out = self.show("L.U.Civ.R. 7(b)(4)")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("L.U.Civ.R. 7(b)(4) · pack example-local@2030-01-01 · current as of 2030-01-01", out.stdout)
        self.assertIn("**[7(b)(4)]** (4) Response time. A response is due fourteen days", out.stdout)
        self.assertIn("after service of the motion.", out.stdout)
        self.assertIn("(A) The court may extend the time.", out.stdout)  # its own child
        self.assertNotIn("Ten pages", out.stdout)  # the next sibling is not
        self.assertIn("not the official copy", out.stdout)
        self.assertNotIn("<a id=", out.stdout)

    def test_whole_rule(self):
        out = self.show("L.U.Civ.R. 7")
        self.assertEqual(out.returncode, 0)
        self.assertIn("(c) Proposed orders.", out.stdout)
        self.assertNotIn("AMENDED PLEADINGS", out.stdout)

    def test_same_number_in_two_rule_sets_resolves_by_prefix(self):
        local = self.show("L.U.Civ.R. 15").stdout
        federal = self.show("Fed. R. Civ. P. 15(a)(2)").stdout
        self.assertIn("A proposed amended pleading is attached", local)
        self.assertNotIn("freely given", local)
        self.assertIn("freely given when justice so requires", federal)
        self.assertIn("example-frcp@2030-12-01", federal)

    def test_bare_rule_lists_candidates_and_picks_none(self):
        out = self.show("Rule 15")
        self.assertEqual(out.returncode, 2)
        self.assertIn("L.U.Civ.R. 15 (pack example-local", out.stdout)
        self.assertIn("Fed. R. Civ. P. 15 (pack example-frcp", out.stdout)
        self.assertNotIn("·", out.stdout)  # no text was chosen
        picked = self.show("Rule 15", "--pack", "example-frcp")
        self.assertEqual(picked.returncode, 0)
        self.assertIn("Fed. R. Civ. P. 15 · pack example-frcp", picked.stdout)

    def test_bare_rule_in_only_one_pack_is_still_a_candidate(self):
        out = self.show("Rule 7(b)(4)")
        self.assertEqual(out.returncode, 2)
        self.assertIn("L.U.Civ.R. 7(b)(4) (pack example-local", out.stdout)

    def test_local_rule_in_words_reads_as_the_docket_local_rule(self):
        out = self.show("Local Rule 7(b)(4)")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("read as L.U.Civ.R. 7(b)(4)", out.stdout)

    def test_local_rule_words_match_brief_check(self):
        # brief check reads "Local Rules 7" and "Local Civil Rule 7" as the docket local_rule; so does show
        for cite in ("Local Rules 7(b)(4)", "Local Civil Rule 7(b)(4)."):
            out = self.show(cite)
            self.assertEqual(out.returncode, 0, out.stdout)
            self.assertIn("read as L.U.Civ.R. 7(b)(4)", out.stdout)

    def test_local_rule_words_with_spaces_before_marks_or_the_period(self):
        # SWE-2 r2: these resolved before brief check's words were reused, and prefix cites still take them
        for cite in ("Local Rule 7 (b)(4)", "Local Rule 7 (b) (4)", "Local Rule 7(b)(4) ."):
            out = self.show(cite)
            self.assertEqual(out.returncode, 0, f"{cite}: {out.stdout}")
            self.assertIn("read as L.U.Civ.R. 7(b)(4)", out.stdout)

    def test_anchor_id(self):
        out = self.show("lucivr-7-b-4-A")
        self.assertEqual(out.returncode, 0)
        self.assertIn("L.U.Civ.R. 7(b)(4)(A)", out.stdout)

    def test_missing_subsection_names_the_nearest_text(self):
        out = self.show("L.U.Civ.R. 7(b)(9)")
        self.assertEqual(out.returncode, 1)
        self.assertIn("has no L.U.Civ.R. 7(b)(9)", out.stdout)
        self.assertIn("nearest text it has is L.U.Civ.R. 7(b)", out.stdout)

    def test_a_pack_changed_after_attach_is_not_used(self):
        rel = os.path.join(self.dir, "Research", "Rules", "example-local_2030-01-01", "rules.md")
        with open(rel, "a", encoding="utf-8") as f:
            f.write("edited\n")
        out = self.show("L.U.Civ.R. 7(b)(4)")
        self.assertEqual(out.returncode, 1)
        self.assertNotIn("fourteen", out.stdout)

    def test_not_a_rule(self):
        out = self.show("42 U.S.C. § 1983")
        self.assertEqual(out.returncode, 1)
        self.assertIn("not a rule cite", out.stdout)


class TestStale(Base):
    def test_past_the_review_window(self):
        self.attach(self.local)
        old = self.cli("rules", "stale", self.dir, "--today", "2031-06-01", check=False)
        self.assertEqual(old.returncode, 1)
        self.assertIn("[STALE] example-local@2030-01-01", old.stdout)
        fresh = self.cli("rules", "stale", self.dir, "--today", "2030-06-01", check=False)
        self.assertEqual(fresh.returncode, 0)
        self.assertIn("[ok]", fresh.stdout)

    def test_no_date_is_unknown_and_flagged(self):
        undated = make_pack(self._tmp.name, "undated", LOCAL, pack_id="undated", version="1", title="Undated",
                            cite_prefix="L.U.Civ.R.")
        self.attach(undated)
        out = self.cli("rules", "stale", self.dir, check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("[UNKNOWN] undated@1", out.stdout)

    def test_a_date_after_today_is_flagged(self):
        self.attach(self.local)  # current as of 2030-01-01
        out = self.cli("rules", "stale", self.dir, "--today", "2029-06-01", check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("[UNKNOWN] example-local@2030-01-01", out.stdout)
        self.assertIn("after today", out.stdout)

    def test_a_pack_changed_after_attach_is_flagged(self):
        self.attach(self.local)
        rel = os.path.join(self.dir, "Research", "Rules", "example-local_2030-01-01", "rules.md")
        with open(rel, "a", encoding="utf-8") as f:
            f.write("edited\n")
        out = self.cli("rules", "stale", self.dir, "--today", "2030-06-01", check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("[NOT USABLE] example-local@2030-01-01", out.stdout)

    def test_a_malformed_review_window_is_refused(self):
        self.attach(self.local)
        att = self.attachments()
        att["packs"][0]["review_after_days"] = "soon"
        self.write_attachments(att)
        out = self.cli("rules", "stale", self.dir, check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("refused:", out.stderr)
        self.assertNotIn("Traceback", out.stderr)


class TestDeadlineRule(Base):
    def add(self, rule):
        out = self.cli("deadline", "add", self.dir, "--label", f"Reply {rule}", "--due", "2099-01-01",
                       "--rule", rule)
        return out, self.rows("SELECT rule_source FROM deadlines WHERE label=?", (f"Reply {rule}",))[0][0]

    def test_free_text_is_unchanged_without_packs(self):
        out, src = self.add("FRCP 12(a)")
        self.assertEqual(src, "FRCP 12(a)")
        self.assertNotIn("↳", out.stdout)

    def test_a_rule_in_a_pack_records_pack_version_and_anchor(self):
        self.attach(self.local)
        _, src = self.add("L.U.Civ.R. 7(b)(4)")
        self.assertEqual(src, "L.U.Civ.R. 7(b)(4) [example-local@2030-01-01#lucivr-7-b-4]")
        _, by_anchor = self.add("lucivr-7-b-4")
        self.assertEqual(by_anchor, src)

    def test_unresolved_rule_stays_free_text_with_a_note(self):
        self.attach(self.local)
        self.attach(self.federal)
        out, src = self.add("Rule 15")
        self.assertEqual(src, "Rule 15")
        self.assertIn("bare rule cite", out.stdout)
        out, src = self.add("L.U.Civ.R. 7(b)(9)")
        self.assertEqual(src, "L.U.Civ.R. 7(b)(9)")
        self.assertIn("stored as free text", out.stdout)


class TestMcp(Base):
    def call(self, name, args):
        env = dict(CLEAN_ENV, MATTER_DIR=self.dir)
        reqs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}}]
        out = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input="\n".join(map(json.dumps, reqs)),
                             capture_output=True, text=True, env=env, check=True)
        listed, called = (json.loads(l) for l in out.stdout.strip().splitlines())
        return [t["name"] for t in listed["result"]["tools"]], called["result"]

    def test_rules_show_tool(self):
        self.attach(self.local)
        names, res = self.call("matter.rules_show", {"cite": "L.U.Civ.R. 7(b)(4)"})
        self.assertIn("matter.rules_show", names)
        self.assertFalse(res.get("isError"))
        self.assertIn("example-local@2030-01-01", res["content"][0]["text"])
        self.assertIn("fourteen days", res["content"][0]["text"])

    def test_not_found_is_an_error(self):
        self.attach(self.local)
        _, res = self.call("matter.rules_show", {"cite": "L.U.Civ.R. 99"})
        self.assertTrue(res.get("isError"))


if __name__ == "__main__":
    unittest.main()
