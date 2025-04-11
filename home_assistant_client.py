import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import time
import logging
from ha_mqtt_discoverable import Settings, DeviceInfo
from ha_mqtt_discoverable.sensors import BinarySensor, BinarySensorInfo, Button, ButtonInfo, Switch, SwitchInfo, Sensor, SensorInfo, Select, SelectInfo

logger = logging.getLogger(__name__)

class HomeAssistantClient:
    def __init__(self, broker, port, username, password, token, api_url, config, entities, supervisor, tv, utils):
        self.token = token
        self.api_url = api_url
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

        self.setup_discovery()

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
                entity_category="diagnostic"
            )
            sensor_settings = Settings(mqtt=self.mqtt_settings, entity=sensor_info)
            binary_sensor = BinarySensor(sensor_settings)
            binary_sensor.write_config()
            setattr(self, f"{sensor['unique_id']}_entity", binary_sensor)
            # self.update_binary_sensor(sensor['unique_id'], GPIO.input(self.config[sensor['gpio_pin']]) == GPIO.HIGH)

    def setup_buttons(self):
        for button in self.entities['buttons']:
            button_info = ButtonInfo(name=button['name'], device=self.device_info, unique_id=button['unique_id'])
            button_settings = Settings(mqtt=self.mqtt_settings, entity=button_info)
            button_entity = Button(button_settings, self.create_button_callback(button['callback']))
            button_entity.write_config()
            setattr(self, f"{button['unique_id']}_entity", button_entity)

    def create_button_callback(self, method_name):
        def callback(client, userdata, message):
            method = getattr(self, method_name)
            method()
        return callback
    
    def setup_selects(self):
        for select in self.entities['selects']:
            select_info = SelectInfo(
                name=select['name'],
                device=self.device_info,
                unique_id=select['unique_id'],
                options=select['options']
            )
            select_settings = Settings(mqtt=self.mqtt_settings, entity=select_info)
            select_entity = Select(select_settings, self.create_select_callback(select['callback']))
            select_entity.write_config()
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
                entity_category=sensor['entity_category'] if 'entity_category' in sensor else None,
                device_class=sensor['device_class'] if 'device_class' in sensor else None,
                unit_of_measurement=sensor['unit_of_measurement'] if 'unit_of_measurement' in sensor else None
            )
            sensor_settings = Settings(mqtt=self.mqtt_settings, entity=sensor_info)
            sensor_entity = Sensor(sensor_settings)
            sensor_entity.write_config()
            setattr(self, f"{sensor['unique_id']}_entity", sensor_entity)
            # get_state = getattr(self, sensor['state'])
            state = self.sensor['state']()
            if state is not None:
                sensor_entity.set_state(state)
            else:
                logger.warning(f"Sensor state is None for {sensor['unique_id']}")

    def setup_switches(self):
        for switch in self.entities['switches']:
            switch_info = SwitchInfo(
                name=switch['name'],
                device=self.device_info,
                unique_id=switch['unique_id']
            )
            switch_settings = Settings(mqtt=self.mqtt_settings, entity=switch_info)
            switch_entity = Switch(switch_settings, self.create_button_callback(switch['on_callback'], switch['off_callback']))
            switch_entity.write_config()
            setattr(self, f"{switch['unique_id']}_entity", switch_entity)
            get_state = getattr(self, switch['state'])
            state = get_state()
            if state == True:
                switch_entity.on()
            else:
                switch_entity.off()

    def create_switch_callback(self, on_callback, off_callback):
        def callback(client: mqtt.Client, userdata, message: mqtt.MQTTMessage):
            payload = message.payload.decode()
            try:
                if payload == "ON":
                    method = getattr(self.supervisor, on_callback)
                    method()
                elif payload == "OFF":
                    method = getattr(self.supervisor, off_callback)
                    method()
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
        self.client.publish(f"hmd/{self.config['name'].lower().replace(' ', '_')}/availability", "offline", retain=True)
        self.client.loop_stop()
        self.client.disconnect()
