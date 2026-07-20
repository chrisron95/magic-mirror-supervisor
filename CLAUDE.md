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

- **`config.yaml`** — device name/model, log level, `default_app` fallback, `tv_inputs` (CEC physical
  addresses for the switchable TV inputs — see `TV` below).
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
  serialized through a single `RLock`. Three user-initiated entry points (`power_on`/`standby`/
  `set_input`) go through `_acquire_for_command` instead of acquiring `self.lock` directly: it tries a
  non-blocking acquire first, and if that's held by the periodic poll's routine `"scan"` (marked
  `background=True` in the one `_run_cec_command` call inside `_poll_loop` — see `_current_process`/
  `_current_is_background`, tracked under their own small `_current_op_lock` since they need to be
  read from a thread that by definition doesn't hold `self.lock`), cancels it via
  `terminate_process_group` so the user's command doesn't wait behind routine housekeeping. It
  deliberately never cancels another *user-initiated* command still in flight — that still just logs
  and drops, same as before, so two deliberate actions can't step on each other unpredictably.
  `_run_cec_command` spawns `cec-client` in its own process group (`preexec_fn=os.setsid`)
  and kills the whole group (via `process_utils.terminate_process_group`) on timeout — plain
  `subprocess.run(..., timeout=)` under `shell=True` only kills the shell, not `cec-client` itself,
  which can then linger holding the adapter open and make every subsequent command fail with
  `ioctl cec_s_mode failed - errno=16` (EBUSY) until something finally kills it manually — note this
  only prevents *future* orphans; an already-orphaned process from before this fix needs a manual
  `pkill`/reboot, it won't clean itself up. The kill grace period on timeout is intentionally short
  (`terminate_process_group(process, timeout=2)`, not the default 5s) since a timed-out command has
  already blown its budget and every extra second here is `self.lock` staying held, blocking any other
  TV command. `wait_for_input_switch`'s retry loop tracks real wall-clock time (`time.monotonic()`), not
  a fixed per-iteration increment — the latter let one slow/timed-out `get_active_source()` call (up to
  `CEC_TIMEOUT` + kill grace) silently blow the loop's nominal budget several times over, since
  `elapsed += interval` didn't account for how long that call actually took. `"scan"` (walks the whole
  CEC bus — every device, not just one) gets its own longer `SCAN_TIMEOUT` (20s vs. `CEC_TIMEOUT`'s
  10s) — confirmed via real logs that `"pow"` (single-device) never times out while `"scan"` regularly
  does, so this is a genuine cost-of-the-operation difference, not adapter contention; a full Pi reboot
  didn't change the pattern either. `wait_for_input_switch`'s own default `timeout` (25s) is set above
  `SCAN_TIMEOUT` so its retry loop can actually fit at least one full scan attempt. `POLL_INTERVAL`
  (60s, was 30s) was also widened for the same reason — less frequent bus-wide scanning, since it's
  the heavier operation. Power/input
  state is tracked in-memory (`is_on`, `internal_input`) and pushed to HA
  proactively on change rather than HA polling for it. `get_current_input()` reports "Off" whenever
  `is_on` is false instead of a stale HDMI reading — `internal_input` itself is left untouched so the
  real last input is still there once the TV powers back on. A `_poll_loop` background thread (started
  in `__init__`, `POLL_INTERVAL` seconds) re-checks power/input periodically — the only way to notice a
  change made via the TV's own remote, since every other code path here only queries the TV in response
  to our own commands. Switchable inputs (`self.inputs`, from `config.yaml`'s `tv_inputs`, falling back
  to `DEFAULT_INPUTS`) are keyed `'rPi'`/`'hdmi'` with a `name` and CEC physical `address` each (e.g.
  `"2.0.0.0"`) — `_active_source_command` derives the actual `tx 1F:82:XX:YY` CEC frame from that
  address by packing its four nibbles into two bytes, rather than hardcoding the frame itself, so
  re-wiring for a different TV is a `config.yaml` edit, not a code change. `set_input()` only ever
  targets these two physical addresses; a CEC-aware device plugged into "hdmi" (e.g. an Apple TV)
  doesn't get its own dedicated command — it's just whatever the TV routes to that address.
  `_parse_scan_devices` parses a whole `cec-client scan` into a list of per-device dicts
  (`number`/`address`/`osd_string`), used both by `_parse_active_source` (correlates
  "currently active source: ... (N)" against `device #N`'s own `osd_string` — a plain
  first-match regex would always find `device #0: TV` instead, since that's always listed
  first regardless of what's actually active) and by `_parse_hdmi_device_name` (looks up
  "hdmi"'s configured physical `address` instead). `get_active_source`/`get_hdmi_device_name`
  are thin wrappers that fetch a fresh scan and hand it to those; `_poll_loop` and
  `initialize_input` instead fetch *one* scan and feed it to both, since each cec-client
  invocation is a full adapter connect/disconnect and doubling that up every poll cycle
  meaningfully adds to CEC bus load. `_apply_hdmi_label` (called with that scan's result,
  *before* anything that calls `update_input()` in that same pass — see the ordering
  comment in `_poll_loop`) pushes new options for the "TV Input" select via
  `HomeAssistantClient.update_select_options`, only if the detected name actually changed.
  `get_tv_input_selection()` (called from
  `update_input()`, alongside the "TV Current Input" sensor push) reports the select's
  *current* value in that same two-option scheme — deliberately not power-aware like
  `get_current_input()`, since "Off" isn't a valid option for it. It returns `None` (and
  `update_input()` then skips pushing) while `internal_input` is still `"Unknown"`, i.e.
  before either background init thread has settled — without this, the select would
  briefly assert a default/fallback value as if it were real, since `power_thread` and
  `input_thread` race independently and `power_thread`'s own `update_input()` call
  (inside `check_power_status()`) can easily win. A non-CEC device (most laptops) is
  invisible to a CEC scan entirely — there's no way to detect or name it, so the
  fallback label is the best available for that case.

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
  since Chromium/MagicMirror console output would be far too chatty for that. "Live" depends on the
  service's own command being at least line-buffered on its piped stdout — most C programs (UxPlay
  included) fully block-buffer instead once stdout isn't a terminal, so `services.yaml` commands that
  want to actually show up in real time may need a `stdbuf -oL -eL` prefix, as UxPlay's does. A
  service can also declare `restart_on_output: "<substring>"`; `_launch` passes a `line_callback` (also
  threaded through `spawn_logged`, reusing the same pump thread) that restarts the service — on a
  fresh thread, not inline, since that callback runs on the very pump thread reading the dying
  process's stdout — the moment its output contains that text. This is how UxPlay gets a fresh
  window on client disconnect: it never clears its own window on "Stop Mirroring" (confirmed via live
  logs), so the supervisor forces a restart itself instead of trusting UxPlay to. The generation check
  inside that restart path is what stops it from firing spuriously if the matched text happens to
  appear in the process's own shutdown output during an unrelated explicit `stop()`.

