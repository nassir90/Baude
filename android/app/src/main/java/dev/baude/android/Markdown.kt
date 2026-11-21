package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import android.util.LruCache
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.BaselineShift
import androidx.compose.ui.text.withStyle
import androidx.compose.foundation.text.appendInlineContent
import androidx.compose.ui.unit.em
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp

// -------------------------------------------------------------------------------------------------- the blocks --

/** A reply's markdown as blocks (widgets.markdown_blocks); their text is source, its inline markdown applied as
 * they're drawn. */
sealed interface Block {
    data class Para(val lines: List<String>) : Block
    data class Heading(val level: Int, val text: String) : Block
    data class Item(val depth: Int, val marker: String, val text: String) : Block
    data class Quote(val text: String) : Block
    data object Rule : Block
    data class Code(val lang: String, val code: String) : Block
    data class Image(val alt: String, val src: String) : Block
    data class Table(val rows: List<List<String>>) : Block
}

private val FENCE = Regex("""^\s*```\s*(\S*)""")
private val TABLE_RULE = Regex(""":?-{2,}:?""")
private val HEADING = Regex("""^(#{1,6})\s+(.*)""")
private val RULE = Regex("""\s*([-*_])(\s*\1){2,}\s*""")
private val ITEM = Regex("""^(\s*)([-*+•◦]|\d+[.)])\s+(.*)$""")
private val QUOTE_MARK = Regex("""^>\s?""")
private val IMAGE = Regex("""^\s*!\[([^\]]*)]\(([^)\s]+)\)\s*$""")

private val blocksCache = LruCache<String, List<Block>>(256)

/** markdownBlocks, kept by text (a long chat's items that scroll back in aren't parsed again). */
fun cachedBlocks(text: String): List<Block> = blocksCache.get(text) ?: markdownBlocks(text).also { blocksCache.put(text, it) }

/** widgets.markdown_parse: line by line, the same checks in the same order. An unclosed fence runs to the end. */
fun markdownBlocks(md: String): List<Block> {
    val blocks = ArrayList<Block>()
    val para = ArrayList<String>()
    val table = ArrayList<List<String>>()
    var code: ArrayList<String>? = null
    var lang = ""
    fun flush() {
        if (para.isNotEmpty()) {
            blocks.add(Block.Para(para.toList()))
            para.clear()
        }
        if (table.isNotEmpty()) {
            blocks.add(Block.Table(table.toList()))
            table.clear()
        }
    }
    for (line in md.split("\n")) {
        val fence = if ("```" in line) FENCE.find(line) else null
        if (code != null) {
            if (fence != null && fence.groupValues[1].isEmpty()) {
                blocks.add(Block.Code(lang, code.joinToString("\n")))
                code = null
            } else code.add(line)
            continue
        }
        if (fence != null) {
            flush()
            code = ArrayList()
            lang = fence.groupValues[1]
            continue
        }
        val stripped = line.trim()
        if (stripped.startsWith("|") && stripped.endsWith("|") && stripped.length > 1) {
            if (para.isNotEmpty()) flush()
            val cells = stripped.substring(1, stripped.length - 1).split("|").map { it.trim() }
            if (!cells.all { TABLE_RULE.matches(it) }) table.add(cells)
            continue
        }
        if (table.isNotEmpty()) flush()
        if (stripped.isEmpty()) {
            flush()
            continue
        }
        val heading = if (line.startsWith("#")) HEADING.find(line) else null
        val item = ITEM.find(line)
        val last = blocks.lastOrNull()
        when {
            heading != null -> {
                flush()
                blocks.add(Block.Heading(heading.groupValues[1].length, heading.groupValues[2]))
            }
            RULE.matches(line) -> {
                flush()
                blocks.add(Block.Rule)
            }
            item != null -> {
                flush()
                val depth = item.groupValues[1].replace("\t", "    ").length / 2
                val m = item.groupValues[2]
                val marker = if (m.length == 1 && m in "-*+•◦") (if (depth % 2 == 0) "•" else "◦") else m
                blocks.add(Block.Item(depth, marker, item.groupValues[3]))
            }
            line.startsWith(">") -> {
                flush()
                val text = QUOTE_MARK.replaceFirst(line, "")
                if (last is Block.Quote) blocks[blocks.size - 1] = Block.Quote(last.text + "\n" + text)
                else blocks.add(Block.Quote(text))
            }
            IMAGE.matches(line) -> {
                flush()
                val m = IMAGE.find(line)!!
                blocks.add(Block.Image(m.groupValues[1], m.groupValues[2]))
            }
            last is Block.Item && para.isEmpty() && line[0].isWhitespace() ->
                blocks[blocks.size - 1] = Block.Item(last.depth, last.marker, last.text + " " + stripped)
            else -> para.add(line)
        }
    }
    if (code != null) blocks.add(Block.Code(lang, code.joinToString("\n")))
    flush()
    return blocks
}

