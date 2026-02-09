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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from gpiozero import AngularServo, Button, PWMLED # type: ignore
from huawei_lte_api.Connection import Connection  # type: ignore
from huawei_lte_api.Client import Client  # type: ignore
from huawei_lte_api.enums.client import ResponseEnum  # type: ignore
import cups
import traceback
import enum
import math
import glob as glob_module
from dataclasses import dataclass
from classes.huawei_modem_reader import HuaweiModemReader
from classes.network_client import NetworkClient
from classes.recovery_manager import RecoveryManager

CONFIG_FILE = "config.json"
STATUS_FILE = "printer_status.json"
LOG_FILE = "app.log"
PENDING_COLLECTIONS_FILE = "pending_collections.json"

# Threading locks for global variable access
state_lock = threading.Lock()
flag_lock = threading.Lock()
pending_ids_lock = threading.Lock()
button_press_event = threading.Event()

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
    "auth_check_url": "/auth/check",
    "collection_url": "/message/collected",
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
    "print_tracking_interval": 2,
    # Network client configuration
    "retry_max_attempts": 3,
    "retry_critical_attempts": 10,
    "retry_backoff_factor": 2.0,
    "retry_max_delay": 60,
    "connection_pool_size": 10,
    "connect_timeout": 10,
    "read_timeout": 30,
    "keepalive_timeout": 60,
    "circuit_breaker_threshold": 5,
    "circuit_breaker_cooldown": 60,
    "max_consecutive_errors": 30,
    "verbose_logging": False,
    "no_print": False,
    "collection_notifications": True,
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
    

@dataclass
class Route:
    interface: str
    metric: int
    gateway: str

VERSION = "V0.3.6"

last_successful_request = time.time()
last_successful_command_request = time.time()

servo = None
button = None
flag_raised = False
config = ConfigManager(DEFAULT_CONFIG)
pending_message_ids = []

def load_pending_collections():
    """Load pending message IDs from persistent storage."""
    global pending_message_ids
    if os.path.exists(PENDING_COLLECTIONS_FILE):
        try:
            with open(PENDING_COLLECTIONS_FILE, 'r') as f:
                pending_message_ids = json.load(f)
                log_event(f"Loaded {len(pending_message_ids)} pending collection ID(s) from disk.")
        except Exception as e:
            log_error(f"Failed to load pending collections: {e}")
            pending_message_ids = []

def save_pending_collections():
    """Save pending message IDs to persistent storage."""
    try:
        with open(PENDING_COLLECTIONS_FILE, 'w') as f:
            json.dump(pending_message_ids, f)
    except Exception as e:
        log_error(f"Failed to save pending collections: {e}")

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

# Network client and recovery manager
network_client = None
recovery_manager = None

# State enum
class State(enum.Enum):
    BOOTING = "Booting"
    IDLE = "Idle"
    INCOMING_TRANSMISSION = "Incoming transmission"
    MESSAGE_RECEIVED = "Message received"
    ACKNOWLEDGING = "Acknowledging message"
    OUT_OF_PAPER = "Out of paper"
    OUT_OF_INK = "Out of ink"
    OUT_OF_INK_AND_PAPER = "Out of ink and paper"
    PAPER_JAM = "Paper jam"
    WAITING_FOR_CUPS = "Waiting for CUPS to start"
    CONNECTION_WEAK = "Connection weak"
    NO_CONNECTION = "No connection"
    CIRCUIT_BREAKER_OPEN = "Server down (circuit breaker open)"
    MODEM_REBOOTING = "Modem rebooting"
    PRINTER_UNREACHABLE = "Printer unreachable"

state = State.BOOTING
state_before_connection_issue = None
no_connection_since = None  # Timestamp when NO_CONNECTION state was entered
last_modem_reboot_attempt = None  # Timestamp of last modem reboot attempt

# Speed of last download
last_download_speed = None

# Setup logging
logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

def log_event(message):
    timestamp = "[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "]"
    print(f"{timestamp} - {message}")
    logging.info(f"{timestamp} - {message}")

def log_error(message):
    timestamp = "[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "]"
    print(f"{timestamp} - {message}")
    logging.error(f"{timestamp} - {message}")

def log_verbose(message):
    """Log verbose debug messages only if verbose_logging is enabled"""
    if config.get("verbose_logging", False):
        timestamp = "[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "]"
        print(f"{timestamp} - [VERBOSE] {message}")
        logging.info(f"{timestamp} - [VERBOSE] {message}")

