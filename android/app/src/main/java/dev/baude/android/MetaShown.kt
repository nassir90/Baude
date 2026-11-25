package dev.baude.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** A setting as Meta shows and changes it (meta.shared_settings, and Baude's group_by): its name, its title and its
 * value, what a value must be, and its row. */
class MetaSetting(val name: String, val title: () -> String, val get: () -> Any, val takes: () -> String,
                  val coerce: (Any?) -> Any, val put: (Any) -> Unit, val note: String? = null, val metaSets: Boolean = true,
                  val row: @Composable (String?, (@Composable () -> Unit)?) -> Unit)

/** A number as Settings shows a scale: two places, the zeros after the point left off (widgets.fmt_scale). */
fun fmtScale(v: Double): String = String.format(Locale.ROOT, "%.2f", v).trimEnd('0').let { if (it.endsWith(".")) it + "0" else it }

/** A setting's value as the index and the replies say it (meta.said_value). */
fun saidValue(v: Any?): String = when (v) {
    is Boolean -> if (v) "on" else "off"
    is Double -> fmtScale(v)
    is Float -> fmtScale(v.toDouble())
    else -> v.toString().let { if (Regex("[\\w.\\-]+").matches(it)) it else JSONObject.quote(it) }
}

private fun round2(d: Double) = Math.round(d * 100) / 100.0

private fun onOff(name: String): (Any?) -> Any = { v ->
    if (v is Boolean) v else when (v?.toString()?.trim()?.lowercase()) {
        "on", "true", "yes", "1" -> true
        "off", "false", "no", "0" -> false
        else -> throw IllegalArgumentException("$name is on or off.")
    }
}

private fun between(name: String, low: Double, high: Double): (Any?) -> Any = { v ->
    val d = (v as? Number)?.toDouble() ?: v?.toString()?.trim()?.toDoubleOrNull()
    val r = d?.let(::round2)
    if (r == null || r < low || r > high) throw IllegalArgumentException("$name is a number from ${fmtScale(low)} to ${fmtScale(high)}.")
    r
}

private fun oneOf(name: String, list: () -> List<String>): (Any?) -> Any = { v ->
    val all = list()
    all.firstOrNull { it.equals(v?.toString()?.trim(), true) }
        ?: throw IllegalArgumentException("$name is one of: ${all.joinToString(", ")}.")
}

private fun saved(block: () -> Unit) {
    block()
    Settings.save(Mesh.app)
}

/** The settings Meta shows and changes, in the desktop's order. */
object MetaSet {
    private fun levels(): List<String> {
        val h = metaHost()
        return metaEntry(Settings.metaModel.value ?: "haiku", metaModels(h), h)?.efforts.orEmpty()
    }

    private fun scale(name: String, title: () -> String, field: androidx.compose.runtime.MutableState<Float>, low: Float, high: Float,
                      note: String? = null) =
        MetaSetting(name, title, { round2(field.value.toDouble()) }, { "${fmtScale(low.toDouble())}–${fmtScale(high.toDouble())}" },
            between(name, low.toDouble(), high.toDouble()), { v -> saved { field.value = (v as Double).toFloat() } }, note,
            row = { f, x -> ScaleRow(name, f, x) })

    val ALL: LinkedHashMap<String, MetaSetting> = linkedMapOf(
        "accent" to MetaSetting("accent", { Wording.SharedSettings.accentColour }, { Settings.accent.value ?: "system" },
            { (listOf("system") + Settings.ACCENTS.keys).joinToString("|") },
            oneOf("accent") { listOf("system") + Settings.ACCENTS.keys },
            { v -> saved { Settings.accent.value = (v as String).takeIf { it in Settings.ACCENTS } } },
            row = { f, x -> AccentRow(f, x) }),
        "code_font" to MetaSetting("code_font", { Wording.SharedSettings.codeFont },
            { Settings.codeFont.value?.takeIf { it in CodeFont.FONTS } ?: CodeFont.DEFAULT }, { CodeFont.FONTS.keys.joinToString("|") },
            oneOf("code_font") { CodeFont.FONTS.keys.toList() }, { v -> saved { Settings.codeFont.value = v as String } },
            "takes from Baude's next start: its row has Restart", row = { f, x -> CodeFontRow(emptyList(), f, x) }),
        "text_scale" to scale("text_scale", { Wording.MetaSettings.textScale }, Settings.textScale, Settings.SCALE_MIN, Settings.SCALE_MAX),
        "icon_scale" to scale("icon_scale", { Wording.MetaSettings.iconScale }, Settings.iconScale, Settings.SCALE_MIN, Settings.SCALE_MAX),
        "global_scale" to scale("global_scale", { Wording.MetaSettings.globalScale }, Settings.globalScale, Settings.ZOOM_MIN,
            Settings.ZOOM_MAX, "what ⌘0 / Ctrl+0 goes back to"),
        "full_width" to MetaSetting("full_width", { Wording.MetaSettings.fullWidth }, { Settings.fullWidth.value }, { "on|off" },
            onOff("full_width"), { v -> saved { Settings.fullWidth.value = v as Boolean } }, row = { f, x -> FullWidthRow(f, x) }),
        "auto_update" to MetaSetting("auto_update", { Wording.Updates.metered }, { Settings.meteredUpdates.value }, { "on|off" },
            onOff("auto_update"), { v ->
                saved { Settings.meteredUpdates.value = v as Boolean }
                if (v as Boolean) Update.check(Mesh.app, force = true)
            }, row = { f, x -> MeteredRow(f, x) }),
        "meta_model" to MetaSetting("meta_model", { Wording.MetaSettings.intelligence }, {
            val h = metaHost()
            val wanted = Settings.metaModel.value ?: "haiku"
            metaEntry(wanted, metaModels(h), h)?.value ?: wanted
        }, { metaModels(metaHost()).joinToString("|") { it.value } },
            oneOf("meta_model") { metaModels(metaHost()).map { it.value } }, { v -> putMetaModel(Mesh.app, v as String) },
            "Meta's own model, from its next message", row = { f, x -> IntelligenceRow(f, x) }),
        "meta_effort" to MetaSetting("meta_effort", { Wording.MetaSettings.effort }, {
            Settings.metaEffort.value?.takeIf { it in levels() } ?: "default"
        }, { (listOf("default") + levels()).joinToString("|") }, oneOf("meta_effort") { listOf("default") + levels() },
            { v -> saved { Settings.metaEffort.value = (v as String).takeIf { it != "default" } } },
            "how hard Meta thinks, of the levels its model takes, from its next message", row = { f, x -> EffortRow(f, x) }),
        "meta_commands" to MetaSetting("meta_commands", { Wording.MetaSettings.runCommands }, {
            val h = metaHost()
            val wanted = Settings.metaModel.value ?: "haiku"
            metaTakesCommands(h, metaEntry(wanted, metaModels(h), h)?.value ?: wanted) && Settings.metaCommands.value
        }, { "on|off" }, onOff("meta_commands"), { v -> saved { Settings.metaCommands.value = v as Boolean } },
            "on Claude's models, Meta runs shell commands and edits files, from its next message; the user's to turn on or off",
            metaSets = false, row = { f, x -> RunCommandsRow(f, x) }),
        "group_by" to MetaSetting("group_by", { Wording.Sidebar.groupBy }, { HomePrefs.by.value }, { "folder|created|updated" },
            oneOf("group_by") { listOf("folder", "created", "updated") }, { v ->
                HomePrefs.by.value = v as String
                HomePrefs.save()
            }, "the left bar's chats, under their folders, the days they were made, or the days they were last used", row = { f, x ->
                Choice(Wording.Sidebar.groupBy, listOf("folder", "created", "updated"), HomePrefs.by.value,
                    { when (it) { "created" -> Wording.Sidebar.created; "updated" -> Wording.Sidebar.updated; else -> Wording.Common.folder } },
                    subtitle = f, extra = x) {
                    HomePrefs.by.value = it
                    HomePrefs.save()
                }
            }),
    )
}

/** Meta's look for what it shows (.meta-ui): rows in a tool card's outline, no fill, a rule between them. */
@Composable
fun <T> MetaBoxed(rows: List<T>, modifier: Modifier = Modifier, row: @Composable (T) -> Unit) {
    val c = LocalBaude.current
    val shape = RoundedCornerShape(Gap.cardRadius)
    Column(modifier.fillMaxWidth().padding(top = 2.dp).clip(shape).border(1.dp, c.rule, shape)) {
        rows.forEachIndexed { i, r ->
            if (i > 0) Box(Modifier.fillMaxWidth().height(1.dp).background(c.rule))
            row(r)
        }
    }
}

/** What Meta showed under a call: a setting's row, chats, pages or a button, the changelog, or a form. */
@Composable
fun MetaShown(spec: JSONObject) {
    val setting = MetaSet.ALL[spec.optString("setting")]
    when {
        spec.has("form_id") -> MetaFormSlot(spec.optString("form_id"))
        spec.has("setting") -> if (setting != null) MetaBoxed(listOf(Unit)) { setting.row(null, null) }
        spec.has("changelog") -> MetaChangelog()
        else -> MetaRowsOf(spec)
    }
}

/** The running version's changelog (update.changelog_group): its last 5, the rest under Show more. */
@Composable
fun MetaChangelog() {
    val ctx = LocalContext.current
    var all by remember { mutableStateOf(false) }
    val list = AppVersion.changes(ctx)
    val shown: List<Change?> = if (all || list.size <= 5) list else list.take(5) + null
    MetaBoxed(shown) { ch ->
        if (ch == null) ButtonRow(Wording.Meta.showMore(n = list.size - 5)) { all = true }
        else ChangeRow(ch.subject, Wording.Changelog.row(made = madeWhen(ch.made), commit = ch.short))
    }
}

/** A mark on a chat in the left bar: how it was changed, and a note on it. */
class Mark(val how: String, val note: String)

/** The last unkept change that marked a chat, else its highlight (meta.marks). */
fun metaMark(h: String, sid: String): Mark? {
    val key = "$h/$sid"
    var found: Mark? = null
    for (a in MetaTools.unkept()) for (m in a.marks) if (m.optString(1) == key) found = Mark(m.optString(2), m.optString(3))
    return found ?: if (key in MetaTools.highlight) Mark("highlight", "") else null
}

/** The note after a marked row's dot. */
@Composable
fun MarkNote(mark: Mark?) {
    val c = LocalBaude.current
    if (mark == null || mark.note.isEmpty()) return
    Text(mark.note, style = Type.body.copy(fontSize = Tokens.Font.body * Tokens.Font.small), color = c.fg.copy(alpha = 0.75f),
        maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 96.dp))
}

