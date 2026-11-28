#!/usr/bin/env python3
"""A stand-in for `strands-decider serve` (Strands Decider's System One server), for tests and renders: no model, no
weights. GET /health and /ping say it's up; POST /v1/systemone answers each question in the shapes the real one does
(noul: its probability; choice: the option picked, each one's probability, a confidence; score: the expected level,
its legend, each level's probability, a confidence), worked out from a hash of the state and the question, so the
same ask gets the same answer. A question as the real one refuses it (a choice with fewer than 2 options, a score with
fewer than 2 levels or more than 10, a type it doesn't know) is answered 422.

    python3 tests/fake_decider.py [--port 8000]     (prints the port it listens on, on 127.0.0.1)
"""
import argparse
import hashlib
import http.server
import json
import math
import sys
import threading
import time

MODEL = "strands-decider-E2B-gemma4-v2-2611"


def _logits(state, name, q, n):
    seed = hashlib.sha256(json.dumps([state, name, q], sort_keys=True).encode()).digest()
    return [seed[i] / 32.0 for i in range(n)]


def _softmax(xs):
    m = max(xs)
    e = [math.exp(x - m) for x in xs]
    return [x / sum(e) for x in e]


def answer(state, name, q):
    kind, crit = q.get("type"), q.get("criteria")
    if kind == "noul":
        p = _softmax(_logits(state, name, q, 2))[1]
        return {"type": "noul", "noul": round(p, 4)}
    if kind == "choice":
        if not isinstance(crit, dict) or len(crit) < 2:
            raise ValueError("choice requires at least 2 options")
        names = list(crit)
        ps = _softmax(_logits(state, name, q, len(names)))
        best = max(range(len(names)), key=ps.__getitem__)
        return {"type": "choice", "choice": names[best], "probabilities": dict(zip(names, map(lambda p: round(p, 4), ps))),
                "confidence": round(ps[best], 4)}
    if kind == "score":
        if not isinstance(crit, list) or not 2 <= len(crit) <= 10:
            raise ValueError("score requires between 2 and 10 levels")
        ps = _softmax(_logits(state, name, q, len(crit)))
        return {"type": "score", "score": round(sum(i * p for i, p in enumerate(ps)), 4),
                "legend": {str(i): str(c) for i, c in enumerate(crit)},
                "probabilities": {str(i): round(p, 4) for i, p in enumerate(ps)}, "confidence": round(max(ps), 4)}
    raise ValueError(f"unknown question type {kind!r}")


class Handler(http.server.BaseHTTPRequestHandler):
    def send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/health", "/ping"):
            return self.send(200, {"status": "ok", "model": MODEL, "checkpoint": "StrandsAgents/" + MODEL,
                                   "device": "cpu", "vision": False})
        self.send(404, {"detail": "Not Found"})

    def do_POST(self):
        if self.path != "/v1/systemone":
            return self.send(404, {"detail": "Not Found"})
        started = time.monotonic()
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            qs = req.get("questions")
            if not isinstance(qs, dict) or not qs:
                raise ValueError("questions: at least 1 question")
            answers = {n: answer(req.get("state"), n, q) for n, q in qs.items()}
        except (ValueError, AttributeError) as e:
            return self.send(422, {"detail": str(e)})
        self.send(200, {"model": MODEL, "answers": answers, "usage": {"input_tokens": 86, "output_tokens": len(answers)},
                        "latency_ms": round((time.monotonic() - started) * 1000 + 480, 2)})

    def log_message(self, *args):
        pass


def serve(port=0):
    """A stand-in listening on 127.0.0.1:port (0: any free one), on a thread of its own: the server (.server_port,
    .shutdown())."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True, name="fake-decider").start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    s = serve(ap.parse_args().port)
    print(s.server_port, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        sys.exit(0)
