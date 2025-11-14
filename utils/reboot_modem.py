#!/usr/bin/env python3
"""
Example code on how to reboot the modem:
python3 reboot.py http://admin:PASSWORD@192.168.8.1/
"""
from huawei_lte_api.Connection import Connection # type: ignore
from huawei_lte_api.Client import Client # type: ignore
from huawei_lte_api.enums.client import ResponseEnum # type: ignore
from huawei_lte_api.exceptions import ResponseErrorException # type: ignore
import time

URL = 'http://192.168.8.1'

print("This util reboots the Huawei modem and waits until it comes online.")

with Connection(URL) as connection:
    client = Client(connection)
    print("Restarting modem...")
    client.device.reboot()
    print("Waiting for modem to restart...")
    time.sleep(20)
    print("Waiting for modem to come up...")
    while True:
        try:
            connection.reload()
            client.monitoring.status()
            print("Modem booted!")
        except ResponseErrorException as e:
            print("Modem not available")
            time.sleep(20)