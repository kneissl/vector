#!/usr/bin/env python3
"""
Decode WPC operator audit counters from Vector /api/memory-snapshot dumps,
driven by a per-game Vector config file.

Anchor model (verified on The Shadow LX-5 and Fish Tales L-5):
  - audit table base per game, Audits.TableBaseAdr (0x1813 Shadow, 0x1811 Fish
    Tales); --table-base still overrides while mapping an unknown title
      TOTAL earnings block   base+6     (6 slots x 6 bytes)
      RECENT-B block         base+42
      RECENT-C block         base+78
      games started          base+114
      minutes on             base+174
  - live credit state from config: HighScores.GCChecksumResultAdr+8 / +9
  - counter record: 3-byte value + check byte = 0xFF - (sum & 0xFF)

The block offsets above hold on both titles. The *encoding* does not, so it
travels in the config too:

  Audits.Endian           "little" (Shadow) or "big" (Fish Tales)
  Audits.CheckByteOffset  3 (Shadow) or 5 (Fish Tales)

Both default to the Shadow's values, so a config written before this was known
decodes exactly as it did. Decoding a big-endian game with little-endian rules
does not fail loudly -- it yields plausible-looking numbers that quietly fail
their check bytes, so always confirm a new title against the operator menu.

Usage:
  # capture (repeat per label: baseline, leftCoin, rightCoin, serviceCredit, gameStart)
  curl -s http://<vector-ip>/api/memory-snapshot | paste -sd' ' - > baseline.txt

  # full report across snapshots
  python3 tools/decode_wpc_audits.py --config src/wpc/config/FishTales_L5.json \
      baseline.txt leftCoin.txt rightCoin.txt serviceCredit.txt gameStart.txt

  # hunt for the earnings block on an unverified game: pass the six values
  # read off the operator menu (left center right 4th paid service). If nothing
  # matches, --find reports whether the other endianness would have.
  python3 tools/decode_wpc_audits.py --config src/wpc/config/FishTales_L5.json \
      --find baseline.txt 2 4 0 0 6 3

  # while mapping an unmapped title, override what the config claims
  python3 tools/decode_wpc_audits.py --config src/wpc/config/Whatever_L1.json \
      --table-base 0x1811 --endian big --check-offset 5 baseline.txt
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


def val(buf, a, big=False):
    """3-byte counter, little-endian unless the game stores it big-endian."""
    if big:
        return (buf[a] << 16) | (buf[a + 1] << 8) | buf[a + 2]
    return buf[a] | (buf[a + 1] << 8) | (buf[a + 2] << 16)


def check_ok(buf, a, ck_off=3):
    """Check byte is 0xFF minus the sum of the three value bytes. Its position
    within the record is per-game (+3 on the Shadow, +5 on Fish Tales)."""
    return buf[a + ck_off] == (0xFF - ((buf[a] + buf[a + 1] + buf[a + 2]) & 0xFF)) & 0xFF


def fmt_from_config(cfg):
    """(big_endian, check_byte_offset) for a game -- Shadow rules by default."""
    audits = cfg.get("Audits", {})
    return audits.get("Endian", "little") == "big", audits.get("CheckByteOffset", 3)


def anchors_from_config(cfg, table_base=None):
    """Predict every audit address for a game from its Vector config."""
    if table_base is None:
        table_base = int(cfg.get("Audits", {}).get("TableBaseAdr", DEFAULT_TABLE_BASE))
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


def decode_block(buf, base, coin_cents, big=False, ck_off=3):
    """One 6-slot earnings block -> named values plus derived cents."""
    d = {name: val(buf, base + i * RECORD_SIZE, big) for i, name in enumerate(SLOTS)}
    d["_base"] = base
    d["checks_ok"] = all(check_ok(buf, base + i * RECORD_SIZE, ck_off) for i in range(len(SLOTS)))
    d["derived_earnings_cents"] = sum(d[SLOTS[i]] * coin_cents[i] for i in range(4))
    return d


def find_blocks(buf, wanted, table_base, adj_ck_start, big=False):
    """Every 6-byte-grid position whose 6 slot values match `wanted`.

    On an unmapped title try both endiannesses -- a block that matches under
    one and not the other is itself the answer to which the game uses."""
    out = []
    for base in range(table_base, adj_ck_start - 36, RECORD_SIZE):
        if [val(buf, base + i * RECORD_SIZE, big) for i in range(6)] == list(wanted):
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
    table_base = None
    if "--table-base" in args:
        ti = args.index("--table-base")
        table_base = int(args[ti + 1], 0)
        del args[ti:ti + 2]
    big, ck_off = fmt_from_config(cfg)
    if "--endian" in args:                      # override while mapping a new title
        ei = args.index("--endian")
        big = args[ei + 1] == "big"
        del args[ei:ei + 2]
    if "--check-offset" in args:
        ki = args.index("--check-offset")
        ck_off = int(args[ki + 1], 0)
        del args[ki:ki + 2]
    anchors = anchors_from_config(cfg, table_base)
    table_base = anchors["table_base"]
    coin = cfg.get("Audits", {}).get("CoinValueCents", DEFAULT_COIN_CENTS)
    name = cfg.get("GameInfo", {}).get("GameName", "?")

    print(f"game: {name}   counters: {'big' if big else 'little'}-endian, "
          f"check byte at +{ck_off}")
    print("predicted anchors:")
    for k, v in anchors.items():
        print(f"  {k:20} {hex(v)} ({v})")

    if args and args[0] == "--find":
        buf = load(args[1])
        wanted = [int(x) for x in args[2:]]
        if len(wanted) != 6:
            raise SystemExit(f"--find needs 6 slot values ({', '.join(SLOTS)})")
        hits = find_blocks(buf, wanted, table_base, anchors["adj_ck_start"], big)
        for base in hits:
            known = {v: k for k, v in BLOCK_OFFSETS.items()}.get(base - table_base)
            print(f"  match @ {hex(base)}" + (f"  == {known} (predicted position)" if known else "  <-- NEW position"))
        if not hits:
            other = find_blocks(buf, wanted, table_base, anchors["adj_ck_start"], not big)
            if other:
                print(f"  no match as {'big' if big else 'little'}-endian, but "
                      f"{[hex(h) for h in other]} match as "
                      f"{'little' if big else 'big'}-endian -- set Audits.Endian accordingly")
            else:
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
            b = decode_block(buf, base, coin, big, ck_off)
            flag = "" if b["checks_ok"] else "  ** CHECK FAIL **"
            cells = "  ".join(f"{s}={b[s]}" for s in SLOTS)
            print(f"    {sname:16} ${b['derived_earnings_cents'] / 100:>7.2f}   {cells}{flag}")

    print("\nSTANDARD AUDITS")
    for label, addr in (("games started", anchors["games_started"]), ("minutes on", anchors["minutes_on"])):
        vals = "  ".join(f"{n}={val(b, addr, big)}" for n, b in snaps)
        ok = all(check_ok(b, addr, ck_off) for _, b in snaps)
        print(f"  {label:16} @{hex(addr)}  {vals}" + ("" if ok else "  ** CHECK FAIL **"))

    print("\nCREDIT STATE (live, not audits)")
    for sname, buf in snaps:
        print(f"  {sname:16} credits_on_machine={buf[anchors['credits_on_machine']]:<4} "
              f"pending_coin_units={buf[anchors['pending_coin_units']]}")


if __name__ == "__main__":
    main()
