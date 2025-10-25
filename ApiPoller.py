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

CONFIG_FILE = "config.json"
STATUS_FILE = "printer_status.json"
LOG_FILE = "app.log"

last_successful_request = time.time()
last_successful_command_request = time.time()

servo = None
button = None
flag_raised = False
config = None

# LED OutputDevice objects
led_red = None
led_green = None
led_blue = None
paper_led_red = None
paper_led_green = None
paper_led_blue = None

waiting_for_refill = False
refill_type = None
_refill_press_count = 0
_last_refill_press_time = 0.0

# CUPS connection
cupsConn = None

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
        config = json.load(f)

def update_config():
    headers = {
        "Authorization": config["printer_token"],
    }
    retries = 5
    while retries > 0:
        try:
            response = requests.get(config["url"] + config["config_url"], headers=headers, timeout=config["request_timeout_interval"])
            if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type', ''):
                log_event("Config retrieved")
                data = response.json()
                if(check_config(data)):
                    with open(CONFIG_FILE, "w") as f:
                        json.dump(data, f, indent=4)
                    load_config_file()
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
    # if waiting_for_refill:
    #     log_event("Waiting for refill...")
    #     return
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
    
    # status = check_supply_levels()
    # if status["paper"] == 0 or status["ink"] == 0:
    #     log_error("Cannot print, out of supplies.")
    #     return

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
        log_event("Raising flag...")
        set_servo_angle(config["flag_up_angle"])

        log_event("Waiting for button press...")
        button.wait_for_press()

        log_event("Lowering flag...")
        set_servo_angle(config["flag_down_angle"])
        flag_raised = False
    except Exception as e:
        log_error(f"Error in raise_flag: {e}")
        flag_raised = False

def generate_file_name(directory, mime):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(directory, f"{timestamp}.{mime}")

def _on_door_open():
    global _refill_press_count, _last_refill_press_time
    if not waiting_for_refill:
        return

    now = time.time()
    if now - _last_refill_press_time > 5:
        _refill_press_count = 0
    _last_refill_press_time = now

    _refill_press_count += 1
    log_event(f"Door opened ({_refill_press_count}/3)")

    if _refill_press_count >= 3:
        _perform_refill()
        _refill_press_count = 0

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
