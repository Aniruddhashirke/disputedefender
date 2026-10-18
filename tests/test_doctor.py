import contextlib
import io
import json
import subprocess
import sys
import threading
import os
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dd.doctor import check_ollama

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def stub(models=("gemma3:4b",), ps=None, tokens=3, answer="ready"):
    class H(BaseHTTPRequestHandler):
        def _json(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/tags":
                self._json({"models": [{"name": m} for m in models]})
            elif self.path == "/api/ps":
                if ps is None:
                    self.send_response(404)
                    self.end_headers()
                else:
                    self._json({"models": ps})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            try:
                for _ in range(tokens):
                    self.wfile.write((json.dumps({"response": answer + " ", "done": False}) + "\n").encode())
                self.wfile.write((json.dumps({"response": "", "done": True, "done_reason": "stop", "eval_count": tokens}) + "\n").encode())
            except (ConnectionError, OSError):
                pass

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


class Doctor(unittest.TestCase):
    def run_check(self, **kw):
        srv, url = stub(**{k: v for k, v in kw.items() if k in ("models", "ps", "tokens")})
        try:
            return check_ollama(url=url, model=kw.get("model"), timeout=5)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_server_not_running_says_how_to_start_it(self):
        ok, lines = check_ollama(url="http://127.0.0.1:9", timeout=2)
        text = "\n".join(lines)
        self.assertFalse(ok)
        self.assertIn("Cannot reach Ollama", text)
        self.assertIn("ollama serve", text)

    def test_healthy_server_reports_gpu_share_and_speed(self):
        ok, lines = self.run_check(ps=[{"name": "gemma3:4b", "size": 3_500_000_000, "size_vram": 1_610_000_000}])
        text = "\n".join(lines)
        self.assertTrue(ok, text)
        self.assertIn("46% on the graphics chip", text)
        self.assertIn("Model in use: gemma3:4b", text)
        self.assertIn("tokens per second", text)

    def test_cpu_only_is_called_out(self):
        ok, lines = self.run_check(ps=[{"name": "gemma3:4b", "size": 2_900_000_000, "size_vram": 0}])
        self.assertIn("100% CPU", "\n".join(lines))

    def test_missing_model_lists_what_is_installed(self):
        ok, lines = self.run_check(models=("llama3:8b",), ps=[], model="gemma3:4b")
        text = "\n".join(lines)
        self.assertFalse(ok)
        self.assertIn("not installed", text)
        self.assertIn("llama3:8b", text)

    def test_no_models_installed(self):
        ok, lines = self.run_check(models=())
        self.assertFalse(ok)
        self.assertIn("ollama pull", "\n".join(lines))

    def test_older_ollama_without_ps_still_works(self):
        ok, lines = self.run_check(ps=None)
        self.assertTrue(ok)
        self.assertIn("Could not read where the model is running", "\n".join(lines))

    def test_cli_exit_codes(self):
        env = dict(os.environ, DD_OLLAMA_URL="http://127.0.0.1:9")
        r = subprocess.run([sys.executable, "-m", "dd", "doctor", "--timeout", "2"], cwd=ROOT, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("FAIL", r.stdout)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
