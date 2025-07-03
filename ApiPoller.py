import json
import os
import requests
import time
import logging
from datetime import datetime
from pathlib import Path
from PIL import Image
import subprocess
import RPi.GPIO as GPIO # type: ignore
import threading
from gpiozero import AngularServo # type: ignore
from huawei_lte_api.Connection import Connection  # type: ignore
from huawei_lte_api.Client import Client  # type: ignore
from huawei_lte_api.enums.client import ResponseEnum  # type: ignore

CONFIG_FILE = "config.json"
STATUS_FILE = "printer_status.json"
LOG_FILE = "app.log"

last_successful_request = time.time()

servo = None
flag_raised = False
config = None

waiting_for_refill = False
refill_type = None
_refill_press_count = 0
_last_refill_press_time = 0.0

# Setup logging
logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def log_event(message):
    print(message)
    logging.info(message)

def log_error(message):
    print(f"ERROR: {message}")
    logging.error(message)

def modem_reboot_scheduler():
    while True:
        time.sleep(config["modem_restart_trigger_interval"])
        reboot_modem()

def reboot_modem():
    try:
        log_event("Rebooting modem...")
        with Connection(config["modem_gateway_url"]) as connection:
            client = Client(connection)
            if client.device.reboot() == ResponseEnum.OK.value:
                log_event("Modem reboot requested successfully.")
                time.sleep(30)
                send_modem_reboot()
            else:
                log_error("Modem reboot failed.")
    except Exception as e:
        log_error(f"Error rebooting modem: {e}")

def send_modem_reboot():
    log_event("Notifying server of modem restart...")
    headers = {"Authorization": config["printer_token"]}
    url = config["url"] + config["modem_restart_url"]
    
    timeout_time = time.time() + config["modem_restart_notify_timeout_interval"]  # Try for up to 5 minutes (300 seconds)

    while time.time() < timeout_time:
        try:
            response = requests.get(url, headers=headers, timeout=config["request_timeout_interval"])
            if response.status_code == 200:
                log_event("Server acknowledged modem reboot.")
                return True
            else:
                log_error(f"Modem reboot notify failed: {response.status_code}")
        except Exception as e:
            log_error(f"Error notifying server of modem reboot: {e}")
        
        time.sleep(10)  # Wait 10 seconds before retrying

def load_status():
    """Load printer status from file, create default if missing."""
    if not os.path.exists(STATUS_FILE):
        status = {"paper": config["paper_capacity"], "ink": config["ink_capacity"]}
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
    global waiting_for_refill, refill_type
    status = load_status()
    if status["paper"] == 0 and status["ink"] > 0:
        waiting_for_refill = True
        refill_type = "paper"
        set_led_color(1, 1, 0)     # Yellow
        log_event("Out of paper — waiting for refill")
    elif status["ink"] == 0:
        waiting_for_refill = True
        refill_type = "ink" if status["paper"] > 0 else "both"
        set_led_color(1, 0, 0)     # Red
        log_event("Out of ink — waiting for refill")
    return status

def check_for_refill():
    """Poll API endpoint to check if printer has been refilled."""
    try:
        config = load_config()
        headers = {"Authorization": config["printer_token"]}
        response = requests.get(config["url"] + config["refill_url"], timeout=config["request_timeout_interval"], headers=headers)
        if response.status_code == 200:
            refill_data = response.json()
            refilled = False
            status = load_status()

            if refill_data.get("paper_refilled", False):
                log_event("Paper refilled")
                status["paper"] = config["paper_capacity"]
                refilled = True

            if refill_data.get("ink_refilled", False):
                log_event("Ink refilled")
                status["ink"] = config["ink_capacity"]
                refilled = True

            if refilled:
                save_status(status)

            return refilled
        
    except requests.exceptions.RequestException as e:
        log_error(f"Error checking refill status: {e}")
        
    return False

def _perform_refill():
    global waiting_for_refill, refill_type
    status = load_status()

    if refill_type in ("paper", "both"):
        status["paper"] = config["paper_capacity"]
        log_event("Paper manually refilled.")

    if refill_type in ("ink", "both"):
        status["ink"] = config["ink_capacity"]
        status["paper"] = config["paper_capacity"]
        log_event("Ink manually refilled.")

    save_status(status)

    waiting_for_refill = False
    set_led_color(0, 1, 0)         # Green
    log_event("Resumed normal operation after manual refill.")

def load_config():
    global config

    if not os.path.exists(CONFIG_FILE):
        log_event(f"Rename and edit one of the provided config files to config.json and edit token, then start program again.")
        exit()
    
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    
    if config["printer_token"] == "<TOKEN>":
        log_error("Please enter a valid printer token in the config file and restart.")
        exit()
    
    return config

