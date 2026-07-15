# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python supervisor daemon that runs on a Raspberry Pi 4 driving a "Magic Mirror" (a 42" TV behind
mirrored acrylic with an IR touchscreen overlay). It manages TV power/input via HDMI-CEC, launches and
supervises kiosk/app processes (Chromium dashboard, MagicMirror2, etc.), reads GPIO buttons, and
exposes everything to Home Assistant over MQTT (auto-discovery). It replaces what used to be several
separate systemd units (one per app) with a single always-running process plus a data-driven app config.

There is exactly one instance of this process running at a time — GPIO pins can't be shared across
processes — so it's deployed as a single systemd service, not scaled or load-balanced.

## Running it

```bash
source .venv/bin/activate
python main.py
```

- No test suite, linter, or CI config exists in this repo — there's nothing to run beyond starting
  the process itself. Sanity-check changes by running `main.py` (GPIO/CEC/hardware-dependent bits will
  fail gracefully off-Pi, but MQTT discovery, app-launch logic, and config parsing can still be exercised).
- On the Pi it runs as `magic-mirror-supervisor.service` (systemd); see `readme.md` for the unit file.
  After deploying a change on the Pi: `sudo systemctl restart magic-mirror-supervisor.service`, then
  `journalctl -f -u magic-mirror-supervisor.service` to watch it come up.
- Requires `include-system-site-packages = True` in `.venv/pyvenv.cfg` (GPIO/CEC libraries are
  system packages, not pip-installable in a normal venv).

## Configuration (config/, gitignored where noted)

Runtime behavior is data-driven from four YAML files, loaded once at startup in `main.py`:

- **`config.yaml`** — device name/model, log level, `default_app` fallback.
- **`secrets.yaml`** (gitignored) — MQTT credentials, `ha_url`. Referenced elsewhere as `{{secrets.<key>}}`.
- **`entities.yaml`** — declares every Home Assistant entity (binary_sensors, sensors, buttons,
  switches, selects) and, for each, which Python method backs its state/callback — see "dotted-path
  dispatch" below.
- **`apps.yaml`** — declares every launchable app (Chromium kiosk instances, MagicMirror2, or anything
  else). An entry either references a built-in template via `app: "<type>"` (currently only `"kiosk"`,
  defined in `app/app_templates.py`) plus instance overrides (usually just `url`/`name`), or defines
  `working_directory`/`environment`/`setup`/`background`/`command`/`restart`/`liveness_check` directly,
  the way `magicmirror2` does. `{{user_home}}`, `{{uid}}`, `{{secrets.<key>}}`, and (for templated
  entries) `{{url}}` are substituted by `AppManager._resolve_apps`.

`data/settings.yaml` (gitignored, written at runtime) holds the small set of values that can change
live from Home Assistant (e.g. the HA-selected default startup app) and must survive a restart,
independent of the static `config/` files — see `app/settings_store.py`.

Adding a new HA-controllable action or sensor is almost always a config-only change (edit
`entities.yaml` to point at an existing or new method), not a code change. Adding a new *kind* of
launchable app (not just another kiosk URL) means adding a template function to `app/app_templates.py`.

## Architecture

`main.py` is the composition root: it loads the four config files, then constructs (in order) `TV` →
`Supervisor` (which owns an `AppManager`) → `Utils` → three `ButtonHandler`s → `HomeAssistantClient`,
wiring circular references (`supervisor.ha_client`, `tv.ha_client`, etc.) after construction since HA
setup needs a live `supervisor`/`tv`/`utils` and vice versa. It then waits for network connectivity
before auto-starting the configured default app, and blocks in `signal.pause()` — all real work happens
on background threads (button GPIO callbacks, MQTT client loop, app-monitor threads) or MQTT-triggered
callbacks, not driven from the main thread.

- **`app/tv.py`** (`TV`) — HDMI-CEC control via shelling out to `cec-client`. All CEC access is
  serialized through a single `RLock` (concurrent `cec-client` invocations stomp on each other); power-on
  and standby acquire it non-blocking and bail out with a log if a command is already in flight, rather
  than queuing. Power/input state is tracked in-memory (`is_on`, `internal_input`) and pushed to HA
  proactively on change rather than HA polling for it.

