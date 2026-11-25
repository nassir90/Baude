package dev.baude.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Base64
import java.util.Date
import java.util.Locale

/** Characters of each tool call's input, and of its result, in a copied conversation (meta.REPORT_ITEM). */
private const val ITEM = 4000

/** How long a copied conversation is at most (meta.report_most: what an issue's body holds, with room for the note). */
private const val MOST = 65_000 - 5_000

/** ⋮ → Copy Transcript for an Issue (baude.copy_report): the chat as text for an issue on the clipboard, its head (the
 * app, the session, its model, effort, mode, host and folder, when it was copied) and then the whole transcript read
 * from its host, each tool call with its input and result; "Transcript copied". */
fun copyReport(ctx: Context, hostId: String, sid: String) {
    val app = ctx.applicationContext
    val s = Mesh.session(hostId, sid)
    val host = Mesh.hosts.firstOrNull { it.id == hostId }
    val head = listOf(Wording.Report.app to Wording.MeshVersions.android, Wording.Report.session to sid,
        Wording.Common.model to (s?.model?.ifBlank { null } ?: "default"),
        Wording.MetaSettings.effort to (s?.effort?.ifBlank { null } ?: "default"),
        Wording.Report.mode to s?.mode.orEmpty(),
        Wording.NewSession.host to Mesh.label(hostId) + (host?.let { " (${it.address})" } ?: ""),
        Wording.Subscriptions.account to (s?.account?.name ?: Wording.Subscriptions.cliDefault),
        Wording.Common.folder to s?.cwd.orEmpty(),
        Wording.Report.context to Wording.Report.notSaidYet,
        Wording.Report.copied to SimpleDateFormat("yyyy-MM-dd HH:mm:ss Z", Locale.ENGLISH).format(Date()))
    CoroutineScope(Dispatchers.Default).launch {
        val text = try {
            val read = readTranscript(hostId, sid)
            if (read.isEmpty()) Wording.Report.nothingSaid + "\n" else report(head, read)
        } catch (e: Exception) {
            report(head, null, e.message ?: e.toString())
        }
        Handler(Looper.getMainLooper()).post {
            app.getSystemService(ClipboardManager::class.java)
                .setPrimaryClip(ClipData.newPlainText(Wording.Window.copyTranscript, text))
            Toast.makeText(app, Wording.Window.transcriptCopied, Toast.LENGTH_SHORT).show()
        }
    }
}

/** A session's whole transcript from its host (the bytes put together, then decoded once): "" with nothing said yet. */
private suspend fun readTranscript(hostId: String, sid: String): String {
    val all = java.io.ByteArrayOutputStream()
    var from = 0L
    while (true) {
        val r = asked(hostId, Msg.Transcript(session = sid, offset = from).json())
        if (r.optString("op") != Op.TRANSCRIPT) throw Exception(Msg.Error.from(r).said.orEmpty())
        val got = Msg.TranscriptReply.from(r)
        got.data.orEmpty().takeIf { it.isNotEmpty() }?.let { all.write(Base64.getDecoder().decode(it)) }
        val next = got.next?.toLong() ?: break
        val size = got.size?.toLong() ?: next
        if (next >= size || next <= from) break
        from = next
    }
    return String(all.toByteArray(), Charsets.UTF_8)
}

/** A text as meta.trimmed has it: its start as far as limit, and how much is left. */
fun trimmed(text: String, limit: Int = ITEM): String =
    if (text.length <= limit) text else text.take(limit).trimEnd() + "\n" + Wording.Report.moreCharacters(n = (text.length - limit).toString())

/** The head's lines, the values in a column (those with none left out). */
fun headText(head: List<Pair<String, String>>): String {
    val shown = head.filter { it.second.isNotEmpty() }
    val width = shown.maxOfOrNull { it.first.length } ?: 0
    return shown.joinToString("\n") { (it.first + ":").padEnd(width + 1) + " " + it.second }
}

/** The head, then the parts as many of the last as fit (meta.capped_report), what was left out said. */
fun capped(head: List<Pair<String, String>>, parts: List<String>): String {
    val top = headText(head)
    val kept = ArrayList<String>()
    var size = top.length
    for (part in parts.reversed()) {
        if (size + part.length + 2 > MOST - 200) {
            if (kept.isEmpty()) kept.add("[…]" + part.takeLast((MOST - 200 - size).coerceAtLeast(0)))
            break
        }
        kept.add(part)
        size += part.length + 2
    }
    val cut = parts.size - kept.size
    return (listOf(top) + (if (cut > 0) listOf(Wording.Report.leftOut(n = cut)) else emptyList()) + kept.reversed())
        .joinToString("\n\n").trim() + "\n"
}

