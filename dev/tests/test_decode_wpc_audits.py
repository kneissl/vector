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
    """Fish Tales sits on its own table base, carried in its Audits block."""
    cfg = json.load(open(REPO / "src" / "wpc" / "config" / "FishTales_L5.json"))
    a = dw.anchors_from_config(cfg)
    assert a["credits_on_machine"] == 7307 + 8      # GCChecksumResultAdr + 8
    assert a["pending_coin_units"] == 7307 + 9
    assert a["adj_ck_start"] == 6941                # Adjustments.ChecksumStartAdr
    # base 0x1811, two lower than the Shadow's -- confirmed against the menu
    assert a["table_base"] == 0x1811
    assert a["earnings_total"] == 6167
    assert a["games_started"] == 6275
    assert a["minutes_on"] == 6335


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


# --- per-game counter format -------------------------------------------------
#
# The Shadow stores counters little-endian with the check byte at +3; Fish Tales
# stores them big-endian with the check byte at +5. Same 6-byte grid, same
# 0xFF-minus-sum checksum. Format therefore travels in the config, not in code.

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Read off the Fish Tales operator menu on 2026-09-02, same session as the dump.
FT_MENU = {
    "games started": (6275, 2381),
    "total plays": (6281, 2361),
    "replay awards": (6293, 203),
    "extra balls": (6323, 243),
    "left flipper": (6653, 116444),
    "right flipper": (6659, 111767),
    "recent left slot": (6203, 3237),
    "recent right slot": (6215, 4206),
    "recent paid credits": (6227, 1852),
    "recent service credits": (6233, 120),
}


def put_counter_be(buf, addr, value, ck_off=5):
    buf[addr] = (value >> 16) & 0xFF
    buf[addr + 1] = (value >> 8) & 0xFF
    buf[addr + 2] = value & 0xFF
    buf[addr + ck_off] = (0xFF - ((buf[addr] + buf[addr + 1] + buf[addr + 2]) & 0xFF)) & 0xFF


def test_big_endian_value_and_shifted_check_byte():
    buf = make_buf()
    put_counter_be(buf, 100, 0x030201)
    assert dw.val(buf, 100, big=True) == 0x030201
    assert dw.check_ok(buf, 100, ck_off=5)
    # the little-endian reading of the same bytes is a different number
    assert dw.val(buf, 100) == 0x010203
    buf[105] ^= 0xFF
    assert not dw.check_ok(buf, 100, ck_off=5)


def test_fmt_from_config_defaults_to_shadow_rules():
    """Configs written before the format was known keep working unchanged."""
    assert dw.fmt_from_config({}) == (False, 3)
    shadow = json.load(open(REPO / "src" / "wpc" / "config" / "TheShadow_LX5.json"))
    assert dw.fmt_from_config(shadow) == (False, 3)


def test_fmt_from_config_fishtales_is_big_endian():
    cfg = json.load(open(REPO / "src" / "wpc" / "config" / "FishTales_L5.json"))
    assert dw.fmt_from_config(cfg) == (True, 5)


def test_real_fishtales_dump_matches_the_operator_menu():
    """The whole point: config + decoder reproduce what the menu showed."""
    cfg = json.load(open(REPO / "src" / "wpc" / "config" / "FishTales_L5.json"))
    big, ck_off = dw.fmt_from_config(cfg)
    buf = dw.load(str(FIXTURES / "fishtales_L5_baseline.txt"))
    for label, (addr, expected) in FT_MENU.items():
        assert dw.val(buf, addr, big=big) == expected, label
        assert dw.check_ok(buf, addr, ck_off=ck_off), label


def test_real_fishtales_dump_derives_menu_earnings():
    """Recent earnings is computed from the coin slots: menu read $1,860.75."""
    cfg = json.load(open(REPO / "src" / "wpc" / "config" / "FishTales_L5.json"))
    big, ck_off = dw.fmt_from_config(cfg)
    buf = dw.load(str(FIXTURES / "fishtales_L5_baseline.txt"))
    block = dw.decode_block(buf, 6203, [25, 25, 25, 25], big=big, ck_off=ck_off)
    assert block["checks_ok"]
    assert block["derived_earnings_cents"] == 186075


def test_shadow_rules_misread_the_fishtales_dump():
    """Guard the regression that started all this: little-endian on a
    big-endian game yields garbage that fails its own check bytes."""
    buf = dw.load(str(FIXTURES / "fishtales_L5_baseline.txt"))
    assert dw.val(buf, 6275) != 2381
    assert not dw.check_ok(buf, 6275)


def test_two_coin_dump_moves_the_expected_slots():
    """One left-slot and one right-slot coin, captured on the machine."""
    cfg = json.load(open(REPO / "src" / "wpc" / "config" / "FishTales_L5.json"))
    big, ck_off = dw.fmt_from_config(cfg)
    a = dw.load(str(FIXTURES / "fishtales_L5_baseline.txt"))
    b = dw.load(str(FIXTURES / "fishtales_L5_two_coins.txt"))
    for base in (6167, 6203, 6239):        # TOTAL, RECENT-B, RECENT-C
        before = dw.decode_block(a, base, [25, 25, 25, 25], big=big, ck_off=ck_off)
        after = dw.decode_block(b, base, [25, 25, 25, 25], big=big, ck_off=ck_off)
        assert after["left slot"] - before["left slot"] == 1, base
        assert after["right slot"] - before["right slot"] == 1, base
        assert after["center slot"] == before["center slot"], base
        assert after["checks_ok"]
    # 4 quarters to the credit on this machine, so two coins are still pending
    assert b[cfg["Audits"]["CreditsOnMachineAdr"]] == 0
    assert b[cfg["Audits"]["PendingCoinUnitsAdr"]] == 2
