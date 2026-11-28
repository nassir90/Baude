package dev.baude.android

import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import java.util.Base64
import java.util.TreeMap
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** A model the mesh serves: one of a member's providers that speaks OpenAI's API (Ollama's, an endpoint's), as its host
 * process says it (info's providers), run there through that host process's infer: the API proxy. member: the
 * machine's id in the mesh; provider: its id there (ollama, openai/<endpoint>); model: its name in the provider's API. */
data class Served(val member: String, val memberName: String, val provider: String, val providerName: String,
                  val model: String, val display: String, val window: Int = WINDOW) {
    val key: String get() = "$member/$provider"
    /** Its value as a host names another member's model (openai/<endpoint_id>/<model>), so Baude says what it is. */
    val value: String get() = "openai/${endpointId(member, provider)}/$model"

    fun json(): JSONObject = JSONObject().put("member", member).put("member_name", memberName).put("provider", provider)
        .put("provider_name", providerName).put("model", model).put("display", display).put("window", window)

    companion object {
        fun of(o: JSONObject) = Served(o.optString("member"), o.optString("member_name"), o.optString("provider"),
            o.optString("provider_name"), o.optString("model"), o.optString("display"), o.optInt("window", WINDOW))

        const val WINDOW = WireConstants.DEFAULT_WINDOW // (a model's context, where its host says none)
    }
}

/** mesh.endpoint_id: another member's provider's id as an endpoint on a machine, the same every run. */
fun endpointId(member: String, provider: String): String {
    var slug = provider.lowercase().replace(Regex("[^a-z0-9_-]"), "-")
    if (slug.length > 17) slug = slug.take(10) + "-" + MessageDigest.getInstance("SHA-1").digest(provider.toByteArray())
        .joinToString("") { "%02x".format(it) }.take(6)
    return "mesh-${member.lowercase().replace(Regex("[^a-z0-9]"), "").take(8)}-$slug"
}

/** The models the mesh serves this phone's agents: those the machines this phone reaches serve over an API, as any
 * member may use the mesh's inference, and the calls to them. */
object Inference {
    const val WAIT_MS = WireConstants.INFER_S * 1000L // (mesh.INFER_S: a provider has this long to say something, each time)

    /** Every model those machines serve over an API (and that call tools), by machine. */
    fun found(): List<Served> {
        val out = ArrayList<Served>()
        val seen = HashSet<String>()
        for (c in Mesh.states.values.toList()) {
            if (c.state != "up" || c.place is Place.Phone) continue
            val info = c.info ?: continue
            val member = info.id?.takeIf(::isId) ?: continue
            if (!seen.add(member)) continue
            for (p in info.providers.orEmpty()) {
                if (p.http != true && p.baseUrl == null) continue
                for (m in p.models.orEmpty()) {
                    if (m.tools == false) continue
                    val id = m.id.orEmpty().ifBlank { m.value.orEmpty() }
                    if (id.isBlank()) continue
                    out.add(Served(member, info.name.orEmpty().ifBlank { member.take(8) }, p.id.orEmpty(),
                        p.name.orEmpty().ifBlank { p.id.orEmpty() }, id, m.displayName.orEmpty().ifBlank { id },
                        m.window?.toInt()?.takeIf { it > 0 } ?: Served.WINDOW))
                }
            }
        }
        return out
    }

    /** A model the mesh serves, by its value. */
    fun of(value: String): Served? = found().firstOrNull { it.value == value }

    fun display(s: PhoneSession): String = s.meta.optJSONObject("infer")?.let { Served.of(it) }?.let {
        "${it.display} · ${it.memberName}"
    } ?: s.meta.optString("model")

    /** The connection to a member's host process that's up now (a host of yours, or reached through one). */
    fun connTo(member: String): Connection? = Mesh.states.values.toList().firstOrNull {
        it.state == "up" && it.place !is Place.Phone && it.info?.id.orEmpty() == member
    }

    class Failed(why: String) : Exception(why)

    /** What a model call came back with: its text, its tool calls ({id, name, arguments: as the model wrote them}),
     * why it stopped (finish_reason), its id and usage. */
    class Answer(val id: String, val text: String, val calls: List<JSONObject>, val finish: String?, val usage: JSONObject?)

    /** A chat completion, streamed, asked of the member that serves the session's model on a channel of its own (a
     * connect to its host process, or a relay to it through a host of yours): text(delta) as it comes. Stopping the
     * turn closes the channel, so the host lets go of the provider, which stops. Failed saying why; null if it's
     * stopped. */
    fun complete(s: PhoneSession, body: JSONObject, text: (String) -> Unit): Answer? {
        val served = s.meta.optJSONObject("infer")?.let { Served.of(it) } ?: throw Failed("this chat has no model")
        val conn = connTo(served.member) ?: throw Failed("${served.memberName} isn't connected now")
        val base = if (conn.place is Place.Direct) conn else conn.via ?: throw Failed("${served.memberName} isn't reached now")
        val ch = base.exec() ?: throw Failed("${base.host?.label ?: "its host"} isn't connected now")
        val done = CountDownLatch(1)
        var error: String? = null
        var heard = System.currentTimeMillis()
        val sse = Sse(text)
        val line = WireLine(ch.inputStream, ch.outputStream) { ch.disconnect() }
        s.stopModel = { line.shut() }
        val infer = Msg.Infer(provider = served.provider, method = "POST", path = "/chat/completions",
            headers = mapOf("content-type" to "application/json", "accept" to "text/event-stream"),
            body = Base64.getEncoder().encodeToString(body.put("stream", true).toString().toByteArray()), req = 2).json()
        thread(name = "infer", isDaemon = true) {
            try {
                ch.connect(15_000)
                line.send(if (conn.place is Place.Member) Msg.Relay(to = served.member, req = 0).json()
                    else Msg.Hello(client = "baude-android", req = 1).json())
                line.run { m ->
                    heard = System.currentTimeMillis()
                    when (m.optString("op")) {
                        Op.RELAYED -> line.send(Msg.Hello(client = "baude-android", req = 1).json())
                        Op.HELLO -> line.send(infer)
                        Op.INFER_HEAD -> Msg.InferHead.from(m).let { h ->
                            sse.head(h.status?.toInt() ?: 0, h.headers?.optString("Content-Type").orEmpty()) }
                        Op.INFER_DATA -> sse.feed(Base64.getDecoder().decode(Msg.InferData.from(m).data.orEmpty()))
                        Op.INFER_END -> line.shut()
                        Op.ERROR -> {
                            error = Msg.Error.from(m).said ?: Wording.BotBrowser.hostSaidNo
                            line.shut()
                        }
                    }
                }
            } catch (e: Exception) {
                if (error == null) error = e.message ?: e.javaClass.simpleName
            } finally {
                ch.disconnect()
                done.countDown()
            }
        }
        try {
            while (!done.await(250, TimeUnit.MILLISECONDS)) {
                if (s.cancel) return null
                if (System.currentTimeMillis() - heard > WAIT_MS) throw Failed("${served.memberName} said nothing for " +
                    "${WAIT_MS / 1000} s")
            }
        } finally {
            line.shut()
            s.stopModel = null
        }
        if (s.cancel) return null
        error?.let { throw Failed("${served.providerName} on ${served.memberName}: $it") }
        return sse.answer("${served.providerName} on ${served.memberName}")
    }

    /** A streamed answer read as it comes (OpenAI's server-sent events: data: {choices: [{delta, finish_reason}]}), or
     * one that came whole (a provider that doesn't stream, or an error's). */
    private class Sse(private val text: (String) -> Unit) {
        private var status = 0
        private var streamed = false
        private val pending = ByteArrayOutputStream()
        private val whole = ByteArrayOutputStream()
        private val said = StringBuilder()
        private val calls = TreeMap<Int, JSONObject>()
        private var finish: String? = null
        private var id = ""
        private var usage: JSONObject? = null

        fun head(status: Int, type: String) {
            this.status = status
            streamed = status in 200..299 && type.contains("event-stream")
        }

        fun feed(data: ByteArray) {
            if (!streamed) return whole.write(data)
            pending.write(data)
            var bytes = pending.toByteArray()
            var at = bytes.indexOf(NL)
            while (at >= 0) {
                event(String(bytes, 0, at, Charsets.UTF_8).trim())
                bytes = bytes.copyOfRange(at + 1, bytes.size)
                at = bytes.indexOf(NL)
            }
            pending.reset()
            pending.write(bytes)
        }

        private fun event(line: String) {
            if (!line.startsWith("data:")) return
            val data = line.removePrefix("data:").trim()
            if (data == "[DONE]" || data.isEmpty()) return
            val o = try {
                JSONObject(data)
            } catch (_: Exception) {
                return
            }
            o.optString("id").takeIf { it.isNotBlank() }?.let { id = it }
            o.optJSONObject("usage")?.let { usage = it }
            o.optJSONObject("error")?.let { throw Failed(it.optString("message", it.toString())) }
            val choice = o.optJSONArray("choices")?.optJSONObject(0) ?: return
            if (!choice.isNull("finish_reason")) finish = choice.optString("finish_reason")
            val delta = choice.optJSONObject("delta") ?: return
            if (!delta.isNull("content")) delta.optString("content").takeIf { it.isNotEmpty() }?.let {
                said.append(it)
                text(it)
            }
            for (t in delta.optJSONArray("tool_calls").list()) {
                val c = calls.getOrPut(t.optInt("index", calls.size)) { JSONObject().put("id", "").put("name", "").put("arguments", "") }
                t.optString("id").takeIf { it.isNotBlank() }?.let { c.put("id", it) }
                val f = t.optJSONObject("function") ?: continue
                if (!f.isNull("name")) c.put("name", c.optString("name") + f.optString("name"))
                if (!f.isNull("arguments")) c.put("arguments", c.optString("arguments") + f.optString("arguments"))
            }
        }

        fun answer(who: String): Answer {
            if (streamed) {
                if (pending.size() > 0) event(pending.toString("UTF-8").trim())
                return Answer(id, said.toString().trim(), calls.values.toList(), finish, usage)
            }
            val body = whole.toString("UTF-8")
            val o = try {
                JSONObject(body)
            } catch (_: Exception) {
                throw Failed("$who said: ${body.take(300)} (status $status)")
            }
            if (status !in 200..299) throw Failed("$who: " +
                (o.optJSONObject("error")?.optString("message") ?: o.optString("error").ifBlank { "status $status" }))
            val choice = o.optJSONArray("choices")?.optJSONObject(0) ?: throw Failed("$who's answer had no message")
            val m = choice.optJSONObject("message") ?: JSONObject()
            val got = if (m.isNull("content")) "" else m.optString("content").trim()
            if (got.isNotEmpty()) text(got)
            return Answer(o.optString("id"), got, m.optJSONArray("tool_calls").list().map { c ->
                val f = c.optJSONObject("function") ?: JSONObject()
                JSONObject().put("id", c.optString("id")).put("name", f.optString("name")).put("arguments", f.optString("arguments"))
            }, if (choice.isNull("finish_reason")) null else choice.optString("finish_reason"), o.optJSONObject("usage"))
        }

        private fun ByteArray.indexOf(b: Byte): Int {
            for (i in indices) if (this[i] == b) return i
            return -1
        }
    }

    private const val NL: Byte = 10
}

/** An agent's turn on this phone: the model asked (through the mesh: Inference, its words said as they come) with the
 * conversation so far and this phone's tools (PhoneTools), each call it makes run here as its chat's permission mode
 * has it (PhoneHost.permit: unasked, asked of you, or refused), till it answers without one; what it says kept and said as claude says it, so heads show it as any
 * agent's. */
object PhoneAgent {
    /** Tool calls in one turn, at most; then the model's asked to answer in words. In Manual each edit's asked of
     * you, so a turn that needs more is one to split; and it bounds what a model stuck in a loop costs the machine that serves it. */
    private const val MAX_CALLS = WireConstants.AGENT_CALLS_MAX
    const val STOPPED = "[Request interrupted by user]"
    private const val COMPACT_AT = 0.8 // (of its model's context: the conversation compacted, as claude's auto-compact)
    private const val COMPACT_ASK = "Your task is to create a detailed summary of the conversation so far, paying " +
        "close attention to the user's explicit requests and your previous actions. Include: the user's requests and " +
        "intents; the key facts and decisions; the files in your folder you read, wrote or changed, and why; errors and " +
        "how they were fixed; what's pending; and the work in hand just before this summary, with the user's last " +
        "message, so the work can carry on from the summary alone. Write only the summary."

    private class Stopped : Exception()

    fun turn(s: PhoneSession) {
        val began = System.currentTimeMillis()
        var steps = 0
        val tokens = LongArray(4) // (the turn's, every answer's added up: its telemetry's, as PhoneHost.ended takes them)
        var calls = 0
        var result = ""
        var error = false
        try {
            PhoneHost.tell(s, JSONObject().put("type", "system").put("subtype", "init").put("session_id", s.id)
                .put("model", s.meta.optString("model")).put("cwd", s.folder.path)
                .put("tools", JSONArray(PhoneTools.names(s))).put("permissionMode", PhoneHost.mode(s)))
            var compacted = false
            while (true) {
                if (s.cancel) throw Stopped()
                steps++
                val last = calls >= MAX_CALLS // (no more calls: answered in words)
                val told = system(s)
                var convo = PhoneHost.conversation(s)
                val window = (s.meta.optJSONObject("infer")?.let { Served.of(it) }?.window ?: Served.WINDOW)
                val size = (told.length + convo.toString().length) / 4 // (tokens, about: four characters each)
                if (!compacted && size > window * COMPACT_AT && convo.length() > 2) { // (once a turn at most)
                    compacted = true
                    if (compact(s, told, convo, size)) convo = PhoneHost.conversation(s)
                    if (s.cancel) throw Stopped()
                }
                val messages = JSONArray().put(JSONObject().put("role", "system").put("content", told))
                convo.list().forEach { messages.put(it) }
                if (last) messages.put(JSONObject().put("role", "user").put("content", "(You've made $MAX_CALLS tool " +
                    "calls in this turn, the most one turn on this phone may. Answer now, in words, without tools: say " +
                    "what you've done and what's left.)"))
                val model = (s.meta.optJSONObject("infer") ?: JSONObject()).optString("model")
                val body = JSONObject().put("model", model).put("messages", messages).put("tools", PhoneTools.schemas(s))
                    .put("stream_options", JSONObject().put("include_usage", true))
                if (last) body.put("tool_choice", "none")
                val mid = "msg_" + UUID.randomUUID()
                var streaming = false
                val answer = Inference.complete(s, body) { delta ->
                    if (!streaming) {
                        streaming = true
                        event(s, JSONObject().put("type", "message_start").put("message", JSONObject().put("id", mid)
                            .put("type", "message").put("role", "assistant").put("model", model).put("content", JSONArray())))
                        event(s, JSONObject().put("type", "content_block_start").put("index", 0)
                            .put("content_block", JSONObject().put("type", "text").put("text", "")))
                    }
                    event(s, JSONObject().put("type", "content_block_delta").put("index", 0)
                        .put("delta", JSONObject().put("type", "text_delta").put("text", delta)))
                } ?: throw Stopped()
                if (streaming) {
                    event(s, JSONObject().put("type", "content_block_stop").put("index", 0))
                    event(s, JSONObject().put("type", "message_stop"))
                }
                val made = if (last) emptyList() else answer.calls
                val blocks = JSONArray()
                if (answer.text.isNotEmpty()) blocks.put(JSONObject().put("type", "text").put("text", answer.text))
                val uses = made.map { c ->
                    val input = try {
                        JSONObject(c.optString("arguments").ifBlank { "{}" })
                    } catch (_: Exception) {
                        null
                    }
                    JSONObject().put("type", "tool_use").put("id", c.optString("id").ifBlank { "toolu_" + UUID.randomUUID() })
                        .put("name", c.optString("name")).put("input", input ?: JSONObject())
                        .also { if (input == null) it.put("_cut", c.optString("arguments").take(200)) }
                }
                uses.forEach { blocks.put(JSONObject(it.toString()).apply { remove("_cut") }) }
                val usage = answer.usage
                val prompt = (usage?.optLong("prompt_tokens") ?: 0).coerceAtLeast(0)
                val cached = (usage?.optJSONObject("prompt_tokens_details")?.optLong("cached_tokens") ?: 0).coerceIn(0, prompt)
                tokens[0] += prompt - cached // (prompt_tokens has those read from the server's cache in it: apart here)
                tokens[2] += cached
                tokens[3] += (usage?.optLong("completion_tokens") ?: 0).coerceAtLeast(0)
                PhoneHost.said(s, "assistant", JSONObject().put("id", if (streaming) mid else answer.id.ifBlank { mid })
                    .put("type", "message").put("role", "assistant").put("model", model)
                    .put("content", blocks).put("stop_reason", if (uses.isEmpty()) "end_turn" else "tool_use")
                    .put("usage", JSONObject().put("input_tokens", prompt - cached).put("cache_read_input_tokens", cached)
                        .put("output_tokens", usage?.optInt("completion_tokens") ?: 0)))
                if (uses.isEmpty()) {
                    result = answer.text.ifEmpty { if (last) "Stopped after $MAX_CALLS tool calls" else "" }
                    if (answer.finish == "length") result += if (result.isEmpty()) "(cut off: the model's length limit)" else " (cut off)"
                    break
                }
                var stopped = false
                // (each call's result kept as it's had: one the app's stopped after isn't said not to have run)
                fun answered(id: String, text: String, error: Boolean) = PhoneHost.said(s, "user",
                    JSONObject().put("role", "user").put("content", JSONArray().put(toolResult(id, text, error))))
                for (use in uses) {
                    val id = use.getString("id")
                    val name = use.getString("name")
                    when {
                        stopped || s.cancel -> {
                            stopped = true
                            answered(id, "The user stopped this turn before it ran.", true)
                        }
                        calls >= MAX_CALLS -> answered(id, "Not run: this turn's made its $MAX_CALLS tool calls, the most " +
                            "one turn on this phone may.", true)
                        use.has("_cut") -> answered(id, "This call's arguments weren't whole JSON" +
                            (if (answer.finish == "length") " (your reply reached its length limit)" else "") +
                            ", so it didn't run: call it again, with less in it.", true)
                        name !in PhoneTools.names(s) -> answered(id, "No such tool here: $name. The tools " +
                            "are ${PhoneTools.names(s).joinToString()}.", true)
                        else -> {
                            calls++
                            val input = use.getJSONObject("input")
                            val allowed = PhoneHost.permit(s, id, name, input)
                            when {
                                allowed == null -> {
                                    stopped = true
                                    answered(id, "The user stopped this turn before it ran.", true)
                                }
                                allowed.optString("behavior") != "allow" ->
                                    answered(id, allowed.optString("message", "The user denied this tool use."), true)
                                else -> {
                                    val (out, bad) = try {
                                        PhoneTools.run(s, name, allowed.optJSONObject("updatedInput") ?: input)
                                    } catch (e: Exception) {
                                        "${e.javaClass.simpleName}: ${e.message}" to true
                                    }
                                    answered(id, out, bad)
                                }
                            }
                        }
                    }
                }
                if (stopped) throw Stopped()
            }
        } catch (_: Stopped) {
            PhoneHost.said(s, "user", JSONObject().put("role", "user").put("content",
                JSONArray().put(JSONObject().put("type", "text").put("text", STOPPED))))
            result = ""
        } catch (e: Exception) {
            val why = if (e is Inference.Failed) e.message ?: "it failed" else "${e.javaClass.simpleName}: ${e.message}"
            PhoneHost.said(s, "assistant", JSONObject().put("id", "msg_" + UUID.randomUUID()).put("type", "message")
                .put("role", "assistant").put("model", "<synthetic>").put("content", JSONArray()
                    .put(JSONObject().put("type", "text").put("text", "API Error: $why"))), apiError = true)
            result = why
            error = true
        } finally {
            PhoneHost.ended(s, result, error, steps, System.currentTimeMillis() - began, tokens)
        }
    }

    /** The conversation compacted, as claude compacts one near its model's context: the model asked to summarise it
     * (said as claude says it, compacting), and from then on given the summary in its place. Whether it was. */
    private fun compact(s: PhoneSession, told: String, convo: JSONArray, size: Int): Boolean {
        PhoneHost.tell(s, JSONObject().put("type", "system").put("subtype", "status").put("status", "compacting")
            .put("session_id", s.id))
        val messages = JSONArray().put(JSONObject().put("role", "system").put("content", told))
        convo.list().forEach { messages.put(it) }
        messages.put(JSONObject().put("role", "user").put("content", COMPACT_ASK))
        val body = JSONObject().put("model", (s.meta.optJSONObject("infer") ?: JSONObject()).optString("model"))
            .put("messages", messages).put("tools", PhoneTools.schemas(s)).put("tool_choice", "none")
        val why = try {
            val answer = Inference.complete(s, body) {} ?: return false
            if (answer.text.isNotBlank()) {
                PhoneHost.compacted(s, "This session is being continued from a previous conversation that ran out of " +
                    "context. The summary below covers the earlier portion of the conversation.\n\n" + answer.text, size)
                return true
            }
            "the model wrote no summary"
        } catch (e: Exception) {
            e.message ?: e.javaClass.simpleName
        }
        PhoneHost.tell(s, JSONObject().put("type", "system").put("subtype", "status").put("status", JSONObject.NULL)
            .put("compact_result", "failed").put("compact_error", why).put("session_id", s.id))
        return false
    }

    private fun event(s: PhoneSession, e: JSONObject) = PhoneHost.tell(s, JSONObject().put("type", "stream_event")
        .put("event", e).put("session_id", s.id).put("parent_tool_use_id", JSONObject.NULL).put("uuid", UUID.randomUUID().toString()))

    private fun toolResult(id: String, text: String, error: Boolean) = JSONObject().put("type", "tool_result")
        .put("tool_use_id", id).put("content", text.take(PhoneTools.RESULT_MAX)).put("is_error", error)

    /** What the agent's told at each turn, before the conversation: where it is, what it can do, who it is (a Bot:
     * its memory, as baudelib's bot_system tells a host's Bot). */
    private fun system(s: PhoneSession): String {
        val bot = s.meta.optJSONObject("bot")
        val memory = File(s.folder, PhoneHost.MEMORY).takeIf { it.exists() }?.readText()?.trim().orEmpty()
        val head = (if (bot != null) "You are ${bot.optString("name")}, a Bot: a chat the user keeps coming back to, " +
            "with a name and a memory that last. " else "You are the assistant in a chat the user has started. ") +
            "You run on ${PhoneHost.name}, an Android phone in the user's Baude mesh of machines; your model runs on " +
            "another machine of the mesh. You have no shell. Your tools: files in your own folder on the phone (Read, " +
            "Write, Edit, Glob; paths are relative to it), questions to the user (AskUserQuestion)" +
            (if (bot != null) ", and the mesh's hosts and their chats (mcp__mesh__*)" else "") +
            ". As this chat's permission mode has it, the user may be asked " +
            "before a tool runs, and may say no." + (if (PhoneHost.mode(s) == "plan") " It's plan mode: look, then " +
            "put your plan to the user with ExitPlanMode; change nothing till they agree." else "") + "\n" +
            "Your folder: ${s.folder.path}"
        if (bot == null) return head
        return head + "\n\nYour memory is the Markdown file ${PhoneHost.MEMORY} in your folder. It is yours to keep: " +
            "edit it with your file tools when you learn something worth keeping, and take out what's no longer " +
            "true. Keep it short and current.\n\n" +
            (if (memory.isNotEmpty()) "Your memory as this turn starts:\n<memory>\n${memory.take(32_000)}\n</memory>"
            else "Your memory is empty so far.")
    }

    /** A transcript, as OpenAI's chat messages (read once: then add keeps them as it grows). */
    fun read(f: File): JSONArray {
        val out = JSONArray()
        if (!f.exists()) return out
        f.forEachLine { row ->
            try {
                add(out, JSONObject(row))
            } catch (_: Exception) {
            }
        }
        return out
    }

    /** A transcript's record added to the conversation, as OpenAI's chat messages: what you said (pictures as images;
     * a PDF named, which isn't read here), what it said and the tools it called, and each call's result. */
    fun add(out: JSONArray, o: JSONObject) {
        // (compacted: what was before is its summary from now on, the record after this)
        if (o.optString("type") == "system" && o.optString("subtype") == "compact_boundary") {
            while (out.length() > 0) out.remove(out.length() - 1)
            return
        }
        if (o.optBoolean("isApiErrorMessage")) return
        val msg = o.optJSONObject("message") ?: return
        val blocks = when (val c = msg.opt("content")) {
            is JSONArray -> c.list()
            is String -> listOf(JSONObject().put("type", "text").put("text", c))
            else -> emptyList()
        }
        when (o.optString("type")) {
            "assistant" -> {
                val text = blocks.filter { it.optString("type") == "text" }.joinToString("\n") { it.optString("text") }
                val calls = JSONArray()
                for (b in blocks.filter { it.optString("type") == "tool_use" }) calls.put(JSONObject()
                    .put("id", b.optString("id")).put("type", "function").put("function", JSONObject()
                        .put("name", b.optString("name")).put("arguments", (b.optJSONObject("input") ?: JSONObject()).toString())))
                val m = JSONObject().put("role", "assistant").put("content", if (text.isEmpty() && calls.length() > 0) JSONObject.NULL else text)
                if (calls.length() > 0) m.put("tool_calls", calls)
                out.put(m)
            }
            "user" -> {
                for (b in blocks.filter { it.optString("type") == "tool_result" }) {
                    val c = b.opt("content")
                    val text = if (c is JSONArray) c.list().joinToString("\n") { it.optString("text") } else c?.toString().orEmpty()
                    out.put(JSONObject().put("role", "tool").put("tool_call_id", b.optString("tool_use_id")).put("content", text))
                }
                val parts = JSONArray()
                for (b in blocks) when (b.optString("type")) {
                    "text" -> parts.put(JSONObject().put("type", "text").put("text", b.optString("text")))
                    "image" -> b.optJSONObject("source")?.let { src ->
                        parts.put(JSONObject().put("type", "image_url").put("image_url", JSONObject()
                            .put("url", "data:${src.optString("media_type")};base64,${src.optString("data")}")))
                    }
                    "document" -> parts.put(JSONObject().put("type", "text")
                        .put("text", "(A PDF was attached, ${b.optString("title", "untitled")}: it can't be read here.)"))
                }
                if (parts.length() == 0) return
                val plain = parts.list().all { it.optString("type") == "text" }
                out.put(JSONObject().put("role", "user").put("content",
                    if (plain) parts.list().joinToString("\n\n") { it.optString("text") } else parts))
            }
        }
    }
}