/** A foot's flat text button: a tap runs it, a long-press says what it is. */
@Composable
fun FootButton(label: String, tip: String, text: Color, onClick: () -> Unit) {
    Text(label, style = Type.caption, color = text, maxLines = 1,
        modifier = Modifier.clip(RoundedCornerShape(4.dp)).tip(tip, onClick).padding(horizontal = 4.dp))
}

/** The foot of Meta's page, as its box's status line: Clear, Copy, the working dots, the context meter. */
@Composable
fun MetaFoot() {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    Mesh.tick.value
    val text = c.fg.copy(alpha = c.fg.alpha * 0.75f)
    Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically) {
        FootButton(Wording.Common.clear, Wording.Meta.clearTip, text) { Meta.clear() }
        FootButton(Wording.Common.copy, Wording.Meta.copyTip, text) { copyMetaReport(ctx) }
        WorkingDots(Meta.chat.thinking.value)
        Spacer(Modifier.weight(1f))
        ContextMeter(metaContextShown(), false, text)
    }
}

/** Meta's context as its meter shows it: the session's usage in the window of the model picked if it's another. */
fun metaContextShown(): ContextShown {
    val c = Meta.chat
    val h = metaHost() ?: c.hostId
    val picked = Settings.metaModel.value ?: "haiku"
    val value = metaEntry(picked, metaModels(h), h)?.value ?: picked
    var usage = c.chrome.usage.value
    if (usage != null && Meta.startedModelValue.isNotEmpty() && Meta.startedModelValue != value)
        usage = JSONObject().put("totalTokens", usage.optInt("totalTokens")).put("maxTokens", contextWindow(h, value) ?: 0)
    return contextShown(c, value, usage)
}

