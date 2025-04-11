import subprocess
import logging
import os
import yaml
from signal import pause
from tv import TV
from buttons import ButtonHandler
from utils import get_ip_address

# Load configuration from YAML files
with open('config.yaml', 'r') as config_file:
    config = yaml.safe_load(config_file)

with open('secrets.yaml', 'r') as secrets_file:
    secrets = yaml.safe_load(secrets_file)

with open('entities.yaml', 'r') as entities_file:
    entities = yaml.safe_load(entities_file)

# Configuration
LOG_LEVEL = getattr(logging, config['log_level'].upper(), logging.DEBUG)

# Logging configuration
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

def notify(title, message):
    """Send a notification to the desktop."""
    logging.info(f"Notification: {title} - {message}")
    subprocess.run(["notify-send", title, message, "--urgency=low"])

def switch_apps():
    """Switch between Magic Mirror and Kiosk applications."""
    services = {"kiosk": "magicmirror", "magicmirror": "kiosk"}
    
    active_service = next((s for s in services if subprocess.run(f"systemctl is-active --quiet {s}", shell=True).returncode == 0), None)
    new_service = services.get(active_service, "magicmirror")

    logging.info(f"Switching to {new_service.capitalize()}")
    notify("App Switching...", f"Starting {new_service.capitalize()}")

    if active_service:
        subprocess.run(f"sudo systemctl stop {active_service}.service", shell=True)

    subprocess.run(f"sudo systemctl start {new_service}.service", shell=True)

def app_selector():
    """Choose between Magic Mirror or Kiosk application."""
    process = subprocess.run([
        "notify-send", "Smart Mirror", "Choose an app to open:",
        "--action=mirror=Magic Mirror",  # Button 1
        "--action=kiosk=Home Assistant"   # Button 2
    ], capture_output=True, text=True)

    response = process.stdout.strip()
    logging.info(f"Starting {response.capitalize()}")
    notify("App Starting...", f"Starting {response.capitalize()}")
    if response == "mirror":
        os.system("sudo systemctl stop kiosk.service")
        os.system("sudo systemctl start magicmirror.service")
    elif response == "kiosk":
        os.system("sudo systemctl stop magicmirror.service")
        os.system("sudo systemctl start kiosk.service")

def stop_all_apps():
    """Stop both Magic Mirror and Kiosk services."""
    notify("Button Handler", "All applications stopped")
    for service in ["magicmirror", "kiosk"]:
        subprocess.run(f"sudo systemctl stop {service}.service", shell=True)

def shutdown():
    """Shut down the system."""
    logging.warning("Shutting down the system!")
    subprocess.run(["sudo", "shutdown", "-h", "now"])

def sample():
    notify("Hello", "You found the secret button")

# Initialize TV
tv = TV("0.0.0.0")

# Initialize Buttons with Logging
ButtonHandler("Button 1", 25, press_callback=tv.toggle_power, hold_callback=lambda: (tv.standby(), shutdown()))
ButtonHandler("Button 2", 24, press_callback=switch_apps, hold_callback=app_selector)
ButtonHandler("Button 3", 23, press_callback=stop_all_apps, hold_callback=tv.rotate_input)

pause()