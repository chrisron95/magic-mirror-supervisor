import subprocess
import logging
import os
from .apps import AppManager

NONE_APP_OPTION = "No Startup App"  # sentinel for the default_app select's "don't auto-start anything" option
NO_APP_RUNNING = "Nothing Running"  # "Current App" sensor's state when no app is running

# "None" is deliberately avoided above: Home Assistant's MQTT integration treats a state
# payload of the literal string "None" as a reserved sentinel meaning "reset to unknown",
# not as a selectable value, so it would never actually display as selected.

class Supervisor:
    def __init__(self, config, ha_client, sounds, tv, utils, settings_store, apps_config, user_home=None, secrets=None):
        self.config = config
        self.ha_client = ha_client
        self.sounds = sounds
        self.tv = tv
        self.utils = utils
        self.settings_store = settings_store
        self.apps = AppManager((apps_config or {}).get('apps', {}), user_home=user_home, secrets=secrets)

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
        self._notify_current_app()

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
        if default_app and default_app != NONE_APP_OPTION:
            self.start_app(default_app)
        else:
            logging.info("No default_app configured; not starting any app")

    def set_default_app(self, app_name):
        """Persist the user-selected default startup app (e.g. from the HA select entity)."""
        if app_name != NONE_APP_OPTION and app_name not in self.apps.list_apps():
            logging.warning(f"Ignoring invalid default_app selection: {app_name}")
            return
        self.settings_store.set("default_app", app_name)
        if self.ha_client:
            self.ha_client.update_select("default_app", app_name)

    def refresh_kiosk(self):
        """Refresh the screen."""
        self.notify("Refreshing Screen", "Screen refreshed")
        os.system("xdotool key F5")

    def get_current_app_display_name(self):
        """Display name of the currently running app, for the "Current App" sensor."""
        name = self.apps.current_app
        if not name:
            return NO_APP_RUNNING
        return self.apps.apps.get(name, {}).get('name', name)

    def _notify_current_app(self):
        """Push the currently running app to the "Current App" sensor and "App Switcher"
        select. NO_APP_RUNNING is itself a valid app_switcher option, so it's used as-is
        when nothing is running."""
        if not self.ha_client:
            return
        self.ha_client.update_sensor("current_app", self.get_current_app_display_name())
        self.ha_client.update_select("app_switcher", self.apps.current_app or NO_APP_RUNNING)

    def switch_to_app(self, name):
        """Callback for the App Switcher select: starts the given app, or stops whatever's
        running if NO_APP_RUNNING was selected."""
        if name == NO_APP_RUNNING:
            self.stop_all_apps()
        else:
            self.start_app(name)

    def stop_all_apps(self):
        """Stop whichever app is currently running."""
        self.notify("Button Handler", "All applications stopped")
        self.apps.stop()
        self._notify_current_app()

    def sample(self):
        self.notify("Hello", "You found the secret button")