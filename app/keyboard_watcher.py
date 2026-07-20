#!/usr/bin/env python3
"""Bridges Chromium's AT-SPI accessibility tree to wvkbd-mobintl's show/hide signals.
Neither wvkbd's nor squeekboard's own --auto flag works here: both need
zwp_input_method_v2 fed by the focused client's text-input-v3 implementation, which
XWayland-hosted Chromium doesn't speak. Chromium exposes AT-SPI instead (via
--force-renderer-accessibility), the same mechanism onboard used, so this owns a
wvkbd-mobintl process and drives it from AT-SPI focus events instead.
Run as a services.yaml entry.
"""
import logging
import signal
import subprocess
import sys
import threading

import pyatspi

logger = logging.getLogger(__name__)

KEYBOARD_HEIGHT = 200
HIDE_DELAY = 0.3  # debounce before hiding: a text field losing AT-SPI focus during its
                   # own re-render (e.g. one keystroke in the HA frontend) regains it
                   # almost immediately, without necessarily re-firing a focus-gained
                   # event, so hiding on the raw event flickers the keyboard shut

# Roles onboard's AtspiAutoShow checked: native widgets (ENTRY, SPIN_BUTTON, COMBO_BOX)
# plus web content roles Chromium exposes for <input>/<textarea>/contenteditable
# (TEXT, DOCUMENT_*, PARAGRAPH).
EDITABLE_ROLES = {
    pyatspi.ROLE_TEXT,
    pyatspi.ROLE_DATE_EDITOR,
    pyatspi.ROLE_PASSWORD_TEXT,
    pyatspi.ROLE_ENTRY,
    pyatspi.ROLE_DOCUMENT_TEXT,
    pyatspi.ROLE_DOCUMENT_FRAME,
    pyatspi.ROLE_DOCUMENT_EMAIL,
    pyatspi.ROLE_SPIN_BUTTON,
    pyatspi.ROLE_COMBO_BOX,
    pyatspi.ROLE_PARAGRAPH,
}


def is_editable(source):
    try:
        return source.getRole() in EDITABLE_ROLES and source.getState().contains(pyatspi.STATE_EDITABLE)
    except Exception:
        return False


class KeyboardController:
    """Debounces hide so transient focus churn (e.g. mid-typing re-renders) doesn't
    flicker the keyboard shut; show always wins immediately and cancels any pending hide."""

    def __init__(self, wvkbd):
        self.wvkbd = wvkbd
        self._lock = threading.Lock()
        self._hide_timer = None

    def show(self):
        with self._lock:
            if self._hide_timer:
                self._hide_timer.cancel()
                self._hide_timer = None
            self.wvkbd.send_signal(signal.SIGUSR2)

    def hide(self):
        with self._lock:
            if self._hide_timer:
                self._hide_timer.cancel()
            self._hide_timer = threading.Timer(HIDE_DELAY, self._do_hide)
            self._hide_timer.start()

    def _do_hide(self):
        with self._lock:
            self._hide_timer = None
        self.wvkbd.send_signal(signal.SIGUSR1)


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    # Debian's packaged wvkbd (0.12) doesn't support --non-exclusive; it also hardcodes
    # the "top" layer rather than "overlay" (confirmed in its shipped source), which is
    # why it doesn't render above the fullscreen kiosk yet.
    wvkbd = subprocess.Popen(["wvkbd-mobintl", "--hidden", "-L", str(KEYBOARD_HEIGHT)])
    controller = KeyboardController(wvkbd)

    def on_focus(event):
        if event.detail1 != 1:
            return  # only act on focus gained; loss is handled by whatever gains it next
        if is_editable(event.source):
            controller.show()
        else:
            controller.hide()

    def exit_if_wvkbd_dies():
        # Without this, a dead wvkbd (crash, bad arg) leaves this process running a
        # useless AT-SPI listener that ServiceManager still thinks is a healthy service.
        wvkbd.wait()
        logger.warning(f"wvkbd-mobintl exited (code {wvkbd.returncode}); exiting so the service restarts")
        pyatspi.Registry.stop()

    threading.Thread(target=exit_if_wvkbd_dies, daemon=True).start()

    pyatspi.Registry.registerEventListener(on_focus, "object:state-changed:focused")
    try:
        pyatspi.Registry.start()
    finally:
        wvkbd.terminate()


if __name__ == "__main__":
    main()
