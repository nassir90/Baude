package dev.baude.android

import org.json.JSONObject

/** A kube request to a host, its reply on the main thread (an error one if it isn't there). */
fun kube(hostId: String, msg: Msg.Kube, then: (Msg.KubeReply) -> Unit) {
    Mesh.ask(hostId, msg.json()) { then(Msg.KubeReply.from(it)) }
}

/** A pod's log followed, or a kind's list watched, on a connection of its own to the host (as Browser's Watch is):
 * kube_follow sent as it comes up, what it sends handled on the main thread; stop() lets go of it, which stops the
 * kubectl the host runs for it. */
class KubeFollow(private val hostId: String, private val msg: Msg.KubeFollow, private val onText: (String) -> Unit = {},
                 private val onRows: (Msg.KubeRows) -> Unit = {}, private val onEnd: (Msg.KubeEnd) -> Unit = {},
                 private val onError: (String) -> Unit = {}, private val onUp: (Boolean) -> Unit = {}) {
    private var conn: Connection? = null
    @Volatile private var stopped = false

    /** Whether there's a host to follow through. */
    fun start(): Boolean {
        conn = Mesh.watch(hostId, ::heard) { c ->
            if (stopped) return@watch
            if (c.state == "up") {
                onUp(true)
                c.send(msg.json())
            } else if (c.state == "down") onUp(false)
        }
        return conn != null
    }

    fun stop() {
        stopped = true
        conn?.stop()
        conn = null
    }

    /** (on its connection's thread) What the host says of it. */
    private fun heard(c: Connection, m: JSONObject) {
        if (m.optInt("req", -1) != REQ) return
        when (m.optString("op")) {
            Op.KUBE_TEXT -> Msg.KubeText.from(m).text?.let { t -> Mesh.post { if (!stopped) onText(t) } }
            Op.KUBE_ROWS -> Msg.KubeRows.from(m).let { r -> Mesh.post { if (!stopped) onRows(r) } }
            Op.KUBE_END -> Msg.KubeEnd.from(m).let { e -> Mesh.post { if (!stopped) onEnd(e) } }
            Op.ERROR -> Msg.Error.from(m).said.orEmpty().let { e -> Mesh.post { if (!stopped) onError(e) } }
        }
    }

    companion object {
        const val REQ = 1 // (the follow's own: no request of the connection's is numbered so)
    }
}