/** The send button's tooltip (model_changed): the model Meta's asked, its effort, and whether it runs commands. */
fun metaSendTip(): String {
    val h = metaHost()
    val picked = Settings.metaModel.value ?: "haiku"
    val entry = metaEntry(picked, metaModels(h), h)
    val level = Settings.metaEffort.value?.takeIf { entry != null && it in entry.efforts }
    var name = (entry?.name ?: picked) + (level?.let { ", " + (EFFORT_LABELS[it] ?: it).lowercase() } ?: "")
    if (metaTakesCommands(h, entry?.value) && Settings.metaCommands.value) name = Wording.Meta.withCommands(model = name)
    return Wording.Meta.sendTip(model = name, shortcut = Wording.Composer.sendKey)
}

/** A button on the changes bar: a word, flat. */
@Composable
fun BarButton(label: String, onClick: () -> Unit) {
    val c = LocalBaude.current
    Box(Modifier.heightIn(min = 24.dp).clip(RoundedCornerShape(6.dp)).clickable(onClick = onClick).padding(horizontal = 8.dp),
        contentAlignment = Alignment.Center) {
        Text(label, style = Type.bold, color = c.fg)
    }
}

/** Over Meta's box: how many changes of Meta's are marked in the left bar, with Keep and Undo for them all. */
@Composable
fun MetaChangesBar() {
    val c = LocalBaude.current
    val n = MetaTools.unkept().size
    if (n == 0) return
    Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 3.dp)) {
        Row(Modifier.weight(1f).clip(RoundedCornerShape(Tokens.Meta.changesBarRadius))
            .background(c.accentBg.copy(alpha = Tokens.Meta.changesBarAlpha.toFloat()))
            .padding(start = 10.dp, end = 2.dp, top = 1.dp, bottom = 1.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(Wording.Meta.changes(n = n), style = Type.caption, color = c.fg, modifier = Modifier.weight(1f))
            BarButton(Wording.Meta.keep) { MetaTools.keep(MetaTools.unkept()) }
            BarButton(Wording.Common.undo) { MetaTools.undoMany(MetaTools.unkept(), byUser = true) }
        }
    }
}

