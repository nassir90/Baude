package dev.baude.android

import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.URLEncoder
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/** What a Bot's browser tools ask of its browser here: emission.py's Control, op for op, its answers the same (the
 * same ops and arguments, the same fields back), so a Bot's tools work unchanged with its browser on the phone. Input's
 * real (a mouse moved to a place, then pressed; keys one at a time), as Android delivers a mouse's and a keyboard's; a
 * page's text comes from the page itself through emission.py's own PAGE_JS (built in as an asset), and what's on it is
 * named by the refs read_page and find give. Where the phone can't do as a desktop does, the answer says so. */
class Control(private val b: Browser, private val pageJs: String, private val replRun: String, val pointer: BotPointer,
              private val drawn: () -> Unit) {
    private val main = Handler(Looper.getMainLooper())
    private val scales = HashMap<String, Double>() // (each tab's last screenshot's pixels to the page's)
    private val reads = HashMap<String, Pair<List<Any>, String>>() // (each tab's last read_page: what was asked, its hash)
    private val queue = ArrayList<Triple<(JSONObject, (JSONObject) -> Unit) -> Unit, JSONObject, (JSONObject) -> Unit>>()

    private fun error(why: String) = JSONObject().put("error", why)

    // (tabs)

    private fun tab(msg: JSONObject): Tab {
        val want = msg.optString("tab").takeIf { msg.has("tab") && !msg.isNull("tab") && it.isNotEmpty() }
        if (want != null) {
            val t = b.tabs[want] ?: throw IllegalArgumentException("no tab $want: tabs_context lists them")
            b.select(t)
            return t
        }
        return b.current ?: b.open("about:blank")
    }

    fun describe(t: Tab): JSONObject = JSONObject().put("tab", t.id).put("title", t.title).put("url", t.url)
        .put("loading", t.loading).put("selected", t === b.current).apply {
            val got = b.prompts(t)
            if (got.isNotEmpty()) put("prompts", JSONArray(got))
        }

    private fun merged(a: JSONObject, extra: JSONObject?): JSONObject {
        extra?.keys()?.forEach { a.put(it, extra.get(it)) }
        return a
    }

    /** answer(describe(t) + extra) once t's page has loaded (or LOAD_S has gone), after a moment to start. */
    private fun settle(t: Tab, answer: (JSONObject) -> Unit, extra: JSONObject? = null, started: Long = SystemClock.uptimeMillis()) {
        main.postDelayed(object : Runnable {
            override fun run() {
                val waited = (SystemClock.uptimeMillis() - started) / 1000.0
                if (!t.closed && (waited < 0.35 || (t.loading && waited < LOAD_S && b.prompts(t).isEmpty()))) {
                    main.postDelayed(this, 100)
                    return
                }
                if (t.closed) return answer(error("tab ${t.id} has closed: tabs_context lists the open ones"))
                answer(merged(describe(t), extra))
            }
        }, 100)
    }

    private val random = java.security.SecureRandom()
    private fun nonce() = ByteArray(12).also { random.nextBytes(it) }.joinToString("") { "%02x".format(it) }

    init {
        for (a in listOf(EM_MADE, EM_BOUND, EM_REF)) check(a in pageJs) { "PAGE_JS has changed: $a" }
    }

    /** PAGE_JS, its em kept in t's page under its key (made for each page it loads: not one a page's scripts know
     * ahead; not enumerable, not to be replaced or changed once it's there), its refs the phone's own (ref_p12: not a
     * desktop's ref_12), with what's done by the page's natives as they were when it was first put in: a hit test, a
     * promise's answer kept for this process to take (each carrying the page's secret, so one em didn't keep is
     * seen), JSON.stringify; seen and typed, what an op keeps in it between scripts. */
    private fun em(t: Tab): String = "const K = '${t.key}', S = '${t.secret}';\n" +
        pageJs.replace(EM_REF, "id = 'ref_p' + (++n)")
            .replace(EM_MADE, "if (!Object.getOwnPropertyDescriptor(globalThis, K)) globalThis[K] = ")
            .replace(EM_BOUND, "\n" + SEAL + "\nconst em = globalThis[K];")

    /** What's put in a page as it starts loading (Browser's): em made then, before most of its scripts have run. */
    val early: (Tab) -> String = { t -> "(() => { ${em(t)}\n})();" }

    /** body (an async function's) run in t's page, em there (page: not in its scope, for javascript): then(its result)
     * given what it returned, or the error answered (or given to failed, if there's one). Its answer's kept by em,
     * under an id of this call's, till it's taken. */
    private fun js(t: Tab, body: String, answer: (JSONObject) -> Unit, then: ((Any?) -> Unit)? = null, page: Boolean = false,
                   failed: ((String) -> Unit)? = null) {
        if (t.closed || t.id !in b.tabs) return answer(error("tab ${t.id} has closed: tabs_context lists the open ones"))
        val waiting = b.prompts(t).filter { it.optString("kind") in listOf("alert", "confirm", "prompt") }
        if (waiting.isNotEmpty()) return answer(error(
            "the page is waiting on its prompt, and runs nothing till it's answered (answer_prompt)").put("prompts", JSONArray(waiting)))
        val fail = failed ?: { why: String -> answer(error(why)) }
        val ok = then ?: { v: Any? -> answer(JSONObject().put("result", v ?: JSONObject.NULL)) }
        val id = nonce()
        val script = "(() => { const __em = (() => { ${em(t)}\nreturn em; })();" +
            " if (!__em || typeof __em.owns !== 'function' || !__em.owns('${t.secret}')) return 'not ours';" +
            " __em.settle((async () => {" + (if (page) "" else " const em = __em;") + "\n$body\n})(), '$id');" +
            " return true; })()"
        val key = t.key
        val secret = t.secret
        val loads = t.loads
        val started = SystemClock.uptimeMillis()
        t.web.evaluateJavascript(script) { began ->
            if (began == "\"not ours\"") return@evaluateJavascript fail("the page has something of its own where " +
                "Emission's script is kept, so nothing it answers can be trusted: reload it (navigate: reload), and try again")
            if (began != "true") return@evaluateJavascript fail("SyntaxError: the script couldn't be run")
            fun look() {
                if (t.closed) return fail("tab ${t.id} has closed: tabs_context lists the open ones")
                t.web.evaluateJavascript("(() => { const S = globalThis['$key']; return S && S.take ? S.take('$id') : null; })()") { got ->
                    val r = try {
                        if (got == null || got == "null") null else JSONObject(got)
                    } catch (_: Exception) {
                        null
                    }
                    val waited = SystemClock.uptimeMillis() - started
                    when {
                        r != null && r.optString("s") != secret -> fail("an answer came from the page that wasn't " +
                            "Emission's script's: reload the page (navigate: reload), and try again")
                        r?.has("ok") == true -> {
                            val text = r.optString("ok")
                            val value = try {
                                org.json.JSONTokener(text).nextValue()
                            } catch (_: Exception) {
                                text
                            }
                            try {
                                ok(if (value == JSONObject.NULL) null else value)
                            } catch (e: Exception) {
                                answer(error(said(e)))
                            }
                        }
                        r != null -> fail(r.optString("error"))
                        t.loads != loads && waited > 1000 -> fail("the page changed before it answered")
                        waited > JS_S * 1000 -> fail("the page didn't answer in ${JS_S.toInt()} s")
                        else -> main.postDelayed({ look() }, 15)
                    }
                }
            }
            look()
        }
    }

    private val INPUT = setOf("click", "drag", "hover", "type", "key", "scroll", "form_input", "navigate", "new_tab",
        "close_tab", "select_tab", "answer_prompt", "resize") // (what changes the page: done in the order it came)

    private val OPS: Map<String, (JSONObject, (JSONObject) -> Unit) -> Unit> = mapOf(
        "tabs" to ::opTabs, "select_tab" to ::opSelectTab, "new_tab" to ::opNewTab, "close_tab" to ::opCloseTab,
        "navigate" to ::opNavigate, "screenshot" to ::opScreenshot, "zoom" to ::opZoom, "read_page" to ::opReadPage,
        "find" to ::opFind, "get_text" to ::opGetText, "javascript" to ::opJavascript, "click" to ::opClick,
        "drag" to ::opDrag, "hover" to ::opHover, "type" to ::opType, "key" to ::opKey, "scroll" to ::opScroll,
        "console" to ::opConsole, "network" to ::opNetwork, "resize" to ::opResize, "form_input" to ::opFormInput,
        "prompts" to ::opPrompts, "answer_prompt" to ::opAnswerPrompt, "sites" to ::opSites)

    fun run(op: String, msg: JSONObject, answer: (JSONObject) -> Unit) {
        val fn = OPS[op] ?: return answer(error("no such request: $op"))
        if (op !in INPUT) {
            try {
                fn(msg, guarded(answer))
            } catch (e: Exception) {
                answer(error(said(e)))
            }
            return
        }
        queue += Triple(fn, msg, answer)
        if (queue.size == 1) nextInput()
    }

    private fun guarded(answer: (JSONObject) -> Unit): (JSONObject) -> Unit {
        var done = false
        return { r -> if (!done) { done = true; drawn(); answer(r) } }
    }

    /** The first of what's waiting done (the next once it's answered, whatever the answer). */
    private fun nextInput() {
        while (queue.isNotEmpty()) {
            val (fn, msg, answer) = queue[0]
            val done = guarded { reply ->
                answer(reply)
                if (queue.isNotEmpty() && queue[0].third === answer) {
                    queue.removeAt(0)
                    main.post { nextInput() }
                }
            }
            try {
                fn(msg, done)
                return
            } catch (e: Exception) {
                answer(error(said(e)))
                queue.removeAt(0)
            }
        }
    }

    private fun said(e: Exception) = if (e is IllegalArgumentException || e is IllegalStateException) e.message ?: "$e"
    else "${e.javaClass.simpleName}: ${e.message}"

    private fun opTabs(msg: JSONObject, answer: (JSONObject) -> Unit) =
        answer(JSONObject().put("profile", b.profile).put("tabs", JSONArray(b.tabs.values.map(::describe)))
            .put("downloads", JSONArray(b.downloads.takeLast(10))))

    private fun opSelectTab(msg: JSONObject, answer: (JSONObject) -> Unit) = answer(describe(tab(msg)))

    private fun opNewTab(msg: JSONObject, answer: (JSONObject) -> Unit) =
        settle(b.open(address(msg.optString("url").ifEmpty { "about:blank" })), answer)

    private fun opCloseTab(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        if (b.tabs.size == 1) b.open("about:blank")
        b.close(t)
        reads.remove(t.id)
        scales.remove(t.id)
        answer(JSONObject().put("closed", t.id).put("tabs", JSONArray(b.tabs.values.map(::describe))))
    }

    private fun opNavigate(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        when (val url = msg.optString("url")) {
            "back" -> t.web.goBack()
            "forward" -> t.web.goForward()
            "reload" -> t.web.reload()
            else -> t.web.loadUrl(address(url))
        }
        settle(t, answer)
    }

    // (looking)

    /** then(view) for t's page as it's shown (view: its size, CSS pixels, and k: the WebView's pixels to one). */
    private fun picture(t: Tab, answer: (JSONObject) -> Unit, then: (Double, Double, Float) -> Unit) {
        js(t, "return {w: innerWidth, h: innerHeight}", answer, { v ->
            val o = v as JSONObject
            val w = max(1.0, o.optDouble("w"))
            val h = max(1.0, o.optDouble("h"))
            main.postDelayed({
                if (t.web.width <= 0 || t.web.height <= 0) return@postDelayed answer(error("no screenshot: the page isn't laid out"))
                try {
                    then(w, h, (t.web.width / w).toFloat())
                } catch (e: Exception) {
                    answer(error("no screenshot: ${said(e)}"))
                }
            }, 120)
        })
    }

    private fun jpeg(bm: Bitmap, quality: Int): String {
        val out = ByteArrayOutputStream()
        bm.compress(Bitmap.CompressFormat.JPEG, quality, out)
        bm.recycle()
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    private fun opScreenshot(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        picture(t, answer) { w, h, _ ->
            val scale = min(1.0, SHOT_MAX / max(w, h))
            val pw = max(1, (w * scale).roundToInt())
            val ph = max(1, (h * scale).roundToInt())
            val image = jpeg(Input.picture(t.web, 0f, 0f, t.web.width.toFloat(), t.web.height.toFloat(), pw, ph), 82)
            scales[t.id] = scale
            answer(describe(t).put("image", image).put("mime", "image/jpeg").put("width", pw).put("height", ph)
                .put("note", "x and y for click, scroll and zoom are this picture's pixels"))
        }
    }

    /** A region of the last screenshot ([x0, y0, x1, y1], its pixels), at the page's own detail: what x and y are in
     * stays the screenshot's. */
    private fun opZoom(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val region = msg.optJSONArray("region")
        val r = (0 until (region?.length() ?: 0)).mapNotNull { region!!.opt(it) as? Number }.map { it.toDouble() }
        require(r.size == 4 && r[2] > r[0] && r[3] > r[1]) {
            "zoom needs a region: [x0, y0, x1, y1], the screenshot's pixels, x1 past x0, y1 past y0"
        }
        val scale = scales[t.id] ?: throw IllegalArgumentException("zoom is of a screenshot's region: take one first")
        picture(t, answer) { w, h, k ->
            val x0 = max(0.0, r[0] / scale)
            val y0 = max(0.0, r[1] / scale)
            val x1 = min(w, r[2] / scale)
            val y1 = min(h, r[3] / scale)
            if (x1 - x0 < 1 || y1 - y0 < 1) return@picture answer(error("that region is off the page (the screenshot is " +
                "${(w * scale).roundToInt()} by ${(h * scale).roundToInt()})"))
            val px = x0 * k
            val py = y0 * k
            val pw = (x1 - x0) * k
            val ph = (y1 - y0) * k
            val fit = min(1.0, ZOOM_MAX / max(pw, ph))
            val ow = max(1, (pw * fit).roundToInt())
            val oh = max(1, (ph * fit).roundToInt())
            answer(describe(t).put("image", jpeg(Input.picture(t.web, px.toFloat(), py.toFloat(), pw.toFloat(), ph.toFloat(), ow, oh), 90))
                .put("mime", "image/jpeg").put("width", ow).put("height", oh)
                .put("region", JSONArray(r.map { it.roundToInt() }))
                .put("note", "x and y for click and scroll are still the screenshot's pixels"))
        }
    }

    /** The page's elements near what's shown (all of ref's part, if a ref's given); or, if nothing's changed since this
     * tab's last read (its address, and what it gave), a line saying so: once, the next read giving them again. */
    private fun opReadPage(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val ref = msg.optString("ref").takeIf { it.isNotEmpty() }
        val root = if (ref != null) "em.get(${JSONObject.quote(ref)})" else "document.body"
        val near = if (ref != null) "null" else "$NEAR"
        val max = msg.optInt("max_chars", 0).takeIf { it > 0 } ?: 30000
        js(t, "return em.read($root, ${msg.optString("filter") == "all"}, $max, $near)", answer, { v ->
            val got = v as JSONObject
            var text = got.optString("text")
            if (got.optBoolean("cut")) text += "\n(… cut short: read a part with ref_id, or look with find)"
            val off = listOf("above", "below").mapNotNull { side -> got.optInt(side).takeIf { it > 0 }?.let { "$it $side" } }
            if (off.isNotEmpty()) text += "\n(Not listed: ${off.joinToString(" and ").replaceFirst(" ", " elements ")}, " +
                "more than a screen from what's shown. Scroll, or find, reaches them.)"
            val asked = listOf(t.url, msg.optString("filter"), ref ?: "", msg.optInt("max_chars", 0))
            val seen = asked to sha1(text)
            if (reads[t.id] == seen) {
                reads.remove(t.id)
                return@js answer(describe(t).put("page", "Unchanged since your last read_page of this tab. (Read again if " +
                    "you no longer have it.)"))
            }
            reads[t.id] = seen
            answer(describe(t).put("page", text))
        })
    }

    private fun opFind(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val q = msg.optString("query")
        require(q.isNotBlank()) { "find needs a query" }
        js(t, "return em.find(${JSONObject.quote(q)}, ${msg.optInt("limit", 0).takeIf { it > 0 } ?: 20})", answer,
            { found -> answer(describe(t).put("found", found ?: JSONArray())) })
    }

    private fun opGetText(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val cap = msg.optInt("max_chars", 0).takeIf { it > 0 } ?: 50000
        js(t, "const el = document.querySelector('main, article, [role=main]') || document.body;" +
            "const t = (el.innerText || ''); return t.length > $cap ? t.slice(0, $cap) + " +
            "'\\n(… ' + (t.length - $cap) + ' more characters)' : t", answer,
            { text -> answer(describe(t).put("text", text ?: "")) })
    }

    /** text run in the page as a REPL runs it (its last expression's value back, await anywhere): the first of
     * replBodies' readings of it that compiles, run; what it throws said as the error. code: an async function's body,
     * run as it is. */
    private fun opJavascript(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        if (!msg.has("text")) return js(t, msg.optString("code"), answer, { v ->
            answer(describe(t).put("result", v ?: JSONObject.NULL)) }, page = true)
        val bodies = replBodies(msg.optString("text"))
        val ran = { v: Any? ->
            if (v is JSONObject && v.length() == 1 && v.has(REPL_THREW)) answer(error(v.optString(REPL_THREW)))
            else answer(describe(t).put("result", v ?: JSONObject.NULL))
        }
        fun run(i: Int) = js(t, replRun.replace("BODY", bodies[i]), answer, ran, page = true)
        fun tried(i: Int) {
            js(t, replRun.replace("BODY", bodies[i]), answer, ran, page = true, failed = { why ->
                if ("SyntaxError" in why && i + 1 < bodies.size) tried(i + 1) else answer(error(why))
            })
        }
        js(t, "const bodies = ${JSONArray(bodies)}, AF = (async () => {}).constructor; let why = null;" +
            "for (let i = 0; i < bodies.length; i++) {" +
            "  try { new AF(bodies[i]); return {at: i}; } catch (e) {" +
            "    if (!(e instanceof SyntaxError)) return {unchecked: true}; why = String(e); } }" +
            "return {why}", answer, { got ->
            val o = got as JSONObject
            when {
                o.has("at") && !o.isNull("at") -> run(o.getInt("at"))
                o.optBoolean("unchecked") -> tried(0)
                else -> answer(error(o.optString("why").ifEmpty { "SyntaxError" }))
            }
        }, page = true)
    }

    // (doing)

    /** then(x, y, covered, k) for a ref's centre (scrolled to first), or for x and y as the last screenshot had them:
     * the page's CSS pixels, k the WebView's pixels to one. */
    private fun point(t: Tab, msg: JSONObject, answer: (JSONObject) -> Unit, then: (Double, Double, String?, Float) -> Unit) {
        val ref = msg.optString("ref").takeIf { it.isNotEmpty() }
        fun k(iw: Double) = (t.web.width / max(1.0, iw)).toFloat()
        if (ref != null) js(t, "const el = em.get(${JSONObject.quote(ref)}), c = await em.centre(el), h = em.hit(el, c.x, c.y);" +
            "return {x: h.x, y: h.y, covered: h.at ? null : h.by ? em.line(h.by, 0) : 'nothing: it is off the page'," +
            " iw: innerWidth, ih: innerHeight}", answer, { c ->
            val o = c as JSONObject
            val iw = o.getDouble("iw")
            then(o.getDouble("x").coerceIn(0.0, iw), o.getDouble("y").coerceIn(0.0, o.getDouble("ih")),
                if (o.isNull("covered")) null else o.optString("covered"), k(iw))
        })
        else if (msg.has("x") && msg.has("y") && !msg.isNull("x") && !msg.isNull("y")) {
            val scale = if (msg.optBoolean("css")) 1.0 else scales[t.id] ?: 1.0
            val x = msg.getDouble("x") / scale
            val y = msg.getDouble("y") / scale
            js(t, "return innerWidth", answer, { iw -> then(x, y, null, k((iw as Number).toDouble())) })
        } else throw IllegalArgumentException("a ref, or x and y, says where")
    }

    private fun opClick(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val mods = msg.optJSONArray("modifiers").strings().filter { it in listOf("shift", "ctrl", "alt", "cmd") }
        point(t, msg, answer) { x, y, covered, k ->
            val started = SystemClock.uptimeMillis()
            val count = max(1, msg.optInt("count", 1))
            val press = msg.optInt("press", 0) // (one press of a click count's: the Browser page's)
            pointer.add("click", x, y, count = if (press > 0) 1 else count, tab = t.id)
            drawn()
            Input.click(t.web, (x * k).toFloat(), (y * k).toFloat(), msg.optString("button"), if (press > 0) 1 else count, mods) {
                if (msg.optBoolean("quick")) return@click answer(JSONObject().put("clicked",
                    JSONArray(listOf(x.roundToInt(), y.roundToInt()))))
                val s = if (msg.optBoolean("css")) 1.0 else scales[t.id] ?: 1.0
                settle(t, answer, JSONObject().put("clicked", JSONArray(listOf((x * s).roundToInt(), (y * s).roundToInt())))
                    .apply { if (covered != null) put("covered_by", covered) }, started)
            }
        }
    }

    /** A drag along path ([[x, y], ...], the page's CSS pixels if css, else the last screenshot's). */
    private fun opDrag(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val scale = if (msg.optBoolean("css")) 1.0 else scales[t.id] ?: 1.0
        val raw = msg.optJSONArray("path") ?: JSONArray()
        val path = (0 until raw.length()).mapNotNull { raw.optJSONArray(it) }.filter { it.length() == 2 }
            .map { it.getDouble(0) / scale to it.getDouble(1) / scale }
        require(path.size >= 2) { "a drag needs a path of two points at least" }
        val started = SystemClock.uptimeMillis()
        js(t, "return innerWidth", answer, { iw ->
            val k = (t.web.width / max(1.0, (iw as Number).toDouble())).toFloat()
            pointer.add("drag", path[0].first, path[0].second, path = path, tab = t.id)
            drawn()
            Input.drag(t.web, path.map { (it.first * k).toFloat() to (it.second * k).toFloat() }) {
                settle(t, answer, JSONObject().put("dragged", path.size), started)
            }
        })
    }

    private fun opHover(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        point(t, msg, answer) { x, y, _, k ->
            pointer.add("hover", x, y, tab = t.id)
            drawn()
            Input.move(t.web, (x * k).toFloat(), (y * k).toFloat())
            main.postDelayed({ answer(describe(t)) }, 80)
        }
    }

    /** text typed into the focused field (or ref's, focused first), a key press a character at a time, as a keyboard
     * would type it (a character no key here makes: put in as the page's own editing would). */
    private fun opType(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val ref = msg.optString("ref").takeIf { it.isNotEmpty() }
        val focus = if (ref != null) "em.get(${JSONObject.quote(ref)}).focus();" else ""
        val left = ArrayDeque(msg.optString("text").toList())
        fun readBack() = js(t, "const el = em.typed;" +
            "if (!el || !el.isConnected) return null;" +
            "const v = 'value' in el ? String(el.value) : el.textContent;" +
            "return v.length > 2000 ? v.slice(0, 2000) + '…' : v", answer, { value ->
            answer(if (value != null) describe(t).put("value", value) else describe(t).put("note", "the field's gone (the page changed)"))
        })
        fun nextChar() {
            val ch = left.removeFirstOrNull() ?: return main.postDelayed({ readBack() }, SETTLE_MS).let { }
            if (!(if (ch == '\n') Input.press(t.web, "Enter", emptyList()).let { true } else Input.type(t.web, ch)))
                return js(t, "const ch = ${JSONObject.quote(ch.toString())}, el = em.typed;" +
                    "if (!document.execCommand('insertText', false, ch) && el) {" +
                    "  if ('value' in el) em.setValue(el, el.value + ch); else el.textContent += ch; }" +
                    "return true", answer, { main.postDelayed({ nextChar() }, TYPE_GAP_MS) })
            main.postDelayed({ nextChar() }, TYPE_GAP_MS)
        }
        js(t, focus + "const el = document.activeElement;" +
            "if (!em.editable(el) && !${msg.optBoolean("anywhere")}) throw new Error('nothing to type into is focused: " +
            "click the field, or give its ref'); em.typed = em.editable(el) ? el : null; return true", answer, { nextChar() })
    }

    private fun opKey(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val combos = msg.optString("keys").split(Regex("\\s+")).filter { it.isNotEmpty() }
        require(combos.isNotEmpty()) { "key needs keys: Enter, Tab, cmd+a, Escape, …" }
        val names = mapOf("return" to "Enter", "enter" to "Enter", "esc" to "Escape", "escape" to "Escape", "tab" to "Tab",
            "backspace" to "Backspace", "delete" to "Delete", "del" to "Delete", "space" to "Space", "up" to "Up",
            "down" to "Down", "left" to "Left", "right" to "Right", "home" to "Home", "end" to "End",
            "pageup" to "PageUp", "pagedown" to "PageDown")
        val modNames = mapOf("shift" to "shift", "ctrl" to "ctrl", "control" to "ctrl", "alt" to "alt", "option" to "alt",
            "cmd" to "cmd", "command" to "cmd", "meta" to "cmd", "super" to "cmd")
        val presses = ArrayDeque(combos.map { combo ->
            val parts = combo.split("+")
            var mods = parts.dropLast(1).mapNotNull { modNames[it.lowercase()] }.toSortedSet()
            var k = parts.last()
            k = names[k.lowercase()] ?: k
            if (k.length == 1 && k[0].isUpperCase()) {
                k = k.lowercase()
                mods = (mods + "shift").toSortedSet()
            }
            k to mods.toList()
        })
        val started = SystemClock.uptimeMillis()
        fun press() {
            val (k, mods) = presses.removeFirstOrNull() ?: return settle(t, answer, null, started)
            try {
                Input.press(t.web, k, mods)
            } catch (e: Exception) {
                return answer(error(said(e)))
            }
            main.postDelayed({ press() }, KEY_GAP_MS)
        }
        press()
    }

    /** A real mouse wheel turned over a ref, a point of the last screenshot, or the page's middle: dy pixels down (600
     * if none's given; up negative), dx right; what's under the pointer scrolls. */
    private fun opScroll(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val dx = msg.optInt("dx", 0)
        val dy = if (msg.has("dy") && !msg.isNull("dy")) msg.optInt("dy") else 600
        val under = { x: Double, y: Double -> "let el = document.elementFromPoint($x, $y);" +
            "while (el && el !== document.body && el !== document.documentElement && !(" +
            "el.scrollHeight > el.clientHeight + 2 && /(auto|scroll)/.test(getComputedStyle(el).overflowY)) && !(" +
            "el.scrollWidth > el.clientWidth + 2 && /(auto|scroll)/.test(getComputedStyle(el).overflowX)))" +
            " el = el.parentElement;" +
            "const inner = !!el && el !== document.body && el !== document.documentElement;" +
            "const s = inner ? el : document.scrollingElement;" }
        fun inView(came: JSONObject): String {
            val lines = came.optJSONArray("lines").strings()
            if (lines.isEmpty()) return "Nothing new came into view."
            val more = came.optInt("more")
            return "Came into view:\n" + lines.joinToString("\n") + if (more > 0) "\n(… and $more more: read_page lists them)" else ""
        }
        val at = { x: Double, y: Double, _: String?, k: Float ->
            pointer.add("scroll", x, y, dx = dx.toDouble(), dy = dy.toDouble(), tab = t.id)
            drawn()
            js(t, under(x, y) + "return inner", answer, {
                Input.wheel(t.web, (x * k).toFloat(), (y * k).toFloat(), dx, dy, k)
                main.postDelayed({
                    js(t, under(x, y) + "await em.sleep(120);" +
                        "const came = em.came(em.seen || new Set(), $CAME_LINES, $CAME_CHARS);" +
                        "em.seen = null;" +
                        "return {where: {x: s.scrollLeft, y: s.scrollTop, height: s.scrollHeight, inner}, came}", answer, { got ->
                        val o = got as JSONObject
                        answer(describe(t).put("scrolled", o.getJSONObject("where")).put("by", "wheel")
                            .put("in_view", inView(o.getJSONObject("came"))))
                    })
                }, 250)
            })
        }
        // (what's in view first: before a ref's element is scrolled to, which may bring things into view itself)
        if (msg.optString("ref").isNotEmpty() || (msg.has("x") && !msg.isNull("x")))
            js(t, "em.seen = new Set(em.onscreen()); return true", answer, { point(t, msg, answer, at) })
        else js(t, "em.seen = new Set(em.onscreen()); return {x: innerWidth / 2, y: innerHeight / 2, iw: innerWidth}", answer, { c ->
            val o = c as JSONObject
            at(o.getDouble("x"), o.getDouble("y"), null, (t.web.width / max(1.0, o.getDouble("iw"))).toFloat())
        })
    }

    /** What the page has said in its console (and its errors), newest last: pattern, a regular expression, only those
     * matching it; onlyErrors, only errors; clear, forgotten after. */
    private fun opConsole(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val p = msg.optString("pattern")
        val re: (String) -> Boolean = if (p.isEmpty()) { _ -> true } else try {
            Regex(p).let { r -> { s: String -> r.containsMatchIn(s) } }
        } catch (_: Exception) {
            { s: String -> p in s }
        }
        val errors = msg.optBoolean("onlyErrors")
        val got = t.console.filter { re(it.optString("text")) && (!errors || it.optString("level") == "error") }
            .takeLast(max(1, msg.optInt("limit", 0).takeIf { it > 0 } ?: 100))
        if (msg.optBoolean("clear")) t.console.clear()
        answer(describe(t).put("console", JSONArray(got)))
    }

    /** What the page has loaded (itself, and its resources), newest last: urlPattern, only URLs with it in them; clear,
     * those forgotten after. */
    private fun opNetwork(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        js(t, "const p = ${JSONObject.quote(msg.optString("urlPattern"))}, K = Symbol.for('emission.network');" +
            "const since = window[K] ?? -1;" +
            "const got = [...performance.getEntriesByType('navigation'), ...performance.getEntriesByType('resource')]" +
            ".filter(e => e.startTime > since && (!p || e.name.includes(p)))" +
            ".slice(-${max(1, msg.optInt("limit", 0).takeIf { it > 0 } ?: 100)}).map(e => ({url: e.name, type: e.initiatorType || " +
            "e.entryType, status: e.responseStatus || null, ms: Math.round(e.duration), bytes: e.transferSize || null}));" +
            "if (${msg.optBoolean("clear")}) window[K] = performance.now(); return got", answer,
            { got -> answer(describe(t).put("requests", got ?: JSONArray())) })
    }

    /** The page's size (its viewport) made width by height CSS pixels: laid out at it, shown shrunk to fit the screen. */
    private fun opResize(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val w = msg.optInt("width")
        val h = msg.optInt("height")
        require(w in 200..RESIZE_MAX.first && h in 200..RESIZE_MAX.second) {
            "width between 200 and ${RESIZE_MAX.first}, height between 200 and ${RESIZE_MAX.second}"
        }
        if (msg.optBoolean("fit")) return answer(describe(t).put("size", JSONObject.NULL))
        b.stage.size = w to h
        main.postDelayed({
            js(t, "return {width: innerWidth, height: innerHeight}", answer, { v ->
                answer(describe(t).put("size", v).put("note", "on the phone's screen, the page is shown shrunk to fit"))
            })
        }, 300)
    }

    private fun opFormInput(msg: JSONObject, answer: (JSONObject) -> Unit) {
        val t = tab(msg)
        val ref = msg.optString("ref")
        require(ref.isNotEmpty()) { "form_input needs the field's ref" }
        val v = if (msg.has("value")) msg.get("value") else JSONObject.NULL
        js(t, "const el = em.get(${JSONObject.quote(ref)}), v = ${JSONArray(listOf(v)).toString().removeSurrounding("[", "]")};" +
            "if (el.tagName === 'SELECT') {" +
            "  const o = [...el.options].find(o => o.value === String(v) || o.text.trim() === String(v));" +
            "  if (!o) throw new Error('no option ' + v + ': ' + [...el.options].map(o => o.text.trim()).join(', '));" +
            "  em.setValue(el, o.value); return o.text.trim(); }" +
            "if (el.type === 'checkbox' || el.type === 'radio') {" +
            "  const want = v === true || v === 'true' || v === 'on' || v === 1;" +
            "  if (el.checked !== want) el.click(); return el.checked; }" +
            "if (!['INPUT', 'TEXTAREA'].includes(el.tagName)) throw new Error(${JSONObject.quote(ref)}" +
            " + ' is a ' + el.tagName.toLowerCase() + ', not a field');" +
            "em.setValue(el, String(v ?? '')); return el.value", answer,
            { value -> answer(describe(t).put("value", value ?: JSONObject.NULL)) })
    }

    // (a page's own prompts: alert, confirm, prompt, a site's sign-in, files to upload)

    private fun opPrompts(msg: JSONObject, answer: (JSONObject) -> Unit) = answer(JSONObject().put("prompts", JSONArray(b.prompts(null))))

    private fun opAnswerPrompt(msg: JSONObject, answer: (JSONObject) -> Unit) {
        b.answerPrompt(msg)
        main.postDelayed({ answer(JSONObject().put("ok", true).put("tabs", JSONArray(b.tabs.values.map(::describe)))) }, 300)
    }

    // (sign-ins)

    private fun opSites(msg: JSONObject, answer: (JSONObject) -> Unit) =
        answer(JSONObject().put("profile", b.profile).put("sites", JSONArray(b.sites())))

    companion object {
        const val KEY_GAP_MS = 30L  // (between keys, as a person's: kept steady)
        const val TYPE_GAP_MS = 12L // (between characters typed)
        const val SETTLE_MS = 100L  // (the last one typed: taken in by the page before its field's read back)
        const val LOAD_S = 30.0     // (a page's load waited for this long at most)
        const val JS_S = 80.0       // (a script's answer waited for this long at most: the host waits 85 s)
        const val SHOT_MAX = 1024.0 // (a screenshot's longest side: about XGA's, as computer use has it; zoom for more)
        const val ZOOM_MAX = 1568.0 // (a zoom's longest side: what a model sees whole)
        const val NEAR = 1          // (read_page: what's shown, and this many screens above and below it)
        const val CAME_LINES = 30   // (a scroll's answer: at most this much of what came into view)
        const val CAME_CHARS = 1500
        val RESIZE_MAX = 1920 to 2400 // (the page's size at most: a phone draws all of it, shrunk)
        const val REPL_THREW = "⁣threw"
        const val REPL_SPLITS = 6
        const val HOME_PAGE = "https://duckduckgo.com/"
        // (PAGE_JS's text that's changed as it's put in a page: checked as the app's built, in its build.gradle.kts)
        const val EM_MADE = "if (!globalThis.__em) globalThis.__em = "
        const val EM_BOUND = "\nconst em = globalThis.__em;"
        const val EM_REF = "id = 'ref_' + (++n)"
        const val SEAL = """if (Object.getOwnPropertyDescriptor(globalThis, K).writable) {
  const R = Reflect.apply, D = Document.prototype.elementFromPoint, B = Element.prototype.getBoundingClientRect,
    C = Node.prototype.contains, J = JSON.stringify, T = Promise.prototype.then, M = new Map(), MS = Map.prototype.set,
    MG = Map.prototype.get, MD = Map.prototype.delete, made = globalThis[K];
  const hit = (el, x, y) => { const h = R(D, document, [x, y]), b = R(B, el, []);
    return {at: !!h && (h === el || R(C, el, [h]) || R(C, h, [el])), x: b.left + b.width / 2, y: b.top + b.height / 2, by: h}; };
  const settle = (p, id) => R(T, p, [v => { let s; try { s = {ok: R(J, JSON, [v ?? null])}; } catch (e) { s = {error: String(e)}; }
    R(MS, M, [id, s]); }, e => R(MS, M, [id, {error: String(e)}])]);
  const take = id => { const r = R(MG, M, [id]); R(MD, M, [id]); return r === undefined ? null : {...r, s: S}; };
  const owns = x => x === S;
  const sealed = {...made, hit, settle, take, owns}, kept = {};
  for (const name of ['seen', 'typed']) Object.defineProperty(sealed, name, {get: () => kept[name], set: v => { kept[name] = v; }});
  Object.defineProperty(globalThis, K, {value: Object.freeze(sealed), enumerable: false, writable: false,
    configurable: false});
}"""
        const val SEARCH = "https://duckduckgo.com/?q="

        private fun sha1(text: String) = java.security.MessageDigest.getInstance("SHA-1").digest(text.toByteArray())
            .joinToString("") { "%02x".format(it) }

        /** What's typed in the address bar as a URL (emission.address): one as it is; a local host or an IP address
         * given http://, another host https://; anything else searched for. */
        fun address(raw: String): String {
            val text = raw.trim()
            if (text.isEmpty()) return HOME_PAGE
            if ("://" in text || text.startsWith("about:") || text.startsWith("file:") || text.startsWith("data:")) return text
            if (' ' !in text && !text.startsWith("/") && !text.startsWith("~")) {
                val host = try {
                    java.net.URI("http://$text").host?.removeSurrounding("[", "]") ?: ""
                } catch (_: Exception) {
                    ""
                }
                val ip = Regex("\\d{1,3}(\\.\\d{1,3}){3}").matches(host) || ':' in host
                if (ip || host == "localhost" || host.endsWith(".localhost")) return "http://$text"
                if ('.' in host) return "https://$text"
            }
            return SEARCH + URLEncoder.encode(text, "UTF-8")
        }

        /** text, as a REPL reads it, as async function bodies to try in turn (emission.repl_bodies): all of it one
         * expression (returned); then, from the last statement back, the statements before one and that one returned;
         * then all of it, returning nothing. */
        fun replBodies(raw: String): List<String> {
            val text = raw.trim().trimEnd(';').trimEnd()
            val splits = ArrayList<Int>()
            var depth = 0
            var i = 0
            val n = text.length
            val ops = "+-*/%&|^.,?:=<>([{!~"
            val after = "+-*/%&|^.,?:=<>)]}([`"
            while (i < n) {
                val c = text[i]
                if (c in "'\"`") {
                    var j = i + 1
                    while (j < n && text[j] != c) j += if (text[j] == '\\') 2 else 1
                    i = j + 1
                    continue
                }
                if (text.startsWith("//", i)) {
                    i = text.indexOf('\n', i).let { if (it < 0) n else it }
                    continue
                }
                if (text.startsWith("/*", i)) {
                    i = text.indexOf("*/", i + 2).let { if (it < 0) n else it + 2 }
                    continue
                }
                if (c in "([{") depth += 1
                else if (c in ")]}") {
                    depth = max(0, depth - 1)
                    if (c == '}' && depth == 0) splits += i + 1
                } else if (depth == 0 && c == ';') splits += i + 1
                else if (depth == 0 && c == '\n') {
                    val before = text.substring(0, i).trimEnd().takeLast(1)
                    val next = text.substring(i).trimStart().take(1)
                    if (before.isNotEmpty() && next.isNotEmpty() && before[0] !in ops && next[0] !in after) splits += i + 1
                }
                i += 1
            }
            val bodies = arrayListOf("return ($text\n);")
            val lasts = HashSet<String>()
            for (at in splits.toSortedSet().reversed()) {
                val head = text.substring(0, at)
                val last = text.substring(at).trim()
                if (last.isNotEmpty() && last !in lasts && lasts.size < REPL_SPLITS) {
                    lasts += last
                    bodies += "$head\nreturn ($last\n);"
                }
            }
            bodies += text
            return bodies
        }
    }
}
