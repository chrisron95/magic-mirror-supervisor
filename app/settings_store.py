import logging
import os
import yaml

logger = logging.getLogger(__name__)

class SettingsStore:
    """Persisted key/value store for settings that can change at runtime (e.g. via Home
    Assistant) and must survive a restart, separate from the static config.yaml."""

    def __init__(self, path="settings.yaml"):
        self.path = path
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _save(self):
        with open(self.path, "w") as f:
            yaml.safe_dump(self._data, f)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self._save()
        logger.info(f"Setting '{key}' updated to '{value}'")
