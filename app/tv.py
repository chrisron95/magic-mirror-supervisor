import subprocess
import re
import time
import threading
import logging

class TV:
    CEC_TIMEOUT = 10  # seconds; prevents a hung cec-client from blocking button/MQTT threads indefinitely

    def __init__(self, address, ha_client):
        self.address = address
        self.ha_client = ha_client
        self.lock = threading.Lock()  # serializes power/input commands against overlapping button/MQTT triggers
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

    def _run_cec_command(self, cec_command, timeout=None):
        """Run a cec-client command, returning its stdout (empty string on failure/timeout)."""
        timeout = timeout or self.CEC_TIMEOUT
        try:
            return subprocess.run(
                f"echo '{cec_command}' | cec-client -s -d 1",
                shell=True, capture_output=True, text=True, timeout=timeout
            ).stdout
        except subprocess.TimeoutExpired:
            logging.error(f"cec-client command '{cec_command}' timed out after {timeout}s")
            return ""
        except subprocess.CalledProcessError as e:
            logging.error(f"cec-client command '{cec_command}' failed: {e}")
            return ""

    def check_power_status(self):
        """Check if the TV is on or in standby and update Home Assistant."""
        power_status = False  # Default to False (TV is off)
        output = self._run_cec_command(f"pow {self.address}").lower()

        logging.info(f"Checking TV power status... Raw output: {output.strip()}")

        if "power status: on" in output:
            power_status = True
        elif "power status: standby" in output or "power status: in transition from standby to on" in output:
            power_status = False
        else:
            logging.warning("Unexpected power status response, assuming TV is OFF.")
            power_status = False

        # Update the Home Assistant switch with the power status
        if self.ha_client:
            self.ha_client.update_switch("tv_power", "ON" if power_status else "OFF")
            self.ha_client.update_binary_sensor("tv_power", power_status)

        # Store the power status in the instance variable
        self.is_on = power_status

        return power_status

    def get_power_status(self):
        """Return the last-known power status without querying the TV again. cec-client
        re-initializes the whole CEC adapter on every invocation (often 10+ seconds), so
        entity setup uses this cached value (from the check_power_status() call already
        made in __init__) instead of paying that cost twice more at startup."""
        return self.is_on

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

        if not self.lock.acquire(blocking=False):
            logging.info("TV command already in progress, ignoring power-on request.")
            return

        try:
            logging.info("Sending power-on command to TV...")
            self._run_cec_command(f"on {self.address}")

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
        finally:
            self.lock.release()

    def standby(self):
        """Put the TV into standby mode, then confirm it actually turned off."""
        if not self.is_on:
            logging.info("Standby request ignored: TV is already OFF.")
            return

        if not self.lock.acquire(blocking=False):
            logging.info("TV command already in progress, ignoring standby request.")
            return

        try:
            logging.info("Turning off TV (standby mode)...")
            self._run_cec_command(f"standby {self.address}")

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
        finally:
            self.lock.release()

    def get_active_source(self):
        """Retrieve and track the currently active HDMI input source."""
        output = self._run_cec_command("scan")

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


    def update_input(self):
        """Return the currently set input source."""
        # self.get_active_source()  # Update internal input
        if self.ha_client:
            self.ha_client.update_sensor("tv_current_input", self.internal_input)
        return self.internal_input

    def set_input(self, desired_source):
        """Change the TV input to a specified source and confirm the switch."""
        input_map = {
            'rPi': "tx 1F:82:20:00",
            'hdmi': "tx 1F:82:30:00"
        }
        if desired_source not in input_map:
            return

        if not self.lock.acquire(blocking=False):
            logging.info(f"TV command already in progress, ignoring input switch to {desired_source}.")
            return

        try:
            logging.info(f"Switching TV input to {desired_source}")
            self._run_cec_command(input_map[desired_source])

            # Set internal state before waiting for confirmation
            self.internal_input = desired_source
            self.wait_for_input_switch(desired_source)
            self.update_input()  # Update Home Assistant with the new input
        finally:
            self.lock.release()

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

            if desired_source == "hdmi":
                # Only a real connected device becoming active confirms the switch to HDMI
                if detected_input not in ("Unknown", "rPi"):
                    logging.info(f"TV detected real input: {detected_input}")
                    return True
            elif self.internal_input == desired_source:
                # No device to detect for rPi, so fall back to internal tracking
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