/** A compaction as its marker says it (chatparts.compact_text). */
fun compactText(meta: JSONObject?): String {
    fun num(a: String, b: String): Long? = meta?.let { m -> listOf(a, b).firstNotNullOfOrNull { k -> m.opt(k).takeIf { it is Number } as Number? } }?.toLong()
    val pre = num("pre_tokens", "preTokens")
    val post = num("post_tokens", "postTokens")
    val took = num("duration_ms", "durationMs")
    val parts = ArrayList<String>()
    parts.add(when (meta?.optString("trigger")) {
        "auto" -> Wording.Compaction.compactedAuto
        "manual" -> Wording.Compaction.compactedManual
        else -> Wording.Compaction.compacted
    })
    if (pre != null && pre > 0) parts.add(if (post != null && post > 0)
        Wording.Compaction.tokensBeforeAfter(pre = fmtTokens(pre.toInt()), post = fmtTokens(post.toInt()))
        else Wording.Compaction.tokensBefore(pre = fmtTokens(pre.toInt())))
    if (took != null && took >= 1000) {
        val secs = Math.round(took / 1000.0)
        parts.add(if (secs >= 60) Wording.Time.minutesSeconds(minutes = (secs / 60).toString(), seconds = (secs % 60).toString()) else Wording.Time.seconds(seconds = secs.toString()))
    }
    return parts.joinToString(" · ")
}

private fun blocks(content: Any?): List<JSONObject> = when (content) {
    is String -> listOf(JSONObject().put("type", "text").put("text", content))
    is JSONArray -> (0 until content.length()).mapNotNull { content.optJSONObject(it) }
    else -> emptyList()
}

/** The transcript as text for an issue (meta.transcript_report): the head, then who said what, each tool call with its
 * whole input, its result and whether it failed (each as far as ITEM); its end, if it's longer than MOST. A transcript
 * that couldn't be read (read null: why) is said in place of its parts. */
private fun report(head: List<Pair<String, String>>, read: String?, why: String = ""): String {
    if (read == null) return capped(head, listOf(Wording.Report.notRead(why = why)))
    val parts = ArrayList<String>()
    val calls = HashMap<String, String>()
    for (raw in read.lineSequence()) {
        val e = try { JSONObject(raw) } catch (_: Exception) { continue }
        val type = e.optString("type")
        val stamp = e.optString("timestamp").replace('T', ' ').take(19)
        if (type == "system" && e.optString("subtype") == "compact_boundary" && !e.optBoolean("isSidechain")) {
            parts.add("## " + compactText(e.optJSONObject("compactMetadata")) + if (stamp.isNotEmpty()) " · $stamp" else "")
            continue
        }
        if (type != "user" && type != "assistant" || e.optBoolean("isSidechain") || e.optBoolean("isMeta")) continue
        val msg = e.optJSONObject("message") ?: JSONObject()
        val content = msg.opt("content")
        if (e.optBoolean("isCompactSummary")) {
            val text = if (content is String) content else blocks(content).filter { it.optString("type") == "text" }
                .joinToString("\n") { it.optString("text") }
            val said = Wording.Report.summary + "\n" + trimmed(text.trim(), ITEM)
            if (parts.isNotEmpty() && parts.last().startsWith("## " + Wording.Compaction.compacted) && "\n" !in parts.last())
                parts[parts.size - 1] = parts.last() + "\n" + said
            else parts.add("## $said")
            continue
        }
        val out = ArrayList<String>()
        for (b in blocks(content)) when (b.optString("type")) {
            "text" -> b.optString("text").trim().takeIf { it.isNotEmpty() }?.let { out.add(it) }
            "tool_use" -> {
                calls[b.optString("id")] = b.optString("name")
                val input = (b.optJSONObject("input") ?: JSONObject()).toString(1).replace("\\/", "/")
                out.add(Wording.Report.toolCall(name = b.optString("name")) + "\n" + trimmed(input, ITEM))
            }
            "tool_result" -> {
                val res = b.opt("content").let { r ->
                    if (r is String) r else blocks(r).joinToString("\n") { it.optString("text").ifEmpty { "[${it.optString("type")}]" } }
                }
                val name = calls[b.optString("tool_use_id")] ?: Wording.Report.unknownTool
                out.add((if (b.optBoolean("is_error")) Wording.Report.toolError(name = name) else Wording.Report.toolResult(name = name)) +
                    "\n" + trimmed(res, ITEM))
            }
        }
        if (out.isEmpty()) continue
        val bs = blocks(content)
        val results = type == "user" && content is JSONArray && bs.size == content.length() && bs.all { it.optString("type") == "tool_result" }
        val model = msg.optString("model")
        val who = when {
            results -> Wording.Report.toolResults
            type == "user" -> Wording.Report.user
            else -> (if (model.isNotEmpty() && model != "<synthetic>") Wording.Report.claudeModel(model = model) else Wording.Report.claude) +
                if (e.optBoolean("isApiErrorMessage")) " " + Wording.Report.apiError else ""
        }
        parts.add("## $who" + (if (stamp.isNotEmpty()) " · $stamp" else "") + "\n" + out.joinToString("\n"))
    }
    return capped(head, parts.ifEmpty { listOf(Wording.Report.nothingSaid) })
}
