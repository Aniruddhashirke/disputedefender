import json
import os
import threading
import unittest
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

from dd.api import make_server
from dd.draft import BackendError, MockBackend, OllamaBackend
from dd.evidence import build_evidence, classify, find_gaps
from dd.adapters import get_adapter
from dd.parse import load_case
from dd.pipeline import run_case
from dd.verify import verify_letter

ROOT = os.path.join(os.path.dirname(__file__), "..", "cases")
TODAY = date(2026, 3, 12)


def case(name):
    return load_case(os.path.join(ROOT, name))


class Parse(unittest.TestCase):
    def test_load(self):
        d = case("case_001_inr_delivered")
        self.assertEqual(d.order.order_id, "1001234")
        self.assertEqual(len(d.messages), 3)
        self.assertEqual(d.photos, ["packing_2026-03-02.jpg"])
        self.assertEqual(classify(d), "not_received")
        self.assertEqual(classify(case("case_004_not_as_described")), "not_as_described")


class Evidence(unittest.TestCase):
    def test_gaps_listed_when_missing(self):
        d = case("case_004_not_as_described")
        ev = build_evidence(d, "not_as_described")
        slots = {g.slot for g in find_gaps(ev, get_adapter("etsy").checklist("not_as_described"))}
        self.assertIn("listing_description", slots)
        self.assertIn("policy_reference", slots)
        self.assertNotIn("item_photos", slots)

    def test_full_case_has_fewer_gaps(self):
        d = case("case_001_inr_delivered")
        ev = build_evidence(d, "not_received")
        self.assertEqual(find_gaps(ev, get_adapter("etsy").checklist("not_received")), [])


class Triage(unittest.TestCase):
    def test_strong_case_fights(self):
        r = run_case(case("case_001_inr_delivered"), MockBackend(), today=TODAY)
        self.assertEqual(r.triage.verdict, "fight")
        self.assertGreaterEqual(r.triage.win_likelihood, 0.9)

    def test_no_evidence_folds(self):
        r = run_case(case("case_002_inr_no_evidence"), MockBackend(), today=TODAY)
        self.assertEqual(r.triage.verdict, "fold")
        self.assertIsNone(r.letter)

    def test_low_value_folds_despite_good_evidence(self):
        r = run_case(case("case_003_low_value"), MockBackend(), today=TODAY)
        self.assertEqual(r.triage.verdict, "fold")
        self.assertIn("not worth", r.triage.reason)

    def test_force_drafts_anyway(self):
        r = run_case(case("case_003_low_value"), MockBackend(), today=TODAY, force=True)
        self.assertIsNotNone(r.letter)


class Verify(unittest.TestCase):
    def setUp(self):
        d = case("case_001_inr_delivered")
        self.ev = build_evidence(d, "not_received")
        self.deliv = next(e for e in self.ev if e.kind == "delivery_confirmation")

    def test_good_line_passes(self):
        issues, ok = verify_letter(f"The carrier shows the package delivered on 2026-03-05. [{self.deliv.id}]", self.ev)
        self.assertEqual(issues, [])

    def test_wrong_date_flagged(self):
        issues, _ = verify_letter(f"The carrier shows the package delivered on 2026-03-04. [{self.deliv.id}]", self.ev)
        self.assertEqual(len(issues), 1)
        self.assertIn("2026-03-04", issues[0].problem)

    def test_uncited_claim_flagged(self):
        issues, _ = verify_letter("The package was left at the front door.", self.ev)
        self.assertEqual(issues[0].problem, "no evidence citation")

    def test_unknown_citation_flagged(self):
        issues, _ = verify_letter("It was delivered. [E99]", self.ev)
        self.assertIn("unknown evidence", issues[0].problem)

    def test_invented_quote_flagged(self):
        issues, _ = verify_letter(f'The buyer wrote: "I received it" [{self.deliv.id}]', self.ev)
        self.assertIn("quoted text", issues[0].problem)

    def test_boilerplate_allowed(self):
        issues, _ = verify_letter("Hello,\nThank you for reviewing this case.\nSincerely,", self.ev)
        self.assertEqual(issues, [])


