import logging
import os
import sys
import yaml
import pygame
import signal
from signal import pause
from app.tv import TV
from app.buttons import ButtonHandler
from app.home_assistant_client import HomeAssistantClient
from app.supervisor import Supervisor
from app.utils import Utils
from app.settings_store import SettingsStore

# Load configuration from YAML files
with open('config/config.yaml', 'r') as config_file:
    config = yaml.safe_load(config_file)

with open('config/secrets.yaml', 'r') as secrets_file:
    secrets = yaml.safe_load(secrets_file)

with open('config/entities.yaml', 'r') as entities_file:
    entities = yaml.safe_load(entities_file)

with open('config/apps.yaml', 'r') as apps_file:
    apps_config = yaml.safe_load(apps_file)

# "{{user_home}}"/"{{uid}}"/"{{url}}"/"{{secrets.<key>}}" placeholders in apps.yaml (and app
# templates) are resolved by AppManager itself; it just needs the configured home directory
# and secrets here.
user_home = config.get('user_home', os.path.expanduser('~'))

# Persisted, user-changeable settings (e.g. default_app selected from Home Assistant)
settings_store = SettingsStore('data/settings.yaml')

# Configuration
LOG_LEVEL = getattr(logging, config['log_level'].upper(), logging.DEBUG)

# Logging configuration
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Initialize pygame for audio playback
pygame.mixer.init()
sounds = {
    "test": pygame.mixer.Sound(os.path.join('sounds', "oh-finally-355.mp3"))
}

supervisor = None  # guards signal_handler if a signal arrives before main() sets this up

def signal_handler(sig, frame):
    logger.info('Signal received, exiting...')
    if ha_client:
        ha_client.cleanup()
    if supervisor:
        supervisor.apps.stop()  # avoid leaking the running app's process group across a restart
    utils.cleanup_gpios()
    sys.exit(0)

def main():
    """Main function to initialize the system."""
    logger.info("Initializing system...")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Initialize TV
    global tv
    tv = TV("0.0.0.0", ha_client=None)
    logger.info("TV initialized")

    # Initialize Supervisor
    global supervisor
    supervisor = Supervisor(
        config=config,
        ha_client=None,
        sounds=sounds,
        tv=tv,
        utils=None,
        settings_store=settings_store,
        apps_config=apps_config,
        user_home=user_home,
        secrets=secrets
    )
    logger.info("Supervisor initialized")

    # Initialize Utils
    global utils
    utils = Utils(
        config=config,
        secrets=secrets,
        supervisor=supervisor,
        tv=tv,
        button1=None,
        button2=None,
        button3=None
    )
    supervisor.utils = utils  # Set utils in supervisor
    logger.info("Utils initialized")

    # Initialize Buttons with Logging
    global button1, button2, button3
    button1 = ButtonHandler("Button 1", 25, press_callback=tv.toggle_power, hold_callback=lambda: (tv.standby(), utils.shutdown()))
    button2 = ButtonHandler("Button 2", 24, press_callback=supervisor.switch_apps, hold_callback=supervisor.app_selector)
    button3 = ButtonHandler("Button 3", 23, press_callback=supervisor.stop_all_apps, hold_callback=tv.rotate_input)
    utils.button1 = button1
    utils.button2 = button2
    utils.button3 = button3
    logger.info("Buttons initialized")

    # Initialize Home Assistant Client
    global ha_client
    ha_client = None
    try:
        ha_client = HomeAssistantClient(
            broker=secrets['mqtt_broker'],
            port=secrets['mqtt_port'],
            username=secrets['mqtt_username'],
            password=secrets['mqtt_password'],
            config=config,
            entities=entities,
            supervisor=supervisor,
            tv=tv,
            utils=utils
        )
        supervisor.ha_client = ha_client  # Set ha_client in supervisor
        tv.ha_client = ha_client  # Set ha_client in TV

        ha_client.setup_discovery()
        logger.info("Home Assistant integration initialized")
    except Exception:
        logger.exception("Failed to initialize Home Assistant integration; continuing in offline mode")

    # Auto-start the default app now that the supervisor is fully up, but only once a
    # network connection is detected — the kiosk dashboard and MagicMirror's modules
    # both depend on it, so starting either offline would just show a broken screen.
    if utils.wait_for_network():
        logger.info("Network available, starting default app")
        supervisor.start_default_app()
    else:
        logger.warning("No network connection detected; not starting default app")

    pause()

if __name__ == "__main__":
    main()
