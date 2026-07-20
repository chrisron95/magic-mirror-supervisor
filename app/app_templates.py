"""Built-in app templates. An apps.yaml entry can reference one of these via
`app: "<name>"` instead of repeating all of its setup/command/environment boilerplate —
see the "kiosk" entry in config/apps.yaml for the common case (just `url` + `name`).

Adding a new *type* of app (e.g. a slideshow) means adding a template here. Adding
another *instance* of an existing type (a second kiosk pointed at a different URL) is
just a new apps.yaml entry referencing the same template.
"""

def KIOSK(overrides):
    """`overrides` is the apps.yaml entry (minus `app:`). `show_navigation` (default
    False) is consumed here rather than passed through, since it only exists to pick
    which Chromium flag to use."""
    overrides = dict(overrides)
    show_navigation = overrides.pop("show_navigation", False)
    # --kiosk is what hides the omnibox/back/forward/reload UI; everything else about
    # kiosk mode (fullscreen, no infobars, etc.) comes from the other flags below.
    kiosk_flag = "" if show_navigation else "--kiosk "

    base = {
        "working_directory": "{{user_home}}",
        "environment": {
            "DISPLAY": ":0.0",
            "XAUTHORITY": "{{user_home}}/.Xauthority",
            # Share the real desktop session's D-Bus bus (rather than each process guessing/
            # creating its own) so Chromium's own AT-SPI tree is visible to the
            # onscreen_keyboard service's focus listener (see services.yaml).
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/{{uid}}/bus",
        },
        "setup": [
            "xset s noblank",
            "xset s off",
            "xset -dpms",
            "sed -i 's/\"exited_cleanly\":false/\"exited_cleanly\":true/' {{user_home}}/.config/chromium/Default/Preferences",
            "sed -i 's/\"exit_type\":\"Crashed\"/\"exit_type\":\"Normal\"/' {{user_home}}/.config/chromium/Default/Preferences",
            # Chromium doesn't get to clean these up when we have to SIGKILL a frozen instance;
            # a stale lock makes the next launch think another instance is already running and
            # hang trying to hand off to it instead of opening a window.
            "rm -f {{user_home}}/.config/chromium/SingletonLock {{user_home}}/.config/chromium/SingletonSocket {{user_home}}/.config/chromium/SingletonCookie",
        ],
        "background": [
            "unclutter -idle 0.5 -root",
        ],
        "command": (
            "/usr/bin/chromium-browser --noerrdialogs --disable-infobars "
            "--enable-features=OverlayScrollbar,OverlayScrollbarFlashAfterAnyScrollUpdate,OverlayScrollbarFlashWhenMouseEnter "
            "--disable-restore-session-state " + kiosk_flag + "--force-device-scale-factor=0.9 "
            "--pull-to-refresh=1 --enable-virtual-keyboard --password-store=basic "
            "--force-renderer-accessibility {{url}}"
        ),
        "restart": True,
        # If the screen hasn't visibly changed in 3 minutes, treat it as a frozen renderer
        # (still running, just unresponsive) and force a restart. Requires `grim`
        # (sudo apt install grim) to be installed on the Pi.
        "liveness_check": {
            "interval": 30,
            "stale_after": 180,
        },
    }
    return {**base, **overrides}


TEMPLATES = {
    "kiosk": KIOSK,
}
