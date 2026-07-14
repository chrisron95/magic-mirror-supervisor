import subprocess
import logging
import os
from .apps import AppManager

class Supervisor:
    def __init__(self, config, ha_client, sounds, tv, utils, settings_store, apps_config):
        self.config = config
        self.ha_client = ha_client
        self.sounds = sounds
        self.tv = tv
        self.utils = utils
        self.settings_store = settings_store
        self.apps = AppManager((apps_config or {}).get('apps', {}))

    def notify(self, title, message):
        """Send a notification to the desktop."""
        logging.info(f"Notification: {title} - {message}")
        subprocess.run(["notify-send", title, message, "--urgency=low"])

    def start_app(self, name):
        """Start the named app from apps.yaml, stopping whatever's currently running."""
        app_config = self.apps.apps.get(name)
        if not app_config:
            logging.warning(f"Unknown app '{name}'; not starting")
            return
        display_name = app_config.get('name', name)
        logging.info(f"Starting {display_name}")
        self.notify("App Starting...", display_name)
        self.apps.start(name)

    def switch_apps(self):
        """Cycle to the next app configured in apps.yaml."""
        apps = self.apps.list_apps()
        if not apps:
            logging.warning("No apps configured; nothing to switch to")
            return
        current_index = apps.index(self.apps.current_app) if self.apps.current_app in apps else -1
        self.start_app(apps[(current_index + 1) % len(apps)])

    def app_selector(self):
        """Choose which configured app to open, via a desktop notification menu."""
        apps = self.apps.apps
        if not apps:
            logging.warning("No apps configured; nothing to select")
            return

        command = ["notify-send", "Smart Mirror", "Choose an app to open:"]
        command += [f"--action={key}={app_config.get('name', key)}" for key, app_config in apps.items()]
        process = subprocess.run(command, capture_output=True, text=True)

        response = process.stdout.strip()
        if response in apps:
            self.start_app(response)

    def start_default_app(self):
        """Start the default app: a persisted (HA-selected) choice wins over config.yaml's fallback."""
        default_app = self.settings_store.get("default_app", self.config.get('default_app'))
        if default_app:
            self.start_app(default_app)
        else:
            logging.info("No default_app configured; not starting any app")

    def set_default_app(self, app_name):
        """Persist the user-selected default startup app (e.g. from the HA select entity)."""
        if app_name not in self.apps.list_apps():
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
        """Stop whichever app is currently running."""
        self.notify("Button Handler", "All applications stopped")
        self.apps.stop()

    def sample(self):
        self.notify("Hello", "You found the secret button")