package dev.baude.android

import org.json.JSONObject

/** The wire version each op a page uses came in (wire.VERSION's history): a host older than that doesn't speak it, so
 * it's never offered for that page. "control" is a control request to a session's claude (set_model, stop_task…),
 * which every version carries. */
private val SINCE = mapOf(
    "subscribe" to 1, "start" to 1, "control" to 1, "read_file" to 1, "ephemeral" to 2, "catalogue" to 5,
    "bot" to 8, "memory" to 8, "bot_picture" to 8, "emission" to 10, "emission_watch" to 11, "routines" to 12,
    "run_routine" to 12, "always_allow" to 15, "feedback" to 17, "app_tools" to 17, "list_dir" to 17, "git" to 17,
    "signal_names" to 17,
)

/** Does the host (a connection that's up) take op? */
fun offers(hostId: String, op: String): Boolean = offers(Mesh.conn(hostId)?.info ?: return false, op)

/** Does a host take op, as its hello says (wire.speaks): by the ops it lists, where it lists them (a host that speaks
 * only part of the wire: a phone's); else by its version, the one that brought op (since; SINCE's, if it's not
 * given). */
fun offers(info: JSONObject?, op: String, since: Int? = null): Boolean {
    info ?: return false
    info.optJSONArray("ops")?.let { ops -> return (0 until ops.length()).any { ops.optString(it) == op } }
    return info.optInt("version", 1) >= (since ?: SINCE[op] ?: Int.MAX_VALUE)
}

/** What to say when a host's too old for something (one that lists the ops it takes: that it doesn't). */
fun tooOld(hostId: String, what: String) =
    if (Mesh.conn(hostId)?.info?.has("ops") == true) "${Mesh.label(hostId)} can't $what"
    else "${Mesh.label(hostId)}'s Baude can't $what: update it"
