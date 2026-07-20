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

import pyatspi

logger = logging.getLogger(__name__)

KEYBOARD_HEIGHT = 200

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


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    wvkbd = subprocess.Popen(["wvkbd-mobintl", "--hidden", "-L", str(KEYBOARD_HEIGHT)])

    def on_focus(event):
        if event.detail1 != 1:
            return  # only act on focus gained; a widget losing focus is always followed
                    # by a gained event elsewhere, which decides show vs hide on its own
        wvkbd.send_signal(signal.SIGUSR2 if is_editable(event.source) else signal.SIGUSR1)

    pyatspi.Registry.registerEventListener(on_focus, "object:state-changed:focused")
    try:
        pyatspi.Registry.start()
    finally:
        wvkbd.terminate()


if __name__ == "__main__":
    main()