class Hardening(unittest.TestCase):
    def setUp(self):
        self.d = case("case_004_not_as_described")
        self.ev = build_evidence(self.d, "not_as_described")

    def test_name_cited_to_wrong_evidence_flagged(self):
        issues, _ = verify_letter("The buyer, Priya N., opened a case on 2026-03-09 [E1].", self.ev)
        self.assertEqual(len(issues), 1)
        self.assertIn("Priya", issues[0].problem)

    def test_name_cited_to_right_evidence_passes(self):
        issues, _ = verify_letter("The order was placed by Priya N. on 2026-02-25 [E2].", self.ev)
        self.assertEqual(issues, [])

    def test_invented_carrier_flagged(self):
        issues, _ = verify_letter("The scarf shipped on 2026-02-26 via FedEx [E3].", self.ev)
        self.assertIn("FedEx", issues[0].problem)

    def test_closing_added_when_model_omits_it(self):
        from dd.pipeline import ensure_closing
        out = ensure_closing("Hello,\nThe carrier shows delivery on 2026-03-02. [E4]\nSincerely,")
        self.assertIn("I ask that this case be resolved", out)
        self.assertTrue(out.rstrip().endswith("Sincerely,"))
        self.assertEqual(ensure_closing(out), out)

    def test_refund_offer_triggers_warning(self):
        r = run_case(self.d, MockBackend(), today=TODAY)
        self.assertEqual(r.suggested_exclusions, ["E7", "E8"])
        self.assertTrue(any("--exclude E7,E8" in w for w in r.warnings))
        self.assertIn("I ask that this case be resolved", r.letter)

    def test_curly_quotes_cannot_bypass_quote_check(self):
        issues, _ = verify_letter("The buyer wrote: \u201cI love it and want to keep it\u201d [E6].", self.ev)
        self.assertEqual(len(issues), 1)
        self.assertIn("quoted text", issues[0].problem)

    def test_curly_quotes_with_real_text_pass(self):
        issues, _ = verify_letter("The buyer wrote: \u201cThe scarf looks more orange than the photos.\u201d [E6]", self.ev)
        self.assertEqual(issues, [])

    def test_exclude_removes_evidence_from_letter(self):
        r = run_case(self.d, MockBackend(), today=TODAY, exclude=["E7", "E8"])
        self.assertNotIn("partial refund", r.letter)
        self.assertNotIn("full refund", r.letter)
        self.assertEqual(r.excluded, ["E7", "E8"])
        self.assertEqual(r.suggested_exclusions, [])
        self.assertFalse(r.degraded)

    def test_citing_excluded_evidence_is_flagged(self):
        class Sneaky:
            name = "sneaky"
            def generate(self, prompt, evidence, dtype, issues=None):
                return "Hello,\nThe seller offered a partial refund. [E7]\nSincerely,"
        r = run_case(self.d, Sneaky(), today=TODAY, exclude=["E7"])
        self.assertTrue(r.degraded)
        self.assertNotIn("partial refund", r.letter)

    def test_unknown_exclude_id_errors(self):
        with self.assertRaises(ValueError):
            run_case(self.d, MockBackend(), today=TODAY, exclude=["E99"])


class FlakyBackend:
    """First draft contains a fabricated claim; second draft is clean. Tests retry-with-feedback."""
    name = "flaky"

    def __init__(self):
        self.calls = 0
        self.seen_feedback = None

    def generate(self, prompt, evidence, dtype, issues=None):
        self.calls += 1
        if self.calls == 1:
            return "Hello,\nThe package was signed for by the buyer's neighbor.\nSincerely,"
        self.seen_feedback = prompt
        return MockBackend().generate(prompt, evidence, dtype)


class AlwaysBad:
    name = "bad"

    def generate(self, prompt, evidence, dtype, issues=None):
        return "Hello,\nThe buyer admitted receiving it.\nThe carrier shows the package delivered on 2026-03-05. [E4]\nSincerely,"


class Pipeline(unittest.TestCase):
    def test_end_to_end_verified(self):
        r = run_case(case("case_001_inr_delivered"), MockBackend(), today=TODAY)
        self.assertEqual(r.issues, [])
        self.assertFalse(r.degraded)
        self.assertEqual(r.days_left, 5)
        self.assertIn("[E4]", r.letter)

    def test_retry_with_feedback(self):
        b = FlakyBackend()
        r = run_case(case("case_001_inr_delivered"), b, today=TODAY)
        self.assertEqual(b.calls, 2)
        self.assertEqual(r.attempts, 2)
        self.assertIn("no evidence citation", b.seen_feedback)
        self.assertFalse(r.degraded)

    def test_fails_closed_after_retries(self):
        r = run_case(case("case_001_inr_delivered"), AlwaysBad(), today=TODAY)
        self.assertTrue(r.degraded)
        self.assertNotIn("admitted", r.letter)
        self.assertIn("2026-03-05", r.letter)

    def test_every_letter_line_in_mock_is_cited_or_boilerplate(self):
        for name in ("case_001_inr_delivered", "case_004_not_as_described"):
            r = run_case(case(name), MockBackend(), today=TODAY)
            self.assertEqual(r.issues, [], name)

    def test_deadline_passed_warns(self):
        r = run_case(case("case_001_inr_delivered"), MockBackend(), today=date(2026, 4, 1))
        self.assertTrue(any("Deadline passed" in w for w in r.warnings))


