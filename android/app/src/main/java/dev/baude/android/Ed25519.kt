package dev.baude.android

import java.math.BigInteger
import java.security.MessageDigest
import java.security.SecureRandom

/** Ed25519 (RFC 8032), as baudelib/ed25519.py has it, the same so each checks the other's: the phone's key as a member
 * of the mesh (PhoneLink), which signs the tunnels it opens, and what checks the opens made to it. */
object Ed25519 {
    private val P = BigInteger.TWO.pow(255) - BigInteger.valueOf(19)
    private val L = BigInteger.TWO.pow(252) + BigInteger("27742317777372353535851937790883648493")
    private val D = (BigInteger.valueOf(-121665) * BigInteger.valueOf(121666).modInverse(P)).mod(P)
    private val I = BigInteger.TWO.modPow((P - BigInteger.ONE).shiftRight(2), P)
    private val MASK = BigInteger.ONE.shiftLeft(255) - BigInteger.ONE

    private class Pt(val x: BigInteger, val y: BigInteger, val z: BigInteger, val t: BigInteger)

    private fun x(y: BigInteger, sign: Int): BigInteger? {
        if (y >= P) return null
        val x2 = ((y * y - BigInteger.ONE) * (D * y * y + BigInteger.ONE).modInverse(P)).mod(P)
        if (x2.signum() == 0) return if (sign != 0) null else BigInteger.ZERO
        var x = x2.modPow((P + BigInteger.valueOf(3)).shiftRight(3), P)
        if ((x * x - x2).mod(P).signum() != 0) x = (x * I).mod(P)
        if ((x * x - x2).mod(P).signum() != 0) return null
        return if (x.testBit(0) != (sign != 0)) P - x else x
    }

    private val GY = (BigInteger.valueOf(4) * BigInteger.valueOf(5).modInverse(P)).mod(P)
    private val GX = x(GY, 0)!!
    private val G = Pt(GX, GY, BigInteger.ONE, (GX * GY).mod(P))
    private val ZERO = Pt(BigInteger.ZERO, BigInteger.ONE, BigInteger.ONE, BigInteger.ZERO)

    private fun add(p: Pt, q: Pt): Pt {
        val a = ((p.y - p.x) * (q.y - q.x)).mod(P)
        val b = ((p.y + p.x) * (q.y + q.x)).mod(P)
        val c = (BigInteger.TWO * p.t * q.t * D).mod(P)
        val d = (BigInteger.TWO * p.z * q.z).mod(P)
        val e = b - a; val f = d - c; val g = d + c; val h = b + a
        return Pt((e * f).mod(P), (g * h).mod(P), (f * g).mod(P), (e * h).mod(P))
    }

    private fun mul(s0: BigInteger, p0: Pt): Pt {
        var s = s0; var p = p0; var q = ZERO
        while (s.signum() > 0) {
            if (s.testBit(0)) q = add(q, p)
            p = add(p, p)
            s = s.shiftRight(1)
        }
        return q
    }

    private fun same(p: Pt, q: Pt) = (p.x * q.z - q.x * p.z).mod(P).signum() == 0 && (p.y * q.z - q.y * p.z).mod(P).signum() == 0

    private fun le(b: ByteArray) = BigInteger(1, b.reversedArray())

    /** n (below 2^256) as 32 bytes, the lowest first. */
    private fun le32(n: BigInteger): ByteArray {
        val big = n.toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
        return big.reversedArray() + ByteArray(32 - big.size)
    }

    private fun bytes(p: Pt): ByteArray {
        val z = p.z.modInverse(P)
        val x = (p.x * z).mod(P); val y = (p.y * z).mod(P)
        return le32(if (x.testBit(0)) y.setBit(255) else y)
    }

    private fun point(b: ByteArray): Pt? {
        if (b.size != 32) return null
        val n = le(b)
        val y = n.and(MASK)
        val x = x(y, if (n.testBit(255)) 1 else 0) ?: return null
        return Pt(x, y, BigInteger.ONE, (x * y).mod(P))
    }

    private fun hash(vararg parts: ByteArray): BigInteger {
        val md = MessageDigest.getInstance("SHA-512")
        for (p in parts) md.update(p)
        return le(md.digest())
    }

    private fun expand(secret: ByteArray): Pair<BigInteger, ByteArray> {
        val h = MessageDigest.getInstance("SHA-512").digest(secret)
        var a = le(h.copyOfRange(0, 32))
        a = a.and(BigInteger.ONE.shiftLeft(254) - BigInteger.valueOf(8)).or(BigInteger.ONE.shiftLeft(254))
        return a to h.copyOfRange(32, 64)
    }

    fun newSecret(): ByteArray = ByteArray(32).also { SecureRandom().nextBytes(it) }

    /** The public key (32 bytes) of a secret one (32 bytes). */
    fun public(secret: ByteArray): ByteArray = bytes(mul(expand(secret).first, G))

    /** message's signature (64 bytes) by secret. */
    fun sign(secret: ByteArray, message: ByteArray): ByteArray {
        val (a, prefix) = expand(secret)
        val key = bytes(mul(a, G))
        val r = hash(prefix, message).mod(L)
        val bigR = bytes(mul(r, G))
        return bigR + le32((r + hash(bigR, key, message) * a).mod(L))
    }

    /** Is signature message's, by the secret key (public, 32 bytes) is of? */
    fun verify(key: ByteArray, message: ByteArray, signature: ByteArray): Boolean {
        if (key.size != 32 || signature.size != 64) return false
        val a = point(key) ?: return false
        val r = point(signature.copyOfRange(0, 32)) ?: return false
        val s = le(signature.copyOfRange(32, 64))
        if (s >= L) return false
        return same(mul(s, G), add(r, mul(hash(signature.copyOfRange(0, 32), key, message).mod(L), a)))
    }
}
