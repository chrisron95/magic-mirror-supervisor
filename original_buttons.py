import subprocess
from gpiozero import Button
from signal import pause
import os
import re
import time
import threading
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TV:
    def __init__(self, address):
        self.address = address
        self.is_on = self.check_power_status()
        logging.info(f"TV initialized. Power: {'ON' if self.is_on else 'OFF'}")

        # Set internal input to a default before checking the actual input
        self.internal_input = "Unknown"

        # Start the input check in a separate thread so buttons remain responsive
        self.input_thread = threading.Thread(target=self.initialize_input, daemon=True)
        self.input_thread.start()

    def initialize_input(self):
        """Background thread to check initial TV input."""
        detected_input = self.get_active_source()

        if detected_input == "Unknown":
            logging.warning("TV input is 'unknown' on startup, switching to rPi")
            self.internal_input = "rPi"
            self.set_input("rPi")
        else:
            logging.info(f"TV input detected on startup: {detected_input}")
            self.internal_input = detected_input  # Save valid input

    def check_power_status(self):
        """Check if the TV is on or in standby."""
        try:
            output = subprocess.run(
                f"echo 'pow {self.address}' | cec-client -s -d 1",
                shell=True, capture_output=True, text=True, check=True
            ).stdout.lower()

            logging.info(f"Checking TV power status... Raw output: {output.strip()}")

            if "power status: on" in output:
                return True
            elif "power status: standby" in output or "power status: in transition from standby to on" in output:
                return False
            else:
                logging.warning("Unexpected power status response, assuming TV is OFF.")
                return False
        except subprocess.CalledProcessError as e:
            logging.error(f"Error checking power status: {e}")
            return False

    def toggle_power(self):
        """Toggle the power state of the TV."""
        if self.is_on:
            logging.info("TV is ON, sending standby command...")
            self.standby()
        else:
            logging.info("TV is OFF, sending power on command...")
            self.power_on()

    def power_on(self):
        """Turn on the TV, then confirm it actually turned on."""
        if self.is_on:
            logging.info("Power-on request ignored: TV is already ON.")
            return
        
        logging.info("Sending power-on command to TV...")
        subprocess.run(f"echo 'on {self.address}' | cec-client -s -d 1", shell=True)
        self.is_on = self.check_power_status()

    def standby(self):
        """Put the TV into standby mode."""
        if not self.is_on:
            logging.info("Standby request ignored: TV is already OFF.")
            return
        
        logging.info("Turning off TV (standby mode)...")
        subprocess.run(f"echo 'standby {self.address}' | cec-client -s -d 1", shell=True)
        self.is_on = self.check_power_status()

    def get_active_source(self):
        """Retrieve and track the currently active HDMI input source."""
        try:
            output = subprocess.run("echo 'scan' | cec-client -s -d 1", shell=True, capture_output=True, text=True).stdout

            match = re.search(r"currently active source:\s*(.+)", output)

            if match:
                source_info = match.group(1).strip()
                
                if "unknown (-1)" in source_info or "TV" in source_info:
                    logging.warning(f"TV reports 'unknown (-1)', keeping last known input: {self.internal_input}")
                    return "Unknown"

                match_device = re.search(r"device #(\d+):\s*([^\n]+)", output)
                if match_device:
                    device_id = match_device.group(1)
                    device_name = match_device.group(2).strip()
                    detected_input = f"HDMI {device_id} ({device_name})"
                    self.internal_input = detected_input  # Update internal input
                    logging.info(f"TV detected real input: {detected_input}")
                    return detected_input

            return "Unknown"
        
        except subprocess.CalledProcessError as e:
            logging.error(f"Error getting active source: {e}")
            return "Error"

    def set_input(self, desired_source):
        """Change the TV input to a specified source and confirm the switch."""
        input_map = {
            'rPi': "tx 1F:82:20:00",
            'hdmi': "tx 1F:82:30:00"
        }
        if desired_source in input_map:
            logging.info(f"Switching TV input to {desired_source}")
            subprocess.run(f"echo '{input_map[desired_source]}' | cec-client -s -d 1", shell=True)

            # Set internal state before waiting for confirmation
            self.internal_input = desired_source
            self.wait_for_input_switch(desired_source)

    def wait_for_input_switch(self, desired_source, timeout=10, interval=2):
        """Poll the input status every `interval` seconds until `timeout` is reached."""
        logging.info(f"Waiting for TV to switch to {desired_source}...")
        elapsed = 0
        while elapsed < timeout:
            time.sleep(interval)
            elapsed += interval
            detected_input = self.get_active_source()

            # If a device is plugged in, use detected input
            if detected_input != "Unknown" and detected_input != "rPi":
                logging.info(f"TV detected real input: {detected_input}")
                return True

            # If nothing is plugged in, fall back to internal tracking
            if self.internal_input == desired_source:
                logging.info(f"TV successfully switched to {desired_source} after {elapsed} seconds.")
                return True

        logging.warning(f"TV input switch to {desired_source} timed out after {timeout} seconds. Keeping last attempted input: {desired_source}")
        self.internal_input = desired_source  # Ensure the script doesn't get stuck
        return False

    def rotate_input(self):
        """Toggle between rPi and HDMI input."""
        logging.info(f"Rotating TV input. Current: {self.internal_input}")
        new_input = 'hdmi' if self.internal_input == 'rPi' else 'rPi'
        self.set_input(new_input)

class ButtonHandler:
    def __init__(self, name, pin, press_callback=None, hold_callback=None, hold_time=1):
        self.name = name
        self.button = Button(pin, bounce_time=0.05, hold_time=hold_time)
        self.was_held = False  # Prevent press event after hold

        if press_callback:
            self.button.when_released = self._wrap_press(press_callback)  # Register single press **only after release**
        if hold_callback:
            self.button.when_held = self._wrap_hold(hold_callback)  # Register hold and suppress single press

    def _wrap_press(self, callback):
        def wrapped():
            if not self.was_held:
                logging.info(f"{self.name} pressed (single)")
                callback()
            self.was_held = False  # Reset flag after press
        return wrapped

    def _wrap_hold(self, callback):
        def wrapped():
            logging.info(f"{self.name} held")
            self.was_held = True  # Mark as held to suppress single press
            callback()
        return wrapped

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
