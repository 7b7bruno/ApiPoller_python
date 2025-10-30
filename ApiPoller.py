import json
import os
import requests
import time
import logging
from datetime import datetime
from pathlib import Path
from PIL import Image
import subprocess
import threading
from gpiozero import AngularServo, Button, OutputDevice # type: ignore
from huawei_lte_api.Connection import Connection  # type: ignore
from huawei_lte_api.Client import Client  # type: ignore
from huawei_lte_api.enums.client import ResponseEnum  # type: ignore
import cups
import traceback
import enum

CONFIG_FILE = "config.json"
STATUS_FILE = "printer_status.json"
LOG_FILE = "app.log"

# Default configuration values
DEFAULT_CONFIG = {
    "printer_token": "<TOKEN>",
    "url": "https://senior-gimenio.eu/api",
    "request_url": "/message/request",
    "ack_url": "/message/ack",
    "command_url": "/command",
    "command_ack_url": "/command/ack",
    "config_url": "/config",
    "image_url": "/message/image",
    "refill_url": "/printer/refill",
    "flag_state_url": "/special/flag",
    "modem_restart_url": "/printer/modem-restart",
    "modem_gateway_url": "http://192.168.8.1",
    "print_command": "/snap/bin/cups.lp",
    "printer_name": "Canon_SELPHY_CP1500",
    "initial_delay": 10,
    "cups_retries": 30,
    "check_interval": 30,
    "command_check_interval": 10,
    "request_timeout_interval": 30,
    "reboot_modem": False,
    "modem_restart_trigger_interval": 3600,
    "modem_restart_notify_timeout_interval": 300,
    "modem_boot_time": 60,
    "image_path": "images/",
    "paper_capacity": 18,
    "ink_capacity": 54,
    "led_pins": {
        "red": 23,
        "green": 15,
        "blue": 18
    },
    "paper_led_pins": {
        "red": 13,
        "green": 19,
        "blue": 26
    },
    "paper_led": False,
    "servo_pin": 14,
    "button_pin": 24,
    "flag_down_angle": 180,
    "flag_up_angle": 0,
    "rise_delay": 48,
    "print_tracking_interval": 2
}

class ConfigManager:
    """Manages configuration with default value fallback."""

    def __init__(self, defaults):
        self.defaults = defaults
        self.config = {}

    def update_from_dict(self, config_dict):
        """Update config from a dictionary, merging with defaults."""
        # Start with defaults
        merged = self.defaults.copy()

        # Deep merge for nested dictionaries
        for key, value in config_dict.items():
            if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value

        self.config = merged

    def __getitem__(self, key):
        """Dict-like access: config["key"]"""
        return self.config.get(key, self.defaults.get(key))

    def get(self, key, default=None):
        """Safe access with optional default."""
        return self.config.get(key, self.defaults.get(key, default))

    def __contains__(self, key):
        """Support 'in' operator."""
        return key in self.config or key in self.defaults

last_successful_request = time.time()
last_successful_command_request = time.time()

servo = None
button = None
flag_raised = False
config = ConfigManager(DEFAULT_CONFIG)

# LED OutputDevice objects
led_red = None
led_green = None
led_blue = None
paper_led_red = None
paper_led_green = None
paper_led_blue = None

waiting_for_refill = False
refill_type = None

# CUPS connection
cupsConn = None

# State enum
class State(enum.Enum):
    IDLE = "idle"
    INCOMING_TRANSMISSION = "incoming_transmission"
    MESSAGE_RECEIVED = "message_received"
    OUT_OF_PAPER = "out_of_paper"
    OUT_OF_INK = "out_of_ink"
    OUT_OF_INK_AND_PAPER = "out_of_ink_and_paper"

state = State.IDLE

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

def init_config():
    global config

    if not os.path.exists(CONFIG_FILE):
        log_event(f"Rename and edit one of the provided config files to config.json and edit token, then start program again.")
        exit()
    
    load_config_file()

    if config["printer_token"] == "<TOKEN>":
        log_error("Please enter a valid printer token in the config file and restart.")
        exit()

    update_config()

def load_config_file():
    global config
    with open(CONFIG_FILE, 'r') as f:
        config_dict = json.load(f)
    config.update_from_dict(config_dict)

