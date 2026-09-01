import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import decode_wpc_audits as dw

TABLE_BASE = 0x1813


def make_buf():
    return [0] * 8192


def put_counter(buf, addr, value):
    buf[addr] = value & 0xFF
    buf[addr + 1] = (value >> 8) & 0xFF
    buf[addr + 2] = (value >> 16) & 0xFF
    buf[addr + 3] = (0xFF - ((buf[addr] + buf[addr + 1] + buf[addr + 2]) & 0xFF)) & 0xFF


def test_val3_and_check_ok():
    buf = make_buf()
    put_counter(buf, 100, 0x030201)
    assert dw.val(buf, 100) == 0x030201
    assert dw.check_ok(buf, 100)
    buf[103] ^= 0xFF
    assert not dw.check_ok(buf, 100)


def test_earnings_block_decode_and_derived_cents():
    buf = make_buf()
    base = TABLE_BASE + 6  # TOTAL block
    slots = [2, 4, 0, 0, 6, 3]  # left, center, right, 4th, paid, service
    for i, v in enumerate(slots):
        put_counter(buf, base + i * 6, v)
    block = dw.decode_block(buf, base, [25, 25, 25, 25])
    assert block["paid credits"] == 6
    assert block["service credits"] == 3
    assert block["derived_earnings_cents"] == (2 + 4) * 25


def test_anchors_from_config_fishtales():
    cfg = json.load(open(REPO / "src" / "wpc" / "config" / "FishTales_L5.json"))
    a = dw.anchors_from_config(cfg, table_base=TABLE_BASE)
    assert a["credits_on_machine"] == 7307 + 8      # GCChecksumResultAdr + 8
    assert a["pending_coin_units"] == 7307 + 9
    assert a["adj_ck_start"] == 6941                # Adjustments.ChecksumStartAdr
    assert a["earnings_total"] == TABLE_BASE + 6
    assert a["games_started"] == TABLE_BASE + 114
    assert a["minutes_on"] == TABLE_BASE + 174


def test_anchors_from_config_shadow_matches_verified_addresses():
    cfg = json.load(open(REPO / "src" / "wpc" / "config" / "TheShadow_LX5.json"))
    a = dw.anchors_from_config(cfg, table_base=TABLE_BASE)
    assert a["earnings_total"] == 0x1819
    assert a["games_started"] == 0x1885
    assert a["minutes_on"] == 0x18C1
    assert a["credits_on_machine"] == 0x1D50
    assert a["pending_coin_units"] == 0x1D51


def test_find_blocks_locates_planted_block():
    buf = make_buf()
    base = TABLE_BASE + 42  # RECENT-B position
    for i, v in enumerate([1, 2, 3, 4, 5, 6]):
        put_counter(buf, base + i * 6, v)
    hits = dw.find_blocks(buf, [1, 2, 3, 4, 5, 6], TABLE_BASE, 6941)
    assert hits == [base]
