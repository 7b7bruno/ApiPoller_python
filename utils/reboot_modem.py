#!/usr/bin/env python3
"""
Example code on how to reboot the modem using AT commands.
Usage: python3 reboot_modem.py
"""
import serial  # type: ignore
import time

SERIAL_PORT = '/dev/ttyUSB2'
BAUDRATE = 115200

print("This util reboots the Quectel modem using AT commands and waits until it comes online.")

# Open serial connection
ser = serial.Serial(
    port=SERIAL_PORT,
    baudrate=BAUDRATE,
    timeout=5,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE
)

try:
    # Clear buffers
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    print("Restarting modem with AT+CFUN=1,1...")
    # Send reboot command (AT+CFUN=1,1 performs a full module reset)
    ser.write(b"AT+CFUN=1,1\r\n")

    # Read immediate response (may be OK or nothing as modem reboots)
    time.sleep(0.5)
    if ser.in_waiting > 0:
        response = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
        print(f"Response: {response}")

    print("Waiting for modem to restart...")
    time.sleep(20)

    # Close and reopen serial connection
    ser.close()
    time.sleep(2)

    print("Waiting for modem to come up...")
    while True:
        try:
            # Attempt to reconnect
            ser = serial.Serial(
                port=SERIAL_PORT,
                baudrate=BAUDRATE,
                timeout=5,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE
            )

            # Clear buffers
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            # Test with simple AT command
            ser.write(b"AT\r\n")
            time.sleep(0.5)

            if ser.in_waiting > 0:
                response = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                if "OK" in response:
                    print("Modem booted!")
                    break

            ser.close()
            print("Modem not responding yet...")
            time.sleep(5)

        except Exception as e:
            print(f"Waiting for modem... ({e})")
            time.sleep(5)

finally:
    if ser.is_open:
        ser.close()