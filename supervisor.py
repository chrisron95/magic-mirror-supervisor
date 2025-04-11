import subprocess
import logging
import os

class Supervisor:
    def __init__(self, config, ha_client, sounds, tv, utils):
        self.config = config
        self.ha_client = ha_client
        self.sounds = sounds
        self.tv = tv
        self.utils = utils

    def notify(self, title, message):
        """Send a notification to the desktop."""
        logging.info(f"Notification: {title} - {message}")
        subprocess.run(["notify-send", title, message, "--urgency=low"])

    def switch_apps(self):
        """Switch between Magic Mirror and Kiosk applications."""
        services = {"kiosk": "magicmirror", "magicmirror": "kiosk"}
        
        active_service = next((s for s in services if subprocess.run(f"systemctl is-active --quiet {s}", shell=True).returncode == 0), None)
        new_service = services.get(active_service, "magicmirror")

        logging.info(f"Switching to {new_service.capitalize()}")
        self.notify("App Switching...", f"Starting {new_service.capitalize()}")

        if active_service:
            subprocess.run(f"sudo systemctl stop {active_service}.service", shell=True)

        subprocess.run(f"sudo systemctl start {new_service}.service", shell=True)

    def app_selector(self):
        """Choose between Magic Mirror or Kiosk application."""
        process = subprocess.run([
            "notify-send", "Smart Mirror", "Choose an app to open:",
            "--action=mirror=Magic Mirror",  # Button 1
            "--action=kiosk=Home Assistant"   # Button 2
        ], capture_output=True, text=True)

        response = process.stdout.strip()
        logging.info(f"Starting {response.capitalize()}")
        self.notify("App Starting...", f"Starting {response.capitalize()}")
        if response == "mirror":
            os.system("sudo systemctl stop kiosk.service && sudo systemctl start magicmirror.service")
        elif response == "kiosk":
            os.system("sudo systemctl stop magicmirror.service && sudo systemctl start kiosk.service")

    def start_magic_mirror_app(self):
        os.system("sudo systemctl stop kiosk.service && sudo systemctl start magicmirror.service")

    def start_kiosk_app(self):
        os.system("sudo systemctl stop magicmirror.service && sudo systemctl start kiosk.service")

    def stop_all_apps(self):
        """Stop both Magic Mirror and Kiosk services."""
        self.notify("Button Handler", "All applications stopped")
        for service in ["magicmirror", "kiosk"]:
            subprocess.run(f"sudo systemctl stop {service}.service", shell=True)

    def sample(self):
        self.notify("Hello", "You found the secret button")