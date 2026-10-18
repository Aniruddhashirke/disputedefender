"""Robustness: formatting habits of language models, quote tolerance, near-miss repair, and a seeded fuzz test."""
import json
import os
import random
import re
import tempfile
import unittest
from datetime import date

from dd.draft import MockBackend
from dd.evidence import build_evidence, classify
from dd.generate import generate_cases
from dd.harvest import harvest
from dd.models import Evidence
from dd.parse import load_case
from dd.pipeline import clean_draft, run_case, suggest_exclusions
from dd.verify import canonicalize, repair_letter, verify_letter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def ev():
    return [
        Evidence("E1", "dispute_notice", 'Case opened 2026-03-09: "The package never arrived.". Amount in dispute: $48.00. Type: item not received', {}),
        Evidence("E2", "order_record", "Order 1001234: Leather journal, $48.00, placed 2026-03-01, buyer Sam R.", {}),
        Evidence("E3", "shipping_tracking", "Shipped 2026-03-02 via USPS, tracking 9400100000000000001", {}),
        Evidence("E4", "delivery_confirmation", "Carrier shows delivered on 2026-03-05 (tracking 9400100000000000001)", {}),
        Evidence("E5", "buyer_communication", "2026-03-07 09:12 buyer: The scarf looks more orange than the photos.", {"sender": "buyer"}),
        Evidence("E6", "buyer_communication", "2026-03-07 10:40 seller: Tracking shows it was delivered on March 5. Could you check with neighbors?", {"sender": "seller"}),
    ]


def clean(text):
    return verify_letter(text, ev())[0] == []


class CitationAndFormatting(unittest.TestCase):
    def test_citation_variants_are_equivalent(self):
        for tag in ("[E4]", "(E4)", "[e4]", "[ E4 ]", "[E4]."):
            self.assertTrue(clean(f"The carrier shows delivery on 2026-03-05 {tag}"), tag)
        for tag in ("[E3, E4]", "[E3,E4]", "[E3; E4]", "[E3 and E4]", "[E3][E4]", "[E3] [E4]"):
            self.assertTrue(clean(f"It shipped on 2026-03-02 via USPS and was delivered on 2026-03-05 {tag}"), tag)

    def test_markdown_habits_are_undone(self):
        for line in ("- The carrier shows delivery on 2026-03-05 [E4]", "* The carrier shows delivery on 2026-03-05 [E4]",
                     "1. The carrier shows delivery on 2026-03-05 [E4]", "2) The carrier shows delivery on 2026-03-05 [E4]",
                     "**The carrier shows delivery on 2026-03-05** [E4]", "\u2022 The carrier shows delivery on 2026-03-05 [E4]"):
            self.assertTrue(clean(line), line)
        self.assertEqual(canonicalize("**Hello,**"), "Hello,")
        self.assertTrue(clean("**Hello,**\nSincerely,"))

    def test_canonicalising_does_not_touch_file_names_or_ids(self):
        self.assertEqual(canonicalize("A photo file named scarf_packed_2026-02-26.jpg is on file [E5]."), "A photo file named scarf_packed_2026-02-26.jpg is on file [E5].")

    def test_unknown_citation_still_flagged_after_canonicalising(self):
        self.assertFalse(clean("The carrier shows delivery on 2026-03-05 [E4, E99]"))

    def test_platform_names_are_not_treated_as_claims(self):
        self.assertTrue(clean("The buyer opened a case on Etsy on 2026-03-09 [E1]."))


class QuoteTolerance(unittest.TestCase):
    def test_full_stop_inside_the_quotation_marks(self):
        self.assertTrue(clean('The buyer wrote: "The scarf looks more orange than the photos." [E5]'))
        self.assertTrue(clean('The case says "The package never arrived." [E1]'))
        self.assertTrue(clean('The case says "The package never arrived" [E1]'))

    def test_comma_or_question_mark_at_the_end(self):
        self.assertTrue(clean('The seller asked "Could you check with neighbors" [E6]'))
        self.assertTrue(clean('The seller asked "Could you check with neighbors?" [E6]'))

    def test_ellipsis_between_parts(self):
        self.assertTrue(clean('The seller wrote "Tracking shows it was delivered on March 5... Could you check with neighbors" [E6]'))
        self.assertFalse(clean('The seller wrote "Tracking shows it was delivered on March 5... and I left it inside" [E6]'))

    def test_invented_quote_still_flagged(self):
        self.assertFalse(clean('The buyer wrote: "I received it and love it" [E5]'))

    def test_whitespace_differences_are_ignored(self):
        self.assertTrue(clean('The buyer wrote: "The scarf  looks more   orange than the photos" [E5]'))