def on_network_connection_weak():
    """Callback when network connection has first failure."""
    global state, state_before_connection_issue
    with state_lock:
        # Only save previous state if we're not already in a connection issue state
        if state not in [State.CONNECTION_WEAK, State.NO_CONNECTION, State.CIRCUIT_BREAKER_OPEN]:
            state_before_connection_issue = state
        state = State.CONNECTION_WEAK

def on_network_connection_lost():
    """Callback when network connection is completely lost (all retries exhausted)."""
    global state, state_before_connection_issue
    with state_lock:
        # Only save previous state if we're not already in a connection issue state
        if state not in [State.CONNECTION_WEAK, State.NO_CONNECTION, State.CIRCUIT_BREAKER_OPEN]:
            state_before_connection_issue = state
        state = State.NO_CONNECTION

    log_event("Connection lost - triggering recovery manager")

    def retry_connection():
        """Check if network connection is restored"""
        try:
            response = network_client.get(
                config["url"] + config["request_url"],
                headers=getInitialHeaders(),
                max_attempts=1
            )
            return response.status_code in [200, 201]
        except Exception:
            return False

    # Skip recovery escalation if connected via wifi - modem reboot won't help
    if get_connection_type() == "wifi":
        log_event("Connection lost but connected via wifi. Skipping modem reboot - recovery will rely on network client retries.")
        return

    recovery_manager.handle_critical_failure(
        operation_name="network_connection",
        ack_id="connection_lost",
        ack_data={},
        retry_callback=retry_connection
    )

def on_network_connection_restored():
    """Callback when network connection is restored."""
    global state, state_before_connection_issue
    with state_lock:
        log_event("Connection restored")
        # Restore to previous state if we were in a connection issue state
        if state in [State.CONNECTION_WEAK, State.NO_CONNECTION, State.CIRCUIT_BREAKER_OPEN]:
            if state_before_connection_issue is not None:
                state = state_before_connection_issue
                state_before_connection_issue = None
            else:
                # Fallback to IDLE if we don't have a previous state
                state = State.IDLE

def on_circuit_breaker_open():
    """Callback when circuit breaker opens (server confirmed down, internet is up)."""
    global state, state_before_connection_issue
    with state_lock:
        # Only save previous state if we're not already in a connection issue state
        if state not in [State.CONNECTION_WEAK, State.NO_CONNECTION, State.CIRCUIT_BREAKER_OPEN]:
            state_before_connection_issue = state
        state = State.CIRCUIT_BREAKER_OPEN

def on_circuit_breaker_close():
    """Callback when circuit breaker closes (server recovered)."""
    global state, state_before_connection_issue
    with state_lock:
        # Restore to previous state if we're in circuit breaker open state
        if state == State.CIRCUIT_BREAKER_OPEN:
            if state_before_connection_issue is not None:
                state = state_before_connection_issue
                state_before_connection_issue = None
            else:
                # Fallback to IDLE if we don't have a previous state
                state = State.IDLE

def init_network_client():
    """Initialize network client and recovery manager with configuration."""
    global network_client, recovery_manager

    network_client = NetworkClient(
        pool_connections=config["connection_pool_size"],
        pool_maxsize=config["connection_pool_size"] * 2,
        keepalive_timeout=config["keepalive_timeout"],
        connect_timeout=config["connect_timeout"],
        read_timeout=config["read_timeout"],
        retry_max_attempts=config["retry_max_attempts"],
        retry_backoff_factor=config["retry_backoff_factor"],
        retry_max_delay=config["retry_max_delay"],
        circuit_breaker_threshold=config["circuit_breaker_threshold"],
        circuit_breaker_cooldown=config["circuit_breaker_cooldown"],
        on_connection_weak=on_network_connection_weak,
        on_connection_lost=on_network_connection_lost,
        on_connection_restored=on_network_connection_restored,
        on_circuit_breaker_open=on_circuit_breaker_open,
        on_circuit_breaker_close=on_circuit_breaker_close
    )

    recovery_manager = RecoveryManager(
        modem_reboot_callback=reboot_modem
    )

    log_event("Network client initialized with keepalive and exponential backoff")

