package dev.baude.android

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.text.InputType
import android.view.GestureDetector
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.inputmethod.BaseInputConnection
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputMethodManager
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView

/** What a key the phone's keyboard lacks sends (xterm's: an arrow's in the application mode a program can ask for). */
private fun keyBytes(t: HostTerm, key: String): String = when (key) {
    "esc" -> "\u001b"
    "tab" -> "\t"
    "up", "down", "right", "left" -> (if (t.screen.appCursorKeys) "\u001bO" else "\u001b[") +
        mapOf("up" to "A", "down" to "B", "right" to "C", "left" to "D").getValue(key)
    else -> ""
}

/** A host's terminal drawn (as Multiplex's VTE is: its text in the window's light or dark colours, Baude's code
 * font), sized to the room it's given (the host's program told), what's typed on the phone's keyboard sent to it; under
 * it a row of the keys a phone's keyboard lacks: Esc, Tab, Ctrl (held for the next key), the arrows, and the
 * keyboard. While it's shown its connection's made; the app put away lets go of it (the program goes on there), and
 * coming back attaches to it again. */
@Composable
fun TerminalPane(t: HostTerm, modifier: Modifier = Modifier) {
    val c = LocalBaude.current
    val dark = c.dark
    val px = with(LocalDensity.current) { Type.mono.fontSize.toPx() }
    val view = remember(t) { arrayOfNulls<TermView>(1) }
    val activity = androidx.compose.ui.platform.LocalContext.current as? android.app.Activity
    DisposableEffect(t) {
        t.wanted = true
        Terms.add(t)
        t.open()
        onDispose {
            t.wanted = false
            if (activity?.isChangingConfigurations != true) t.leave() // (turned: the page made again keeps it)
        }
    }
    Column(modifier) {
        val drawn = t.drawn // (read here: a change draws it again)
        AndroidView(
            factory = { ctx -> TermView(ctx, t).also { view[0] = it } },
            update = { v ->
                v.colours(if (dark) 0xffe8e6e3.toInt() else 0xff1f1f1f.toInt(), if (dark) 0xff1e1e1e.toInt() else 0xffffffff.toInt(),
                    c.accentBg.toArgb())
                v.textPx(px)
                v.shown(drawn)
            },
            modifier = Modifier.weight(1f).fillMaxWidth().semantics { contentDescription = t.screen.text() },
        )
        KeysRow(t) { view[0]?.keyboard() }
    }
}

/** The keys a phone's keyboard lacks, in a row under the terminal (phone-only: the desktop has a keyboard). */
@Composable
private fun KeysRow(t: HostTerm, keyboard: () -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().background(c.frame).horizontalScroll(rememberScrollState()).padding(horizontal = 4.dp, vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        KeyButton(Wording.Multiplex.keyEsc) { t.send(keyBytes(t, "esc").toByteArray()) }
        KeyButton(Wording.Multiplex.keyTab) { t.send(keyBytes(t, "tab").toByteArray()) }
        KeyButton(Wording.Multiplex.keyCtrl, on = t.ctrl) { t.ctrl = !t.ctrl }
        for ((key, icon, label) in listOf(Triple("left", Adwaita.GoPrevious, Wording.Multiplex.keyLeft),
            Triple("up", Adwaita.GoUp, Wording.Multiplex.keyUp), Triple("down", Adwaita.GoDown, Wording.Multiplex.keyDown),
            Triple("right", Adwaita.GoNext, Wording.Multiplex.keyRight))) {
            KeyButton(label, icon = icon) { t.send(keyBytes(t, key).toByteArray()) }
        }
        KeyButton(Wording.Multiplex.keyboard, icon = Adwaita.InputKeyboard, onClick = keyboard)
    }
}

@Composable
private fun KeyButton(label: String, icon: ImageVector? = null, on: Boolean = false, onClick: () -> Unit) {
    val c = LocalBaude.current
    Box(Modifier.heightIn(min = 34.dp).clip(RoundedCornerShape(6.dp)).background(if (on) c.fg.copy(alpha = 0.16f) else c.raised)
        .clickable(onClick = onClick).padding(horizontal = 12.dp), contentAlignment = Alignment.Center) {
        if (icon != null) Icon(icon, label, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        else Text(label, style = Type.bold, color = c.fg, maxLines = 1)
    }
}

/** ☰ → Terminal (the phone's ⋮ in a chat): a terminal on the chat's host, its login shell, filling the page under its
 * title (the program's, " (ended)" once it has). Back leaves it running there, to come back to; × ends it. */
@Composable
fun TerminalScreen(hostId: String, back: () -> Unit) {
    val c = LocalBaude.current
    val t = remember(hostId) { Terms.plain(hostId) }
    BackHandler(onBack = back)
    Column(Modifier.fillMaxSize().background(c.frame)) {
        val title = t.title.ifBlank { Wording.Multiplex.terminalMenu } + if (t.ended != null) " (${Wording.Multiplex.ended})" else ""
        Bar(title, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) }, end = {
            IconButton(Adwaita.WindowClose, Wording.Multiplex.close) { t.close(); back() }
        })
        Box(Modifier.weight(1f).navigationBarsPadding().imePadding().inside(c)) {
            Column(Modifier.fillMaxSize()) {
                t.said?.let { GreyBanner(it) }
                TerminalPane(t, Modifier.weight(1f))
            }
        }
    }
}