class NearMissRepair(unittest.TestCase):
    def test_typo_in_a_quote_is_corrected_to_the_exact_text(self):
        bad = 'The buyer wrote: "The scarf looks more orang than the photos" [E5]'
        self.assertFalse(clean(bad))
        fixed, notes = repair_letter(bad, ev())
        self.assertIn('"The scarf looks more orange than the photos"', fixed)
        self.assertTrue(clean(fixed))
        self.assertTrue(any("near-match quote" in n for n in notes))
        self.assertFalse(any(n.startswith("dropped") for n in notes))

    def test_a_different_quote_is_dropped_not_matched(self):
        fixed, notes = repair_letter('The buyer wrote: "I received it and love it" [E5]', ev())
        self.assertEqual(fixed, "")
        self.assertTrue(any(n.startswith("dropped") for n in notes))

    def test_emphasis_word_is_removed_without_losing_the_fact(self):
        fixed, notes = repair_letter("The carrier clearly shows delivery on 2026-03-05 [E4].", ev())
        self.assertEqual(fixed, "The carrier shows delivery on 2026-03-05 [E4].")
        self.assertTrue(any("removed the word 'clearly'" in n for n in notes))
        fixed, notes = repair_letter("Obviously, the carrier shows delivery on 2026-03-05 [E4].", ev())
        self.assertEqual(fixed, "The carrier shows delivery on 2026-03-05 [E4].")

    def test_claims_that_cannot_be_softened_are_dropped(self):
        for word in ("proves", "admitted", "received", "signed"):
            fixed, notes = repair_letter(f"The buyer {word} the package [E4].", ev())
            self.assertEqual(fixed, "", word)
            self.assertTrue(any(n.startswith("dropped") for n in notes), word)

    def test_two_problems_in_one_clause_are_both_fixed(self):
        fixed, notes = repair_letter("Order 1001234 clearly shipped on 2026-03-02 via USPS [E3].", ev())
        self.assertIn("Order 1001234 shipped on 2026-03-02 via USPS [E3][E2].", fixed)
        self.assertTrue(clean(fixed))

    def test_repair_is_idempotent(self):
        messy = ("**Hello,**\n- The carrier clearly shows delivery on 2026-03-05 (E4).\n"
                 '2. The buyer wrote: "The scarf looks more orang than the photos" [E5, E5]\nThe buyer admitted it.\nSincerely,')
        once, _ = repair_letter(messy, ev())
        twice, notes2 = repair_letter(once, ev())
        self.assertEqual(once, twice)
        self.assertEqual(notes2, [])
        self.assertTrue(clean(once))


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.d = load_case(os.path.join(ROOT, "cases", "case_001_inr_delivered"))

    def test_messy_but_honest_model_output_passes_first_time_and_is_saved_canonical(self):
        class Messy:
            name = "messy"
            def generate(self, prompt, evidence, dtype, issues=None):
                ids = {e.kind: e.id for e in evidence}
                return (f"**Hello,**\n- The order shipped on 2026-03-02 via USPS [{ids['order_record']}, {ids['shipping_tracking']}].\n"
                        f"- The carrier shows delivery on 2026-03-05 ({ids['delivery_confirmation']}).\nSincerely,")
        r = run_case(self.d, Messy(), today=date(2026, 3, 12))
        self.assertEqual(r.attempts, 1)
        self.assertEqual(r.repairs, [])
        self.assertNotIn("**", r.letter)
        self.assertNotIn("- The", r.letter)
        self.assertNotIn(", E", r.letter)

    def test_harvested_completions_are_canonical_even_when_the_model_formats_badly(self):
        class MessyMock(MockBackend):
            def generate(self, prompt, evidence, dtype, issues=None):
                lines = super().generate(prompt, evidence, dtype, issues).splitlines()
                return "\n".join(["**Hello,**"] + ["- " + l for l in lines[1:-1]] + [lines[-1]])
        with tempfile.TemporaryDirectory() as t:
            generate_cases(os.path.join(t, "g"), 8, 2)
            out = os.path.join(t, "h.jsonl")
            stats = harvest(os.path.join(t, "g"), MessyMock(), out, today=date(2026, 12, 1))
            self.assertGreater(stats["kept"], 0)
            with open(out, encoding="utf-8") as f:
                rows = [json.loads(l) for l in f]
        for r in rows:
            self.assertTrue(r["completion"].startswith("Hello,"))
            self.assertNotIn("**", r["completion"])
            self.assertFalse([l for l in r["completion"].splitlines() if l.startswith("- ")])

    def test_clean_draft_removes_fences_and_formatting(self):
        self.assertEqual(clean_draft("```text\n**Hello,**\n- Fact [E1, E2]\n```"), "Hello,\nFact [E1][E2]")


