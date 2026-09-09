"""Regression tests for kit-cite-0.4 syntax gaps (chair 2026-09-09).

These strings are DEVELOPMENT examples (holdout_v2 misses restated in new
sentences). Passing them is regression coverage, not acceptance.
"""
import unittest

from matterkit import citations


def extracted(text: str) -> list[str]:
    return [r["raw_fragment"] for r in citations.extract_citations(text)
            if r["status"] == "extracted"]


def exact(text: str, needle: str) -> None:
    rows = citations.extract_citations(text)
    hits = [r for r in rows if r["status"] == "extracted"
            and text[r["char_start"]:r["char_end"]] == needle
            and r["raw_fragment"] == needle]
    if not hits:
        raise AssertionError(
            f"{needle!r} not an exact extracted span in {text!r}; got {extracted(text)}"
        )


class TestParserCapability(unittest.TestCase):
    def test_experimental_at_capability_level(self):
        self.assertEqual(citations.PARSER_CAPABILITY, "experimental")
        self.assertEqual(citations.PARSER_ID, "kit-cite-0.4")

    def test_unparsable_section_is_still_occurrence_failure(self):
        rows = citations.extract_citations("See § for details.")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "extraction-failed")


class TestSyntaxGaps(unittest.TestCase):
    def test_coordinated_chapter_list(self):
        text = "offenses under chapter 109A, 109B, 110, or 117 of this title."
        exact(text, "chapter 109A, 109B, 110, or 117")

    def test_chapter_abbreviation(self):
        exact("Derived from act Apr. 20, 1871, ch. 645, § 1.", "ch. 645")
        exact("See also ch. 321 for related procedure.", "ch. 321")

    def test_nested_section_parentheticals(self):
        text = "the fine in §\u202f330016(1)(L) applies."
        exact(text, "§\u202f330016(1)(L)")

    def test_capital_section_word(self):
        exact("Compare Section 80 of the 1909 Act.", "Section 80")

    def test_usc_without_section_sign(self):
        exact("Authority: 15 U.S.C. 78j.", "15 U.S.C. 78j")
        # with-sign form must still win on the same title
        exact("See 15 U.S.C. § 1681a for definitions.", "15 U.S.C. § 1681a")

    def test_public_law_spelled_out(self):
        exact("Enacted as Public Law 107-204 on July 30, 2002.", "Public Law 107-204")
        exact("Earlier: Pub. L. 96–170.", "Pub. L. 96–170")

    def test_house_bill_identifier(self):
        exact("The House passed H.R. 3763 that day.", "H.R. 3763")

    def test_florida_session_law_two_digit_year(self):
        exact("History. — s. 1, ch. 89-154; s. 2, ch. 2013-180.", "s. 1, ch. 89-154")
        exact("History. — s. 1, ch. 89-154; s. 2, ch. 2013-180.", "s. 2, ch. 2013-180")


if __name__ == "__main__":
    unittest.main()
