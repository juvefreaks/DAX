"""Minimal webhook receiver: prints every round and appends it to out/webhook_received.jsonl.

    python webhook_receiver.py --port 8080
    python run_desk.py --webhook http://localhost:8080/round
(expose it with a tunnel such as cloudflared if you want a public URL)
"""
import argparse
import json
import pathlib
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = pathlib.Path("out/webhook_received.jsonl")


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            p = json.loads(body)
        except ValueError:
            self.send_response(400); self.end_headers(); return
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(p) + "\n")
        print(f"[{p.get('start','?')}] ROUND {p.get('round')} {p.get('call')} "
              f"A={p.get('pnl_a'):+.2f} B={p.get('pnl_b'):+.2f} | {p.get('reason')}", flush=True)
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    HTTPServer(("0.0.0.0", ap.parse_args().port), H).serve_forever()
