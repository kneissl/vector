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
        "free_plays": None,
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
    assert not ar.should_post(changed=True, idle_ms=1 * MIN, since_attempt_ms=2 * HOUR, since_ok_ms=2 * HOUR)
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


# --- per-game counter format -------------------------------------------------
#
# The Shadow is little-endian with the check byte at +3; Fish Tales is
# big-endian with it at +5. decode_audits() must take both from the config and
# keep behaving exactly as before when a config says nothing.

import json

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

FT_CFG = json.load(open(REPO / "src" / "wpc" / "config" / "FishTales_L5.json"))["Audits"]


def load_dump(name):
    return bytearray(int(x) for x in open(FIXTURES / name).read().split())


def put_counter_be(mem, addr, value, ck_off=5):
    mem[addr] = (value >> 16) & 0xFF
    mem[addr + 1] = (value >> 8) & 0xFF
    mem[addr + 2] = value & 0xFF
    mem[addr + ck_off] = (0xFF - ((mem[addr] + mem[addr + 1] + mem[addr + 2]) & 0xFF)) & 0xFF


def test_val3_and_check_ok_honour_format_args():
    mem = make_mem()
    put_counter_be(mem, 100, 0x030201)
    assert ar._val3(mem, 100, big=True) == 0x030201
    assert ar._check_ok(mem, 100, ck_off=5)
    assert ar._val3(mem, 100) == 0x010203          # little-endian default unchanged
    assert not ar._check_ok(mem, 100)              # +3 default unchanged


def test_decode_audits_reads_a_big_endian_game():
    """Same fixture values as the Shadow tests, stored the Fish Tales way."""
    cfg = dict(CFG, Endian="big", CheckByteOffset=5)
    mem = make_mem()
    for i, v in enumerate([2, 4, 0, 0, 6, 3]):
        put_counter_be(mem, cfg["EarningsTableAdr"] + i * 6, v)
    put_counter_be(mem, cfg["GamesStartedAdr"], 17)
    put_counter_be(mem, cfg["MinutesOnAdr"], 1234)
    for i, v in enumerate([1, 1, 0, 0, 2, 1]):
        put_counter_be(mem, cfg["RecentEarningsTableAdr"] + i * 6, v)
    for key, v in (("TotalPlaysAdr", 15), ("BallsPlayedAdr", 44),
                   ("LeftFlipperAdr", 900), ("RightFlipperAdr", 901)):
        put_counter_be(mem, cfg[key], v)
    mem[cfg["CreditsOnMachineAdr"]] = 3
    mem[cfg["PendingCoinUnitsAdr"]] = 2

    got = ar.decode_audits(mem, cfg)
    assert got is not None
    assert got["total_earnings_minor"] == (2 + 4) * 25
    assert got["total_paid_credits"] == 6
    assert got["gamesplayed"] == 17
    assert got["minutes_on"] == 1234
    assert got["total_plays"] == 15
    assert got["left_flips"] == 900
    assert got["credits_on_machine"] == 3
    assert got["money_held_minor"] == 50


def test_decode_audits_rejects_a_mid_write_big_endian_snapshot():
    cfg = dict(CFG, Endian="big", CheckByteOffset=5)
    mem = make_mem()
    for i, v in enumerate([2, 4, 0, 0, 6, 3]):
        put_counter_be(mem, cfg["EarningsTableAdr"] + i * 6, v)
    put_counter_be(mem, cfg["GamesStartedAdr"], 17)
    mem[cfg["EarningsTableAdr"] + 5] ^= 0xFF       # corrupt one check byte
    assert ar.decode_audits(mem, cfg) is None


def test_decode_real_fishtales_dump_matches_the_operator_menu():
    """End to end: shipped config + real hardware dump == what the menu showed."""
    got = ar.decode_audits(load_dump("fishtales_L5_baseline.txt"), FT_CFG)
    assert got is not None
    assert got["gamesplayed"] == 2381                  # games started
    assert got["total_plays"] == 2361
    assert got["left_flips"] == 116444
    assert got["right_flips"] == 111767
    assert got["total_paid_credits"] == 1897
    assert got["total_service_credits"] == 120
    assert got["total_earnings_minor"] == 403700       # menu: $4,037.00
    assert got["recent_earnings_minor"] == 186075      # menu: $1,860.75
    assert got["recent_paid_credits"] == 1852
    assert got["balls_played"] is None                 # not mapped on this title


def test_real_fishtales_two_coin_dump_shows_money_held():
    """Fish Tales is $1.00/play, so two quarters sit pending, not credited."""
    got = ar.decode_audits(load_dump("fishtales_L5_two_coins.txt"), FT_CFG)
    assert got is not None
    assert got["credits_on_machine"] == 0
    assert got["money_held_minor"] == 50


def test_fishtales_payload_is_well_formed():
    counters = ar.decode_audits(load_dump("fishtales_L5_baseline.txt"), FT_CFG)
    payload = ar.build_payload(counters, "FT-0001", "L-5", "test")
    assert payload["audit"]["recent_earnings_minor"] == 186075
    assert payload["context"]["gamesplayed"] == 2361    # total plays wins
    assert payload["context"]["gamestarts"] == 2381
    assert payload["flips"] == {"left_total": 116444, "right_total": 111767}
    assert "ballsplayed" not in payload["context"]



