# Magic Mirror Supervisor

**[Original Project](https://github.com/chrisron95/magic-mirror-supervisor) Created in 2025 by Chris Heder (GitHub: [@chrisron95](https://github.com/chrisron95)).**

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
    - [**config/config.yaml**](#configconfigyaml)
    - [**config/secrets.yaml**](#configsecretsyaml)
    - [**config/entities.yaml**](#configentitiesyaml)
    - [**config/apps.yaml**](#configappsyaml)
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
- **scrot** (optional): Only needed if an app in `apps.yaml` uses `liveness_check` (screenshot-based freeze detection). Install with `sudo apt install scrot`.

---

## Installation

1. **Clone the Repository**:

    Replace the repository URL with yours if you forked your own version

    ```bash
    git clone https://github.com/chrisron95/magic-mirror-supervisor.git
    cd magic-mirror-supervisor
    ```

2. **Set up a Python Virtual Environment (Recommended)**:

    Create a Python virtual environment:

    ```bash
    python3 -m venv .venv
    ```

    **Important**: To ensure your virtual environment works correctly with system packages, you **must** set `include-system-site-packages = True` in the `.venv/pyvenv.cfg` file.

    Edit the `.venv/pyvenv.cfg` file:

    ```bash
    nano .venv/pyvenv.cfg
    ```

    Add/modify the following line:

    ```ini
    include-system-site-packages = True
    ```

    After making this change, save and close the file.

    Then, activate the virtual environment:

    ```bash
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate
    ```

3. **Install Required Dependencies**:

    Use the `requirements.txt` file to install necessary Python libraries:

    ```bash
    pip install -r requirements.txt
    ```

4. **Set Up Systemd Service** (only after the script is working):

    **Note**: You should follow these steps **after** the script is working correctly for you. Since there can only be **one instance** of this script running (due to GPIO pins alredy being in use), it is best to ensure everything is working before setting it up as a system service.

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

The system configuration is handled through YAML files under `config/`:

### **config/config.yaml**
This file contains general configuration like the device name, model, logging level, and the fallback app to auto-start at boot.

```yaml
name: "Magic Mirror"
user_home: "/home/chris"
manufacturer: "Raspberry Pi"
model: "4 Model B"
log_level: "INFO"
default_app: "kiosk"
```

- **name**: Name of your device as it appears in Home Assistant.
- **user_home**: Absolute path to the Pi user's home directory. `apps.yaml` can reference it via `{{user_home}}` instead of hardcoding a path — used for things like the Chromium profile and the MagicMirror install location. Optional; defaults to whichever user the supervisor process runs as.
- **log_level**: Set the logging level (e.g., `INFO`, `DEBUG`).
- **default_app**: Which app (from `apps.yaml`) to start at boot if nothing's been selected yet via Home Assistant. See [entities.yaml](#configentitiesyaml) and [apps.yaml](#configappsyaml).

### **config/secrets.yaml**
This file stores sensitive data, such as MQTT credentials and Home Assistant API tokens. It's gitignored — never commit it.

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

### **config/entities.yaml**
This file defines the entities (buttons, sensors, switches, selects) that will be discovered in Home Assistant, and the method to use for them.

```yaml
binary_sensors:
  - name: "TV Power"
    unique_id: "tv_power"
    state: "tv.check_power_status"

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

  - name: "Start MagicMirror App"
    unique_id: "start_magicmirror"
    callback: "supervisor.start_app"
    args: ["magicmirror"]

selects:
  - name: "Default Startup App"
    unique_id: "default_app"
    options: ["kiosk", "magicmirror"]
    default_option: "kiosk"
    callback: "set_default_app"
```

- **binary_sensors** / **sensors**: Report device/system state (TV power, IP address, CPU temperature, etc.) back to Home Assistant.
- **buttons**: Defines actions that buttons can trigger, such as reboot, shutdown, or starting an app. `args` is optional and lets a button call a method with a fixed argument (e.g. `supervisor.start_app("magicmirror")`).
- **selects**: HA dropdown entities. The "Default Startup App" select lets you change which app auto-starts at boot without editing `config.yaml`; the choice is persisted in `data/settings.yaml`.

### **config/apps.yaml**
This file defines the apps the supervisor can launch (Chromium kiosk, MagicMirror, or anything you add — a game, a photo slideshow, etc.), replacing what used to be separate systemd services for each. See the comments in the file itself for the schema; `supervisor.start_app("name")` and the buttons/selects above are how you trigger one.

An entry can either reference a built-in **app type** via `app: "<type>"` (defined in `app/app_templates.py`) and just supply the instance-specific bits — for the `"kiosk"` type, that's normally just `url` (and `name`) — or define everything directly (`working_directory`, `environment`, `setup`, `background`, `command`, `restart`, `liveness_check`), the way `magicmirror` does. Adding a second kiosk pointed at a different dashboard is just:
```yaml
security_cam:
  app: "kiosk"
  name: "Security Camera"
  url: "http://192.168.1.70:8123/dashboard-camera/dashboard"
```
Any template field can also be overridden per-instance (e.g. a different `liveness_check` threshold for one specific kiosk). Adding a whole new *type* of app (not just another kiosk instance) means adding a new template to `app/app_templates.py`.

An app can optionally set `liveness_check` (`interval` / `stale_after`, in seconds) to catch a specific failure mode `restart: true` alone can't: a process that's still running but has hung (e.g. a frozen browser tab), rather than one that's actually exited. With it enabled, the supervisor periodically screenshots the display and restarts the app if the screen hasn't visibly changed for `stale_after` seconds — requires `scrot` installed on the Pi.

Each app's stdout/stderr log under `logs/` is capped at `AppManager.MAX_LOG_BYTES` (5 MB by default) and rotated to a single `.1` backup when it's exceeded, so log growth stays bounded regardless of uptime or how chatty an app's console output is.

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

```
magic-mirror-supervisor/
├── main.py                        # Entry point; wires everything together and runs the event loop
├── requirements.txt
├── app/                           # Application code
│   ├── tv.py                      # TV power/input control via HDMI-CEC
│   ├── buttons.py                 # GPIO button handling (press/hold)
│   ├── supervisor.py              # App switching, notifications, default-app selection
│   ├── apps.py                    # Launches/supervises the apps defined in config/apps.yaml
│   ├── app_templates.py           # Built-in app types (e.g. "kiosk") apps.yaml entries can reference
│   ├── home_assistant_client.py   # MQTT/Home Assistant discovery and entity sync
│   ├── settings_store.py          # Small persisted key/value store (data/settings.yaml)
│   └── utils.py                   # System stats and system actions (reboot, shutdown, updates)
├── config/                        # Deployment-specific configuration (see Configuration below)
│   ├── config.yaml
│   ├── secrets.yaml                (gitignored)
│   ├── entities.yaml
│   └── apps.yaml
├── data/
│   └── settings.yaml               (gitignored; written at runtime, e.g. the HA-selected default app)
├── logs/                           (gitignored; per-app stdout/stderr, size-capped and rotated)
├── sounds/                         # Audio assets
└── pi_files/                       # Reference copies of the systemd unit files installed on the Pi
```

- **`main.py`**: The main script that initializes and runs the Magic Mirror Supervisor, managing the TV, buttons, Home Assistant integration, and more.
- **`app/tv.py`**: Handles TV operations like turning it on/off, switching inputs, and checking the power status.
- **`app/buttons.py`**: Manages physical button interactions via GPIO.
- **`app/supervisor.py`**: Handles higher-level actions like switching apps, refreshing the kiosk, and stopping apps.
- **`app/apps.py`**: Starts, stops, and (if configured) auto-restarts the apps defined in `config/apps.yaml` — this is what replaced the old `kiosk.service`/`magicmirror.service` systemd units.
- **`app/app_templates.py`**: Defines built-in app types (currently just `"kiosk"`) so a new kiosk instance in `apps.yaml` only needs a `url`, not a full copy of the Chromium command/setup/environment.
- **`app/home_assistant_client.py`**: Manages MQTT communication with Home Assistant, setting up sensors, buttons, switches, and selects.
- **`app/settings_store.py`**: Persists small bits of runtime-changeable state (like the HA-selected default app) to `data/settings.yaml`, separate from the static `config/` files.
- **`app/utils.py`**: Provides utility functions like system stats (CPU temperature, memory usage), network connectivity checks, and system actions (reboot, shutdown).