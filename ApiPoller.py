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

CONFIG_FILE = "config.json"
LOG_FILE = "app.log"
SERVO_PIN = 18
BUTTON_PIN = 23

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
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "printer_token": "<TOKEN>",
            "url": "http://stripe.test/api",
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
    headers = {"Authorization": config["printer_token"]}
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
    
    try:
        image = Image.open(image_path)
        orientation_option = "-o landscape" if image.width >= image.height else "-o portrait"
        command = ["lp", "-o", "media=Postcard.Borderless", "-o", "fill", orientation_option, image_path]
        subprocess.run(command)
    except Exception as e:
        log_error(f"Error processing image: {e}")

def raise_flag():
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(SERVO_PIN, GPIO.OUT)
        GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        
        def set_servo_angle(angle):
            pwm = GPIO.PWM(SERVO_PIN, 50)
            pwm.start(0)
            duty_cycle = (angle / 18.0) + 2.5
            pwm.ChangeDutyCycle(duty_cycle)
            time.sleep(0.5)
            pwm.stop()
            GPIO.setup(SERVO_PIN, GPIO.IN)  # Disable the servo by setting GPIO to input mode
        
        log_event("Raising flag...")
        set_servo_angle(180)
        
        # log_event("Waiting for button press...")
        # while GPIO.input(BUTTON_PIN):
        #     time.sleep(0.1)
        
        # log_event("Lowering flag...")
        # set_servo_angle(0)
        GPIO.cleanup()
    except Exception as e:
        log_error(f"Error in raise_flag: {e}")

def generate_file_name(directory, mime):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(directory, f"{timestamp}.{mime}")

if __name__ == "__main__":
    config = load_config()
    log_event("Gimenio started")
    while True:
        try:
            check_for_new_messages(config)
            time.sleep(config["check_interval"])
        except Exception as e:
            log_error(f"Unhandled error: {e}")
