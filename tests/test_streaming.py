"""The Ollama backend streams, so a slow but working model is not mistaken for a stuck one, and stalls are explained."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dd.draft import BackendError, OllamaBackend
from dd.evalrun import evaluate, render_report
from dd.parse import load_case
from dd.pipeline import run_case

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CASES = os.path.join(ROOT, "cases")
LETTER = "Hello,\nThe carrier shows the package delivered on 2026-03-05. [E4]\nSincerely,"


def server(script):
    """script(handler) writes the response however the test wants. Returns (server, base_url)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"models": [{"name": "gemma3:4b"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            try:
                script(self)
            except (ConnectionError, OSError):
                pass

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def send(h, obj):
    h.wfile.write((json.dumps(obj) + "\n").encode())
    h.wfile.flush()


def good(h):
    for part in (LETTER[:20], LETTER[20:60], LETTER[60:]):
        send(h, {"response": part, "done": False})
    send(h, {"response": "", "done": True, "done_reason": "stop", "eval_count": 3})


class Streaming(unittest.TestCase):
    def run_script(self, script, **kw):
        srv, url = server(script)
        try:
            b = OllamaBackend(url=url, model="gemma3:4b", **kw)
            return b, b.generate("EVIDENCE", [], "x")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_text_is_assembled_and_speed_is_recorded(self):
        b, text = self.run_script(good)
        self.assertEqual(text, LETTER)
        st = b.last_stats
        self.assertEqual(st["tokens"], 3)
        self.assertEqual(st["done_reason"], "stop")
        self.assertFalse(st["truncated"])
        self.assertIsNotNone(st["first_token_seconds"])

    def test_silence_before_the_first_word_is_explained(self):
        def silent(h):
            time.sleep(3)
        with self.assertRaises(BackendError) as cm:
            self.run_script(silent, timeout=1)
        self.assertIn("No first word", str(cm.exception))
        self.assertIn("ollama ps", str(cm.exception))

    def test_a_stall_in_the_middle_is_explained(self):
        def stalls(h):
            send(h, {"response": "Hello,", "done": False})
            time.sleep(3)
        with self.assertRaises(BackendError) as cm:
            self.run_script(stalls, timeout=1)
        self.assertIn("stalled", str(cm.exception))
        self.assertIn("after 1 tokens", str(cm.exception))

    def test_a_slow_but_steady_model_is_not_timed_out(self):
        def drip(h):
            for part in ("Hello,\n", "The carrier shows the package delivered on 2026-03-05. [E4]\n", "Sincerely,"):
                time.sleep(0.6)
                send(h, {"response": part, "done": False})
            send(h, {"response": "", "done": True, "done_reason": "stop"})
        _, text = self.run_script(drip, timeout=1)  # 1.8 s in total, but never silent for 1 s
        self.assertTrue(text.endswith("Sincerely,"))

    def test_total_time_cap(self):
        def endless(h):
            for _ in range(50):
                time.sleep(0.3)
                send(h, {"response": "x", "done": False})
        srv, url = server(endless)
        try:
            b = OllamaBackend(url=url, model="gemma3:4b", timeout=5)
            b.max_seconds = 1
            with self.assertRaises(BackendError) as cm:
                b.generate("EVIDENCE", [], "x")
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertIn("longer than", str(cm.exception))

    def test_error_line_and_empty_answer(self):
        with self.assertRaises(BackendError) as cm:
            self.run_script(lambda h: send(h, {"error": "model requires more memory"}))
        self.assertIn("more memory", str(cm.exception))
        with self.assertRaises(BackendError) as cm:
            self.run_script(lambda h: send(h, {"response": "", "done": True, "done_reason": "stop"}))
        self.assertIn("empty", str(cm.exception))

    def test_truncation_is_reported_and_warned_about(self):
        def cut(h):
            send(h, {"response": "Hello,\nThe carrier shows the package delivered on 2026-03-05. [E4]\nThe buyer wro", "done": False})
            send(h, {"response": "", "done": True, "done_reason": "length", "eval_count": 512})
        srv, url = server(cut)
        try:
            b = OllamaBackend(url=url, model="gemma3:4b")
            r = run_case(load_case(os.path.join(CASES, "case_001_inr_delivered")), b, today=date(2026, 3, 12))
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertTrue(r.history[0]["stats"]["truncated"])
        self.assertTrue(any("length limit" in w for w in r.warnings))


class EvalReporting(unittest.TestCase):
    def test_rows_carry_speed_and_limit_works(self):
        srv, url = server(good)
        try:
            b = OllamaBackend(url=url, model="gemma3:4b")
            res = evaluate(CASES, b, repeat=1, today=date(2026, 3, 12), limit=1)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(res["summary"]["runs"], 1)
        self.assertIsNotNone(res["rows"][0]["tokens"])
        self.assertIn("avg_tokens_per_second", res["summary"])

    def test_report_lists_flagged_lines_and_repairs(self):
        class Liar:
            name = "liar"
            def generate(self, prompt, evidence, dtype, issues=None):
                return "Hello,\nThe buyer admitted receiving it [E4].\nThe carrier shows delivery on 2026-03-05 [E4].\nSincerely,"
        res = evaluate(CASES, Liar(), repeat=1, today=date(2026, 3, 12), limit=1)
        text = render_report(res)
        self.assertIn("WHAT WENT WRONG", text)
        self.assertIn("admitted", text)
        self.assertIn("repair:", text)

    def test_report_command_reads_a_saved_file_and_explains_bad_ones(self):
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "r.json")
            subprocess.run([sys.executable, "-m", "dd", "eval", "cases", "--repeat", "1", "--limit", "1", "--today", "2026-03-12", "--out", f],
                           cwd=ROOT, capture_output=True, text=True)
            ok = subprocess.run([sys.executable, "-m", "dd", "report", f], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("WHAT WENT WRONG", ok.stdout)
            bad = subprocess.run([sys.executable, "-m", "dd", "report", os.path.join(t, "nope.json")], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertNotIn("Traceback", bad.stderr)


if __name__ == "__main__":
    unittest.main()
