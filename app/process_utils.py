import logging
import os
import signal
import subprocess
import threading

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


def spawn_logged(command, cwd, env, log_path, max_log_bytes, stream_logger=None, stream_prefix="", line_callback=None):
    """Launch `command` in its own process group (so the whole subtree can be killed
    together later), with stdout/stderr appended to a size-capped, rotated log file.

    If `stream_logger` is given, output is also re-emitted live via that logger (prefixed
    with `stream_prefix`), so it lands in journalctl too, not just the log file.
    If `line_callback` is given, it's called with each raw output line as it arrives."""
    rotate_log_if_large(log_path, max_log_bytes)
    log_file = open(log_path, "a")

    if stream_logger is None and line_callback is None:
        return subprocess.Popen(
            command, shell=True, cwd=cwd, env=env,
            stdout=log_file, stderr=subprocess.STDOUT,
            preexec_fn=os.setsid
        )

    process = subprocess.Popen(
        command, shell=True, cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        preexec_fn=os.setsid
    )

    def _pump():
        try:
            for line in process.stdout:
                log_file.write(line)
                log_file.flush()
                if stream_logger:
                    stream_logger.info(f"[{stream_prefix}] {line.rstrip()}")
                if line_callback:
                    line_callback(line)
        finally:
            log_file.close()

    threading.Thread(target=_pump, daemon=True).start()
    return process


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
