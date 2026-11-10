"""WPC displayMessage: toggling the custom message keeps the adjustments checksum valid."""
import importlib
import sys
import types

import pytest

ADJ = {"Type": 10, "CustomMessageOn": 120, "ChecksumStartAdr": 100, "ChecksumEndAdr": 199, "ChecksumResultAdr": 200}
MSG = {"Type": 10, "AddressS1": 300, "Length": 32, "ChecksumStartAdr": 300, "ChecksumEndAdr": 340, "ChecksumResultAdr": 341}


def _valid(ram):
    chk = (0xFFFF - sum(ram[100:200])) & 0xFFFF
    return chk == (ram[200] << 8) | ram[201]


@pytest.fixture
def dm(monkeypatch):
    ram = bytearray(512)
    extras = {"show_ip_address": 1}
    stubs = {
        "Shadow_Ram_Definitions": {"shadowRam": ram},
        "SharedState": {"gdata": {"Adjustments": dict(ADJ), "DisplayMessage": dict(MSG)}},
        "SPI_DataStore": {"read_record": lambda name, idx: extras},
        "logger": {"logger_instance": types.SimpleNamespace(log=lambda *a: None)},
    }
    for name, attrs in stubs.items():
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.syspath_prepend("src/wpc")
    monkeypatch.delitem(sys.modules, "displayMessage", raising=False)
    module = importlib.import_module("displayMessage")
    yield module, ram, extras
    sys.modules.pop("displayMessage", None)


def _seal(ram):
    chk = (0xFFFF - sum(ram[100:200])) & 0xFFFF
    ram[200], ram[201] = chk >> 8, chk & 0xFF


def test_toggle_keeps_valid_checksum_valid(dm):
    module, ram, extras = dm
    ram[110] = 25
    _seal(ram)
    module.init("192.168.1.30")
    module.refresh()
    assert ram[120] == 1 and _valid(ram)
    extras["show_ip_address"] = 0
    module.refresh()
    assert ram[120] == 0 and _valid(ram)


def test_invalid_checksum_is_left_alone(dm):
    module, ram, extras = dm
    ram[200], ram[201] = 0x12, 0x34
    module.init("192.168.1.30")
    module.refresh()
    assert ram[120] == 1
    assert (ram[200], ram[201]) == (0x12, 0x34)
