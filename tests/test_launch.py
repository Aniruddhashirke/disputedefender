"""The 'No module named dd' problem: the project must run from other folders."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def run(args, cwd):
    return subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True, timeout=60)


class Launch(unittest.TestCase):
    def test_run_py_works_from_any_folder(self):
        with tempfile.TemporaryDirectory() as t:
            r = run([os.path.join(ROOT, "run.py"), "run", "cases/case_001_inr_delivered", "--today", "2026-03-12"], t)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("VERDICT: FIGHT", r.stdout)

    def test_folder_launch_works_from_the_parent_folder(self):
        r = run([os.path.basename(ROOT), "run", "cases/case_001_inr_delivered", "--today", "2026-03-12"], os.path.dirname(ROOT))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("VERDICT: FIGHT", r.stdout)

    def test_batch_resolves_relative_to_project(self):
        with tempfile.TemporaryDirectory() as t:
            r = run([os.path.join(ROOT, "run.py"), "batch", "cases", "--today", "2026-03-12"], t)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("case_005_nad_with_listing", r.stdout)


if __name__ == "__main__":
    unittest.main()
