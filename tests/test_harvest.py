import json
import os
import tempfile
import unittest
from datetime import date

from dd.draft import MockBackend
from dd.generate import generate_cases
from dd.harvest import harvest

TODAY = date(2026, 3, 25)


def lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


class Harvest(unittest.TestCase):
    def test_keeps_first_draft_clean_letters_with_the_exact_prompt(self):
        with tempfile.TemporaryDirectory() as t:
            generate_cases(os.path.join(t, "g"), 6, 3)
            out = os.path.join(t, "o", "h.jsonl")
            stats = harvest(os.path.join(t, "g"), MockBackend(), out, today=TODAY)
            rows = lines(out)
        self.assertEqual(stats["kept"] + stats["skipped_fold"], 6)  # folded cases are not training material
        self.assertGreater(stats["kept"], 0)
        self.assertEqual(len(rows), stats["kept"])
        r = rows[0]
        self.assertTrue(r["prompt"].startswith("You write a short, polite response letter"))
        self.assertIn("EVIDENCE (the only facts you may use):", r["prompt"])
        self.assertTrue(r["completion"].startswith("Hello,"))
        self.assertNotIn("PREVIOUS DRAFT", r["prompt"])  # first-draft prompts only
        self.assertNotIn("Photo on file", r["prompt"])   # photos are written by code, so the model never sees them

    def test_resumes_without_duplicates(self):
        with tempfile.TemporaryDirectory() as t:
            generate_cases(os.path.join(t, "g"), 12, 3)
            out = os.path.join(t, "h.jsonl")
            harvest(os.path.join(t, "g"), MockBackend(), out, today=TODAY, limit=2)
            self.assertEqual(len(lines(out)), 2)
            stats = harvest(os.path.join(t, "g"), MockBackend(), out, today=TODAY)
            self.assertEqual(stats["already_done"], 2)
            self.assertEqual(len(lines(out)), 2 + stats["kept"])
            self.assertEqual(len({r["case"] for r in lines(out)}), len(lines(out)))  # no duplicates

    def test_skips_letters_that_needed_a_retry_or_repair(self):
        class FirstDraftBad(MockBackend):
            calls = 0
            def generate(self, prompt, evidence, dtype, issues=None):
                FirstDraftBad.calls += 1
                text = super().generate(prompt, evidence, dtype, issues)
                return text.replace("Hello,", "Hello,\nThe buyer clearly received it.") if FirstDraftBad.calls % 2 == 1 else text
        with tempfile.TemporaryDirectory() as t:
            generate_cases(os.path.join(t, "g"), 8, 3)
            out = os.path.join(t, "h.jsonl")
            stats = harvest(os.path.join(t, "g"), FirstDraftBad(), out, today=TODAY)
            rows = lines(out) if os.path.exists(out) else []
        self.assertEqual(stats["skipped_not_clean_first_draft"] + stats["kept"] + stats["skipped_fold"], 8)
        self.assertGreater(stats["skipped_not_clean_first_draft"], 0)
        for r in rows:
            self.assertNotIn("clearly received", r["completion"])


if __name__ == "__main__":
    unittest.main()