def check_prolonged_no_connection():
    """Check if we've been in NO_CONNECTION state too long and trigger modem reboot."""
    global no_connection_since, last_modem_reboot_attempt

    # Don't trigger if modem is already rebooting
    with state_lock:
        current_state = state

    if current_state == State.MODEM_REBOOTING:
        return

    # Only check if we're in NO_CONNECTION state
    if current_state != State.NO_CONNECTION or no_connection_since is None:
        return

    # Calculate how long we've been disconnected
    disconnected_duration = time.time() - no_connection_since

    # Check if we've been disconnected for more than 60 seconds
    if disconnected_duration > 60:
        # Skip modem reboot if connected via wifi - modem isn't the active connection
        if get_connection_type() == "wifi":
            log_event(f"NO_CONNECTION for {disconnected_duration:.0f}s, but connected via wifi. Skipping modem reboot.")
            return

        # Check cooldown - don't reboot more than once per 5 minutes
        if last_modem_reboot_attempt is not None:
            time_since_last_reboot = time.time() - last_modem_reboot_attempt
            if time_since_last_reboot < 300:  # 5 minutes cooldown
                log_event(f"NO_CONNECTION for {disconnected_duration:.0f}s, but modem was rebooted {time_since_last_reboot:.0f}s ago. Waiting...")
                return

        log_event(f"NO_CONNECTION persisted for {disconnected_duration:.0f}s - triggering modem reboot")
        last_modem_reboot_attempt = time.time()
        no_connection_since = None  # Reset timer to prevent immediate re-trigger
        reboot_modem()

def reboot_modem():
    global state, state_before_connection_issue

    # Save current state before modem reboot
    with state_lock:
        state_before_modem_reboot = state
        state = State.MODEM_REBOOTING

    try:
        log_event("Rebooting modem...")

        with Connection(config["modem_gateway_url"]) as connection:
            client = Client(connection)
            if client.device.reboot() == ResponseEnum.OK.value:
                log_event("Modem reboot requested successfully.")

                # Wait for modem to fully boot up before resuming operations
                modem_boot_time = config["modem_boot_time"]
                log_event(f"Waiting {modem_boot_time}s for modem to boot...")
                time.sleep(modem_boot_time)
                log_event("Modem should be ready now")

                # Restore previous state
                with state_lock:
                    state = state_before_modem_reboot
            else:
                log_error("Modem reboot failed.")
                with state_lock:
                    state = state_before_modem_reboot
    except Exception as e:
        log_error(f"Error rebooting modem: {e}")
        with state_lock:
            state = state_before_modem_reboot

def init_config():
    global config

    if not os.path.exists(CONFIG_FILE):
        log_event(f"Rename and edit one of the provided config files to config.json and edit token, then start program again.")
        exit()
    
    load_config_file()

    if config["printer_token"] == "<TOKEN>":
        log_error("Please enter a valid printer token in the config file and restart.")
        exit()

def load_config_file():
    global config
    with open(CONFIG_FILE, 'r') as f:
        config_dict = json.load(f)
    config.update_from_dict(config_dict)

def update_config():
    global state
    log_event("Pulling config...")
    if state in [State.BOOTING, State.NO_CONNECTION]:
        headers = getInitialHeaders()
        log_event("Using initial headers only")
    else:
        headers = getHeaders()
        log_event("Using full headers")

    try:
        response = network_client.get(
            config["url"] + config["config_url"],
            headers=headers,
            max_attempts=5  # Use more attempts for config fetch
        )
        if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type', ''):
            log_event("Config retrieved")
            data = response.json()
            if check_config(data):
                with open(CONFIG_FILE, "w") as f:
                    json.dump(data, f, indent=4)
                # Server config overrides all, then defaults fill in any missing fields
                config.update_from_dict(data)
                log_event("Config updated")
            else:
                log_error("Pulled config doesn't pass integrity check, not using it.")
        else:
            log_error(f"Error: {response.status_code}")
        state = State.BOOTING
    except Exception as e:
        log_error(f"Failed to update config after retries: {e}")
        state = State.NO_CONNECTION

def check_config(data):
    if not isinstance(data, dict):
        return False

    token = data.get("printer_token")
    if not isinstance(token, str):
        return False

    if len(token) != 32:
        return False

    return token.isalnum()

def get_default_routes() -> list[Route]:
    """Get all default routes, sorted by preference (lowest metric first)."""
    routes = []
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True
        )
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split()
            
            iface = parts[parts.index("dev") + 1] if "dev" in parts else None
            metric = int(parts[parts.index("metric") + 1]) if "metric" in parts else 0
            gateway = parts[parts.index("via") + 1] if "via" in parts else None
            
            if iface:
                routes.append(Route(iface, metric, gateway))
    except Exception:
        pass
    
    return sorted(routes, key=lambda r: r.metric)

def get_connection_type():
    routes = get_default_routes()
    if not routes:
        return "none"
    
    iface = routes[0].interface  # Lowest metric = active
    
    if iface == "wlan0":
        return "wifi"
    elif iface in ("ppp0", "wwan0", "usb0"):
        return "lte"
    return "unknown"

