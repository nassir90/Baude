package dev.baude.android

/** The wire version each op a page uses came in (wire.VERSION's history): a host older than that doesn't speak it, so
 * it's never offered for that page. "control" is a control request to a session's claude (set_model, stop_task…),
 * which every version carries. */
private val SINCE = mapOf(
    "subscribe" to 1, "start" to 1, "control" to 1, "read_file" to 1, "ephemeral" to 2, "catalogue" to 5,
    "bot" to 8, "memory" to 8, "bot_picture" to 8, "emission" to 10, "emission_watch" to 11, "routines" to 12,
    "run_routine" to 12, "feedback" to 17, "app_tools" to 17, "list_dir" to 17, "git" to 17, "signal_names" to 17,
)

/** Does the host (a connection that's up) take op? By its version, as its hello said it. */
fun offers(hostId: String, op: String): Boolean {
    val c = Mesh.conn(hostId) ?: return false
    val v = c.info?.optInt("version", 1) ?: return false
    return v >= (SINCE[op] ?: Int.MAX_VALUE)
}

/** What to say when a host's too old for something. */
fun tooOld(hostId: String, what: String) = "${Mesh.label(hostId)}'s Baude can't $what: update it"