// ------------------------------------------------------------------------------------------------ the inline --

private const val W = "\\p{L}\\p{N}_"
private val CODE_SPAN = Regex("`([^`\\n]+)`")
private val MD_LINK = Regex("""\[([^\]]+)]\(([^)\s]+)\)""")
private val BARE_URL = Regex("(?<![$W/])(https?://[^\\s<>()\\[\\]`*\\u0000]+[^\\s<>()\\[\\]`*.,;:!?'\"\\u0000])")
private val BOLD_ITALIC = Regex("\\*\\*\\*(?=\\S)(.+?)(?<=\\S)\\*\\*\\*", RegexOption.DOT_MATCHES_ALL)
private val BOLD = Regex("\\*\\*(?=\\S)(.+?)(?<=\\S)\\*\\*", RegexOption.DOT_MATCHES_ALL)
private val STAR_ITALIC = Regex("(?<![$W*])\\*(?![\\s*])(.+?)(?<![\\s*])\\*(?![$W*])")
private val UNDERSCORE_ITALIC = Regex("(?<![$W])_(?!\\s)(.+?)(?<!\\s)_(?![$W])")
private val STRIKE = Regex("~~(.+?)~~")
private val KEPT = Regex("\\u0000(\\d+)\\u0000")
private val EM_DASH = Regex("(?<=\\s)--(?=\\s)")
private val SCHEME = Regex("^[a-z][a-z0-9+.-]*:", RegexOption.IGNORE_CASE)
private val PATH = Regex("(?<![$W/.:~@-])(" +
    "(?:~|\\.{1,2})?/(?:[$W.@+-]+/)*[$W.@+-]*" +
    "|(?:[$W.@+-]+/)*[$W@+-][$W.@+-]*\\.[A-Za-z0-9]{1,8}" +
    "|(?:[$W.@+-]+/)+(?:[$W@+-][$W.@+-]*)?" +
    ")(?::(\\d+)(?::\\d+)?)?(?![$W/])")

private fun esc(s: String) = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
private fun escAttr(s: String) = esc(s).replace("\"", "&quot;")
private fun unesc(s: String) = s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&amp;", "&")

private class Found(val path: String, val line: Int?, val dir: Boolean)

/** widgets.resolve_path: the existing file or folder text names (it's all of it a path, at least 3 characters). */
private fun resolvePath(place: ChatPlace, text: String): Found? {
    val m = PATH.matchEntire(text.trim()) ?: return null
    val raw = m.groupValues[1]
    if (raw.length < 3) return null
    val hit = PathCheck.find(place, raw) ?: return null
    return Found(hit.path, m.groups[2]?.value?.toIntOrNull(), hit.dir)
}

private fun fileLink(f: Found, inner: String) =
    "<a kind=\"${if (f.dir) "dir" else "file"}\" href=\"${escAttr(f.path)}\" line=\"${f.line ?: ""}\">$inner</a>"