def getInitialHeaders():
    with state_lock:
        if state in [State.OUT_OF_INK, State.OUT_OF_PAPER, State.OUT_OF_INK_AND_PAPER, State.PAPER_JAM, State.BOOTING, State.WAITING_FOR_CUPS, State.ACKNOWLEDGING, State.PRINTER_UNREACHABLE]:
            status = state.value
        else:
            status = "Normal"

    headers = {
        "Authorization": config["printer_token"],
        "X-Printer-Status": status
    }

    return headers

def getHeaders():
    """
    Get headers with modem signal data.

    Attempts to read modem data once with timeout. Falls back to basic headers
    if modem is unavailable.

    Returns:
        dict: Headers dictionary with or without modem data
    """

    connection_type = get_connection_type()

    if connection_type == "wifi":
        log_event("Using wifi, sending basic headers...")
        return getInitialHeaders()
    
    # Try to read modem data once with timeout (no retries)
    data = None
    timeout_seconds = 2

    def _read_modem_data():
        """Helper function to read modem data (for timeout wrapping)."""
        with HuaweiModemReader(config["modem_gateway_url"], timeout=timeout_seconds) as reader:
            return reader.get_signal_data()

    try:
        # Execute modem read with timeout using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_read_modem_data)
            data = future.result(timeout=timeout_seconds)
    except FuturesTimeoutError:
        log_error("Modem read timeout - falling back to basic headers")
    except Exception as e:
        log_error(f"Modem read error: {e} - falling back to basic headers")

    # If modem data unavailable, fall back to basic headers
    if data is None:
        log_error("Modem unavailable - using basic headers without signal data")
        return getInitialHeaders()

    # Got modem data successfully - build full headers
    with state_lock:
        if state in [State.OUT_OF_INK, State.OUT_OF_PAPER, State.OUT_OF_INK_AND_PAPER, State.PAPER_JAM, State.BOOTING, State.WAITING_FOR_CUPS, State.ACKNOWLEDGING, State.PRINTER_UNREACHABLE]:
            status = state.value
        else:
            status = "Normal"

    headers = {
        "Authorization": config["printer_token"],
        "X-Modem-Operator": data["operator_name"],
        "X-Modem-Network-Mode": data["network_mode"],
        "X-Modem-RSRP": str(data["rsrp"]),
        "X-Modem-RSRQ": str(data["rsrq"]),
        "X-Modem-SINR": str(data["sinr"]),
        "X-Modem-Download-Speed": str(last_download_speed),
        "X-Printer-Status": status
    }

    return headers

def check_for_new_messages():
    global last_successful_request, state
    print("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] Checking for new messages...")

    # Cache headers once to avoid multiple modem reads on retries
    cached_headers = getHeaders()

    try:
        response = network_client.get(
            config["url"] + config["request_url"],
            headers=cached_headers,
            max_attempts=5  # More attempts for polling endpoint
        )
        if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type', ''):
            log_event("New message found")
            handle_message(config, response.json())
        elif response.status_code == 201:
            # log_event("No new messages found")
            print("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] No new messages found")
        else:
            log_error(f"Error: {response.status_code}")
        last_successful_request = time.time()
        with state_lock:
            if state != State.MESSAGE_RECEIVED:
                state = State.IDLE
    except Exception as e:
        log_error(f"Failed to check for new messages: {e}")
        with state_lock:
            state = State.NO_CONNECTION



def handle_message(config, data):
    global state
    with state_lock:
        state = State.INCOMING_TRANSMISSION

    def handle_transmission_failure():
        global state
        with state_lock:
            if state != State.MESSAGE_RECEIVED:
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
        with state_lock:
            state = State.MESSAGE_RECEIVED
        with pending_ids_lock:
            pending_message_ids.append(message_id)
            save_pending_collections()
        ack_complete_event = threading.Event()
        flag_thread = threading.Thread(target=raise_flag, args=(ack_complete_event,), daemon=True)
        flag_thread.start()
        ack_message(message_id)
        ack_complete_event.set()  # Signal that ACK is complete
    else:
        log_error("Print tracking failed. Not raising flag. Ack'ing message")
        handle_transmission_failure()
        ack_message(message_id)

def get_image(config, message_id):
    log_event("Getting image...")
    # Cache headers once to avoid multiple modem reads on retries
    cached_headers = getHeaders()
    try:
        response = network_client.get_streaming(
            f"{config['url']}{config['image_url']}/{message_id}",
            headers=cached_headers,
            max_attempts=3  # Fewer attempts for large downloads
        )
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
    except Exception as e:
        log_error(f"Failed to get image after retries: {e}")
        return None

