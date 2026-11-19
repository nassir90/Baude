package dev.baude.android

import android.content.Context
import android.os.Build
import com.jcraft.jsch.JSch
import com.jcraft.jsch.KeyPair
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import java.util.Base64

/** This phone's SSH key: made here (ECDSA P-256, which every OpenSSH takes and Android can make and sign with), kept in
 * the app's own files, its public half put in a host's ~/.ssh/authorized_keys by you. */
class Key(val private: ByteArray, val public: ByteArray) {
    /** The public key's line, as authorized_keys takes it. */
    val line: String get() = String(public).trim()

    /** Its fingerprint, as ssh-keygen -l shows it. */
    val fingerprint: String
        get() = fingerprintOf(Base64.getDecoder().decode(line.split(" ")[1]))
}

object Keys {
    private fun files(ctx: Context) = File(ctx.filesDir, "id_baude") to File(ctx.filesDir, "id_baude.pub")

    fun load(ctx: Context): Key? {
        val (prv, pub) = files(ctx)
        return if (prv.exists() && pub.exists()) Key(prv.readBytes(), pub.readBytes()) else null
    }

    /** A new key, in place of any there was (a host that had the old one in its authorized_keys needs this one). */
    fun make(ctx: Context): Key {
        val pair = KeyPair.genKeyPair(JSch(), KeyPair.ECDSA, 256)
        val prv = ByteArrayOutputStream().also { pair.writePrivateKey(it) }.toByteArray()
        val comment = "baude@" + (Build.MODEL ?: "android").replace(Regex("[^A-Za-z0-9._-]"), "-")
        val pub = ByteArrayOutputStream().also { pair.writePublicKey(it, comment) }.toByteArray()
        pair.dispose()
        val (prvFile, pubFile) = files(ctx)
        prvFile.writeBytes(prv)
        prvFile.setReadable(false, false)
        prvFile.setReadable(true, true)
        pubFile.writeBytes(pub)
        return Key(prv, pub)
    }
}

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
