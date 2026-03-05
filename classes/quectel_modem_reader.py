#!/usr/bin/env python3

"""
QuectelModemReader - A class for reading signal data and status from Quectel LTE/5G modems.

This class uses pyserial to connect to a Quectel modem via serial interface and retrieve
signal strength metrics (RSRP, RSRQ, SINR), operator information, and network mode using
AT commands.

Example usage:
    from classes.quectel_modem_reader import QuectelModemReader

    with QuectelModemReader("/dev/ttyUSB2") as reader:
        data = reader.get_signal_data()
        print(f"RSRP: {data['rsrp']} dBm")
        print(f"Operator: {data['operator_name']}")
        print(f"Network: {data['network_mode']}")
"""

import re
import time
import serial  # type: ignore


class QuectelModemReader:
    """
    A class for reading signal data and status from Quectel LTE/5G modems.

    Attributes:
        port (str): The serial port device path (e.g., "/dev/ttyUSB2")
        baudrate (int): Serial connection baud rate
        timeout (int): Read timeout in seconds
        serial_conn: Active serial connection object to the modem
    """

    # Network type mapping from AT+QNWINFO responses
    NETWORK_TYPE_MAP = {
        'NO SERVICE': 'No Service',
        'GSM': 'GSM',
        'GPRS': 'GPRS',
        'EDGE': 'EDGE',
        'WCDMA': 'WCDMA',
        'HSDPA': 'HSDPA',
        'HSUPA': 'HSUPA',
        'HSPA': 'HSPA',
        'HSPA+': 'HSPA+',
        'LTE': 'LTE',
        'LTE-A': 'LTE CA',
        'CAT-M': 'LTE Cat-M',
        'CAT-NB': 'LTE Cat-NB',
        'NR5G-NSA': 'NR5G NSA',
        'NR5G-SA': 'NR5G SA'
    }

    def __init__(self, port: str = "/dev/ttyUSB2", baudrate: int = 115200, timeout: int = 10):
        """
        Initialize the QuectelModemReader.

        Args:
            port (str): Serial port device path. Default is "/dev/ttyUSB2"
            baudrate (int): Baud rate for serial connection. Default is 115200
            timeout (int): Read timeout in seconds. Default is 10
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_conn = None

    def __enter__(self):
        """Context manager entry - establishes serial connection to the modem."""
        self.serial_conn = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE
        )
        # Clear any pending data
        self.serial_conn.reset_input_buffer()
        self.serial_conn.reset_output_buffer()

        # Test connection with simple AT command
        response = self._send_at_command("AT")
        if "OK" not in response:
            raise RuntimeError(f"Modem not responding on {self.port}")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes serial connection to the modem."""
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
        return False

    def _send_at_command(self, command: str, timeout: float = None) -> str:
        """
        Send an AT command and read the response.

        Args:
            command (str): AT command to send (without \r\n)
            timeout (float): Optional timeout override for this command

        Returns:
            str: Response from modem

        Raises:
            RuntimeError: If serial connection is not open
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            raise RuntimeError("Serial connection not open. Use context manager (with statement).")

        # Clear input buffer before sending command
        self.serial_conn.reset_input_buffer()

        # Send command with carriage return
        cmd_bytes = (command + "\r\n").encode('utf-8')
        self.serial_conn.write(cmd_bytes)

        # Read response with timeout
        response_lines = []
        start_time = time.time()
        effective_timeout = timeout if timeout is not None else self.timeout

        while True:
            if time.time() - start_time > effective_timeout:
                raise TimeoutError(f"Timeout waiting for response to: {command}")

            if self.serial_conn.in_waiting > 0:
                line = self.serial_conn.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    response_lines.append(line)
                    # Check for completion markers
                    if line in ('OK', 'ERROR') or line.startswith('+CME ERROR'):
                        break
            else:
                time.sleep(0.01)  # Small delay to avoid busy waiting

        return '\n'.join(response_lines)

    @staticmethod
    def _parse_signal_value(value) -> int | None:
        """
        Parse signal value from AT response.

        Extracts numeric integer value from strings like "-116" or "-116dBm".

        Args:
            value: Raw value from AT response (may be string or int)

        Returns:
            Parsed integer value or None if parsing fails
        """
        if value is None or value == '':
            return None
        match = re.search(r'-?\d+', str(value))
        return int(match.group()) if match else None

    def _get_signal_info(self) -> dict:
        """
        Fetch signal information using AT+QENG="servingcell".

        Returns:
            dict: Signal data including rsrp, rsrq, sinr
        """
        response = self._send_at_command('AT+QENG="servingcell"')

        # Parse AT+QENG response
        # Example LTE response:
        # +QENG: "servingcell","NOCONN","LTE","FDD",234,30,1D07201,366,100,3,5,5,774E,-116,-14,0,-

        signal_data = {'rsrp': None, 'rsrq': None, 'sinr': None}

        for line in response.split('\n'):
            if line.startswith('+QENG: "servingcell"'):
                parts = [p.strip().strip('"') for p in line.split(',')]

                # Check if this is LTE data (has enough fields)
                if len(parts) >= 17 and 'LTE' in parts:
                    # LTE field positions (may vary by firmware)
                    # Typical format has RSRP at index -5, RSRQ at -4, SINR at -3
                    try:
                        signal_data['rsrp'] = self._parse_signal_value(parts[-5])
                        signal_data['rsrq'] = self._parse_signal_value(parts[-4])
                        signal_data['sinr'] = self._parse_signal_value(parts[-3])
                    except (IndexError, ValueError):
                        pass

        return signal_data

    def _get_status_info(self) -> dict:
        """
        Fetch network status using AT+QNWINFO.

        Returns:
            dict: Status data including network type
        """
        response = self._send_at_command('AT+QNWINFO')

        # Parse AT+QNWINFO response
        # Example: +QNWINFO: "FDD LTE","23430","LTE BAND 3",1300

        network_type = 'Unknown'

        for line in response.split('\n'):
            if line.startswith('+QNWINFO:'):
                parts = [p.strip().strip('"') for p in line.split(':')[1].split(',')]
                if len(parts) >= 1 and parts[0]:
                    # Extract network type (e.g., "FDD LTE" -> "LTE")
                    raw_type = parts[0].upper()
                    if 'LTE' in raw_type:
                        network_type = 'LTE'
                    elif 'WCDMA' in raw_type or 'UMTS' in raw_type:
                        network_type = 'WCDMA'
                    elif 'GSM' in raw_type:
                        network_type = 'GSM'
                    elif 'NR' in raw_type or '5G' in raw_type:
                        network_type = 'NR5G-NSA' if 'NSA' in raw_type else 'NR5G-SA'
                    else:
                        network_type = raw_type

        return {'CurrentNetworkType': network_type}

    def _get_plmn_info(self) -> dict:
        """
        Fetch operator information using AT+COPS?.

        Returns:
            dict: PLMN data including operator name
        """
        response = self._send_at_command('AT+COPS?')

        # Parse AT+COPS response
        # Example: +COPS: 0,0,"Tele2",7

        operator_name = 'Unknown'

        for line in response.split('\n'):
            if line.startswith('+COPS:'):
                # Extract operator name from quotes
                match = re.search(r'"([^"]+)"', line)
                if match:
                    operator_name = match.group(1)

        return {'FullName': operator_name, 'ShortName': operator_name}

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

        # Get signal metrics (already parsed as integers)
        rsrp = signal_info.get('rsrp')
        rsrq = signal_info.get('rsrq')
        sinr = signal_info.get('sinr')

        # Get operator name
        operator_name = (
            plmn_info.get('FullName') or
            plmn_info.get('ShortName') or
            "Unknown"
        )

        # Get network mode
        network_type_raw = status_info.get('CurrentNetworkType', 'Unknown')
        network_mode = self.NETWORK_TYPE_MAP.get(
            network_type_raw,
            network_type_raw
        )

        return {
            'rsrp': rsrp,
            'rsrq': rsrq,
            'sinr': sinr,
            'operator_name': operator_name,
            'network_mode': network_mode
        }


def main():
    """Example usage of QuectelModemReader."""
    with QuectelModemReader("/dev/ttyUSB2") as reader:
        data = reader.get_signal_data()

        print("Modem Signal Data:")
        print(f"  Operator: {data['operator_name']}")
        print(f"  Network Mode: {data['network_mode']}")
        print(f"  RSRP: {data['rsrp']} dBm" if data['rsrp'] is not None else "  RSRP: N/A")
        print(f"  RSRQ: {data['rsrq']} dB" if data['rsrq'] is not None else "  RSRQ: N/A")
        print(f"  SINR: {data['sinr']} dB" if data['sinr'] is not None else "  SINR: N/A")


if __name__ == "__main__":
    main()