def save_image(config, response, message_id):
    global last_download_speed
    try:
        mime = response.headers['Content-Type'].split('/')[1]
        filename = generate_file_name(config["image_path"], mime)
        os.makedirs(config["image_path"], exist_ok=True)
        image_path = os.path.join(config["image_path"], filename)
        
        with open(image_path, 'wb') as f:
            start = time.time()
            total_bytes = 0
            chunks = []
            for chunk in response.iter_content(1024):
                chunks.append(chunk)
                total_bytes += len(chunk)
                f.write(chunk)
            elapsed = time.time() - start
            speed_kbps = (total_bytes / 1024) / elapsed
            last_download_speed = math.floor(speed_kbps)
        
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
    global state
    log_event(f"Acknowledging message ID: {message_id}")

    # Cache headers once to avoid multiple modem reads on retries
    cached_headers = getHeaders()

    def try_ack():
        """Try to send acknowledgment using cached headers"""
        try:
            response = network_client.post(
                f"{config['url']}{config['ack_url']}?message_id={message_id}",
                headers=cached_headers,
                max_attempts=config["retry_critical_attempts"]
            )
            log_event(response.text)
            return True
        except Exception as e:
            log_error(f"Failed to acknowledge message: {e}")
            return False

    # Try to send ack
    success = try_ack()

    if not success:
        # Critical failure - escalate recovery
        log_error(f"CRITICAL: Failed to acknowledge message {message_id} after all retries")

        ack_data = {
            'url': f"{config['url']}{config['ack_url']}?message_id={message_id}",
            'message_id': message_id
        }

        # Skip recovery escalation if connected via wifi - modem reboot won't help
        if get_connection_type() == "wifi":
            log_event(f"Ack message {message_id} failed but connected via wifi. Skipping modem reboot.")
            return

        # Use recovery manager to handle escalation
        recovery_manager.handle_critical_failure(
            operation_name="ack_message",
            ack_id=str(message_id),
            ack_data=ack_data,
            retry_callback=try_ack
        )

def send_status():
    log_event(f"Sending printer status - {state} - to server.")
    # Cache headers once to avoid multiple modem reads on retries
    cached_headers = getHeaders()
    try:
        response = network_client.get(
            f"{config['url']}{config['auth_check_url']}",
            headers=cached_headers,
            max_attempts=3
        )
        log_event(response.text)
    except Exception as e:
        log_error(f"Failed to send printer status to server: {e}")

def check_printer_reachable():
    global state
    status_sent = False
    with state_lock:
        previous_state = state
    while True:
        try:
            result = subprocess.run(
                ["lsusb"], capture_output=True, text=True, timeout=5
            )
            is_reachable = "cp1500" in result.stdout.lower()
        except Exception:
            is_reachable = False

        if is_reachable:
            if status_sent:
                with state_lock:
                    state = previous_state
                send_status()
                log_event("Printer USB device detected again")
            return True

        with state_lock:
            if state != State.PRINTER_UNREACHABLE:
                state = State.PRINTER_UNREACHABLE
        if not status_sent:
            log_error("Printer unreachable: CP1500 not found in lsusb output")
            send_status()
            status_sent = True
        else:
            log_event("Waiting for printer USB device...")
        time.sleep(5)

def print_image(image_path):
    log_event("Printing image...")
    if not os.path.exists(image_path):
        log_error("Image file not found, skipping print...")
        return None

    if cupsConn is None:
        log_error("CUPS connection not initialized, cannot print.")
        return None

    check_printer_reachable()

    # Check if no_print mode is enabled (for testing without wasting supplies)
    if config.get("no_print", False):
        log_event("[NO_PRINT MODE] Simulating print without actually printing")
        with Image.open(image_path) as img:
            width, height = img.size
            is_landscape = width > height
        print(f"Image size: {width}x{height} ({'landscape' if is_landscape else 'portrait'})")
        log_event("[NO_PRINT MODE] ✓ Simulated job submitted (no actual print)")
        return -1  # Return fake job_id to indicate simulated print

    printer_name = config["printer_name"]
    # Detect orientation
    with Image.open(image_path) as img:
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

    # Check if this is a simulated print (no_print mode)
    if job_id == -1:
        log_event("[NO_PRINT MODE] Simulating print tracking - returning success immediately")
        time.sleep(2)  # Simulate brief delay as if tracking
        log_event("[NO_PRINT MODE] ✓ Simulated job completed successfully")
        return True

    if cupsConn is None:
        log_error("CUPS connection not initialized, cannot track print job.")
        return False

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
                            with state_lock:
                                state = State.OUT_OF_INK_AND_PAPER
                        elif "media-empty-error" in reasons:
                            current_error = "Out of paper"
                            with state_lock:
                                state = State.OUT_OF_PAPER
                        elif "marker-supply-empty-error" in reasons:
                            current_error = "Out of ink or ink cartridge missing"
                            with state_lock:
                                state = State.OUT_OF_INK
                        elif "input-tray-missing" in reasons:
                            current_error = "Paper casette missing or incorrectly inserted"
                            with state_lock:
                                state = State.OUT_OF_PAPER
                        elif "media-jam-error" in reasons:
                            current_error = "Paper jam"
                            with state_lock:
                                state = State.PAPER_JAM
                        if current_error is not None and last_error != current_error:
                            log_error(current_error)
                            last_error = current_error
                            send_status()
                    elif last_error is not None:
                        with state_lock:
                            state = State.INCOMING_TRANSMISSION
                        last_error = None
                        send_status()

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

        except Exception as e:
            log_error(f"Error tracking job: {e}")
            traceback.print_exc()
            return False
    

