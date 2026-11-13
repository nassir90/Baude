"""A head's way to a host process (baude_headless.py): this machine's, started if it isn't running, or another
machine's over ssh. For the command-line
heads, baude_oneshot.py and baude_headless.py's own commands; Baude has its own (client.Network)."""
import os
import signal
import subprocess
import sys

from baudelib import wire
from baudelib.server import running_pid, state_home

HEADLESS = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "baude_headless.py")


def start_here(wait=20.0, follow_checkout=True, auto_update=False):
    """This machine's host process, started if it isn't running (in a process of its own, which goes on after this
    one: its output in its state folder's host.log; one started meanwhile by another head is found, as there's one
    each). False if none's there after wait seconds. follow_checkout: its updater restarts it onto what's checked out,
    which this machine's Baude keeps up to date (Baude's way; False: it fetches and fast-forwards the checkout itself,
    for a machine with no Baude on it, connect's); auto_update: its updater's on whatever this machine's Baude
    settings say (--auto-update)."""
    path = wire.socket_path()
    if wire.wait_for_socket(path, 0.2):
        return True
    if os.environ.get("BAUDE_HOST_IN_PROCESS") == "1":  # (the tests': one in this process, gone with it)
        return _in_process(path, wait)
    if (pid := running_pid()) is not None:  # (running, but its socket's gone, its folder taken away: listening again)
        try:
            os.kill(pid, signal.SIGUSR1)
        except OSError:
            pass
        if wire.wait_for_socket(path, 5):
            return True
    os.makedirs(state_home(), exist_ok=True)
    with open(os.path.join(state_home(), "host.log"), "ab") as log:
        subprocess.Popen([sys.executable, HEADLESS, "serve", *(["--follow-checkout"] if follow_checkout else []),
                          *(["--auto-update"] if auto_update else [])],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
    return wire.wait_for_socket(path, wait)


def log_path():
    return os.path.join(state_home(), "host.log")


def reach(dest=None, command=None, member=None):
    """(channel, info) for a host process: another machine's over ssh (dest: an alias, a host, user@host; command:
    its host process's, if baude_headless.py isn't on its PATH), a member of the mesh through this machine's (member:
    its id), else this machine's, started if need be. OSError if it can't be."""
    if dest:
        return wire.connect_ssh(dest, command)
    if not start_here():
        raise OSError(f"no host process here, and none could be started (see {log_path()})")
    return wire.connect_mesh(member) if member else wire.connect_local()


_HERE = []


def _in_process(path, wait):
    """(BAUDE_HOST_IN_PROCESS=1, the tests') This machine's host process in this one, on a thread: as one started
    would be, its claudes this process's children."""
    import threading
    from baudelib.server import AlreadyRunning, Server
    if not _HERE:
        try:
            server = Server(socket_path=path, log=lambda _text: None)
        except AlreadyRunning:
            return wire.wait_for_socket(path, wait)
        _HERE.append(server)
        threading.Thread(target=server.serve_forever, daemon=True, name="host-in-process").start()
    return wire.wait_for_socket(path, wait)
