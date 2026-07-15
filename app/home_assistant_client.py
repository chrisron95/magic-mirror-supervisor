import paho.mqtt.client as mqtt
import time
import logging
from ha_mqtt_discoverable import Settings, DeviceInfo
from ha_mqtt_discoverable.sensors import BinarySensor, BinarySensorInfo, Button, ButtonInfo, Switch, SwitchInfo, Sensor, SensorInfo, Select, SelectInfo
from .supervisor import NONE_APP_OPTION, NO_APP_RUNNING

logger = logging.getLogger(__name__)

APPS_ALL_OPTIONS = "{{apps_all}}"  # entities.yaml select `options:` shorthand — see _apps_all_options()
APPS_OPTIONS = "{{apps}}"  # entities.yaml select `options:` shorthand — see _apps_options()

class HomeAssistantClient:
    def __init__(self, broker, port, username, password, config, entities, supervisor, tv, utils):
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
        self.client.username_pw_set(username, password)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.retained_values = {}
        # unique_id -> {'to_canonical': {display: canonical}, 'to_display': {canonical: display}},
        # only populated for selects using the "{{apps_all}}" options shorthand
        self._select_maps = {}

        # Connect asynchronously so a down/absent network never blocks or raises here;
        # the network loop thread keeps retrying with backoff until the broker is reachable.
        self.client.reconnect_delay_set(min_delay=1, max_delay=120)
        self.client.connect_async(broker, port, 60)
        self.client.loop_start()

        self.config = config
        self.entities = entities
        self.supervisor = supervisor
        self.tv = tv
        self.utils = utils
        self._shared_entities = []  # entities on the shared client; re-announced on reconnect
        logger.info("HomeAssistantClient initialized, connecting to MQTT broker in the background")

        self.device_info = DeviceInfo(
            name=config['name'],
            identifiers=[config['name'].lower().replace(' ', '_')],
            model=self.utils.model,
            manufacturer=self.utils.manufacturer,
            sw_version=self.utils.sw_version,
            hw_version=self.utils.hw_version,
            configuration_url=config.get('configuration_url', None)
        )
        # Button/Switch/Select each open and connect their own MQTT client internally
        # (the library's Subscriber base class requires this), so they use their own settings.
        self.mqtt_settings = Settings.MQTT(
            host=broker,
            username=username,
            password=password,
            port=port
        )
        # BinarySensor/Sensor don't manage their own connection, so it's safe (and cheaper)
        # for them to reuse the already-connecting shared client instead of opening a new one.
        self.shared_mqtt_settings = Settings.MQTT(
            host=broker,
            username=username,
            password=password,
            port=port,
            client=self.client
        )

    def _rebroadcast_availability_on_reconnect(self, entity):
        """Button/Switch/Select each own a separate MQTT client (see the comment in
        __init__), so the shared client's on_connect handler above never fires for them.
        Wrap their client's on_connect so a dropped-and-restored connection re-announces
        "online" too, without disturbing the library's own on_connect (command re-subscribe)."""
        original_on_connect = entity.mqtt_client.on_connect
        def on_connect(client, userdata, *args):
            if original_on_connect:
                original_on_connect(client, userdata, *args)
            entity.set_availability(True)
        entity.mqtt_client.on_connect = on_connect

    def setup_discovery(self):
        if 'binary_sensors' in self.entities and len(self.entities['binary_sensors']) > 0:
            self.setup_binary_sensors()
        if 'buttons' in self.entities and len(self.entities['buttons']) > 0:
            self.setup_buttons()
        if 'sensors' in self.entities and len(self.entities['sensors']) > 0:
            self.setup_sensors()
        if 'switches' in self.entities and len(self.entities['switches']) > 0:
            self.setup_switches()
        if 'selects' in self.entities and len(self.entities['selects']) > 0:
            self.setup_selects()

    def setup_binary_sensors(self):
        for sensor in self.entities['binary_sensors']:
            try:
                sensor_info = BinarySensorInfo(
                    name=sensor['name'],
                    device=self.device_info,
                    unique_id=sensor['unique_id'],
                    icon=sensor.get('icon', None),
                    device_class=sensor.get('device_class', None),       # https://www.home-assistant.io/integrations/binary_sensor/#device-class
                    entity_category=sensor.get('entity_category', None), # https://developers.home-assistant.io/docs/core/entity/#generic-properties
                    enabled_by_default=sensor.get('enabled_by_default', None),
                    expire_after=self.config.get('expire_after', None),
                    force_update=True
                )
                sensor_settings = Settings(mqtt=self.shared_mqtt_settings, entity=sensor_info, manual_availability=True)
                binary_sensor = BinarySensor(sensor_settings)
                binary_sensor.write_config()
                binary_sensor.set_availability(True)
                setattr(self, f"{sensor['unique_id']}_entity", binary_sensor)
                self._shared_entities.append(binary_sensor)
                self.update_binary_sensor(sensor['unique_id'], False)

                # Resolve and set the initial state
                state_method = sensor.get('state')
                state = None
                if state_method:
                    try:
                        # Resolve dotted paths like "utils.get_ip_address"
                        parts = state_method.split('.')
                        obj = self
                        for part in parts[:-1]:  # Traverse to the parent object
                            obj = getattr(obj, part)
                        method = getattr(obj, parts[-1])  # Get the final method
                        if callable(method):
                            state = method()  # Call the resolved method
                    except AttributeError as e:
                        logger.error(f"Error resolving state method {state_method} for sensor {sensor['unique_id']}: {e}")

                # Set the sensor state or log a warning if state is None
                if state is not None:
                    binary_sensor.update_state(state)
                    logger.info(f"Sensor {sensor['unique_id']} initialized with state: {state}")
                else:
                    logger.warning(f"Sensor {sensor['unique_id']} state is None or could not be resolved")
            except Exception as e:
                logger.warning(f"Failed to set up binary sensor {sensor.get('unique_id')}: {e}")

    def setup_buttons(self):
        for button in self.entities['buttons']:
            try:
                button_info = ButtonInfo(
                    name=button['name'],
                    device=self.device_info,
                    unique_id=button['unique_id'],
                    icon=button.get('icon', None),
                    device_class=button.get('device_class', None),       # https://www.home-assistant.io/integrations/button/#device-class
                    entity_category=button.get('entity_category', None), # https://developers.home-assistant.io/docs/core/entity/#generic-properties
                    enabled_by_default=button.get('enabled_by_default', None),
                    retain=button.get('retain', None),
                    expire_after=self.config.get('expire_after', None),
                    force_update=True
                )
                button_settings = Settings(mqtt=self.mqtt_settings, entity=button_info, manual_availability=True)
                button_entity = Button(button_settings, self.create_button_callback(button['callback'], button.get('args')))
                button_entity.write_config()
                button_entity.set_availability(True)
                self._rebroadcast_availability_on_reconnect(button_entity)
                setattr(self, f"{button['unique_id']}_entity", button_entity)
            except Exception as e:
                logger.warning(f"Failed to set up button {button.get('unique_id')}: {e}")

    def create_button_callback(self, method_name, args=None):
        def callback(client, userdata, message):
            try:
                # Resolve dotted paths like "tv.standby"
                parts = method_name.split('.')
                obj = self
                for part in parts[:-1]:  # Traverse to the parent object
                    obj = getattr(obj, part)
                method = getattr(obj, parts[-1])  # Get the final method
                method(*args) if args else method()  # Call the resolved method
            except AttributeError as e:
                logger.error(f"Callback method not found: {e}")
        return callback
    
    def _build_apps_options(self, none_option):
        """Shared by both the "{{apps_all}}" and "{{apps}}" options shorthands: build the
        options list and canonical<->display maps from apps.yaml, using each app's display
        `name` (falling back to its apps.yaml key if it has none), plus `none_option` (if
        any) as a first "no app" entry. Callbacks receive the app's key (or `none_option`),
        not the display name shown in HA."""
        apps = self.supervisor.apps.apps
        to_display = {}
        to_canonical = {}
        if none_option:
            to_display[none_option] = none_option
            to_canonical[none_option] = none_option
        for key, app_config in apps.items():
            display = app_config.get('name', key)
            to_display[key] = display
            to_canonical[display] = key
        options = ([none_option] if none_option else []) + [to_display[key] for key in apps.keys()]
        return options, to_canonical, to_display

    def _apps_all_options(self):
        """"{{apps_all}}": a NONE_APP_OPTION option (meaning "don't auto-start anything")
        followed by every configured app. Used by selects like the default startup app."""
        return self._build_apps_options(NONE_APP_OPTION)

    def _apps_options(self):
        """"{{apps}}": a NO_APP_RUNNING option (meaning "stop whatever's running")
        followed by every configured app. Used by selects that reflect/control the app
        that's actually running right now, like the app switcher."""
        return self._build_apps_options(NO_APP_RUNNING)

    def _to_display(self, unique_id, canonical_value):
        return self._select_maps.get(unique_id, {}).get('to_display', {}).get(canonical_value, canonical_value)

    def _to_canonical(self, unique_id, display_value):
        return self._select_maps.get(unique_id, {}).get('to_canonical', {}).get(display_value, display_value)

    def setup_selects(self):
        for select in self.entities['selects']:
            try:
                unique_id = select['unique_id']
                raw_options = select['options']
                uses_apps_all = raw_options == APPS_ALL_OPTIONS
                uses_apps = raw_options == APPS_OPTIONS
                uses_apps_shorthand = uses_apps_all or uses_apps

                if uses_apps_shorthand:
                    options, to_canonical, to_display = self._apps_all_options() if uses_apps_all else self._apps_options()
                    self._select_maps[unique_id] = {'to_canonical': to_canonical, 'to_display': to_display}
                else:
                    options = raw_options

                # entities.yaml's default_option (if any) is a canonical value (an app key,
                # or NONE_APP_OPTION) and wins if set. Otherwise, a "{{apps_all}}" select
                # falls back to whatever config.yaml's default_app actually is — the app
                # that will really auto-start — so the select reflects reality instead of
                # showing a generic "nothing selected" placeholder.
                default_option = select.get('default_option')
                if default_option is None and uses_apps_all:
                    default_option = self.supervisor.config.get('default_app') or NONE_APP_OPTION

                select_info = SelectInfo(
                    name=select['name'],
                    device=self.device_info,
                    unique_id=unique_id,
                    options=options,
                    icon=select.get('icon', None),
                    entity_category=select.get('entity_category', None), # https://developers.home-assistant.io/docs/core/entity/#generic-properties
                    enabled_by_default=select.get('enabled_by_default', None),
                    retain=select.get('retain', None),
                    expire_after=self.config.get('expire_after', None),
                    force_update=True
                )
                select_settings = Settings(mqtt=self.mqtt_settings, entity=select_info, manual_availability=True)
                select_entity = Select(select_settings, self.create_select_callback(select['callback'], unique_id))
                select_entity.write_config()
                select_entity.set_availability(True)
                self._rebroadcast_availability_on_reconnect(select_entity)
                setattr(self, f"{unique_id}_entity", select_entity)

                # Persisted setting (if any) wins over the entities.yaml fallback default.
                # A persisted value that's no longer valid (e.g. an app key from before it
                # was renamed in apps.yaml) would publish a state outside the entity's
                # declared `options`, which HA shows as "unknown" — fall back instead.
                valid_values = set(to_display.keys()) if uses_apps_shorthand else set(options)
                current_value = self.supervisor.settings_store.get(unique_id, default_option)
                if current_value not in valid_values:
                    if current_value is not None:
                        logger.warning(f"Persisted value '{current_value}' for select '{unique_id}' is no longer valid; falling back to '{default_option}'")
                    current_value = default_option

                if current_value:
                    select_entity.set_options(self._to_display(unique_id, current_value))
            except Exception as e:
                logger.warning(f"Failed to set up select {select.get('unique_id')}: {e}")

    def create_select_callback(self, method_name, unique_id):
        def callback(client, userdata, message):
            payload = message.payload.decode()
            canonical_value = self._to_canonical(unique_id, payload)
            try:
                method = getattr(self.supervisor, method_name)
                method(canonical_value)
            except AttributeError as e:
                logger.error(f"Callback method not found: {e}")
        return callback

    def setup_sensors(self):
        for sensor in self.entities['sensors']:
            try:
                sensor_info = SensorInfo(
                    name=sensor['name'],
                    device=self.device_info,
                    unique_id=sensor['unique_id'],
                    unit_of_measurement=sensor.get('unit_of_measurement', None),
                    icon=sensor.get('icon', None),
                    device_class=sensor.get('device_class', None),       # https://www.home-assistant.io/integrations/sensor/#device-class
                    entity_category=sensor.get('entity_category', None), # https://developers.home-assistant.io/docs/core/entity/#generic-properties
                    state_class=sensor.get('state_class', None),       # https://developers.home-assistant.io/docs/core/entity/#state-class
                    enabled_by_default=sensor.get('enabled_by_default', None),
                    expire_after=self.config.get('expire_after', None),
                    force_update=True
                )
                sensor_settings = Settings(mqtt=self.shared_mqtt_settings, entity=sensor_info, manual_availability=True)
                sensor_entity = Sensor(sensor_settings)
                sensor_entity.write_config()
                sensor_entity.set_availability(True)
                self._shared_entities.append(sensor_entity)
                setattr(self, f"{sensor['unique_id']}_entity", sensor_entity)

                # Resolve and set the initial state
                state_method = sensor.get('state')
                state = None
                if state_method:
                    try:
                        # Resolve dotted paths like "utils.get_ip_address"
                        parts = state_method.split('.')
                        obj = self
                        for part in parts[:-1]:  # Traverse to the parent object
                            obj = getattr(obj, part)
                        method = getattr(obj, parts[-1])  # Get the final method
                        if callable(method):
                            state = method()  # Call the resolved method
                    except AttributeError as e:
                        logger.error(f"Error resolving state method {state_method} for sensor {sensor['unique_id']}: {e}")

                # Set the sensor state or log a warning if state is None
                if state is not None:
                    sensor_entity.set_state(state)
                    logger.info(f"Sensor {sensor['unique_id']} initialized with state: {state}")
                else:
                    logger.warning(f"Sensor {sensor['unique_id']} state is None or could not be resolved")
            except Exception as e:
                logger.warning(f"Failed to set up sensor {sensor.get('unique_id')}: {e}")

    def setup_switches(self):
        for switch in self.entities['switches']:
            try:
                switch_info = SwitchInfo(
                    name=switch['name'],
                    device=self.device_info,
                    unique_id=switch['unique_id'],
                    icon=switch.get('icon', None),
                    device_class=switch.get('device_class', None),       # https://www.home-assistant.io/integrations/switch/#device-class
                    entity_category=switch.get('entity_category', None), # https://developers.home-assistant.io/docs/core/entity/#generic-properties
                    enabled_by_default=switch.get('enabled_by_default', None),
                    retain=switch.get('retain', None),
                    expire_after=self.config.get('expire_after', None),
                    force_update=True
                )
                switch_settings = Settings(mqtt=self.mqtt_settings, entity=switch_info, manual_availability=True)
                switch_entity = Switch(switch_settings, self.create_switch_callback(switch['on_callback'], switch['off_callback']))
                switch_entity.write_config()
                switch_entity.set_availability(True)
                self._rebroadcast_availability_on_reconnect(switch_entity)
                setattr(self, f"{switch['unique_id']}_entity", switch_entity)

                # Resolve and set the initial state
                state_method = switch.get('state')
                state = None
                if state_method:
                    try:
                        # Resolve dotted paths like "tv.check_power_status"
                        parts = state_method.split('.')
                        obj = self
                        for part in parts:  # Traverse through the parts to resolve the object
                            obj = getattr(obj, part)
                        if callable(obj):
                            state = obj()  # Call the resolved method
                        else:
                            logger.warning(f"State method {state_method} is not callable for switch {switch['unique_id']}")
                    except AttributeError as e:
                        logger.error(f"Error resolving state method {state_method} for switch {switch['unique_id']}: {e}")
                        state = False

                # Set the switch state based on the resolved state
                if state:
                    switch_entity.on()
                else:
                    switch_entity.off()
            except Exception as e:
                logger.warning(f"Failed to set up switch {switch.get('unique_id')}: {e}")

    def create_switch_callback(self, on_callback, off_callback):
        def callback(client: mqtt.Client, userdata, message: mqtt.MQTTMessage):
            payload = message.payload.decode()
            try:
                if payload == "ON":
                    # Resolve dotted paths for the ON callback
                    parts = on_callback.split('.')
                    obj = self
                    for part in parts[:-1]:  # Traverse to the parent object
                        obj = getattr(obj, part)
                    method = getattr(obj, parts[-1])  # Get the final method
                    method()  # Call the resolved method
                elif payload == "OFF":
                    # Resolve dotted paths for the OFF callback
                    parts = off_callback.split('.')
                    obj = self
                    for part in parts[:-1]:  # Traverse to the parent object
                        obj = getattr(obj, part)
                    method = getattr(obj, parts[-1])  # Get the final method
                    method()  # Call the resolved method
            except AttributeError as e:
                logger.error(f"Callback method not found: {e}")
        return callback

    def on_connect(self, client, userdata, flags, rc):
        logger.info(f"Connected to MQTT broker with result code {rc}")
        # The MQTT LWT clears an entity's retained availability to "offline" the moment
        # any connection drop is detected, even if the underlying client reconnects on
        # its own right after — so every (re)connect needs to re-announce "online".
        for entity in self._shared_entities:
            entity.set_availability(True)

    def on_disconnect(self, client, userdata, rc):
        logger.warning(f"Disconnected from MQTT broker (result code {rc}); will keep retrying in the background")

    def on_message(self, client, userdata, message):
        topic = message.topic.split("/")[-1]
        self.retained_values[topic] = message.payload.decode()

    def get_retained_value(self, unique_id):
        return self.retained_values.get(unique_id, None)
    
    def update_binary_sensor(self, unique_id, state):
        binary_sensor = getattr(self, f"{unique_id}_entity", None)
        if binary_sensor:
            binary_sensor.update_state(state)
        else:
            logger.warning(f"Binary sensor with unique_id {unique_id} not found.")

    def update_sensor(self, unique_id, state):
        sensor = getattr(self, f"{unique_id}_entity", None)
        if sensor:
            sensor.set_state(state)
        else:
            logger.warning(f"Sensor with unique_id {unique_id} not found.")

    def update_select(self, unique_id, value):
        select_entity = getattr(self, f"{unique_id}_entity", None)
        if select_entity:
            select_entity.set_options(self._to_display(unique_id, value))
        else:
            logger.warning(f"Select with unique_id {unique_id} not found.")

    def update_switch(self, unique_id, state):
        switch = getattr(self, f"{unique_id}_entity", None)
        if switch:
            if state == "ON":
                switch.on()
            elif state == "OFF":
                switch.off()
        else:
            logger.warning(f"Switch with unique_id {unique_id} not found.")

    def cleanup(self):
        logger.info("Cleaning up Home Assistant client")

        # Publish "offline" availability for each entity
        for entity_type in ['sensors', 'switches', 'buttons', 'binary_sensors', 'selects']:
            if entity_type in self.entities:
                for entity in self.entities[entity_type]:
                    unique_id = entity['unique_id']
                    entity_id = getattr(self, f"{unique_id}_entity", None)
                    if entity_id:
                        entity_id.set_availability(False)
                        logger.info(f"Set {entity_type[:-1]} {unique_id} to offline")
                    else:
                        logger.warning(f"{entity_type[:-1].capitalize()} with unique_id {unique_id} not found.")

        # Stop MQTT client
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("Home Assistant client cleaned up")
