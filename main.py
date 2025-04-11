import logging
import os
import sys
import yaml
import pygame
import signal
from signal import pause
from tv import TV
from buttons import ButtonHandler
from home_assistant_client import HomeAssistantClient
from supervisor import Supervisor
from utils import Utils

# Load configuration from YAML files
with open('config.yaml', 'r') as config_file:
    config = yaml.safe_load(config_file)

with open('secrets.yaml', 'r') as secrets_file:
    secrets = yaml.safe_load(secrets_file)

with open('entities.yaml', 'r') as entities_file:
    entities = yaml.safe_load(entities_file)

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

def signal_handler(sig, frame):
    logger.info('Signal received, exiting...')
    HomeAssistantClient.cleanup()
    sys.exit(0)

def main():
    """Main function to initialize the system."""
    logger.info("Initializing system...")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    

    ha_client = HomeAssistantClient(
        broker=secrets['mqtt_broker'],
        port=secrets['mqtt_port'],
        username=secrets['mqtt_username'],
        password=secrets['mqtt_password'],
        token=secrets['ha_api_token'],
        api_url=secrets['ha_api_url'],
        config=config,
        entities=entities,
        supervisor=None,  # We'll set this after creating phone_controller
        tv=None,  # We'll set this after creating TV
        utils=None  # We'll set this after creating utils
    )

    # Initialize TV
    global tv
    tv = TV("0.0.0.0")
    ha_client.tv = tv  # Set TV in HA client
    logger.info("TV initialized")

    global supervisor
    supervisor = Supervisor(config, ha_client, sounds)
    ha_client.supervisor = supervisor  # Set supervisor in HA client
    logger.info("Supervisor initialized")

    global utils
    utils = Utils(config, supervisor, tv)
    ha_client.utils = utils  # Set utils in HA client
    logger.info("Utils initialized")

    # Initialize Buttons with Logging
    ButtonHandler("Button 1", 25, press_callback=tv.toggle_power, hold_callback=lambda: (tv.standby(), supervisor.shutdown()))
    ButtonHandler("Button 2", 24, press_callback=supervisor.switch_apps, hold_callback=supervisor.app_selector)
    ButtonHandler("Button 3", 23, press_callback=supervisor.stop_all_apps, hold_callback=tv.rotate_input)

    pause()

if __name__ == "__main__":
    main()
