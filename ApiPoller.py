import json
import os
import requests
import time
import logging
from datetime import datetime
from pathlib import Path
from PIL import Image
import subprocess
import RPi.GPIO as GPIO
import threading
from gpiozero import AngularServo

CONFIG_FILE = "config.json"
STATUS_FILE = "printer_status.json"
LOG_FILE = "app.log"
SERVO_PIN = 14
BUTTON_PIN = 4

RED_PIN = 22
GREEN_PIN = 27
BLUE_PIN = 17

PAPER_CAPACITY = 18
INK_CAPACITY = 54


servo = AngularServo(SERVO_PIN, min_angle=0, max_angle=180, min_pulse_width=0.5/1000, max_pulse_width=2.5/1000)

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

def load_status():
    """Load printer status from file, create default if missing."""
    if not os.path.exists(STATUS_FILE):
        status = {"paper": PAPER_CAPACITY, "ink": INK_CAPACITY}
        save_status(status)
    else:
        with open(STATUS_FILE, 'r') as f:
            status = json.load(f)
    return status

def save_status(status):
    """Save printer status to file."""
    with open(STATUS_FILE, 'w') as f:
        json.dump(status, f, indent=4)

def check_supply_levels():
    """Check if ink or paper is empty and stop operation if needed."""
    status = load_status()
    if status["paper"] == 0 or status["ink"] == 0:
        log_event("Printer out of paper or ink. Stopping operation.")
        set_led_color(1, 0, 0)  # Turn LED red
        while not check_for_refill():
            log_event("Waiting for refill...")
            time.sleep(10)  # Poll every 10 seconds
        log_event("Printer refilled. Resuming operation.")
        set_led_color(0, 1, 0)  # Turn LED green
    return status

def check_for_refill():
    """Poll API endpoint to check if printer has been refilled."""
    try:
        config = load_config()
        headers = {"Authorization": config["printer_token"]}
        response = requests.get("https://senior-gimenio.eu/api/printer/refill", timeout=10, headers=headers)
        if response.status_code == 200:
            refill_data = response.json()
            if refill_data.get("paper_refilled", False):
                log_event("Paper refilled")
                status = load_status()
                status["paper"] = PAPER_CAPACITY
                save_status(status)
            if refill_data.get("ink_refilled", False):
                log_event("Ink refilled")
                status = load_status()
                status["ink"] = INK_CAPACITY
                save_status(status)
            return True
    except requests.exceptions.RequestException as e:
        log_error(f"Error checking refill status: {e}")
    return False

def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "printer_token": "<TOKEN>",
            "url": "https://senior-gimenio.eu/api",
            "request_url": "/message/request",
            "ack_url": "/message/ack",
            "image_url": "/message/image",
            "check_interval": 1,
            "image_path": "images/"
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)
        log_event(f"Config file created at {CONFIG_FILE}. Please edit and restart.")
        exit()
    
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    
    if config["printer_token"] == "<TOKEN>":
        log_error("Please enter a valid printer token in the config file and restart.")
        exit()
    
    return config

def check_for_new_messages(config):
    print("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] Checking for new messages...")
    # log_event("Checking for new messages...")

    status = load_status()
    headers = {
        "Authorization": config["printer_token"],
        "X-Paper-Remaining": str(status["paper"]),
        "X-Ink-Remaining": str(status["ink"])
    }
    while True:
        try:
            response = requests.get(config["url"] + config["request_url"], headers=headers, timeout=10)
            if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type', ''):
                log_event("New message found")
                parse_message(config, response.json())
            elif response.status_code == 201:
                # log_event("No new messages found")
                print("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] No new messages found")
            else:
                log_error(f"Error: {response.status_code}")
            break
        except requests.exceptions.RequestException as e:
            log_error(f"Connection lost: {e}. Retrying in 5 seconds...")
            time.sleep(5)

def parse_message(config, data):
    message_id = data.get("id")
    if message_id:
        get_image(config, message_id)

