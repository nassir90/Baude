package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.onLongClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.DpOffset
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** Meta's forms as its conversation has them (metachat.MetaChat.form_card): each a card in the reply that brought it
 * up, filled live by Meta (fill_form), submitted only by you; the one whose place has scrolled up past the top hovers
 * there, pinned, till its place is back in view. */
object MetaForms {
    const val PIN_MARGIN = 8 // dp: a pinned form hovers this far from the page's top (MetaChat.PIN_MARGIN)
    val pinned = mutableStateOf<String?>(null)    // (the form hovering over the conversation: its id)
    val heights = mutableStateMapOf<String, Int>() // (each form's card's height, px: its gap while it's pinned)
    val focused = mutableStateOf<String?>(null)   // (the form whose card has a field you're in: its id)

    /** The forms a line of Meta's conversation holds, by id: a call's (what it showed), or a "rows" line's. */
    fun ids(line: Line): List<String> = when (line.kind) {
        "metacall" -> Meta.calls[line.text]?.shown?.mapNotNull { it.str("form_id") }.orEmpty()
        "rows" -> listOfNotNull(try { JSONObject(line.text).str("form_id") } catch (_: Exception) { null })
        else -> emptyList()
    }
}

private val LocalPinned = compositionLocalOf { false }

/** A form's place in its reply: its card, or, while it's pinned over the conversation, a gap its height. */
@Composable
fun MetaFormSlot(id: String) {
    if (MetaForms.pinned.value == id) {
        val h = MetaForms.heights[id] ?: 0
        Spacer(Modifier.fillMaxWidth().height(with(LocalDensity.current) { h.toDp() }))
    } else MetaFormCard(id)
}

/** The form pinned over the conversation's top: the last one not submitted, and pinned, whose place has gone up past
 * the list's top (MetaChat._pin_forms); none once its place is back in view. While you're in a field of a form's
 * card, what's pinned stays as it is (the keyboard moving the list doesn't take the card from under you). pad: the
 * conversation's sides. */
@Composable
fun BoxScope.MetaPinnedForm(list: LazyListState, pad: Dp) {
    val margin = with(LocalDensity.current) { MetaForms.PIN_MARGIN.dp.roundToPx() }
    val chat = Meta.chat
    val want by remember(chat, list) { derivedStateOf {
        if (MetaForms.focused.value != null) return@derivedStateOf MetaForms.pinned.value
        val info = list.layoutInfo
        var pick: String? = null
        for ((i, l) in chat.lines.withIndex()) for (id in MetaForms.ids(l)) {
            val f = MetaTools.forms[id] ?: continue
            if (f.optString("state", "open") != "open" || !f.optBoolean("pinned", true)) continue
            val item = info.visibleItemsInfo.firstOrNull { it.index == i }
            if (if (item != null) item.offset - info.viewportStartOffset < margin else i < list.firstVisibleItemIndex) pick = id
        }
        pick
    } }
    SideEffect { if (MetaForms.pinned.value != want) MetaForms.pinned.value = want }
    val id = want ?: return
    CompositionLocalProvider(LocalPinned provides true) {
        Box(Modifier.align(Alignment.TopCenter).padding(top = MetaForms.PIN_MARGIN.dp, start = pad, end = pad)) { MetaFormCard(id) }
    }
}

/** A form's card, as its form is now: what Meta fills goes into its fields where they are. */
@Composable
fun MetaFormCard(id: String) {
    val f = MetaTools.forms[id] ?: return
    androidx.compose.runtime.key(id) {
        when (f.optString("form")) {
            "add_host" -> AddHostForm(f)
            "new_group" -> NewGroupForm(f)
            "spawn_agent" -> SpawnAgentForm(f)
            else -> Text(Wording.Meta.formUnavailable(form = f.optString("form").replace('_', ' '), why = "unknown form"),
                style = Type.caption, color = LocalBaude.current.dim)
        }
    }
}

/** A form's field's value, as its form keeps it. */
fun formValue(f: JSONObject, name: String): String = f.optJSONObject("values")?.optString(name).orEmpty()

/** A form's field set (your edit, or a pick), kept in its form as it's made (MetaTools.edited). */
fun setFormValues(id: String, vararg set: Pair<String, String>) {
    val v = JSONObject(MetaTools.forms[id]?.optJSONObject("values")?.toString() ?: "{}")
    for ((k, x) in set) v.put(k, x)
    MetaTools.edited(id, v)
}

/** A form's text field's text and cursor: as you leave them, and set to what's in the form, the cursor at its end,
 * once that's something else (Meta's filled it). set: your edit, kept in the form. */
@Composable
fun formText(f: JSONObject, name: String): Pair<TextFieldValue, (TextFieldValue) -> Unit> {
    val id = f.optString("id")
    val kept = formValue(f, name)
    var text by remember(id, name) { mutableStateOf(TextFieldValue(kept, TextRange(kept.length))) }
    val now = if (text.text == kept) text else TextFieldValue(kept, TextRange(kept.length))
    return now to { t: TextFieldValue ->
        text = t
        if (t.text != kept) setFormValues(id, name to t.text)
    }
}

/** What a form's card has round its fields (form_card): its heading, what it says, the fields, what's wrong with them
 * (once something's filled, or a submit failed), what came of it once it's submitted or cancelled, and Cancel and its
 * own verb at the end; submitted or cancelled, its fields are shut and its buttons gone, and it stays in its place.
 * submit: what's wrong, or null once it's submitted (MetaTools.closed). Long-pressed: Pin or Unpin, while it's open. */
