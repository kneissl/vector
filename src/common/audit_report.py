# This file is part of the Warped Pinball SYS11Wifi Project.
# https://creativecommons.org/licenses/by-nc/4.0/
# This work is licensed under CC BY-NC 4.0
"""
Audit phone-home: decode WPC operator audits from shadow RAM and post them to
the PinBooks ingest gateway.

Pure logic (decode, payload, gating) lives at module top so desktop pytest can
exercise it; everything that touches hardware, wifi or the network is imported
lazily inside audit_tick()/_post(). Timestamps are deliberately absent from the
payload -- the server stamps observation time, so this module has no clock,
timezone or NTP dependency.

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

_SLOT_COUNT = 6  # left, center, right, 4th, paid credits, service credits

MIN_POST_INTERVAL_MS = 60 * 60 * 1000       # never post more than hourly
HEARTBEAT_MS = 24 * 60 * 60 * 1000          # post unchanged state daily
IDLE_MS = 5 * 60 * 1000                     # machine quiet this long before TLS
_NEVER = 1 << 40                            # "elapsed" stand-in for never-happened


def _val3(mem, a):
    return mem[a] | (mem[a + 1] << 8) | (mem[a + 2] << 16)


def _check_ok(mem, a):
    return mem[a + 3] == (0xFF - ((mem[a] + mem[a + 1] + mem[a + 2]) & 0xFF)) & 0xFF


def decode_audits(mem, cfg):
    """Decode the audit counters, or None if any check byte fails
    (a mid-write snapshot; skip this tick and try again later)."""
    base = cfg["EarningsTableAdr"]
    rec = cfg.get("RecordSize", 6)
    slots = []
    for i in range(_SLOT_COUNT):
        a = base + i * rec
        if not _check_ok(mem, a):
            return None
        slots.append(_val3(mem, a))
    for a in (cfg["GamesStartedAdr"], cfg["MinutesOnAdr"]):
        if not _check_ok(mem, a):
            return None
    coin = cfg.get("CoinValueCents", [25, 25, 25, 25])
    return {
        "total_earnings_minor": sum(slots[i] * coin[i] for i in range(4)),
        "total_paid_credits": slots[4],
        "total_service_credits": slots[5],
        "gamesplayed": _val3(mem, cfg["GamesStartedAdr"]),
        "minutes_on": _val3(mem, cfg["MinutesOnAdr"]),
        "credits_on_machine": mem[cfg["CreditsOnMachineAdr"]],
        "money_held_minor": mem[cfg["PendingCoinUnitsAdr"]] * coin[0],
    }


def state_crc(counters):
    """Cheap change detector over the decoded counters (order-independent)."""
    parts = []
    for k in sorted(counters):
        parts.append(k)
        parts.append(str(counters[k]))
    return crc32("|".join(parts).encode()) & 0xFFFFFFFF


def build_payload(counters, serial, software_version, collector_version):
    """The gateway's JJP-shaped body. No observed_at (server stamps time),
    no machine_id (identity comes from the bearer token)."""
    return {
        "game_serial": serial,
        "software_version": software_version,
        "collector_version": collector_version,
        "audit": {
            "total_earnings_minor": counters["total_earnings_minor"],
            "total_paid_credits": counters["total_paid_credits"],
            "total_service_credits": counters["total_service_credits"],
        },
        "context": {
            "gamesplayed": counters["gamesplayed"],
            "minuteson": counters["minutes_on"],
        },
        "balances": {
            "credits_on_machine": counters["credits_on_machine"],
            "money_held_minor": counters["money_held_minor"],
        },
    }


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
