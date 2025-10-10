import json
import os
import time
import logging
import requests
import RPi.GPIO as GPIO # type: ignore
from gpiozero import AngularServo # type: ignore
from datetime import datetime
import threading

CONFIG_FILE = "config.json"
LOG_FILE = "flagpoller.log"

config = None
servo = None
flag_raised = False

# Setup logging
logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def log_event(message):
    print(message)
    logging.info(message)

def log_error(message):
    print(f"ERROR: {message}")
    logging.error(message)

def load_config():
    global config
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"{CONFIG_FILE} not found.")
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    return config

def set_led_color(red, green, blue):
    GPIO.output(config["led_pins"]["red"], red)
    GPIO.output(config["led_pins"]["green"], green)
    GPIO.output(config["led_pins"]["blue"], blue)

def update_led_status():
    global flag_raised
    while True:
        if flag_raised:
            set_led_color(0, 0, 1)  # Blue
        else:
            set_led_color(0, 1, 0)  # Green
        time.sleep(0.5)

def init_led():
    GPIO.setup(config["led_pins"]["red"], GPIO.OUT)
    GPIO.setup(config["led_pins"]["green"], GPIO.OUT)
    GPIO.setup(config["led_pins"]["blue"], GPIO.OUT)
    threading.Thread(target=update_led_status, daemon=True).start()

def init_GPIO():
    GPIO.setmode(GPIO.BCM)

def init_servo():
    global servo
    servo = AngularServo(
        config["servo_pin"],
        min_angle=0,
        max_angle=180,
        min_pulse_width=0.5/1000,
        max_pulse_width=2.5/1000
    )
    servo.angle = config["flag_down_angle"]
    time.sleep(1)
    servo.detach()

def set_servo_angle(angle):
    global servo
    servo.angle = angle
    time.sleep(1)
    servo.detach()

def raise_flag():
    global flag_raised
    if not flag_raised:
        log_event("Raising flag.")
        set_servo_angle(config["flag_up_angle"])
        flag_raised = True

def lower_flag():
    global flag_raised
    if flag_raised:
        log_event("Lowering flag.")
        set_servo_angle(config["flag_down_angle"])
        flag_raised = False

def poll_flag_state():
    headers = {"Authorization": config["printer_token"]}
    url = config["url"] + config["flag_state_url"]
    while True:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                state = response.json().get("state", "down")
                if state == "up":
                    raise_flag()
                else:
                    lower_flag()
            else:
                log_error(f"Unexpected status: {response.status_code}")
        except Exception as e:
            log_error(f"Polling error: {e}")
        time.sleep(config["check_interval"])

if __name__ == "__main__":
    config = load_config()
    init_GPIO()
    init_led()
    init_servo()
    log_event("FlagPoller started")
    poll_flag_state()
