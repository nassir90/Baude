package dev.baude.android

/** Does a host take op, as its hello says (wire.takes): every op, unless it lists the ops it takes (a phone's, which
 * takes part of the wire) and op isn't one of them. "control" is a control request to a session's agent (set_model,
 * stop_task…); "ephemeral" is start's. One that doesn't take an op it's asked answers unsupported_operation. */
fun takes(info: Msg.Info?, op: String): Boolean = info?.ops?.let { op in it } ?: true

/** Does the host (a connection that's said hello) take op? */
fun takes(hostId: String, op: String): Boolean = takes(Mesh.conn(hostId)?.info ?: return false, op)

/** Does the host list op among the ops it takes (a phone's: what it keeps of claude's whatever the program)? */
fun lists(hostId: String, op: String): Boolean = Mesh.conn(hostId)?.info?.ops?.contains(op) == true
