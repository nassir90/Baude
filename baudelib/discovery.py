"""Servers found on the local network: DNS-SD over multicast DNS (RFC 6762, 6763), as Bonjour and Avahi do it, on
nothing but sockets. A server says it's there (Advertiser: _baude._tcp.local., its port and a few words in its TXT
record); Baude asks who is and listens (Browser). `dns-sd -B _baude._tcp` lists them too.

IPv4 only; one packet a question, answers without name compression."""
import socket
import struct
import threading
import time

from baudelib.wire import SERVICE

MDNS_ADDR, MDNS_PORT = "224.0.0.251", 5353
PTR, TXT, SRV, A, ANY = 12, 16, 33, 1, 255
TTL = 120          # how long what's said holds
ANNOUNCE_S = 60    # a server says it again this often
QUERY_S = 30       # a browser asks again this often
CHECK_S = 2        # what's said comes straight back (IP_MULTICAST_LOOP): nothing in this long, and the socket's deaf


def _name(name):
    out = b""
    for part in name.rstrip(".").split("."):
        raw = part.encode("utf-8")[:63]
        out += bytes([len(raw)]) + raw
    return out + b"\0"


def _read_name(data, at, depth=0):
    """(name, where it ends) for a name at `at`, pointers followed."""
    parts, end = [], None
    while True:
        if at >= len(data) or depth > 20:
            raise ValueError("bad name")
        n = data[at]
        if n == 0:
            at += 1
            break
        if n & 0xC0 == 0xC0:
            ptr = ((n & 0x3F) << 8) | data[at + 1]
            if end is None:
                end = at + 2
            at, depth = ptr, depth + 1
            continue
        parts.append(data[at + 1:at + 1 + n].decode("utf-8", "replace"))
        at += 1 + n
    return ".".join(parts) + ".", end if end is not None else at


def _record(name, rtype, ttl, rdata, flush=False):
    return _name(name) + struct.pack("!HHIH", rtype, 0x8001 if flush else 1, ttl, len(rdata)) + rdata


def _txt(words):
    out = b""
    for k, v in words.items():
        raw = f"{k}={v}".encode("utf-8")[:255]
        out += bytes([len(raw)]) + raw
    return out or b"\0"


def parse(data):
    """(questions [(name, type)], records [(name, type, ttl, value)]) of a DNS packet; value: a PTR's name, an SRV's
    (port, target), a TXT's {key: value}, an A's address."""
    _id, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
    at, questions, records = 12, [], []
    for _ in range(qd):
        name, at = _read_name(data, at)
        qtype, _qclass = struct.unpack("!HH", data[at:at + 4])
        at += 4
        questions.append((name.lower(), qtype))
    for _ in range(an + ns + ar):
        name, at = _read_name(data, at)
        rtype, _rclass, ttl, size = struct.unpack("!HHIH", data[at:at + 10])
        at += 10
        raw, value = data[at:at + size], None
        if rtype == PTR:
            value = _read_name(data, at)[0]
        elif rtype == SRV:
            value = (struct.unpack("!H", raw[4:6])[0], _read_name(data, at + 6)[0])
        elif rtype == TXT:
            value, i = {}, 0
            while i < len(raw):
                n = raw[i]
                word = raw[i + 1:i + 1 + n].decode("utf-8", "replace")
                if "=" in word:
                    k, v = word.split("=", 1)
                    value[k] = v
                i += 1 + n
        elif rtype == A and size == 4:
            value = socket.inet_ntoa(raw)
        at += size
        records.append((name, rtype, ttl, value))
    return questions, records


def _socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):  # (macOS's mDNSResponder, Avahi and others listen on 5353 too)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(("", MDNS_PORT))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                 struct.pack("4s4s", socket.inet_aton(MDNS_ADDR), socket.inet_aton("0.0.0.0")))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    s.settimeout(1.0)
    return s


def local_address():
    """This machine's address on the network (the one a packet out would leave from), else 127.0.0.1."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((MDNS_ADDR, MDNS_PORT))  # (UDP: nothing's sent)
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _label(text):
    return "".join(c if c.isalnum() or c in "-" else "-" for c in text)[:60].strip("-") or "baude"


class _Port:
    """mDNS's port, listened on, opened again whenever it may have gone deaf. A socket hears the network it was opened
    on: one opened before the network was up, on a network the machine has since left (Wi-Fi changed, woken from sleep
    elsewhere), or before macOS let it use the local network, hears nothing however long it's kept, as if nothing were
    there till the app's restarted. So it's opened again (at the next time it speaks unasked) when the machine's address
    has changed, a send failed, or nothing's come in since it last spoke: its own packets come back to it
    (IP_MULTICAST_LOOP), so silence means it's deaf. Silence for CHECK_S after it speaks, and it's opened again then and
    there (deaf). One that can't be opened yet is tried again the next time it speaks."""

    def __init__(self):
        self.sock, self.where, self.heard, self.spoke = None, None, False, None
        self.stopped = threading.Event()
        self.lock = threading.Lock()

    def _reopen(self):
        with self.lock:
            if self.stopped.is_set():
                return
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock, self.where = None, local_address()
            try:
                self.sock = _socket()
            except OSError:
                pass  # (no multicast here yet: tried again the next time it speaks)

    def _due(self):
        """Before it speaks unasked: opened again if it may be deaf (or was never opened). True if it's open."""
        if self.sock is None or not self.heard or local_address() != self.where:
            self._reopen()
        self.heard, self.spoke = False, time.monotonic()
        return self.sock is not None

    def deaf(self):
        """Nothing back, not even what it said itself, in the CHECK_S since it last spoke unasked (said once a time)."""
        if self.spoke is not None and not self.heard and time.monotonic() - self.spoke > CHECK_S:
            self.spoke = None
            return True
        return False

    def _send(self, packet):
        sock = self.sock
        if sock is None:
            return
        try:
            sock.sendto(packet, (MDNS_ADDR, MDNS_PORT))
        except OSError:
            self.heard = False  # (opened again before it next speaks)

    def _receive(self):
        """(packet, who sent it), or None: nothing within a second, or nothing open to hear it on."""
        sock = self.sock
        if sock is None:
            self.stopped.wait(1)
            return None
        try:
            data, addr = sock.recvfrom(9000)
        except socket.timeout:
            return None
        except OSError:  # (closed under it: stopped, or opened again)
            if not self.stopped.is_set() and sock is self.sock:
                self.heard = False
                self.stopped.wait(1)
            return None
        self.heard = True
        return data, addr[0]

    def _close(self):
        self.stopped.set()
        with self.lock:
            sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


