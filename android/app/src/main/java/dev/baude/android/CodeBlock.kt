package dev.baude.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

/** How tall a code block may be before it scrolls inside itself: two thirds of the chat's visible height, 160 at
 * least (CodeBlock.set_max_height). The chat's page sets it. */
object CodeBlocks {
    var maxHeight by mutableStateOf(600.dp)

    fun forChat(visible: Dp): Dp = maxOf(Tokens.Markdown.codeMin, visible * Tokens.Markdown.codeHeight.toFloat())
}

/** An edit's hunk as CodeBlock's diff lines (widgets.diff_block): what it replaced, then what it put in. */
fun diffBlock(old: String, new: String): List<Pair<String, String>> =
    (splitLines(old).map { "del" to it } + splitLines(new).map { "add" to it }).ifEmpty { listOf("ctx" to "") }

private fun splitLines(s: String) = if (s.isEmpty()) emptyList() else s.removeSuffix("\n").lines()

private typealias Spans = List<Triple<Int, Int, Tok>>

/** The spans to colour a block by: for plain code one list; for a diff one for each of its lines. */
private fun colour(code: String, syntax: String?, path: String, numbered: Boolean, diff: List<Pair<String, String>>?): List<Spans> {
    if (diff == null) return listOf(if (numbered) numberedSpans(code, path) else codeSpans(code, path, syntax.orEmpty()))
    val out = MutableList<Spans>(diff.size) { emptyList() }
    for (kind in listOf("del", "add", "ctx")) {
        val at = diff.indices.filter { diff[it].first == kind }
        if (at.isEmpty()) continue
        val text = at.joinToString("\n") { diff[it].second }
        val per = lineSpans(text, codeSpans(text, path, syntax.orEmpty()))
        at.forEachIndexed { k, i -> out[i] = per.getOrElse(k) { emptyList() } }
    }
    return out
}

/** A code block as Baude's chat draws it: a raised panel, its code highlighted (syntax: a language's name; else
 * path's file type), a corner over its first line with its language and a copy button, scrolling sideways (wrap:
 * wrapped instead) and, past CodeBlocks.maxHeight, inside itself. numbered: a Read's output. diff: an edit's lines
 * (diffBlock), each on its red or green. under: what the panel sits on. */
@Composable
fun CodeBlock(code: String, lang: String = "", syntax: String? = lang.ifBlank { null }, path: String = "",
              numbered: Boolean = false, diff: List<Pair<String, String>>? = null, wrap: Boolean = false,
              under: Color = LocalBaude.current.inside) {
    val c = LocalBaude.current
    val dark = c.inside.luminance() < 0.5f
    val panel = c.raised.compositeOver(under)
    val key = listOf(code, syntax, path, numbered, diff)
    var spans by remember { mutableStateOf(if (code.length <= 4000) colour(code, syntax, path, numbered, diff) else emptyList()) }
    var doneFor by remember { mutableStateOf<Any?>(if (code.length <= 4000) key else null) }
    LaunchedEffect(key) { // (a block that grows keeps its colours till new ones are made, a moment after it's still)
        if (doneFor == key) return@LaunchedEffect
        if (spans.isNotEmpty()) delay(150L + minOf(code.length, 20_000) / 40)
        spans = withContext(Dispatchers.Default) { colour(code, syntax, path, numbered, diff) }
        doneFor = key
    }
    val ctx = LocalContext.current
    val shape = RoundedCornerShape(Gap.codeRadius)
    BoxWithConstraints(Modifier.fillMaxWidth().clip(shape).background(panel)) {
        val viewport = maxWidth
        Box(Modifier.heightIn(max = CodeBlocks.maxHeight).verticalScroll(rememberScrollState())) {
            if (diff != null) {
                Column(Modifier.then(if (wrap) Modifier.fillMaxWidth() else Modifier.horizontalScroll(rememberScrollState()))
                    .padding(vertical = 6.dp)) {
                    Column(Modifier.then(if (wrap) Modifier.fillMaxWidth() else Modifier.defaultMinSize(minWidth = viewport)
                        .width(IntrinsicSize.Max))) {
                        diff.forEachIndexed { i, (kind, line) ->
                            val back = when (kind) {
                                "del" -> if (dark) Tokens.dark.diff.removedLine else Tokens.light.diff.removedLine
                                "add" -> if (dark) Tokens.dark.diff.addedLine else Tokens.light.diff.addedLine
                                else -> Color.Transparent
                            }
                            Text(highlighted(line, spans.getOrElse(i) { emptyList() }, c, dark), style = Type.mono, color = c.fg,
                                softWrap = wrap, modifier = Modifier.fillMaxWidth().background(back).padding(horizontal = 8.dp))
                        }
                    }
                }
            } else {
                val text: AnnotatedString = highlighted(code, spans.firstOrNull().orEmpty(), c, dark)
                Text(text, style = Type.mono, color = c.fg, softWrap = wrap,
                    modifier = Modifier.then(if (wrap) Modifier.fillMaxWidth() else Modifier.horizontalScroll(rememberScrollState()))
                        .padding(horizontal = 8.dp, vertical = 6.dp))
            }
        }
        val tag = syntax.orEmpty().ifBlank { lang }
        Row(Modifier.align(Alignment.TopEnd).clip(RoundedCornerShape(topEnd = Gap.codeRadius))
            .height(IntrinsicSize.Min)) {
            Box(Modifier.width(Tokens.Markdown.cornerFeather).fillMaxHeight()
                .background(Brush.horizontalGradient(listOf(panel.copy(alpha = 0f), panel))))
            Row(Modifier.background(panel).padding(start = 4.dp, end = 4.dp, top = 3.dp, bottom = 3.dp),
                verticalAlignment = Alignment.CenterVertically) {
                if (tag.isNotBlank()) {
                    Text(tag, style = Type.body, color = c.fg.copy(alpha = c.fg.alpha * Tokens.Markdown.codeTagOpacity.toFloat()),
                        fontSize = Type.body.fontSize * Tokens.Markdown.codeTag.toFloat())
                    Box(Modifier.width(4.dp))
                }
                Box(Modifier.size(Tokens.Markdown.codeCopy).clip(RoundedCornerShape(4.dp)).clickable {
                    ctx.getSystemService(Context.CLIPBOARD_SERVICE).let { it as ClipboardManager }
                        .setPrimaryClip(ClipData.newPlainText("code", code))
                }, contentAlignment = Alignment.Center) {
                    Icon(Adwaita.EditCopy, Wording.Common.copy, modifier = Modifier.size(16.dp),
                        tint = c.fg.copy(alpha = c.fg.alpha * Tokens.Markdown.codeCopyOpacity.toFloat()))
                }
            }
        }
    }
}
