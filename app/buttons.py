from gpiozero import Button
import logging
import threading
import yaml


class ButtonHandler:
    """Wraps a gpiozero Button with press-count (single/double/triple/...) and hold
    disambiguation. `press_callbacks` maps press count -> callable; any count without an
    entry is simply ignored. A hold always suppresses whatever press count it interrupts."""

    MULTI_PRESS_WINDOW = 0.35  # seconds to wait for another press before dispatching

    def __init__(self, name, pin, press_callbacks=None, hold_callback=None, hold_time=1):
        self.name = name
        self.press_callbacks = press_callbacks or {}
        self.hold_callback = hold_callback
        self.button = Button(pin, bounce_time=0.05, hold_time=hold_time)

        self._lock = threading.Lock()
        self._press_count = 0
        self._timer = None
        self._was_held = False  # prevents a held button's release from also counting as a press

        if self.press_callbacks:
            self.button.when_released = self._on_released
        if self.hold_callback:
            self.button.when_held = self._on_held

    def _on_released(self):
        with self._lock:
            if self._was_held:
                self._was_held = False
                return
            self._press_count += 1
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.MULTI_PRESS_WINDOW, self._dispatch)
            self._timer.daemon = True
            self._timer.start()

    def _dispatch(self):
        with self._lock:
            count = self._press_count
            self._press_count = 0
        callback = self.press_callbacks.get(count)
        if callback:
            logging.info(f"{self.name} pressed x{count}")
            callback()
        else:
            logging.info(f"{self.name} pressed x{count} (no action configured)")

    def _on_held(self):
        with self._lock:
            self._was_held = True
            self._press_count = 0
            if self._timer:
                self._timer.cancel()
        logging.info(f"{self.name} held")
        self.hold_callback()

    def cleanup(self):
        """Cleanup the button handler."""
        if self._timer:
            self._timer.cancel()
        self.button.close()
        logging.info(f"{self.name} button handler cleaned up.")


def _resolve(context, dotted_path):
    """Resolve a dotted path like "tv.toggle_power" against `context` (an object exposing
    `tv`/`supervisor`/`utils`), the same way entities.yaml callbacks are resolved."""
    obj = context
    for part in dotted_path.split('.'):
        obj = getattr(obj, part)
    return obj


def _build_action(context, spec):
    """`spec` is a single dotted-path string, or a list of them to run in order (e.g. a
    hold that should both stop the TV and shut down)."""
    paths = [spec] if isinstance(spec, str) else list(spec)
    resolved = []
    for path in paths:
        try:
            resolved.append(_resolve(context, path))
        except AttributeError as e:
            logging.error(f"Button action '{path}' could not be resolved: {e}")

    def run():
        for callback in resolved:
            callback()
    return run


def load_buttons(path, context):
    """Load config/buttons.yaml and construct a ButtonHandler per entry. Each entry's
    `triggers` map (press count, or "hold") holds dotted paths (or lists of them)
    resolved against `context`."""
    with open(path, 'r') as f:
        config = yaml.safe_load(f) or {}

    handlers = []
    for entry in config.get('buttons', []):
        triggers = entry.get('triggers') or {}
        press_callbacks = {
            count: _build_action(context, spec)
            for count, spec in triggers.items() if count != 'hold'
        }
        hold_spec = triggers.get('hold')
        hold_callback = _build_action(context, hold_spec) if hold_spec else None

        handlers.append(ButtonHandler(
            entry['name'],
            entry['pin'],
            press_callbacks=press_callbacks,
            hold_callback=hold_callback,
            hold_time=entry.get('hold_time', 1)
        ))
    return handlers
