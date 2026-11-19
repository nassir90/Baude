package dev.baude.android

import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.outlined.AddBox
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// An Emission window's bars (emission.Chrome), as the phone's browser has them: back, forward, reload (a stop while a
// page loads), the address, a new tab and the profile, in an Adw.HeaderBar's place on the frame; under it the tabs'
// strip (Adw.TabBar's: every tab, each as wide as the others, the one in front raised with its close), a page's
// progress along its foot. The window's own close is the phone's back.

/** The bar: emission.Chrome's bar, its parts in its order. go: the address typed (activated, as its entry is). */
@Composable
fun EmissionBar(b: Browser, profile: String, go: (String) -> Unit, nav: (String) -> Unit, newTab: () -> Unit) {
    val c = LocalBaude.current
    Row(
        Modifier.fillMaxWidth().background(c.frame).statusBarsPadding().height(Gap.bar).padding(horizontal = 6.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        IconButton(Icons.AutoMirrored.Filled.KeyboardArrowLeft, "Back", enabled = b.canBack.value) { nav("back") }
        IconButton(Icons.AutoMirrored.Filled.KeyboardArrowRight, "Forward", enabled = b.canForward.value) { nav("forward") }
        if (b.loading.value) IconButton(Icons.Filled.Close, "Stop") { nav("stop") }
        else IconButton(Icons.Filled.Refresh, "Reload") { nav("reload") }
        Address(b.shownUrl.value, go, Modifier.weight(1f))
        IconButton(Icons.Outlined.AddBox, "New Tab", onClick = newTab)
        Row(Modifier.heightIn(min = Gap.composerButton).padding(horizontal = 8.dp), // (who: the profile's, the Bot's
            verticalAlignment = Alignment.CenterVertically) {                        // own, shown not changed here)
            Text(profile, style = Type.bold, color = c.fg.copy(alpha = c.fg.alpha * 0.5f), maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 96.dp))
            Icon(Icons.Filled.KeyboardArrowDown, null, tint = c.fg.copy(alpha = c.fg.alpha * 0.5f),
                modifier = Modifier.size(16.dp))
        }
    }
}

/** The address, as Chrome's entry: the page's, put back as it changes except while something's typed there. */
@Composable
private fun Address(url: String, go: (String) -> Unit, modifier: Modifier) {
    val c = LocalBaude.current
    val focus = LocalFocusManager.current
    var typing by remember { mutableStateOf(false) }
    var text by remember { mutableStateOf("") }
    if (!typing && text != url) text = url
    Box(modifier.height(Gap.composerButton).clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.1f))
        .padding(horizontal = 9.dp), contentAlignment = Alignment.CenterStart) {
        if (text.isEmpty()) Text("Search or type an address", style = Type.body, color = c.dim, maxLines = 1,
            overflow = TextOverflow.Clip)
        BasicTextField(text, { text = it }, singleLine = true, textStyle = Type.body.copy(color = c.fg),
            cursorBrush = SolidColor(c.accentBg),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go),
            keyboardActions = KeyboardActions(onGo = {
                if (text.isNotBlank()) go(text.trim())
                focus.clearFocus()
            }),
            modifier = Modifier.fillMaxWidth().onFocusChanged { typing = it.isFocused })
    }
}

/** The tabs' strip (Adw.TabBar's, autohide off: shown with one tab too; its tabs as wide as each other, never
 * narrower than TAB_MIN, the strip scrolled sideways once they don't fit), a page's progress over its foot. */