class Advertiser(_Port):
    """Says a server is on the network: on start, every ANNOUNCE_S, whenever asked, and goodbye (TTL 0) on stop."""

    def __init__(self, name, port, words):
        super().__init__()
        self.instance = f"{_label(name)}.{SERVICE}"
        self.host = f"{_label(socket.gethostname().split('.')[0])}.local."
        self.port, self.words = port, dict(words)

    def packet(self, ttl=TTL):
        answers = [_record(SERVICE, PTR, ttl, _name(self.instance)),
                   _record(self.instance, SRV, ttl, struct.pack("!HHH", 0, 0, self.port) + _name(self.host), True),
                   _record(self.instance, TXT, ttl, _txt(self.words), True),
                   _record(self.host, A, ttl, socket.inet_aton(local_address()), True)]
        return struct.pack("!HHHHHH", 0, 0x8400, 0, len(answers), 0, 0) + b"".join(answers)

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="mdns-advertise").start()
        return self

    def _say(self, ttl=TTL):
        self._send(self.packet(ttl))

    def _run(self):
        next_say, said = 0.0, 0
        while not self.stopped.is_set():
            if time.monotonic() >= next_say:
                where = self.where
                if self._due():
                    if self.where != where:
                        said = 0  # (somewhere new: said twice again, as at first)
                    self._say()
                    said += 1
                # (twice at first, a second apart, as RFC 6762 has it)
                next_say = time.monotonic() + (1 if said < 2 else ANNOUNCE_S)
            elif self.deaf():
                self._reopen()
                self._say()
            got = self._receive()
            if got is None:
                continue
            try:
                questions, _ = parse(got[0])
            except (ValueError, struct.error, IndexError):
                continue
            mine = {SERVICE.lower(), self.instance.lower(), self.host.lower()}
            if any(name in mine and qtype in (PTR, SRV, TXT, A, ANY) for name, qtype in questions):
                self._say()

    def stop(self):
        if not self.stopped.is_set():
            self._say(0)  # goodbye
        self._close()


class Browser(_Port):
    """Finds servers: found({instance, host, target (its host's name), port, words}) as one says it's there (again, whenever it does), lost(instance)
    when one says goodbye. Asks every QUERY_S, and at once when asked to (ask_soon). Calls come on its own thread."""

    def __init__(self, found, lost=None):
        super().__init__()
        self.found, self.lost = found, lost or (lambda _i: None)
        self.parts = {}  # instance -> {"port", "target", "words"}; host name -> address
        self.next_ask = 0.0

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="mdns-browse").start()
        return self

    def ask(self):
        self._send(struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + _name(SERVICE) + struct.pack("!HH", PTR, 1))

    def ask_soon(self):
        """Ask who's there now, not at the next QUERY_S (within a second: on its own thread)."""
        self.next_ask = 0.0

    def _run(self):
        while not self.stopped.is_set():
            if time.monotonic() >= self.next_ask:
                self.next_ask = time.monotonic() + QUERY_S
                if self._due():
                    self.ask()
            elif self.deaf():
                self._reopen()
                self.ask()
            got = self._receive()
            if got is None:
                continue
            try:
                _questions, records = parse(got[0])
            except (ValueError, struct.error, IndexError):
                continue
            self._take(records, got[1])

    def _take(self, records, sender):
        service = SERVICE.lower()
        touched, gone = set(), set()
        for name, rtype, ttl, value in records:
            if rtype == PTR and name.lower() == service and value:
                (gone if ttl == 0 else touched).add(value)
                self.parts.setdefault(value, {})
            elif rtype == SRV and name.lower().endswith("." + service) and value:
                p = self.parts.setdefault(name, {})
                p["port"], p["target"] = value
                touched.add(name)
            elif rtype == TXT and name.lower().endswith("." + service):
                self.parts.setdefault(name, {})["words"] = value or {}
                touched.add(name)
            elif rtype == A and value:
                self.parts[name.lower()] = value
        for instance in gone:
            self.parts.pop(instance, None)
            self.lost(instance)
        for instance in touched - gone:
            p = self.parts.get(instance) or {}
            if "port" not in p:
                continue
            host = self.parts.get((p.get("target") or "").lower())
            self.found({"instance": instance, "host": host if isinstance(host, str) else sender, "sender": sender,
                        "target": p.get("target"), "port": p["port"], "words": p.get("words") or {}})

    def stop(self):
        self._close()
