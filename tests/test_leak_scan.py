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
                     "x/astra_transcript.md", "x/codex-conversation.json", "x/claude-session.jsonl",
                     "x/claude-session-export.md"):
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
        self.write("claude-session.jsonl", b"\x00\x01", mode="wb")
        tip = self.commit("add")
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}")
        self.assertEqual([(f.kind, f.where.split(":", 1)[1]) for f in found], [("session-transcript", "claude-session.jsonl")])

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
        self.write("claude-session.jsonl", "{}\n")
        tip = self.commit("two")
        self.write("a.txt", "clean\n")
        os.remove(os.path.join(self.repo, "claude-session.jsonl"))
        self.commit("three")
        found = leak_scan.scan_rev(self.repo, tip)
        self.assertEqual(sorted((f.kind, f.where) for f in found),
                         [("home-path", f"{tip[:10]}:a.txt:1"), ("session-transcript", f"{tip[:10]}:claude-session.jsonl")])
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


# ---- SWE-2's review of 81e8344 (docs/superpowers/reviews/2026-11-01-leak-scan-code/) -----------------------

def write_patterns(case, body, encoding="utf-8"):
    d = tempfile.TemporaryDirectory()
    case.addCleanup(d.cleanup)
    p = os.path.join(d.name, "patterns.txt")
    with open(p, "w", encoding=encoding) as fh:
        fh.write(body)
    return p


class TestLitPatterns(unittest.TestCase):
    """F1 (P1): a `lit:` line must be a literal of its own text whatever surrounds it."""

    def literal(self, body, encoding="utf-8"):
        return leak_scan.load_patterns(write_patterns(self, body, encoding))

    def test_leading_whitespace_before_lit_is_ignored(self):
        pats = self.literal("  lit:zebra\n\tlit:quagga\n")
        self.assertEqual([p.regex.pattern for p in pats], ["zebra", "quagga"])
        self.assertTrue(pats[0].regex.search("a zebra here"))

    def test_the_prefix_is_not_case_sensitive(self):
        for body in ("LIT:zebra\n", "Lit:zebra\n"):
            with self.subTest(body=body):
                pats = self.literal(body)
                self.assertEqual(pats[0].regex.pattern, "zebra")

    def test_a_byte_order_mark_on_the_first_line_is_dropped(self):
        pats = self.literal("lit:zebra\nlit:quagga\n", encoding="utf-8-sig")
        self.assertEqual(len(pats), 2)
        self.assertTrue(pats[0].regex.search("a zebra here"))

    def test_a_regex_line_after_a_byte_order_mark_still_works(self):
        pats = self.literal("zeb+ra\n", encoding="utf-8-sig")
        self.assertTrue(pats[0].regex.search("a zebbra here"))

    def test_the_text_after_the_prefix_is_kept_whole(self):
        pats = self.literal("lit:Foo v. Bar (2d)  \n")
        self.assertTrue(pats[0].regex.search("see foo v. bar (2d) here"))


