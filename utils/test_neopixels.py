#!/usr/bin/env python3

"""
Test NeoPixel functionality.

This script tests the NeoPixel LEDs by cycling through different colors.
Requires pigpiod to be running: sudo pigpiod

Usage:
    python3 test_neopixels.py
"""

import sys
import os
from pathlib import Path
import time
import pigpio

# Add parent directory to path so we can import from classes
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the NeoPixel class from ApiPoller
# (We'll use a simplified version here for testing)

class NeoPixel:
    """
    NeoPixel (WS281x) controller using pigpio.

    Requires pigpio daemon to be running: sudo pigpiod
    """
    def __init__(self, gpio_pin: int, num_pixels: int, brightness: float = 1.0):
        """
        Initialize NeoPixel controller.

        Args:
            gpio_pin: GPIO pin number (BCM numbering)
            num_pixels: Number of LEDs in the strip
            brightness: Global brightness (0.0 to 1.0)
        """
        self.gpio_pin = gpio_pin
        self.num_pixels = num_pixels
        self.brightness = max(0.0, min(1.0, brightness))

        # Connect to pigpio daemon
        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise RuntimeError("Failed to connect to pigpio daemon. Is pigpiod running?")

        # Initialize pixel buffer (GRB order for WS281x)
        self.pixels = [(0, 0, 0)] * num_pixels

        # Set up GPIO for NeoPixel output
        self.pi.set_mode(gpio_pin, pigpio.OUTPUT)

    def set_pixel(self, pixel_num: int, red: int, green: int, blue: int):
        """
        Set a single pixel color (0-255 for each channel).

        Args:
            pixel_num: Pixel index (0-based)
            red, green, blue: Color values (0-255)
        """
        if 0 <= pixel_num < self.num_pixels:
            self.pixels[pixel_num] = (red, green, blue)

    def set_all(self, red: int, green: int, blue: int):
        """
        Set all pixels to the same color.

        Args:
            red, green, blue: Color values (0-255)
        """
        for i in range(self.num_pixels):
            self.pixels[i] = (red, green, blue)

    def show(self):
        """
        Update the LED strip with buffered pixel data.

        Sends data to WS281x LEDs via pigpio waveform.
        """
        # Build bit stream for WS281x protocol
        # T0H: 0.4us, T0L: 0.85us  →  Bit 0
        # T1H: 0.8us, T1L: 0.45us  →  Bit 1

        wf = []

        for pixel in self.pixels:
            # Apply brightness and convert to 8-bit GRB order
            r = int(pixel[0] * self.brightness)
            g = int(pixel[1] * self.brightness)
            b = int(pixel[2] * self.brightness)

            # WS281x uses GRB order
            grb = (g << 16) | (r << 8) | b

            # Send 24 bits (GRB)
            for i in range(23, -1, -1):
                bit = (grb >> i) & 1

                if bit:
                    # Bit 1: 0.8us high, 0.45us low
                    wf.append(pigpio.pulse(1 << self.gpio_pin, 0, 800))  # 0.8us high
                    wf.append(pigpio.pulse(0, 1 << self.gpio_pin, 450))  # 0.45us low
                else:
                    # Bit 0: 0.4us high, 0.85us low
                    wf.append(pigpio.pulse(1 << self.gpio_pin, 0, 400))  # 0.4us high
                    wf.append(pigpio.pulse(0, 1 << self.gpio_pin, 850))  # 0.85us low

        # Send reset (>50us low)
        wf.append(pigpio.pulse(0, 1 << self.gpio_pin, 60))

        # Clear any existing waveforms
        self.pi.wave_clear()

        # Add pulses to waveform
        self.pi.wave_add_generic(wf)

        # Create and transmit waveform
        wave_id = self.pi.wave_create()
        if wave_id >= 0:
            self.pi.wave_send_once(wave_id)
            # Wait for transmission to complete
            while self.pi.wave_tx_busy():
                time.sleep(0.001)
            self.pi.wave_delete(wave_id)

    def clear(self):
        """Turn off all LEDs."""
        self.set_all(0, 0, 0)
        self.show()

    def cleanup(self):
        """Clean up GPIO resources."""
        self.clear()
        if self.pi.connected:
            self.pi.stop()


def main():
    # Configuration
    GPIO_PIN = 18
    NUM_PIXELS = 3
    BRIGHTNESS = 0.5

    print(f"NeoPixel Test Utility")
    print(f"GPIO Pin: {GPIO_PIN}")
    print(f"Number of LEDs: {NUM_PIXELS}")
    print(f"Brightness: {BRIGHTNESS}")
    print()

    try:
        # Initialize NeoPixels
        print("Initializing NeoPixels...")
        strip = NeoPixel(GPIO_PIN, NUM_PIXELS, BRIGHTNESS)
        print("✓ NeoPixels initialized")
        print()

        # Test sequence
        colors = [
            ("Red", 255, 0, 0),
            ("Green", 0, 255, 0),
            ("Blue", 0, 0, 255),
            ("Yellow", 255, 255, 0),
            ("Cyan", 0, 255, 255),
            ("Magenta", 255, 0, 255),
            ("White", 255, 255, 255),
            ("Orange", 255, 128, 0),
            ("Purple", 128, 0, 255),
        ]

        print("Starting color cycle test (Ctrl+C to stop)...")
        print()

        while True:
            for name, r, g, b in colors:
                print(f"  {name:12s} RGB({r:3d}, {g:3d}, {b:3d})")
                strip.set_all(r, g, b)
                strip.show()
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except RuntimeError as e:
        print(f"\n✗ Error: {e}")
        print("\nMake sure pigpiod is running:")
        print("  sudo pigpiod")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        # Clean up
        try:
            print("\nCleaning up...")
            strip.clear()
            strip.cleanup()
            print("✓ NeoPixels turned off")
        except:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
