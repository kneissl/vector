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
}


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
    assert p["context"]["gamesplayed"] == 17
    assert p["context"]["minuteson"] == 1234
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
