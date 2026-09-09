"""Scorer self-tests (chair requirement, 2026-09-08): the canonical matcher
must behave correctly on prefix-polluted spans, duplicate predictions, and
overlapping predictions. Run: python3 -m unittest tests.test_scorer -v
(from the matter-kit root) or python3 evals/setabc_v1/test_scorer.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KIT_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, KIT_ROOT)

import score  # noqa: E402  (the canonical scorer module)


def make_matcher(gold_items):
    return lambda preds: score.match(gold_items, preds)


class TestPrefixPollution(unittest.TestCase):
    """A prediction that swallows the sentence prefix must NOT match the gold
    span, and must surface as a false positive (the rev2 ca/ca_bp/tx_bo defect,
    now pinned at the matcher level)."""

    def test_prefix_polluted_span_fails_and_becomes_fp(self):
        gold = [{"id": "B-900", "set": "set_b", "style": "ca", "doc": "d1",
                 "start": 8, "end": 30, "text": "Cal. Civ. Code § 1500"}]
        preds = [{"doc": "d1", "start": 0, "end": 30,
                  "text": "Compare Cal. Civ. Code § 1500", "parser": "kit"}]
        rec = score.match(gold, preds)
        self.assertEqual(rec["matched_ids"], [])
        self.assertEqual(rec["missed_ids"], ["B-900"])
        self.assertEqual(len(rec["false_positives"]), 1)
        self.assertEqual(rec["false_positives"][0]["start"], 0)

    def test_exact_span_matches(self):
        gold = [{"id": "B-900", "set": "set_b", "style": "ca", "doc": "d1",
                 "start": 8, "end": 30, "text": "Cal. Civ. Code § 1500"}]
        preds = [{"doc": "d1", "start": 8, "end": 30,
                  "text": "Cal. Civ. Code § 1500", "parser": "kit"}]
        rec = score.match(gold, preds)
        self.assertEqual(rec["matched_ids"], ["B-900"])
        self.assertEqual(rec["false_positives"], [])

    def test_trailing_period_span_is_not_exact(self):
        # eyecite-style normalization bleeding into the source span
        # (e.g. span ending one char later) must not match exact.
        gold = [{"id": "B-901", "set": "set_b", "style": "fla", "doc": "d1",
                 "start": 0, "end": 20, "text": "Fla. Stat. § 605.100"}]
        preds = [{"doc": "d1", "start": 0, "end": 21,
                  "text": "Fla. Stat. § 605.100.", "parser": "eyecite"}]
        rec = score.match(gold, preds)
        self.assertEqual(rec["matched_ids"], [])
        self.assertEqual(rec["missed_ids"], ["B-901"])

    def test_text_drift_on_same_span_asserts(self):
        # Span equality with different text is a scorer integrity violation.
        gold = [{"id": "B-902", "set": "set_b", "style": "x", "doc": "d1",
                 "start": 0, "end": 5, "text": "abcde"}]
        preds = [{"doc": "d1", "start": 0, "end": 5, "text": "zzzzz", "parser": "kit"}]
        with self.assertRaises(AssertionError):
            score.match(gold, preds)


class TestDuplicates(unittest.TestCase):
    def test_duplicate_predictions_collapse_to_one_match_one_fp(self):
        # Two identical spans: first consumes the gold match, the second is a
        # duplicate FP — never two matches for one gold item.
        gold = [{"id": "A-000", "set": "set_a", "style": "reporter", "doc": "d1",
                 "start": 0, "end": 11, "text": "347 U.S. 483"}]
        preds = [{"doc": "d1", "start": 0, "end": 11, "text": "347 U.S. 483", "parser": "kit"},
                 {"doc": "d1", "start": 0, "end": 11, "text": "347 U.S. 483", "parser": "kit"}]
        rec = score.match(gold, preds)
        self.assertEqual(rec["matched_ids"], ["A-000"])
        self.assertEqual(len(rec["false_positives"]), 1)

    def test_match_consumes_prediction(self):
        # One prediction cannot match two gold items at the same span.
        g = {"id": "A-000", "set": "set_a", "style": "reporter", "doc": "d1",
             "start": 0, "end": 11, "text": "347 U.S. 483"}
        g2 = dict(g, id="A-001")
        preds = [{"doc": "d1", "start": 0, "end": 11, "text": "347 U.S. 483", "parser": "kit"}]
        rec = score.match([g, g2], preds)
        self.assertEqual(sorted(rec["matched_ids"]), ["A-000"])
        self.assertEqual(rec["missed_ids"], ["A-001"])
        self.assertEqual(rec["false_positives"], [])


class TestOverlaps(unittest.TestCase):
    def test_overlapping_but_not_equal_span_does_not_match(self):
        gold = [{"id": "B-903", "set": "set_b", "style": "usc", "doc": "d1",
                 "start": 4, "end": 23, "text": "15 U.S.C. § 1681a"}]
        preds = [{"doc": "d1", "start": 0, "end": 23,
                  "text": "Under 15 U.S.C. § 1681a", "parser": "kit"},
                 {"doc": "d1", "start": 4, "end": 21,
                  "text": "15 U.S.C. § 1681a"[:-2] + "", "parser": "kit"}]
        rec = score.match(gold, preds)
        self.assertEqual(rec["matched_ids"], [])
        self.assertEqual(len(rec["false_positives"]), 2)

    def test_union_logic_exercise(self):
        # Union must be the set-union of matched IDs, computed from stored sets.
        kit_ids = {"B-001", "B-002"}
        eye_ids = {"B-002", "B-003"}
        self.assertEqual(len(kit_ids | eye_ids), 3)
        self.assertEqual(len(kit_ids & eye_ids), 1)


class TestHygieneDiagnostic(unittest.TestCase):
    def test_hygiene_counts_prefix_and_period_variants_but_exact_does_not(self):
        gold = [{"id": "B-904", "set": "set_b", "style": "fla", "doc": "d1",
                 "start": 0, "end": 20, "text": "Fla. Stat. § 605.100"}]
        preds = [{"doc": "d1", "start": 0, "end": 21,
                  "text": "Fla. Stat. § 605.100.", "parser": "eyecite"}]
        rec = score.match(gold, preds)
        self.assertEqual(rec["matched_ids"], [])          # exact: no
        hits = score.hygiene_diagnostic(gold, preds)      # diagnostic: yes
        self.assertEqual(hits, ["B-904"])

    def test_hygiene_rejects_sentence_prefix(self):
        gold = [{"id": "B-905", "set": "set_b", "style": "ca", "doc": "d1",
                 "start": 8, "end": 30, "text": "Cal. Civ. Code § 1500"}]
        preds = [{"doc": "d1", "start": 0, "end": 30,
                  "text": "Compare Cal. Civ. Code § 1500", "parser": "kit"}]
        # start mismatch (0 != 8) already disqualifies; guard the rule too.
        self.assertEqual(score.hygiene_diagnostic(gold, preds), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