private fun linkPaths(escaped: String, place: ChatPlace?): String = if (place == null) escaped else
    PATH.replace(escaped) { m -> resolvePath(place, unesc(m.value))?.let { fileLink(it, m.value) } ?: m.value }

/** widgets.link_markup: a link to a file or folder (a target with no scheme), an artifact (read as its title) or a
 * URL. markup: the link's text as markup already. */
private fun linkMarkup(place: ChatPlace?, url: String, text: String?, markup: String?): String {
    val inner = markup ?: esc(text ?: url)
    if ("://" !in url && (!SCHEME.containsMatchIn(url) || PATH.matches(url)) || url.startsWith("file:")) {
        place ?: return inner
        val (path, line) = FileLinks.resolve(place, url) ?: return inner
        val dir = PathCheck.find(place, path)?.dir == true || path.endsWith("/")
        return "<a kind=\"${if (dir) "dir" else "file"}\" href=\"${escAttr(path)}\" line=\"${line ?: ""}\">$inner</a>"
    }
    if (ARTIFACT_URL.matches(url)) {
        val label = if (text != null && text != url) inner else esc(place?.artifacts?.firstOrNull { it.url == url }?.title
            ?.takeIf { it != Wording.References.claudeArtifact } ?: Wording.References.claudeArtifact)
        return "<a kind=\"url\" href=\"${escAttr(url)}\">$label ↗</a>"
    }
    return "<a kind=\"url\" href=\"${escAttr(url)}\">$inner</a>"
}

/** widgets._emphasis: bold, italic and strike-through in escaped text, CommonMark's way (2 * 3 * 4 isn't italic). */
private fun emphasis(text: String): String {
    var s = text
    if ('*' in s) {
        s = BOLD_ITALIC.replace(s, "<b><i>$1</i></b>")
        s = BOLD.replace(s, "<b>$1</b>")
        s = STAR_ITALIC.replace(s, "<i>$1</i>")
    }
    if ('_' in s) s = UNDERSCORE_ITALIC.replace(s, "<i>$1</i>")
    return if ("~~" in s) STRIKE.replace(s, "<s>$1</s>") else s
}

/** widgets._inline_markup: a line's inline markdown as markup (<b> <i> <s> <code> <a kind href line>). Code spans and
 * links are set aside first, as placeholders, so emphasis goes round them: **`name`** is bold code. */
internal fun inlineMarkup(text: String, place: ChatPlace?): String {
    val marks = ArrayList<String>()
    val bare = ArrayList<String>()
    fun keep(markup: String, plain: String? = null): String {
        marks.add(markup)
        bare.add(plain ?: markup)
        return "\u0000${marks.size - 1}\u0000"
    }
    var p = text
    if ('`' in p) p = CODE_SPAN.replace(p) { m ->
        val markup = "<code>" + esc(m.groupValues[1]) + "</code>"
        val hit = place?.let { resolvePath(it, m.groupValues[1]) }
        keep(if (hit != null) fileLink(hit, markup) else refLink(m.groupValues[1], markup, true, place) ?: markup, markup)
    }
    if ("](" in p) p = MD_LINK.replace(p) { m ->
        val inner = KEPT.replace(emphasis(esc(m.groupValues[1]))) { bare[it.groupValues[1].toInt()] }
        keep(linkMarkup(place, m.groupValues[2], m.groupValues[1], inner))
    }
    if ("://" in p) p = BARE_URL.replace(p) { keep(linkMarkup(place, it.groupValues[1], null, null)) }
    if (place != null) p = REF_PLAIN.replace(p) { m -> refLink(m.value, esc(m.value), false, place)?.let { keep(it) } ?: m.value }
    if ("--" in p) p = EM_DASH.replace(p, "—")
    val s = emphasis(linkPaths(esc(p), place))
    return if ('\u0000' in s) KEPT.replace(s) { marks[it.groupValues[1].toInt()] } else s
}