/** The cells of a host terminal's screen drawn, and the phone's keyboard's keys taken (hardware ones too): a tap
 * brings up the keyboard, a drag up or down goes through the lines scrolled off the top. */
class TermView(ctx: Context, private val t: HostTerm) : View(ctx) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = CodeFont.typeface }
    private var fgDefault = 0xff1f1f1f.toInt()
    private var bgDefault = 0xffffffff.toInt()
    private var cursorColour = 0xff3584e4.toInt()
    private var cellW = 1f
    private var cellH = 1f
    private var ascent = 0f
    private var back = 0 // (lines scrolled back into the scrollback: 0 at the screen)
    private var dragged = 0f
    private var lastDrawn = -1L

    init {
        isFocusable = true
        isFocusableInTouchMode = true
        textPx(36f)
    }

    fun colours(fg: Int, bg: Int, cursor: Int) {
        if (fg == fgDefault && bg == bgDefault && cursor == cursorColour) return
        fgDefault = fg; bgDefault = bg; cursorColour = cursor
        invalidate()
    }

    fun textPx(px: Float) {
        if (px == paint.textSize) return
        paint.textSize = px
        val m = paint.fontMetrics
        cellW = paint.measureText("M").coerceAtLeast(1f)
        cellH = (m.descent - m.ascent).coerceAtLeast(1f)
        ascent = -m.ascent
        fit()
        invalidate()
    }

    fun shown(drawn: Long) {
        if (drawn == lastDrawn) return
        lastDrawn = drawn
        back = back.coerceAtMost(t.screen.scrollback.size)
        invalidate()
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) = fit()

    private fun fit() {
        if (width <= 0 || height <= 0) return
        t.resize((width / cellW).toInt().coerceAtLeast(2), (height / cellH).toInt().coerceAtLeast(1))
    }

    fun keyboard() {
        requestFocus()
        context.getSystemService(InputMethodManager::class.java)?.showSoftInput(this, 0)
    }

    private val gestures = GestureDetector(ctx, object : GestureDetector.SimpleOnGestureListener() {
        override fun onDown(e: MotionEvent) = true
        override fun onSingleTapUp(e: MotionEvent): Boolean {
            keyboard()
            return true
        }
        override fun onScroll(e1: MotionEvent?, e2: MotionEvent, dx: Float, dy: Float): Boolean {
            if (t.screen.altScreen) return true
            dragged -= dy
            val lines = (dragged / cellH).toInt()
            if (lines != 0) {
                dragged -= lines * cellH
                back = (back + lines).coerceIn(0, t.screen.scrollback.size)
                invalidate()
            }
            return true
        }
    })

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_DOWN) parent?.requestDisallowInterceptTouchEvent(true)
        return gestures.onTouchEvent(event) || super.onTouchEvent(event)
    }

    override fun onDraw(canvas: Canvas) {
        val s = t.screen
        paint.color = bgDefault
        canvas.drawRect(0f, 0f, width.toFloat(), height.toFloat(), paint)
        val screen = s.screen
        val sb = s.scrollback
        val rows = s.rows
        for (r in 0 until rows) {
            val at = r - back
            val line = if (at >= 0) screen.getOrNull(at) else sb.getOrNull(sb.size + at)
            line ?: continue
            drawLine(canvas, line, r * cellH, if (at == s.cursorRow && back == 0 && s.cursorShown) s.cursorCol else -1)
        }
    }

    private fun colour(c: Int, fallback: Int): Int = when {
        c == TermEmulator.DEFAULT -> fallback
        c and TermEmulator.TRUE != 0 -> 0xff000000.toInt() or (c and 0xffffff)
        else -> 0xff000000.toInt() or TermEmulator.indexed(c)
    }

    private val chars = StringBuilder()

    private fun drawLine(canvas: Canvas, line: TermEmulator.Line, y: Float, cursor: Int) {
        val n = minOf(line.size(), t.screen.columns)
        var i = 0
        while (i < n) {
            val look = line.look[i].toInt()
            var fg = colour(line.fg[i], fgDefault)
            var bg = colour(line.bg[i], bgDefault)
            if (look and TermEmulator.INVERSE != 0) { val x = fg; fg = bg; bg = x }
            if (i == cursor) { bg = cursorColour; fg = bgDefault }
            val cp = line.ch[i]
            val wide = i + 1 < n && line.ch[i + 1] == TermEmulator.WIDE_TAIL
            val w = if (wide) 2 else 1
            val x = i * cellW
            if (bg != bgDefault) {
                paint.color = bg
                canvas.drawRect(x, y, x + w * cellW, y + cellH, paint)
            }
            if (cp > 0 && look and TermEmulator.HIDDEN == 0) {
                paint.color = if (look and TermEmulator.DIM != 0) (fg and 0x00ffffff) or 0x99000000.toInt() else fg
                paint.isFakeBoldText = look and TermEmulator.BOLD != 0
                paint.textSkewX = if (look and TermEmulator.ITALIC != 0) -0.2f else 0f
                paint.isUnderlineText = look and TermEmulator.UNDERLINE != 0
                paint.isStrikeThruText = look and TermEmulator.STRIKE != 0
                chars.setLength(0)
                chars.appendCodePoint(cp)
                canvas.drawText(chars, 0, chars.length, x, y + ascent, paint)
            }
            i += w
        }
        if (cursor >= n && cursor < t.screen.columns) {
            paint.color = cursorColour
            canvas.drawRect(cursor * cellW, y, (cursor + 1) * cellW, y + cellH, paint)
        }
    }

    // ---- the keyboard
    override fun onCheckIsTextEditor() = true

    override fun onCreateInputConnection(out: EditorInfo): InputConnection {
        // (as a password is typed: no suggestions or corrections held back, each key sent as it's pressed)
        out.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD or
            InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        out.imeOptions = EditorInfo.IME_FLAG_NO_FULLSCREEN or EditorInfo.IME_FLAG_NO_EXTRACT_UI or EditorInfo.IME_ACTION_NONE
        return object : BaseInputConnection(this, true) {
            private fun flush() {
                val e = editable ?: return
                val text = e.toString()
                if (text.isNotEmpty()) typed(text)
                e.clear()
            }

            override fun commitText(text: CharSequence?, newCursorPosition: Int): Boolean {
                super.commitText(text, newCursorPosition)
                flush()
                return true
            }

            override fun finishComposingText(): Boolean {
                super.finishComposingText()
                flush()
                return true
            }

            override fun deleteSurroundingText(beforeLength: Int, afterLength: Int): Boolean {
                if ((editable?.length ?: 0) == 0) repeat(beforeLength.coerceAtLeast(1)) { t.send(byteArrayOf(0x7f)) }
                return super.deleteSurroundingText(beforeLength, afterLength)
            }

            override fun sendKeyEvent(event: KeyEvent): Boolean {
                if (event.action == KeyEvent.ACTION_DOWN) onKeyDown(event.keyCode, event)
                return true
            }
        }
    }

    private fun typed(text: String) {
        back = 0
        t.type(text.replace('\n', '\r'))
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        val app = t.screen.appCursorKeys
        val csi = if (app) "\u001bO" else "\u001b["
        val seq = when (keyCode) {
            KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_NUMPAD_ENTER -> "\r"
            KeyEvent.KEYCODE_DEL -> "\u007f"
            KeyEvent.KEYCODE_FORWARD_DEL -> "\u001b[3~"
            KeyEvent.KEYCODE_TAB -> if (event.isShiftPressed) "\u001b[Z" else "\t"
            KeyEvent.KEYCODE_ESCAPE -> "\u001b"
            KeyEvent.KEYCODE_DPAD_UP -> csi + "A"
            KeyEvent.KEYCODE_DPAD_DOWN -> csi + "B"
            KeyEvent.KEYCODE_DPAD_RIGHT -> csi + "C"
            KeyEvent.KEYCODE_DPAD_LEFT -> csi + "D"
            KeyEvent.KEYCODE_MOVE_HOME -> if (app) "\u001bOH" else "\u001b[H"
            KeyEvent.KEYCODE_MOVE_END -> if (app) "\u001bOF" else "\u001b[F"
            KeyEvent.KEYCODE_PAGE_UP -> "\u001b[5~"
            KeyEvent.KEYCODE_PAGE_DOWN -> "\u001b[6~"
            KeyEvent.KEYCODE_INSERT -> "\u001b[2~"
            else -> null
        }
        if (seq != null) {
            back = 0
            t.ctrl = false
            t.send(((if (event.isAltPressed) "\u001b" else "") + seq).toByteArray())
            return true
        }
        if (keyCode == KeyEvent.KEYCODE_BACK) return super.onKeyDown(keyCode, event)
        val ch = event.getUnicodeChar(event.metaState and (KeyEvent.META_CTRL_MASK or KeyEvent.META_ALT_MASK).inv())
        if (ch == 0) return super.onKeyDown(keyCode, event)
        if (event.isCtrlPressed) t.ctrl = true
        back = 0
        val text = String(Character.toChars(ch))
        if (event.isAltPressed) t.send(byteArrayOf(0x1b))
        t.type(text)
        return true
    }
}