class TestHookRemoteAndRefs(GitCase):
    """F2, F8 and the stdin idiom: the sample hook."""
    ZERO = "0" * 40

    def hook(self, lines, first="origin", env=None):
        e = dict(os.environ, LEAK_SCAN_PY=os.path.join(KIT, "scripts", "leak_scan.py"))
        e.pop(leak_scan.PATTERNS_ENV, None)
        e.update(env or {})
        return subprocess.run(["sh", SHELL_SAMPLE, first, "https://example.invalid/r.git"],
                              cwd=self.repo, input=lines, capture_output=True, text=True, env=e)

    def two_commits(self):
        self.write("a.txt", "p " + MAC + "dan/x\n")
        old = self.commit("old, already on the remote")
        self.write("a.txt", "p " + MAC + "dan/x\nclean\n")
        new = self.commit("new")
        self.git("update-ref", "refs/remotes/origin/main", old)
        return old, new

    def test_a_url_instead_of_a_remote_name_scans_only_what_no_remote_branch_has(self):
        _, new = self.two_commits()
        r = self.hook(f"refs/heads/b {new} refs/heads/b {self.ZERO}\n", first="https://example.invalid/r.git")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_named_remote_that_is_not_configured_falls_back_the_same_way(self):
        _, new = self.two_commits()
        r = self.hook(f"refs/heads/b {new} refs/heads/b {self.ZERO}\n", first="nosuchremote")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_ref_named_for_a_case_is_blocked_though_its_commits_are_clean(self):
        self.write("a.txt", "x\n")
        sha = self.commit("clean")
        pats = write_patterns(self, "lit:zebra\n")
        r = self.hook(f"refs/heads/zebra-case {sha} refs/heads/zebra-case {self.ZERO}\n",
                      env={leak_scan.PATTERNS_ENV: pats})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("[case-pattern] pattern #1", r.stdout)
        self.assertNotIn("zebra", r.stdout)

    def test_an_ordinary_ref_name_passes(self):
        self.write("a.txt", "x\n")
        sha = self.commit("clean")
        pats = write_patterns(self, "lit:zebra\n")
        r = self.hook(f"refs/heads/feat/ok {sha} refs/heads/feat/ok {self.ZERO}\n", env={leak_scan.PATTERNS_ENV: pats})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_every_pushed_ref_is_read_even_when_the_first_is_clean(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("a.txt", "x\n" + MAC + "dan/y\n")
        bad = self.commit("leak")
        r = self.hook(f"refs/heads/one {base} refs/heads/one {self.ZERO}\nrefs/heads/two {bad} refs/heads/two {base}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_the_scanner_call_does_not_inherit_the_hooks_stdin(self):
        with open(SHELL_SAMPLE, encoding="utf-8") as fh:
            lines = [l for l in fh if "--range" in l and l.strip().startswith('"$python"')]
        self.assertEqual(len(lines), 1)
        self.assertIn("</dev/null", lines[0])

    def test_check_authors_is_wired_behind_an_opt_in_variable(self):
        with open(SHELL_SAMPLE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("LEAK_SCAN_CHECK_AUTHORS", text)
        self.assertIn("--check-authors", text)


class TestDiffHeaderParsing(GitCase):
    """F3: quoted names and diff.* prefix settings must not break path extraction."""

    def tip_and_base(self, files, seed="seed.txt"):
        self.write(seed, "x\n")
        base = self.commit("base")
        for name, text in files.items():
            self.write(name, text)
        return base, self.commit("add")

    def test_a_name_with_a_tab_is_still_name_checked(self):
        name = "a\tb-swe2-transcript.md"
        base, tip = self.tip_and_base({name: "clean\n"})
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}")
        self.assertEqual([(f.kind, f.where) for f in found], [("session-transcript", f"{tip[:10]}:{name}")])

    def test_a_name_with_a_quote_and_a_backslash(self):
        name = 'q"uote\\back-swe2-transcript.md'
        base, tip = self.tip_and_base({name: "clean\n"})
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}")
        self.assertEqual([(f.kind, f.where) for f in found], [("session-transcript", f"{tip[:10]}:{name}")])

    def test_a_leak_in_a_file_with_a_quoted_name_has_its_name_and_line(self):
        name = "a\tb.txt"
        base, tip = self.tip_and_base({name: "ok\n" + MAC + "dan/x\n"})
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}")
        self.assertEqual([(f.kind, f.where) for f in found], [("home-path", f"{tip[:10]}:{name}:2")])

    def test_prefix_settings_in_the_users_git_config_do_not_change_the_result(self):
        for key, value in [("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true"),
                           ("diff.srcPrefix", "SRC/"), ("diff.dstPrefix", "DST/")]:
            with self.subTest(setting=key):
                self.git("config", key, value)
                base, tip = self.tip_and_base({"swe2-transcript-%s.md" % key.split(".")[1]: "p " + MAC + "dan/x\n"},
                                              seed="seed-%s.txt" % key.split(".")[1])
                found = leak_scan.scan_range(self.repo, f"{base}..{tip}")
                name = "swe2-transcript-%s.md" % key.split(".")[1]
                self.assertEqual(sorted((f.kind, f.where) for f in found),
                                 [("home-path", f"{tip[:10]}:{name}:1"), ("session-transcript", f"{tip[:10]}:{name}")])
                self.git("config", "--unset", key)


