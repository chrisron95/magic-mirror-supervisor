import os
import subprocess
import re
import time
import threading
import logging

from .process_utils import terminate_process_group

class TV:
    CEC_TIMEOUT = 10   # seconds, for most commands
    SCAN_TIMEOUT = 20  # "scan" walks the whole bus, so it needs more time
    POLL_INTERVAL = 60  # seconds between background power/input polls

    # Fallback if config.yaml doesn't declare tv_inputs. Physical CEC address per input.
    DEFAULT_INPUTS = {
        'rPi': {'name': 'Raspberry Pi', 'address': '2.0.0.0'},
        'hdmi': {'name': 'HDMI 3', 'address': '3.0.0.0'},
    }

    def __init__(self, address, ha_client, inputs=None):
        self.address = address
        self.ha_client = ha_client
        self.inputs = inputs or self.DEFAULT_INPUTS
        self.lock = threading.RLock()  # serializes all cec-client access

        # Currently-running cec-client process, so a user command can cancel it if it's
        # just the background poll (see _acquire_for_command). Own lock since it's read
        # from a thread that doesn't hold self.lock.
        self._current_process = None
        self._current_is_background = False
        self._current_op_lock = threading.Lock()

        self.is_on = False
        self.power_thread = threading.Thread(target=self.initialize_power_status, daemon=True)
        self.power_thread.start()

        self.internal_input = "Unknown"
        self._hdmi_label = self.inputs['hdmi']['name']

        self.input_thread = threading.Thread(target=self.initialize_input, daemon=True)
        self.input_thread.start()

        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        """Catches input/power changes made via the TV's own remote."""
        while True:
            time.sleep(self.POLL_INTERVAL)
            self.check_power_status()
            if self.is_on:
                # One scan shared by both lookups below, not two separate cec-client calls.
                output = self._run_cec_command("scan", timeout=self.SCAN_TIMEOUT, background=True)
                self._apply_hdmi_label(self._parse_hdmi_device_name(output))
                self._parse_active_source(output)
                self.update_input()

    def initialize_power_status(self):
        self.check_power_status()
        logging.info(f"TV initialized. Power: {'ON' if self.is_on else 'OFF'}")

    def initialize_input(self):
        output = self._run_cec_command("scan", timeout=self.SCAN_TIMEOUT)
        self._apply_hdmi_label(self._parse_hdmi_device_name(output))

        detected_input = self._parse_active_source(output)

        if detected_input == "Unknown":
            logging.warning("TV input is 'unknown' on startup, switching to rPi")
            self.set_input("rPi")
        else:
            logging.info(f"TV input detected on startup: {detected_input}")
            self.internal_input = detected_input
            self.update_input()

    def _run_cec_command(self, cec_command, timeout=None, background=False):
        """Run a cec-client command, returning its stdout (empty on failure/timeout).
        Runs in its own process group and is fully killed on timeout, not just the shell
        wrapper. `background=True` marks it cancellable by _acquire_for_command."""
        timeout = timeout or self.CEC_TIMEOUT
        with self.lock:
            process = subprocess.Popen(
                f"echo '{cec_command}' | cec-client -s -d 1",
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                preexec_fn=os.setsid
            )
            with self._current_op_lock:
                self._current_process = process
                self._current_is_background = background
            try:
                stdout, _ = process.communicate(timeout=timeout)
                return stdout
            except subprocess.TimeoutExpired:
                logging.error(f"cec-client command '{cec_command}' timed out after {timeout}s")
                terminate_process_group(process, timeout=2)  # short grace, it already overran
                return ""
            finally:
                with self._current_op_lock:
                    if self._current_process is process:
                        self._current_process = None
                        self._current_is_background = False

    def _acquire_for_command(self, description):
        """Acquire self.lock for a user command. Cancels an in-progress background scan
        rather than waiting for it; never cancels another user command."""
        if self.lock.acquire(blocking=False):
            return True

        with self._current_op_lock:
            process = self._current_process
            is_background = self._current_is_background
        if process is None or not is_background:
            return False

        logging.info(f"Cancelling in-progress background scan to run: {description}")
        terminate_process_group(process, timeout=2)
        return self.lock.acquire(blocking=True, timeout=5)

    def check_power_status(self):
        """Check if the TV is on or in standby and update Home Assistant."""
        power_status = False
        output = self._run_cec_command(f"pow {self.address}").lower()

        logging.info(f"Checking TV power status... Raw output: {output.strip()}")

        if "power status: on" in output:
            power_status = True
        elif "power status: standby" in output or "power status: in transition from standby to on" in output:
            power_status = False
        else:
            logging.warning("Unexpected power status response, assuming TV is OFF.")
            power_status = False

        self.is_on = power_status  # set before update_input(), which reads it

        if self.ha_client:
            self.ha_client.update_switch("tv_power_switch", "ON" if power_status else "OFF")
            self.ha_client.update_binary_sensor("tv_power", power_status)

        self.update_input()

        return power_status

    def get_power_status(self):
        """Return the last-known power status without querying the TV again."""
        return self.is_on

    def toggle_power(self):
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

        if not self._acquire_for_command("power on"):
            logging.info("TV command already in progress, ignoring power-on request.")
            return

        try:
            logging.info("Sending power-on command to TV...")
            self._run_cec_command(f"on {self.address}")

            timeout = 60
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

        if not self._acquire_for_command("standby"):
            logging.info("TV command already in progress, ignoring standby request.")
            return

        try:
            logging.info("Turning off TV (standby mode)...")
            self._run_cec_command(f"standby {self.address}")

            timeout = 60
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
        """Retrieve and track the currently active HDMI input source (a fresh scan)."""
        return self._parse_active_source(self._run_cec_command("scan", timeout=self.SCAN_TIMEOUT))

    def _parse_active_source(self, output):
        """Same as get_active_source(), against already-fetched scan output."""
        match = re.search(r"currently active source:\s*(.+)", output)
        if not match:
            return "Unknown"

        source_info = match.group(1).strip()
        if "unknown (-1)" in source_info or "TV" in source_info:
            # "TV (0)" also covers a non-CEC source being selected -- CEC can't tell them apart.
            logging.warning(f"TV reports 'unknown (-1)', keeping last known input: {self.internal_input}")
            return self.internal_input

        number_match = re.search(r"\((\d+)\)", source_info)
        if not number_match:
            return "Unknown"

        device = self._find_device_by_number(output, number_match.group(1))
        device_name = device.get("osd_string", "Unknown") if device else "Unknown"
        detected_input = f"HDMI {number_match.group(1)} ({device_name})"
        self.internal_input = detected_input
        logging.info(f"TV detected real input: {detected_input}")
        return detected_input

    def get_hdmi_device_name(self):
        """Name of whatever CEC device is on the "hdmi" input (a fresh scan)."""
        return self._parse_hdmi_device_name(self._run_cec_command("scan", timeout=self.SCAN_TIMEOUT))

    def _parse_hdmi_device_name(self, output):
        """Same as get_hdmi_device_name(), against already-fetched scan output. Falls back
        to the configured default name if nothing CEC-aware is on that port."""
        device = self._find_device_by_address(output, self.inputs['hdmi']['address'])
        return (device and device.get('osd_string')) or self.inputs['hdmi']['name']

    @staticmethod
    def _parse_scan_devices(scan_output):
        """Parse `cec-client scan` output into a list of per-device dicts."""
        devices = []
        current = None
        for line in scan_output.splitlines():
            line = line.strip()
            header = re.match(r"device #(\d+):", line)
            if header:
                current = {"number": header.group(1)}
                devices.append(current)
                continue
            if current is None:
                continue
            if line.startswith("address:"):
                current["address"] = line.split(":", 1)[1].strip()
            elif line.startswith("osd string:"):
                current["osd_string"] = line.split(":", 1)[1].strip()
        return devices

    @classmethod
    def _find_device_by_number(cls, scan_output, number):
        return next((d for d in cls._parse_scan_devices(scan_output) if d.get("number") == number), None)

    @classmethod
    def _find_device_by_address(cls, scan_output, address):
        return next((d for d in cls._parse_scan_devices(scan_output) if d.get("address") == address), None)

    def _apply_hdmi_label(self, label):
        """Update the "TV Input" select's second option if the detected label changed."""
        if label == self._hdmi_label:
            return
        self._hdmi_label = label
        if self.ha_client:
            self.ha_client.update_select_options("tv_input", [self.inputs['rPi']['name'], label])

    def update_input(self):
        """Push the currently set input source to Home Assistant."""
        if self.ha_client:
            self.ha_client.update_sensor("tv_current_input", self.get_current_input())
            selection = self.get_tv_input_selection()
            if selection is not None:
                self.ha_client.update_select("tv_input", selection)
        return self.internal_input

    def get_current_input(self):
        """Current input's display name (same as the "TV Input" select), or "Off"/"Unknown"."""
        if not self.is_on:
            return "Off"
        selection = self.get_tv_input_selection()
        return selection if selection is not None else "Unknown"

    def get_tv_input_selection(self):
        """Current value for the "TV Input" select, or None if not known yet."""
        if self.internal_input == "Unknown":
            return None
        if self.internal_input == 'rPi':
            return self.inputs['rPi']['name']
        return self._hdmi_label

    def set_input(self, desired_source):
        """Change the TV input to a specified source (a key in self.inputs)."""
        input_config = self.inputs.get(desired_source)
        if not input_config:
            logging.warning(f"Unknown TV input '{desired_source}'; not switching")
            return

        if not self._acquire_for_command(f"input switch to {desired_source}"):
            logging.info(f"TV command already in progress, ignoring input switch to {desired_source}.")
            return

        try:
            logging.info(f"Switching TV input to {desired_source}")
            self._run_cec_command(self._active_source_command(input_config['address']))

            self.internal_input = desired_source
            self.wait_for_input_switch(desired_source)
            self.update_input()
        finally:
            self.lock.release()

    @staticmethod
    def _active_source_command(physical_address):
        """CEC "tx" command that makes `physical_address` (e.g. "2.0.0.0") active."""
        nibbles = [int(part) for part in physical_address.split('.')]
        byte1 = (nibbles[0] << 4) | nibbles[1]
        byte2 = (nibbles[2] << 4) | nibbles[3]
        return f"tx 1F:82:{byte1:02X}:{byte2:02X}"

    def set_input_rpi(self):
        logging.info("Setting TV input to rPi...")
        self.set_input('rPi')

    def set_input_hdmi(self):
        logging.info("Setting TV input to HDMI...")
        self.set_input('hdmi')

    def wait_for_input_switch(self, desired_source, timeout=25, interval=2):
        """Poll the input status every `interval` seconds until `timeout` is reached."""
        logging.info(f"Waiting for TV to switch to {desired_source}...")
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            time.sleep(interval)
            detected_input = self.get_active_source()

            if desired_source == "hdmi":
                if detected_input not in ("Unknown", "rPi"):
                    logging.info(f"TV detected real input: {detected_input}")
                    return True
            elif self.internal_input == desired_source:
                logging.info(f"TV successfully switched to {desired_source} after {time.monotonic() - start:.1f}s")
                return True

        logging.warning(f"TV input switch to {desired_source} timed out after {timeout}s. Keeping last attempted input: {desired_source}")
        self.internal_input = desired_source
        return False

    def rotate_input(self):
        logging.info(f"Rotating TV input. Current: {self.internal_input}")
        new_input = 'hdmi' if self.internal_input == 'rPi' else 'rPi'
        self.set_input(new_input)
