__version__ = "0.0.2"

# highly recommended to set a lowish garbage collection threshold
# to minimise memory fragmentation as we sometimes want to
# allocate relatively large blocks of ram.
import gc
import os

# phew! the Pico (or Python) HTTP Endpoint Wrangler
from . import logging

gc.threshold(50000)


# determine if remotely mounted or not, changes some behaviours like
# logging truncation
remote_mount = False
try:
    os.statvfs(".")  # causes exception if remotely mounted (mpremote/pyboard.py)
except Exception:
    remote_mount = True


def get_ip_address():
    import network

    try:
        return network.WLAN(network.STA_IF).ifconfig()[0]
    except Exception:
        return None


def is_connected_to_wifi():
    import network

    wlan = network.WLAN(network.STA_IF)
    # Require a real DHCP lease, not just L2 association: isconnected() can stay
    # True with the IP dropped to 0.0.0.0 after a lease lapse / AP reboot.
    return wlan.isconnected() and wlan.ifconfig()[0] != "0.0.0.0"


def _gateway_reachable(gw, port=80, timeout_ms=1000):
    # TCP connect to the gateway. A successful connect OR a fast failure
    # (connection refused / reset) both prove the gateway answered at L3 -> link
    # is alive. Only a full-timeout (no answer at all) means the link is dead.
    # This is immune to the two failure modes a ping/ICMP probe suffers from:
    # APs that drop ICMP, and gateways with no open port.
    import time

    import usocket

    s = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
    s.settimeout(timeout_ms / 1000)
    start = time.ticks_ms()
    try:
        s.connect((gw, port))
        return True
    except OSError:
        return time.ticks_diff(time.ticks_ms(), start) < (timeout_ms - 150)
    finally:
        try:
            s.close()
        except Exception:
            pass


def wifi_link_alive():
    """True only if we can actually reach the gateway (real L3 liveness).

    Biased toward reporting DOWN when uncertain, because this gates reconnect:
    a false 'up' is what bricks us (recovery never runs), while a false 'down'
    just costs one unnecessary reconnect."""
    import network

    wlan = network.WLAN(network.STA_IF)
    if not wlan.isconnected():
        return False
    cfg = wlan.ifconfig()
    if cfg[0] == "0.0.0.0":  # associated but no DHCP lease
        return False
    gw = cfg[2]
    if not gw or gw == "0.0.0.0":
        return False
    try:
        return _gateway_reachable(gw)
    except Exception:
        return False  # treat probe error as link down -> reconnect


# helper method to quickly get connected to wifi
def connect_to_wifi(ssid, password, timeout_seconds=30):
    import time

    import network

    statuses = {
        network.STAT_IDLE: "idle",
        network.STAT_CONNECTING: "connecting",
        network.STAT_WRONG_PASSWORD: "wrong password",
        network.STAT_NO_AP_FOUND: "access point not found",
        network.STAT_CONNECT_FAIL: "connection failed",
        network.STAT_GOT_IP: "got ip address",
    }

    wlan = network.WLAN(network.STA_IF)
    # Tear the radio fully down before reconnecting. Re-issuing connect() on a
    # driver stuck half-associated (the state that makes isconnected() lie) is
    # commonly ignored by the firmware; the active(False)->active(True) cycle
    # forces it out of that state so the fresh connect() is honored. It also
    # tends to flush leaked lwIP state from the previous association.
    try:
        wlan.disconnect()
    except Exception:
        pass
    wlan.active(False)
    time.sleep(0.5)  # let the chip fully deinit
    wlan.active(True)
    wlan.config(pm=0xA11140)  # disable power save; dozing radio drops packets
    wlan.connect(ssid, password)
    start = time.ticks_ms()
    status = wlan.status()

    logging.debug(f"  - {statuses.get(status, 'unknown status')}")  # got '2' as status sometimes
    while not wlan.isconnected() and (time.ticks_ms() - start) < (timeout_seconds * 1000):
        new_status = wlan.status()
        if status != new_status:
            logging.debug(f"  - {statuses.get(new_status, 'unknown status')}")
            status = new_status
        time.sleep(0.25)

    mac = wlan.config("mac")
    mac_address = ":".join("{:02x}".format(b) for b in mac)
    print("Server:  MAC Address ", mac_address)

    if wlan.status() == network.STAT_GOT_IP:
        return wlan.ifconfig()[0]
    return None


# helper method to put the pico into access point mode
def access_point(ssid, password=None):
    import network

    # start up network in access point mode
    wlan = network.WLAN(network.AP_IF)
    wlan.config(essid=ssid)
    if password:
        wlan.config(password=password)
    else:
        wlan.config(security=0)  # disable password
    wlan.active(True)

    return wlan
