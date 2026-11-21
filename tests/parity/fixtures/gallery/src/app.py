import time

RETRY_S = 0.5


def upload(path):
    for attempt in range(5):
        if send(path):
            return True
        time.sleep(RETRY_S)
    return False
