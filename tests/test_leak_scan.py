"""scripts/leak_scan.py: finds home paths, personal emails, assistant-session transcripts
and caller-supplied case patterns in a tree, a revision or a git range.

Synthetic strings only. Strings that would trip the scanner on this very file are
built from pieces, so the repo's own tree scan stays clean.
Run: python3 -m unittest tests.test_leak_scan -v
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(KIT, "scripts"))

import leak_scan  # noqa: E402

MAC = "/" + "Users" + "/"
LIN = "/" + "home" + "/"
WIN = "C:" + "\\" + "Users" + "\\"
AT = "@"
SHELL_SAMPLE = os.path.join(KIT, "scripts", "pre-push.sample")


def kinds(findings):
    return sorted(f.kind for f in findings)


class TestScanText(unittest.TestCase):
    def test_mac_linux_and_windows_home_paths_are_found(self):
        for path in (MAC + "alice/dev/x.py", LIN + "bob/work", WIN + "carol\\Documents"):
            with self.subTest(path=path):
                found = leak_scan.scan_text("open('" + path + "')\n", "f.py")
                self.assertEqual(kinds(found), ["home-path"])
                self.assertEqual(found[0].where, "f.py:1")

    def test_placeholder_and_ci_home_paths_are_not_found(self):
        for text in ("~/dev/kit", "$HOME/dev", MAC + "<name>/x", MAC + "runner/work",
                     LIN + "runner/work", MAC + "Shared/x", "see " + MAC + " for homes",
                     "src" + MAC + "x"):
            with self.subTest(text=text):
                self.assertEqual(leak_scan.scan_text(text + "\n", "f"), [])

    def test_personal_emails_are_found(self):
        found = leak_scan.scan_text("mail jane.doe" + AT + "gmail.com or x" + AT + "yahoo.co.uk\n", "f")
        self.assertEqual(kinds(found), ["email", "email"])
        self.assertEqual([f.detail for f in found], ["jane.doe" + AT + "gmail.com", "x" + AT + "yahoo.co.uk"])

    def test_reserved_noreply_and_file_like_emails_are_not_found(self):
        ok = ["noreply" + AT + "anthropic.com", "x" + AT + "example.com", "x" + AT + "example.org",
              "a" + AT + "b.example", "a" + AT + "b.invalid", "a" + AT + "b.test",
              "12345+name" + AT + "users.noreply.github.com", "git" + AT + "github.com:o/r.git",
              "a" + AT + "b.txt", "a" + AT + "b.docx", "x" + AT + "localhost"]
        for e in ok:
            with self.subTest(email=e):
                self.assertEqual(leak_scan.scan_text("<" + e + ">\n", "f"), [])

    def test_decorators_and_handles_are_not_emails(self):
        self.assertEqual(leak_scan.scan_text("    " + AT + "unittest.skipIf(x)\n" + AT + "name here\n", "f"), [])

    def test_allow_email_glob(self):
        text = "me" + AT + "mydomain.org and you" + AT + "gmail.com\n"
        found = leak_scan.scan_text(text, "f", allow_emails=("*" + AT + "mydomain.org",))
        self.assertEqual([f.detail for f in found], ["you" + AT + "gmail.com"])

    def test_session_markers_are_found(self):
        for marker in ("<system" + "-reminder>", "</system" + "-reminder>",
                       "the user's private" + " global instructions for all projects",
                       "# user" + "Email", "Session" + "Start:startup hook success"):
            with self.subTest(marker=marker):
                self.assertEqual(kinds(leak_scan.scan_text("x " + marker + " y\n", "f")), ["session-transcript"])

    def test_line_numbers_count_from_one(self):
        found = leak_scan.scan_text("ok\nok\nsee " + MAC + "dan/x\n", "doc.md")
        self.assertEqual([f.where for f in found], ["doc.md:3"])

    def test_inline_allow_marker_silences_builtin_classes_only(self):
        line = MAC + "dan/x  # leak-scan: allow\n"
        self.assertEqual(leak_scan.scan_text(line, "f"), [])
        pats = [leak_scan.CasePattern(1, leak_scan.re.compile("zebra", leak_scan.re.I))]
        found = leak_scan.scan_text("Zebra " + line, "f", patterns=pats)
        self.assertEqual(kinds(found), ["case-pattern"])

    def test_one_line_can_carry_two_classes(self):
        found = leak_scan.scan_text(MAC + "ed/x and ed" + AT + "gmail.com\n", "f")
        self.assertEqual(kinds(found), ["email", "home-path"])


class TestCasePatterns(unittest.TestCase):
    def write(self, body):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = os.path.join(d.name, "patterns.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        return p

    def test_file_format_comments_blanks_literals_and_regexes(self):
        p = self.write("# my case\n\nlit:Foo v. Bar (2d)\n\\b\\d:\\d\\d-cv-\\d+\\b\n")
        pats = leak_scan.load_patterns(p)
        self.assertEqual([x.number for x in pats], [1, 2])
        self.assertTrue(pats[0].regex.search("see foo v. bar (2D) here"))
        self.assertFalse(pats[0].regex.search("foo v. bar 2d"))
        self.assertTrue(pats[1].regex.search("No. 2:25-cv-01234"))

    def test_match_never_reveals_the_pattern_or_the_text(self):
        pats = leak_scan.load_patterns(self.write("lit:Quincy Zebra\n"))
        found = leak_scan.scan_text("Quincy Zebra signed\n", "f", patterns=pats)
        self.assertEqual(kinds(found), ["case-pattern"])
        shown = found[0].render(redact=False)
        self.assertNotIn("Quincy", shown)
        self.assertNotIn("Zebra", shown)
        self.assertIn("pattern #1", shown)
        self.assertEqual(found[0].as_dict()["detail"], "pattern #1")

    def test_missing_file_is_a_configuration_error(self):
        with self.assertRaises(leak_scan.ConfigError):
            leak_scan.load_patterns("/nonexistent/patterns.txt")

    def test_bad_regex_error_names_the_line_but_not_its_text(self):
        p = self.write("fine\n(unclosed secretword\n")
        with self.assertRaises(leak_scan.ConfigError) as cm:
            leak_scan.load_patterns(p)
        self.assertIn("line 2", str(cm.exception))
        self.assertNotIn("secretword", str(cm.exception))

    def test_empty_file_loads_no_patterns(self):
        self.assertEqual(leak_scan.load_patterns(self.write("# nothing\n")), [])


class TestPathNames(unittest.TestCase):
    def test_assistant_session_transcript_names_are_found(self):
        for name in ("docs/r/swe2-transcript.md", "a/swe2-transcript-r2.md", "x/claude-session-transcript.txt",
                     "x/astra_transcript.md", "x/codex-conversation.json", "x/run.jsonl", "x/Session-Export.md"):
            with self.subTest(name=name):
                self.assertEqual(kinds(leak_scan.scan_path_name(name)), ["session-transcript"])

    def test_court_transcript_names_are_not_assistant_transcripts(self):
        for name in ("docs/deposition-transcript-format.md", "tests/fixtures/transcript.txt",
                     "matterkit/transcription.py", "docs/swe2-review.md", "docs/session-notes.md"):
            with self.subTest(name=name):
                self.assertEqual(leak_scan.scan_path_name(name), [])

    def test_case_pattern_in_a_file_name_is_found(self):
        pats = [leak_scan.CasePattern(3, leak_scan.re.compile("zebra", leak_scan.re.I))]
        found = leak_scan.scan_path_name("cases/Zebra-v-Lion/notes.md", patterns=pats)
        self.assertEqual(kinds(found), ["case-pattern"])
        self.assertNotIn("Zebra", found[0].render(redact=False))
        self.assertEqual(found[0].where, "cases/<masked>/notes.md")

    def test_content_findings_in_a_file_whose_name_matches_do_not_print_the_name(self):
        pats = [leak_scan.CasePattern(1, leak_scan.re.compile("zebra", leak_scan.re.I))]
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "Zebra-notes"))
            with open(os.path.join(d, "Zebra-notes", "a.md"), "w") as fh:
                fh.write("see " + MAC + "dan/x\n")
            found = leak_scan.scan_tree(d, patterns=pats)
        self.assertEqual(sorted(f.where for f in found), ["<masked>/a.md", "<masked>/a.md:1"])


def run(args, cwd, env=None, check=True):
    e = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
             GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t" + AT + "example.com",
             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t" + AT + "example.com")
    e.update(env or {})
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=e)
    if check and r.returncode:
        raise AssertionError(f"{args} failed: {r.stderr}")
    return r


class GitCase(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.repo = os.path.join(d.name, "r")
        os.mkdir(self.repo)
        self.git("init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *a, check=True, env=None):
        return run(["git", *a], self.repo, env=env, check=check)

    def write(self, rel, text, mode="w"):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
            fh.write(text)

    def commit(self, msg="c", env=None):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg, env=env)
        return self.git("rev-parse", "HEAD").stdout.strip()


class TestScanRange(GitCase):
    def test_clean_range_has_no_findings(self):
        self.write("a.txt", "hello\n")
        base = self.commit("base")
        self.write("a.txt", "hello\nworld\n")
        tip = self.commit("add world")
        self.assertEqual(leak_scan.scan_range(self.repo, f"{base}..{tip}"), [])

    def test_added_line_is_found_with_commit_file_and_new_file_line_number(self):
        self.write("a.txt", "one\ntwo\n")
        base = self.commit("base")
        self.write("a.txt", "one\ntwo\npath " + MAC + "dan/x\nfour\n")
        tip = self.commit("add")
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}")
        self.assertEqual(kinds(found), ["home-path"])
        self.assertEqual(found[0].where, f"{tip[:10]}:a.txt:3")

    def test_commit_message_is_scanned(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("a.txt", "y\n")
        tip = self.commit("fix thing\n\nreviewed in " + MAC + "dan/wt\nby me" + AT + "gmail.com")
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}")
        self.assertEqual(kinds(found), ["email", "home-path"])
        self.assertTrue(all(f.where.startswith(f"{tip[:10]}:message:") for f in found))

    def test_leak_added_then_removed_inside_the_range_is_still_found(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("a.txt", "x\n" + MAC + "dan/leak\n")
        self.commit("leak")
        self.write("a.txt", "x\n")
        tip = self.commit("redact")
        self.assertEqual(kinds(leak_scan.scan_range(self.repo, f"{base}..{tip}")), ["home-path"])

    def test_removing_a_leak_is_not_a_finding(self):
        self.write("a.txt", "x\n" + MAC + "dan/leak\n")
        base = self.commit("base")
        self.write("a.txt", "x\n")
        tip = self.commit("redact")
        self.assertEqual(leak_scan.scan_range(self.repo, f"{base}..{tip}"), [])

    def test_transcript_file_name_is_found_even_when_its_text_is_clean(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("docs/review/swe2-transcript.md", "nothing sensitive\n")
        tip = self.commit("add")
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}")
        self.assertEqual(kinds(found), ["session-transcript"])
        self.assertEqual(found[0].where, f"{tip[:10]}:docs/review/swe2-transcript.md")

    def test_binary_file_content_is_skipped_but_its_name_is_checked(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("img.bin", b"\x00\x01" + (MAC + "dan/x").encode(), mode="wb")
        self.write("run.jsonl", b"\x00\x01", mode="wb")
        tip = self.commit("add")
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}")
        self.assertEqual([(f.kind, f.where.split(":", 1)[1]) for f in found], [("session-transcript", "run.jsonl")])

    def test_merge_conflict_resolution_is_scanned(self):
        self.write("a.txt", "base\n")
        root = self.commit("base")
        self.git("checkout", "-q", "-b", "side")
        self.write("a.txt", "side\n")
        self.commit("side")
        self.git("checkout", "-q", "main")
        self.write("a.txt", "main\n")
        self.commit("main")
        self.git("merge", "side", check=False)
        self.write("a.txt", "resolved " + MAC + "dan/x\n")
        tip = self.commit("merge side")
        found = leak_scan.scan_range(self.repo, f"{root}..{tip}")
        self.assertEqual(kinds(found), ["home-path"])
        self.assertEqual(found[0].where, f"{tip[:10]}:a.txt:1")

    def test_spec_with_not_clause_covers_a_new_branch(self):
        self.write("a.txt", "x\n")
        self.commit("base")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("a.txt", "x\n" + MAC + "dan/y\n")
        tip = self.commit("new")
        found = leak_scan.scan_range(self.repo, f"{tip} --not --remotes=origin")
        self.assertEqual(kinds(found), ["home-path"])

    def test_bad_range_is_a_configuration_error(self):
        self.write("a.txt", "x\n")
        self.commit("base")
        with self.assertRaises(leak_scan.ConfigError):
            leak_scan.scan_range(self.repo, "nonexistent..alsonot")

    def test_author_check_is_opt_in_and_ignores_noreply(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("a.txt", "y\n")
        tip = self.commit("c", env={"GIT_AUTHOR_EMAIL": "me" + AT + "gmail.com",
                                   "GIT_COMMITTER_EMAIL": "1+n" + AT + "users.noreply.github.com"})
        self.assertEqual(leak_scan.scan_range(self.repo, f"{base}..{tip}"), [])
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}", check_authors=True)
        self.assertEqual([(f.kind, f.detail) for f in found], [("author-email", "me" + AT + "gmail.com")])

    def test_range_locations_never_print_a_path_that_matches_a_case_pattern(self):
        pats = [leak_scan.CasePattern(1, leak_scan.re.compile("zebra", leak_scan.re.I))]
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("Zebra-notes/swe2-transcript.md", "see " + MAC + "dan/x\n")
        tip = self.commit("add")
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}", patterns=pats)
        self.assertEqual(kinds(found), ["case-pattern", "home-path", "session-transcript"])
        self.assertTrue(all("ebra" not in f.render(redact=False) for f in found))
        self.assertTrue(all(f.where.startswith(f"{tip[:10]}:<masked>/") for f in found))

    def test_case_pattern_in_added_line_and_message(self):
        pats = [leak_scan.CasePattern(1, leak_scan.re.compile("quincy", leak_scan.re.I))]
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("a.txt", "x\nQuincy was here\n")
        tip = self.commit("about quincy")
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}", patterns=pats)
        self.assertEqual(sorted(f.where.split(":", 1)[1] for f in found), ["a.txt:2", "message:1"])
        self.assertTrue(all("uincy" not in f.render(redact=False) for f in found))


class TestScanTreeAndRev(GitCase):
    def test_tree_scans_tracked_and_untracked_but_not_ignored_files(self):
        self.write(".gitignore", "ignored/\n")
        self.write("tracked.txt", "p " + MAC + "a/x\n")
        self.commit("c")
        self.write("untracked.txt", "p " + LIN + "b/x\n")
        self.write("ignored/skip.txt", "p " + LIN + "c/x\n")
        found = leak_scan.scan_tree(self.repo)
        self.assertEqual(sorted(f.where for f in found), ["tracked.txt:1", "untracked.txt:1"])

    def test_tree_outside_git_walks_the_directory_and_skips_dot_git(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            for rel, text in ((".git/config", MAC + "a/x\n"), ("sub/f.md", MAC + "b/x\n")):
                p = os.path.join(d, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as fh:
                    fh.write(text)
            found = leak_scan.scan_tree(d)
            self.assertEqual([f.where for f in found], [os.path.join("sub", "f.md") + ":1"])

    def test_exclude_glob_skips_paths(self):
        self.write("keep.txt", MAC + "a/x\n")
        self.write("docs/skip.txt", MAC + "a/x\n")
        self.commit("c")
        found = leak_scan.scan_tree(self.repo, excludes=("docs/*",))
        self.assertEqual([f.where for f in found], ["keep.txt:1"])

    def test_file_names_are_checked_in_tree_mode(self):
        self.write("x/swe2-transcript.md", "clean\n")
        self.commit("c")
        self.assertEqual(kinds(leak_scan.scan_tree(self.repo)), ["session-transcript"])

    def test_rev_scans_a_commit_without_a_checkout(self):
        self.write("a.txt", "clean\n")
        self.commit("one")
        self.write("a.txt", "p " + MAC + "a/x\n")
        self.write("run.jsonl", "{}\n")
        tip = self.commit("two")
        self.write("a.txt", "clean\n")
        os.remove(os.path.join(self.repo, "run.jsonl"))
        self.commit("three")
        found = leak_scan.scan_rev(self.repo, tip)
        self.assertEqual(sorted((f.kind, f.where) for f in found),
                         [("home-path", f"{tip[:10]}:a.txt:1"), ("session-transcript", f"{tip[:10]}:run.jsonl")])
        self.assertEqual(leak_scan.scan_rev(self.repo, "HEAD"), [])


class TestCli(GitCase):
    def call(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        old = dict(os.environ)
        os.environ.pop(leak_scan.PATTERNS_ENV, None)
        os.environ.update(env or {})
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = leak_scan.main(argv)
        finally:
            os.environ.clear()
            os.environ.update(old)
        return rc, out.getvalue(), err.getvalue()

    def test_exit_0_when_clean_and_1_on_a_hit(self):
        self.write("a.txt", "fine\n")
        self.commit("c")
        rc, out, _ = self.call(["--tree", self.repo])
        self.assertEqual((rc, out), (0, ""))
        self.write("b.txt", MAC + "dan/x\n")
        rc, out, _ = self.call(["--tree", self.repo])
        self.assertEqual(rc, 1)
        self.assertIn("b.txt:1: [home-path] " + MAC + "dan/x", out)

    def test_redact_hides_matches_for_every_class(self):
        self.write("b.txt", MAC + "dan/x\n")
        rc, out, _ = self.call(["--tree", self.repo, "--redact"])
        self.assertEqual(rc, 1)
        self.assertIn("b.txt:1: [home-path] <redacted>", out)
        self.assertNotIn("dan", out)

    def test_json_output(self):
        self.write("b.txt", MAC + "dan/x\n")
        rc, out, _ = self.call(["--tree", self.repo, "--format", "json"])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out), [{"where": "b.txt:1", "kind": "home-path", "detail": MAC + "dan/x"}])

    def test_patterns_from_flag_and_from_environment_and_flag_wins(self):
        self.write("b.txt", "Quincy and Zebra\n")
        with tempfile.TemporaryDirectory() as d:
            q, z = os.path.join(d, "q"), os.path.join(d, "z")
            for p, body in ((q, "lit:quincy\n"), (z, "lit:zebra\n")):
                with open(p, "w") as fh:
                    fh.write(body)
            rc, out, err = self.call(["--tree", self.repo, "--patterns", q])
            self.assertEqual((rc, out.strip()), (1, "b.txt:1: [case-pattern] pattern #1"))
            self.assertIn("1 case pattern", err)
            rc, out, _ = self.call(["--tree", self.repo], env={leak_scan.PATTERNS_ENV: z})
            self.assertEqual((rc, out.strip()), (1, "b.txt:1: [case-pattern] pattern #1"))
            self.write("b.txt", "only Zebra\n")
            rc, out, _ = self.call(["--tree", self.repo, "--patterns", q], env={leak_scan.PATTERNS_ENV: z})
            self.assertEqual(rc, 0, out)

    def test_absence_of_case_patterns_is_announced_not_silent(self):
        self.write("a.txt", "fine\n")
        rc, _, err = self.call(["--tree", self.repo])
        self.assertEqual(rc, 0)
        self.assertIn("no case patterns configured", err)

    def test_missing_patterns_file_is_exit_2_even_if_the_tree_is_clean(self):
        self.write("a.txt", "fine\n")
        rc, _, err = self.call(["--tree", self.repo, "--patterns", "/nonexistent/p.txt"])
        self.assertEqual(rc, 2)
        self.assertIn("patterns", err)
        rc, _, _ = self.call(["--tree", self.repo], env={leak_scan.PATTERNS_ENV: "/nonexistent/p.txt"})
        self.assertEqual(rc, 2)

    def test_range_and_rev_modes_via_cli(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("a.txt", MAC + "dan/x\n")
        tip = self.commit("leak")
        rc, out, _ = self.call(["--repo", self.repo, "--range", f"{base}..{tip}"])
        self.assertEqual(rc, 1)
        self.assertIn(f"{tip[:10]}:a.txt:1: [home-path]", out)
        rc, out, _ = self.call(["--repo", self.repo, "--rev", base])
        self.assertEqual((rc, out), (0, ""))

    def test_a_mode_is_required_and_bad_range_is_exit_2(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            leak_scan.main([])
        self.assertEqual(cm.exception.code, 2)
        self.write("a.txt", "x\n")
        self.commit("base")
        rc, _, err = self.call(["--repo", self.repo, "--range", "nope..nada"])
        self.assertEqual(rc, 2)
        self.assertIn("git", err)

    def test_script_runs_as_a_program(self):
        self.write("a.txt", MAC + "dan/x\n")
        script = os.path.join(KIT, "scripts", "leak_scan.py")
        r = subprocess.run([sys.executable, script, "--tree", self.repo, "--redact"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("[home-path] <redacted>", r.stdout)


@unittest.skipUnless(os.path.exists(SHELL_SAMPLE), "scripts/pre-push.sample not present")
class TestPrePushSample(GitCase):
    ZERO = "0" * 40

    def hook(self, lines, env=None):
        e = dict(os.environ, LEAK_SCAN_PY=os.path.join(KIT, "scripts", "leak_scan.py"))
        e.pop(leak_scan.PATTERNS_ENV, None)
        e.update(env or {})
        return subprocess.run(["sh", SHELL_SAMPLE, "origin", "git@example.com:o/r.git"],
                              cwd=self.repo, input=lines, capture_output=True, text=True, env=e)

    def test_blocks_a_push_whose_new_commits_leak(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("a.txt", MAC + "dan/x\n")
        tip = self.commit("leak")
        r = self.hook(f"refs/heads/b {tip} refs/heads/b {base}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("home-path", r.stdout)

    def test_allows_a_clean_push_and_a_branch_deletion(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("a.txt", "y\n")
        tip = self.commit("clean")
        self.assertEqual(self.hook(f"refs/heads/b {tip} refs/heads/b {base}\n").returncode, 0)
        self.assertEqual(self.hook(f"(delete) {self.ZERO} refs/heads/b {base}\n").returncode, 0)

    def test_new_branch_scans_commits_the_remote_does_not_have(self):
        self.write("a.txt", "x\n")
        self.commit("base")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("a.txt", MAC + "dan/x\n")
        tip = self.commit("leak")
        r = self.hook(f"refs/heads/new {tip} refs/heads/new {self.ZERO}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
