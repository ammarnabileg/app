import os
import sys
import time
import threading
import subprocess


def _in_docker():
    """Detect if we are running inside a container (Docker/containerd/k8s)."""
    if os.path.exists('/.dockerenv'):
        return True
    try:
        with open('/proc/1/cgroup', 'rt') as f:
            data = f.read()
        return ('docker' in data) or ('containerd' in data) or ('kubepods' in data)
    except Exception:
        return False


def _relaunch_args():
    """Build the command that re-launches this exact application."""
    if getattr(sys, 'frozen', False):
        # PyInstaller bundle: the executable itself
        return [sys.executable] + sys.argv[1:]
    return [sys.executable] + sys.argv


def _do_restart(delay):
    time.sleep(delay)
    if _in_docker():
        # Inside a container the process IS the container: exit cleanly and let
        # the orchestrator's restart policy (restart: unless-stopped) revive it.
        os._exit(0)
    args = _relaunch_args()
    kwargs = {'cwd': os.getcwd(), 'close_fds': True}
    if os.name == 'nt':
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kwargs['creationflags'] = 0x00000008 | 0x00000200
    else:
        kwargs['start_new_session'] = True
    try:
        subprocess.Popen(args, **kwargs)
    finally:
        os._exit(0)


def restart_application(delay=1.2):
    """Spawn a fresh copy of the app after `delay` seconds, then terminate this process."""
    t = threading.Thread(target=_do_restart, args=(delay,), daemon=True)
    t.start()
    return True
