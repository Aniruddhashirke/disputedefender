"""Thin JSON API over run_case. Standard library only so it runs anywhere.
POST /v1/cases/run  body = {"platform","notice","order","messages","photos","policy_text","listing_text","force"?}
GET  /health
Swapping this for FastAPI later is a mechanical change; the pipeline function does not change."""
import json
import re
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .draft import BackendError
from .parse import dispute_from_dict
from .pipeline import run_case


MAX_BODY = 1_000_000
LOCAL_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")


def make_server(backend, host="127.0.0.1", port=8000):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            origin = self.headers.get("Origin", "")
            if LOCAL_ORIGIN.match(origin):  # only a UI served from this machine may call the API from a browser
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            origin = self.headers.get("Origin", "")
            if LOCAL_ORIGIN.match(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"status": "ok", "backend": backend.name})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/v1/cases/run":
                return self._send(404, {"error": "not found"})
            try:
                size = int(self.headers.get("Content-Length", 0))
                if size > MAX_BODY:
                    return self._send(413, {"error": "request too large"})
                payload = json.loads(self.rfile.read(size) or b"{}")
                today = date.fromisoformat(payload["today"]) if payload.get("today") else None
                r = run_case(dispute_from_dict(payload), backend, today=today, force=bool(payload.get("force")),
                             exclude=payload.get("exclude"), photos=payload.get("photos", "code"),
                             prompt_style=payload.get("prompt_style", "full"))
                self._send(200, r.to_dict())
            except (ValueError, KeyError, TypeError) as e:
                self._send(400, {"error": str(e)})
            except BackendError as e:
                self._send(502, {"error": str(e)})

        def log_message(self, *a):
            pass

    return ThreadingHTTPServer((host, port), H)


if __name__ == "__main__":
    import argparse
    from .draft import get_backend
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="mock")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    srv = make_server(get_backend(a.backend), port=a.port)
    print(f"Dispute Defender API on http://127.0.0.1:{a.port}")
    srv.serve_forever()
