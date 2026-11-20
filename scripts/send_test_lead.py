"""Send one signed test lead to a running instance.

python scripts/send_test_lead.py --url http://localhost:8000 --secret "$WEBHOOK_SECRET"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.security import sign_body  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--secret", default="")
    parser.add_argument("--phone", default="(512) 555-0142")
    parser.add_argument("--message", default="AC is blowing warm air and it's 101 outside")
    args = parser.parse_args()

    body = json.dumps({"name": "Test Customer", "phone": args.phone, "message": args.message}).encode()
    headers = {"Content-Type": "application/json"}
    if args.secret:
        ts = str(int(time.time()))
        headers |= {"X-Timestamp": ts, "X-Signature": sign_body(args.secret, body, ts)}
    req = urllib.request.Request(f"{args.url.rstrip('/')}/webhooks/form", data=body, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (developer tool, URL from CLI)
        print(resp.status, resp.read().decode())


if __name__ == "__main__":
    main()
