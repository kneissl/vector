import sys
from pathlib import Path

SRC_COMMON = Path(__file__).resolve().parents[2] / "src" / "common"
sys.path.insert(0, str(SRC_COMMON))

import audit_report as ar

CFG = {
    "EarningsTableAdr": 6169,
    "GamesStartedAdr": 6277,
    "MinutesOnAdr": 6337,
    "CreditsOnMachineAdr": 7504,
    "PendingCoinUnitsAdr": 7505,
    "RecordSize": 6,
    "CoinValueCents": [25, 25, 25, 25],
    "TotalPlaysAdr": 6283,
    "BallsPlayedAdr": 6343,
    "LeftFlipperAdr": 6655,
    "RightFlipperAdr": 6661,
    "RecentEarningsTableAdr": 6205,
}

# a game whose config predates the new counters (e.g. Fish Tales)
OLD_CFG = {k: v for k, v in CFG.items() if k not in (
    "TotalPlaysAdr", "BallsPlayedAdr", "LeftFlipperAdr", "RightFlipperAdr", "RecentEarningsTableAdr")}


def make_mem():
    return bytearray(8192)


def put_counter(mem, addr, value):
    mem[addr] = value & 0xFF
    mem[addr + 1] = (value >> 8) & 0xFF
    mem[addr + 2] = (value >> 16) & 0xFF
    mem[addr + 3] = (0xFF - ((mem[addr] + mem[addr + 1] + mem[addr + 2]) & 0xFF)) & 0xFF


def populated_mem():
    mem = make_mem()
    for i, v in enumerate([2, 4, 0, 0, 6, 3]):  # left,center,right,4th,paid,service
        put_counter(mem, CFG["EarningsTableAdr"] + i * 6, v)
    put_counter(mem, CFG["GamesStartedAdr"], 17)
    put_counter(mem, CFG["MinutesOnAdr"], 1234)
    mem[CFG["CreditsOnMachineAdr"]] = 5
    mem[CFG["PendingCoinUnitsAdr"]] = 2
    put_counter(mem, CFG["TotalPlaysAdr"], 5)
    put_counter(mem, CFG["BallsPlayedAdr"], 16)
    put_counter(mem, CFG["LeftFlipperAdr"], 150)
    put_counter(mem, CFG["RightFlipperAdr"], 227)
    for i, v in enumerate([2, 0, 8, 0, 5, 8]):  # recent: left,center,right,4th,paid,service
        put_counter(mem, CFG["RecentEarningsTableAdr"] + i * 6, v)
    return mem


def test_decode_audits():
    c = ar.decode_audits(populated_mem(), CFG)
    assert c == {
        "total_earnings_minor": 150,     # (2+4) coins * 25c
        "total_paid_credits": 6,
        "total_service_credits": 3,
        "gamesplayed": 17,
        "minutes_on": 1234,
        "credits_on_machine": 5,
        "money_held_minor": 50,          # 2 pending units * 25c
        "total_plays": 5,
        "balls_played": 16,
        "left_flips": 150,
        "right_flips": 227,
        "recent_earnings_minor": 250,    # (2+8) coins * 25c
        "recent_paid_credits": 5,
    }


def test_decode_returns_none_on_bad_check_byte():
    mem = populated_mem()
    mem[CFG["EarningsTableAdr"] + 3] ^= 0xFF
    assert ar.decode_audits(mem, CFG) is None


def test_state_crc_stable_and_sensitive():
    a = ar.decode_audits(populated_mem(), CFG)
    b = dict(a)
    assert ar.state_crc(a) == ar.state_crc(b)
    b["total_paid_credits"] += 1
    assert ar.state_crc(a) != ar.state_crc(b)


def test_build_payload_shape():
    c = ar.decode_audits(populated_mem(), CFG)
    p = ar.build_payload(c, "12345", "The Shadow", "vector-1.11.30")
    assert p["game_serial"] == "12345"
    assert p["software_version"] == "The Shadow"
    assert p["collector_version"] == "vector-1.11.30"
    assert p["audit"]["total_earnings_minor"] == 150
    assert p["audit"]["total_paid_credits"] == 6
    assert p["audit"]["total_service_credits"] == 3
    assert p["context"]["gamesplayed"] == 5   # total plays, not games-started
    assert p["context"]["gamestarts"] == 17   # JJP sends both
    assert p["context"]["minuteson"] == 1234
    assert p["context"]["ballsplayed"] == 16
    assert p["flips"]["left_total"] == 150
    assert p["flips"]["right_total"] == 227
    assert p["audit"]["recent_earnings_minor"] == 250
    assert p["audit"]["recent_paid_credits"] == 5
    assert p["balances"]["credits_on_machine"] == 5
    assert p["balances"]["money_held_minor"] == 50
    assert "observed_at" not in p     # server stamps time
    assert "machine_id" not in p      # identity comes from the token


MIN = 60 * 1000
HOUR = 60 * MIN


def test_should_post_gating():
    # not idle long enough
    assert not ar.should_post(changed=True, idle_ms=4 * MIN, since_attempt_ms=2 * HOUR, since_ok_ms=2 * HOUR)
    # attempted too recently
    assert not ar.should_post(changed=True, idle_ms=10 * MIN, since_attempt_ms=30 * MIN, since_ok_ms=2 * HOUR)
    # changed and clear to go
    assert ar.should_post(changed=True, idle_ms=10 * MIN, since_attempt_ms=2 * HOUR, since_ok_ms=2 * HOUR)
    # unchanged: only the 24h heartbeat posts
    assert not ar.should_post(changed=False, idle_ms=10 * MIN, since_attempt_ms=2 * HOUR, since_ok_ms=23 * HOUR)
    assert ar.should_post(changed=False, idle_ms=10 * MIN, since_attempt_ms=2 * HOUR, since_ok_ms=25 * HOUR)


