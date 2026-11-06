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
        self.assertIn("sends nothing unless you turn on case law lookups", shipped()["index.html"])

    def test_the_five_labels_match_the_store(self):
        from matterkit import store
        js = shipped()["app.js"]
        css = shipped()["app.css"]
        for label in store.VALID_STATUS:
            with self.subTest(label=label):
                self.assertIn(f'"{label}"', js)
                if label != "DOCUMENTED FACT":  # the default .fact line; every other label draws its own
                    self.assertIn(f'[data-label="{label}"]', css)


def _hex_rgb(value):
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    assert len(h) == 6, value
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def luminance(value):
    """WCAG 2.x relative luminance of a #rgb or #rrggbb colour."""
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in _hex_rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def colour_tokens(block):
    return dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?)\s*;", block))


class TestContrast(unittest.TestCase):
    """Colour tokens read from app.css itself, so a palette edit can't quietly drop below the target.

    Text needs 4.5:1 (WCAG 1.4.3). The edge of a field or toggle needs 3:1 against what it sits on (1.4.11);
    --rule stays a pale decorative line and is not held to that.
    """

    @classmethod
    def setUpClass(cls):
        css = re.sub(r"/\*.*?\*/", "", shipped()["app.css"], flags=re.S)
        cls.css = css
        light = colour_tokens(re.search(r":root\s*\{([^}]*)\}", css).group(1))
        cls.dark_own = colour_tokens(re.search(r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root\s*\{([^}]*)\}", css).group(1))
        cls.themes = {"light": light, "dark": {**light, **cls.dark_own}}  # the dark block overrides; the rest is inherited

    def test_control_edge_is_3_to_1_on_both_grounds_in_both_themes(self):
        self.assertIn("control-edge", self.dark_own, "the dark theme must set its own edge colour")
        for theme, t in self.themes.items():
            for ground in ("paper", "surface"):
                with self.subTest(theme=theme, ground=ground):
                    ratio = contrast(t["control-edge"], t[ground])
                    self.assertGreaterEqual(ratio, 3.0, f"{theme}: --control-edge on --{ground} is {ratio:.2f}:1")

    def test_text_and_action_colours_are_4_5_to_1_on_both_grounds_in_both_themes(self):
        for theme, t in self.themes.items():
            for token in ("ink", "ink-2", "action", "alarm"):
                for ground in ("paper", "surface"):
                    with self.subTest(theme=theme, token=token, ground=ground):
                        ratio = contrast(t[token], t[ground])
                        self.assertGreaterEqual(ratio, 4.5, f"{theme}: --{token} on --{ground} is {ratio:.2f}:1")

    def test_the_focus_ring_stands_out_on_both_grounds(self):
        for theme, t in self.themes.items():
            for ground in ("paper", "surface"):
                with self.subTest(theme=theme, ground=ground):
                    self.assertGreaterEqual(contrast(t["action"], t[ground]), 3.0)
        self.assertRegex(self.css, r":focus-visible\s*\{\s*outline:\s*3px solid var\(--action\)")

    def test_essential_control_boundaries_use_the_edge_token(self):
        for selector in ('input[type="text"], select, textarea', ".segmented button", ".drop"):
            with self.subTest(selector=selector):
                m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", self.css)
                self.assertIsNotNone(m, f"no rule for {selector}")
                border = re.search(r"border:\s*([^;]*);", m.group(1))
                self.assertIsNotNone(border)
                self.assertIn("var(--control-edge)", border.group(1))


if __name__ == "__main__":
    unittest.main()