- **`app/app_templates.py`** — built-in reusable app definitions (`TEMPLATES` dict). An `apps.yaml`
  entry with `app: "kiosk"` gets merged with `KIOSK(overrides)`'s base dict (Chromium flags, X11/DBus
  env, singleton-lock cleanup, unclutter background process, liveness check) before `AppManager`
  resolves placeholders. Its `DBUS_SESSION_BUS_ADDRESS` override exposes Chromium's own AT-SPI
  accessibility tree on the shared session bus — needed by the `onscreen_keyboard` service (below),
  not by anything in this file itself.

- **`app/keyboard_watcher.py`** — on-screen keyboard, run as the `onscreen_keyboard` service
  (`services.yaml`). Owns a `wvkbd-mobintl --hidden` process and drives its show/hide via
  `SIGUSR2`/`SIGUSR1` in response to AT-SPI `object:state-changed:focused` events read off the
  session D-Bus bus — the same mechanism the old `onboard` used, which this replaces since onboard's
  X11 toplevel windows fought for focus with windowed apps under labwc. Neither wvkbd's nor
  squeekboard's own `--auto` flag works here: both need `zwp_input_method_v2` fed by the focused
  client's `text-input-v3` implementation, which XWayland-hosted Chromium doesn't speak — confirmed
  via wvkbd's own man page and a live test of squeekboard (enabled via `raspi-config`) on this Pi,
  which didn't render above the kiosk at all. `EDITABLE_ROLES` mirrors the role set onboard's
  `AtspiAutoShow` checked (native widgets like `ENTRY`/`SPIN_BUTTON`/`COMBO_BOX` plus the web-content
  roles Chromium exposes for `<input>`/`<textarea>`/contenteditable). First real-hardware test
  surfaced two more issues, both since addressed but not yet re-verified: wvkbd itself also didn't
  render above the fullscreen `--kiosk` Chromium (only over windowed apps) even though it's confirmed
  to default to the `overlay` layer — passing `--non-exclusive` (wvkbd requests an exclusive zone
  reservation by default; `button_popup.py`'s fullscreen Mirror Mode overlay, which does reliably
  render above this exact kiosk, explicitly sets `exclusive_zone = -1` instead) is the current fix
  attempt. Typing also intermittently hid the keyboard mid-field — `KeyboardController` now debounces
  hide by `HIDE_DELAY` so a real editable regaining focus (e.g. after a framework re-render on
  keystroke) cancels a pending hide instead of flickering shut; show always wins immediately.