def update_config():
    headers = {
        "Authorization": config["printer_token"],
    }
    retries = 30
    while retries > 0:
        try:
            response = requests.get(config["url"] + config["config_url"], headers=headers, timeout=config["request_timeout_interval"])
            if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type', ''):
                log_event("Config retrieved")
                data = response.json()
                if(check_config(data)):
                    with open(CONFIG_FILE, "w") as f:
                        json.dump(data, f, indent=4)
                    # Server config overrides all, then defaults fill in any missing fields
                    config.update_from_dict(data)
                    log_event("Config updated")
                else:
                    log_error("Pulled config doesn't pass integrity check, not using It.")
            elif response.status_code == 201:
                # log_event("No new messages found")
                print("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] No new messages found")
            else:
                log_error(f"Error: {response.status_code}")
                retries -= 1
            break
        except requests.exceptions.RequestException as e:
            request_timeout_interval = config["request_timeout_interval"]
            log_error(f"Connection lost: {e}. Retrying in {request_timeout_interval} seconds...")
            time.sleep(1)
            retries -= 1

def check_config(data):
    if not isinstance(data, dict):
        return False

    token = data.get("printer_token")
    if not isinstance(token, str):
        return False

    if len(token) != 32:
        return False

    return token.isalnum()

def check_for_new_messages():
    global last_successful_request
    print("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] Checking for new messages...")

    headers = {
        "Authorization": config["printer_token"],
    }
    while True:
        try:
            response = requests.get(config["url"] + config["request_url"], headers=headers, timeout=config["request_timeout_interval"])
            if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type', ''):
                log_event("New message found")
                handle_message(config, response.json())
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



def handle_message(config, data):
    global state
    state = State.INCOMING_TRANSMISSION

    def handle_transmission_failure():
        if state is not State.MESSAGE_RECEIVED:
            state = State.IDLE

    message_id = data.get("id", None)
    
    if message_id is None:
        log_error("Retrieved message contains invalid message id. Skipping.")
        handle_transmission_failure()
        return
    
    image_path = get_image(config, message_id)
    if image_path is None:
        log_error("Failed to pull image.")
        handle_transmission_failure()
        return

    job_id = print_image(image_path)
    if job_id is None:
        log_error("Failed to print.")
        handle_transmission_failure()
        return

    print_completed = track_print(job_id)
    if print_completed:
        state = State.MESSAGE_RECEIVED
        flag_thread = threading.Thread(target=raise_flag, daemon=True)
        flag_thread.start()
        ack_message(message_id)
    else:
        log_error("Print tracking failed. Not raising flag. Ack'ing message")
        handle_transmission_failure()
        ack_message(message_id)

def get_image(config, message_id):
    log_event("Getting image...")
    headers = {"Authorization": config["printer_token"]}
    while True:
        try:
            response = requests.get(f"{config['url']}{config['image_url']}/{message_id}", headers=headers, stream=True, timeout=config["request_timeout_interval"])
            if response.status_code == 200 and 'image' in response.headers.get('Content-Type', ''):
                log_event("Image pulled.")
                image_path = save_image(config, response, message_id)
                return image_path
            elif response.status_code == 201:
                log_event("No new messages found")
                return None
            else:
                log_error(f"Error: {response.status_code}")
                return None
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
        return image_path
        # ack_message(config, message_id)
        # print_image(image_path)
        # if not flag_raised:
        #     threading.Thread(target=raise_flag, daemon=True).start()
    except Exception as e:
        log_error(f"Error saving image: {e}")
        return None

def ack_message(message_id):
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

    printer_name = config["printer_name"]
    # Detect orientation
    img = Image.open(image_path)
    width, height = img.size
    is_landscape = width > height
    options = {
        'media': 'custom_max_102x153mm',
        'print-scaling': 'fill',
        'orientation-requested': '4' if is_landscape else '3'  # 3=portrait, 4=landscape
    }
    
    print(f"Image size: {width}x{height} ({'landscape' if is_landscape else 'portrait'})")

    try:
        job_id = cupsConn.printFile(printer_name, image_path, 
                               f'Photo Print', options)
        print(f"✓ Job {job_id} submitted: {image_path}")   
        return job_id   
    except Exception as e:
        log_error(f"Error processing image: {e}")
        return None

