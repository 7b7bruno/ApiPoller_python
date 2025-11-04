#!/usr/bin/env python3
"""
LED Color Testing Utility

This utility allows you to experiment with LED colors using the same
approach as ApiPoller.py. It uses PWMLED for full RGB color control.

Usage:
    Interactive mode:
        python led_test.py

    Command-line mode:
        python led_test.py <red> <green> <blue>
        Example: python led_test.py 1 0.3 0  (orange)

    Preset colors:
        python led_test.py --preset <color_name>
        Example: python led_test.py --preset orange

Values should be between 0.0 (off) and 1.0 (full brightness)
"""

import sys
import time
from gpiozero import PWMLED #type:ignore

# Default configuration - same as ApiPoller.py
DEFAULT_CONFIG = {
    "led_pins": {
        "red": 23,
        "green": 15,
        "blue": 18
    }
}

class ConfigManager:
    """Manages configuration with default value fallback."""

    def __init__(self, defaults):
        self.defaults = defaults
        self.config = {}

    def update_from_dict(self, config_dict):
        """Update config from a dictionary, merging with defaults."""
        merged = self.defaults.copy()
        for key, value in config_dict.items():
            if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        self.config = merged

    def __getitem__(self, key):
        """Dict-like access: config["key"]"""
        return self.config.get(key, self.defaults.get(key))


# Preset colors for quick testing
PRESET_COLORS = {
    "red": (1, 0, 0),
    "green": (0, 1, 0),
    "blue": (0, 0, 1),
    "yellow": (1, 1, 0),
    "orange": (1, 0.3, 0),
    "cyan": (0, 1, 1),
    "magenta": (1, 0, 1),
    "white": (1, 1, 1),
    "dim_white": (0.5, 0.5, 0.5),
    "purple": (0.5, 0, 1),
    "pink": (1, 0.2, 0.5),
    "off": (0, 0, 0)
}


class LEDTester:
    """LED testing utility using PWMLED."""

    def __init__(self):
        self.config = ConfigManager(DEFAULT_CONFIG)
        self.led_red = None
        self.led_green = None
        self.led_blue = None
        self.init_leds()

    def init_leds(self):
        """Initialize LEDs using PWMLED - same as ApiPoller.py"""
        print(f"Initializing LEDs on pins: R={self.config['led_pins']['red']}, "
              f"G={self.config['led_pins']['green']}, B={self.config['led_pins']['blue']}")

        self.led_red = PWMLED(self.config["led_pins"]["red"])
        self.led_green = PWMLED(self.config["led_pins"]["green"])
        self.led_blue = PWMLED(self.config["led_pins"]["blue"])

        print("LEDs initialized successfully!")

    def set_color(self, red, green, blue):
        """Set LED color - same approach as ApiPoller.py"""
        # Clamp values between 0 and 1
        red = max(0.0, min(1.0, red))
        green = max(0.0, min(1.0, green))
        blue = max(0.0, min(1.0, blue))

        self.led_red.value = red
        self.led_green.value = green
        self.led_blue.value = blue

        print(f"Color set to: R={red:.2f}, G={green:.2f}, B={blue:.2f}")

    def show_presets(self):
        """Display available preset colors."""
        print("\nAvailable preset colors:")
        for name, (r, g, b) in PRESET_COLORS.items():
            print(f"  {name:12} - R={r:.1f}, G={g:.1f}, B={b:.1f}")

    def interactive_mode(self):
        """Interactive mode for testing colors."""
        print("\n" + "="*60)
        print("LED Color Testing - Interactive Mode")
        print("="*60)
        print("\nCommands:")
        print("  <r> <g> <b>  - Set RGB values (0.0-1.0)")
        print("  preset <name> - Use a preset color")
        print("  list         - Show available presets")
        print("  off          - Turn off LEDs")
        print("  quit         - Exit")
        print("\nExamples:")
        print("  1 0.3 0      - Orange")
        print("  preset orange - Orange (using preset)")
        print("  0.5 0 1      - Purple")
        print("="*60)

        while True:
            try:
                cmd = input("\nEnter command: ").strip().lower()

                if not cmd:
                    continue

                if cmd in ["quit", "exit", "q"]:
                    print("Turning off LEDs and exiting...")
                    self.set_color(0, 0, 0)
                    break

                if cmd == "list":
                    self.show_presets()
                    continue

                if cmd == "off":
                    self.set_color(0, 0, 0)
                    continue

                parts = cmd.split()

                # Handle preset command
                if parts[0] == "preset" and len(parts) == 2:
                    preset_name = parts[1]
                    if preset_name in PRESET_COLORS:
                        r, g, b = PRESET_COLORS[preset_name]
                        self.set_color(r, g, b)
                    else:
                        print(f"Unknown preset: {preset_name}")
                        print("Use 'list' to see available presets")
                    continue

                # Handle RGB values
                if len(parts) == 3:
                    try:
                        r = float(parts[0])
                        g = float(parts[1])
                        b = float(parts[2])
                        self.set_color(r, g, b)
                    except ValueError:
                        print("Error: RGB values must be numbers between 0.0 and 1.0")
                    continue

                print("Invalid command. Type 'quit' to exit or 'list' for presets.")

            except KeyboardInterrupt:
                print("\n\nInterrupted. Turning off LEDs and exiting...")
                self.set_color(0, 0, 0)
                break
            except EOFError:
                print("\nExiting...")
                self.set_color(0, 0, 0)
                break

    def cleanup(self):
        """Clean up GPIO resources."""
        if self.led_red:
            self.led_red.close()
        if self.led_green:
            self.led_green.close()
        if self.led_blue:
            self.led_blue.close()


def main():
    """Main entry point."""
    tester = LEDTester()

    try:
        # Command-line mode
        if len(sys.argv) > 1:
            # Preset mode
            if sys.argv[1] == "--preset" and len(sys.argv) == 3:
                preset_name = sys.argv[2].lower()
                if preset_name in PRESET_COLORS:
                    r, g, b = PRESET_COLORS[preset_name]
                    tester.set_color(r, g, b)
                    print(f"\nLED set to preset '{preset_name}'")
                    print("Press Ctrl+C to turn off and exit...")
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        print("\nTurning off LEDs...")
                        tester.set_color(0, 0, 0)
                else:
                    print(f"Error: Unknown preset '{preset_name}'")
                    tester.show_presets()
                    return 1

            # RGB values mode
            elif len(sys.argv) == 4:
                try:
                    r = float(sys.argv[1])
                    g = float(sys.argv[2])
                    b = float(sys.argv[3])
                    tester.set_color(r, g, b)
                    print("\nPress Ctrl+C to turn off and exit...")
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        print("\nTurning off LEDs...")
                        tester.set_color(0, 0, 0)
                except ValueError:
                    print("Error: RGB values must be numbers between 0.0 and 1.0")
                    print(__doc__)
                    return 1

            else:
                print(__doc__)
                return 1

        # Interactive mode
        else:
            tester.interactive_mode()

    finally:
        tester.cleanup()

    return 0


if __name__ == "__main__":
    sys.exit(main())