class TestUnreadableAndInternalErrors(GitCase):
    """F4: an unreadable file is an error with exit 2, never a traceback, and a stray exception is one line."""

    def run_cli(self, argv, env=None):
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

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read anything")
    def test_an_unreadable_file_is_exit_2_and_the_rest_is_still_scanned(self):
        self.write("locked.txt", "x\n")
        self.write("leaky.txt", MAC + "dan/x\n")
        self.commit("c")
        path = os.path.join(self.repo, "locked.txt")
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o644)
        rc, out, err = self.run_cli(["--tree", self.repo])
        self.assertEqual(rc, 2)
        self.assertIn("leaky.txt:1: [home-path]", out)
        self.assertIn("cannot read locked.txt", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn(self.repo, err)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read anything")
    def test_an_unreadable_file_whose_name_matches_a_case_pattern_is_masked(self):
        self.write("zebra-notes.txt", "x\n")
        self.commit("c")
        path = os.path.join(self.repo, "zebra-notes.txt")
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o644)
        rc, _, err = self.run_cli(["--tree", self.repo, "--patterns", write_patterns(self, "lit:zebra\n")])
        self.assertEqual(rc, 2)
        self.assertNotIn("zebra", err)

    def test_an_unexpected_exception_is_one_sanitized_line_and_exit_2(self):
        self.write("a.txt", "x\n")
        self.commit("c")
        real = leak_scan.scan_tree

        def boom(*a, **k):
            raise RuntimeError("secret detail in " + self.repo)

        leak_scan.scan_tree = boom
        self.addCleanup(setattr, leak_scan, "scan_tree", real)
        rc, _, err = self.run_cli(["--tree", self.repo])
        self.assertEqual(rc, 2)
        self.assertIn("unexpected RuntimeError", err)
        self.assertNotIn("secret detail", err)
        self.assertNotIn("Traceback", err)

    def test_an_unexpected_exception_can_be_shown_for_debugging(self):
        self.write("a.txt", "x\n")
        self.commit("c")
        real = leak_scan.scan_tree

        def boom(*a, **k):
            raise RuntimeError("visible")

        leak_scan.scan_tree = boom
        self.addCleanup(setattr, leak_scan, "scan_tree", real)
        with self.assertRaises(RuntimeError):
            self.run_cli(["--tree", self.repo], env={"LEAK_SCAN_DEBUG": "1"})


class TestSensitiveFileNames(unittest.TestCase):
    """F5: an address or a home path inside a file name is a finding, and the location hides it."""

    def test_an_email_in_a_file_name_is_a_finding_and_masked_in_the_location(self):
        found = leak_scan.scan_path_name("docs/" + "jane.doe" + AT + "gmail.com-notes.md")
        self.assertEqual(kinds(found), ["email"])
        self.assertEqual(found[0].where, "docs/<masked>")
        self.assertEqual(found[0].detail, "jane.doe" + AT + "gmail.com")

    def test_a_home_path_at_the_start_of_a_path_is_a_finding(self):
        found = leak_scan.scan_path_name("Users/dan/notes.md")
        self.assertEqual(kinds(found), ["home-path"])
        self.assertNotIn("dan", found[0].where)

    def test_a_directory_that_happens_to_be_called_users_is_not(self):
        self.assertEqual(leak_scan.scan_path_name("vendor/Users/dan/notes.md"), [])

    def test_ordinary_top_level_directories_are_not_home_directories(self):
        # a web app's home/ and a Django users/ are common; only a macOS-style Users/<name>/ tree is read as a home
        for name in ("home/index.html", "home/views.py", "home/dan/notes.md", "users/models.py", "Users_guide/a.md"):
            with self.subTest(name=name):
                self.assertEqual(leak_scan.scan_path_name(name), [])

    def test_an_allowed_email_in_a_name_is_neither_flagged_nor_masked(self):
        name = "docs/me" + AT + "mydomain.org.md"
        self.assertEqual(leak_scan.scan_path_name(name, allow_emails=("*" + AT + "mydomain.org*",)), [])
        self.assertEqual(leak_scan.mask_path(name, allow_emails=("*" + AT + "mydomain.org*",)), name)

    def test_content_findings_in_such_a_file_do_not_print_its_name(self):
        with tempfile.TemporaryDirectory() as d:
            name = "jane.doe" + AT + "gmail.com-notes.md"
            with open(os.path.join(d, name), "w") as fh:
                fh.write("see " + MAC + "dan/x\n")
            found = leak_scan.scan_tree(d)
        self.assertEqual(sorted(f.where for f in found), ["<masked>", "<masked>:1"])