def check_for_new_messages():
    global last_successful_request
    if waiting_for_refill:
        log_event("Waiting for refill...")
        return
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
            response = requests.get(config["url"] + config["request_url"], headers=headers, timeout=config["request_timeout_interval"])
            if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type', ''):
                log_event("New message found")
                parse_message(config, response.json())
            elif response.status_code == 201:
                # log_event("No new messages found")
                print("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] No new messages found")
            else:
                log_error(f"Error: {response.status_code}")
            last_successful_request = time.time()
            break
        except requests.exceptions.RequestException as e:
            request_timeout_interval = config["request_timeout_interval"]
            log_error(f"Connection lost: {e}. Retrying in {request_timeout_interval} seconds...")

def parse_message(config, data):
    message_id = data.get("id")
    if message_id:
        get_image(config, message_id)

def get_image(config, message_id):
    log_event("Getting image...")
    headers = {"Authorization": config["printer_token"]}
    while True:
        try:
            response = requests.get(f"{config['url']}{config['image_url']}/{message_id}", headers=headers, stream=True, timeout=config["request_timeout_interval"])
            if response.status_code == 200 and 'image' in response.headers.get('Content-Type', ''):
                log_event("Image pulled.")
                save_image(config, response, message_id)
            elif response.status_code == 201:
                log_event("No new messages found")
            else:
                log_error(f"Error: {response.status_code}")
            break
        except requests.exceptions.RequestException as e:
            request_timeout_interval = config["request_timeout_interval"]
            log_error(f"Connection lost: {e}. Retrying in {request_timeout_interval} seconds...")
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
            response = requests.post(f"{config['url']}{config['ack_url']}?message_id={message_id}", headers=headers, timeout=config["request_timeout_interval"])
            log_event(response.text)
            break
        except requests.exceptions.RequestException as e:
            request_timeout_interval = config["request_timeout_interval"]
            log_error(f"Connection lost: {e}. Retrying in {request_timeout_interval} seconds...")
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
    log_event("Waiting " + str(config["rise_delay"]) + " seconds before rising flag")
    time.sleep(config["rise_delay"])

    try:
        GPIO.setup(config["button_pin"], GPIO.IN, pull_up_down=GPIO.PUD_UP)
        def set_servo_angle(angle):
            global servo
            servo.angle = angle
            time.sleep(1)
            servo.detach()
        
        log_event("Raising flag...")
        set_servo_angle(config["flag_up_angle"])
        
        log_event("Waiting for button press...")
        while GPIO.input(config["button_pin"]):
            time.sleep(0.1)
        
        log_event("Lowering flag...")
        set_servo_angle(config["flag_down_angle"])
        flag_raised = False
    except Exception as e:
        log_error(f"Error in raise_flag: {e}")

def generate_file_name(directory, mime):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(directory, f"{timestamp}.{mime}")

def _door_pressed(channel):
    global _refill_press_count, _last_refill_press_time
    if not waiting_for_refill:
        return

    now = time.time()
    if now - _last_refill_press_time > 5:
        _refill_press_count = 0

    _last_refill_press_time = now

    # Count rising edges (door opened)
    if GPIO.input(channel) == GPIO.HIGH:
        _refill_press_count += 1
        log_event(f"Refill door press detected {_refill_press_count}/3")

    if _refill_press_count >= 3:
        _perform_refill()
        _refill_press_count = 0

def init_servo():
    global servo
    servo = AngularServo(config["servo_pin"], min_angle=0, max_angle=180, min_pulse_width=0.5/1000, max_pulse_width=2.5/1000)
    servo.angle = config["flag_down_angle"]
    time.sleep(1)
    servo.detach()

# Function to control LED color
def set_led_color(red, green, blue):
    GPIO.output(config["led_pins"]["red"], red)
    GPIO.output(config["led_pins"]["green"], green)
    GPIO.output(config["led_pins"]["blue"], blue)

# Function to update LED status based on flag state
def update_led_status():
    global flag_raised
    while True:
        if waiting_for_refill:
            if refill_type == "paper":
                set_led_color(1, 1, 0)
            else:
                set_led_color(1, 0, 0)
        elif flag_raised:
            set_led_color(0, 0, 1)  # Blue (message received, flag raised)
        else:
            set_led_color(0, 1, 0)  # Green (normal operation)
        time.sleep(0.5)

def init_led():
    GPIO.setup(config["led_pins"]["red"], GPIO.OUT)
    GPIO.setup(config["led_pins"]["green"], GPIO.OUT)
    GPIO.setup(config["led_pins"]["blue"], GPIO.OUT)

    led_thread = threading.Thread(target=update_led_status, daemon=True)
    led_thread.start()

def init_GPIO():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(config["button_pin"], GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.add_event_detect(config["button_pin"], GPIO.BOTH,
                      callback=_door_pressed, bouncetime=200)

if __name__ == "__main__":
    config = load_config()
    init_GPIO()
    init_led()
    init_servo()
    log_event("Gimenio started")
    threading.Thread(target=modem_reboot_scheduler, daemon=True).start()
    log_event("Modem restart thread started")
    while True:
        try:
            check_supply_levels()
            check_for_new_messages()
            time.sleep(config["check_interval"])
        except Exception as e:
            log_error(f"Unhandled error: {e}")
