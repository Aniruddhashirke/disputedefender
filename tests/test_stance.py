"""Found in a real Gemma run with the short prompt: the model wrote a customer-service reply to the BUYER, apologising and
'confirming' the buyer's claim, which no seller wants in a dispute response. Apologies and admissions must come from the
evidence; they are never the model's own."""
import unittest

from dd.models import Evidence
from dd.verify import repair_letter, verify_letter


def ev():
    return [
        Evidence("E1", "dispute_notice", 'Case opened 2026-01-20: "The item arrived damaged.". Amount in dispute: $22.00. Type: item arrived damaged', {}),
        Evidence("E2", "order_record", "Order 1001234: Linen kitchen towel set, $22.00, placed 2026-01-13, buyer Sam R.", {}),
        Evidence("E3", "shipping_tracking", "Shipped 2026-01-14 via USPS, tracking 9400100000000000001", {}),
        Evidence("E4", "delivery_confirmation", "Carrier shows delivered on 2026-01-18 (tracking 9400100000000000001)", {}),
        Evidence("E5", "buyer_communication", "2026-01-19 09:00 buyer: The box was crushed and the item inside is broken.", {"sender": "buyer"}),
        Evidence("E6", "buyer_communication", "2026-01-20 10:30 seller: I am sorry to hear that. I packed it in a double-walled box.", {"sender": "seller"}),
    ]


class Stance(unittest.TestCase):
    def flagged(self, line):
        issues, _ = verify_letter(line, ev())
        return [i.problem for i in issues]

    def test_apologies_the_model_adds_on_its_own_are_flagged(self):
        for line in ("We sincerely apologize for the inconvenience [E5].", "We are sorry the box was crushed [E5].",
                     "We regret that the item arrived damaged [E1].", "This was our fault [E1].", "It was our mistake [E1]."):
            self.assertTrue(any("strong claim word" in p for p in self.flagged(line)), line)

    def test_they_are_dropped_by_repair_not_softened(self):
        fixed, notes = repair_letter("We sincerely apologize for the inconvenience [E5].", ev())
        self.assertEqual(fixed, "")
        self.assertTrue(any(n.startswith("dropped") for n in notes))

    def test_a_quoted_apology_that_really_is_in_the_evidence_is_allowed(self):
        self.assertEqual(self.flagged('The seller wrote: "I am sorry to hear that." [E6]'), [])
        self.assertEqual(self.flagged("The seller said she was sorry to hear that [E6]."), [])  # the evidence uses the word itself

    def test_indeed_is_removed_like_other_emphasis(self):
        fixed, notes = repair_letter("The package was indeed delivered on 2026-01-18 [E4].", ev())
        self.assertEqual(fixed, "The package was delivered on 2026-01-18 [E4].")
        self.assertTrue(any("removed the word 'indeed'" in n for n in notes))

    def test_a_capitalised_word_after_a_dash_is_not_a_name(self):
        line = "We shipped your order [E2] \u2013 Order 1001234 \u2013 on 2026-01-14 via USPS [E3]."
        problems = self.flagged(line)
        self.assertFalse(any("name or proper noun" in p for p in problems), problems)
        fixed, notes = repair_letter(line, ev())  # the order number is in E2, so the citation is completed, nothing is lost
        self.assertEqual(verify_letter(fixed, ev())[0], [])
        self.assertFalse(any(n.startswith("dropped") for n in notes), notes)
        self.assertIn("[E3][E2]", fixed)

    def test_real_names_are_still_checked_after_a_dash(self):
        self.assertTrue(any("name or proper noun" in p for p in self.flagged("We shipped it \u2013 Marcus collected it \u2013 on 2026-01-14 [E3].")))


if __name__ == "__main__":
    unittest.main()
