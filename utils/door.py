import RPi.GPIO as GPIO
import time

# Define the button GPIO pin
BUTTON_PIN = 4  # GPIO4 (Pin 7)

# Setup GPIO mode and button pin
GPIO.setmode(GPIO.BCM)  # Use Broadcom GPIO numbering
GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Enable internal pull-up resistor

print("Monitoring button state... (Press CTRL+C to exit)")

try:
    while True:
        if GPIO.input(BUTTON_PIN) == GPIO.LOW:  # Button is pressed
            print("Button Pressed")
        else:
            print("Button Not Pressed")
        time.sleep(0.1)  # Small delay to prevent excessive printing
except KeyboardInterrupt:
    print("\nExiting...")
    GPIO.cleanup()  # Reset GPIO settings
