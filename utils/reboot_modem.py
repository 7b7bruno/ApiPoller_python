#!/usr/bin/env python3
"""
Example code on how to reboot the modem:
python3 reboot.py http://admin:PASSWORD@192.168.8.1/
"""
from huawei_lte_api.Connection import Connection # type: ignore
from huawei_lte_api.Client import Client # type: ignore
from huawei_lte_api.enums.client import ResponseEnum # type: ignore

URL = 'http://192.168.8.1'

with Connection(URL) as connection:
    client = Client(connection)
    if client.device.reboot() == ResponseEnum.OK.value:
        print('Reboot requested successfully')
    else:
        print('Error')