class TestTranscriptNameRule(unittest.TestCase):
    """F6: names that only look like a session transcript are not flagged; real ones still are."""

    def test_ordinary_files_are_not_flagged(self):
        for name in ("data/train.jsonl", "evals/cases.jsonl", "chat-export.py", "session-history.md",
                     "matterkit/session_export.py", "docs/agent-notes.md", "docs/chat-history.md",
                     "docs/session-export.md", "x/run.jsonl",
                     # the launch logs and usage files review folders carry (two lines of timestamps)
                     "docs/superpowers/reviews/x/swe2.log", "docs/superpowers/reviews/x/astra.log",
                     "docs/superpowers/reviews/x/swe2-run1.log", "docs/superpowers/reviews/x/usage.json"):
            with self.subTest(name=name):
                self.assertEqual(leak_scan.scan_path_name(name), [])

    def test_session_transcripts_still_are(self):
        for name in ("docs/r/swe2-transcript.md", "x/claude-session-transcript.txt", "x/astra_transcript.md",
                     "x/codex-conversation.json", "docs/agent-conversation.md", "x/chat_transcript.md",
                     "x/claude-session.jsonl", "x/claude-session-export.md",
                     "x/3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b.jsonl"):
            with self.subTest(name=name):
                self.assertEqual(kinds(leak_scan.scan_path_name(name)), ["session-transcript"])

    def test_session_log_text_is_found_by_its_content_whatever_the_file_is_called(self):
        line = '{"parent' + 'Uuid":null,"session' + 'Id":"3f2a1b4c","type":"assistant"}\n'
        self.assertEqual(kinds(leak_scan.scan_text(line, "x/anything.jsonl")), ["session-transcript"])

    def test_the_knobs_exist_for_a_name_that_is_flagged_anyway(self):
        found = leak_scan.scan_path_name("docs/agent-conversation.md")
        self.assertEqual(kinds(found), ["session-transcript"])