# --- System 11 (Taxi): 4-byte BCD records, 3 coin chutes, no service slot ----

TAXI_CFG = json.load(open(REPO / "src" / "sys11" / "config" / "Taxi_L4.json"))["Audits"]


def put_counter_bcd(mem, addr, value, ck_off=3):
    """Big-endian BCD, two digits per byte, Sys11-style 4-byte record."""
    digits = f"{value:06d}"
    for i in range(3):
        mem[addr + i] = (int(digits[2 * i]) << 4) | int(digits[2 * i + 1])
    mem[addr + ck_off] = (0xFF - ((mem[addr] + mem[addr + 1] + mem[addr + 2]) & 0xFF)) & 0xFF


def test_val3_decodes_bcd():
    mem = bytearray(2048)
    put_counter_bcd(mem, 100, 104)
    assert mem[100:103] == bytes([0x00, 0x01, 0x04])   # not binary 104
    assert ar._val3(mem, 100, big=True, bcd=True) == 104
    assert ar._check_ok(mem, 100, ck_off=3)
    put_counter_bcd(mem, 100, 123456)
    assert ar._val3(mem, 100, big=True, bcd=True) == 123456


def test_decode_audits_reads_a_sys11_bcd_game():
    mem = bytearray(2048)
    for i, v in enumerate([12, 0, 104, 28]):           # left,center,right,paid
        put_counter_bcd(mem, TAXI_CFG["EarningsTableAdr"] + i * 4, v)
    put_counter_bcd(mem, TAXI_CFG["GamesStartedAdr"], 33)
    put_counter_bcd(mem, TAXI_CFG["FreePlaysAdr"], 8)
    put_counter_bcd(mem, TAXI_CFG["MinutesOnAdr"], 16)
    put_counter_bcd(mem, TAXI_CFG["BallsPlayedAdr"], 24)
    mem[TAXI_CFG["CreditsOnMachineAdr"]] = 0x12        # BCD 12 credits
    mem[TAXI_CFG["PendingCoinUnitsAdr"]] = 0x03

    got = ar.decode_audits(mem, TAXI_CFG)
    assert got is not None
    assert got["total_earnings_minor"] == (12 + 0 + 104) * 25
    assert got["total_paid_credits"] == 28
    assert got["total_service_credits"] is None        # no such record on Sys11
    assert got["gamesplayed"] == 33
    assert got["free_plays"] == 8
    assert got["minutes_on"] == 16
    assert got["balls_played"] == 24
    assert got["credits_on_machine"] == 12             # BCD byte, not 0x12=18
    assert got["money_held_minor"] == 75
    assert got["recent_earnings_minor"] is None        # no recent table on Sys11


def test_sys11_mid_write_snapshot_still_vetoes():
    mem = bytearray(2048)
    for i, v in enumerate([12, 0, 104, 28]):
        put_counter_bcd(mem, TAXI_CFG["EarningsTableAdr"] + i * 4, v)
    put_counter_bcd(mem, TAXI_CFG["GamesStartedAdr"], 33)
    mem[TAXI_CFG["EarningsTableAdr"] + 3] ^= 0xFF
    assert ar.decode_audits(mem, TAXI_CFG) is None


def test_sys11_payload_omits_service_credits_and_sends_freeplays():
    mem = bytearray(2048)
    for i, v in enumerate([12, 0, 104, 28]):
        put_counter_bcd(mem, TAXI_CFG["EarningsTableAdr"] + i * 4, v)
    put_counter_bcd(mem, TAXI_CFG["GamesStartedAdr"], 33)
    put_counter_bcd(mem, TAXI_CFG["FreePlaysAdr"], 8)
    put_counter_bcd(mem, TAXI_CFG["MinutesOnAdr"], 16)
    put_counter_bcd(mem, TAXI_CFG["BallsPlayedAdr"], 24)
    c = ar.decode_audits(mem, TAXI_CFG)
    p = ar.build_payload(c, "TAXI-0001", "Taxi", "vector-test")
    assert "total_service_credits" not in p["audit"]
    assert p["context"]["freeplays"] == 8
    assert p["context"]["gamesplayed"] == 33
    assert p["context"]["gamestarts"] == 33
    assert "flips" not in p


def test_decode_real_taxi_dump_matches_the_operator_menu():
    """Shipped config + real hardware dump == the operator menu + known events:
    AU01=2, AU03 run to 104, minutes 16, balls 24, free 8, 2 credits on
    the machine, nothing pending."""
    got = ar.decode_audits(load_dump("taxi_L4_rightcoin104.txt"), TAXI_CFG)
    assert got is not None
    assert got["total_earnings_minor"] == (2 + 0 + 104) * 25
    assert got["total_paid_credits"] == 28
    assert got["total_service_credits"] is None
    assert got["gamesplayed"] == 33
    assert got["free_plays"] == 8
    assert got["minutes_on"] == 16
    assert got["balls_played"] == 24
    assert got["credits_on_machine"] == 2
    assert got["money_held_minor"] == 0


def test_wpc_payload_still_carries_service_credits():
    c = ar.decode_audits(populated_mem(), CFG)
    p = ar.build_payload(c, "12345", "The Shadow", "vector-1.11.30")
    assert p["audit"]["total_service_credits"] == 3
