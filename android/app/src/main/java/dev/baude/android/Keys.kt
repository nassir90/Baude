package dev.baude.android

import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import com.jcraft.jsch.Identity
import java.io.ByteArrayOutputStream
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.util.Base64

/** This phone's SSH key: ECDSA P-256 (ecdsa-sha2-nistp256, which every OpenSSH takes), made in the Android Keystore,
 * which signs with it and never gives it out: in the phone's secure hardware, where it has some. Its public half is
 * put in a host's ~/.ssh/authorized_keys by you. */
class Key(private val private: PrivateKey, public: ECPublicKey) {
    /** Its public key as ssh has it: string "ecdsa-sha2-nistp256", string "nistp256", string Q (0x04 X Y). */
    val blob: ByteArray = ssh {
        string(ALG.toByteArray())
        string(CURVE.toByteArray())
        string(byteArrayOf(4) + fixed(public.w.affineX) + fixed(public.w.affineY))
    }

    /** The public key's line, as authorized_keys takes it. */
    val line: String get() = "$ALG ${Base64.getEncoder().encodeToString(blob)} ${comment()}"

    /** Its fingerprint, as ssh-keygen -l shows it. */
    val fingerprint: String get() = fingerprintOf(blob)

    /** What JSch signs with: the Keystore, asked to sign each time (SHA256withECDSA, its DER made ssh's r and s).
     * failed: why it couldn't sign, if it couldn't (JSch hears only that there's no signature). */
    fun identity(failed: (String) -> Unit = {}): Identity = object : Identity {
        override fun setPassphrase(passphrase: ByteArray?) = true
        override fun getPublicKeyBlob(): ByteArray = blob
        override fun getSignature(data: ByteArray): ByteArray? = try {
            val der = Signature.getInstance("SHA256withECDSA").run {
                initSign(private)
                update(data)
                sign()
            }
            val (r, s) = rs(der)
            ssh {
                string(ALG.toByteArray())
                string(ssh { string(r.toByteArray()); string(s.toByteArray()) })
            }
        } catch (e: Exception) {
            failed(e.message ?: e.javaClass.simpleName)
            null
        }
        override fun getAlgName() = ALG
        override fun getName() = "baude"
        override fun isEncrypted() = false
        override fun clear() {}
    }

    companion object {
        const val ALG = "ecdsa-sha2-nistp256"
        private const val CURVE = "nistp256"
    }
}

object Keys {
    private const val ALIAS = "baude-ssh"

    private fun store() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    /** The key, or null if none's been made; an exception if the Keystore couldn't be read (not the same: a key may
     * well be there). */
    fun load(): Key? {
        val store = store()
        if (!store.containsAlias(ALIAS)) return null
        val entry = store.getEntry(ALIAS, null) as? KeyStore.PrivateKeyEntry
            ?: throw IllegalStateException("its entry isn't a private key")
        return Key(entry.privateKey, entry.certificate.publicKey as ECPublicKey)
    }

    /** A new key, in place of any there was (a host that had the last one in its authorized_keys needs this one): in
     * the phone's StrongBox if it has one, else its trusted environment (or, on a phone with neither, Android's
     * software Keystore). */
    fun make(): Key {
        fun spec(strongBox: Boolean) = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256)
            .apply { if (strongBox && Build.VERSION.SDK_INT >= 28) setIsStrongBoxBacked(true) }
            .build()
        val gen = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        val pair = try {
            if (Build.VERSION.SDK_INT < 28) throw IllegalStateException()
            gen.initialize(spec(true))
            gen.generateKeyPair()
        } catch (_: Exception) { // (no StrongBox: the trusted environment's)
            gen.initialize(spec(false))
            gen.generateKeyPair()
        }
        return Key(pair.private, pair.public as ECPublicKey)
    }
}

/** The comment a key's line ends with: the phone it's on. */
private fun comment() = "baude@" + (Build.MODEL ?: "android").replace(Regex("[^A-Za-z0-9._-]"), "-")

/** A coordinate as P-256's 32 bytes, big-endian. */
private fun fixed(n: BigInteger): ByteArray {
    val b = n.toByteArray().let { if (it.size > 32) it.copyOfRange(it.size - 32, it.size) else it }
    return ByteArray(32 - b.size) + b
}

/** r and s of an ECDSA signature in DER: SEQUENCE { INTEGER r, INTEGER s }. */
private fun rs(der: ByteArray): Pair<BigInteger, BigInteger> {
    var at = 0
    fun len(): Int {
        var n = der[at++].toInt() and 0xff
        if (n and 0x80 != 0) {
            val count = n and 0x7f
            n = 0
            repeat(count) { n = (n shl 8) or (der[at++].toInt() and 0xff) }
        }
        return n
    }
    fun int(): BigInteger {
        require(der[at++].toInt() == 0x02)
        val n = len()
        return BigInteger(1, der.copyOfRange(at, at + n)).also { at += n }
    }
    require(der[at++].toInt() == 0x30)
    len()
    return int() to int()
}

/** Bytes as ssh lays them out: each string its length (4 bytes, big-endian), then it. */
private class SshOut {
    val out = ByteArrayOutputStream()
    fun string(b: ByteArray) {
        out.write(byteArrayOf((b.size ushr 24).toByte(), (b.size ushr 16).toByte(), (b.size ushr 8).toByte(), b.size.toByte()))
        out.write(b)
    }
}

private fun ssh(build: SshOut.() -> Unit): ByteArray = SshOut().apply(build).out.toByteArray()

/** SHA256:… of a key's blob, as OpenSSH writes it (base64, no padding). */
fun fingerprintOf(blob: ByteArray): String =
    "SHA256:" + Base64.getEncoder().withoutPadding().encodeToString(MessageDigest.getInstance("SHA-256").digest(blob))

/** The type a key's blob says it is (its first string: ssh-ed25519, ecdsa-sha2-nistp256, …). */
fun keyType(blob: ByteArray): String {
    if (blob.size < 4) return "?"
    val n = ((blob[0].toInt() and 0xff) shl 24) or ((blob[1].toInt() and 0xff) shl 16) or
        ((blob[2].toInt() and 0xff) shl 8) or (blob[3].toInt() and 0xff)
    return if (n in 1..(blob.size - 4)) String(blob, 4, n) else "?"
}
