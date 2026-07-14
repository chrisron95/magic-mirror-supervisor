import logging
import os
import signal
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

class AppManager:
    """Launches and supervises the user-facing apps defined in apps.yaml (kiosk browser,
    MagicMirror, etc.), replacing what used to be separate systemd services for each."""

    RESTART_DELAY = 2  # seconds to wait before relaunching an app that exited unexpectedly

    def __init__(self, apps, log_dir="logs"):
        self.apps = apps  # dict: app name -> config, as parsed from apps.yaml
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

        self._lock = threading.RLock()
        self._current_name = None
        self._processes = []       # every process (background + main) for the current app
        self._main_process = None  # the tracked/monitored process
        self._generation = 0       # bumped by stop(); invalidates any in-flight restart-monitor

    def list_apps(self):
        return list(self.apps.keys())

    @property
    def current_app(self):
        return self._current_name

    def start(self, name):
        """Stop whatever's running, then launch the named app."""
        if name not in self.apps:
            logger.warning(f"Unknown app '{name}'; not starting")
            return

        with self._lock:
            self.stop()
            self._launch(name)

    def stop(self):
        """Stop whatever app is currently running, if any."""
        with self._lock:
            self._generation += 1  # tells any in-flight restart-monitor to stand down
            if not self._processes:
                self._current_name = None
                return

            logger.info(f"Stopping app '{self._current_name}'")
            for process in self._processes:
                self._terminate(process)

            self._processes = []
            self._main_process = None
            self._current_name = None

    def _launch(self, name):
        app = self.apps[name]
        working_directory = app.get('working_directory')
        env = {**os.environ, **app.get('environment', {})}

        for setup_command in app.get('setup', []):
            logger.info(f"[{name}] setup: {setup_command}")
            result = subprocess.run(setup_command, shell=True, cwd=working_directory, env=env)
            if result.returncode != 0:
                logger.warning(f"[{name}] setup command exited {result.returncode}: {setup_command}")

        processes = []
        for background_command in app.get('background', []):
            logger.info(f"[{name}] background: {background_command}")
            processes.append(self._spawn(name, background_command, working_directory, env, "background"))

        command = app.get('command')
        main_process = None
        if command:
            logger.info(f"[{name}] command: {command}")
            main_process = self._spawn(name, command, working_directory, env, "app")
            processes.append(main_process)
        else:
            logger.warning(f"App '{name}' has no command defined")

        self._current_name = name
        self._processes = processes
        self._main_process = main_process

        if main_process and app.get('restart', True):
            generation = self._generation
            threading.Thread(target=self._monitor, args=(name, generation), daemon=True).start()

    def _spawn(self, app_name, command, cwd, env, log_suffix):
        log_path = os.path.join(self.log_dir, f"{app_name}-{log_suffix}.log")
        log_file = open(log_path, "a")
        return subprocess.Popen(
            command, shell=True, cwd=cwd, env=env,
            stdout=log_file, stderr=subprocess.STDOUT,
            preexec_fn=os.setsid  # own process group, so we can cleanly kill the whole subtree later
        )

    def _terminate(self, process, timeout=5):
        if process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=timeout)
            return
        except (subprocess.TimeoutExpired, ProcessLookupError):
            pass
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            process.wait(timeout=2)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            pass

    def _monitor(self, name, generation):
        """Wait for the app's main process to exit, and relaunch it if nothing else has
        stopped/switched apps in the meantime (a stale `generation` means one has)."""
        process = self._main_process
        if process is None:
            return
        process.wait()

        with self._lock:
            if generation != self._generation:
                return
            logger.warning(f"App '{name}' exited unexpectedly (code {process.returncode}); restarting in {self.RESTART_DELAY}s")

        time.sleep(self.RESTART_DELAY)

        with self._lock:
            if generation != self._generation:
                return
            self._launch(name)