def raise_flag(ack_complete_event):
    global flag_raised, state
    with flag_lock:
        if flag_raised:
            log_error("Flag already raised, skipping.")
            return

    log_event("Raising flag.")

    try:
        log_event("Raising flag...")
        # Set flag_raised BEFORE moving servo to avoid race condition
        # (button press during servo movement would otherwise be missed)
        with flag_lock:
            flag_raised = True
            button_press_event.clear()

        set_servo_angle(config["flag_up_angle"])

        log_event("Waiting for button press...")
        log_event(f"[DEBUG] About to wait, event_is_set={button_press_event.is_set()}")
        button_press_event.wait()
        log_event(f"[DEBUG] Wait completed, event_is_set={button_press_event.is_set()}")

        log_event("Lowering flag...")
        set_servo_angle(config["flag_down_angle"])
        log_verbose("Servo lowered, acquiring flag_lock...")

        with flag_lock:
            flag_raised = False

        log_verbose("flag_raised set to False, checking ack_complete_event...")

        # Check if ACK is still in progress
        if not ack_complete_event.is_set():
            log_verbose("ACK still in progress, entering ACKNOWLEDGING state...")
            with state_lock:
                state = State.ACKNOWLEDGING
            log_event("Waiting for acknowledgment to complete...")
            if not ack_complete_event.wait(timeout=1500):  # 25 minutes = 1500 seconds
                log_error("Acknowledgment took longer than 25 minutes - proceeding anyway to prevent infinite hang")
        else:
            log_verbose("ACK already complete, skipping wait")

        log_verbose("Acquiring pending_ids_lock to reset pending ids...")
        with pending_ids_lock:
            ids_to_send = list(pending_message_ids)
            pending_message_ids.clear()
            save_pending_collections()

        if ids_to_send:
            log_verbose("Sending collection events for pending ids")
            send_collection_event(ids_to_send)

        log_verbose("Acquiring state_lock to reset state...")

        # ACK is done, reset state
        with state_lock:
            log_verbose(f"Current state: {state}")
            if state != State.INCOMING_TRANSMISSION:
                state = State.IDLE
                log_verbose("State set to IDLE")

        log_verbose("raise_flag completed successfully")
    except Exception as e:
        log_error(f"Error in raise_flag: {e}")
        with state_lock:
            if state != State.INCOMING_TRANSMISSION:
                state = State.IDLE
        with flag_lock:
            flag_raised = False

def send_collection_event(message_ids):
    if not config["collection_notifications"]:
        log_verbose(f"Collection notifications disabled, skipping {len(message_ids)} message(s)")
        return
    log_event(f"Sending collection event for {len(message_ids)} message(s):{message_ids}")
    # Cache headers once to avoid multiple modem reads on retries
    cached_headers = getHeaders()
    try:
        response = network_client.post(
            f"{config['url']}{config['collection_url']}",
            headers=cached_headers,
            json={"message_ids": message_ids},
            max_attempts=3
        )
        log_event(response.text)
    except Exception as e:
        log_error(f"Failed to send printer status to server: {e}")

def generate_file_name(directory, mime):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"{timestamp}.{mime}"

def init_servo():
    global servo
    servo = AngularServo(config["servo_pin"], min_angle=0, max_angle=180, min_pulse_width=0.5/1000, max_pulse_width=2.5/1000)
    servo.angle = config["flag_down_angle"]
    time.sleep(1)
    servo.detach()

def set_servo_angle(angle):
            global servo
            log_verbose(f"set_servo_angle called with angle={angle}")

            if servo is None:
                log_error("Servo not initialized, cannot set angle.")
                return

            log_verbose(f"Setting servo.angle to {angle}")
            servo.angle = angle

            log_verbose("Sleeping 1 second...")
            time.sleep(1)

            log_verbose("Detaching servo...")
            servo.detach()

            log_verbose("set_servo_angle completed")

