import subprocess
import logging
import os

class Supervisor:
    def __init__(self, config, ha_client, sounds, tv, utils, settings_store):
        self.config = config
        self.ha_client = ha_client
        self.sounds = sounds
        self.tv = tv
        self.utils = utils
        self.settings_store = settings_store

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

    def start_default_app(self):
        """Start the default app: a persisted (HA-selected) choice wins over config.yaml's fallback."""
        default_app = self.settings_store.get("default_app", self.config.get('default_app'))
        if default_app == "kiosk":
            self.start_kiosk_app()
        elif default_app == "magicmirror":
            self.start_magic_mirror_app()
        elif default_app:
            logging.warning(f"Unknown default_app '{default_app}'; not starting any app")
        else:
            logging.info("No default_app configured; not starting any app")

    def set_default_app(self, app_name):
        """Persist the user-selected default startup app (e.g. from the HA select entity)."""
        if app_name not in ("kiosk", "magicmirror"):
            logging.warning(f"Ignoring invalid default_app selection: {app_name}")
            return
        self.settings_store.set("default_app", app_name)
        if self.ha_client:
            self.ha_client.update_select("default_app", app_name)

    def refresh_kiosk(self):
        """Refresh the screen."""
        self.notify("Refreshing Screen", "Screen refreshed")
        os.system("xdotool key F5")

    def stop_all_apps(self):
        """Stop both Magic Mirror and Kiosk services."""
        self.notify("Button Handler", "All applications stopped")
        for service in ["magicmirror", "kiosk"]:
            subprocess.run(f"sudo systemctl stop {service}.service", shell=True)

    def sample(self):
        self.notify("Hello", "You found the secret button")