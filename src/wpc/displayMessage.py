#  WPC




# This file is part of the Warped Pinball SYS11Wifi Project.
# https://creativecommons.org/licenses/by-nc/4.0/
# This work is licensed under CC BY-NC 4.0
"""
display message handling (custom message shown on the pinball machine display)

updated for system 9 - optionally shows IP address in high score display
"""

from Shadow_Ram_Definitions import shadowRam
import SharedState as S
import SPI_DataStore as DataStore
from logger import logger_instance
log = logger_instance

localCopyIp = 0
show_ip_last_state = False


def fixAdjustmentChecksum():
    pass
    return
  

def _adjustmentsChecksum():
    """return (calculated, stored, result address) for the game adjustments block, or None"""
    adj = S.gdata.get("Adjustments", {})
    if adj.get("Type") != 10:
        return None
    start = adj.get("ChecksumStartAdr", 0)
    end = adj.get("ChecksumEndAdr", 0)
    result = adj.get("ChecksumResultAdr", 0)
    if start == 0 or end == 0 or result == 0:
        return None
    chk = 0
    for adr in range(start, end + 1):
        chk = chk + shadowRam[adr]
    chk = (0xFFFF - chk) & 0xFFFF
    return chk, (shadowRam[result] << 8) | shadowRam[result + 1], result


def _setCustomMessageOn(value):
    """
    write the game's custom message on/off adjustment.

    The byte sits inside the checksummed adjustments block, so the block
    checksum has to follow it or the game sees corrupt adjustments at its next
    power up. Only a checksum that was valid before the write is updated - a
    block that is already invalid (or a game def with a wrong range) is left
    alone.
    """
    message_adr = S.gdata["Adjustments"].get("CustomMessageOn")
    if message_adr is None or shadowRam[message_adr] == value:
        return
    before = _adjustmentsChecksum()
    shadowRam[message_adr] = value
    if before is not None and before[0] == before[1]:
        chk, _, result = _adjustmentsChecksum()
        shadowRam[result] = (chk >> 8) & 0xFF
        shadowRam[result + 1] = chk & 0xFF
    else:
        log.log("MSG: adjustments checksum not valid, left unchanged")


def _fixDisplayMessageChecksum():
    if S.gdata["DisplayMessage"]["Type"] == 10: 
        chk = 0
        for adr in range(S.gdata["DisplayMessage"]["ChecksumStartAdr"], S.gdata["DisplayMessage"]["ChecksumEndAdr"] + 1):
            chk = chk + shadowRam[adr]
        chk =0xFFFF - chk
        # Store MSByte and LSByte
        msb = (chk >> 8) & 0xFF
        lsb = chk & 0xFF
        #print("SCORE: Checksum: ---------------- ", hex(chk), hex(msb), hex(lsb))
        shadowRam[S.gdata["DisplayMessage"]["ChecksumResultAdr"]] = msb
        shadowRam[S.gdata["DisplayMessage"]["ChecksumResultAdr"] + 1] = lsb


def _set(ipAddress):
    """
    put ip address values in shadow ram
    """
    if not isinstance(ipAddress, str):
        ipAddress = "000.000.000.000"

    print("MSG: set display message ", ipAddress)

    padding_total = 16 - len(ipAddress)
    left_padding = padding_total // 2
    right_padding = padding_total - left_padding        
    padded_ip = " " * left_padding + ipAddress + " " * right_padding

    if S.gdata["DisplayMessage"]["Type"] == 10:
        # 16 x 2 lines x 3 screens
        inp = "WARPED PINBALL  "

        if "AddressS1" in S.gdata["DisplayMessage"]:
            for i, char in enumerate(inp + padded_ip):
                shadowRam[S.gdata["DisplayMessage"]["AddressS1"] + i] = ord(char)

        if "AddressS2" in S.gdata["DisplayMessage"]:
            for i, char in enumerate(inp + padded_ip):
                shadowRam[S.gdata["DisplayMessage"]["AddressS2"] + i] = ord(char)

        if "AddressS3" in S.gdata["DisplayMessage"]:
            for i, char in enumerate(inp + padded_ip):
                shadowRam[S.gdata["DisplayMessage"]["AddressS3"] + i] = ord(char)

        print("MSG: set display message ", padded_ip)

        _setCustomMessageOn(1)

        _fixDisplayMessageChecksum()





def _blank():
    """
    clear the display message
    """
    if S.gdata["DisplayMessage"]["Type"] == 10:
        Length = S.gdata["DisplayMessage"].get("Length", 16)    
        # Write spaces (0x20) to each address location
        for i in range(Length):
            if "AddressS1" in S.gdata["DisplayMessage"]:
                shadowRam[S.gdata["DisplayMessage"]["AddressS1"] + i] = 0x20
                
            if "AddressS2" in S.gdata["DisplayMessage"]:
                shadowRam[S.gdata["DisplayMessage"]["AddressS2"] + i] = 0x20
                
            if "AddressS3" in S.gdata["DisplayMessage"]:
                shadowRam[S.gdata["DisplayMessage"]["AddressS3"] + i] = 0x20


def init(ipAddress):
    """
    call to set the ip address to be displayed at powerup
    """
    global localCopyIp, show_ip_last_state
    localCopyIp = ipAddress
    show_ip_last_state = DataStore.read_record("extras", 0)["show_ip_address"]
    log.log(f"MSG: init ip address {ipAddress}")


def refresh():
    """refresh IP address display
    call from schduler and any time config for "show ip address" changes
    """
    global localCopyIp, show_ip_last_state
    
    #just turned off 
    if show_ip_last_state==1 and DataStore.read_record("extras", 0)["show_ip_address"]==0:
        # turn off custom message                
        _blank()        
        _setCustomMessageOn(0)
        _fixDisplayMessageChecksum()
        print("MSG: turned off")
        
    #refresh message    
    if DataStore.read_record("extras", 0)["show_ip_address"] == 1:
        _blank()
        _set(localCopyIp)
        print("MSG: refreshed ", localCopyIp)
        
    show_ip_last_state = DataStore.read_record("extras", 0)["show_ip_address"]

