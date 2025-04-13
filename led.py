import RPi.GPIO as GPIO
import time

# Define RGB LED pins
RED_PIN = 22
GREEN_PIN = 27
BLUE_PIN = 17

# Setup GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(RED_PIN, GPIO.OUT)
GPIO.setup(GREEN_PIN, GPIO.OUT)
GPIO.setup(BLUE_PIN, GPIO.OUT)

# Function to change color
def set_color(red, green, blue):
    GPIO.output(RED_PIN, red)
    GPIO.output(GREEN_PIN, green)
    GPIO.output(BLUE_PIN, blue)

try:
    while True:
        set_color(1, 0, 0)  # Red
        time.sleep(1)
        set_color(0, 1, 0)  # Green
        time.sleep(1)
        set_color(0, 0, 1)  # Blue
        time.sleep(1)
        set_color(1, 1, 0)  # Yellow
        time.sleep(1)
        set_color(1, 0, 1)  # Purple
        time.sleep(1)
        set_color(0, 1, 1)  # Cyan
        time.sleep(1)
        set_color(1, 1, 1)  # White
        time.sleep(1)
        set_color(0, 0, 0)  # Off
        time.sleep(1)
except KeyboardInterrupt:
    GPIO.cleanup()
