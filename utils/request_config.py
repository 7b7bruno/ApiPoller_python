import json
import requests

TOKEN = ""
URL = ""
CONFIG_ENDPOINT = ""

def update_config():
    headers = {
        "Authorization": TOKEN,
    }
    retries = 5
    while retries > 0:
        try:
            response = requests.get(URL + CONFIG_ENDPOINT, headers=headers, timeout=30)
            if response.status_code == 200 and 'application/json' in response.headers.get('Content-Type', ''):
                print("Config retrieved")
                config = response.json()
                
            elif response.status_code == 201:
                # log_event("No new messages found")
                print("unknown response")
            else:
                print(f"Error: {response.status_code}")
                retries -= 1
            break
        except requests.exceptions.RequestException as e:
            request_timeout_interval = 30
            print(f"Connection lost: {e}. Retrying in {request_timeout_interval} seconds...")
            retries -= 1
    return config

if __name__ == "__main__":
    data = update_config()
    with open("config.json", "w") as file:
        json.dump(data, file, indent=4)