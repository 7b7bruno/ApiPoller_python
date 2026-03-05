#!/usr/bin/env python3

"""
Test NeoPixel functionality.

This script tests the NeoPixel LEDs by cycling through different colors.
Uses the Adafruit CircuitPython NeoPixel library.

Usage:
    python3 test_neopixels.py
"""

import sys
import os
from pathlib import Path
import time
import board
import neopixel

# Add parent directory to path so we can import from classes
sys.path.insert(0, str(Path(__file__).parent.parent))


class NeoPixelStrip:
    """
    NeoPixel (WS281x) controller using Adafruit CircuitPython library.

    Simple wrapper around adafruit-circuitpython-neopixel.
    """
    def __init__(self, gpio_pin: int, num_pixels: int, brightness: float = 1.0):
        """
        Initialize NeoPixel controller.

        Args:
            gpio_pin: GPIO pin number (BCM numbering)
            num_pixels: Number of LEDs in the strip
            brightness: Global brightness (0.0 to 1.0)
        """
        # Map GPIO pin number to board pin
        pin_map = {
            18: board.D18,
            12: board.D12,
            21: board.D21,
            10: board.D10,
        }

        if gpio_pin not in pin_map:
            raise ValueError(f"GPIO pin {gpio_pin} not supported. Use 18, 12, 21, or 10.")

        # Initialize the NeoPixel strip
        self.pixels = neopixel.NeoPixel(
            pin_map[gpio_pin],
            num_pixels,
            brightness=max(0.0, min(1.0, brightness)),
            auto_write=False,
            pixel_order=neopixel.GRB
        )
        self.num_pixels = num_pixels

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
        self.pixels.fill((red, green, blue))

    def show(self):
        """
        Update the LED strip with buffered pixel data.
        """
        self.pixels.show()

    def clear(self):
        """Turn off all LEDs."""
        self.pixels.fill((0, 0, 0))
        self.pixels.show()

    def cleanup(self):
        """Clean up GPIO resources."""
        self.clear()
        self.pixels.deinit()


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
        print("\nTroubleshooting:")
        print("  - Make sure you have the required libraries installed:")
        print("    sudo pip3 install adafruit-circuitpython-neopixel")
        print("  - Run with sudo if you get permission errors:")
        print("    sudo python3 test_neopixels.py")
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
