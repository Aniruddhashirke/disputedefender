"""Photos written by code, the eval that no longer looks hung, and the case generator."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date

from dd.cli import main
from dd.draft import BackendError, MockBackend, OllamaBackend, build_prompt, get_backend
from dd.evalrun import evaluate
from dd.evidence import build_evidence, classify
from dd.parse import load_case
from dd.pipeline import run_case
from dd.verify import verify_letter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CASES = os.path.join(ROOT, "cases")
TODAY = date(2026, 3, 12)


def case(name):
    return load_case(os.path.join(CASES, name))


class Recorder(MockBackend):
    def __init__(self):
        self.prompts = []

    def generate(self, prompt, evidence, dtype, issues=None):
        self.prompts.append(prompt)
        return super().generate(prompt, evidence, dtype, issues)


class PhotosByCode(unittest.TestCase):
    def test_model_never_sees_photo_evidence_and_code_writes_the_sentence(self):
        b = Recorder()
        d = case("case_005_nad_with_listing")
        r = run_case(d, b, today=TODAY, exclude=["E7", "E8"])
        self.assertEqual(r.attempts, 1)
        self.assertEqual(r.issues, [])
        self.assertNotIn("Photo on file", b.prompts[0])
        self.assertNotIn("PHOTOS ARE NOT VISUAL EVIDENCE", b.prompts[0])  # no photo rules when there is nothing about photos to say
        self.assertIn("A photo file named scarf_packed_2026-02-26.jpg is on file [E5].", r.letter)
        lines = r.letter.splitlines()
        self.assertLess(lines.index("A photo file named scarf_packed_2026-02-26.jpg is on file [E5]."),
                        next(i for i, l in enumerate(lines) if l.startswith("I ask that this case")))
        ev = build_evidence(d, classify(d))
        self.assertEqual(verify_letter(r.letter, [e for e in ev if e.id not in ("E7", "E8")], require_facts=True)[0], [])

    def test_a_model_that_cites_a_photo_anyway_is_flagged_and_dropped(self):
        class Sneaky:
            name = "sneaky"
            def generate(self, prompt, evidence, dtype, issues=None):
                return ("Hello,\nThe buyer says the color is different from the listing photos [E1].\n"
                        "The photo shows the scarf packed as described [E5].\nSincerely,")
        d = case("case_005_nad_with_listing")
        r = run_case(d, Sneaky(), today=TODAY, exclude=["E7", "E8"])
        self.assertTrue(r.degraded)
        self.assertNotIn("as described", r.letter)
        self.assertIn("A photo file named scarf_packed_2026-02-26.jpg is on file [E5].", r.letter)

    def test_model_mode_keeps_the_old_behaviour(self):
        b = Recorder()
        run_case(case("case_005_nad_with_listing"), b, today=TODAY, exclude=["E7", "E8"], photos="model")
        self.assertIn("Photo on file", b.prompts[0])
        self.assertIn("PHOTOS ARE NOT VISUAL EVIDENCE", b.prompts[0])

    def test_bad_mode_is_an_error(self):
        with self.assertRaises(ValueError):
            run_case(case("case_001_inr_delivered"), MockBackend(), today=TODAY, photos="both")

    def test_prompt_photo_rules_only_when_photo_evidence_is_present(self):
        d = case("case_005_nad_with_listing")
        ev = build_evidence(d, classify(d))
        self.assertIn("PHOTOS ARE NOT VISUAL EVIDENCE", build_prompt("not_as_described", ev))
        no_photo = [e for e in ev if e.kind != "item_photos"]
        self.assertNotIn("PHOTOS ARE NOT VISUAL EVIDENCE", build_prompt("not_as_described", no_photo))


class EvalRuntime(unittest.TestCase):
    def test_progress_and_incremental_updates_every_run(self):
        lines, snaps = [], []
        res = evaluate(CASES, MockBackend(), repeat=2, today=TODAY, progress=lines.append, on_update=snaps.append)
        self.assertEqual(res["summary"]["runs"], 6)
        self.assertEqual(len(snaps), 6)                      # a partial result is available after every run
        self.assertEqual(snaps[2]["summary"]["runs"], 3)
        self.assertTrue(any("left" in l and "elapsed" in l for l in lines))
        self.assertTrue(any("verdict is fold" in l for l in lines))

    def test_stops_cleanly_when_the_model_server_keeps_failing(self):
        class Down:
            name = "down"
            def generate(self, *a, **k):
                raise BackendError("Ollama call failed: connection refused")
        lines = []
        res = evaluate(CASES, Down(), repeat=2, today=TODAY, progress=lines.append)
        self.assertTrue(res["summary"]["interrupted"])
        self.assertEqual(res["summary"]["errors"], 3)
        self.assertTrue(any("Is Ollama running" in l for l in lines))

    def test_ctrl_c_returns_partial_results(self):
        class CtrlC(MockBackend):
            calls = 0
            def generate(self, *a, **k):
                CtrlC.calls += 1
                if CtrlC.calls == 3:
                    raise KeyboardInterrupt
                return super().generate(*a, **k)
        res = evaluate(CASES, CtrlC(), repeat=2, today=TODAY)
        self.assertTrue(res["summary"]["interrupted"])
        self.assertEqual(res["summary"]["runs"], 2)

    def test_cli_eval_saves_json_after_the_run_and_prints_progress_to_stderr(self):
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "r.json")
            r = subprocess.run([sys.executable, "-m", "dd", "eval", "cases", "--repeat", "1", "--today", "2026-03-12", "--out", out],
                               cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("left", r.stderr)
            self.assertIn("first draft clean", r.stdout)
            with open(out, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["summary"]["runs"], 3)


class Timeouts(unittest.TestCase):
    def test_default_and_override(self):
        old = os.environ.pop("DD_TIMEOUT", None)
        try:
            self.assertEqual(OllamaBackend(url="http://localhost:1").timeout, 300.0)
            self.assertEqual(get_backend("ollama", timeout=45).timeout, 45.0)
            os.environ["DD_TIMEOUT"] = "90"
            self.assertEqual(OllamaBackend(url="http://localhost:1").timeout, 90.0)
        finally:
            os.environ.pop("DD_TIMEOUT", None)
            if old:
                os.environ["DD_TIMEOUT"] = old

    def test_num_predict_setting(self):
        self.assertEqual(OllamaBackend(url="http://localhost:1").num_predict, 512)


class Generator(unittest.TestCase):
    def test_cli_generate_then_batch_runs_offline(self):
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "gen")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["generate", out, "--count", "12", "--seed", "7"]), 0)
                self.assertEqual(len(os.listdir(out)), 12)
                self.assertEqual(main(["generate", out, "--count", "3"]), 2)  # refuses to overwrite
            for name in os.listdir(out):
                r = run_case(load_case(os.path.join(out, name)), MockBackend(), today=date(2026, 3, 25))
                self.assertEqual(r.issues, [], name)

    def test_same_seed_gives_same_cases(self):
        from dd.generate import generate_cases
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            generate_cases(os.path.join(a, "x"), 5, 1)
            generate_cases(os.path.join(b, "x"), 5, 1)
            for name in sorted(os.listdir(os.path.join(a, "x"))):
                with open(os.path.join(a, "x", name, "order.csv"), encoding="utf-8") as f1, open(os.path.join(b, "x", name, "order.csv"), encoding="utf-8") as f2:
                    self.assertEqual(f1.read(), f2.read())


if __name__ == "__main__":
    unittest.main()
