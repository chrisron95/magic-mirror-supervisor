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

Runtime behavior is data-driven from six YAML files, loaded once at startup in `main.py`:

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
- **`buttons.yaml`** — declares each physical GPIO button (name, pin, `hold_time`) and its `triggers`
  map: press count (`1`/`2`/`3`/...) or `"hold"` → a dotted method path or list of them, resolved
  against the running `tv`/`supervisor`/`utils` instances the same way `entities.yaml` callbacks are.
  Loaded via `app/buttons.py`'s `load_buttons()`.
- **`services.yaml`** — declares independent background services (currently just `uxplay`/AirPlay)
  that run via `app/services.py`'s `ServiceManager`, separate from `AppManager`. Unlike apps, any
  number of services can run concurrently with each other and with whatever app is showing — they're
  toggled on/off (e.g. via an HA switch), not switched between. `working_directory`/`environment`/
  `command`/`restart` mean the same as a directly-defined `apps.yaml` entry; `autostart: true` starts
  it at boot instead of waiting for a toggle.

`data/settings.yaml` (gitignored, written at runtime) holds the small set of values that can change
live from Home Assistant (e.g. the HA-selected default startup app) and must survive a restart,
independent of the static `config/` files — see `app/settings_store.py`.

Adding a new HA-controllable action or sensor is almost always a config-only change (edit
`entities.yaml` to point at an existing or new method), not a code change. Adding a new *kind* of
launchable app (not just another kiosk URL) means adding a template function to `app/app_templates.py`.

## Architecture

`main.py` is the composition root: it loads the config files, then constructs (in order) `TV` →
`Supervisor` (which owns an `AppManager` and a `ServiceManager`) → `Utils` → the `ButtonHandler`s (via
`buttons.py`'s `load_buttons()`, given a `SimpleNamespace(tv=, supervisor=, utils=)` to resolve
`buttons.yaml`'s dotted paths against) → `HomeAssistantClient`,
wiring circular references (`supervisor.ha_client`, `tv.ha_client`, etc.) after construction since HA
setup needs a live `supervisor`/`tv`/`utils` and vice versa. Autostart services (`services.yaml`) start
right after, independent of network state; the configured default app only starts once network
connectivity is confirmed. It then blocks in `signal.pause()` — all real work happens on background
threads (button GPIO callbacks, MQTT client loop, app/service-monitor threads) or MQTT-triggered
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
  resurrects it or clobbers a newer one. Two independent failure-detection paths: `_monitor` (process
  exited) triggers `restart: true`; `_monitor_liveness` (process alive but screen hasn't changed, via
  periodic `grim` screenshot hashing) triggers `liveness_check`. Spawning (own process group via
  `preexec_fn=os.setsid`, log rotation) and killing (`os.killpg`, SIGTERM then SIGKILL) are shared with
  `ServiceManager` via `app/process_utils.py`.

- **`app/services.py`** (`ServiceManager`) — the same idea as `AppManager` but for `services.yaml`'s
  independent background services (currently just `uxplay`): no single-slot exclusivity, so any number
  can run concurrently with each other and with whatever app is current. Generation tracking is
  per-service (`_generation[name]`, not one shared counter) for the same stale-monitor-standdown reason
  as `AppManager`. An optional `on_state_change(name, running)` callback — `Supervisor` wires this to
  `HomeAssistantClient.update_switch(name, ...)` — fires on every start/stop, including auto-restarts,
  so a service's HA switch (`unique_id` == the service's `services.yaml` key) stays in sync without
  polling. `start(name, extra_args=...)` appends extra CLI args to the configured `command` for that
  run *and* any auto-restarts of it (stored in `_extra_args[name]`, not passed to `_launch` each time) —
  this is how `Supervisor.start_uxplay` applies the persisted rotation flag (`-r R`/`-r L`/`-f I`) without
  `ServiceManager` needing to know anything UxPlay-specific. Unlike `AppManager`, `_launch` passes
  `spawn_logged(..., stream_logger=logger, stream_prefix=name)`, so a service's output also lands in
  `journalctl -u magic-mirror-supervisor.service` live, not just its own log file — apps stay file-only
  since Chromium/MagicMirror console output would be far too chatty for that.

