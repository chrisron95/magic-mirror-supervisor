import subprocess
import re
import time
import threading
import logging

class TV:
    def __init__(self, address, ha_client):
        self.address = address
        self.ha_client = ha_client
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
        self.update_input()  # Update Home Assistant with the detected input

    def check_power_status(self):
        """Check if the TV is on or in standby and update Home Assistant."""
        power_status = False  # Default to False (TV is off)
        try:
            output = subprocess.run(
                f"echo 'pow {self.address}' | cec-client -s -d 1",
                shell=True, capture_output=True, text=True, check=True
            ).stdout.lower()

            logging.info(f"Checking TV power status... Raw output: {output.strip()}")

            if "power status: on" in output:
                power_status = True
            elif "power status: standby" in output or "power status: in transition from standby to on" in output:
                power_status = False
            else:
                logging.warning("Unexpected power status response, assuming TV is OFF.")
                power_status = False
        except subprocess.CalledProcessError as e:
            logging.error(f"Error checking power status: {e}")
            power_status = False

        # Update the Home Assistant switch with the power status
        if self.ha_client:
            self.ha_client.update_switch("tv_power", "ON" if power_status else "OFF")

        # Store the power status in the instance variable
        self.is_on = power_status

        return power_status

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

        # Poll the power status until the TV is ON or timeout is reached
        timeout = 60  # 1 minute
        interval = 2
        elapsed = 0

        while elapsed < timeout:
            time.sleep(interval)
            elapsed += interval
            if self.check_power_status():
                logging.info("TV successfully powered ON.")
                return

        logging.error("Failed to power ON the TV within the timeout period.")

    def standby(self):
        """Put the TV into standby mode, then confirm it actually turned off."""
        if not self.is_on:
            logging.info("Standby request ignored: TV is already OFF.")
            return

        logging.info("Turning off TV (standby mode)...")
        subprocess.run(f"echo 'standby {self.address}' | cec-client -s -d 1", shell=True)

        # Poll the power status until the TV is OFF or timeout is reached
        timeout = 60  # 1 minute
        interval = 5
        elapsed = 0

        while elapsed < timeout:
            time.sleep(interval)
            elapsed += interval
            if not self.check_power_status():
                logging.info("TV successfully entered standby mode.")
                return

        logging.error("Failed to put the TV into standby mode within the timeout period.")

    def get_active_source(self):
        """Retrieve and track the currently active HDMI input source."""
        try:
            output = subprocess.run("echo 'scan' | cec-client -s -d 1", shell=True, capture_output=True, text=True).stdout

            match = re.search(r"currently active source:\s*(.+)", output)

            if match:
                source_info = match.group(1).strip()
                
                if "unknown (-1)" in source_info or "TV" in source_info:
                    logging.warning(f"TV reports 'unknown (-1)', keeping last known input: {self.internal_input}")
                    return self.internal_input  # Keep the last known input

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
        
        
    def update_input(self):
        """Return the currently set input source."""
        # self.get_active_source()  # Update internal input
        if self.ha_client:
            self.ha_client.update_sensor("tv_input", self.internal_input)
        return self.internal_input

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
            self.update_input()  # Update Home Assistant with the new input

    def set_input_rpi(self):
        """Set the TV input to the Raspberry Pi."""
        logging.info("Setting TV input to rPi...")
        self.set_input('rPi')
        
    def set_input_hdmi(self):
        """Set the TV input to HDMI."""
        logging.info("Setting TV input to HDMI...")
        self.set_input('hdmi')

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