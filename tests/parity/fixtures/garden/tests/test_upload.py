from garden.upload import upload


class Flaky:
    """A socket that drops once, then takes everything."""

    def __init__(self):
        self.sent, self.dropped = [], False

    def send(self, part):
        if not self.dropped:
            self.dropped = True
            raise OSError("connection dropped")
        self.sent.append(part)

    def retry(self, timeout):
        return True


def test_upload_resumes_after_a_drop():
    sock = Flaky()
    upload(sock, [b"a", b"b"])
    assert sock.sent == [b"a", b"b"]
