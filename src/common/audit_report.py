# This file is part of the Warped Pinball SYS11Wifi Project.
# https://creativecommons.org/licenses/by-nc/4.0/
# This work is licensed under CC BY-NC 4.0
"""
Audit phone-home: decode operator audits from shadow RAM and post them to
the PinBooks ingest gateway.

Pure logic (decode, payload, gating) lives at module top so desktop pytest can
exercise it; everything that touches hardware, wifi or the network is imported
lazily inside audit_tick()/_post(). Timestamps are deliberately absent from the
payload -- the server stamps observation time, so this module has no clock,
timezone or NTP dependency.

Counter byte order, check-byte position and digit encoding are per-game (see
_fmt). WPC stores 3-byte binary counters on a 6-byte grid; System 11 stores
3-byte BCD counters on a 4-byte grid with only three coin slots and no
service-credit or recent-earnings records (verified on Taxi against its
operator menu, snapshots/taxi/FINDINGS.md in the mapping workspace). The
0xFF-minus-sum check byte is common to every title seen so far.

Reference decoder with the same byte rules: tools/decode_wpc_audits.py.
"""
try:
    from ubinascii import crc32  # MicroPython
except ImportError:
    from binascii import crc32

try:
    import ujson as json
except ImportError:
    import json

CONFIG_PATH = "phone_home.json"

DEFAULT_INGEST_URL = "https://pinbooks-ingest.troy-e1a.workers.dev/v1/audits"

MIN_POST_INTERVAL_MS = 60 * 60 * 1000       # never post more than hourly
HEARTBEAT_MS = 24 * 60 * 60 * 1000          # post unchanged state daily
IDLE_MS = 2 * 60 * 1000                     # machine quiet this long before TLS
_NEVER = 1 << 40                            # "elapsed" stand-in for never-happened


def _bcd(b):
    return (b >> 4) * 10 + (b & 0x0F)


def _val3(mem, a, big=False, bcd=False):
    b = (mem[a], mem[a + 1], mem[a + 2]) if big else (mem[a + 2], mem[a + 1], mem[a])
    if bcd:
        return (_bcd(b[0]) * 100 + _bcd(b[1])) * 100 + _bcd(b[2])
    return (b[0] << 16) | (b[1] << 8) | b[2]


def _val1(mem, a, bcd=False):
    return _bcd(mem[a]) if bcd else mem[a]


def _check_ok(mem, a, ck_off=3):
    return mem[a + ck_off] == (0xFF - ((mem[a] + mem[a + 1] + mem[a + 2]) & 0xFF)) & 0xFF


def _fmt(cfg):
    """(big_endian, check_byte_offset, bcd) for this game.

    The 0xFF-minus-sum checksum is universal so far, but nothing else is: The
    Shadow is little-endian binary with the check at +3, Fish Tales big-endian
    binary with it at +5, Taxi (System 11) big-endian BCD with it at +3.
    Defaults reproduce the Shadow, so configs written before this was known are
    unaffected. Getting it wrong is silent -- the numbers look plausible and
    only the check bytes disagree (BCD vs binary not even that: both check
    bytes validate, only the decoded value differs past 9) -- so confirm a new
    title against its operator menu before trusting it."""
    return cfg.get("Endian", "little") == "big", cfg.get("CheckByteOffset", 3), cfg.get("Bcd", False)


def _opt_counter(mem, cfg, key, big=False, ck_off=3, bcd=False):
    """Optional play counter: None when the address is unconfigured (older
    game configs) or its check byte fails -- never a reason to veto a report."""
    a = cfg.get(key)
    if a is None or not _check_ok(mem, a, ck_off):
        return None
    return _val3(mem, a, big, bcd)


