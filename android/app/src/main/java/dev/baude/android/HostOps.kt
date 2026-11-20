package dev.baude.android

import org.json.JSONObject

/** Does the host (a connection that's up) take it? */
fun offers(hostId: String, it: Feature): Boolean = offers(Mesh.conn(hostId)?.info ?: return false, it)

/** Does a host take it, as its hello says (wire.speaks): by the ops it lists, where it lists them (a host that speaks
 * only part of the wire: a phone's), by the name it's listed by; else by its version, the one that brought it (Since:
 * the wire's schema's). */
fun offers(info: JSONObject?, it: Feature): Boolean {
    info ?: return false
    info.optJSONArray("ops")?.let { ops -> return (0 until ops.length()).any { i -> ops.optString(i) == it.listed } }
    return info.optInt("version", 1) >= it.since
}

/** What to say when a host's too old for something (one that lists the ops it takes: that it doesn't). */
fun tooOld(hostId: String, what: String) =
    if (Mesh.conn(hostId)?.info?.has("ops") == true) "${Mesh.label(hostId)} can't $what"
    else "${Mesh.label(hostId)}'s Baude can't $what: update it"
