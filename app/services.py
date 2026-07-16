import logging
import os
import threading
import time

from .process_utils import spawn_logged, terminate_process_group

logger = logging.getLogger(__name__)


class ServiceManager:
    """Starts/stops independent background services (e.g. UxPlay) defined in
    config/services.yaml. Unlike AppManager's apps — where only one runs at a time —
    any number of services can run concurrently with each other and with whatever app
    is currently showing, matching how they ran as separate systemd units before."""

    RESTART_DELAY = 2  # seconds to wait before relaunching a service that exited unexpectedly
    MAX_LOG_BYTES = 5 * 1024 * 1024  # rotate a log past this size, keeping one backup

    def __init__(self, services, user_home=None, secrets=None, log_dir="logs", on_state_change=None):
        self.services = self._resolve_services(services or {}, user_home or os.path.expanduser('~'), secrets or {})
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self._on_state_change = on_state_change  # optional callback(name, running: bool)

        self._lock = threading.RLock()
        self._running = {}     # name -> subprocess.Popen, present only while actually running
        self._generation = {}  # name -> int; bumped by stop() to stand down any in-flight monitor
        self._extra_args = {}  # name -> extra CLI args appended to the base command, set by start()
                                # and preserved across auto-restarts (e.g. UxPlay's rotation flag)

    def _resolve_services(self, raw_services, user_home, secrets):
        """Substitute {{user_home}}, {{uid}}, {{secrets.<key>}} — same placeholders
        apps.yaml supports, minus the {{url}} kiosk-template-only one."""
        replacements = {'{{user_home}}': user_home, '{{uid}}': str(os.getuid())}
        for key, value in secrets.items():
            replacements[f'{{{{secrets.{key}}}}}'] = str(value)
        return {name: self._substitute(dict(entry), replacements) for name, entry in raw_services.items()}

    @staticmethod
    def _substitute(value, replacements):
        if isinstance(value, str):
            for placeholder, replacement in replacements.items():
                value = value.replace(placeholder, replacement)
            return value
        if isinstance(value, dict):
            return {k: ServiceManager._substitute(v, replacements) for k, v in value.items()}
        if isinstance(value, list):
            return [ServiceManager._substitute(v, replacements) for v in value]
        return value

    def list_services(self):
        return list(self.services.keys())

    def is_running(self, name):
        with self._lock:
            process = self._running.get(name)
            return process is not None and process.poll() is None

    def start(self, name, extra_args=""):
        """`extra_args`, if given, is appended to the service's base command for this run
        (and any auto-restarts of it) — e.g. UxPlay's `-r R` rotation flag."""
        if name not in self.services:
            logger.warning(f"Unknown service '{name}'; not starting")
            return
        with self._lock:
            if self.is_running(name):
                logger.info(f"Service '{name}' already running")
                return
            self._extra_args[name] = extra_args
            self._generation[name] = self._generation.get(name, 0) + 1  # stand down any stale monitor
            self._launch(name)

    def stop(self, name):
        """Stop the named service, if it's running."""
        with self._lock:
            self._generation[name] = self._generation.get(name, 0) + 1  # stand down any in-flight monitor
            process = self._running.pop(name, None)
            if not process:
                return
            logger.info(f"Stopping service '{name}'")
            terminate_process_group(process)
        self._notify(name, False)

    def stop_all(self):
        """Stop every currently-running service — used on supervisor shutdown."""
        for name in list(self._running.keys()):
            self.stop(name)

    def start_autostart(self):
        """Start every service declared with `autostart: true` — mirrors what
        `WantedBy=default.target` gave these as standalone systemd units."""
        for name, service in self.services.items():
            if service.get('autostart'):
                self.start(name)

    def _launch(self, name):
        service = self.services[name]
        working_directory = service.get('working_directory')
        env = {**os.environ, **service.get('environment', {})}
        command = service.get('command')
        if not command:
            logger.warning(f"Service '{name}' has no command defined")
            return
        extra_args = self._extra_args.get(name)
        if extra_args:
            command = f"{command} {extra_args}"

        logger.info(f"[{name}] command: {command}")
        log_path = os.path.join(self.log_dir, f"{name}.log")

        generation = self._generation[name]
        restart_trigger = service.get('restart_on_output')
        line_callback = None
        if restart_trigger:
            def line_callback(line, generation=generation):
                if restart_trigger in line:
                    threading.Thread(target=self._restart_on_trigger, args=(name, generation), daemon=True).start()

        process = spawn_logged(command, working_directory, env, log_path, self.MAX_LOG_BYTES,
                                stream_logger=logger, stream_prefix=name, line_callback=line_callback)
        self._running[name] = process

        if service.get('restart', True):
            threading.Thread(target=self._monitor, args=(name, generation), daemon=True).start()

        self._notify(name, True)

    def _restart_on_trigger(self, name, generation):
        """Restart a still-running service because its own output matched
        `restart_on_output` (e.g. UxPlay never clears its window on client disconnect,
        so we force a fresh process/window instead). Runs on its own thread since it's
        invoked from the pump thread reading the process's stdout — stop()'s
        process.wait() would otherwise block against the very pipe it's draining."""
        with self._lock:
            if self._generation.get(name) != generation:
                return
            extra_args = self._extra_args.get(name, "")
            logger.info(f"Service '{name}' output matched restart trigger; restarting")
        self.stop(name)
        self.start(name, extra_args=extra_args)

    def _monitor(self, name, generation):
        """Wait for the service's process to exit, and relaunch it if nothing else has
        stopped/restarted it in the meantime (a stale `generation` means one has)."""
        with self._lock:
            process = self._running.get(name)
        if process is None:
            return
        process.wait()

        with self._lock:
            if self._generation.get(name) != generation:
                return
            logger.warning(f"Service '{name}' exited unexpectedly (code {process.returncode}); restarting in {self.RESTART_DELAY}s")

        time.sleep(self.RESTART_DELAY)

        with self._lock:
            if self._generation.get(name) != generation:
                return
            self._launch(name)

    def _notify(self, name, running):
        if not self._on_state_change:
            return
        try:
            self._on_state_change(name, running)
        except Exception:
            logger.exception(f"on_state_change callback failed for service '{name}'")
