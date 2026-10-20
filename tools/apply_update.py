#!/usr/bin/env python3
"""Apply a developer-build update to a Vector board over its authenticated API.

  python3 tools/apply_update.py --host 192.168.1.82 --password <Gpassword> \
      --url http://192.168.1.66:8765/update-sys11.json

Serve the update file first, e.g. from the repo root:
  python3 -m http.server 8765 --bind <your-lan-ip>

Fetches a challenge, signs challenge+path+body with HMAC-SHA256(Gpassword),
POSTs /api/update/apply with skip_signature_check=true, and streams the
board's progress log until the update completes and the board reboots.
"""
import argparse
import hashlib
import hmac
import json
import urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--password", required=True, help="the board's Gpassword")
    ap.add_argument("--url", required=True, help="HTTP URL of the update .json")
    a = ap.parse_args()

    body = json.dumps({"url": a.url, "skip_signature_check": True})
    base = f"http://{a.host}"
    challenge = json.load(urllib.request.urlopen(f"{base}/api/auth/challenge"))["challenge"]
    path = "/api/update/apply"
    digest = hmac.new(a.password.encode(), (challenge + path + body).encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        base + path,
        data=body.encode(),
        headers={"content-type": "application/json", "x-auth-hmac": digest, "x-auth-challenge": challenge},
    )
    with urllib.request.urlopen(req) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            print(line.decode().strip())


if __name__ == "__main__":
    main()
