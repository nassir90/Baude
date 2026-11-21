package dev.baude.android.screenshots

import android.content.Context
import dev.baude.android.Connection
import dev.baude.android.Host
import dev.baude.android.Mesh
import dev.baude.android.Msg
import dev.baude.android.Place
import org.json.JSONObject

/** A host's connection as the rig has it: up, its info given (every op taken), nothing ever sent or answered (what a
 * page asks for stays coming). */
class FixtureConnection(ctx: Context, host: Host) : Connection(ctx, Place.Direct(host), identify = { _, _ -> },
    route = { null }, heard = { _, _ -> }, changed = {}) {
    init {
        state = "up"
        info = Msg.Info(id = host.id, name = host.name, home = "/home/dev")
    }

    override fun start() {}
    override fun stop() {}
    override fun send(msg: JSONObject) = true
    override fun request(msg: JSONObject, then: (JSONObject) -> Unit) {}

    companion object {
        /** host's connection, as Mesh keeps one it's made (its own map's private: set through reflection). */
        fun up(ctx: Context, host: Host): FixtureConnection {
            val c = FixtureConnection(ctx, host)
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
