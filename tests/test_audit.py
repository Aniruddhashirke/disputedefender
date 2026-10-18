"""Audit tests: holes found by reviewing the code, plus real Gemma letters from earlier runs used as regression fixtures."""
import os
import tempfile
import unittest
from datetime import date

from dd.draft import MockBackend, build_prompt
from dd.evidence import build_evidence, classify
from dd.parse import load_case, read_text
from dd.pipeline import key_evidence_ids, run_case
from dd.adapters import get_adapter
from dd.verify import verify_letter

ROOT = os.path.join(os.path.dirname(__file__), "..", "cases")
TODAY = date(2026, 3, 12)


def ev(name):
    d = load_case(os.path.join(ROOT, name))
    return d, build_evidence(d, classify(d))


# Real letter written by local Gemma (gemma3 via Ollama) for case_005, quoted from a screenshot. Curly quotes included.
GEMMA_REAL_LETTER = """Hello,
The buyer opened a case on 2026-03-09: \u201cBuyer says the color is different from the listing photos\u201d. [E1]
Order 1001555 was a hand-dyed wool scarf, $85.00, placed 2026-02-25 [E2].
The order was shipped 2026-02-26 via UPS, tracking 1Z999AA10123456784 [E3].
Delivery confirmation shows the carrier delivered on 2026-03-02, tracking 1Z999AA10123456784 [E4].
A photo on file: scarf_packed_2026-02-26.jpg [E5].
The buyer contacted the seller on 2026-03-04 12:30: \u201cThe scarf looks more orange than the photos\u201d. [E6].
The seller responded on 2026-03-04 14:05: \u201cHand-dyed items can vary slightly. I can offer a partial refund if you would like\u201d. [E7].
The buyer contacted the seller on 2026-03-08 08:45: \u201cNo, I want a full refund\u201d. [E8].
The listing text states: \u201cHand-dyed wool scarf. Each piece is dyed by hand, so color may vary slightly from the photos\u201d. [E9].
I ask that this case be resolved in the seller's favor based on the evidence above.
Thank you for reviewing this case.
Sincerely,"""

# Earlier real Gemma letter that cited a name to the wrong evidence and invented what a photo shows.
GEMMA_REAL_BAD = """Hello,
The buyer, Priya N., opened a case on 2026-03-09 [E1].
A photo of the scarf packed on 2026-02-26 is available [E5].
Sincerely,"""


class RealGemmaRegression(unittest.TestCase):
    def test_real_letter_passes(self):
        _, e = ev("case_005_nad_with_listing")
        issues, _ = verify_letter(GEMMA_REAL_LETTER, e)
        self.assertEqual(issues, [], [i.problem for i in issues])

    def test_real_bad_letter_is_flagged(self):
        _, e = ev("case_005_nad_with_listing")
        issues, _ = verify_letter(GEMMA_REAL_BAD, e)
        self.assertEqual(len(issues), 2)
        self.assertIn("Priya", issues[0].problem)   # name cited to the wrong evidence
        self.assertIn("photo", issues[1].problem)   # invented what the photo shows (caught since the photo rule was added)


# Real Gemma output (prompt v3, case_005 with E7 and E8 excluded), quoted from a screenshot. Good structure, but the
# last clause claims what the photo shows and when it was taken, which the evidence does not say.
GEMMA_V3_LETTER = """Hello,
The buyer says the color is different from the listing photos [E1]. Each piece is dyed by hand, so color may vary slightly from the photos [E9], and a photo on file shows the scarf packed on 2026-02-26 [E5].
Order 1001555, a hand-dyed wool scarf, was placed on 2026-02-25 and shipped on 2026-02-26 via UPS, tracking 1Z999AA10123456784 [E2][E3]. The carrier shows delivered on 2026-03-02 [E4].
On 2026-03-04 at 12:30, the buyer stated the scarf looks more orange than the photos [E6].
I ask that this case be resolved in the seller's favor based on the evidence above.
Thank you for reviewing this case.
Sincerely,"""


class ClauseLevel(unittest.TestCase):
    def setUp(self):
        self.d, self.e = ev("case_005_nad_with_listing")

    def test_real_v3_letter_only_the_photo_clause_is_flagged(self):
        issues, ok = verify_letter(GEMMA_V3_LETTER, self.e)
        self.assertEqual(len(issues), 1)
        self.assertIn("photo", issues[0].problem)
        self.assertIn("shows the scarf packed", issues[0].line)
        self.assertEqual(len(ok), 6)  # the other lines pass, including the [E2][E3] combined sentence

    def test_photo_referred_to_by_file_name_passes(self):
        issues, _ = verify_letter("A photo file named scarf_packed_2026-02-26.jpg is on file [E5].", self.e)
        self.assertEqual(issues, [])

    def test_photo_without_file_name_flagged(self):
        issues, _ = verify_letter("A photo from 2026-02-26 is on file [E5].", self.e)
        self.assertIn("file name", issues[0].problem)

    def test_citation_cannot_vouch_for_a_claim_next_to_a_different_citation(self):
        # The delivery date is in E4 only. Citing E1 next to it must not make it pass just because E4 appears later.
        issues, _ = verify_letter("The package was delivered on 2026-03-02 [E1] and the case opened on 2026-03-09 [E4].", self.e)
        self.assertEqual(len(issues), 1)
        self.assertIn("2026-03-02", issues[0].problem)

    def test_text_after_last_citation_is_flagged(self):
        issues, _ = verify_letter("The carrier shows delivery on 2026-03-02 [E4]. The buyer was home all day.", self.e)
        self.assertIn("after the last citation", issues[0].problem)

    def test_combined_citations_still_allowed(self):
        issues, _ = verify_letter("Order 1001555 shipped on 2026-02-26 via UPS [E2][E3].", self.e)
        self.assertEqual(issues, [])


