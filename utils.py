import psutil
import logging
import os

logger = logging.getLogger(__name__)

class Utils:
    def __init__(self, config, supervisor, tv):
        self.config = config
        self.supervisor = supervisor
        self.tv = tv
        global ip_address
        ip_address = Utils.get_ip_address()
        logger.info(f"IP address: {ip_address}")

        global disk_usage
        disk_usage = Utils.get_disk_usage()
        logger.info(f"Disk usage: {disk_usage}%")

    def get_ip_address(self):
        return psutil.net_if_addrs()['wlan0'][0].address

    @staticmethod
    def get_cpu_temperature():
        return psutil.sensors_temperatures()['cpu_thermal'][0].current

    @staticmethod
    def get_memory_usage():
        return psutil.virtual_memory().percent

    @staticmethod
    def get_swap_usage():
        return psutil.swap_memory().percent

    @staticmethod
    def get_disk_usage():
        return psutil.disk_usage('/').percent
    
    def update(self):
        """Update the system."""
        logger.warning("Updating the system!")
        os.system("sudo apt update && sudo apt upgrade -y")
        logger.info("System updated successfully!")
    
    def reboot(self):
        """Reboot the system."""
        logger.warning("Rebooting the system!")
        os.system("sudo reboot")

    def shutdown(self):
        """Shut down the system."""
        logger.warning("Shutting down the system!")
        self.tv.standby()
        os.system("sudo shutdown -h now")