// ---- Copy: the conversation as text for an issue

private fun withCommas(n: Int) = String.format(Locale.ENGLISH, "%,d", n)

/** The report's head (meta.report_head): the pairs of the lines over a copied conversation. */
private fun reportHead(): List<Pair<String, String>> {
    val c = Meta.chat
    val h = metaHost()
    val has = Meta.hasSession
    val list = metaModels(h)
    val picked = Settings.metaModel.value ?: "haiku"
    val pickedValue = metaEntry(picked, list, h)?.value ?: picked
    val value = if (has) Meta.startedModelValue else pickedValue
    fun nameOf(v: String) = metaEntry(v, list, h)?.name ?: v
    val level = (if (has) Meta.startedWithParts.getOrNull(1)?.ifEmpty { null } else Settings.metaEffort.value) ?: ""
    val commands = if (has) Meta.startedWithParts.getOrNull(2) == "true" else metaTakesCommands(h, pickedValue) && Settings.metaCommands.value
    val program = program(value)
    val tools = listOf("Read", "Grep", "Glob") + (if (commands) Meta.COMMAND_TOOLS else emptyList()) +
        MetaTools.TOOLS.let { a -> (0 until a.length()).map { a.getJSONObject(it).optString("name") } }
    val u = c.chrome.usage.value
    val context = if (u != null && (u.optInt("maxTokens") > 0 || u.optBoolean("showWithoutLimit"))) {
        val used = u.optInt("totalTokens")
        val total = u.optInt("maxTokens")
        (if (total > 0) Wording.Report.tokensOf(used = withCommas(used), total = withCommas(total))
            else Wording.SidePanel.tokens(n = withCommas(used))) + u.optJSONArray("categories").list()
            .filter { it.optInt("tokens") > 0 && it.optString("kind") != "deferred" }
            .map { "${it.optString("name")} ${withCommas(it.optInt("tokens"))}" }.let { if (it.isEmpty()) "" else " (${it.joinToString(", ")})" }
    } else if (!has) Wording.Report.noSession else {
        val shown = metaContextShown()
        ((shown.label + " " + (shown.text ?: "")).removePrefix(Wording.ContextUsage.label)).trim(' ', ':', '—')
            .ifEmpty { Wording.Report.notSaidBySession }
    }
    return listOf(
        Wording.Report.app to "${Wording.MeshVersions.android} (${Wording.MetaSettings.title})",
        Wording.Common.model to "${nameOf(value)} ($value)",
        Wording.Report.nextMessage to if (pickedValue != value) Wording.Report.pickedSince(name = nameOf(pickedValue), value = pickedValue) else "",
        Wording.Report.runsOn to if (has) (if (program == "claude" || program == "codex") program else "$program-claude") else "",
        Wording.MetaSettings.effort to if (level.isNotEmpty()) (EFFORT_LABELS[level] ?: level) else Wording.Report.default,
        Wording.MetaSettings.codeMode to Wording.Report.off,
        Wording.Report.runsCommands to if (commands) Wording.Report.on else Wording.Report.off,
        Wording.Report.mode to if (has) Meta.startedMode else "",
        Wording.Report.context to context,
        Wording.Lookup.tools to tools.distinct().joinToString(", "),
        Wording.Report.copied to SimpleDateFormat("yyyy-MM-dd HH:mm:ss Z", Locale.ENGLISH).format(Date()))
}