class Diagnostics(unittest.TestCase):
    def test_history_records_each_attempt_and_is_rendered(self):
        from dd.pipeline import render_packet

        class Bad:
            name = "bad"
            def generate(self, prompt, evidence, dtype, issues=None):
                return "Hello,\nThe buyer clearly received it [E4].\nSincerely,"
        r = run_case(load_case(os.path.join(ROOT, "case_005_nad_with_listing")), Bad(), today=TODAY)
        self.assertEqual(len(r.history), r.attempts)
        self.assertIn("clearly", r.history[0]["issues"][0]["problem"])
        text = render_packet(r, show_drafts=True)
        self.assertIn("VERIFIER FLAGGED THESE LINES", text)
        self.assertIn("RAW DRAFT, ATTEMPT 1", text)
        self.assertIn("strong claim word", text)


class VerifierHoles(unittest.TestCase):
    def setUp(self):
        self.d, self.e = ev("case_005_nad_with_listing")

    def bad(self, line, fragment):
        issues, _ = verify_letter(line, self.e)
        self.assertEqual(len(issues), 1, line)
        self.assertIn(fragment, issues[0].problem)

    def test_boilerplate_prefix_cannot_smuggle_claims(self):
        self.bad("Thank you. The package was left at the buyer's door.", "no evidence citation")
        self.bad("Hello, the package was delivered to the front desk", "no evidence citation")
        self.bad("I ask that this case be resolved because the buyer is lying", "no evidence citation")

    def test_plain_boilerplate_still_allowed(self):
        issues, _ = verify_letter("Hello,\nThank you for reviewing this case.\nSincerely,", self.e)
        self.assertEqual(issues, [])

    def test_partly_changed_tracking_id_flagged(self):
        self.bad("The order shipped via UPS with tracking 1Z999BB10123456784 [E3].", "id not in cited evidence")

    def test_wrong_day_with_month_name_flagged(self):
        self.bad("The carrier shows delivery on March 4 [E4].", "date not in cited evidence")

    def test_right_day_with_month_name_passes(self):
        issues, _ = verify_letter("The carrier shows delivery on March 2 [E4].", self.e)
        self.assertEqual(issues, [])

    def test_single_digit_cannot_hide_inside_a_date(self):
        self.bad("The package was delivered on the 4 of the month [E4].", "number or date not in cited evidence: 4")

    def test_overclaim_words_flagged(self):
        self.bad("The buyer clearly received the scarf [E4].", "clearly")
        self.bad("The carrier proves delivery on 2026-03-02 [E4].", "proves")

    def test_overclaim_word_allowed_when_evidence_uses_it(self):
        from dd.models import Evidence
        e = [Evidence("E1", "buyer_communication", "2026-03-07 09:12 buyer: I never received it", {})]
        issues, _ = verify_letter("The buyer wrote on 2026-03-07 that they never received it [E1].", e)
        self.assertEqual(issues, [])


class Files(unittest.TestCase):
    def test_reads_utf16_and_cp1252_files_from_windows(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = os.path.join(t, "a.txt"), os.path.join(t, "b.txt")
            with open(a, "wb") as f:
                f.write("Listing: caf\u00e9 scarf".encode("utf-16"))
            with open(b, "wb") as f:
                f.write("Listing: caf\u00e9 scarf".encode("cp1252"))
            self.assertEqual(read_text(a), "Listing: caf\u00e9 scarf")
            self.assertEqual(read_text(b), "Listing: caf\u00e9 scarf")

    def test_more_not_received_phrasings(self):
        d = load_case(os.path.join(ROOT, "case_002_inr_no_evidence"))
        for phrase in ("Package never came", "Item was not delivered", "Lost in transit"):
            d.notice.reason, d.notice.type_raw = phrase, ""
            self.assertEqual(classify(d), "not_received", phrase)


class PromptDesign(unittest.TestCase):
    def test_key_evidence_leads_and_comes_from_triage_weights(self):
        d, e = ev("case_005_nad_with_listing")
        w = get_adapter("etsy").weights("not_as_described")
        ids = key_evidence_ids(e, w)
        self.assertEqual(ids[0], "E9")  # listing text is the most valuable evidence for this dispute type
        prompt = build_prompt("not_as_described", e, None, ids, "Did the item match what the listing described?")
        self.assertIn("KEY EVIDENCE", prompt)
        self.assertIn("Central question", prompt)
        self.assertIn("EXAMPLE OF THE STYLE", prompt)

    def test_case_005_has_no_listing_gap_but_004_does(self):
        r5 = run_case(load_case(os.path.join(ROOT, "case_005_nad_with_listing")), MockBackend(), today=TODAY)
        r4 = run_case(load_case(os.path.join(ROOT, "case_004_not_as_described")), MockBackend(), today=TODAY)
        self.assertNotIn("listing_description", {g.slot for g in r5.gaps})
        self.assertIn("listing_description", {g.slot for g in r4.gaps})
        self.assertGreater(r5.triage.win_likelihood, r4.triage.win_likelihood)
        self.assertEqual(r5.issues, [])


if __name__ == "__main__":
    unittest.main()