def test_default_ingest_url_constant():
    assert ar.DEFAULT_INGEST_URL.startswith("https://")
    assert ar.DEFAULT_INGEST_URL.endswith("/v1/audits")


def test_status_reports_stored_url_and_default(monkeypatch):
    # unconfigured device: stored url is empty, default is advertised separately
    monkeypatch.setattr(ar, "load_config", lambda: None)
    s = ar.status()
    assert s["url"] == ""
    assert s["default_url"] == ar.DEFAULT_INGEST_URL
    assert not s["configured"]

    # stored override is returned as-is, default still advertised
    monkeypatch.setattr(ar, "load_config", lambda: {"url": "https://example.test/v1/audits", "token": "t", "serial": "s"})
    s = ar.status()
    assert s["url"] == "https://example.test/v1/audits"
    assert s["default_url"] == ar.DEFAULT_INGEST_URL
    assert s["configured"]


def test_elapsed_clamps_wrapped_negative_diffs():
    # a machine dormant past the ticks_ms wrap window must read as
    # long-idle (heartbeat due), never as recently-active
    fake_diff = lambda now, then: now - then
    assert ar._elapsed(100, None, fake_diff) == ar._NEVER
    assert ar._elapsed(100, 40, fake_diff) == 60
    assert ar._elapsed(100, 200, fake_diff) == ar._NEVER  # wrapped/negative


def test_state_crc_ignores_minutes_on():
    # minutes_on ticks every powered-on minute; if it participated in the
    # change checksum the board would re-post hourly forever with no play
    a = ar.decode_audits(populated_mem(), CFG)
    b = dict(a)
    b["minutes_on"] += 60
    assert ar.state_crc(a) == ar.state_crc(b)


def test_bad_minutes_on_check_does_not_block_report():
    # The Shadow maintains the minutes-on check byte out of step with the
    # counter (observed live: value 211, check off by one after hours of
    # uptime). A diagnostic counter must not veto the money report.
    mem = populated_mem()
    mem[CFG["MinutesOnAdr"] + 3] ^= 0xFF
    c = ar.decode_audits(mem, CFG)
    assert c is not None
    assert c["minutes_on"] is None
    assert c["total_earnings_minor"] == 150  # money still strict and intact


def test_payload_omits_minuteson_when_unreadable():
    mem = populated_mem()
    mem[CFG["MinutesOnAdr"] + 3] ^= 0xFF
    c = ar.decode_audits(mem, CFG)
    p = ar.build_payload(c, "12345", "The Shadow", "vector-1.11.30")
    assert "minuteson" not in p["context"]
    assert p["context"]["gamesplayed"] == 5


def test_bad_total_plays_check_falls_back_to_games_started():
    # play counters are absent-tolerant: a bad check byte must not veto the
    # report, and gamesplayed falls back to the games-started counter
    mem = populated_mem()
    mem[CFG["TotalPlaysAdr"] + 3] ^= 0xFF
    c = ar.decode_audits(mem, CFG)
    assert c is not None
    assert c["total_plays"] is None
    assert c["total_earnings_minor"] == 150  # money still strict and intact
    p = ar.build_payload(c, "12345", "The Shadow", "vector-1.11.30")
    assert p["context"]["gamesplayed"] == 17  # games-started fallback
    assert p["context"]["gamestarts"] == 17
    # the rest of the new counters are unaffected
    assert p["context"]["ballsplayed"] == 16
    assert p["flips"]["left_total"] == 150


def test_old_config_without_new_keys_still_works():
    # protects Fish Tales and any game not yet mapped: decode yields Nones,
    # payload omits every new field, gamesplayed is games-started
    c = ar.decode_audits(populated_mem(), OLD_CFG)
    assert c is not None
    assert c["total_plays"] is None
    assert c["balls_played"] is None
    assert c["left_flips"] is None
    assert c["right_flips"] is None
    assert c["recent_earnings_minor"] is None
    assert c["recent_paid_credits"] is None
    # crc must hash the None values stably
    assert ar.state_crc(c) == ar.state_crc(dict(c))
    p = ar.build_payload(c, "12345", "Fish Tales", "vector-1.11.30")
    assert p["context"]["gamesplayed"] == 17
    assert p["context"]["gamestarts"] == 17
    assert "ballsplayed" not in p["context"]
    assert "flips" not in p
    assert "recent_earnings_minor" not in p["audit"]
    assert "recent_paid_credits" not in p["audit"]


def test_one_bad_flipper_check_drops_flips_object_entirely():
    mem = populated_mem()
    mem[CFG["RightFlipperAdr"] + 3] ^= 0xFF
    c = ar.decode_audits(mem, CFG)
    assert c is not None
    assert c["left_flips"] == 150
    assert c["right_flips"] is None
    p = ar.build_payload(c, "12345", "The Shadow", "vector-1.11.30")
    assert "flips" not in p  # both-or-neither: gateway validates the pair


def test_bad_recent_earnings_check_nulls_both_recent_fields():
    mem = populated_mem()
    # corrupt the check byte of recent slot 2
    mem[CFG["RecentEarningsTableAdr"] + 2 * 6 + 3] ^= 0xFF
    c = ar.decode_audits(mem, CFG)
    assert c is not None  # does not veto the report
    assert c["recent_earnings_minor"] is None
    assert c["recent_paid_credits"] is None
    p = ar.build_payload(c, "12345", "The Shadow", "vector-1.11.30")
    assert "recent_earnings_minor" not in p["audit"]
    assert "recent_paid_credits" not in p["audit"]
    assert p["audit"]["total_earnings_minor"] == 150  # main block untouched