class StubOllama(BaseHTTPRequestHandler):
    installed = ["llama3:8b", "gemma3:4b", "gemma-test:latest"]

    def do_GET(self):
        body = json.dumps({"models": [{"name": n} for n in self.installed]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        req = json.loads(self.rfile.read(n))
        assert req["stream"] is True and "EVIDENCE" in req["prompt"]
        text = "Hello,\nThe carrier shows the package delivered on 2026-03-05. [E4]\nSincerely,"
        pieces = [text[i:i + 12] for i in range(0, len(text), 12)]
        lines = [json.dumps({"response": p, "done": False}) for p in pieces] + [json.dumps({"response": "", "done": True, "done_reason": "stop", "eval_count": len(pieces)})]
        body = ("\n".join(lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class OllamaBackendTest(unittest.TestCase):
    def test_roundtrip_against_stub_server(self):
        srv = HTTPServer(("127.0.0.1", 0), StubOllama)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        b = OllamaBackend(url=f"http://127.0.0.1:{srv.server_port}", model="gemma-test")  # matches gemma-test:latest
        r = run_case(case("case_001_inr_delivered"), b, today=TODAY)
        srv.shutdown()
        srv.server_close()
        self.assertEqual(r.issues, [])
        self.assertIn("delivered on 2026-03-05", r.letter)

    def _stub(self):
        srv = HTTPServer(("127.0.0.1", 0), StubOllama)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://127.0.0.1:{srv.server_port}"

    def test_auto_picks_installed_gemma_when_no_model_given(self):
        srv, url = self._stub()
        old = os.environ.pop("DD_MODEL", None)
        try:
            b = OllamaBackend(url=url)
            r = run_case(case("case_001_inr_delivered"), b, today=TODAY)
        finally:
            srv.shutdown()
            srv.server_close()
            if old:
                os.environ["DD_MODEL"] = old
        self.assertEqual(b.model, "gemma3:4b")
        self.assertEqual(r.issues, [])

    def test_missing_model_error_lists_installed_models(self):
        srv, url = self._stub()
        try:
            b = OllamaBackend(url=url, model="gemma3")  # only gemma3:4b is installed, not gemma3:latest
            with self.assertRaises(BackendError) as cm:
                run_case(case("case_001_inr_delivered"), b, today=TODAY)
        finally:
            srv.shutdown()
            srv.server_close()
        msg = str(cm.exception)
        self.assertIn("not installed", msg)
        self.assertIn("gemma3:4b", msg)

    def test_unreachable_server_raises_clear_error(self):
        b = OllamaBackend(url="http://127.0.0.1:9", timeout=1)
        with self.assertRaises(BackendError):
            run_case(case("case_001_inr_delivered"), b, today=TODAY)


class Api(unittest.TestCase):
    def test_run_endpoint(self):
        srv = make_server(MockBackend(), port=0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        payload = {"platform": "etsy", "today": "2026-03-12",
                   "notice": {"type": "item not received", "reason": "Never arrived", "amount": "48.00", "opened": "2026-03-10"},
                   "order": {"order_id": "7", "item": "Mug", "amount": "48.00", "order_date": "2026-03-01", "ship_date": "2026-03-02",
                             "carrier": "USPS", "tracking_number": "940010", "delivery_status": "delivered", "delivery_date": "2026-03-05"},
                   "messages": [{"ts": "2026-03-07 09:12", "sender": "buyer", "text": "Not here yet."}]}
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/cases/run", json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"})
        out = json.loads(urllib.request.urlopen(req).read())
        health = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/health").read())
        srv.shutdown()
        srv.server_close()
        self.assertEqual(out["triage"]["verdict"], "fight")
        self.assertEqual(out["issues"], [])
        self.assertEqual(health["status"], "ok")


if __name__ == "__main__":
    unittest.main()
