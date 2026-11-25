package dev.baude.android

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

/** This phone's own Claude accounts, as a host's are (hostaccounts), for the phone as a host: it has no claude, so each
 * is a token from claude setup-token (kind "token"), kept in the app's own files (no other app can read them) and
 * never sent anywhere else. Answers the accounts op: list, add, rename, token, remove, select, usage (null: a setup
 * token can't read usage). The phone has no Codex. */
object PhoneAccounts {
    private val lock = Any()

    private fun file() = File(File(PhoneHost.ctx.filesDir, "phone"), "accounts.json")

    private fun read(): JSONObject = try {
        JSONObject(file().readText())
    } catch (_: Exception) {
        JSONObject().put("accounts", JSONArray()).put("default", "")
    }

    private fun write(o: JSONObject) {
        val f = file()
        f.parentFile?.mkdirs()
        val tmp = File(f.parentFile, f.name + ".tmp")
        tmp.writeText(o.toString())
        tmp.renameTo(f)
    }

    private fun list(o: JSONObject): List<JSONObject> = o.optJSONArray("accounts")?.let { a ->
        (0 until a.length()).mapNotNull { a.optJSONObject(it) }
    }.orEmpty()

    private fun checkToken(token: String?): String {
        val t = token.orEmpty().trim()
        if (!t.startsWith("sk-ant-oat") || t.any { it.isWhitespace() }) throw IllegalArgumentException(Wording.Subscriptions.enterToken)
        return t
    }

    /** An accounts request's answer's fields (accounts, defaults, claude_cli; error, usage). */
    fun answer(msg: JSONObject): Array<Pair<String, Any?>> = synchronized(lock) {
        val m = Msg.Accounts.from(msg)
        val o = read()
        val all = list(o).toMutableList()
        val id = m.id.orEmpty()
        val extra = ArrayList<Pair<String, Any?>>()
        val error = try {
            when (m.what) {
                "list" -> Unit
                "add" -> {
                    val name = m.name.orEmpty().trim()
                    if (m.provider != "claude" || name.isEmpty()) throw IllegalArgumentException(Wording.Subscriptions.enterName)
                    if (all.any { it.optString("name") == name }) throw IllegalArgumentException(Wording.Subscriptions.nameInUse)
                    all.add(JSONObject().put("id", UUID.randomUUID().toString().replace("-", "")).put("name", name)
                        .put("token", checkToken(m.token)))
                }
                "rename" -> {
                    val name = m.name.orEmpty().trim()
                    if (name.isEmpty()) throw IllegalArgumentException(Wording.Subscriptions.enterName)
                    val a = all.firstOrNull { it.optString("id") == id } ?: throw IllegalArgumentException(Wording.Subscriptions.gone)
                    if (all.any { it !== a && it.optString("name") == name }) throw IllegalArgumentException(Wording.Subscriptions.nameInUse)
                    a.put("name", name)
                }
                "token" -> (all.firstOrNull { it.optString("id") == id } ?: throw IllegalArgumentException(Wording.Subscriptions.gone))
                    .put("token", checkToken(m.token))
                "remove" -> {
                    if (!all.removeAll { it.optString("id") == id }) throw IllegalArgumentException(Wording.Subscriptions.gone)
                    if (o.optString("default") == id) o.put("default", "")
                }
                "select" -> {
                    if (id.isNotEmpty() && all.none { it.optString("id") == id }) throw IllegalArgumentException(Wording.Subscriptions.gone)
                    o.put("default", id)
                }
                "usage" -> extra.add("usage" to JSONObject.NULL)
                else -> throw IllegalArgumentException(Wording.Subscriptions.noClaude)
            }
            o.put("accounts", JSONArray(all))
            write(o)
            null
        } catch (e: IllegalArgumentException) {
            e.message
        }
        val shown = JSONArray(all.sortedBy { it.optString("name").lowercase() }.map {
            JSONObject().put("id", it.optString("id")).put("provider", "claude").put("name", it.optString("name")).put("kind", "token")
        })
        return arrayOf("what" to m.what, "accounts" to shown,
            "defaults" to JSONObject().put("claude", o.optString("default")).put("codex", ""), "claude_cli" to false,
            "error" to error, *extra.toTypedArray())
    }
}