- **`app/app_templates.py`** — built-in reusable app definitions (`TEMPLATES` dict). An `apps.yaml`
  entry with `app: "kiosk"` gets merged with `KIOSK(overrides)`'s base dict (Chromium flags, X11/DBus
  env, singleton-lock cleanup, unclutter/onboard background processes, liveness check) before
  `AppManager` resolves placeholders.

- **`app/supervisor.py`** (`Supervisor`) — the app-switching/notification layer above `AppManager`:
  cycling apps, the desktop notify-send app picker, resolving/persisting the default startup app, and
  keeping the "Current App" sensor / "App Switcher" select in HA in sync via `_notify_current_app`.
  `NONE_APP_OPTION` ("No Startup App") and `NO_APP_RUNNING` ("Nothing Running") are deliberately not the
  literal string `"None"` — HA's MQTT integration treats that as a reserved "reset to unknown" sentinel,
  not a selectable value. A single long-lived `_uptime_loop` thread (started in `__init__`, not per
  app launch) refreshes all three uptime-flavored values every `UPTIME_REFRESH_INTERVAL` seconds via
  `_push_uptimes`: the "Current App" sensor's `uptime` attribute (from `AppManager.get_uptime_seconds()`,
  read fresh each tick so it self-heals after a crash/liveness restart without AppManager needing to call
  back into it), plus the "Pi Uptime" and "Supervisor Uptime" sensors (from `Utils`). This is the only
  polling loop in the codebase; everything else pushes on change. Also owns thin per-service wrapper
  methods (e.g. `start_uxplay`/`stop_uxplay`/`is_uxplay_running`, delegating to `ServiceManager`) since
  HA switch callbacks in `entities.yaml` are zero-argument dotted paths — adding another independent
  service means adding both a `services.yaml` entry and one more such wrapper trio here.
  `start_autostart_services()` (called once from `main.py`, not gated on network) is the general path
  for `autostart: true` services, but special-cases `uxplay` to go through `start_uxplay()` — otherwise
  the persisted rotation/audio-mode settings (`UXPLAY_ROTATION_OPTIONS` +
  `UXPLAY_AUDIO_MODE_OPTIONS`, set via `set_uxplay_rotation`/`set_uxplay_audio_mode` — the "AirPlay
  Orientation"/"AirPlay Audio Mode" selects — persisted in `data/settings.yaml`) would be silently
  dropped on every boot. `start_uxplay()` joins both option dicts' flags into one `extra_args` string
  (e.g. `"-r R -vs 0"`) passed to `ServiceManager.start`.

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
  display string but callbacks and persisted state use the canonical key. A sensor can also declare
  `attributes: {name: dotted.path}` in `entities.yaml` (e.g. Current App's `uptime`) — resolved the
  same way as `state:` at setup, and re-resolved on demand via `refresh_sensor_attributes()` (kept
  track of in `_sensor_attribute_specs`), which is what `Supervisor`'s periodic uptime loop calls.

- **`app/buttons.py`** (`ButtonHandler`, `load_buttons`) — wraps `gpiozero.Button` with press-count
  (single/double/triple/...) and hold disambiguation: each `when_released` bumps a counter and
  (re)starts a short timer (`MULTI_PRESS_WINDOW`), which fires the matching `press_callbacks[count]`
  once presses stop arriving; `when_held` resets the counter and suppresses the release it interrupts,
  so a hold never also dispatches as a press. `load_buttons()` reads `buttons.yaml` and resolves its
  `triggers` dotted paths into these callbacks — the same dotted-path-dispatch idea as `entities.yaml`,
  just resolved against a `SimpleNamespace` instead of `HomeAssistantClient`.

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
