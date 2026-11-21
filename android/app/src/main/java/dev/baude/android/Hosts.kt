package dev.baude.android

import android.content.Context
import com.jcraft.jsch.HostKey
import com.jcraft.jsch.HostKeyRepository
import com.jcraft.jsch.UserInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.Base64
import java.util.UUID

/** A host of the mesh, as ssh reaches it: user@address:port, its host process the command there (Baude's own default,
 * found on its PATH: baude_headless.py, run as "<command> connect"). Over Tailscale, address is its name or address
 * there. */
data class Host(
    val id: String = UUID.randomUUID().toString(),
    val name: String,
    val user: String,
    val address: String,
    val port: Int = 22,
    val command: String = "baude_headless.py",
) {
    val dest: String get() = "$user@$address" + if (port != 22) ":$port" else ""
    val label: String get() = name.ifBlank { address }

    fun json() = JSONObject().put("id", id).put("name", name).put("user", user).put("address", address)
        .put("port", port).put("command", command)

    companion object {
        fun of(o: JSONObject) = Host(
            o.getString("id"), o.optString("name"), o.getString("user"), o.getString("address"),
            o.optInt("port", 22), o.optString("command").ifBlank { "baude_headless.py" },
        )

        /** "user@address[:port]" read, or null if it isn't one. */
        fun parse(dest: String, name: String = "", command: String = ""): Host? {
            val m = Regex("""^\s*([^@\s]+)@([^:\s]+)(?::(\d+))?\s*$""").find(dest) ?: return null
            return Host(
                name = name.trim(), user = m.groupValues[1], address = m.groupValues[2],
                port = m.groupValues[3].toIntOrNull() ?: 22, command = command.trim().ifBlank { "baude_headless.py" },
            )
        }
    }
}

/** The hosts, and the host keys you've trusted (each host's, as it first showed it and you said yes to it). */
object HostStore {
    private fun prefs(ctx: Context) = ctx.getSharedPreferences("hosts", Context.MODE_PRIVATE)

    fun load(ctx: Context): List<Host> {
        val a = JSONArray(prefs(ctx).getString("hosts", "[]"))
        return (0 until a.length()).map { Host.of(a.getJSONObject(it)) }
    }

    fun save(ctx: Context, hosts: List<Host>) {
        prefs(ctx).edit().putString("hosts", JSONArray(hosts.map { it.json() }).toString()).apply()
    }

    private fun keyName(host: Host) = "key:${host.address}:${host.port}"

    /** The key trusted for the host: "type base64", or null. */
    fun trusted(ctx: Context, host: Host): String? = prefs(ctx).getString(keyName(host), null)

    fun trust(ctx: Context, host: Host, type: String, blob: ByteArray) {
        prefs(ctx).edit().putString(keyName(host), "$type ${Base64.getEncoder().encodeToString(blob)}").apply()
    }

    fun forget(ctx: Context, host: Host) {
        prefs(ctx).edit().remove(keyName(host)).remove(machineName(host)).apply()
    }

    private fun machineName(host: Host) = "machine:${host.address}:${host.port}"

    /** The id the host's host process last said it has (its hello's), or null if it's never been reached. */
    fun machine(ctx: Context, host: Host): String? = prefs(ctx).getString(machineName(host), null)

    fun setMachine(ctx: Context, host: Host, id: String) {
        if (machine(ctx, host) != id) prefs(ctx).edit().putString(machineName(host), id).apply()
    }

    /** The name a member of the mesh has on this phone (Mesh's Rename), by its id; null: its own. */
    fun alias(ctx: Context, member: String): String? = prefs(ctx).getString("alias:$member", null)

    fun setAlias(ctx: Context, member: String, name: String) {
        prefs(ctx).edit().apply { if (name.isBlank()) remove("alias:$member") else putString("alias:$member", name.trim()) }.apply()
    }

    /** The members of the mesh removed on this phone (Mesh's Remove), by id: not reached or listed here, till the
     * machine's added as a host. */
    fun removed(ctx: Context): Set<String> = prefs(ctx).getStringSet("removed", null).orEmpty()

    fun setRemoved(ctx: Context, member: String, removed: Boolean) {
        val now = removed(ctx).toMutableSet().apply { if (removed) add(member) else remove(member) }
        prefs(ctx).edit().putStringSet("removed", now).apply()
    }
}

/** The key a host showed, when it isn't the one trusted for it: none trusted yet, or another (was: the one trusted,
 * "type base64"), which it changed from. */
data class ShownKey(val type: String, val blob: ByteArray, val was: String? = null) {
    val changed: Boolean get() = was != null
    val fingerprint: String get() = fingerprintOf(blob)
    val wasType: String? get() = was?.substringBefore(" ")
    val wasFingerprint: String?
        get() = was?.substringAfter(" ", "")?.let { try { fingerprintOf(Base64.getDecoder().decode(it)) } catch (_: Exception) { null } }
}

/** JSch's host key check, by HostStore: the trusted key, or the connection's refused and what was shown kept. */
class TrustedKeys(private val ctx: Context, private val host: Host) : HostKeyRepository {
    @Volatile
    var shown: ShownKey? = null

    override fun check(name: String?, key: ByteArray): Int {
        val trusted = HostStore.trusted(ctx, host)
        val now = "${keyType(key)} ${Base64.getEncoder().encodeToString(key)}"
        if (trusted == now) return HostKeyRepository.OK
        shown = ShownKey(keyType(key), key, was = trusted)
        return if (trusted == null) HostKeyRepository.NOT_INCLUDED else HostKeyRepository.CHANGED
    }

    override fun add(hostkey: HostKey?, ui: UserInfo?) {}
    override fun remove(host: String?, type: String?) {}
    override fun remove(host: String?, type: String?, key: ByteArray?) {}
    override fun getKnownHostsRepositoryID(): String = "baude"
    override fun getHostKey(): Array<HostKey> = emptyArray()
    override fun getHostKey(host: String?, type: String?): Array<HostKey> = emptyArray()
}
