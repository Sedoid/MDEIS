"""A tiny stand-in for the llama.cpp embedding server, for tests.

It speaks the same /v1/embeddings protocol over real HTTP, so the client code is exercised for real.
The vector is a hashed bag of words (same words, similar vector). It carries no meaning of its own:
tests with it check the plumbing, not search quality. Search quality is tested against the real
model on the server (test_rulebook_real_embedder.py).
"""

import hashlib
import json
import math
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DIM = 768


def bag_of_words(text, dim=DIM):
    vec = [0.0] * dim
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeEmbedder:
    """Start with `FakeEmbedder()`, stop with `.stop()`. `calls` lists every request's inputs."""

    def __init__(self, dim=DIM):
        self.dim = dim
        self.mode = "ok"  # "ok", "error" (HTTP 500), "short" (one vector too few), "wrong_size"
        self.calls: list[list[str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200 if self.path == "/health" else 404)
                self.end_headers()

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append(list(body["input"]))
                if outer.mode == "error":
                    self.send_response(500)
                    self.end_headers()
                    return
                texts = body["input"]
                size = outer.dim - 1 if outer.mode == "wrong_size" else outer.dim
                vectors = [bag_of_words(t, size) for t in texts]
                if outer.mode == "short":
                    vectors = vectors[:-1]
                data = [{"index": i, "embedding": v} for i, v in reversed(list(enumerate(vectors)))]
                payload = json.dumps({"model": "fake-embed", "data": data}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def texts_sent(self):
        return [t for call in self.calls for t in call]

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
