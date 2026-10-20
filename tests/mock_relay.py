#!/usr/bin/env python3
"""Tiny mock OpenAI-compatible relay used by test_offline.py (no network, no billing)."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
MODELS = {
    "data": [
        {"id": "gpt-ui-model"},
        {"id": "gpt-5.4"},
        {"id": "gpt-image-2"},
        {"id": "gpt-image-2.5"},
        {"id": "gpt-image-2.5-flare"},
    ]
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):  # silence
        pass

    def _auth_ok(self):
        return self.headers.get("Authorization", "").startswith("Bearer sk-test-")

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            if not self._auth_ok():
                return self._json(401, {"error": {"message": "invalid api key"}})
            return self._json(200, MODELS)
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if not self._auth_ok():
            return self._json(401, {"error": {"message": "invalid api key"}})
        path = self.path.rstrip("/")
        raw = self._read_body()

        if path == "/v1/images/generations":
            payload = json.loads(raw.decode("utf-8"))
            if "prompt" not in payload or "model" not in payload:
                return self._json(400, {"error": {"message": "missing prompt/model"}})
            return self._json(200, {"created": 0, "data": [{"b64_json": TINY_PNG_B64}]})

        if path == "/v1/images/edits":
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                return self._json(400, {"error": {"message": "expected multipart"}})
            if b'name="prompt"' not in raw or b'name="image[]"' not in raw:
                return self._json(400, {"error": {"message": "missing prompt/image"}})
            return self._json(200, {"data": [{"b64_json": TINY_PNG_B64}]})

        if path == "/v1/responses":
            payload = json.loads(raw.decode("utf-8"))
            if payload.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                def send(obj):
                    self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
                    self.wfile.flush()

                send(
                    {
                        "type": "response.image_generation_call.partial_image",
                        "partial_image_b64": TINY_PNG_B64,
                    }
                )
                send(
                    {
                        "type": "response.completed",
                        "response": {
                            "output": [
                                {"type": "image_generation_call", "result": TINY_PNG_B64}
                            ]
                        },
                    }
                )
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            return self._json(
                200, {"output": [{"type": "image_generation_call", "result": TINY_PNG_B64}]}
            )

        self._json(404, {"error": {"message": "not found"}})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PORT={server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
