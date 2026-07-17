import json
import subprocess
import logging
import os
import threading
import time
from .apps import AppManager
from .services import ServiceManager
from .utils import format_duration

NONE_APP_OPTION = "No Startup App"  # not "None" -- HA's MQTT integration treats that as a reserved sentinel
NO_APP_RUNNING = "Nothing Running"  # "Current App" sensor's state when no app is running

UPTIME_REFRESH_INTERVAL = 30  # seconds between uptime sensor/attribute refreshes

# Display name -> UxPlay CLI flag(s). "Normal" maps to no extra args at all.
UXPLAY_ROTATION_OPTIONS = {
    "Normal": "",
    "Rotate Right": "-r R",
    "Rotate Left": "-r L",
    "Upside Down": "-f I",
}

# "-vs 0" suppresses video rendering while still playing audio; there's no server-side
# flag for the reverse (video-only isn't something UxPlay exposes as a toggle).
UXPLAY_AUDIO_MODE_OPTIONS = {
    "Video & Audio": "",
    "Audio Only": "-vs 0",
}

class Supervisor:
    def __init__(self, config, ha_client, sounds, tv, utils, settings_store, apps_config, user_home=None, secrets=None, services_config=None):
        self.config = config
        self.ha_client = ha_client
        self.sounds = sounds
        self.tv = tv
        self.utils = utils
        self.settings_store = settings_store
        self.apps = AppManager((apps_config or {}).get('apps', {}), user_home=user_home, secrets=secrets)
        self.services = ServiceManager(
            (services_config or {}).get('services', {}),
            user_home=user_home, secrets=secrets,
            on_state_change=self._on_service_state_change
        )
        threading.Thread(target=self._uptime_loop, daemon=True).start()

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
        """Choose which configured app to open, via an on-screen picker."""
        apps = self.apps.apps
        if not apps:
            logging.warning("No apps configured; nothing to select")
            return

        display_names = {key: app_config.get('name', key) for key, app_config in apps.items()}
        popup_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "button_popup.py")
        request = {"title": "Choose an app to open", "options": display_names}
        process = subprocess.run(
            ["python3", popup_script],
            input=json.dumps(request), capture_output=True, text=True,
        )

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
        subprocess.run(["wtype", "-P", "F5", "-p", "F5"])

    def get_current_app_display_name(self):
        """Display name of the currently running app, for the "Current App" sensor."""
        name = self.apps.current_app
        if not name:
            return NO_APP_RUNNING
        return self.apps.apps.get(name, {}).get('name', name)

    def get_current_app_uptime(self):
        """Formatted uptime of the currently running app (e.g. "2h 14m"), or None."""
        uptime_seconds = self.apps.get_uptime_seconds()
        return format_duration(uptime_seconds) if uptime_seconds is not None else None

    def _notify_current_app(self):
        """Push the currently running app to the "Current App" sensor and "App Switcher" select."""
        if not self.ha_client:
            return
        self.ha_client.update_sensor("current_app", self.get_current_app_display_name())
        self.ha_client.update_select("app_switcher", self.apps.current_app or NO_APP_RUNNING)
        self._push_uptimes()

    def _uptime_loop(self):
        """Keep uptime-flavored sensors/attributes ticking for as long as the supervisor runs."""
        while True:
            time.sleep(UPTIME_REFRESH_INTERVAL)
            self._push_uptimes()

    def _push_uptimes(self):
        if not self.ha_client:
            return

        self.ha_client.refresh_sensor_attributes()  # e.g. Current App's "uptime" attribute

        if self.utils:
            self.ha_client.update_sensor("pi_uptime", self.utils.get_pi_uptime())
            self.ha_client.update_sensor("supervisor_uptime", self.utils.get_supervisor_uptime())

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

    def set_tv_input(self, value):
        """Callback for the "TV Input" select. Anything but the Pi's name maps to hdmi."""
        if value == self.tv.inputs['rPi']['name']:
            self.tv.set_input_rpi()
        else:
            self.tv.set_input_hdmi()

    def _on_service_state_change(self, name, running):
        """ServiceManager callback: keep a service's HA switch in sync."""
        if not self.ha_client:
            return
        self.ha_client.update_switch(name, "ON" if running else "OFF")

    def start_uxplay(self):
        extra_args = " ".join(filter(None, [
            UXPLAY_ROTATION_OPTIONS[self.get_uxplay_rotation()],
            UXPLAY_AUDIO_MODE_OPTIONS[self.get_uxplay_audio_mode()],
        ]))
        self.services.start("uxplay", extra_args=extra_args)

    def stop_uxplay(self):
        self.services.stop("uxplay")

    def is_uxplay_running(self):
        return self.services.is_running("uxplay")

    def get_uxplay_rotation(self):
        """Persisted AirPlay screen orientation (a UXPLAY_ROTATION_OPTIONS key)."""
        return self.settings_store.get("uxplay_rotation", "Normal")

    def start_mirror_mode(self):
        self.services.start("mirror_mode")

    def stop_mirror_mode(self):
        self.services.stop("mirror_mode")

    def toggle_mirror_mode(self):
        if self.is_mirror_mode_running():
            self.stop_mirror_mode()
        else:
            self.start_mirror_mode()

    def is_mirror_mode_running(self):
        return self.services.is_running("mirror_mode")

    def set_uxplay_rotation(self, value):
        """Persist the orientation, restarting AirPlay now if it's running (UxPlay
        only applies rotation at launch)."""
        if value not in UXPLAY_ROTATION_OPTIONS:
            logging.warning(f"Ignoring invalid uxplay rotation selection: {value}")
            return
        self.settings_store.set("uxplay_rotation", value)
        if self.ha_client:
            self.ha_client.update_select("uxplay_rotation", value)
        if self.services.is_running("uxplay"):
            self.stop_uxplay()
            self.start_uxplay()

    def get_uxplay_audio_mode(self):
        """Persisted AirPlay audio mode (a UXPLAY_AUDIO_MODE_OPTIONS key)."""
        return self.settings_store.get("uxplay_audio_mode", "Video & Audio")

    def set_uxplay_audio_mode(self, value):
        """Same restart-if-running behavior as set_uxplay_rotation."""
        if value not in UXPLAY_AUDIO_MODE_OPTIONS:
            logging.warning(f"Ignoring invalid uxplay audio mode selection: {value}")
            return
        self.settings_store.set("uxplay_audio_mode", value)
        if self.ha_client:
            self.ha_client.update_select("uxplay_audio_mode", value)
        if self.services.is_running("uxplay"):
            self.stop_uxplay()
            self.start_uxplay()

    def start_autostart_services(self):
        """Start every `autostart: true` service. UxPlay goes through start_uxplay()
        so it picks up the persisted rotation/audio mode."""
        for name in self.services.list_services():
            if not self.services.services.get(name, {}).get('autostart'):
                continue
            if name == "uxplay":
                self.start_uxplay()
            else:
                self.services.start(name)

    def sample(self):
        self.notify("Hello", "You found the secret button")