@Composable
fun TabStrip(b: Browser, pick: (String) -> Unit, close: (String) -> Unit) {
    val c = LocalBaude.current
    BoxWithConstraints(Modifier.fillMaxWidth().background(c.frame)) {
        val n = b.strip.size.coerceAtLeast(1)
        val wide = maxOf(TAB_MIN, (maxWidth - 12.dp - 6.dp * (n - 1)) / n)
        val scroll = rememberScrollState()
        val step = with(LocalDensity.current) { (wide + 6.dp).toPx() }
        val front = b.strip.indexOfFirst { it.id == b.selected.value }
        LaunchedEffect(front, n) { // (the tab in front scrolled to, as Adw.TabBar scrolls to it)
            if (front >= 0) scroll.animateScrollTo((front * step).toInt().coerceAtMost(scroll.maxValue))
        }
        Row(Modifier.fillMaxWidth().horizontalScroll(scroll).padding(horizontal = 6.dp, vertical = 3.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            for (t in b.strip) {
                val front = t.id == b.selected.value
                Row(
                    Modifier.width(wide).height(Gap.composerButton).clip(RoundedCornerShape(8.dp))
                        .then(if (front) Modifier.background(c.fg.copy(alpha = 0.1f)) else Modifier)
                        .clickable { pick(t.id) }.padding(start = 8.dp, end = 3.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    if (t.loading) CircularProgressIndicator(Modifier.size(14.dp), color = c.fg, strokeWidth = 1.5.dp)
                    else Spacer(Modifier.size(14.dp))
                    Text(t.label, style = Type.caption.copy(fontSize = 13.sp), color = c.fg, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center,
                        modifier = Modifier.weight(1f).padding(horizontal = 4.dp))
                    if (front) Box(Modifier.size(26.dp).clip(RoundedCornerShape(13.dp)).clickable { close(t.id) },
                        contentAlignment = Alignment.Center) {
                        Icon(Icons.Filled.Close, "Close Tab", tint = c.fg, modifier = Modifier.size(16.dp))
                    } else Spacer(Modifier.size(26.dp))
                }
            }
        }
        if (b.loading.value) Box(Modifier.align(Alignment.BottomStart).fillMaxWidth(b.progress.value.coerceIn(0.05f, 1f))
            .height(3.dp).background(c.accentBg)) // (the osd progress bar: over the strip's foot, the page not moved)
    }
}

private val TAB_MIN = 120.dp // (a tab's least width, as Adw.TabBar's)

/** What the pages wait on, each a card (browserpane.show_prompts'): answered here, or by the Bot. */
@Composable
fun PromptCards(b: Browser, answer: (org.json.JSONObject) -> Unit) {
    val c = LocalBaude.current
    if (b.asking.isEmpty()) return
    Column(Modifier.fillMaxWidth().padding(horizontal = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        for (p in b.prompts(null)) {
            val kind = p.optString("kind")
            val fields = remember(p.optString("id")) {
                when (kind) {
                    "prompt" -> linkedMapOf("text" to mutableStateOf(p.optString("default")))
                    "sign_in" -> linkedMapOf("user" to mutableStateOf(""), "password" to mutableStateOf(""))
                    else -> linkedMapOf()
                }
            }
            Column(Modifier.padding(top = 4.dp).fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card)
                .padding(horizontal = 8.dp, vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                val text = when (kind) {
                    "sign_in" -> "${p.optString("host").ifBlank { "A site" }} asks to sign in" +
                        p.optString("realm").let { if (it.isNotBlank()) " (“$it”)" else "" }
                    "files" -> "The page asks for files to upload"
                    else -> p.optString("message")
                }
                Text(text, style = Type.body, color = c.fg)
                for ((k, v) in fields) Box(Modifier.fillMaxWidth().height(Gap.composerButton).clip(RoundedCornerShape(8.dp))
                    .background(c.fg.copy(alpha = 0.1f)).padding(horizontal = 9.dp), contentAlignment = Alignment.CenterStart) {
                    if (v.value.isEmpty() && k != "text") Text(if (k == "user") "User name" else "Password", style = Type.body,
                        color = c.dim)
                    BasicTextField(v.value, { v.value = it }, singleLine = true, textStyle = Type.body.copy(color = c.fg),
                        visualTransformation = if (k == "password") androidx.compose.ui.text.input.PasswordVisualTransformation()
                        else androidx.compose.ui.text.input.VisualTransformation.None,
                        cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth())
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp, Alignment.End)) {
                    if (kind != "alert") Button("Cancel") { answer(org.json.JSONObject().put("id", p.optString("id")).put("accept", false)) }
                    if (kind != "files") Button("OK", suggested = true) {
                        answer(org.json.JSONObject().put("id", p.optString("id")).put("accept", true).apply {
                            for ((k, v) in fields) put(k, v.value)
                        })
                    }
                }
            }
        }
    }
}
