"""Static checks on the shipped page files: nothing parses case text as HTML, nothing is fetched
from outside, and nothing would need CSP 'unsafe-inline'.

Design: docs/superpowers/specs/2026-10-31-local-frontend-design.md §4. No server needed.
Run: python3 -m unittest discover -s tests -v
"""
import os
import re
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import webui  # noqa: E402


def shipped():
    out = {}
    for name, _ in webui.STATIC.values():
        with open(os.path.join(webui.STATIC_DIR, name), encoding="utf-8") as f:
            out[name] = f.read()
    return out


class TestShippedFiles(unittest.TestCase):
    def test_the_allowlist_is_the_folder(self):
        self.assertEqual(sorted(os.listdir(webui.STATIC_DIR)), sorted(n for n, _ in webui.STATIC.values()))

    def test_no_html_parsing_sinks(self):
        js = shipped()["app.js"]
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                     "DOMParser", "createContextualFragment", "srcdoc"):
            with self.subTest(sink=sink):
                self.assertNotIn(sink, js)
        self.assertIsNone(re.search(r"set(Timeout|Interval)\(\s*['\"`]", js))  # string timers are eval

    def test_nothing_external(self):
        for name, text in shipped().items():
            with self.subTest(name=name):
                self.assertIsNone(re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/", text, re.I))
                self.assertNotIn("@import", text)

    def test_no_inline_script_style_or_handlers(self):
        html = shipped()["index.html"]
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [])
        self.assertNotIn("<style", html)
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", html, re.I))
        self.assertIsNone(re.search(r"\sstyle\s*=", html, re.I))
        self.assertIsNone(re.search(r"javascript:", "".join(shipped().values()), re.I))

    def test_page_says_it_is_not_legal_advice(self):
        self.assertIn("Not legal advice", shipped()["index.html"])
        self.assertIn("doesn't file or send anything", shipped()["index.html"])

    def test_the_five_labels_match_the_store(self):
        from matterkit import store
        js = shipped()["app.js"]
        css = shipped()["app.css"]
        for label in store.VALID_STATUS:
            with self.subTest(label=label):
                self.assertIn(f'"{label}"', js)
                if label != "DOCUMENTED FACT":  # the default .fact line; every other label draws its own
                    self.assertIn(f'[data-label="{label}"]', css)


if __name__ == "__main__":
    unittest.main()
