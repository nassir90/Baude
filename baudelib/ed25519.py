"""Ed25519 (RFC 8032), in Python alone: each mesh member's key (mesh.Mesh.key), which signs the tunnels it opens
(open's signed and sig), and what checks them where they're opened to. Nothing secret depends on it but the member's
own key, kept in its state folder; a signature's a few milliseconds to make and to check. The phone's Ed25519.kt is
the same, so the two check each other's."""
import hashlib
import os

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
_I = pow(2, (P - 1) // 4, P)


def _x(y, sign):
    """The x of a point on the curve with y (and x's low bit sign): None if there's none."""
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * _I % P
    if (x * x - x2) % P:
        return None
    return P - x if x & 1 != sign else x


_GY = 4 * pow(5, P - 2, P) % P
_GX = _x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % P)
_ZERO = (0, 1, 1, 0)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % P
    b = (p[1] + p[0]) * (q[1] + q[0]) % P
    c = 2 * p[3] * q[3] * D % P
    d = 2 * p[2] * q[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % P, g * h % P, f * g % P, e * h % P


def _mul(s, p):
    q = _ZERO
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _same(p, q):
    return (p[0] * q[2] - q[0] * p[2]) % P == 0 and (p[1] * q[2] - q[1] * p[2]) % P == 0


def _bytes(p):
    z = pow(p[2], P - 2, P)
    x, y = p[0] * z % P, p[1] * z % P
    return (y | (x & 1) << 255).to_bytes(32, "little")


def _point(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    x = _x(y & (1 << 255) - 1, y >> 255)
    return None if x is None else (x, y & (1 << 255) - 1, 1, x * (y & (1 << 255) - 1) % P)


def _hash(*parts):
    return int.from_bytes(hashlib.sha512(b"".join(parts)).digest(), "little")


def _expand(secret):
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little") & (1 << 254) - 8 | 1 << 254
    return a, h[32:]


def new_secret():
    return os.urandom(32)


def public(secret):
    """The public key (32 bytes) of a secret one (32 bytes)."""
    return _bytes(_mul(_expand(secret)[0], _G))


def sign(secret, message):
    """message's signature (64 bytes) by secret."""
    a, prefix = _expand(secret)
    key = _bytes(_mul(a, _G))
    r = _hash(prefix, message) % L
    big_r = _bytes(_mul(r, _G))
    return big_r + ((r + _hash(big_r, key, message) * a) % L).to_bytes(32, "little")


def verify(key, message, signature):
    """Is signature message's, by the secret key (public, 32 bytes) is of?"""
    if not isinstance(key, bytes) or not isinstance(signature, bytes) or len(key) != 32 or len(signature) != 64:
        return False
    a, r = _point(key), _point(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= L:
        return False
    return _same(_mul(s, _G), _add(r, _mul(_hash(signature[:32], key, message) % L, a)))
