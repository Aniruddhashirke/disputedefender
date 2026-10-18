import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date

from dd.cli import main
from dd.draft import MockBackend
from dd.evalrun import categorize, evaluate, render_eval

CASES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cases"))
TODAY = date(2026, 3, 12)


class PhotoLiar(MockBackend):
    """Behaves like the real Gemma runs: a clean letter plus an invented photo detail."""
    name = "photo-liar"

    def generate(self, prompt, evidence, dtype, issues=None):
        text = super().generate(prompt, evidence, dtype)
        return text.replace("is on file.", "shows the scarf packed as described.")


class Eval(unittest.TestCase):
    def test_clean_model_scores_100_percent(self):
        res = evaluate(CASES, MockBackend(), repeat=2, today=TODAY)
        s = res["summary"]
        self.assertEqual(s["first_draft_clean_rate"], 1.0)
        self.assertEqual(s["repaired_rate"], 0.0)
        self.assertEqual(s["avg_attempts"], 1.0)
        self.assertEqual(s["first_draft_problem_runs"], {})
        self.assertIn("case_003_low_value", s["cases_skipped_as_fold"])  # folded cases are not scored
        self.assertEqual(s["runs"], 6)  # three fight cases x 2 runs

    def test_photo_liar_is_caught_and_counted(self):
        res = evaluate(CASES, PhotoLiar(), repeat=1, today=TODAY, photos="model")
        s = res["summary"]
        self.assertGreater(s["repaired_rate"], 0)
        self.assertLess(s["first_draft_clean_rate"], 1.0)
        self.assertIn("photo claim", s["first_draft_problem_runs"])
        self.assertEqual(s["avg_attempts"], 1.0)  # photo-only problems are repaired without a retry

    def test_categories(self):
        self.assertEqual(categorize("photo described beyond its file name (x)"), "photo claim")
        self.assertEqual(categorize("photo evidence must be referred to by its file name"), "photo claim")
        self.assertEqual(categorize("strong claim word not in cited evidence: clearly"), "overclaim word")
        self.assertEqual(categorize("number or date not in cited evidence: 4"), "number or date")
        self.assertEqual(categorize("date not in cited evidence: March 4"), "date")
        self.assertEqual(categorize("something new"), "other")

    def test_render_and_cli_save_json(self):
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "r.json")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["eval", CASES, "--backend", "mock", "--repeat", "1", "--today", "2026-03-12", "--out", out])
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(data["summary"]["first_draft_clean_rate"], 1.0)
        self.assertIn("first draft clean", render_eval(data))


if __name__ == "__main__":
    unittest.main()