- **`app/apps.py`** (`AppManager`) — owns the currently-running app's process group. `start()` always
  stops whatever's running first (only one app runs at a time). Each launched app gets a monotonically
  increasing `_generation`; `stop()` bumps it, and background restart/liveness-monitor threads check
  their captured generation before acting, so a stale monitor from a since-stopped app never
  resurrects it or clobbers a newer one. Processes are launched with `preexec_fn=os.setsid` and killed
  via `os.killpg` (SIGTERM then SIGKILL) so a whole subtree (e.g. Chromium's child processes) dies
  together. Two independent failure-detection paths: `_monitor` (process exited) triggers `restart:
  true`; `_monitor_liveness` (process alive but screen hasn't changed, via periodic `scrot` screenshot
  hashing) triggers `liveness_check`. Per-app stdout/stderr logs under `logs/` are size-capped and
  rotated to a single `.1` backup at spawn time.

- **`app/app_templates.py`** — built-in reusable app definitions (`TEMPLATES` dict). An `apps.yaml`
  entry with `app: "kiosk"` gets merged with `KIOSK(overrides)`'s base dict (Chromium flags, X11/DBus
  env, singleton-lock cleanup, unclutter/onboard background processes, liveness check) before
  `AppManager` resolves placeholders.

- **`app/supervisor.py`** (`Supervisor`) — the app-switching/notification layer above `AppManager`:
  cycling apps, the desktop notify-send app picker, resolving/persisting the default startup app, and
  keeping the "Current App" sensor / "App Switcher" select in HA in sync via `_notify_current_app`.
  `NONE_APP_OPTION` ("No Startup App") and `NO_APP_RUNNING` ("Nothing Running") are deliberately not the
  literal string `"None"` — HA's MQTT integration treats that as a reserved "reset to unknown" sentinel,
  not a selectable value.

- **`app/home_assistant_client.py`** (`HomeAssistantClient`) — MQTT discovery/sync via
  `ha-mqtt-discoverable`. Two connection strategies coexist: `BinarySensor`/`Sensor` reuse one shared
  `mqtt.Client` (cheap, no command channel needed), while `Button`/`Switch`/`Select` each open their own
  client internally (required by the library's `Subscriber` base) — `_rebroadcast_availability_on_reconnect`
  patches each one's `on_connect` so a reconnect re-announces "online" the same way the shared client's
  handler does. Entity callbacks in `entities.yaml` are **dotted method paths resolved at runtime**
  (e.g. `"tv.standby"`, `"utils.get_ip_address"` → `getattr` chase from `self`) — this is how config
  wires HA entities to Python code without a code change; when adding a new callable target, make sure
  it's reachable by attribute lookup from `HomeAssistantClient` (it holds `supervisor`, `tv`, `utils`).
  Selects using the `"{{apps_all}}"`/`"{{apps}}"` options shorthand maintain a canonical-value
  (apps.yaml key) ↔ display-value (app's HA-visible name) map per entity, since HA shows/sends the
  display string but callbacks and persisted state use the canonical key.

- **`app/buttons.py`** (`ButtonHandler`) — wraps `gpiozero.Button` with press-vs-hold disambiguation
  (`when_held` sets a flag that `when_released` checks, so a hold doesn't also fire a press).

- **`app/utils.py`** (`Utils`) — system stats (CPU temp, memory, disk, IP) for HA sensors, plus
  system-level actions (reboot, shutdown, update, restart-service) invoked via `os.system`.

- **`app/settings_store.py`** (`SettingsStore`) — trivial YAML-backed key/value persistence for
  `data/settings.yaml`, separate from static `config/` files.

## Working in this repo

- This runs unattended on a headless Pi as a systemd service — prefer failing soft (log a warning,
  fall back to a sane default) over raising, especially in HA/MQTT setup and app-launch paths, since an
  uncaught exception there can take down the whole supervisor rather than just one feature. The existing
  code's broad `try/except Exception: logger.warning(...)` blocks around per-entity HA setup follow this
  pattern deliberately — don't tighten them to bare `except` blocks without reason.
  Startup itself is the exception: `main.py` requires the four config YAML files to be present.
- The `{{...}}` placeholders in `apps.yaml`/`app_templates.py` and the `dotted.path` method references
  in `entities.yaml` are both resolved by plain string substitution / `getattr` chains, not a templating
  engine — search for `_substitute` (in `apps.py`) and `parts[-1]` / `getattr` (in `home_assistant_client.py`)
  before changing how either config file is interpreted.
- Off-Pi (e.g. developing on a laptop), `gpiozero`, `RPi.GPIO`, `cec-client`, and `/proc/cpuinfo` /
  `/etc/os-release` reads will not behave like they do on the Pi — expect `ButtonHandler`, `TV`, and
  `Utils.get_hw_info`/`get_sw_info` to need a real Pi (or mocking) to exercise fully.
