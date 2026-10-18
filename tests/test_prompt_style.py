"""A short prompt for a fine-tuned model (the fine-tune learns the rules, so the prompt can shrink: lower latency and cost)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date

from dd.draft import MockBackend, build_prompt
from dd.evalrun import evaluate
from dd.evidence import build_evidence, classify
from dd.generate import generate_cases
from dd.harvest import harvest
from dd.models import Issue
from dd.parse import load_case
from dd.pipeline import run_case

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CASES = os.path.join(ROOT, "cases")
TODAY = date(2026, 3, 12)


class Recorder(MockBackend):
    def __init__(self):
        self.prompts = []

    def generate(self, prompt, evidence, dtype, issues=None):
        self.prompts.append(prompt)
        return super().generate(prompt, evidence, dtype, issues)


class ShortPrompt(unittest.TestCase):
    def setUp(self):
        self.d = load_case(os.path.join(CASES, "case_005_nad_with_listing"))
        self.e = build_evidence(self.d, classify(self.d))

    def test_short_is_much_shorter_and_keeps_the_facts(self):
        full = build_prompt("not_as_described", self.e, None, ["E9"], "Did the item match what the listing described?")
        short = build_prompt("not_as_described", self.e, None, ["E9"], "Did the item match what the listing described?", style="short")
        self.assertLess(len(short), len(full) * 0.55)
        for needle in ("[E1]", "[E9]", "Key evidence: E9", "Question:", "Hello,"):
            self.assertIn(needle, short)
        self.assertNotIn("RULES:", short)
        self.assertNotIn("EXAMPLE OF THE STYLE", short)

    def test_short_retry_carries_problems_and_previous_draft(self):
        p = build_prompt("x", self.e, [Issue("bad line", "quoted text not found")], None, "", "old draft", style="short")
        self.assertIn("bad line", p)
        self.assertIn("old draft", p)

    def test_pipeline_uses_the_requested_style(self):
        a, b = Recorder(), Recorder()
        run_case(self.d, a, today=TODAY, exclude=["E7", "E8"])
        run_case(self.d, b, today=TODAY, exclude=["E7", "E8"], prompt_style="short")
        self.assertIn("RULES:", a.prompts[0])
        self.assertNotIn("RULES:", b.prompts[0])

    def test_bad_style_is_an_error(self):
        with self.assertRaises(ValueError):
            run_case(self.d, MockBackend(), today=TODAY, prompt_style="tiny")

    def test_history_always_records_the_short_prompt_too(self):
        r = run_case(self.d, MockBackend(), today=TODAY, exclude=["E7", "E8"])
        self.assertIn("Evidence:", r.history[0]["prompt_short"])
        self.assertIn("RULES:", r.history[0]["prompt"])


class Plumbing(unittest.TestCase):
    def test_harvest_writes_both_prompts(self):
        with tempfile.TemporaryDirectory() as t:
            generate_cases(os.path.join(t, "g"), 6, 4)
            out = os.path.join(t, "h.jsonl")
            harvest(os.path.join(t, "g"), MockBackend(), out, today=date(2026, 12, 1))
            with open(out, encoding="utf-8") as f:
                row = json.loads(f.readline())
        self.assertIn("RULES:", row["prompt"])
        self.assertNotIn("RULES:", row["prompt_short"])
        self.assertTrue(row["completion"].startswith("Hello,"))

    def test_eval_rows_keep_the_flagged_lines_so_failures_can_be_read_later(self):
        class Liar(MockBackend):
            def generate(self, prompt, evidence, dtype, issues=None):
                return super().generate(prompt, evidence, dtype, issues).replace("Hello,", 'Hello,\nThe buyer clearly received it [E4].')
        res = evaluate(CASES, Liar(), repeat=1, today=TODAY)
        bad = [r for r in res["rows"] if r["first_draft_issues"]]
        self.assertTrue(bad)
        self.assertIn("clearly", bad[0]["first_draft_issues"][0]["problem"])
        self.assertIn("received it", bad[0]["first_draft_issues"][0]["line"])
        self.assertEqual(res["prompt_style"], "full")

    def test_cli_accepts_the_flag(self):
        r = subprocess.run([sys.executable, "-m", "dd", "run", "cases/case_001_inr_delivered", "--prompt-style", "short", "--today", "2026-03-12"],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("VERDICT: FIGHT", r.stdout)


if __name__ == "__main__":
    unittest.main()