# Function to control LED color with PWM (0.0-1.0 for each channel)
def set_led_color(red, green, blue):
    led_red.value = red
    led_green.value = green
    led_blue.value = blue

# Function to update LED status based on flag state
def update_led_status():
    global flag_raised
    while True:
        with state_lock:
            current_state = state

        match current_state:
            case State.IDLE:
                set_led_color(0, 1, 0)  # Green
            case State.INCOMING_TRANSMISSION:
                set_led_color(0, 0, 1)  # Blue
            case State.MESSAGE_RECEIVED:
                set_led_color(0, 1, 1)  # Cyan
            case State.ACKNOWLEDGING:
                set_led_color(1, 1, 1)  # White
            case State.OUT_OF_INK:
                set_led_color(1, 0, 0)  # Red
            case State.OUT_OF_PAPER:
                set_led_color(1, 0, 0)  # Red
            case State.OUT_OF_INK_AND_PAPER:
                set_led_color(1, 0, 0)  # Red
            case State.PAPER_JAM:
                set_led_color(1, 0, 0)  # Red
            case State.WAITING_FOR_CUPS:
                set_led_color(1, 0, 1)  # Magenta
            case State.CONNECTION_WEAK:
                set_led_color(1, 0.3, 0)  # Orange (network issues, retrying)
            case State.NO_CONNECTION:
                set_led_color(1, 0, 0)  # Red (connection lost)
            case State.CIRCUIT_BREAKER_OPEN:
                set_led_color(0.2, 0.8, 1)  # Light blue (server down, circuit breaker open)
            case State.MODEM_REBOOTING:
                set_led_color(0.8, 0, 1)  # Purple (modem rebooting)
            case State.PRINTER_UNREACHABLE:
                set_led_color(1, 0, 0)  # Red
            case State.BOOTING:
                set_led_color(1, 1, 0)  # Yellow (initializing)
            case _:
                set_led_color(0, 0, 0)  # Off (unknown state)

        time.sleep(0.5)

# Function to control paper LED color with PWM (0.0-1.0 for each channel)
def set_paper_led_color(red, green, blue):
    paper_led_red.value = red
    paper_led_green.value = green
    paper_led_blue.value = blue

# Function to update LED status based on flag state
def update_paper_led_status():
    while True:
        set_paper_led_color(1, 0, 0)
        time.sleep(0.5)

def init_led():
    global led_red, led_green, led_blue
    led_red = PWMLED(config["led_pins"]["red"])
    led_green = PWMLED(config["led_pins"]["green"])
    led_blue = PWMLED(config["led_pins"]["blue"])

    led_thread = threading.Thread(target=update_led_status, daemon=True)
    led_thread.start()

def init_paper_led():
    global paper_led_red, paper_led_green, paper_led_blue
    if config["paper_led"] == True:
        paper_led_red = PWMLED(config["paper_led_pins"]["red"])
        paper_led_green = PWMLED(config["paper_led_pins"]["green"])
        paper_led_blue = PWMLED(config["paper_led_pins"]["blue"])

        paper_led_thread = threading.Thread(target=update_paper_led_status, daemon=True)
        paper_led_thread.start()

        log_event("This model has an out-of-paper indicator light. It's been switched on.")
    else:
        log_event("This model does not have a paper indicator light.")

def on_button_pressed():
    """Handle button press - signal flag thread or send pending collections."""
    log_event("Door opened")
    with flag_lock:
        log_event(f"[DEBUG] flag_raised={flag_raised}, event_is_set={button_press_event.is_set()}")
        if flag_raised:
            # Signal the flag thread to continue
            log_event("Button press detected - signaling flag thread")
            button_press_event.set()
            log_event(f"[DEBUG] Event set, now is_set={button_press_event.is_set()}")
            return

    # Flag not raised, check for pending collections to send
    with pending_ids_lock:
        if not pending_message_ids:
            return
        ids_to_send = list(pending_message_ids)
        pending_message_ids.clear()
        save_pending_collections()

    log_event(f"Button pressed while flag down - sending {len(ids_to_send)} pending collection(s)")
    send_collection_event(ids_to_send)

def polling_button_handler():
    """Polling-based button detection (lgpio edge callbacks are broken)."""
    last_state = False
    last_trigger_time = 0
    debounce_seconds = 0.3  # Ignore presses within 300ms of last trigger

    while True:
        current = button.is_pressed
        now = time.time()

        # Detect rising edge (button press) with debouncing
        if current and not last_state:
            if now - last_trigger_time >= debounce_seconds:
                last_trigger_time = now
                on_button_pressed()

        last_state = current
        time.sleep(0.05)  # 50ms polling interval

