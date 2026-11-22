package dev.baude.android

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

/** Code shown read-only (codeview.CodeView): highlighted by file's name, each line numbered in a dim gutter, selectable,
 * scrolling down and (unless wrap) sideways; links: names in it that open something, underlined; follow: it keeps to
 * the end as text comes, and says (onFollow) when you scroll away from the end or come back to it; dim: greyed. */
@Composable
fun CodePane(text: String, file: String, modifier: Modifier = Modifier, numbered: Boolean = true, wrap: Boolean = false,
             links: List<Msg.KubeRef> = emptyList(), open: (Msg.KubeRef) -> Unit = {}, follow: Boolean = false,
             onFollow: (Boolean) -> Unit = {}, dim: Boolean = false) {
    val c = LocalBaude.current
    val dark = c.inside.luminance() < 0.5f
    var spans by remember { mutableStateOf<Spans>(emptyList()) }
    LaunchedEffect(text, file) { // (a text that grows keeps its colours till new ones are made, a moment after it's still)
        if (spans.isNotEmpty()) delay(150L + minOf(text.length, 20_000) / 40)
        spans = withContext(Dispatchers.Default) { colour(text, null, file, false, null).firstOrNull().orEmpty() }
    }
    val shown = remember(text, spans, dark, c.fg) { styled(text, spans, c, dark) }
    val starts = remember(text) {
        val s = ArrayList<Int>()
        s.add(0)
        for (i in text.indices) if (text[i] == '\n') s.add(i + 1)
        s.toIntArray()
    }
    val byLine = remember(links) { links.groupBy { it.line?.toInt() ?: -1 } }
    val measure = rememberTextMeasurer()
    val density = LocalDensity.current
    val gutter = if (!numbered) 0.dp else with(density) {
        measure.measure("0".repeat(starts.size.toString().length), Type.mono).size.width.toDp()
    }
    val state = rememberLazyListState()
    LaunchedEffect(text, follow) {
        if (follow && starts.isNotEmpty()) state.scrollToItem(starts.size - 1)
    }
    LaunchedEffect(state) {
        snapshotFlow { (state.isScrollInProgress && state.lastScrolledBackward) to state.canScrollForward }
            .collect { (away, more) -> if (!more) onFollow(true) else if (away) onFollow(false) }
    }
    val linkStyle = SpanStyle(color = c.accent, textDecoration = TextDecoration.Underline)
    SelectionContainer(modifier.alpha(if (dim) 0.4f else 1f)) {
        LazyColumn(Modifier.then(if (wrap) Modifier else Modifier.horizontalScroll(rememberScrollState())), state) {
            items(starts.size) { i ->
                val start = starts[i]
                val end = if (i + 1 < starts.size) starts[i + 1] - 1 else text.length
                val line: AnnotatedString = remember(shown, i, byLine) {
                    buildAnnotatedString {
                        append(shown.subSequence(start, end))
                        for (r in byLine[i].orEmpty()) {
                            val a = r.column?.toInt() ?: continue
                            val b = minOf(a + (r.length?.toInt() ?: 0), end - start)
                            if (a in 0 until b) addLink(LinkAnnotation.Clickable(r.name.orEmpty(), TextLinkStyles(linkStyle)) { open(r) }, a, b)
                        }
                    }
                }
                Row(Modifier.padding(horizontal = 8.dp)) {
                    if (numbered) {
                        Text((i + 1).toString(), style = Type.mono, color = c.dim, textAlign = TextAlign.End, maxLines = 1,
                            softWrap = false, modifier = Modifier.width(gutter))
                        Spacer(Modifier.width(8.dp))
                    }
                    Text(line, style = Type.mono, color = c.fg, softWrap = wrap)
                }
            }
        }
    }
}
