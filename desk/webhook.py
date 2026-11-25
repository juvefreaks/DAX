"""Send each finished round to a webhook (and keep a local JSONL audit log)."""
from __future__ import annotations

import json
import pathlib

import requests


def send_round(url: str | None, payload: dict, log_path: pathlib.Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps(payload, default=str) + "\n")
    if not url:
        return None
    try:
        r = requests.post(url, json=payload, timeout=5)
        return r.status_code
    except requests.RequestException as e:
        print(f"[webhook] round {payload.get('round')} failed: {e}")
        return None
