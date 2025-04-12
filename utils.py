import psutil
import logging
import os

logger = logging.getLogger(__name__)

class Utils:
    def __init__(self, config, supervisor, tv, button1, button2, button3):
        self.config = config
        self.supervisor = supervisor
        self.tv = tv
        global ip_address
        ip_address = self.get_ip_address()
        logger.info(f"IP address: {ip_address}")

        global disk_usage
        disk_usage = self.get_disk_usage()
        logger.info(f"Disk usage: {disk_usage}%")

    def get_ip_address(self):
        return psutil.net_if_addrs()['wlan0'][0].address

    def get_cpu_temperature(self):
        return psutil.sensors_temperatures()['cpu_thermal'][0].current

    def get_memory_usage(self):
        return psutil.virtual_memory().percent

    def get_swap_usage(self):
        return psutil.swap_memory().percent

    def get_disk_usage(self):
        return psutil.disk_usage('/').percent
    
    def update(self):
        """Update the system."""
        logger.warning("Updating the system!")
        os.system("sudo apt update && sudo apt upgrade -y")
        logger.info("System updated successfully!")

    def reload_supervisor(self):
        """Reload the Magic Mirror application."""
        logger.warning("Reloading Magic Mirror!")
        os.system("cd ~/magic-mirror-supervisor && git pull && sudo systemctl restart magic-mirror-supervisor.service")
        logger.info("Magic Mirror reloaded successfully!")
    
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
        self.button1.cleanup()
        self.button2.cleanup()
        self.button3.cleanup()
        logger.info("GPIOs cleaned up successfully!")