private val TAG = Regex("""<(/?)(b|i|s|code|a)((?: [a-z]+="[^"]*")*)>""")
private val ATTR = Regex(""" ([a-z]+)="([^"]*)"""")

/** inlineMarkup's markup as styled text: links in the accent, underlined; a file's or a folder's goes to file, a
 * URL's to open. */
internal fun annotate(markup: String, accent: Color, open: (String) -> Unit, file: (String, Int?, Boolean) -> Unit,
                      ref: (String) -> Unit = {}): AnnotatedString =
    buildAnnotatedString {
        val open_ = ArrayList<String>()
        val counts = ArrayList<String?>() // (an open link's count of matches, shown after its text while a Signal link has several)
        var at = 0
        for (m in TAG.findAll(markup)) {
            append(unesc(markup.substring(at, m.range.first)))
            at = m.range.last + 1
            val tag = m.groupValues[2]
            if (m.groupValues[1] == "/") {
                if (open_.lastOrNull() == tag) {
                    open_.removeAt(open_.size - 1)
                    if (tag == "a") counts.removeLastOrNull()?.let {
                        withStyle(SpanStyle(baselineShift = BaselineShift.Superscript, fontSize = 0.7.em)) { append(it) }
                    }
                    pop()
                }
                continue
            }
            open_.add(tag)
            when (tag) {
                "b" -> pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
                "i" -> pushStyle(SpanStyle(fontStyle = FontStyle.Italic))
                "s" -> pushStyle(SpanStyle(textDecoration = TextDecoration.LineThrough))
                "code" -> pushStyle(SpanStyle(fontFamily = FontFamily.Monospace, fontSize = Type.mono.fontSize,
                    background = Tokens.Markdown.codeSpan))
                else -> {
                    val attrs = ATTR.findAll(m.groupValues[3]).associate { it.groupValues[1] to unesc(it.groupValues[2]) }
                    val href = attrs["href"].orEmpty()
                    val kind = attrs["kind"]
                    val line = attrs["line"]?.toIntOrNull()
                    if (kind == "ref") { // (a Signal link: its logo, then its text drawn by how far its lookup got)
                        val status = attrs["status"].orEmpty()
                        attrs["icon"]?.ifEmpty { null }?.let {
                            appendInlineContent("ref-icon-$it-${if (status == "stale") "stale" else "ok"}", RefIcons.slot)
                        }
                        val colour = when (status) {
                            "pending" -> accent.copy(alpha = accent.alpha * Tokens.Signal.pendingOpacity.toFloat())
                            "not_found" -> Tokens.Signal.notFound
                            "stale" -> Tokens.Signal.stale
                            else -> accent
                        }
                        counts.add(if (status == "ambiguous") attrs["n"] else null)
                        pushLink(LinkAnnotation.Clickable(href, TextLinkStyles(SpanStyle(color = colour,
                            textDecoration = TextDecoration.Underline))) { ref(href) })
                    } else {
                        counts.add(null)
                        pushLink(LinkAnnotation.Clickable(href, TextLinkStyles(SpanStyle(color = accent,
                            textDecoration = TextDecoration.Underline))) {
                            if (kind == "url") open(href) else file(href, line, kind == "dir")
                        })
                    }
                }
            }
        }
        append(unesc(markup.substring(at)))
        while (open_.isNotEmpty()) {
            open_.removeAt(open_.size - 1)
            pop()
        }
    }

private val inlineCache = LruCache<List<Any?>, AnnotatedString>(1024)

/** A text's inline markdown (each line of it its own), as styled text; drawn again when the paths it names are
 * learned of. */
@Composable
internal fun rememberInline(text: String): AnnotatedString {
    val place = LocalChatPlace.current
    val c = LocalBaude.current
    val uri = LocalUriHandler.current
    val known = if (place != null) PathCheck.version.intValue else 0
    val signal = if (place?.sid != null) SignalNames.version.intValue * 1000 + SignalChats.version.intValue else 0
    return remember(text, place, known, signal, c.accent) {
        val key = listOf(text, place, known, signal, c.accent)
        inlineCache.get(key) ?: annotate(text.split("\n").joinToString("\n") { inlineMarkup(it, place) }, c.accent,
            { url -> try { uri.openUri(url) } catch (_: Exception) {} },
            { path, line, dir -> place?.let { FileLinks.open(Wanted(it.hostId, path, line, dir)) } },
            { name -> SignalLinks.tap(name) }).also { inlineCache.put(key, it) }
    }
}

// ------------------------------------------------------------------------------------------------------- drawn --

/** A reply's markdown as Baude's chat draws it (widgets.MarkdownView): its blocks 6 apart. under: the colour the
 * code blocks and tables sit on. */
@Composable
fun MarkdownView(text: String, color: Color = LocalBaude.current.fg, under: Color = LocalBaude.current.inside) {
    val c = LocalBaude.current
    val blocks = remember(text) { cachedBlocks(text) }
    val measure = rememberTextMeasurer()
    val density = LocalDensity.current
    val markerWidth = remember(density) { with(density) { measure.measure("00", Type.body.copy(fontWeight = FontWeight.Bold)).size.width.toDp() } }
    Column(verticalArrangement = Arrangement.spacedBy(Tokens.Markdown.block)) {
        for (b in blocks) when (b) {
            is Block.Para -> Text(rememberInline(b.lines.joinToString("\n")), style = Type.body, color = color,
                inlineContent = RefIcons.inline)
            is Block.Heading -> Text(rememberInline(b.text), color = color, inlineContent = RefIcons.inline, style = Type.body.copy(
                fontSize = Type.body.fontSize * when (b.level) { 1 -> Tokens.Markdown.h1.toFloat(); 2 -> Tokens.Markdown.h2.toFloat(); else -> 1f },
                fontWeight = FontWeight.Bold))
            is Block.Item -> Row(Modifier.padding(start = Tokens.Markdown.itemStart + Tokens.Markdown.indentStep * b.depth),
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(b.marker, style = Type.body.copy(fontWeight = FontWeight.Bold), color = color,
                    textAlign = TextAlign.End, modifier = Modifier.widthIn(min = markerWidth).alpha(Tokens.Markdown.markerOpacity.toFloat()))
                Text(rememberInline(b.text), style = Type.body, color = color, modifier = Modifier.weight(1f),
                    inlineContent = RefIcons.inline)
            }
            is Block.Quote -> Row(Modifier.height(IntrinsicSize.Min)) {
                Box(Modifier.width(Tokens.Markdown.quoteRule).fillMaxHeight()
                    .background(c.fg.copy(alpha = c.fg.alpha * Tokens.Markdown.quoteRuleOpacity.toFloat())))
                Spacer(Modifier.width(Tokens.Markdown.quotePad - Tokens.Markdown.quoteRule))
                Text(rememberInline(b.text), style = Type.body, color = color, inlineContent = RefIcons.inline,
                    modifier = Modifier.weight(1f).alpha(Tokens.Markdown.quoteOpacity.toFloat()))
            }
            Block.Rule -> Box(Modifier.padding(vertical = Tokens.Markdown.ruleGap).fillMaxWidth().height(1.dp).background(c.rule))
            is Block.Code -> CodeBlock(b.code, b.lang, under = under)
            is Block.Table -> MarkdownTable(b.rows, color, under)
            is Block.Image -> MarkdownImage(b.alt, b.src)
        }
    }
}

/** What a Bot said (and anyone's markdown): its text as Baude draws a reply's. under: what its code sits on. */
@Composable
fun Prose(text: String, color: Color = LocalBaude.current.fg, under: Color = LocalBaude.current.inside) =
    MarkdownView(text, color, under)

/** Claude's thinking: its words as a reply's, faded (.thinking). */
@Composable
fun Thinking(text: String) {
    Box(Modifier.fillMaxWidth().alpha(Tokens.Markdown.thinkingOpacity.toFloat())) { MarkdownView(text) }
}
