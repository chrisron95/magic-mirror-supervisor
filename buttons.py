from gpiozero import Button
import logging

class ButtonHandler:
    def __init__(self, name, pin, press_callback=None, hold_callback=None, hold_time=1):
        self.name = name
        self.button = Button(pin, bounce_time=0.05, hold_time=hold_time)
        self.was_held = False  # Prevent press event after hold

        if press_callback:
            self.button.when_released = self._wrap_press(press_callback)  # Register single press **only after release**
        if hold_callback:
            self.button.when_held = self._wrap_hold(hold_callback)  # Register hold and suppress single press

    def _wrap_press(self, callback):
        def wrapped():
            if not self.was_held:
                logging.info(f"{self.name} pressed (single)")
                callback()
            self.was_held = False  # Reset flag after press
        return wrapped

    def _wrap_hold(self, callback):
        def wrapped():
            logging.info(f"{self.name} held")
            self.was_held = True  # Mark as held to suppress single press
            callback()
        return wrapped