class Fuzz(unittest.TestCase):
    """Corrupt valid letters in random ways. Whatever repair returns must verify, must not contain the injected
    fabrication, must contain no word that is in neither the draft nor the evidence (repair never adds a claim),
    and must be stable when repaired again."""

    def test_random_corruptions(self):
        rng = random.Random(20261014)
        fabricated = ["DHL", "2026-12-31", "9999999", "FEDEXTRACK123", "proves", "admitted", "left at the front door"]

        def date_change(t):
            return re.sub(r"2026-\d\d-\d\d", "2026-12-31", t, count=1)

        def carrier(t):
            return re.sub(r"\b(USPS|UPS|FedEx)\b", "DHL", t, count=1)

        def order_id(t):
            return re.sub(r"\b\d{7}\b", "9999999", t, count=1)

        def tracking(t):
            return re.sub(r"\b1Z[A-Z0-9]{6}\d{10}\b|\b94001\d{17}\b", "FEDEXTRACK123", t, count=1)

        def uncited(t):
            lines = t.splitlines()
            lines.insert(rng.randrange(1, max(2, len(lines) - 1)), "The package was left at the front door.")
            return "\n".join(lines)

        def proves(t):
            return re.sub(r"\bshows\b", "proves", t, count=1)

        def admitted(t):
            lines = t.splitlines()
            lines.insert(1, "The buyer admitted receiving it [E1].")
            return "\n".join(lines)

        def cite_swap(t):
            return re.sub(r"\[E\d+\]", "[E99]", t, count=1)

        def bullets(t):
            return "\n".join("- " + l if l.strip() and not l.startswith(("Hello", "Sincerely")) else l for l in t.splitlines())

        def brackets(t):
            return t.replace("][", ", ").replace("[E", "[E", 1)

        def bold(t):
            return "\n".join(f"**{l}**" if l.startswith("Hello") else l for l in t.splitlines())

        mutations = [date_change, carrier, order_id, tracking, uncited, proves, admitted, cite_swap, bullets, brackets, bold]
        checked = 0
        with tempfile.TemporaryDirectory() as t:
            names = generate_cases(os.path.join(t, "g"), 40, 11)
            for name in names:
                d = load_case(os.path.join(t, "g", name))
                full = build_evidence(d, classify(d))
                excl = suggest_exclusions(full)
                r = run_case(d, MockBackend(), today=date(2026, 12, 1), exclude=excl)
                if r.triage.verdict != "fight":
                    continue
                model_ev = [e for e in full if e.id not in excl and e.kind not in ("shipping_proof_photo", "item_photos")]
                good = MockBackend().generate("", model_ev, r.dispute_type)
                for _ in range(4):
                    bad = good
                    for m in rng.sample(mutations, rng.randrange(1, 4)):
                        bad = m(bad)
                    fixed, notes = repair_letter(bad, model_ev)
                    issues, _ = verify_letter(fixed, model_ev)
                    self.assertEqual(issues, [], f"{name}\n--- corrupted\n{bad}\n--- repaired\n{fixed}\n{[i.problem for i in issues]}")
                    for token in fabricated:
                        if token in bad and token not in good:
                            self.assertNotIn(token, fixed, f"{name}: injected {token!r} survived\n{fixed}")
                    words = lambda x: set(re.findall(r"[a-z0-9]+", x.lower()))
                    allowed = words(canonicalize(bad)) | words(" ".join(e.text for e in model_ev)) | {"a", "photo", "file", "named", "is", "on"} | {f"e{i}" for i in range(1, 30)}
                    self.assertEqual(words(fixed) - allowed, set(), f"{name}: repair introduced words not in the draft or the evidence")
                    again, notes2 = repair_letter(fixed, model_ev)
                    self.assertEqual(again, fixed, f"{name}: repair is not stable")
                    checked += 1
        self.assertGreater(checked, 100)


if __name__ == "__main__":
    unittest.main()
