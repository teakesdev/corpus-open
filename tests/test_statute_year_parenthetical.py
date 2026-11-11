"""A statute cite with a trailing year or publisher note in parentheses is matched to Corpus's hit.

What could go wrong, and so what these tests pin:

- A reply writes "Miss. Code Ann. § 89-7-27 (2021)" and the row reads NOT MATCHED IN CORPUS, although
  Corpus returned that section. The parenthetical stays in the cite the reply wrote, while Corpus's
  heading cite has none. The string sent to Corpus already drops it (statutelookup.payload), so the
  match must compare against that same string.
- The fix is too loose: a different section ("89-7-28") or a subsection marker ("(a)") matches the
  wrong heading. Both must stay unmatched.
- The plain cite, with no parenthetical, must keep matching.

The hit is a stand-in with the two attributes match_statute reads. Corpus is stubbed; nothing
reaches the network.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import PluginCase, guard  # noqa: E402

HEADING = "Miss. Code Ann. § 89-7-27 — Proceedings against tenant holding over."
HIT = types.SimpleNamespace(citation=HEADING, provenance={"url": None})

YEAR_REPLY = ("Under Miss. Code Ann. § 89-7-27 (2021) the landlord files a complaint. "
              "The court then hears the matter.")


class TestMatchStatuteWithParenthetical(unittest.TestCase):
    def test_year_in_parentheses_matches(self):
        self.assertIsNotNone(guard.match_statute("Miss. Code Ann. § 89-7-27 (2021)", [HIT]))

    def test_supplement_in_parentheses_matches(self):
        self.assertIsNotNone(guard.match_statute("Miss. Code Ann. § 89-7-27 (Supp. 2024)", [HIT]))

    def test_publisher_note_in_parentheses_matches(self):
        self.assertIsNotNone(guard.match_statute("Miss. Code Ann. § 89-7-27 (West 2021)", [HIT]))

    def test_plain_cite_still_matches(self):
        self.assertEqual(guard.match_statute("Miss. Code Ann. § 89-7-27", [HIT]),
                         (HEADING, None))

    def test_different_section_with_year_does_not_match(self):
        self.assertIsNone(guard.match_statute("Miss. Code Ann. § 89-7-28 (2021)", [HIT]))

    def test_subsection_marker_without_space_is_not_dropped(self):
        # "(a)" is a subsection marker, so the cite sent to Corpus keeps it and the heading
        # without it is a different cite.
        self.assertIsNone(guard.match_statute("Miss. Code Ann. § 89-7-27(a)", [HIT]))


class TestPluginReplyWithParenthetical(PluginCase):
    def test_year_in_parentheses_is_in_corpus_not_unmatched(self):
        self.grant_all()
        block = self.plugin().on_reply(response_text=YEAR_REPLY)
        self.assertIn("IN CORPUS", block)
        self.assertNotIn("NOT MATCHED", block)


if __name__ == "__main__":
    unittest.main()
