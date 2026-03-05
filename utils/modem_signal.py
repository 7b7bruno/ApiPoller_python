#!/usr/bin/env python3

"""
Display 4G/5G modem signal strength using vertical bars and show operator + access mode.
Example usage:
python3 modem_signal.py
"""

import sys
import os
from pathlib import Path

# Add parent directory to path so we can import from classes
sys.path.insert(0, str(Path(__file__).parent.parent))

from argparse import ArgumentParser
from classes.quectel_modem_reader import QuectelModemReader
import re

SIGNAL_LEVELS = {
    0: "     ",
    1: "▂    ",
    2: "▂▃   ",
    3: "▂▃▄  ",
    4: "▂▃▄▅ ",
    5: "▂▃▄▅▇"
}

def parse_dbm(value):
    if value is None:
        return None
    match = re.search(r'-?\d+', str(value))
    return int(match.group()) if match else None

def get_signal_level(rsrp: int) -> int:
    if rsrp is None:
        return 0
    if rsrp >= -80:
        return 5
    elif rsrp >= -90:
        return 4
    elif rsrp >= -100:
        return 3
    elif rsrp >= -110:
        return 2
    elif rsrp >= -120:
        return 1
    else:
        return 0

def generate_signal_bars(level: int, total: int = 5) -> str:
    """
    Create a vertical bar chart (like a phone signal icon).
    Each bar grows higher from left to right.
    """
    result = []
    for row in range(total, 0, -1):
        line = ""
        for col in range(1, total + 1):
            if col <= level and row <= col:
                line += "▇ "
            else:
                line += "  "
        result.append(line.rstrip())
    return "\n".join(result)

def main():

    with QuectelModemReader("/dev/ttyUSB2") as reader:
        # Get signal and network info
        data = reader.get_signal_data()

        rsrp = data['rsrp']
        rsrq = data['rsrq']
        sinr = data['sinr']
        operator_name = data['operator_name']
        readable_mode = data['network_mode']

        level = get_signal_level(rsrp)
        bars = SIGNAL_LEVELS[level]

        # Map internal mode codes (if any) to readable labels
        mode_map = {
           "LTE": "4G",
           "WCDMA": "3G",
           "GSM": "2G",
           "NR5G NSA": "5G",
           "NR5G SA": "5G",
           "NR5G-NSA": "5G",
           "NR5G-SA": "5G"
        }
        display_mode = mode_map.get(readable_mode, readable_mode)

        # Display
        print(f"{operator_name} {display_mode} {bars}")
        print(f"  RSRP: {rsrp} dBm" if rsrp is not None else "  RSRP: N/A")
        if rsrq is not None:
            print(f"  RSRQ: {rsrq} dB")
        if sinr is not None:
            print(f"  SINR: {sinr} dB")

if __name__ == "__main__":
    main()