def decode_audits(mem, cfg):
    """Decode the audit counters, or None if any check byte fails
    (a mid-write snapshot; skip this tick and try again later)."""
    base = cfg["EarningsTableAdr"]
    rec = cfg.get("RecordSize", 6)
    big, ck_off, bcd = _fmt(cfg)
    # WPC: 4 coin slots, then paid, then service credits. System 11: 3 coin
    # chutes, then paid, and no service-credit record at all.
    n_coin = cfg.get("CoinSlotCount", 4)
    n_slots = n_coin + 1 + (1 if cfg.get("ServiceCredits", True) else 0)
    slots = []
    for i in range(n_slots):
        a = base + i * rec
        if not _check_ok(mem, a, ck_off):
            return None
        slots.append(_val3(mem, a, big, bcd))
    if not _check_ok(mem, cfg["GamesStartedAdr"], ck_off):
        return None
    # minutes-on is best-effort: The Shadow maintains its check byte out of
    # step with the counter after long uptimes (observed live: off by one),
    # and it is a diagnostic that never reaches a ledger column -- a bad check
    # byte here must not veto the money report.
    minutes = _val3(mem, cfg["MinutesOnAdr"], big, bcd) if _check_ok(mem, cfg["MinutesOnAdr"], ck_off) else None
    coin = cfg.get("CoinValueCents", [25, 25, 25, 25])

    # recent-earnings block: same slot layout as the main table, but
    # best-effort -- any bad check byte nulls both fields without vetoing.
    recent_minor = recent_paid = None
    rbase = cfg.get("RecentEarningsTableAdr")
    if rbase is not None:
        rslots = []
        for i in range(n_slots):
            a = rbase + i * rec
            if not _check_ok(mem, a, ck_off):
                rslots = None
                break
            rslots.append(_val3(mem, a, big, bcd))
        if rslots is not None:
            recent_minor = sum(rslots[i] * coin[i] for i in range(n_coin))
            recent_paid = rslots[n_coin]

    return {
        "total_earnings_minor": sum(slots[i] * coin[i] for i in range(n_coin)),
        "total_paid_credits": slots[n_coin],
        "total_service_credits": slots[n_coin + 1] if len(slots) > n_coin + 1 else None,
        "gamesplayed": _val3(mem, cfg["GamesStartedAdr"], big, bcd),
        "minutes_on": minutes,
        "credits_on_machine": _val1(mem, cfg["CreditsOnMachineAdr"], bcd),
        "money_held_minor": _val1(mem, cfg["PendingCoinUnitsAdr"], bcd) * coin[0],
        "total_plays": _opt_counter(mem, cfg, "TotalPlaysAdr", big, ck_off, bcd),
        "balls_played": _opt_counter(mem, cfg, "BallsPlayedAdr", big, ck_off, bcd),
        "free_plays": _opt_counter(mem, cfg, "FreePlaysAdr", big, ck_off, bcd),
        "left_flips": _opt_counter(mem, cfg, "LeftFlipperAdr", big, ck_off, bcd),
        "right_flips": _opt_counter(mem, cfg, "RightFlipperAdr", big, ck_off, bcd),
        "recent_earnings_minor": recent_minor,
        "recent_paid_credits": recent_paid,
    }


# Counters that move on their own while the machine merely sits powered on.
# They ride along in the payload but must not count as "the state changed",
# or the board would re-post every hour with nobody playing.
_CRC_EXCLUDE = ("minutes_on",)


def state_crc(counters):
    """Cheap change detector over the decoded counters (order-independent)."""
    parts = []
    for k in sorted(counters):
        if k in _CRC_EXCLUDE:
            continue
        parts.append(k)
        parts.append(str(counters[k]))
    return crc32("|".join(parts).encode()) & 0xFFFFFFFF


def build_payload(counters, serial, software_version, collector_version):
    """The gateway's JJP-shaped body. No observed_at (server stamps time),
    no machine_id (identity comes from the bearer token)."""
    payload = {
        "game_serial": serial,
        "software_version": software_version,
        "collector_version": collector_version,
        "audit": {
            "total_earnings_minor": counters["total_earnings_minor"],
            "total_paid_credits": counters["total_paid_credits"],
        },
        "context": {
            # JJP sends both: gamesplayed is the total-plays counter when
            # readable (falls back to games-started for older configs or a
            # bad check byte); gamestarts is always the games-started counter
            # (unvalidated by the gateway, lands in raw_payload only).
            "gamesplayed": counters["total_plays"] if counters["total_plays"] is not None else counters["gamesplayed"],
            "gamestarts": counters["gamesplayed"],
        },
        "balances": {
            "credits_on_machine": counters["credits_on_machine"],
            "money_held_minor": counters["money_held_minor"],
        },
    }
    # System 11 keeps no service-credit record; the gateway treats every
    # counter as optional, so absent beats a fabricated zero
    if counters["total_service_credits"] is not None:
        payload["audit"]["total_service_credits"] = counters["total_service_credits"]
    if counters["minutes_on"] is not None:
        payload["context"]["minuteson"] = counters["minutes_on"]
    if counters["balls_played"] is not None:
        payload["context"]["ballsplayed"] = counters["balls_played"]
    if counters["free_plays"] is not None:
        payload["context"]["freeplays"] = counters["free_plays"]
    # both-or-neither by choice: a lone flipper total would skew the relative
    # wear picture downstream, so send flips only when both sides read clean
    if counters["left_flips"] is not None and counters["right_flips"] is not None:
        payload["flips"] = {
            "left_total": counters["left_flips"],
            "right_total": counters["right_flips"],
        }
    if counters["recent_earnings_minor"] is not None:
        payload["audit"]["recent_earnings_minor"] = counters["recent_earnings_minor"]
    if counters["recent_paid_credits"] is not None:
        payload["audit"]["recent_paid_credits"] = counters["recent_paid_credits"]
    return payload


