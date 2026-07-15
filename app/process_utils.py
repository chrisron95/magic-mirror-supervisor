import logging
import os
import signal
import subprocess

logger = logging.getLogger(__name__)


def rotate_log_if_large(log_path, max_bytes):
    """Keep a log file bounded: once it exceeds max_bytes, move it to a single ".1"
    backup (overwriting any previous one) and start fresh. Only safe to call before the
    file is (re)opened for the next process — a still-running writer could otherwise get
    orphaned onto the renamed-out file."""
    try:
        if os.path.getsize(log_path) <= max_bytes:
            return
    except OSError:
        return  # doesn't exist yet; nothing to rotate

    try:
        os.replace(log_path, log_path + ".1")
    except OSError as e:
        logger.warning(f"Failed to rotate log {log_path}: {e}")


def spawn_logged(command, cwd, env, log_path, max_log_bytes):
    """Launch `command` in its own process group (so the whole subtree can be killed
    together later), with stdout/stderr appended to a size-capped, rotated log file."""
    rotate_log_if_large(log_path, max_log_bytes)
    log_file = open(log_path, "a")
    return subprocess.Popen(
        command, shell=True, cwd=cwd, env=env,
        stdout=log_file, stderr=subprocess.STDOUT,
        preexec_fn=os.setsid
    )


def terminate_process_group(process, timeout=5):
    """Stop a process (and its whole process group) gracefully, escalating to SIGKILL
    if it doesn't exit within `timeout` seconds."""
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