class TestEnvironmentKnobsAndNames(GitCase):
    """F6 and F8: --exclude and --allow-email from the environment, and --name for ref names."""

    def call(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        old = dict(os.environ)
        for k in (leak_scan.PATTERNS_ENV, "LEAK_SCAN_EXCLUDE", "LEAK_SCAN_ALLOW_EMAIL"):
            os.environ.pop(k, None)
        os.environ.update(env or {})
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = leak_scan.main(argv)
        finally:
            os.environ.clear()
            os.environ.update(old)
        return rc, out.getvalue(), err.getvalue()

    def test_exclude_globs_can_come_from_the_environment(self):
        self.write("docs/agent-conversation.md", "clean\n")
        self.write("keep/agent-conversation.md", "clean\n")
        rc, out, _ = self.call(["--tree", self.repo], env={"LEAK_SCAN_EXCLUDE": "docs/*"})
        self.assertEqual(rc, 1)
        self.assertEqual(out.strip(), "keep/agent-conversation.md: [session-transcript] "
                                      "file name looks like an assistant session transcript")

    def test_allowed_emails_can_come_from_the_environment_separated_by_commas_or_newlines(self):
        self.write("a.txt", "me" + AT + "mydomain.org and you" + AT + "gmail.com and x" + AT + "other.net\n")
        rc, out, _ = self.call(["--tree", self.repo],
                               env={"LEAK_SCAN_ALLOW_EMAIL": "*" + AT + "mydomain.org\n*" + AT + "other.net, "})
        self.assertEqual(rc, 1)
        self.assertEqual(out.strip(), "a.txt:1: [email] you" + AT + "gmail.com")

    def test_name_flag_checks_a_ref_name_against_case_patterns(self):
        pats = write_patterns(self, "lit:zebra\n")
        rc, out, _ = self.call(["--name", "refs/heads/zebra-case", "--patterns", pats])
        self.assertEqual(rc, 1)
        self.assertEqual(out.strip(), "name:refs/heads/<masked>: [case-pattern] pattern #1")

    def test_name_flag_is_clean_for_an_ordinary_name_and_repeatable(self):
        pats = write_patterns(self, "lit:zebra\n")
        rc, out, _ = self.call(["--name", "refs/heads/feat/ok", "--name", "refs/tags/v1", "--patterns", pats])
        self.assertEqual((rc, out), (0, ""))

    def test_name_flag_also_applies_the_builtin_name_rules(self):
        rc, out, _ = self.call(["--name", "refs/heads/swe2-transcript"])
        self.assertEqual(rc, 1)
        self.assertIn("[session-transcript]", out)


class TestPatternsPathIsNotEchoed(GitCase):
    """F9: the error for an unreadable patterns file names where the path came from, not the path."""

    def test_flag(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = leak_scan.main(["--tree", self.repo, "--patterns", "/nonexistent-dir/private-name/p.txt"])
        self.assertEqual(rc, 2)
        self.assertNotIn("private-name", err.getvalue())
        self.assertIn("--patterns", err.getvalue())

    def test_environment_variable(self):
        out, err = io.StringIO(), io.StringIO()
        old = dict(os.environ)
        os.environ[leak_scan.PATTERNS_ENV] = "/nonexistent-dir/private-name/p.txt"
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = leak_scan.main(["--tree", self.repo])
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(rc, 2)
        self.assertNotIn("private-name", err.getvalue())
        self.assertIn(leak_scan.PATTERNS_ENV, err.getvalue())

    def test_load_patterns_error_does_not_contain_the_path(self):
        with self.assertRaises(leak_scan.ConfigError) as cm:
            leak_scan.load_patterns("/nonexistent-dir/private-name/p.txt")
        self.assertNotIn("private-name", str(cm.exception))


class TestSeparatorsInCommitMessages(GitCase):
    """F16: a record or unit separator inside a commit message must not corrupt or crash the range scan."""

    def test_record_separator_in_a_message(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("a.txt", "y\n")
        tip = self.commit("subject\n\nbody with \x1e a record separator\nand " + MAC + "dan/leak")
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}")
        self.assertEqual(kinds(found), ["home-path"])
        self.assertTrue(found[0].where.startswith(f"{tip[:10]}:message:"))

    def test_unit_separator_in_a_message(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        self.write("a.txt", "y\n")
        tip = self.commit("subject\n\nbody \x1f unit\n" + MAC + "dan/leak")
        self.assertEqual(kinds(leak_scan.scan_range(self.repo, f"{base}..{tip}")), ["home-path"])

    def test_several_commits_with_separators_keep_their_own_messages(self):
        self.write("a.txt", "x\n")
        base = self.commit("base")
        for i in range(3):
            self.write("a.txt", f"v{i}\n")
            tip = self.commit(f"c{i} \x1e \x1f \n\n" + (MAC + "dan/leak" if i == 1 else "clean"))
        found = leak_scan.scan_range(self.repo, f"{base}..{tip}")
        self.assertEqual(kinds(found), ["home-path"])


class TestWorkflowFile(unittest.TestCase):
    """F7: the CI job scans pushes as well as pull requests, and --check-authors is wired behind a variable."""

    def setUp(self):
        with open(os.path.join(KIT, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
            self.text = fh.read()

    def test_a_push_gets_a_range_scan(self):
        self.assertIn("github.event_name == 'push'", self.text)
        self.assertIn("github.event.before", self.text)
        self.assertIn("github.sha", self.text)

    def test_a_new_branch_push_without_a_before_sha_is_handled(self):
        self.assertIn("0000000000000000000000000000000000000000", self.text)

    def test_check_authors_is_opt_in_through_a_repository_variable(self):
        self.assertIn("vars.LEAK_SCAN_CHECK_AUTHORS", self.text)
        self.assertIn("--check-authors", self.text)

    def test_the_exclude_and_allow_email_knobs_come_from_repository_variables(self):
        self.assertIn("vars.LEAK_SCAN_EXCLUDE", self.text)
        self.assertIn("vars.LEAK_SCAN_ALLOW_EMAIL", self.text)

    def test_the_fork_limit_is_documented_in_the_file(self):
        self.assertIn("fork", self.text.lower())


if __name__ == "__main__":
    unittest.main()