def track_print(job_id):
    global state
    log_event(f"Tracking job {job_id}...")
    # IPP job states
    job_states = {
        3: 'pending',
        4: 'pending-held',
        5: 'processing',
        6: 'processing-stopped',
        7: 'canceled',
        8: 'aborted',
        9: 'completed'
    }
    last_state = None
    last_error = None
    start_time = time.time()
    job_found = False
    while True:
        try:
            jobs = cupsConn.getJobs(which_jobs='all', my_jobs=False, first_job_id=job_id, limit=1)
            if job_id in jobs:
                job_found = True
                current_state = cupsConn.getJobAttributes(job_id)["job-state"]
                state_name = job_states.get(current_state, f'unknown({current_state})')

                if current_state is None:
                    log_error(f"Job {job_id} status is none. Stopping tracking.")
                    return False
                # Print status change
                if current_state != last_state:
                    log_event(f"Job {job_id} status: {state_name}")
                    last_state = current_state

                # Check for completion or error states
                if current_state == 5:
                    reasons = cupsConn.getJobAttributes(job_id).get("job-printer-state-reasons", [])
                    if len(reasons) > 1:
                        current_error = None
                        if "marker-supply-empty-error" in reasons and "input-tray-missing" in reasons:
                            current_error = "No paper casette/ink cartridge or both"
                            state = State.OUT_OF_INK_AND_PAPER
                        elif "media-empty-error" in reasons:
                            current_error = "Out of paper"
                            state = State.OUT_OF_PAPER
                        elif "marker-supply-empty-error" in reasons:
                            current_error = "Out of ink or ink cartridge missing"
                            state = State.OUT_OF_INK
                        elif "input-tray-missing" in reasons:
                            current_error = "Paper casette missing or incorrectly inserted"
                            state = State.OUT_OF_PAPER
                        if current_error is not None and last_error != current_error:
                            log_error(current_error)
                            last_error = current_error
                    elif last_error is not None:
                        state = State.INCOMING_TRANSMISSION

                elif current_state == 9:  # completed
                    log_event(f"✓ Job {job_id} completed successfully!")
                    return True
                elif current_state in [7, 8]:  # canceled or aborted
                    log_error(f"✗ Job {job_id} {state_name}")
                    return False
            else:
                # Job not in queue
                if job_found:
                    # Job was found before but now gone - it completed
                    log_event(f"✓ Job {job_id} no longer in queue. Assuming It completed successfully.")
                    return True
                else:
                    # Job never found - might have completed immediately
                    # Try a few more times before giving up
                    if time.time() - start_time > 5:
                        log_error(f"✗ Job never found. Stopping tracking.")
                        return False

            time.sleep(config["print_tracking_interval"])

        except:
            log_error(f"Error tracking job: {e}")
            traceback.print_exc()
            return False
    

def raise_flag():
    global flag_raised, state
    if flag_raised:
        log_error("Flag already raised, skipping.")
        return
    
    log_event("Raising flag.")

    try:
        log_event("Raising flag...")
        set_servo_angle(config["flag_up_angle"])
        flag_raised = True

        log_event("Waiting for button press...")
        button.wait_for_press()

        if state is not State.INCOMING_TRANSMISSION:
            state = State.IDLE
        log_event("Lowering flag...")
        set_servo_angle(config["flag_down_angle"])
        flag_raised = False
    except Exception as e:
        log_error(f"Error in raise_flag: {e}")
        if state is not State.INCOMING_TRANSMISSION:
            state = State.IDLE
        flag_raised = False

def generate_file_name(directory, mime):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(directory, f"{timestamp}.{mime}")

def init_servo():
    global servo
    servo = AngularServo(config["servo_pin"], min_angle=0, max_angle=180, min_pulse_width=0.5/1000, max_pulse_width=2.5/1000)
    servo.angle = config["flag_down_angle"]
    time.sleep(1)
    servo.detach()

def set_servo_angle(angle):
            global servo
            servo.angle = angle
            time.sleep(1)
            servo.detach()

# Function to control LED color
def set_led_color(red, green, blue):
    if red:
        led_red.on()
    else:
        led_red.off()
    if green:
        led_green.on()
    else:
        led_green.off()
    if blue:
        led_blue.on()
    else:
        led_blue.off()

# Function to update LED status based on flag state
def update_led_status():
    global flag_raised
    while True:
        match state:
            case State.IDLE:
                set_led_color(0, 1, 0)
            case State.INCOMING_TRANSMISSION:
                set_led_color(0, 0, 1)
            case State.MESSAGE_RECEIVED:
                set_led_color(0, 1, 1)
            case State.OUT_OF_INK:
                set_led_color(1, 0, 0)
            case State.OUT_OF_PAPER:
                set_led_color(1, 0, 0)
            case State.OUT_OF_INK_AND_PAPER:
                set_led_color(1, 0, 0)

        time.sleep(0.5)

# Function to control LED color
def set_paper_led_color(red, green, blue):
    if red:
        paper_led_red.on()
    else:
        paper_led_red.off()
    if green:
        paper_led_green.on()
    else:
        paper_led_green.off()
    if blue:
        paper_led_blue.on()
    else:
        paper_led_blue.off()

# Function to update LED status based on flag state
def update_paper_led_status():
    while True:
        set_paper_led_color(1, 0, 0)
        time.sleep(0.5)