@Composable
fun FormFrame(f: JSONObject, wrong: String?, body: String? = null, submit: () -> String?, fields: @Composable () -> Unit) {
    val c = LocalBaude.current
    val id = f.optString("id")
    val def = MetaTools.FORMS[f.optString("form")]
    val open = f.optString("state", "open") == "open"
    val pinned = f.optBoolean("pinned", true)
    val filled = f.optJSONObject("values")?.let { v -> v.keys().asSequence().any { v.optString(it).isNotBlank() } } == true
    val error = f.str("error")
    val problem = if (open && (filled || error != null)) error ?: wrong else null
    val said = when (f.optString("state")) {
        "submitted" -> if (def?.said != false) Wording.Meta.formDone(said = f.str("said") ?: def?.verb.orEmpty()) else null
        "cancelled" -> Wording.Meta.cancelled
        else -> null
    }
    var menu by remember { mutableStateOf(false) }
    var pressed by remember { mutableStateOf(Offset.Zero) }
    var height by remember { mutableStateOf(0) }
    val shape = RoundedCornerShape(Gap.cardRadius)
    val hovering = LocalPinned.current
    val focus = LocalFocusManager.current
    LaunchedEffect(open) { if (!open && MetaForms.focused.value == id) focus.clearFocus() } // (shut: you're out of it)
    Box(Modifier.fillMaxWidth().onSizeChanged {
        height = it.height
        MetaForms.heights[id] = it.height
    }.onFocusChanged {
        if (it.hasFocus) MetaForms.focused.value = id else if (MetaForms.focused.value == id) MetaForms.focused.value = null
    }.focusGroup()) {
        Column(Modifier.fillMaxWidth()
            .then(if (hovering) Modifier.shadow(6.dp, shape).background(c.inside, shape) else Modifier)
            .border(1.dp, c.rule, shape)
            .then(if (open) Modifier.pointerInput(id) { detectTapGestures(onLongPress = { pressed = it; menu = true }) }
                .semantics { onLongClick(if (pinned) Wording.Meta.unpin else Wording.Meta.pin) { menu = true; true } } else Modifier)
            .padding(horizontal = Gap.cardH, vertical = Gap.cardV),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(def?.heading ?: f.optString("form"), style = Type.body.copy(fontWeight = Tokens.Font.bold), color = c.fg)
            if (!body.isNullOrEmpty()) Text(body, style = Type.caption, color = c.dim)
            Box(if (open) Modifier else Modifier.alpha(0.5f).shut()) { fields() }
            if (problem != null) Text(problem, style = Type.caption, color = c.destructive)
            if (!open && said != null) Text(said, style = Type.caption, color = c.dim)
            if (open) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp, Alignment.End)) {
                Button(Wording.Common.cancel) { MetaTools.closed(id, null) }
                Button(def?.verb ?: Wording.Meta.submit, suggested = true, enabled = wrong == null) {
                    submit()?.let { why -> MetaTools.forms[id]?.let { MetaTools.forms[id] = JSONObject(it.toString()).put("error", why) } }
                }
            }
        }
        val d = LocalDensity.current
        Popover(menu && open, { menu = false }, with(d) { DpOffset(pressed.x.toDp(), (pressed.y - height).toDp()) }) {
            MenuChoice(if (pinned) Wording.Meta.unpin else Wording.Meta.pin, false) {
                menu = false
                MetaTools.forms[id]?.let { MetaTools.forms[id] = JSONObject(it.toString()).put("pinned", !pinned) }
                Meta.save()
            }
        }
    }
}

/** What's in it taking no taps (a submitted or cancelled form's fields): a press there does nothing, a drag from it
 * scrolls the conversation. */
private fun Modifier.shut(): Modifier = pointerInput(Unit) {
    awaitEachGesture {
        val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
        down.consume()
        var dragged = false
        while (true) {
            val e = awaitPointerEvent(PointerEventPass.Initial).changes.firstOrNull { it.id == down.id } ?: break
            if ((e.position - down.position).getDistance() > viewConfiguration.touchSlop) dragged = true
            if (!e.pressed) {
                if (!dragged) e.consume()
                break
            }
        }
    }
}

/** Meta's add_host form (Baude's host_form): where ssh goes. */
@Composable
private fun AddHostForm(f: JSONObject) {
    val id = f.optString("id")
    val (dest, setDest) = formText(f, "destination")
    val wrong = MetaTools.wrong("add_host", JSONObject().put("destination", dest.text))
    fun go(): String? {
        if (wrong != null) return wrong
        addHost(dest.text) ?: return wrong
        MetaTools.closed(id, "looking for Baude on ${dest.text.trim()}")
        return null
    }
    FormFrame(f, wrong, submit = ::go) { AddHostFields(dest, setDest, focus = false) { go() } }
}

/** Meta's new_group form (Baude's group_form): the group's name, made once you press Create. */
@Composable
private fun NewGroupForm(f: JSONObject) {
    val id = f.optString("id")
    val (field, setField) = formText(f, "name")
    val name = field.text
    val wrong = MetaTools.wrong("new_group", JSONObject().put("name", name))
    FormFrame(f, wrong, body = "A list of chats of its own, picked in ⋮ → Groups.", submit = {
        wrong ?: run {
            val g = name.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
            HomePrefs.made.add(g)
            HomePrefs.save()
            MetaTools.closed(id, "made group $g")
            null
        }
    }) { Field(field, setField, Wording.MetaApp.itsName) }
}
