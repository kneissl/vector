#!/usr/bin/env python3
"""
On-site diagnostic for a Vector board whose audit phone-home has gone quiet.

    python3 tools/diagnose_phone_home.py --host 192.168.1.43 \
        --config src/wpc/config/TheShadow_LX5.json

Hits three unauthenticated endpoints (/api/phone-home/status, /api/game/status,
/api/memory-snapshot), decodes the audit table locally with the same rules the
firmware uses, and prints a ranked verdict on which tick gate is blocking.

Offline mode (analyze a saved snapshot, skip the live endpoints):

    python3 tools/diagnose_phone_home.py --snapshot saved.txt \
        --config src/wpc/config/TheShadow_LX5.json
"""
import argparse
import json
import sys
import urllib.request

RECORD_SIZE = 6
SLOTS = ("left slot", "center slot", "right slot", "4th slot", "paid credits", "service credits")


def val3(buf, a):
    return buf[a] | (buf[a + 1] << 8) | (buf[a + 2] << 16)


def check_ok(buf, a):
    return buf[a + 3] == (0xFF - ((buf[a] + buf[a + 1] + buf[a + 2]) & 0xFF)) & 0xFF


def get_json(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=8) as r:
            return json.load(r)
    except Exception as e:
        return {"_error": str(e)}


def get_snapshot(base):
    with urllib.request.urlopen(base + "/api/memory-snapshot", timeout=30) as r:
        return [int(x) for x in r.read().split()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host")
    ap.add_argument("--snapshot", help="saved snapshot file (offline mode)")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    if not args.host and not args.snapshot:
        ap.error("need --host or --snapshot")

    cfg_all = json.load(open(args.config))
    aud = cfg_all.get("Audits")
    if not aud:
        sys.exit("config has no Audits section")
    bip_adr = cfg_all.get("BallInPlay", {}).get("Address")

    findings = []

    ph = game = None
    if args.host:
        base = f"http://{args.host}"
        ph = get_json(base, "/api/phone-home/status")
        game = get_json(base, "/api/game/status")
        print("phone-home status:", json.dumps(ph))
        print("game status      :", json.dumps({k: game.get(k) for k in ("GameActive", "BallInPlay")}))

        if "_error" in ph:
            findings.append(("BOARD UNREACHABLE", f"status endpoint failed: {ph['_error']}"))
        else:
            if not ph.get("configured"):
                findings.append(("GATE 2: NOT CONFIGURED", "phone_home.json lost or missing token/serial -- re-enter via admin page"))
            lr = ph.get("last_result")
            if lr and not str(lr).startswith("2"):
                findings.append(("GATE 7: POSTS FAILING", f"attempts happen but fail: {lr!r} -- network/TLS problem; check /api/logs for 'AUDIT:' lines"))
        if game and not game.get("_error"):
            if game.get("BallInPlay"):
                findings.append(("GATE 3: BALL-IN-PLAY STUCK", f"BallInPlay={game['BallInPlay']} with GameActive={game.get('GameActive')} -- the idle gate never opens; power-cycle the game"))

        buf = get_snapshot(base)
    else:
        buf = [int(x) for x in open(args.snapshot).read().split()]

    if len(buf) != 8192:
        sys.exit(f"snapshot: expected 8192 bytes, got {len(buf)}")

    # the firmware's veto-strict records: six earnings slots + games started
    print("\nstrict check bytes (any FAIL here silently blocks every post):")
    veto = []
    for i, name in enumerate(SLOTS):
        a = aud["EarningsTableAdr"] + i * aud.get("RecordSize", RECORD_SIZE)
        ok = check_ok(buf, a)
        print(f"  {name:16} @{hex(a)}  value={val3(buf, a):<8} {'ok' if ok else 'FAIL'}")
        if not ok:
            veto.append(name)
    gs_ok = check_ok(buf, aud["GamesStartedAdr"])
    print(f"  {'games started':16} @{hex(aud['GamesStartedAdr'])}  value={val3(buf, aud['GamesStartedAdr']):<8} {'ok' if gs_ok else 'FAIL'}")
    if not gs_ok:
        veto.append("games started")
    if veto:
        findings.append(("GATE 5: DECODE VETO", f"stale check byte on: {', '.join(veto)} -- the firmware refuses the whole report. Fix: make this record best-effort (like minutes-on) in audit_report.py"))

    if bip_adr is not None:
        print(f"\nraw BallInPlay byte @{hex(bip_adr)}: {buf[bip_adr]}")
        if not args.host and buf[bip_adr]:
            findings.append(("GATE 3: BALL-IN-PLAY STUCK", f"raw byte = {buf[bip_adr]}"))

    print("\n" + "=" * 60)
    if findings:
        print("VERDICT (most likely first):")
        for tag, detail in findings:
            print(f"  {tag}\n    {detail}")
    else:
        print("VERDICT: no gate is blocked by board state.")
        print("  Config present, no stuck ball, all strict check bytes pass.")
        print("  Remaining suspects: rate-limit timing (wait one 5-min tick while")
        print("  idle), wifi flapping exactly at tick time, or the tick isn't")
        print("  running at all -- check /api/logs and, if needed, reboot the board")
        print("  and watch /api/phone-home/status for a post within ~10 idle minutes.")


if __name__ == "__main__":
    main()
