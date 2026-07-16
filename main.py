import logging
import os
import sys
import time
import yaml
import pygame
import signal
from signal import pause
from types import SimpleNamespace
from app.tv import TV
from app.buttons import load_buttons
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

with open('config/services.yaml', 'r') as services_file:
    services_config = yaml.safe_load(services_file)

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

# Initialize pygame for audio playback. SDL's default driver autodetection goes through
# PipeWire/xdg-desktop-portal, which was timing out under this systemd service's
# environment and dominating startup time; force ALSA directly and fall back to the
# default only if that fails.
os.environ.setdefault('SDL_AUDIODRIVER', 'alsa')
_pygame_init_start = time.monotonic()
try:
    pygame.mixer.init()
except pygame.error:
    logger.warning("pygame.mixer.init() failed with SDL_AUDIODRIVER=alsa; falling back to SDL's default driver")
    del os.environ['SDL_AUDIODRIVER']
    pygame.mixer.init()
logger.info(f"pygame.mixer initialized ({time.monotonic() - _pygame_init_start:.1f}s)")
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
        supervisor.services.stop_all()
    utils.cleanup_gpios()
    sys.exit(0)

def main():
    """Main function to initialize the system."""
    logger.info("Initializing system...")
    boot_start = time.monotonic()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Initialize TV
    global tv
    step_start = time.monotonic()
    tv = TV("0.0.0.0", ha_client=None, inputs=config.get('tv_inputs'))
    logger.info(f"TV initialized ({time.monotonic() - step_start:.1f}s)")

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
        secrets=secrets,
        services_config=services_config
    )
    logger.info("Supervisor initialized")

    # Initialize Utils
    global utils
    utils = Utils(
        config=config,
        secrets=secrets,
        supervisor=supervisor,
        tv=tv,
        buttons=None
    )
    supervisor.utils = utils  # Set utils in supervisor
    logger.info("Utils initialized")

    # Initialize Buttons from config/buttons.yaml
    global buttons
    button_context = SimpleNamespace(tv=tv, supervisor=supervisor, utils=utils)
    buttons = load_buttons('config/buttons.yaml', button_context)
    utils.buttons = buttons
    logger.info(f"Buttons initialized ({len(buttons)})")

    # Start any autostart: true services (e.g. UxPlay) -- not gated on network
    supervisor.start_autostart_services()
    logger.info("Autostart services started")

    # Initialize Home Assistant Client
    global ha_client
    ha_client = None
    try:
        step_start = time.monotonic()
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
        logger.info(f"HomeAssistantClient constructed ({time.monotonic() - step_start:.1f}s)")

        step_start = time.monotonic()
        ha_client.setup_discovery()
        logger.info(f"Home Assistant integration initialized ({time.monotonic() - step_start:.1f}s)")
    except Exception:
        logger.exception("Failed to initialize Home Assistant integration; continuing in offline mode")

    # Auto-start the default app now that the supervisor is fully up, but only once a
    # network connection is detected — the kiosk dashboard and MagicMirror's modules
    # both depend on it, so starting either offline would just show a broken screen.
    step_start = time.monotonic()
    network_available = utils.wait_for_network()
    logger.info(f"wait_for_network returned {network_available} ({time.monotonic() - step_start:.1f}s)")
    if network_available:
        logger.info("Network available, starting default app")
        step_start = time.monotonic()
        supervisor.start_default_app()
        logger.info(f"start_default_app finished ({time.monotonic() - step_start:.1f}s)")
    else:
        logger.warning("No network connection detected; not starting default app")

    logger.info(f"Startup complete ({time.monotonic() - boot_start:.1f}s total)")

    pause()

if __name__ == "__main__":
    main()
