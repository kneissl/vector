#!/usr/bin/env python3
"""
Decode WPC operator audit counters from Vector /api/memory-snapshot dumps,
driven by a per-game Vector config file.

Anchor model (verified on The Shadow LX-5; Fish Tales is the second title):
  - audit table base 0x1813 (assumed WPC-constant; --table-base to override)
      TOTAL earnings block   base+6     (6 slots x 6 bytes)
      RECENT-B block         base+42
      RECENT-C block         base+78
      games started          base+114
      minutes on             base+174
  - live credit state from config: HighScores.GCChecksumResultAdr+8 / +9
  - counter record: 3-byte LE value, check byte = 0xFF - (sum & 0xFF), 2 spare

Usage:
  # capture (repeat per label: baseline, leftCoin, rightCoin, serviceCredit, gameStart)
  curl -s http://<vector-ip>/api/memory-snapshot | paste -sd' ' - > baseline.txt

  # full report across snapshots
  python3 tools/decode_wpc_audits.py --config src/wpc/config/FishTales_L5.json \
      baseline.txt leftCoin.txt rightCoin.txt serviceCredit.txt gameStart.txt

  # hunt for the earnings block on an unverified game: pass the six values
  # read off the operator menu (left center right 4th paid service)
  python3 tools/decode_wpc_audits.py --config src/wpc/config/FishTales_L5.json \
      --find baseline.txt 2 4 0 0 6 3
"""
import json
import sys

RECORD_SIZE = 6
SLOTS = ("left slot", "center slot", "right slot", "4th slot", "paid credits", "service credits")
BLOCK_OFFSETS = {"TOTAL": 6, "RECENT-B": 42, "RECENT-C": 78}
GAMES_STARTED_OFFSET = 114
MINUTES_ON_OFFSET = 174
DEFAULT_TABLE_BASE = 0x1813
DEFAULT_COIN_CENTS = [25, 25, 25, 25]


def val(buf, a):
    """3-byte little-endian counter."""
    return buf[a] | (buf[a + 1] << 8) | (buf[a + 2] << 16)


def check_ok(buf, a):
    """Check byte at +3 is 0xFF minus the sum of the three value bytes."""
    return buf[a + 3] == (0xFF - ((buf[a] + buf[a + 1] + buf[a + 2]) & 0xFF)) & 0xFF


def anchors_from_config(cfg, table_base=DEFAULT_TABLE_BASE):
    """Predict every audit address for a game from its Vector config."""
    gc_ck = int(cfg["HighScores"]["GCChecksumResultAdr"])
    return {
        "table_base": table_base,
        "adj_ck_start": int(cfg["Adjustments"]["ChecksumStartAdr"]),
        "earnings_total": table_base + BLOCK_OFFSETS["TOTAL"],
        "earnings_recent_b": table_base + BLOCK_OFFSETS["RECENT-B"],
        "earnings_recent_c": table_base + BLOCK_OFFSETS["RECENT-C"],
        "games_started": table_base + GAMES_STARTED_OFFSET,
        "minutes_on": table_base + MINUTES_ON_OFFSET,
        "credits_on_machine": gc_ck + 8,
        "pending_coin_units": gc_ck + 9,
    }


def decode_block(buf, base, coin_cents):
    """One 6-slot earnings block -> named values plus derived cents."""
    d = {name: val(buf, base + i * RECORD_SIZE) for i, name in enumerate(SLOTS)}
    d["_base"] = base
    d["checks_ok"] = all(check_ok(buf, base + i * RECORD_SIZE) for i in range(len(SLOTS)))
    d["derived_earnings_cents"] = sum(d[SLOTS[i]] * coin_cents[i] for i in range(4))
    return d


def find_blocks(buf, wanted, table_base, adj_ck_start):
    """Every 6-byte-grid position whose 6 slot values match `wanted`."""
    out = []
    for base in range(table_base, adj_ck_start - 36, RECORD_SIZE):
        if [val(buf, base + i * RECORD_SIZE) for i in range(6)] == list(wanted):
            out.append(base)
    return out


def load(path):
    buf = [int(x) for x in open(path).read().split()]
    if len(buf) != 8192:
        raise SystemExit(f"{path}: expected 8192 bytes, got {len(buf)}")
    return buf


def main():
    args = sys.argv[1:]
    if "--config" not in args:
        raise SystemExit(__doc__)
    ci = args.index("--config")
    cfg = json.load(open(args[ci + 1]))
    del args[ci:ci + 2]
    table_base = DEFAULT_TABLE_BASE
    if "--table-base" in args:
        ti = args.index("--table-base")
        table_base = int(args[ti + 1], 0)
        del args[ti:ti + 2]
    anchors = anchors_from_config(cfg, table_base)
    coin = cfg.get("Audits", {}).get("CoinValueCents", DEFAULT_COIN_CENTS)
    name = cfg.get("GameInfo", {}).get("GameName", "?")

    print(f"game: {name}   predicted anchors:")
    for k, v in anchors.items():
        print(f"  {k:20} {hex(v)} ({v})")

    if args and args[0] == "--find":
        buf = load(args[1])
        wanted = [int(x) for x in args[2:]]
        if len(wanted) != 6:
            raise SystemExit(f"--find needs 6 slot values ({', '.join(SLOTS)})")
        hits = find_blocks(buf, wanted, table_base, anchors["adj_ck_start"])
        for base in hits:
            known = {v: k for k, v in BLOCK_OFFSETS.items()}.get(base - table_base)
            print(f"  match @ {hex(base)}" + (f"  == {known} (predicted position)" if known else "  <-- NEW position"))
        if not hits:
            print("  no match on the 6-byte grid -- layout differs, or a value was mistyped")
        return

    snaps = [(p.rsplit("/", 1)[-1].rsplit(".", 1)[0], load(p)) for p in args]
    if not snaps:
        raise SystemExit(__doc__)

    print("\nEARNINGS AUDITS")
    for tag in BLOCK_OFFSETS:
        base = table_base + BLOCK_OFFSETS[tag]
        print(f"  [{tag} @ {hex(base)}]")
        for sname, buf in snaps:
            b = decode_block(buf, base, coin)
            flag = "" if b["checks_ok"] else "  ** CHECK FAIL **"
            cells = "  ".join(f"{s}={b[s]}" for s in SLOTS)
            print(f"    {sname:16} ${b['derived_earnings_cents'] / 100:>7.2f}   {cells}{flag}")

    print("\nSTANDARD AUDITS")
    for label, addr in (("games started", anchors["games_started"]), ("minutes on", anchors["minutes_on"])):
        vals = "  ".join(f"{n}={val(b, addr)}" for n, b in snaps)
        ok = all(check_ok(b, addr) for _, b in snaps)
        print(f"  {label:16} @{hex(addr)}  {vals}" + ("" if ok else "  ** CHECK FAIL **"))

    print("\nCREDIT STATE (live, not audits)")
    for sname, buf in snaps:
        print(f"  {sname:16} credits_on_machine={buf[anchors['credits_on_machine']]:<4} "
              f"pending_coin_units={buf[anchors['pending_coin_units']]}")


if __name__ == "__main__":
    main()
