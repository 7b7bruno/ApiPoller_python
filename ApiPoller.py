import json
import os
import requests
import time
from datetime import datetime
from pathlib import Path
from PIL import Image
import subprocess
import RPi.GPIO as GPIO
import threading

CONFIG_FILE = "config.json"
SERVO_PIN = 18
BUTTON_PIN = 23

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
        print(f"Config file created at {CONFIG_FILE}. Please edit and restart.")
        exit()
    
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    
    if config["printer_token"] == "<TOKEN>":
        print("Please enter a valid printer token in the config file and restart.")
        exit()
    
    return config

def check_for_new_messages(config):
    print("Checking for new messages...")
    headers = {"Authorization": config["printer_token"]}
    while True:
        try:
            response = requests.get(config["url"] + config["request_url"], headers=headers, timeout=10)
            if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type', ''):
                print("New message found")
                parse_message(config, response.json())
            elif response.status_code == 201:
                print("No new messages found")
            else:
                print(f"Error: {response.status_code}")
            break
        except requests.exceptions.RequestException:
            print("Connection lost. Retrying in 5 seconds...")
            time.sleep(5)

def parse_message(config, data):
    message_id = data.get("id")
    if message_id:
        get_image(config, message_id)

def get_image(config, message_id):
    print("Getting image...")
    headers = {"Authorization": config["printer_token"]}
    while True:
        try:
            response = requests.get(f"{config['url']}{config['image_url']}/{message_id}", headers=headers, stream=True, timeout=10)
            if response.status_code == 200 and 'image' in response.headers.get('Content-Type', ''):
                print("Image pulled.")
                save_image(config, response, message_id)
            elif response.status_code == 201:
                print("No new messages found")
            else:
                print(f"Error: {response.status_code}")
            break
        except requests.exceptions.RequestException:
            print("Connection lost. Retrying in 5 seconds...")
            time.sleep(5)

def save_image(config, response, message_id):
    mime = response.headers['Content-Type'].split('/')[1]
    filename = generate_file_name(config["image_path"], mime)
    
    os.makedirs(config["image_path"], exist_ok=True)
    image_path = os.path.join(config["image_path"], filename)
    
    with open(image_path, 'wb') as f:
        for chunk in response.iter_content(1024):
            f.write(chunk)
    
    print(f"Image saved to {image_path}")
    ack_message(config, message_id)
    print_image(image_path)
    threading.Thread(target=raise_flag, daemon=True).start()

def ack_message(config, message_id):
    print(f"Acknowledging message ID: {message_id}")
    headers = {"Authorization": config["printer_token"]}
    while True:
        try:
            response = requests.post(f"{config['url']}{config['ack_url']}?message_id={message_id}", headers=headers, timeout=10)
            print(response.text)
            break
        except requests.exceptions.RequestException:
            print("Connection lost. Retrying in 5 seconds...")
            time.sleep(5)

def print_image(image_path):
    if not os.path.exists(image_path):
        print("Image file not found, skipping print...")
        return
    
    try:
        image = Image.open(image_path)
        orientation_option = "-o landscape" if image.width >= image.height else "-o portrait"
    except Exception as e:
        print(f"Error processing image: {e}")
        return
    
    command = ["lp", "-o", "media=Postcard.Borderless", "-o", "fill", orientation_option, image_path]
    subprocess.run(command)

def raise_flag():
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
    
    print("Raising flag...")
    set_servo_angle(180)
    
    print("Waiting for button press...")
    while GPIO.input(BUTTON_PIN):
        time.sleep(0.1)
    
    print("Lowering flag...")
    set_servo_angle(0)
    
    GPIO.cleanup()

def generate_file_name(directory, mime):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{timestamp}.{mime}"
    file_path = os.path.join(directory, filename)
    return file_path

if __name__ == "__main__":
    config = load_config()
    while True:
        check_for_new_messages(config)
        time.sleep(config["check_interval"])