def get_image(config, message_id):
    log_event("Getting image...")
    headers = {"Authorization": config["printer_token"]}
    while True:
        try:
            response = requests.get(f"{config['url']}{config['image_url']}/{message_id}", headers=headers, stream=True, timeout=10)
            if response.status_code == 200 and 'image' in response.headers.get('Content-Type', ''):
                log_event("Image pulled.")
                save_image(config, response, message_id)
            elif response.status_code == 201:
                log_event("No new messages found")
            else:
                log_error(f"Error: {response.status_code}")
            break
        except requests.exceptions.RequestException as e:
            log_error(f"Connection lost: {e}. Retrying in 5 seconds...")
            time.sleep(5)

def save_image(config, response, message_id):
    try:
        mime = response.headers['Content-Type'].split('/')[1]
        filename = generate_file_name(config["image_path"], mime)
        os.makedirs(config["image_path"], exist_ok=True)
        image_path = os.path.join(config["image_path"], filename)
        
        with open(image_path, 'wb') as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)
        
        log_event(f"Image saved to {image_path}")
        ack_message(config, message_id)
        print_image(image_path)
        if not flag_raised:
            threading.Thread(target=raise_flag, daemon=True).start()
    except Exception as e:
        log_error(f"Error saving image: {e}")

def ack_message(config, message_id):
    log_event(f"Acknowledging message ID: {message_id}")
    headers = {"Authorization": config["printer_token"]}
    while True:
        try:
            response = requests.post(f"{config['url']}{config['ack_url']}?message_id={message_id}", headers=headers, timeout=10)
            log_event(response.text)
            break
        except requests.exceptions.RequestException as e:
            log_error(f"Connection lost: {e}. Retrying in 5 seconds...")
            time.sleep(5)

def print_image(image_path):
    log_event("Printing image...")
    if not os.path.exists(image_path):
        log_error("Image file not found, skipping print...")
        return
    
    status = check_supply_levels()
    if status["paper"] == 0 or status["ink"] == 0:
        log_error("Cannot print, out of supplies.")
        return

    try:
        image = Image.open(image_path)
        orientation_option = "-o landscape" if image.width >= image.height else "-o portrait"
        command = ["/snap/bin/cups.lp", "-o", "media=Postcard.Borderless", "-o", "fill", orientation_option, image_path]
        subprocess.run(command)
        status["paper"] -= 1
        status["ink"] -= 1
        save_status(status)
        log_event(f"Printed successfully. Remaining: {status['paper']} pages, {status['ink']} ink units.")
    except Exception as e:
        log_error(f"Error processing image: {e}")

def raise_flag():
    global flag_raised
    if flag_raised:
        log_error("Flag already raised, skipping...")
        return
    flag_raised = True
    try:
        GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        def set_servo_angle(angle):
            global servo
            servo.angle = angle
            time.sleep(1)
            servo.detach()
        
        log_event("Raising flag...")
        set_servo_angle(180)
        
        log_event("Waiting for button press...")
        while GPIO.input(BUTTON_PIN):
            time.sleep(0.1)
        
        log_event("Lowering flag...")
        set_servo_angle(10)
        flag_raised = False
    except Exception as e:
        log_error(f"Error in raise_flag: {e}")

def generate_file_name(directory, mime):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(directory, f"{timestamp}.{mime}")

def init_servo():
    global servo
    servo.angle = 10
    time.sleep(1)
    servo.detach()

# Function to control LED color
def set_led_color(red, green, blue):
    GPIO.output(RED_PIN, red)
    GPIO.output(GREEN_PIN, green)
    GPIO.output(BLUE_PIN, blue)

# Function to update LED status based on flag state
def update_led_status():
    global flag_raised
    while True:
        if flag_raised:
            set_led_color(0, 0, 1)  # Blue (message received, flag raised)
        else:
            set_led_color(0, 1, 0)  # Green (normal operation)
        time.sleep(0.5)

def init_led():
    GPIO.setup(RED_PIN, GPIO.OUT)
    GPIO.setup(GREEN_PIN, GPIO.OUT)
    GPIO.setup(BLUE_PIN, GPIO.OUT)

    led_thread = threading.Thread(target=update_led_status, daemon=True)
    led_thread.start()

def init_GPIO():
    GPIO.setmode(GPIO.BCM)

if __name__ == "__main__":
    init_GPIO()
    init_led()
    init_servo()
    config = load_config()
    log_event("Gimenio started")
    while True:
        try:
            check_supply_levels()
            check_for_new_messages(config)
            time.sleep(config["check_interval"])
        except Exception as e:
            log_error(f"Unhandled error: {e}")
