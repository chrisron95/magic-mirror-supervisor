#!/usr/bin/env python3
"""Generic GTK touch-button popup. Reads JSON on stdin:
{"title": "optional heading", "options": {key: label, ...}, "cancel_label": "optional,
default Cancel", "fullscreen": false}
Shows one button per option plus a cancel button, prints the chosen key to stdout
(nothing if cancelled/exited). Runs as its own process/GTK main loop, invoked via
subprocess by callers like Supervisor.app_selector rather than imported directly.

`fullscreen: true` anchors the window to all four screen edges with a solid black
background instead of floating centered, and puts the buttons at the bottom instead of
center — used by the "Mirror Mode" service (config/services.yaml) to blank the screen,
with just the cancel button (relabelled e.g. "Exit Mirror Mode") to return to normal.
Exiting the process is itself the signal ServiceManager cares about, not just stdout.

Uses gtk-layer-shell (not a plain Gtk.Window) so labwc renders it as an overlay-layer
surface above a fullscreen kiosk window, instead of a regular decorated toplevel.
"""
import json
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, GtkLayerShell

CSS = b"""
window { background-color: #1a1a1a; }
window.fullscreen { background-color: #000000; }
label { color: #f5f5f5; font-size: 22px; margin: 12px; }
button {
    background-color: #1a1a1a;
    color: #f5f5f5;
    font-size: 22px;
    border: 2px solid #333333;
    border-radius: 12px;
    padding: 20px;
    margin: 8px;
}
button:active { background-color: #3b82f6; color: #ffffff; }
"""


def main():
    request = json.load(sys.stdin)
    options = request.get("options", {})
    title = request.get("title")
    cancel_label = request.get("cancel_label", "Cancel")
    fullscreen = request.get("fullscreen", False)

    win = Gtk.Window()
    GtkLayerShell.init_for_window(win)
    GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)
    win.set_decorated(False)

    if fullscreen:
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(win, edge, True)
        GtkLayerShell.set_exclusive_zone(win, -1)  # cover other surfaces' reserved space too (e.g. a panel)
        win.get_style_context().add_class("fullscreen")

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    if fullscreen:
        box.set_valign(Gtk.Align.END)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_bottom(40)
    win.add(box)

    if title:
        box.add(Gtk.Label(label=title))

    chosen = []

    def select(key):
        chosen.append(key)
        Gtk.main_quit()

    for key, label in options.items():
        button = Gtk.Button(label=label)
        button.connect("clicked", lambda _b, k=key: select(k))
        box.add(button)

    cancel = Gtk.Button(label=cancel_label)
    cancel.connect("clicked", lambda _b: Gtk.main_quit())
    box.add(cancel)

    style_provider = Gtk.CssProvider()
    style_provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        win.get_screen(), style_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()

    if chosen:
        print(chosen[0])


if __name__ == "__main__":
    main()
