"""Found on a real Gemma run (generated case 016): a true fact cited to the wrong evidence item. The repair now fixes
the citation instead of dropping the line. Also checks the generator's variety."""
import os
import tempfile
import unittest
from collections import Counter
from datetime import date

from dd.draft import MockBackend
from dd.evidence import build_evidence, classify
from dd.generate import generate_cases
from dd.models import Evidence
from dd.parse import load_case
from dd.pipeline import run_case, suggest_exclusions
from dd.verify import repair_letter, verify_letter

TODAY = date(2026, 3, 12)


def ev():
    return [
        Evidence("E1", "dispute_notice", 'Case opened 2026-02-28: "item not received". Amount in dispute: $54.00. Type: not_received', {}),
        Evidence("E2", "order_record", "Order 2000016: Wooden desk organizer, $54.00, placed 2026-02-18, buyer Dev R.", {}),
        Evidence("E3", "shipping_tracking", "Shipped 2026-02-20 via USPS, tracking 9400100000000000016", {}),
        Evidence("E4", "delivery_confirmation", "Carrier shows delivered on 2026-02-22 (tracking 9400100000000000016)", {}),
    ]


# From the real run: the order number belongs to E2, but the model cited E3 next to it.
DRAFT = """Hello,
The buyer says the item not received [E1].
The carrier shows delivered on 2026-02-22 [E4].
Order 2000016 was shipped 2026-02-20 via USPS [E3].
The wooden desk organizer was purchased on 2026-02-18 [E2].
Sincerely,"""


class CitationCompletion(unittest.TestCase):
    def test_verifier_still_flags_the_imprecise_citation(self):
        issues, _ = verify_letter(DRAFT, ev())
        self.assertEqual(len(issues), 1)
        self.assertIn("2000016", issues[0].problem)

    def test_repair_adds_the_missing_citation_instead_of_dropping_the_line(self):
        fixed, notes = repair_letter(DRAFT, ev())
        self.assertIn("Order 2000016 was shipped 2026-02-20 via USPS [E3][E2].", fixed)
        self.assertEqual(verify_letter(fixed, ev())[0], [])
        self.assertTrue(any(n.startswith("completed a citation") for n in notes))
        self.assertFalse(any(n.startswith("dropped") for n in notes))

    def test_pipeline_does_not_retry_when_repair_loses_nothing(self):
        class B:
            name = "b"
            calls = 0
            def generate(self, prompt, evidence, dtype, issues=None):
                B.calls += 1
                return DRAFT
        d = load_case_stub()
        r = run_case(d, B(), today=TODAY)
        self.assertEqual(B.calls, 1)
        self.assertFalse(r.degraded)
        self.assertIn("[E3][E2]", r.letter)

    def test_unsupported_fact_is_still_dropped_not_cited_to_something_else(self):
        bad = DRAFT.replace("Order 2000016 was shipped", "Order 5550000 was shipped")
        fixed, notes = repair_letter(bad, ev())
        self.assertNotIn("5550000", fixed)
        self.assertTrue(any(n.startswith("dropped") for n in notes))

    def test_ambiguous_fact_is_dropped_not_guessed(self):
        e = ev() + [Evidence("E5", "buyer_communication", "2026-02-25 buyer: Order 2000016 is late", {"sender": "buyer"})]
        fixed, notes = repair_letter(DRAFT, e)  # 2000016 is now in both E2 and E5
        self.assertNotIn("USPS [E3]", fixed)
        self.assertTrue(any(n.startswith("dropped") for n in notes))


def load_case_stub():
    import tempfile
    t = tempfile.mkdtemp()
    with open(os.path.join(t, "notice.txt"), "w", encoding="utf-8") as f:
        f.write('platform: etsy\ntype: not_received\nreason: item not received\namount: 54.00\nopened: 2026-02-28\n')
    with open(os.path.join(t, "order.csv"), "w", encoding="utf-8") as f:
        f.write("order_id,buyer_name,item,amount,order_date,ship_date,carrier,tracking_number,delivery_status,delivery_date\n"
                "2000016,Dev R.,Wooden desk organizer,54.00,2026-02-18,2026-02-20,USPS,9400100000000000016,delivered,2026-02-22\n")
    with open(os.path.join(t, "messages.txt"), "w", encoding="utf-8") as f:
        f.write("")
    return load_case(t)


class GeneratorVariety(unittest.TestCase):
    def test_variety_and_realism(self):
        with tempfile.TemporaryDirectory() as t:
            names = generate_cases(os.path.join(t, "g"), 100, 5)
            verdicts, gaps, texts = Counter(), Counter(), []
            for n in names:
                d = load_case(os.path.join(t, "g", n))
                texts += [m.text for m in d.messages] + [d.notice.reason]
                r = run_case(d, MockBackend(), today=date(2026, 12, 1), exclude=suggest_exclusions(build_evidence(d, classify(d))))
                verdicts[r.triage.verdict] += 1
                gaps.update(g.slot for g in r.gaps)
                if r.triage.verdict == "fight":
                    self.assertEqual(r.issues, [], n)
        self.assertGreaterEqual(verdicts["fold"], 8)     # some cases are deliberate folds
        self.assertGreaterEqual(verdicts["fight"], 60)
        for slot in ("listing_description", "shipping_tracking", "delivery_confirmation", "buyer_communication"):
            self.assertGreater(gaps[slot], 0, slot)       # different cases miss different evidence
        self.assertGreater(len(set(texts)), 20)           # not one template repeated
        self.assertFalse([x for x in texts if "because the item" in x.lower()])  # the old ungrammatical template is gone
        self.assertTrue(any("refund" in x.lower() for x in texts))

    def test_tracking_numbers_are_realistic_and_mostly_unique(self):
        with tempfile.TemporaryDirectory() as t:
            names = generate_cases(os.path.join(t, "g"), 60, 9)
            tracks = [load_case(os.path.join(t, "g", n)).order.tracking_number for n in names]
        tracks = [x for x in tracks if x]
        self.assertEqual(len(tracks), len(set(tracks)))
        self.assertTrue(all(len(x) >= 12 for x in tracks))


if __name__ == "__main__":
    unittest.main()
