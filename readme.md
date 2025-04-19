# Magic Mirror Supervisor

**Original Project Created in 2025 by Chris Heder (GitHub: [@chrisron95](https://github.com/chrisron95)).**

**[Original repo](https://github.com/chrisron95/magic-mirror-supervisor)**

---

This Python-based **Magic Mirror Supervisor** manages and automates various functionalities for a **Magic Mirror** setup. The Magic Mirror is a 42" TV with a mirrored acrylic sheet and an IR touch screen overlay. It’s powered by a Raspberry Pi 4. I have the [MagicMirror2](https://magicmirror.builders/) software installed, but I prefer to run a **Chromium kiosk browser** displaying a Home Assistant dashboard.

This project includes features like **TV management**, **system monitoring**, and **Home Assistant integration**. You can control it via physical buttons or through Home Assistant. It also allows you to make changes to your Magic Mirror setup easily, supporting both automated system control and remote management via Home Assistant.

### Key Features:
- **TV Control**: Power on/off the TV, switch between inputs (e.g., Raspberry Pi vs HDMI), monitor TV status.
- **Home Assistant Integration**: Auto-discovery of devices and sensors for controlling and monitoring via MQTT.
- **System Monitoring**: Track CPU temperature, memory usage, disk space, network IP address, etc.
- **GPIO Button Control**: Use physical buttons for common actions (e.g., reboot, shutdown, update, switch apps).
- **App Switching**: Toggle between Magic Mirror and Home Assistant interfaces.
- **System Management**: Reboot, shutdown, update, and pull the latest repo changes via button press.

---

## Table of Contents

- [Magic Mirror Supervisor](#magic-mirror-supervisor)
    - [Key Features:](#key-features)
  - [Table of Contents](#table-of-contents)
  - [Forking and Customization](#forking-and-customization)
    - [Steps to Fork and Customize:](#steps-to-fork-and-customize)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Configuration](#configuration)
    - [**config.yaml**](#configyaml)
    - [**secrets.yaml**](#secretsyaml)
    - [**entities.yaml**](#entitiesyaml)
  - [Usage](#usage)
  - [Project Structure](#project-structure)

---

## Forking and Customization

This project is highly customizable. **Feel free to fork this repository** to tailor it to your specific needs! Here’s how you can do that:

### Steps to Fork and Customize:
1. **Fork the Repo**:
   - Click the "Fork" button on the [GitHub repository page](https://github.com/chrisron95/magic-mirror-supervisor).

2. **Clone Your Fork**:
   - Clone your fork to your local machine:

     ```bash
     git clone https://github.com/your-username/magic-mirror-supervisor.git
     cd magic-mirror-supervisor
     ```

3. **Make Custom Changes**:
   - You can now modify the code, configuration files, and entities to suit your needs. For example, you can add more buttons, change GPIO pin assignments, or modify how the TV is controlled.

4. **Push Changes**:
   - Once you've made your customizations, push them back to your fork:

     ```bash
     git commit -am "Customized for my setup"
     git push origin main
     ```

5. **Update Your Pi**:
   - To apply your changes on your Raspberry Pi, SSH into your Pi and navigate to your repository directory:

     ```bash
     ssh pi@your-pi-ip
     cd /home/pi/magic-mirror-supervisor  # Adjust path if necessary
     ```

   - Pull the latest changes from your fork:

     ```bash
     git pull origin main
     ```

   - If you set up a button for updating the repo and restarting the service, you can press that button to pull the changes and restart the supervisor automatically. Otherwise, restart the service manually:

     ```bash
     sudo systemctl restart magic-mirror-supervisor.service
     ```

---

## Prerequisites

Before getting started, please ensure the following are already set up:

- **Home Assistant**: Must be installed and configured with MQTT enabled.
- **MQTT Broker**: Ensure you have an MQTT broker running (e.g., [Mosquitto](https://mosquitto.org/)).
- **Raspberry Pi**: With Raspberry Pi OS installed and connected to your network.
- **MagicMirror2**: Already set up on the Raspberry Pi for the Magic Mirror interface.
- **IR Touch Screen Overlay**: The setup assumes you have an IR touch screen overlay for the mirror, such as the [IR Touch Screen on Amazon](https://a.co/d/fW02iNM) that makes it a touchscreen interface.

---

## Installation

1. **Clone the Repository**:

    Replace the repository URL with yours if you forked your own version

    ```bash
    git clone https://github.com/chrisron95/magic-mirror-supervisor.git
    cd magic-mirror-supervisor
    ```

2. **Set up a Python Virtual Environment (Recommended)**:

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate
    ```

3. **Install Required Dependencies**:

    Use the `requirements.txt` file to install necessary Python libraries:

    ```bash
    pip install -r requirements.txt
    ```

4. **Set Up Systemd Service** (only after the script is working):

    **Note**: You should follow these steps **after** the script is working correctly for you. Since there can only be **one instance** of this script running (due to HDMI access), it is best to ensure everything is working before setting it up as a system service.

    - Create the systemd service file at `/lib/systemd/system/magic-mirror-supervisor.service`:

        ```bash
        sudo nano /lib/systemd/system/magic-mirror-supervisor.service
        ```

    - Paste the following configuration into the file:
        
        **Note**: make sure to replace `pi` with your Pi's username if necessary

        ```ini
        [Unit]
        Description=Magic Mirror Supervisor
        After=multi-user.target

        [Service]
        Type=idle
        ExecStart=/home/pi/magic-mirror-supervisor/.venv/bin/python3 /home/pi/magic-mirror-supervisor/main.py
        Environment=DISPLAY=:0
        WorkingDirectory=/home/pi/magic-mirror-supervisor
        User=pi

        [Install]
        WantedBy=multi-user.target
        ```

    - Enable and start the service:

        ```bash
        sudo systemctl daemon-reload
        sudo systemctl enable magic-mirror-supervisor.service
        sudo systemctl start magic-mirror-supervisor.service
        ```

        To monitor the live logs of the systemd service, run the following command:

        ```bash
        journalctl -f -u magic-mirror-supervisor.service
        ```

---

## Configuration

The system configuration is handled through the following YAML files:

### **config.yaml**
This file contains general configuration like the device name, model, logging level, and MQTT settings.

```yaml
name: "Magic Mirror"
manufacturer: "Raspberry Pi"
model: "4 Model B"
log_level: "INFO"
```

- **name**: Name of your device as it appears in Home Assistant.
- **log_level**: Set the logging level (e.g., `INFO`, `DEBUG`).

### **secrets.yaml**
This file stores sensitive data, such as MQTT credentials and Home Assistant API tokens.

```yaml
mqtt_broker: "your-mqtt-broker.local"
mqtt_port: 1883
mqtt_username: "your-mqtt-username"
mqtt_password: "your-mqtt-password"
```

- **mqtt_broker**: The hostname or IP address of your MQTT broker.
- **mqtt_port**: The port for the MQTT broker (default is `1883`).
- **mqtt_username** and **mqtt_password**: Credentials for the MQTT broker.
- **ha_api_token**: Your Home Assistant API token.
- **ha_api_url**: URL of your Home Assistant instance.

### **entities.yaml**
This file defines the entities (buttons, sensors, switches) that will be discovered in Home Assistant, and the method to use for them.

```yaml
binary_sensors:
  - name: "TV Power"
    unique_id: "tv_power"
    function: "tv.check_power_status"

sensors:
  - name: "IP Address"
    unique_id: "ip_address"
    state: "utils.get_ip_address"
  - name: "CPU Temperature"
    unique_id: "cpu_temperature"
    state: "utils.get_cpu_temperature"
  - name: "Memory Usage"
    unique_id: "memory_usage"
    state: "utils.get_memory_usage"
  - name: "Disk Usage"
    unique_id: "disk_usage"
    state: "utils.get_disk_usage"

buttons:
  - name: "Reboot"
    unique_id: "reboot"
    callback: "utils.reboot"

  - name: "Shutdown"
    unique_id: "shutdown"
    callback: "utils.shutdown"
```

- **binary_sensors**: Includes binary sensors like the TV's power status.
- **sensors**: Includes sensors like CPU temperature, memory usage, and disk space.
- **buttons**: Defines actions that buttons can trigger, such as reboot or shutdown.

---

## Usage

1. **Run the Supervisor**:

    After setting everything up, you can run the script manually to test if everything is working:

    ```bash
    python main.py
    ```

    This will start managing the Magic Mirror, checking and reporting TV state, monitoring buttons (immediately), and integration with Home Assistant.

2. **Control from Home Assistant**:

    Once integrated, you can control and monitor the following via Home Assistant:
    - **Switches**: Control TV power and input.
    - **Sensors**: Monitor system stats like IP address, CPU temperature, and memory usage.
    - **Buttons**: Trigger actions like reboot, shutdown, or app switching.

3. **Control via Physical Buttons**:

    The physical buttons connected to the Raspberry Pi perform various actions such as toggling TV power, switching between Magic Mirror and Home Assistant, stopping all applications to show the desktop, and more.

    The buttons currently support single press and hold functionality.

    These buttons are configured through the `ButtonHandler` class and interact with GPIO pins.

---

## Project Structure

Here's an overview of the main components:

- **`main.py`**: The main script that initializes and runs the Magic Mirror Supervisor, managing the TV, buttons, Home Assistant integration, and more.
- **`tv.py`**: Handles TV operations like turning it on/off, switching inputs, and checking the power status.
- **`buttons.py`**: Manages physical button interactions via GPIO.
- **`supervisor.py`**: Handles higher-level actions like switching apps, refreshing the kiosk, and stopping apps.
- **`home_assistant_client.py`**: Manages MQTT communication with Home Assistant, setting up sensors, buttons, and switches.
- **`utils.py`**: Provides utility functions like system stats (CPU temperature, memory usage), and system actions (reboot, shutdown).