def init_GPIO():
    global button
    # Note: gpiozero's when_pressed callback doesn't work with lgpio pin factory
    # Using polling-based detection instead
    button = Button(config["button_pin"], pull_up=True)

    # Start polling-based handler (gpiozero edge callbacks broken with lgpio)
    polling_thread = threading.Thread(target=polling_button_handler, daemon=True)
    polling_thread.start()

def check_for_new_commands():
    global last_successful_command_request
    # Cache headers once to avoid multiple modem reads on retries
    cached_headers = getHeaders()
    try:
        response = network_client.get(
            config["url"] + config["command_url"],
            headers=cached_headers,
            max_attempts=5
        )
        if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type', ''):
            log_event("New command found")
            dispatchCommand(response.json())
        # elif response.status_code == 201:
        #     print("No new commands found")
        #     print("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] No new messages found")
        elif response.status_code != 201:
            log_error(f"Command check error: {response.status_code}")
        last_successful_command_request = time.time()
    except Exception as e:
        log_error(f"Failed to check for new commands: {e}")
       
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

    # Cache headers once to avoid multiple modem reads on retries
    cached_headers = getHeaders()

    def try_ack():
        """Try to send command acknowledgment using cached headers"""
        try:
            response = network_client.post(
                f"{config['url']}{config['command_ack_url']}?command_id={command_id}",
                headers=cached_headers,
                max_attempts=config["retry_critical_attempts"]
            )
            log_event(response.text)
            return True
        except Exception as e:
            log_error(f"Failed to acknowledge command: {e}")
            return False

    # Try to send ack
    success = try_ack()

    if not success:
        # Critical failure - escalate recovery
        log_error(f"CRITICAL: Failed to acknowledge command {command_id} after all retries")

        ack_data = {
            'url': f"{config['url']}{config['command_ack_url']}?command_id={command_id}",
            'command_id': command_id
        }

        # Skip recovery escalation if connected via wifi - modem reboot won't help
        if get_connection_type() == "wifi":
            log_event(f"Ack command {command_id} failed but connected via wifi. Skipping modem reboot.")
            return

        # Use recovery manager to handle escalation
        recovery_manager.handle_critical_failure(
            operation_name="ackCommand",
            ack_id=str(command_id),
            ack_data=ack_data,
            retry_callback=try_ack
        )

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
    global cupsConn, state
    log_event(f"Waiting {config['initial_delay']}s before connecting to cups")
    with state_lock:
        state = State.WAITING_FOR_CUPS
    time.sleep(config["initial_delay"])
    start = time.time()
    attempt = 1
    state_sent = False
    while True:
        try:
            cupsConn = cups.Connection()
            cupsConn.getPrinters()
            log_event("CUPS connected")
            with state_lock:
                state = State.BOOTING
            if state_sent:
                send_status()
            return True
        except RuntimeError as e:
            print(f"Connection failed. {time.time() - start}s since start. Attempt [{attempt + 1}/{config['cups_retries']}]")
            if not state_sent:
                send_status()
                state_sent = True
            time.sleep(1)
            if time.time() - start > 300:
                log_error("Cups hasn't started in 5 minutes. Rebooting...")
                reboot()
            attempt += 1

if __name__ == "__main__":
    log_event(f"GPK {VERSION} started")
    init_config()
    log_event("conf loaded from file")
    init_network_client()
    log_event("Network client initialized")
    load_pending_collections()
    log_event("Pending collections loaded")
    init_GPIO()
    log_event("GPIO initialized")
    init_led()
    init_paper_led()
    log_event("LEDs initialized")
    update_config()
    log_event("conf updated from server")
    init_servo()
    log_event("Servo initialized")
    init_CUPS()
    log_event("CUPS initialized")
    check_printer_reachable()
    log_event("Printer reachable")
    with state_lock:
        state = State.IDLE
    consecutive_errors = 0
    max_consecutive_errors = config["max_consecutive_errors"]
    while True:
        try:
            # check_supply_levels()
            # Check for commands first, then messages
            check_for_new_commands()
            check_for_new_messages()
            time.sleep(config["check_interval"])
            consecutive_errors = 0  # Reset on success
        except KeyboardInterrupt:
            log_event("Keyboard interrupt received, shutting down gracefully...")
            break
        except Exception as e:
            consecutive_errors += 1
            log_error(f"Unhandled error ({consecutive_errors}/{max_consecutive_errors}): {e}")
            traceback.print_exc()
            if consecutive_errors >= max_consecutive_errors:
                log_error(f"Too many consecutive errors ({max_consecutive_errors}), shutting down...")
                break
            time.sleep(5)  # Wait before retrying after error