private fun whenText(t: Double?): String =
    if (t == null || t <= 0) "" else " · " + SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.ENGLISH).format(Date((t * 1000).toLong()))

/** The conversation as text for an issue (meta.report_text): the head, then who said what, each tool call with its
 * input and what it gave back. */
fun metaReport(): String {
    val c = Meta.chat
    val parts = ArrayList<String>()
    val lines = c.lines.toList()
    var i = 0
    while (i < lines.size) {
        if (lines[i].kind != "you") {
            i++
            continue
        }
        val you = lines[i]
        val turn = you.uuid?.let { Meta.turns[it] }
        parts.add("## ${Wording.Report.user}" + whenText(turn?.optDouble("t")?.takeIf { !it.isNaN() }) + "\n" + you.text)
        var j = i + 1
        while (j < lines.size && lines[j].kind != "you") j++
        val reply = lines.subList(i + 1, j)
        if (reply.isNotEmpty() || (j >= lines.size && c.thinking.value)) {
            val model = turn?.optString("model")?.ifEmpty { null }
            val out = ArrayList<String>()
            if (j >= lines.size && c.thinking.value) out.add("(${Wording.Report.stillReplying})")
            for (l in reply) when (l.kind) {
                "note" -> out.add("(${l.text})")
                "bot" -> out.add(l.text.trim())
                "metacall" -> Meta.calls[l.text]?.let { call ->
                    val input = call.input.toString(1).replace("\\/", "/")
                    out.add(Wording.Report.metaTool(name = call.name.ifEmpty { "?" }, state = call.state) + "\n" + trimmed(input))
                    call.result?.takeIf { it.isNotEmpty() }?.let { out.add(Wording.Report.result + "\n" + trimmed(it)) }
                }
                "tool" -> out.add("[tool ${l.text}]")
            }
            val err = reply.lastOrNull { it.kind == "error" }
            parts.add("## " + (if (model != null) Wording.Report.metaModel(model = model) else Wording.MetaSettings.title) +
                (if (err != null) " " + Wording.Report.error else "") + whenText(turn?.optDouble("reply_t")?.takeIf { !it.isNaN() }) +
                (if (out.isEmpty()) "" else "\n" + out.joinToString("\n")) + (err?.let { "\n" + it.text } ?: ""))
        }
        i = j
    }
    return capped(reportHead(), parts)
}

/** The conversation copied: on the clipboard, and said so. */
fun copyMetaReport(ctx: Context) {
    val app = ctx.applicationContext
    app.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText(Wording.Meta.copyTip, metaReport()))
    Toast.makeText(app, Wording.Meta.copied, Toast.LENGTH_SHORT).show()
}
