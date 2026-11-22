package dev.baude.android.screenshots

import android.content.Context
import dev.baude.android.Connection
import dev.baude.android.Host
import dev.baude.android.Mesh
import dev.baude.android.Msg
import dev.baude.android.Place
import org.json.JSONArray
import org.json.JSONObject

/** A host's connection as the rig has it: up, its info given (every op taken), nothing ever sent, and nothing answered
 * (what a page asks for stays coming) but what answer gives an answer for. */
class FixtureConnection(ctx: Context, host: Host, home: String = "/home/dev", private val answer: (JSONObject) -> JSONObject? = { null }) : Connection(ctx, Place.Direct(host), identify = { _, _ -> },
    route = { null }, heard = { _, _ -> }, changed = {}) {
    init {
        state = "up"
        // (the models as the desktop rig's fake claude lists them, with their efforts: what the header's pickers need)
        val levels = JSONArray(listOf("low", "medium", "high", "xhigh", "max"))
        fun model(value: String, name: String, efforts: JSONArray?) = JSONObject().put("value", value).put("displayName", name)
            .put("resolvedModel", "claude-$value").apply { if (efforts != null) put("supportsEffort", true).put("supportedEffortLevels", efforts) }
        fun command(name: String, about: String, hint: String, builtin: Boolean) = JSONObject().put("name", name)
            .put("description", about).put("argumentHint", hint).put("builtin", builtin)
        val commands = JSONArray(listOf(command("debug", "Enable debug logging", "[issue]", true),
            command("deep-research", "Fan-out web research", "", true), command("design", "Make a new Design artifact", "[what]", true),
            command("clear", "Clear conversation", "", true), command("compact", "Compact conversation", "", true),
            command("code-review", "Review the current diff", "", false)))
        val init = JSONObject().put("commands", commands).put("models", JSONArray(listOf(model("opus", "Opus 5.5", levels),
            model("sonnet", "Sonnet 5.5", JSONArray(listOf("low", "medium", "high", "xhigh"))), model("claude-haiku-4-5", "Haiku 4.5", null))))
        info = Msg.Info(id = host.id, name = host.name, home = home, init = init)
    }

    override fun start() {}
    override fun stop() {}
    override fun send(msg: JSONObject) = true
    override fun request(msg: JSONObject, then: (JSONObject) -> Unit) {
        answer(msg)?.let(then)
    }

    companion object {
        /** host's connection, as Mesh keeps one it's made (its own map's private: set through reflection). */
        fun up(ctx: Context, host: Host, home: String = "/home/dev", answer: (JSONObject) -> JSONObject? = { null }): FixtureConnection {
            val c = FixtureConnection(ctx, host, home, answer)
            @Suppress("UNCHECKED_CAST")
            val conns = Mesh::class.java.getDeclaredField("conns").apply { isAccessible = true }.get(Mesh) as HashMap<String, Connection>
            conns[host.id] = c
            Mesh.states[host.id] = c
            return c
        }

        fun down() {
            @Suppress("UNCHECKED_CAST")
            (Mesh::class.java.getDeclaredField("conns").apply { isAccessible = true }.get(Mesh) as HashMap<String, Connection>).clear()
            Mesh.states.clear()
            Mesh.open = null
        }
    }
}
