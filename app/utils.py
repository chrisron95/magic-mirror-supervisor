import psutil
import logging
import os
import socket
import time

logger = logging.getLogger(__name__)


def format_duration(seconds):
    """Format a duration in seconds as a compact human string, e.g. "3d 4h 12m"."""
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class Utils:
    def __init__(self, config, secrets, supervisor, tv, buttons=None):
        self.config = config
        self.secrets = secrets
        self.supervisor = supervisor
        self.tv = tv
        self.buttons = buttons or []
        self._start_time = time.monotonic()  # for the "Supervisor Uptime" sensor

        self.hw_info = self.get_hw_info()
        self.sw_info = self.get_sw_info()
        self.serial = None
        self.manufacturer = None
        self.model = None

        global ip_address
        ip_address = self.get_ip_address()
        logger.info(f"IP address: {ip_address}")

        global disk_usage
        disk_usage = self.get_disk_usage()
        logger.info(f"Disk usage: {disk_usage}%")

    def get_ip_address(self):
        """Return the IP address of the first active interface (prefers wlan0, then eth0)."""
        interfaces = psutil.net_if_addrs()
        for preferred in ("wlan0", "eth0"):
            for addr in interfaces.get(preferred, []):
                if addr.family == socket.AF_INET:
                    return addr.address

        for name, addrs in interfaces.items():
            if name == "lo":
                continue
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    return addr.address

        logger.warning("Could not determine IP address from any network interface")
        return "Unknown"

    def has_network_connection(self, timeout=3):
        """Check connectivity by probing the MQTT broker (a stand-in for LAN/HA reachability)."""
        host = self.secrets.get('mqtt_broker')
        port = self.secrets.get('mqtt_port', 1883)
        if not host:
            return False
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def wait_for_network(self, timeout=30, interval=2):
        """Poll for network connectivity for up to `timeout` seconds."""
        elapsed = 0
        while elapsed < timeout:
            if self.has_network_connection():
                return True
            time.sleep(interval)
            elapsed += interval
        logger.warning(f"No network connection detected after waiting {timeout}s")
        return False

    def get_cpu_temperature(self):
        return psutil.sensors_temperatures()['cpu_thermal'][0].current

    def get_memory_usage(self):
        return psutil.virtual_memory().percent

    def get_swap_usage(self):
        return psutil.swap_memory().percent

    def get_disk_usage(self):
        return psutil.disk_usage('/').percent

    def get_pi_uptime(self):
        """Time since the Pi itself booted, for the "Pi Uptime" sensor."""
        return format_duration(time.time() - psutil.boot_time())

    def get_supervisor_uptime(self):
        """Time since this process started, for the "Supervisor Uptime" sensor."""
        return format_duration(time.monotonic() - self._start_time)

    def get_hw_info(self):
        """Get the Hardware Info."""
        with open('/proc/cpuinfo') as f:
            for line in f:
                if line.startswith('Revision'):
                    self.hw_version = line.split(':')[1].strip()
                    logger.info(f"Hardware version: {self.hw_version}")
                elif line.startswith('Serial'):
                    self.serial = line.split(':')[1].strip()
                    logger.info(f"Serial number: {self.serial}")
                elif line.startswith('Model'):
                    self.model = line.split(':')[1].strip()
                    self.manufacturer = ' '.join(self.model.split(' ')[0:2])
                    logger.info(f"Manufacturer: {self.manufacturer}")
                    logger.info(f"Model: {self.model}")
        
    def get_sw_info(self):
        with open('/etc/os-release') as f:
            for line in f:
                if line.startswith('PRETTY_NAME'):
                    version = line.split('=')[1].strip().replace('"', '')
                    self.sw_version = version
                    logger.info(f"Software version: {self.sw_version}")
                    return version
    
    def update_pi(self):
        """Update the system."""
        logger.warning("Updating the system!")
        os.system("sudo apt update && sudo apt upgrade -y && sudo reboot")
        logger.info("System updated successfully!")

    def update_supervisor(self):
        """Update the supervisor."""
        logger.warning("Updating and reloading Magic Mirror Supervisor!")
        user_home = self.config.get('user_home', os.path.expanduser('~'))
        os.system(f"cd {user_home}/magic-mirror-supervisor && git pull && sudo systemctl restart magic-mirror-supervisor.service")
        logger.info("Supervisor updated and reloaded successfully!")

    def restart_supervisor(self):
        """Restart the supervisor."""
        logger.warning("Restarting Magic Mirror Supervisor!")
        os.system("sudo systemctl restart magic-mirror-supervisor.service")
        logger.info("Supervisor restarted successfully!")
    
    def reboot(self):
        """Reboot the system."""
        logger.warning("Rebooting the system!")
        os.system("sudo reboot")

    def shutdown(self):
        """Shut down the system."""
        logger.warning("Shutting down the system!")
        self.tv.standby()
        os.system("sudo shutdown -h now")

    def cleanup_gpios(self):
        """Cleanup GPIOs."""
        logger.info("Cleaning up GPIOs")
        for button in self.buttons:
            button.cleanup()
        logger.info("GPIOs cleaned up successfully!")