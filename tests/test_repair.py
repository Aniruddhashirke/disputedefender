"""Repair tests. The two drafts below are real local-Gemma output (case_005, E7 and E8 excluded) quoted from screenshots."""
import os
import unittest
from datetime import date

from dd.draft import MockBackend, build_prompt
from dd.evidence import build_evidence, classify
from dd.models import Issue
from dd.parse import load_case
from dd.pipeline import run_case
from dd.verify import repair_letter, verify_letter

ROOT = os.path.join(os.path.dirname(__file__), "..", "cases")
TODAY = date(2026, 3, 12)

ATTEMPT_1 = """Hello,

The buyer says the color is different from the listing photos [E1]. E9, E5 state that the scarf is hand-dyed and each piece may vary slightly from the photos.
Order 1001555, a hand-dyed wool scarf, was placed on 2026-02-25 [E2] and shipped on 2026-02-26 via UPS, tracking 1Z999AA10123456784 [E3].
Delivery confirmation shows the item was delivered on 2026-03-02 [E4], and the buyer contacted us on 2026-03-04 at 12:30 stating the scarf looks more orange than the photos [E6].
Sincerely,"""

ATTEMPT_2 = """Hello,

The buyer says the color is different from the listing photos [E1]. E9, E5 state that the scarf is hand-dyed and each piece may vary slightly from the photos.
Order 1001555, a hand-dyed wool scarf, was placed on 2026-02-25 for $85.00 [E2].
It shipped on 2026-02-26 via UPS, tracking 1Z999AA10123456784 [E3] and was delivered on 2026-03-02 [E4].
A photo of the scarf packed on 2026-02-26 is on file [E5].
The buyer contacted us on 2026-03-04 at 12:30 stating the scarf looks more orange than the photos [E6].
The listing description states: \u201cHand-dyed wool scarf. Each piece is dyed by hand, so color may vary slightly from the photos\u201d [E9].

Sincerely,"""


def setup_ev():
    d = load_case(os.path.join(ROOT, "case_005_nad_with_listing"))
    return d, build_evidence(d, classify(d))


class RealDrafts(unittest.TestCase):
    def setUp(self):
        self.d, self.e = setup_ev()

    def test_verifier_flags_what_it_should_in_real_attempt_1(self):
        issues, ok = verify_letter(ATTEMPT_1, self.e)
        self.assertEqual(len(issues), 1)
        self.assertIn("after the last citation", issues[0].problem)  # 'E9, E5 state that...' is an uncited claim

    def test_verifier_flags_both_problems_in_real_attempt_2(self):
        issues, _ = verify_letter(ATTEMPT_2, self.e)
        self.assertEqual(len(issues), 2)
        self.assertTrue(any("photo" in i.problem for i in issues))

    def test_repair_attempt_1_keeps_the_good_clause_and_drops_the_bad_tail(self):
        fixed, notes = repair_letter(ATTEMPT_1, self.e)
        self.assertEqual(verify_letter(fixed, self.e)[0], [])
        self.assertIn("The buyer says the color is different from the listing photos [E1].", fixed)
        self.assertNotIn("E9, E5 state", fixed)
        self.assertTrue(any("dropped uncited text" in n for n in notes))

    def test_repair_attempt_2_rewrites_photo_clause_and_keeps_listing_quote(self):
        fixed, notes = repair_letter(ATTEMPT_2, self.e)
        self.assertEqual(verify_letter(fixed, self.e)[0], [])
        self.assertIn("A photo file named scarf_packed_2026-02-26.jpg is on file [E5].", fixed)
        self.assertNotIn("packed on 2026-02-26 is on file", fixed)
        self.assertIn("Each piece is dyed by hand, so color may vary slightly from the photos", fixed)
        self.assertIn("It shipped on 2026-02-26 via UPS, tracking 1Z999AA10123456784 [E3] and was delivered on 2026-03-02 [E4].", fixed)
        self.assertTrue(any("photo clause" in n for n in notes))

    def test_pipeline_ends_with_a_verified_letter_when_the_model_keeps_failing(self):
        class Stubborn:
            name = "stubborn"
            def generate(self, prompt, evidence, dtype, issues=None):
                return ATTEMPT_2
        r = run_case(self.d, Stubborn(), today=TODAY, exclude=["E7", "E8"])
        self.assertEqual(r.attempts, 2)
        self.assertEqual(verify_letter(r.letter, [e for e in r.evidence if e.id not in ("E7", "E8")])[0], [])
        self.assertTrue(r.degraded)               # something was dropped
        self.assertTrue(r.repairs)
        self.assertTrue(r.issues)                  # what the model got wrong is still reported
        self.assertIn("The buyer says the color is different from the listing photos [E1].", r.letter)
        self.assertIn("I ask that this case be resolved", r.letter)