- **`app/supervisor.py`** (`Supervisor`) — the app-switching/notification layer above `AppManager`:
  cycling apps, the desktop notify-send app picker, resolving/persisting the default startup app, and
  keeping the "Current App" sensor / "App Switcher" select in HA in sync via `_notify_current_app`.
  `NONE_APP_OPTION` ("No Startup App") and `NO_APP_RUNNING` ("Nothing Running") are deliberately not the
  literal string `"None"` — HA's MQTT integration treats that as a reserved "reset to unknown" sentinel,
  not a selectable value. A single long-lived `_uptime_loop` thread (started in `__init__`, not per
  app launch) refreshes all three uptime-flavored values every `UPTIME_REFRESH_INTERVAL` seconds via
  `_push_uptimes`: the "Current App" sensor's `uptime` attribute (from `AppManager.get_uptime_seconds()`,
  read fresh each tick so it self-heals after a crash/liveness restart without AppManager needing to call
  back into it), plus the "Pi Uptime" and "Supervisor Uptime" sensors (from `Utils`). `TV._poll_loop` is
  the only other polling loop in the codebase; everything else pushes on change. Also owns thin per-service wrapper
  methods (e.g. `start_uxplay`/`stop_uxplay`/`is_uxplay_running`, delegating to `ServiceManager`) since
  HA switch callbacks in `entities.yaml` are zero-argument dotted paths — adding another independent
  service means adding both a `services.yaml` entry and one more such wrapper trio here.
  `start_autostart_services()` (called once from `main.py`, not gated on network) is the general path
  for `autostart: true` services, but special-cases `uxplay` to go through `start_uxplay()` — otherwise
  the persisted rotation/audio-mode settings (`UXPLAY_ROTATION_OPTIONS` +
  `UXPLAY_AUDIO_MODE_OPTIONS`, set via `set_uxplay_rotation`/`set_uxplay_audio_mode` — the "AirPlay
  Orientation"/"AirPlay Audio Mode" selects — persisted in `data/settings.yaml`) would be silently
  dropped on every boot. `start_uxplay()` joins both option dicts' flags into one `extra_args` string
  (e.g. `"-r R -vs 0"`) passed to `ServiceManager.start`. `set_tv_input` (the "TV Input" select's
  callback) maps its selected value back to one of only two `TV` methods (`set_input_rpi`/
  `set_input_hdmi`) by comparing against `tv.inputs['rPi']['name']` — anything else selected means
  "hdmi", however that option is currently labeled.

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
  Note select callbacks are the one exception to "dotted path resolved from `self`" — `create_select_callback`
  only ever calls `getattr(self.supervisor, method_name)`, so a select's `callback:` must be a plain
  `Supervisor` method name (e.g. `"set_tv_input"`), not a dotted path like buttons/switches use.
  `update_select_options(unique_id, options)` changes a select's *available* options (not just its
  current value, which `update_select` already handles) by mutating `select_entity._entity.options`
  directly and re-calling `write_config()` — there's no dedicated library method for this, and the
  exact `Select` API differs enough across `ha-mqtt-discoverable` versions (confirmed while building
  this: 0.25.2 has `select_option()`, but this project's deployed version logs "Publishing options..."
  from a `set_options()` method instead) that mutating the entity model directly is the more
  version-stable bet. TV Input's select uses this to swap in a detected CEC device's real name.

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
