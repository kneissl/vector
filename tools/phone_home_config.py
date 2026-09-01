#!/usr/bin/env python3
"""Configure a Vector board's phone-home settings over its authenticated API.

  python3 tools/phone_home_config.py --host 192.168.1.50 --password <Gpassword> \
      --url https://pinbooks-ingest.troy-e1a.workers.dev/v1/audits \
      --token <bearer token> --serial <serial>

Fetches a challenge, signs challenge+path+body with HMAC-SHA256(Gpassword),
POSTs /api/phone-home/config, then prints /api/phone-home/status. Omitted
fields keep their stored value, so a token can be rotated alone.
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
    ap.add_argument("--url")
    ap.add_argument("--token")
    ap.add_argument("--serial")
    a = ap.parse_args()

    fields = {k: v for k, v in (("url", a.url), ("token", a.token), ("serial", a.serial)) if v}
    if not fields:
        raise SystemExit("nothing to set: pass --url/--token/--serial")
    body = json.dumps(fields)
    base = f"http://{a.host}"
    challenge = json.load(urllib.request.urlopen(f"{base}/api/auth/challenge"))["challenge"]
    path = "/api/phone-home/config"
    digest = hmac.new(a.password.encode(), (challenge + path + body).encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        base + path,
        data=body.encode(),
        headers={"content-type": "application/json", "x-auth-hmac": digest, "x-auth-challenge": challenge},
    )
    print(urllib.request.urlopen(req).read().decode())
    print(urllib.request.urlopen(f"{base}/api/phone-home/status").read().decode())


if __name__ == "__main__":
    main()
