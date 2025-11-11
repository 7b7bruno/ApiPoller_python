#!/usr/bin/env python3

"""
HuaweiModemReader - A class for reading signal data and status from Huawei LTE/5G modems.

This class uses the huawei_lte_api library to connect to a Huawei modem and retrieve
signal strength metrics (RSRP, RSRQ, SINR), operator information, and network mode.

Example usage:
    from classes.huawei_modem_reader import HuaweiModemReader

    with HuaweiModemReader("http://192.168.8.1") as reader:
        data = reader.get_signal_data()
        print(f"RSRP: {data['rsrp']} dBm")
        print(f"Operator: {data['operator_name']}")
        print(f"Network: {data['network_mode']}")
"""

import re
from huawei_lte_api.Connection import Connection  # type: ignore
from huawei_lte_api.Client import Client  # type: ignore


class HuaweiModemReader:
    """
    A class for reading signal data and status from Huawei LTE/5G modems.

    Attributes:
        url (str): The modem's connection URL (e.g., "http://192.168.8.1")
        connection: Active connection object to the modem
        client: Client object for API calls
    """

    # Network type mapping from modem codes to readable names
    NETWORK_TYPE_MAP = {
        '0': 'No Service',
        '1': 'GSM', '2': 'GPRS', '3': 'EDGE',
        '4': 'WCDMA', '5': 'HSDPA', '6': 'HSUPA', '7': 'HSPA',
        '8': 'TDSCDMA', '9': 'HSPA+',
        '10': 'EVDO Rev.0', '11': 'EVDO Rev.A', '12': 'EVDO Rev.B',
        '13': '1xRTT', '14': 'UMB', '15': '1xEVDV', '16': '3xRTT',
        '17': 'HSPA+ 64QAM', '18': 'HSPA+ MIMO',
        '19': 'LTE', '41': 'LTE CA',
        '101': 'NR5G NSA', '102': 'NR5G SA'
    }

    def __init__(self, url: str = "http://192.168.8.1", timeout: int = 10):
        """
        Initialize the HuaweiModemReader.

        Args:
            url (str): The modem's base URL. Default is "http://192.168.8.1"
            timeout (int): Connection timeout in seconds. Default is 10
        """
        self.url = url
        self.timeout = timeout
        self.connection = None
        self.client = None

    def __enter__(self):
        """Context manager entry - establishes connection to the modem."""
        self.connection = Connection(self.url, timeout=self.timeout)
        self.connection.__enter__()
        self.client = Client(self.connection)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes connection to the modem."""
        if self.connection:
            self.connection.__exit__(exc_type, exc_val, exc_tb)
        return False

    @staticmethod
    def _parse_signal_value(value) -> int | None:
        """
        Parse signal value from API response.

        The API may return values with 'dBm' or 'dB' suffix or other text.
        This extracts the numeric integer value.

        Args:
            value: Raw value from API (may be string or int)

        Returns:
            Parsed integer value or None if parsing fails
        """
        if value is None:
            return None
        match = re.search(r'-?\d+', str(value))
        return int(match.group()) if match else None

    def _get_signal_info(self) -> dict:
        """
        Fetch raw signal information from the modem.

        Returns:
            dict: Raw signal data including rsrp, rsrq, sinr
        """
        if not self.client:
            raise RuntimeError("Client not connected. Use context manager (with statement).")
        return self.client.device.signal()

    def _get_status_info(self) -> dict:
        """
        Fetch monitoring status from the modem.

        Returns:
            dict: Status data including CurrentNetworkType
        """
        if not self.client:
            raise RuntimeError("Client not connected. Use context manager (with statement).")
        return self.client.monitoring.status()

    def _get_plmn_info(self) -> dict:
        """
        Fetch PLMN (operator) information from the modem.

        Returns:
            dict: PLMN data including operator names
        """
        if not self.client:
            raise RuntimeError("Client not connected. Use context manager (with statement).")
        return self.client.net.current_plmn()

    def get_signal_data(self) -> dict:
        """
        Get complete signal data and status from the modem.

        Returns:
            dict: Dictionary containing:
                - rsrp (int | None): Reference Signal Received Power in dBm
                - rsrq (int | None): Reference Signal Received Quality in dB
                - sinr (int | None): Signal to Interference plus Noise Ratio in dB
                - operator_name (str): Mobile operator name
                - network_mode (str): Current network mode (e.g., "LTE", "NR5G SA")

        Example:
            {
                'rsrp': -116,
                'rsrq': -14,
                'sinr': 0,
                'operator_name': 'Tele2',
                'network_mode': 'LTE'
            }
        """
        # Fetch all required data
        signal_info = self._get_signal_info()
        status_info = self._get_status_info()
        plmn_info = self._get_plmn_info()

        # Parse signal metrics (all as integers)
        rsrp = self._parse_signal_value(signal_info.get('rsrp'))
        rsrq = self._parse_signal_value(signal_info.get('rsrq'))
        sinr = self._parse_signal_value(signal_info.get('sinr'))

        # Get operator name (prefer FullName, fallback to ShortName)
        operator_name = (
            plmn_info.get('FullName') or
            plmn_info.get('ShortName') or
            "Unknown"
        )

        # Parse network mode
        network_type_raw = str(status_info.get('CurrentNetworkType', '0'))
        network_mode = self.NETWORK_TYPE_MAP.get(
            network_type_raw,
            f"Unknown ({network_type_raw})"
        )

        return {
            'rsrp': rsrp,
            'rsrq': rsrq,
            'sinr': sinr,
            'operator_name': operator_name,
            'network_mode': network_mode
        }


def main():
    """Example usage of HuaweiModemReader."""
    with HuaweiModemReader("http://192.168.8.1") as reader:
        data = reader.get_signal_data()

        print("Modem Signal Data:")
        print(f"  Operator: {data['operator_name']}")
        print(f"  Network Mode: {data['network_mode']}")
        print(f"  RSRP: {data['rsrp']} dBm" if data['rsrp'] is not None else "  RSRP: N/A")
        print(f"  RSRQ: {data['rsrq']} dB" if data['rsrq'] is not None else "  RSRQ: N/A")
        print(f"  SINR: {data['sinr']} dB" if data['sinr'] is not None else "  SINR: N/A")


if __name__ == "__main__":
    main()
