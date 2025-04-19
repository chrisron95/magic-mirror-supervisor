import paho.mqtt.client as mqtt
import time
import logging
from ha_mqtt_discoverable import Settings, DeviceInfo
from ha_mqtt_discoverable.sensors import BinarySensor, BinarySensorInfo, Button, ButtonInfo, Switch, SwitchInfo, Sensor, SensorInfo, Select, SelectInfo

logger = logging.getLogger(__name__)

class HomeAssistantClient:
    def __init__(self, broker, port, username, password, config, entities, supervisor, tv, utils):
        self.client = mqtt.Client()
        self.client.username_pw_set(username, password)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.retained_values = {}
        self.client.connect(broker, port, 60)
        self.client.loop_start()
        self.config = config
        self.entities = entities
        self.supervisor = supervisor
        self.tv = tv
        self.utils = utils
        logger.info("HomeAssistantClient initialized and connected to MQTT broker")

        self.device_info = DeviceInfo(
            name=config['name'],
            identifiers=[config['name'].lower().replace(' ', '_')],
            model=config['model'],
            manufacturer=config['manufacturer']
        )
        self.mqtt_settings = Settings.MQTT(
            host=broker,
            username=username,
            password=password,
            port=port
        )

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
            sensor_info = BinarySensorInfo(
                name=sensor['name'],
                device=self.device_info,
                unique_id=sensor['unique_id'],
                icon=sensor.get('icon', None),
                device_class=sensor.get('device_class', None),       # https://www.home-assistant.io/integrations/binary_sensor/#device-class
                entity_category=sensor.get('entity_category', None), # https://developers.home-assistant.io/docs/core/entity/#generic-properties
                enabled_by_default=sensor.get('enabled_by_default', None),
                expire_after=self.config.get('expire_after', None),
                force_update=self.config.get('force_update', None)
            )
            sensor_settings = Settings(mqtt=self.mqtt_settings, entity=sensor_info, manual_availability=True)
            binary_sensor = BinarySensor(sensor_settings)
            binary_sensor.write_config()
            binary_sensor.set_availability(True)
            setattr(self, f"{sensor['unique_id']}_entity", binary_sensor)
            # self.update_binary_sensor(sensor['unique_id'], False)

    def setup_buttons(self):
        for button in self.entities['buttons']:
            button_info = ButtonInfo(
                name=button['name'],
                device=self.device_info,
                unique_id=button['unique_id'],
                icon=button.get('icon', None),
                device_class=button.get('device_class', None),       # https://www.home-assistant.io/integrations/button/#device-class
                entity_category=button.get('entity_category', None), # https://developers.home-assistant.io/docs/core/entity/#generic-properties
                enabled_by_default=button.get('enabled_by_default', None),
                expire_after=self.config.get('expire_after', None),
                force_update=self.config.get('force_update', None)
            )
            button_settings = Settings(mqtt=self.mqtt_settings, entity=button_info, manual_availability=True)
            button_entity = Button(button_settings, self.create_button_callback(button['callback']))
            button_entity.write_config()
            button_entity.set_availability(True)
            setattr(self, f"{button['unique_id']}_entity", button_entity)

    def create_button_callback(self, method_name):
        def callback(client, userdata, message):
            try:
                # Resolve dotted paths like "tv.standby"
                parts = method_name.split('.')
                obj = self
                for part in parts[:-1]:  # Traverse to the parent object
                    obj = getattr(obj, part)
                method = getattr(obj, parts[-1])  # Get the final method
                method()  # Call the resolved method
            except AttributeError as e:
                logger.error(f"Callback method not found: {e}")
        return callback
    
    def setup_selects(self):
        for select in self.entities['selects']:
            select_info = SelectInfo(
                name=select['name'],
                device=self.device_info,
                unique_id=select['unique_id'],
                options=select['options'],
                icon=select.get('icon', None),
                entity_category=select.get('entity_category', None), # https://developers.home-assistant.io/docs/core/entity/#generic-properties
                enabled_by_default=select.get('enabled_by_default', None),
                expire_after=self.config.get('expire_after', None),
                force_update=self.config.get('force_update', None)
            )
            select_settings = Settings(mqtt=self.mqtt_settings, entity=select_info, manual_availability=True)
            select_entity = Select(select_settings, self.create_select_callback(select['callback']))
            select_entity.write_config()
            select_entity.set_availability(True)
            setattr(self, f"{select['unique_id']}_entity", select_entity)
            select_entity.set_options(select['default_option'])

    def create_select_callback(self, method_name):
        def callback(client, userdata, message):
            payload = message.payload.decode()
            try:
                method = getattr(self.supervisor, method_name)
                method(payload)
            except AttributeError as e:
                logger.error(f"Callback method not found: {e}")
        return callback

    def setup_sensors(self):
        for sensor in self.entities['sensors']:
            sensor_info = SensorInfo(
                name=sensor['name'],
                device=self.device_info,
                unique_id=sensor['unique_id'],
                unit_of_measurement=sensor.get('unit_of_measurement', None),
                icon=sensor.get('icon', None),
                device_class=sensor.get('device_class', None),       # https://www.home-assistant.io/integrations/sensor/#device-class
                entity_category=sensor.get('entity_category', None), # https://developers.home-assistant.io/docs/core/entity/#generic-properties
                enabled_by_default=sensor.get('enabled_by_default', None),
                expire_after=self.config.get('expire_after', None),
                force_update=self.config.get('force_update', None)
            )
            sensor_settings = Settings(mqtt=self.mqtt_settings, entity=sensor_info, manual_availability=True)
            sensor_entity = Sensor(sensor_settings)
            sensor_entity.write_config()
            sensor_entity.set_availability(True)
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

    def setup_switches(self):
        for switch in self.entities['switches']:
            switch_info = SwitchInfo(
                name=switch['name'],
                device=self.device_info,
                unique_id=switch['unique_id'],
                icon=switch.get('icon', None),
                device_class=switch.get('device_class', None),       # https://www.home-assistant.io/integrations/switch/#device-class
                entity_category=switch.get('entity_category', None), # https://developers.home-assistant.io/docs/core/entity/#generic-properties
                enabled_by_default=switch.get('enabled_by_default', None),
                expire_after=self.config.get('expire_after', None),
                force_update=self.config.get('force_update', None)
            )
            switch_settings = Settings(mqtt=self.mqtt_settings, entity=switch_info, manual_availability=True)
            switch_entity = Switch(switch_settings, self.create_switch_callback(switch['on_callback'], switch['off_callback']))
            switch_entity.write_config()
            switch_entity.set_availability(True)
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
        self.client.publish(f"hmd/{self.config['name'].lower().replace(' ', '_')}/availability", "online", retain=True)

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
