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


class Advertiser:
    """Says a server is on the network: on start, every ANNOUNCE_S, whenever asked, and goodbye (TTL 0) on stop."""

    def __init__(self, name, port, words):
        self.instance = f"{_label(name)}.{SERVICE}"
        self.host = f"{_label(socket.gethostname().split('.')[0])}.local."
        self.port, self.words = port, dict(words)
        self.stopped = threading.Event()
        self.sock = None

    def packet(self, ttl=TTL):
        answers = [_record(SERVICE, PTR, ttl, _name(self.instance)),
                   _record(self.instance, SRV, ttl, struct.pack("!HHH", 0, 0, self.port) + _name(self.host), True),
                   _record(self.instance, TXT, ttl, _txt(self.words), True),
                   _record(self.host, A, ttl, socket.inet_aton(local_address()), True)]
        return struct.pack("!HHHHHH", 0, 0x8400, 0, len(answers), 0, 0) + b"".join(answers)

    def start(self):
        self.sock = _socket()
        threading.Thread(target=self._run, daemon=True, name="mdns-advertise").start()
        return self

    def _say(self, ttl=TTL):
        try:
            self.sock.sendto(self.packet(ttl), (MDNS_ADDR, MDNS_PORT))
        except OSError:
            pass

    def _run(self):
        self._say()
        next_say = time.monotonic() + 1  # (said twice at first, a second apart, as RFC 6762 has it)
        said = 1
        while not self.stopped.is_set():
            if time.monotonic() >= next_say:
                self._say()
                said += 1
                next_say = time.monotonic() + (1 if said < 2 else ANNOUNCE_S)
            try:
                data, _addr = self.sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                if self.stopped.is_set():
                    break
                time.sleep(1)
                continue
            try:
                questions, _ = parse(data)
            except (ValueError, struct.error, IndexError):
                continue
            mine = {SERVICE.lower(), self.instance.lower(), self.host.lower()}
            if any(name in mine and qtype in (PTR, SRV, TXT, A, ANY) for name, qtype in questions):
                self._say()

    def stop(self):
        if self.sock is None:
            return
        self.stopped.set()
        self._say(0)  # goodbye
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """Finds servers: found({instance, host, port, words}) as one says it's there (again, whenever it does), lost(instance)
    when one says goodbye. Asks every QUERY_S. Calls come on its own thread."""

    def __init__(self, found, lost=None):
        self.found, self.lost = found, lost or (lambda _i: None)
        self.stopped = threading.Event()
        self.sock = None
        self.parts = {}  # instance -> {"port", "target", "words"}; host name -> address

    def start(self):
        try:
            self.sock = _socket()
        except OSError:
            return None  # (no multicast here: servers can still be given by address)
        threading.Thread(target=self._run, daemon=True, name="mdns-browse").start()
        return self

    def ask(self):
        q = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + _name(SERVICE) + struct.pack("!HH", PTR, 1)
        try:
            self.sock.sendto(q, (MDNS_ADDR, MDNS_PORT))
        except OSError:
            pass

    def _run(self):
        next_ask = 0.0
        while not self.stopped.is_set():
            if time.monotonic() >= next_ask:
                self.ask()
                next_ask = time.monotonic() + QUERY_S
            try:
                data, addr = self.sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                if self.stopped.is_set():
                    break
                time.sleep(1)
                continue
            try:
                _questions, records = parse(data)
            except (ValueError, struct.error, IndexError):
                continue
            self._take(records, addr[0])

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
                        "port": p["port"], "words": p.get("words") or {}})

    def stop(self):
        self.stopped.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
