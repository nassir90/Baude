package dev.baude.android

import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import java.util.Base64
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** A model the mesh serves: one of a member's providers that speaks OpenAI's API (Ollama's, an endpoint's), as its host
 * process says it (info's providers), run there through that host process's infer: the API proxy. member: the
 * machine's id in the mesh; provider: its id there (ollama, openai/<endpoint>); model: its name in the provider's API. */
data class Served(val member: String, val memberName: String, val provider: String, val providerName: String,
                  val model: String, val display: String) {
    val key: String get() = "$member/$provider"
    /** Its value as a host names another member's model (openai/<endpoint_id>/<model>), so Baude says what it is. */
    val value: String get() = "openai/${endpointId(member, provider)}/$model"

    fun json(): JSONObject = JSONObject().put("member", member).put("member_name", memberName).put("provider", provider)
        .put("provider_name", providerName).put("model", model).put("display", display)

    companion object {
        fun of(o: JSONObject) = Served(o.optString("member"), o.optString("member_name"), o.optString("provider"),
            o.optString("provider_name"), o.optString("model"), o.optString("display"))
    }
}

/** mesh.endpoint_id: another member's provider's id as an endpoint on a machine, the same every run. */
fun endpointId(member: String, provider: String): String {
    var slug = provider.lowercase().replace(Regex("[^a-z0-9_-]"), "-")
    if (slug.length > 17) slug = slug.take(10) + "-" + MessageDigest.getInstance("SHA-1").digest(provider.toByteArray())
        .joinToString("") { "%02x".format(it) }.take(6)
    return "mesh-${member.lowercase().replace(Regex("[^a-z0-9]"), "").take(8)}-$slug"
}

/** The models the mesh serves this phone's agents: those of every host and member that's connected now, and the
 * calls to them. */
object Inference {
    const val WAIT_MS = 600_000L // (mesh.INFER_S: a provider has this long to answer)

    /** Every model the mesh's connected machines serve over an API (tools: those that call tools), by machine. */
    fun found(): List<Served> {
        val out = ArrayList<Served>()
        val seen = HashSet<String>()
        for (c in Mesh.states.values.toList()) {
            if (c.state != "up" || c.place is Place.Phone) continue
            val info = c.info ?: continue
            val member = info.optString("id").takeIf(::isId) ?: continue
            if (!seen.add(member)) continue
            for (p in info.optJSONArray("providers").list()) {
                if (!p.optBoolean("http") && p.isNull("base_url")) continue
                for (m in p.optJSONArray("models").list()) {
                    if (!m.optBoolean("tools", true)) continue
                    val id = m.optString("id").ifBlank { m.optString("value") }
                    if (id.isBlank()) continue
                    out.add(Served(member, info.optString("name").ifBlank { member.take(8) }, p.optString("id"),
                        p.optString("name").ifBlank { p.optString("id") }, id, m.optString("displayName").ifBlank { id }))
                }
            }
        }
        return out
    }

    /** Those you've let serve this phone (Trust). */
    fun allowed(): List<Served> = found().filter { Trust.mayServe(it.member, it.provider) }

    /** An allowed model, by its value. */
    fun of(value: String): Served? = allowed().firstOrNull { it.value == value }

    fun display(s: PhoneSession): String = s.meta.optJSONObject("infer")?.let { Served.of(it) }?.let {
        "${it.display} · ${it.memberName}"
    } ?: s.meta.optString("model")

    /** The connection to a member's host process that's up now (a host of yours, or reached through one). */
    fun connTo(member: String): Connection? = Mesh.states.values.toList().firstOrNull {
        it.state == "up" && it.place !is Place.Phone && it.info?.optString("id") == member
    }

    class Failed(why: String) : Exception(why)

    /** A chat completion, asked of the member that serves the session's model (OpenAI's API: its answer whole), or
     * Failed saying why not; null if the turn's stopped first. */
    fun complete(s: PhoneSession, body: JSONObject): JSONObject? {
        val served = s.meta.optJSONObject("infer")?.let { Served.of(it) } ?: throw Failed("this agent has no model")
        if (!Trust.mayServe(served.member, served.provider))
            throw Failed("${served.providerName} on ${served.memberName} isn't allowed to serve this phone's agents now: " +
                "allow it on This phone")
        val conn = connTo(served.member) ?: throw Failed("${served.memberName} isn't connected now")
        val done = CountDownLatch(1)
        val data = ByteArrayOutputStream()
        var status = 0
        var error: String? = null
        conn.stream(JSONObject().put("op", "infer").put("provider", served.provider).put("method", "POST")
            .put("path", "/chat/completions").put("headers", JSONObject().put("content-type", "application/json")
                .put("accept", "application/json"))
            .put("body", Base64.getEncoder().encodeToString(body.toString().toByteArray()))) { m ->
            when (m.optString("op")) {
                "infer_head" -> status = m.optInt("status")
                "infer_data" -> synchronized(data) { data.write(Base64.getDecoder().decode(m.optString("data"))) }
                "infer_end" -> done.countDown()
                else -> {
                    error = m.optString("error", "the host said no")
                    done.countDown()
                }
            }
        }
        val until = System.currentTimeMillis() + WAIT_MS
        while (!done.await(250, TimeUnit.MILLISECONDS)) {
            if (s.cancel) return null
            if (System.currentTimeMillis() > until) throw Failed("${served.memberName} didn't answer in time")
        }
        error?.let { throw Failed(it) }
        val text = synchronized(data) { data.toString("UTF-8") }
        val answer = try {
            JSONObject(text)
        } catch (_: Exception) {
            throw Failed("${served.providerName} on ${served.memberName} said: ${text.take(300)} (status $status)")
        }
        if (status !in 200..299) throw Failed("${served.providerName} on ${served.memberName}: " +
            (answer.optJSONObject("error")?.optString("message") ?: answer.optString("error").ifBlank { "status $status" }))
        return answer
    }
}

/** An agent's turn on this phone: the model asked (through the mesh: Inference) with the conversation so far and
 * this phone's tools (PhoneTools), each call it makes asked of you first (PhoneHost.permit) and run here, till it
 * answers without one; what it says kept and said as claude says it, so heads show it as any agent's. */
object PhoneAgent {
    private const val MAX_STEPS = 40 // (model calls in one turn, at most)
    private const val STOPPED = "[Request interrupted by user]"

    private class Stopped : Exception()

    fun turn(s: PhoneSession) {
        val began = System.currentTimeMillis()
        var steps = 0
        var result = ""
        var error = false
        try {
            PhoneHost.tell(s, JSONObject().put("type", "system").put("subtype", "init").put("session_id", s.id)
                .put("model", s.meta.optString("model")).put("cwd", s.meta.optString("cwd"))
                .put("tools", JSONArray(PhoneTools.names())).put("permissionMode", "default"))
            while (true) {
                if (s.cancel) throw Stopped()
                if (++steps > MAX_STEPS) {
                    result = "Stopped after $MAX_STEPS model calls in one turn"
                    error = true
                    break
                }
                val body = JSONObject().put("model", (s.meta.optJSONObject("infer") ?: JSONObject()).optString("model"))
                    .put("messages", conversation(s)).put("tools", PhoneTools.schemas()).put("stream", false)
                val answer = Inference.complete(s, body) ?: throw Stopped()
                val choice = answer.optJSONArray("choices")?.optJSONObject(0)?.optJSONObject("message")
                    ?: throw Inference.Failed("the model's answer had no message")
                val text = if (choice.isNull("content")) "" else choice.optString("content").trim()
                val calls = choice.optJSONArray("tool_calls").list()
                val blocks = JSONArray()
                if (text.isNotEmpty()) blocks.put(JSONObject().put("type", "text").put("text", text))
                val uses = calls.map { c ->
                    val f = c.optJSONObject("function") ?: JSONObject()
                    val args = f.optString("arguments").ifBlank { "{}" }
                    val input = try {
                        JSONObject(args)
                    } catch (_: Exception) {
                        JSONObject().put("_unparsed", args)
                    }
                    JSONObject().put("type", "tool_use").put("id", c.optString("id").ifBlank { "toolu_" + UUID.randomUUID() })
                        .put("name", f.optString("name")).put("input", input)
                }
                uses.forEach { blocks.put(it) }
                val usage = answer.optJSONObject("usage")
                PhoneHost.said(s, "assistant", JSONObject().put("id", answer.optString("id").ifBlank { "msg_" + UUID.randomUUID() })
                    .put("type", "message").put("role", "assistant").put("model", body.optString("model"))
                    .put("content", blocks).put("stop_reason", if (uses.isEmpty()) "end_turn" else "tool_use")
                    .put("usage", JSONObject().put("input_tokens", usage?.optInt("prompt_tokens") ?: 0)
                        .put("output_tokens", usage?.optInt("completion_tokens") ?: 0)))
                if (uses.isEmpty()) {
                    result = text
                    break
                }
                val results = JSONArray()
                var stopped = false
                for (use in uses) {
                    val id = use.getString("id")
                    if (stopped || s.cancel) {
                        stopped = true
                        results.put(toolResult(id, "The user stopped this turn before it ran.", true))
                        continue
                    }
                    val name = use.getString("name")
                    val input = use.getJSONObject("input")
                    if (name !in PhoneTools.names()) {
                        results.put(toolResult(id, "No such tool here: $name. The tools are ${PhoneTools.names().joinToString()}.", true))
                        continue
                    }
                    val answered = PhoneHost.permit(s, id, name, input)
                    if (answered == null) {
                        stopped = true
                        results.put(toolResult(id, "The user stopped this turn before it ran.", true))
                        continue
                    }
                    if (answered.optString("behavior") != "allow") {
                        results.put(toolResult(id, answered.optString("message", "The user denied this tool use."), true))
                        continue
                    }
                    val (out, bad) = try {
                        PhoneTools.run(s, name, answered.optJSONObject("updatedInput") ?: input)
                    } catch (e: Exception) {
                        "${e.javaClass.simpleName}: ${e.message}" to true
                    }
                    results.put(toolResult(id, out, bad))
                }
                PhoneHost.said(s, "user", JSONObject().put("role", "user").put("content", results))
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
            PhoneHost.ended(s, result, error, steps, System.currentTimeMillis() - began)
        }
    }

    private fun toolResult(id: String, text: String, error: Boolean) = JSONObject().put("type", "tool_result")
        .put("tool_use_id", id).put("content", text.take(PhoneTools.RESULT_MAX)).put("is_error", error)

    /** What the agent's told at each turn, before the conversation: where it is, what it can do, who it is (a Bot:
     * its memory, as baudelib's bot_system tells a host's Bot). */
    private fun system(s: PhoneSession): String {
        val bot = s.meta.optJSONObject("bot")
        val memory = File(s.folder, PhoneHost.MEMORY).takeIf { it.exists() }?.readText()?.trim().orEmpty()
        val head = (if (bot != null) "You are ${bot.optString("name")}, a Bot: an agent with a name and a memory that " +
            "last. " else "You are an agent. ") +
            "You run on ${PhoneHost.name}, an Android phone in the user's Baude mesh of machines; your model runs on " +
            "another machine of the mesh. You have no shell. Your tools: files in your own folder on the phone (Read, " +
            "Write, Edit, Glob; paths are relative to it), questions to the user (AskUserQuestion), and the mesh's " +
            "hosts and their chats (mcp__mesh__*). The user is asked before each tool runs, and may say no.\n" +
            "Your folder: ${s.folder.path}"
        if (bot == null) return head
        return head + "\n\nYour memory is the Markdown file ${PhoneHost.MEMORY} in your folder. It is yours to keep: " +
            "edit it with your file tools when you learn something worth keeping, and take out what's no longer " +
            "true. Keep it short and current.\n\n" +
            (if (memory.isNotEmpty()) "Your memory as this turn starts:\n<memory>\n${memory.take(32_000)}\n</memory>"
            else "Your memory is empty so far.")
    }

    /** Its transcript as OpenAI's chat messages: what you said (pictures as images; a PDF named, which isn't read
     * here), what it said and the tools it called, and each call's result. */
    fun conversation(s: PhoneSession): JSONArray {
        val out = JSONArray().put(JSONObject().put("role", "system").put("content", system(s)))
        val f = PhoneHost.transcript(s)
        if (!f.exists()) return out
        val open = ArrayList<String>() // (calls made with no result kept: the app stopped meanwhile, say)
        fun unanswered() {
            for (id in open) out.put(JSONObject().put("role", "tool").put("tool_call_id", id)
                .put("content", "It didn't run: the app stopped first."))
            open.clear()
        }
        for (row in f.readLines()) {
            val o = try {
                JSONObject(row)
            } catch (_: Exception) {
                continue
            }
            if (o.optBoolean("isApiErrorMessage")) continue
            val msg = o.optJSONObject("message") ?: continue
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
                    unanswered()
                    out.put(m)
                    for (i in 0 until calls.length()) open.add(calls.getJSONObject(i).getString("id"))
                }
                "user" -> {
                    for (b in blocks.filter { it.optString("type") == "tool_result" }) {
                        val c = b.opt("content")
                        val text = if (c is JSONArray) c.list().joinToString("\n") { it.optString("text") } else c?.toString().orEmpty()
                        out.put(JSONObject().put("role", "tool").put("tool_call_id", b.optString("tool_use_id")).put("content", text))
                        open.remove(b.optString("tool_use_id"))
                    }
                    unanswered()
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
                    if (parts.length() == 0) continue
                    val plain = parts.list().all { it.optString("type") == "text" }
                    out.put(JSONObject().put("role", "user").put("content",
                        if (plain) parts.list().joinToString("\n\n") { it.optString("text") } else parts))
                }
            }
        }
        unanswered()
        return out
    }
}