def should_post(changed, idle_ms, since_attempt_ms, since_ok_ms):
    """Post only from a quiet machine, at most hourly, on change or daily heartbeat."""
    if idle_ms < IDLE_MS:
        return False
    if since_attempt_ms < MIN_POST_INTERVAL_MS:
        return False
    return changed or since_ok_ms >= HEARTBEAT_MS


# ---- device configuration (flash file; FRAM config record is full) ----

def load_config():
    """{'url':..., 'token':..., 'serial':...} or None if unconfigured."""
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        f.write(json.dumps(cfg))


# runtime state, RAM-only: after a reboot one extra post happens and the
# gateway dedupes it (event_id is content-derived).
_state = {"last_attempt": None, "last_ok": None, "last_crc": None, "last_active": None, "last_result": None}


def status():
    cfg = load_config() or {}
    return {
        "configured": bool(cfg.get("token") and cfg.get("serial")),
        "url": cfg.get("url", ""),
        "default_url": DEFAULT_INGEST_URL,
        "serial": cfg.get("serial", ""),
        "token_set": bool(cfg.get("token")),
        "last_result": _state["last_result"],
    }


# ---- scheduled tick (runs in the cooperative loop; every import below is
# lazy so desktop pytest can import this module) ----

def _elapsed(now_ms, then_ms, ticks_diff):
    if then_ms is None:
        return _NEVER
    d = ticks_diff(now_ms, then_ms)
    # ticks_diff is only faithful for spans under half the wrap period
    # (~6 days on rp2); a negative result means "older than that", which
    # for every gate should read as very long ago, not as the future.
    return d if d >= 0 else _NEVER


def audit_tick():
    """Scheduled every 5 minutes. Decides whether this is a safe, useful moment
    to phone home, and does so. Any failure logs and waits for a later tick."""
    try:
        # cheap config gates first -- on a build/game without Audits support or
        # an unconfigured device this returns before touching hardware modules,
        # so the 5-minute tick stays a no-op instead of logging import errors
        import SharedState as S

        cfg = (getattr(S, "gdata", None) or {}).get("Audits")
        ph = load_config()
        if not cfg or not ph or not (ph.get("token") and ph.get("serial")):
            return
        url = ph.get("url") or DEFAULT_INGEST_URL

        import time

        import DataMapper
        from phew import is_connected_to_wifi

        now = time.ticks_ms()
        if DataMapper.get_ball_in_play() != 0:
            _state["last_active"] = now
            return
        if not is_connected_to_wifi():
            return

        from Shadow_Ram_Definitions import shadowRam

        counters = decode_audits(shadowRam, cfg)
        if counters is None:
            return  # mid-write snapshot; next tick

        crc = state_crc(counters)
        if not should_post(
            changed=(crc != _state["last_crc"]),
            idle_ms=_elapsed(now, _state["last_active"], time.ticks_diff),
            since_attempt_ms=_elapsed(now, _state["last_attempt"], time.ticks_diff),
            since_ok_ms=_elapsed(now, _state["last_ok"], time.ticks_diff),
        ):
            return

        _state["last_attempt"] = now
        if _post(url, ph, counters):
            _state["last_ok"] = now
            _state["last_crc"] = crc
    except Exception as e:
        print("AUDIT: tick error:", e)


def _post(url, ph, counters):
    """One synchronous HTTPS POST. Blocks the loop a few seconds -- acceptable
    because audit_tick only reaches here on a machine idle for 5+ minutes."""
    from logger import logger_instance

    try:
        import SharedState as S
        from mrequests.mrequests import post

        game_name = (getattr(S, "gdata", {}) or {}).get("GameInfo", {}).get("GameName", "")
        payload = build_payload(
            counters,
            ph["serial"],
            game_name,
            "vector-" + getattr(S, "VectorVersion", "0"),
        )
        r = post(
            url,
            headers={
                b"authorization": b"Bearer " + ph["token"].encode(),
                b"content-type": b"application/json",
            },
            data=json.dumps(payload).encode(),
            timeout=15,
        )
        ok = r.status_code in (200, 201)
        _state["last_result"] = "%d @earnings=%d" % (r.status_code, counters["total_earnings_minor"])
        r.close()
        if ok:
            logger_instance.log("AUDIT: posted, status %d" % r.status_code)
        else:
            logger_instance.log("AUDIT: gateway refused, status %d" % r.status_code)
        return ok
    except Exception as e:
        _state["last_result"] = "error: %s" % e
        logger_instance.log("AUDIT: post failed: %s" % e)
        return False