# Real Gemma draft from the final run (identical on both attempts): the only problem was the invented photo claim.
PHOTO_ONLY = """Hello,

The buyer says the color is different from the listing photos [E1]. Each piece is dyed by hand, so color may vary slightly from the photos [E9], and a photo on file named scarf_packed_2026-02-26.jpg shows the scarf packed in its original condition [E5].
The order was placed on 2026-02-25 [E2], shipped on 2026-02-26 via UPS, tracking 1Z999AA10123456784 [E3], and delivered on 2026-03-02 [E4].
On 2026-03-04 at 12:30, the buyer stated that the scarf looks more orange than the photos [E6].
Sincerely,"""


class PhotoOnlyShortcut(unittest.TestCase):
    def counting(self, text):
        class B:
            name = "b"
            calls = 0
            def generate(self, prompt, evidence, dtype, issues=None):
                B.calls += 1
                return text
        return B

    def test_photo_only_problem_is_repaired_without_a_retry(self):
        d, e = setup_ev()
        B = self.counting(PHOTO_ONLY)
        r = run_case(d, B(), today=TODAY, exclude=["E7", "E8"], photos="model")
        self.assertEqual(B.calls, 1)
        self.assertEqual(r.attempts, 1)
        self.assertFalse(r.degraded)  # nothing was dropped, only rewritten
        self.assertIn("A photo file named scarf_packed_2026-02-26.jpg is on file [E5].", r.letter)
        self.assertNotIn("original condition", r.letter)
        self.assertIn("Each piece is dyed by hand", r.letter)
        self.assertEqual(verify_letter(r.letter, [x for x in e if x.id not in ("E7", "E8")])[0], [])

    def test_other_problems_still_get_a_retry(self):
        d, e = setup_ev()
        B = self.counting(PHOTO_ONLY.replace("2026-03-02", "2026-03-03"))  # wrong delivery date as well
        r = run_case(d, B(), today=TODAY, exclude=["E7", "E8"], photos="model")
        self.assertEqual(B.calls, 2)
        self.assertEqual(verify_letter(r.letter, [x for x in e if x.id not in ("E7", "E8")])[0], [])
        self.assertNotIn("2026-03-03", r.letter)


class Retry(unittest.TestCase):
    def test_retry_prompt_contains_previous_draft_and_asks_for_minimal_change(self):
        d, e = setup_ev()
        p = build_prompt("not_as_described", e, [Issue("bad line", "photo described beyond its file name")], ["E9"], "q", previous=ATTEMPT_2)
        self.assertIn("PREVIOUS DRAFT", p)
        self.assertIn("Change ONLY the flagged lines", p)
        self.assertIn("bad line", p)
        self.assertIn("A photo file named <file name> is on file [E#]", p)

    def test_evidence_ids_written_in_prose_are_flagged(self):
        d, e = setup_ev()
        issues, _ = verify_letter("As E9 states, each piece is dyed by hand [E9].", e)
        self.assertIn("only as [E#]", issues[0].problem)


class RepairNeverAddsClaims(unittest.TestCase):
    """Corrupt a valid letter in many ways. Whatever repair returns must verify clean."""

    def test_corruptions(self):
        d, e = setup_ev()
        good = MockBackend().generate("", [x for x in e if x.id not in ("E7", "E8")], "not_as_described")
        corruptions = [
            lambda t: t.replace("2026-03-02", "2026-03-03"),
            lambda t: t.replace("UPS", "FedEx"),
            lambda t: t.replace("1Z999AA10123456784", "1Z999BB10123456784"),
            lambda t: t + "\nThe buyer admitted she received the scarf.",
            lambda t: t.replace("delivered", "clearly delivered"),
            lambda t: t.replace("[E4]", "[E99]"),
            lambda t: t.replace("is on file.", "shows the scarf packed on 2026-02-26."),
            lambda t: t.replace("[E3]", "[E3] and the buyer was home all day"),
            lambda t: t.replace("The carrier shows", "As E4 shows, the carrier shows"),
        ]
        for i, c in enumerate(corruptions):
            bad = c(good)
            self.assertNotEqual(bad, good, f"corruption {i} did nothing")
            fixed, _ = repair_letter(bad, [x for x in e if x.id not in ("E7", "E8")])
            issues, _ = verify_letter(fixed, [x for x in e if x.id not in ("E7", "E8")])
            self.assertEqual(issues, [], f"corruption {i}: {[x.problem for x in issues]}\n{fixed}")


if __name__ == "__main__":
    unittest.main()
