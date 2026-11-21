"""Uploads that pick up where a dropped connection left them."""
import socket

RETRY_WAITS = (0.5, 1, 2, 4)


def upload(sock, parts):
    """Send each part; after a drop, wait for the connection to come back and carry on."""
    for part in parts:
        try:
            sock.send(part)
        except socket.error:
            for wait in RETRY_WAITS:
                if sock.retry(timeout=wait):
                    break
            else:
                raise TimeoutError("no retry after the connection dropped")
            sock.send(part)
