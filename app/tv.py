import os
import subprocess
import re
import time
import threading
import logging

from .process_utils import terminate_process_group

class TV:
    CEC_TIMEOUT = 10  # seconds; prevents a hung cec-client from blocking button/MQTT threads indefinitely
    # "scan" walks the whole CEC bus (every connected device), unlike a single-device query
    # like "pow" — in practice it reliably needs more than CEC_TIMEOUT, especially with
    # several devices present, so it gets its own more generous budget.
    SCAN_TIMEOUT = 20
    POLL_INTERVAL = 60  # seconds between background power/input polls, to catch changes made via the TV's own remote rather than through us

    # Fallback if config.yaml doesn't declare its own tv_inputs — a name plus the CEC
    # physical address (e.g. "2.0.0.0") that switching to that input actually targets.
    # Re-map this (in config.yaml, not here) for a different TV/wiring instead of editing code.
    DEFAULT_INPUTS = {
        'rPi': {'name': 'Raspberry Pi', 'address': '2.0.0.0'},
        'hdmi': {'name': 'HDMI 3', 'address': '3.0.0.0'},
    }

    def __init__(self, address, ha_client, inputs=None):
        self.address = address
        self.ha_client = ha_client
        self.inputs = inputs or self.DEFAULT_INPUTS
        self.lock = threading.RLock()  # serializes all cec-client access; reentrant since command methods hold it across their own status-check calls

        # Tracks whatever cec-client process is currently running, so a user-initiated
        # command can cancel it (if it's just the background poll) instead of being
        # dropped outright — see _acquire_for_command. Guarded by its own small lock
        # rather than self.lock, since it needs to be read/written from a thread that by
        # definition doesn't hold self.lock (that's the point of it being busy).
        self._current_process = None
        self._current_is_background = False
        self._current_op_lock = threading.Lock()

        # Set a default before checking the actual power status
        self.is_on = False
        self.power_thread = threading.Thread(target=self.initialize_power_status, daemon=True)
        self.power_thread.start()

        # Set internal input to a default before checking the actual input
        self.internal_input = "Unknown"
        self._hdmi_label = self.inputs['hdmi']['name']  # last name pushed to the "TV Input" select's options

        # Start the input check in a separate thread so buttons remain responsive
        self.input_thread = threading.Thread(target=self.initialize_input, daemon=True)
        self.input_thread.start()

        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        """Periodically re-check power/input state — the only way to notice a change made
        via the TV's own remote instead of through us, since otherwise we only ever query
        the TV in response to our own commands."""
        while True:
            time.sleep(self.POLL_INTERVAL)
            self.check_power_status()
            if self.is_on:
                # One shared scan for both the active-source lookup and the "hdmi" input's
                # device-name lookup, instead of each doing its own cec-client invocation —
                # halves CEC bus traffic per cycle. _apply_hdmi_label runs first since
                # update_input() (called via _parse_active_source's side effects, below)
                # reads _hdmi_label for the "TV Input" select's current value.
                output = self._run_cec_command("scan", timeout=self.SCAN_TIMEOUT, background=True)
                self._apply_hdmi_label(self._parse_hdmi_device_name(output))
                self._parse_active_source(output)
                self.update_input()

    def initialize_power_status(self):
        """Background thread to check initial TV power status."""
        self.check_power_status()
        logging.info(f"TV initialized. Power: {'ON' if self.is_on else 'OFF'}")

    def initialize_input(self):
        """Background thread to check initial TV input."""
        output = self._run_cec_command("scan", timeout=self.SCAN_TIMEOUT)  # shared by both lookups below, see _poll_loop
        self._apply_hdmi_label(self._parse_hdmi_device_name(output))

        detected_input = self._parse_active_source(output)

        if detected_input == "Unknown":
            logging.warning("TV input is 'unknown' on startup, switching to rPi")
            self.set_input("rPi")  # updates internal_input and publishes itself
        else:
            logging.info(f"TV input detected on startup: {detected_input}")
            self.internal_input = detected_input  # Save valid input
            self.update_input()  # Update Home Assistant with the detected input

    def _run_cec_command(self, cec_command, timeout=None, background=False):
        """Run a cec-client command, returning its stdout (empty string on failure,
        timeout, or cancellation). Serialized via self.lock — concurrent invocations stall
        each other out. Runs in its own process group and is fully killed (not just the
        shell wrapper) on timeout — without that, a hung cec-client can outlive the
        timeout and keep holding the CEC adapter open, making every subsequent command
        fail too. `background=True` marks this call as cancellable by
        _acquire_for_command — used only by the periodic poll's routine scan, so a
        user-initiated command isn't stuck waiting behind it."""
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
                # Short grace period: it's already blown its budget, no need to wait the
                # default 5s for a graceful SIGTERM before escalating — every extra second
                # here is extra time self.lock stays held, blocking any other TV command.
                terminate_process_group(process, timeout=2)
                return ""
            finally:
                with self._current_op_lock:
                    if self._current_process is process:
                        self._current_process = None
                        self._current_is_background = False

    def _acquire_for_command(self, description):
        """Acquire self.lock for a user-initiated command (power on/standby/input switch).
        If it's already held by the background poll's routine scan, cancel that scan
        instead of making the user wait behind it — routine housekeeping shouldn't block
        something a user is actively doing. Never cancels another user-initiated command
        that's still in flight; that still just gets logged and dropped, same as before,
        so two deliberate actions in a row don't step on each other unpredictably."""
        if self.lock.acquire(blocking=False):
            return True

        with self._current_op_lock:
            process = self._current_process
            is_background = self._current_is_background
        if process is None or not is_background:
            return False

        logging.info(f"Cancelling in-progress background scan to run: {description}")
        terminate_process_group(process, timeout=2)
        # The cancelled call's own `with self.lock:` releases it as soon as
        # communicate() unblocks from the kill, which should be near-immediate — this
        # blocking acquire is just to wait out that short handoff, not a real contention.
        return self.lock.acquire(blocking=True, timeout=5)

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

        # Store the power status before pushing anything — get_current_input() (called via
        # update_input() below) reports "Off" based on this, not a fresh CEC query.
        self.is_on = power_status

        # Update the Home Assistant switch with the power status
        if self.ha_client:
            self.ha_client.update_switch("tv_power_switch", "ON" if power_status else "OFF")
            self.ha_client.update_binary_sensor("tv_power", power_status)

        self.update_input()  # keep "TV Current Input" in sync with power state too

        return power_status

    def get_power_status(self):
        """Return the last-known power status without querying the TV again."""
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

        if not self._acquire_for_command("power on"):
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

        if not self._acquire_for_command("standby"):
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
        """Retrieve and track the currently active HDMI input source (a fresh scan)."""
        return self._parse_active_source(self._run_cec_command("scan", timeout=self.SCAN_TIMEOUT))

    def _parse_active_source(self, output):
        """Same as get_active_source(), but against already-fetched scan `output` — lets
        callers that also need get_hdmi_device_name() (e.g. the poll loop) share a single
        scan instead of each doing their own cec-client invocation."""
        match = re.search(r"currently active source:\s*(.+)", output)
        if not match:
            return "Unknown"

        source_info = match.group(1).strip()
        if "unknown (-1)" in source_info or "TV" in source_info:
            # "TV (0)" covers both the TV's own tuner genuinely being active, and a
            # non-CEC source being selected (the TV can't tell CEC apart from those, so it
            # just reports itself) — either way there's no better answer than the last
            # known input.
            logging.warning(f"TV reports 'unknown (-1)', keeping last known input: {self.internal_input}")
            return self.internal_input

        number_match = re.search(r"\((\d+)\)", source_info)
        if not number_match:
            return "Unknown"

        device = self._find_device_by_number(output, number_match.group(1))
        device_name = device.get("osd_string", "Unknown") if device else "Unknown"
        detected_input = f"HDMI {number_match.group(1)} ({device_name})"
        self.internal_input = detected_input  # Update internal input
        logging.info(f"TV detected real input: {detected_input}")
        return detected_input

    def get_hdmi_device_name(self):
        """Display name of whatever CEC-aware device is on the "hdmi" input's physical
        address (a fresh scan) — see _parse_hdmi_device_name for details."""
        return self._parse_hdmi_device_name(self._run_cec_command("scan", timeout=self.SCAN_TIMEOUT))

    def _parse_hdmi_device_name(self, output):
        """Same as get_hdmi_device_name(), but against already-fetched scan `output`.
        Falls back to "hdmi"'s configured default name if nothing CEC-capable is detected
        there — most non-CEC devices (e.g. a laptop) are simply invisible to a CEC scan,
        not just unnamed."""
        device = self._find_device_by_address(output, self.inputs['hdmi']['address'])
        return (device and device.get('osd_string')) or self.inputs['hdmi']['name']

    @staticmethod
    def _parse_scan_devices(scan_output):
        """Parse `cec-client scan` output into a list of dicts (one per "device #N: ..."
        block), each with whichever of "number"/"address"/"osd_string" fields it had."""
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
        """Swap in a real device name (e.g. "Apple TV") for the "TV Input" select's
        second option when one's CEC-aware, falling back to that input's configured
        default label otherwise — only pushes new options if the label actually changed."""
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
        """Return the last-known input without pushing an update. Reports "Off" while the
        TV is off — an HDMI input reading is meaningless once the TV itself is powered
        down, and self.internal_input is deliberately left untouched so the actual last
        input is still there to report once the TV powers back on."""
        if not self.is_on:
            return "Off"
        return self.internal_input

    def get_tv_input_selection(self):
        """Current value for the "TV Input" select, in its two-option scheme (the Pi's
        configured name, or whatever the "hdmi" input's current label is), or None while
        the real input isn't known yet (startup, before either background thread has
        settled) — update_input() skips pushing in that case rather than asserting a
        possibly-wrong value, the same way get_current_input() reports "Unknown" until
        settled. Deliberately not power-aware like get_current_input(), since "Off" isn't
        one of this select's options and it's answering "which input is selected", not
        "is anything showing"."""
        if self.internal_input == "Unknown":
            return None
        if self.internal_input == 'rPi':
            return self.inputs['rPi']['name']
        return self._hdmi_label

    def set_input(self, desired_source):
        """Change the TV input to a specified source (a key in self.inputs) and confirm
        the switch."""
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

            # Set internal state before waiting for confirmation
            self.internal_input = desired_source
            self.wait_for_input_switch(desired_source)
            self.update_input()  # Update Home Assistant with the new input
        finally:
            self.lock.release()

    @staticmethod
    def _active_source_command(physical_address):
        """Build the CEC "tx" command that makes `physical_address` (e.g. "2.0.0.0") the
        active source, by packing its four nibbles into the two-byte Active Source payload."""
        nibbles = [int(part) for part in physical_address.split('.')]
        byte1 = (nibbles[0] << 4) | nibbles[1]
        byte2 = (nibbles[2] << 4) | nibbles[3]
        return f"tx 1F:82:{byte1:02X}:{byte2:02X}"

    def set_input_rpi(self):
        """Set the TV input to the Raspberry Pi."""
        logging.info("Setting TV input to rPi...")
        self.set_input('rPi')
        
    def set_input_hdmi(self):
        """Set the TV input to HDMI."""
        logging.info("Setting TV input to HDMI...")
        self.set_input('hdmi')

    def wait_for_input_switch(self, desired_source, timeout=25, interval=2):
        """Poll the input status every `interval` seconds until `timeout` is reached."""
        logging.info(f"Waiting for TV to switch to {desired_source}...")
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            time.sleep(interval)
            # get_active_source() can itself take up to CEC_TIMEOUT (plus kill-on-timeout
            # grace) if the underlying scan hangs — track real wall-clock time, not a
            # fixed per-iteration increment, or one slow/timed-out scan silently blows the
            # whole budget several times over instead of bounding it.
            detected_input = self.get_active_source()

            if desired_source == "hdmi":
                # Only a real connected device becoming active confirms the switch to HDMI
                if detected_input not in ("Unknown", "rPi"):
                    logging.info(f"TV detected real input: {detected_input}")
                    return True
            elif self.internal_input == desired_source:
                # No device to detect for rPi, so fall back to internal tracking
                logging.info(f"TV successfully switched to {desired_source} after {time.monotonic() - start:.1f}s")
                return True

        logging.warning(f"TV input switch to {desired_source} timed out after {timeout}s. Keeping last attempted input: {desired_source}")
        self.internal_input = desired_source  # Ensure the script doesn't get stuck
        return False

    def rotate_input(self):
        """Toggle between rPi and HDMI input."""
        logging.info(f"Rotating TV input. Current: {self.internal_input}")
        new_input = 'hdmi' if self.internal_input == 'rPi' else 'rPi'
        self.set_input(new_input)