def init_led():
    global led_red, led_green, led_blue
    led_red = OutputDevice(config["led_pins"]["red"])
    led_green = OutputDevice(config["led_pins"]["green"])
    led_blue = OutputDevice(config["led_pins"]["blue"])

    led_thread = threading.Thread(target=update_led_status, daemon=True)
    led_thread.start()

def init_paper_led():
    global paper_led_red, paper_led_green, paper_led_blue
    if config["paper_led"] == True:
        paper_led_red = OutputDevice(config["paper_led_pins"]["red"])
        paper_led_green = OutputDevice(config["paper_led_pins"]["green"])
        paper_led_blue = OutputDevice(config["paper_led_pins"]["blue"])

        paper_led_thread = threading.Thread(target=update_paper_led_status, daemon=True)
        paper_led_thread.start()

        log_event("This model has an out-of-paper indicator light. It's been switched on.")
    else:
        log_event("This model does not have a paper indicator light.")

def init_GPIO():
    global button
    button = Button(config["button_pin"], pull_up=True, bounce_time=0.1)
    # button.when_pressed = _on_door_open

def init_command_thread():
    command_thread = threading.Thread(target=pollCommands, daemon=True)
    command_thread.start()

def pollCommands():
    while True:
        check_for_new_commands()
        time.sleep(config["command_check_interval"])

def check_for_new_commands():
    global last_successful_command_request
    headers = {
        "Authorization": config["printer_token"],
    }
    while True:
        try:
            response = requests.get(config["url"] + config["command_url"], headers=headers, timeout=config["request_timeout_interval"])
            if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type', ''):
                log_event("New command found")
                dispatchCommand(response.json())
            elif response.status_code == 201:
                log_event("No new commands found")
            #     print("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] No new messages found")
            else:
                log_error(f"Command check error: {response.status_code}")
            last_successful_command_request = time.time()
            break
        except requests.exceptions.RequestException as e:
            request_timeout_interval = config["request_timeout_interval"]
            log_error(f"Connection lost: {e}. Retrying in {request_timeout_interval} seconds...")
       
def dispatchCommand(data):
    command_id = data.get("command_id")
    command = data.get("command")
    if command and command_id:
        ackCommand(command_id)
        match command:
            case "reboot":
                reboot()
            case "shutdown":
                shutdown()
            case "flagup":
                flagUp()
            case "flagdown":
                flagDown()
            case "loadconfig":
                update_config()
    else:
        log_error("Unknown response for command")

def ackCommand(command_id):
    log_event(f"Acknowledging command. ID: {command_id}")
    headers = {"Authorization": config["printer_token"]}
    while True:
        try:
            response = requests.post(f"{config['url']}{config['command_ack_url']}?command_id={command_id}", headers=headers, timeout=config["request_timeout_interval"])
            log_event(response.text)
            break
        except requests.exceptions.RequestException as e:
            request_timeout_interval = config["request_timeout_interval"]
            log_error(f"Connection lost: {e}. Retrying in {request_timeout_interval} seconds...")
            time.sleep(5)

def reboot():
    command = ["sudo", "reboot"]
    subprocess.run(command)
def shutdown():
    command = ["sudo", "poweroff"]
    subprocess.run(command)
def flagUp():
    set_servo_angle(config["flag_up_angle"])
def flagDown():
    set_servo_angle(config["flag_down_angle"])

def init_CUPS():
    global cupsConn
    log_event(f"Waiting {config["initial_delay"]}s before connecting to cups")
    time.sleep(config["initial_delay"])
    for attempt in range(config["cups_retries"]):
        try:
            cupsConn = cups.Connection()
            cupsConn.getPrinters()
            print("CUPS connected")
            return True
        except RuntimeError as e:
            if attempt < config["cups_retries"] - 1:
                print(f"Waiting for CUPS... ({attempt + 1}/{config['cups_retries']})")
                time.sleep(1)
            else:
                raise Exception(f"CUPS not available after {config["cups_retries"]}")
    return None

if __name__ == "__main__":
    init_config()
    init_GPIO()
    init_led()
    init_paper_led()
    init_servo()
    init_CUPS()
    log_event("Gimenio started")
    if config["reboot_modem"] is True:
        threading.Thread(target=modem_reboot_scheduler, daemon=True).start()
        log_event("Modem restart thread started")
    init_command_thread()
    log_event("Command thread started")
    log_event("DEMO PRINTER. Paper and ink level tracking disabled.")
    while True:
        try:
            # check_supply_levels()
            check_for_new_messages()
            time.sleep(config["check_interval"])
        except Exception as e:
            log_error(f"Unhandled error: {e}")
