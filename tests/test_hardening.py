"""Final audit tests: bad input, unusable model output, privacy warning, API safety."""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import date

from dd.api import make_server
from dd.draft import MockBackend, OllamaBackend
from dd.evidence import build_evidence, classify
from dd.parse import load_case
from dd.pipeline import run_case, render_packet, clean_draft
from dd.verify import verify_letter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CASES = os.path.join(ROOT, "cases")
TODAY = date(2026, 3, 12)


def case(name):
    return load_case(os.path.join(CASES, name))


class BadInput(unittest.TestCase):
    def test_missing_folder_is_a_clear_error(self):
        with self.assertRaises(ValueError) as cm:
            load_case(os.path.join(CASES, "no_such_case"))
        self.assertIn("not found", str(cm.exception))

    def test_folder_without_notice_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError) as cm:
                load_case(t)
        self.assertIn("notice.txt", str(cm.exception))

    def test_bad_date_names_the_field(self):
        with tempfile.TemporaryDirectory() as t:
            with open(os.path.join(t, "notice.txt"), "w") as f:
                f.write("type: item not received\nopened: 03/10/2026\namount: 40\nreason: never arrived\n")
            with self.assertRaises(ValueError) as cm:
                run_case(load_case(t), MockBackend(), today=TODAY)
        self.assertIn("'opened'", str(cm.exception))
        self.assertIn("2026-03-10", str(cm.exception))

    def test_cli_reports_error_without_traceback(self):
        r = subprocess.run([sys.executable, "-m", "dd", "run", "cases/nope"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("error:", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_crlf_files_from_windows(self):
        with tempfile.TemporaryDirectory() as t:
            def w(name, data):
                with open(os.path.join(t, name), "wb") as f:
                    f.write(data)
            w("notice.txt", b"platform: etsy\r\ntype: item not received\r\namount: 40\r\nopened: 2026-03-10\r\nreason: Never arrived\r\n")
            w("order.csv", b"order_id,buyer_name,item,amount,order_date,ship_date,carrier,tracking_number,delivery_status,delivery_date\r\n9,Al,Mug,40,2026-03-01,2026-03-02,USPS,94001,delivered,2026-03-05\r\n")
            w("messages.txt", b"2026-03-07 09:12 | buyer | Not here\r\n")
            r = run_case(load_case(t), MockBackend(), today=TODAY)
        self.assertEqual(r.triage.verdict, "fight")
        self.assertEqual(r.issues, [])


class DeadlineWording(unittest.TestCase):
    def text(self, today):
        return render_packet(run_case(case("case_001_inr_delivered"), MockBackend(), today=today)).splitlines()[1]

    def test_wording(self):
        self.assertEqual(self.text(date(2026, 3, 16)), "DEADLINE: 2026-03-17 (1 day left)")
        self.assertEqual(self.text(date(2026, 3, 17)), "DEADLINE: 2026-03-17 (due today)")
        self.assertEqual(self.text(date(2026, 4, 1)), "DEADLINE: 2026-03-17 (passed 15 days ago)")


class UnusableModelOutput(unittest.TestCase):
    def run_with(self, text):
        class B:
            name = "b"
            local = True
            def generate(self, prompt, evidence, dtype, issues=None):
                return text
        d = case("case_001_inr_delivered")
        r = run_case(d, B(), today=TODAY)
        return r, build_evidence(d, classify(d))

    def test_empty_output_falls_back_to_the_evidence_only_template(self):
        for text in ("", "   \n", "Sure! Here is your letter."):
            r, ev = self.run_with(text)
            self.assertEqual(verify_letter(r.letter, ev, require_facts=True)[0], [], repr(text))
            self.assertTrue(any("evidence-only template" in x for x in r.repairs), repr(text))
            self.assertTrue(r.degraded)
            self.assertIn("[E4]", r.letter)

    def test_code_fences_are_stripped(self):
        d = case("case_001_inr_delivered")
        good = MockBackend().generate("", build_evidence(d, classify(d)), "not_received")

        class B:
            name = "b"
            def generate(self, prompt, evidence, dtype, issues=None):
                return "```text\n" + good + "\n```"
        r = run_case(d, B(), today=TODAY, photos="model")
        self.assertEqual(r.attempts, 1)
        self.assertFalse(r.degraded)
        self.assertNotIn("```", r.letter)
        self.assertNotIn("```", clean_draft("```\nx\n```"))

    def test_zero_max_attempts_does_not_crash(self):
        r = run_case(case("case_001_inr_delivered"), MockBackend(), today=TODAY, max_attempts=0)
        self.assertEqual(r.attempts, 1)


class Privacy(unittest.TestCase):
    def test_local_detection(self):
        self.assertTrue(OllamaBackend(url="http://localhost:11434").local)
        self.assertTrue(OllamaBackend(url="http://127.0.0.1:11434").local)
        self.assertFalse(OllamaBackend(url="http://203.0.113.5:11434").local)
        self.assertFalse(OllamaBackend(url="https://models.example.com").local)

    def test_remote_model_server_triggers_a_warning(self):
        class Remote(MockBackend):
            local = False
        r = run_case(case("case_001_inr_delivered"), Remote(), today=TODAY)
        self.assertTrue(any("not on this machine" in w for w in r.warnings))
        r2 = run_case(case("case_001_inr_delivered"), MockBackend(), today=TODAY)
        self.assertFalse(any("not on this machine" in w for w in r2.warnings))


class ApiSafety(unittest.TestCase):
    def setUp(self):
        self.srv = make_server(MockBackend(), port=0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def get(self, origin=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/health", headers={"Origin": origin} if origin else {})
        with urllib.request.urlopen(req) as r:
            return r.headers

    def test_cors_only_for_local_origins(self):
        self.assertEqual(self.get("http://localhost:5173").get("Access-Control-Allow-Origin"), "http://localhost:5173")
        self.assertIsNone(self.get("https://evil.example.com").get("Access-Control-Allow-Origin"))
        self.assertIsNone(self.get().get("Access-Control-Allow-Origin"))

    def test_preflight(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("OPTIONS", "/v1/cases/run", headers={"Origin": "http://localhost:5173"})
        r = c.getresponse()
        self.assertEqual(r.status, 204)
        self.assertEqual(r.getheader("Access-Control-Allow-Origin"), "http://localhost:5173")
        c.close()

    def test_oversized_body_rejected(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.putrequest("POST", "/v1/cases/run")
        c.putheader("Content-Length", "5000000")
        c.endheaders()
        self.assertEqual(c.getresponse().status, 413)
        c.close()

    def test_bad_json_is_400_not_a_crash(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/cases/run", b"{not json", {"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()


if __name__ == "__main__":
    unittest.main()


class MissingFolders(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "dd", *args], cwd=ROOT, capture_output=True, text=True, timeout=60)

    def test_missing_folder_gives_a_helpful_message_not_a_traceback(self):
        for cmd in (["eval", "no_such_folder", "--backend", "mock"], ["batch", "no_such_folder", "--backend", "mock"],
                    ["harvest", "no_such_folder", "--backend", "mock"]):
            r = self.run_cli(*cmd)
            self.assertEqual(r.returncode, 2, cmd)
            self.assertNotIn("Traceback", r.stderr, cmd)
            self.assertIn("folder not found", r.stderr, cmd)
            self.assertIn("python -m dd generate no_such_folder", r.stderr, cmd)

    def test_empty_folder_is_explained(self):
        with tempfile.TemporaryDirectory() as t:
            r = self.run_cli("eval", t, "--backend", "mock")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no case folders", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class Version(unittest.TestCase):
    def test_version_flag_and_results_carry_the_version(self):
        from dd import __version__
        r = subprocess.run([sys.executable, "-m", "dd", "--version"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), f"dd {__version__}")
        from dd.evalrun import evaluate
        self.assertEqual(evaluate(CASES, MockBackend(), repeat=1, today=TODAY, limit=1)["version"], __version__)
