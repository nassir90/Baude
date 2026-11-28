package dev.baude.android

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.os.SystemClock
import android.view.View
import kotlin.math.PI
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin

/** The Bot's pointer as it's drawn over its page, never in it (emissionpointer.BotPointer's, as Baude and Emission draw
 * it on a desktop): what its tools do with the mouse (hover, click, drag, scroll; where, the page's CSS pixels) played
 * in turn. It glides there; a click presses it (a ring spreads from it, one each for a double click's presses), a drag
 * takes it pressed along its path, a scroll shows the wheel's way (a chevron going that way). It fades once the Bot's
 * let it be a while. The glow (the page's edges in the accent) fades in while the Bot's at Emission's tools (acting) or its
 * pointer's moving, and out once neither is, GLOW_HOLD_S after. */
class BotPointer {
    private sealed class Step {
        class Glide(val to: Pair<Double, Double>, val share: Double) : Step()
        class Press(val down: Boolean) : Step()
        object Tap : Step()
        class Hint(val dx: Double, val dy: Double) : Step()
    }

    private class Doing(val kind: String, val from: Pair<Double, Double>? = null, val to: Pair<Double, Double>? = null,
                        val dur: Double = 0.0)

    private class Mark(val ring: Boolean, val x: Double, val y: Double, val t0: Double, val dx: Double = 0.0,
                       val dy: Double = 0.0)

    class Look(val x: Double?, val y: Double?, val alpha: Double, val scale: Double,
               val rings: List<DoubleArray>, val hints: List<DoubleArray>, val glow: Double)

    private var at: Pair<Double, Double>? = null
    private val steps = ArrayList<Step>()
    private var step: Doing? = null
    private var stepT = 0.0
    private var pressed = false
    private val marks = ArrayList<Mark>()
    private var seen = -1e9
    private var alpha = 0.0
    private var glow = 0.0
    var acting = false
    private var looked: Double? = null
    private var moved = -1e9
    var tab: String? = null // (the tab it was last in: drawn over that tab's page only)

    private fun now() = SystemClock.uptimeMillis() / 1000.0

    /** What the Bot did: kind hover, click, drag or scroll, at x, y (a drag's path from there). */
    fun add(kind: String, x: Double, y: Double, path: List<Pair<Double, Double>>? = null, count: Int = 1,
            dx: Double = 0.0, dy: Double = 0.0, tab: String? = null) {
        if (tab != null && tab != this.tab) { // (another tab's page: shown there, not glided from the last one's)
            this.tab = tab
            at = null
            steps.clear()
            step = null
        }
        if (kind == "drag" && path != null && path.size >= 2) {
            steps += Step.Glide(path[0], 1.0)
            steps += Step.Press(true)
            val whole = path.zipWithNext { a, b -> dist(a, b) }.sum().takeIf { it > 0 } ?: 1.0
            val total = min(1.2, max(0.35, whole / 900))
            for ((a, p) in path.zipWithNext()) steps += Step.Glide(p, total * dist(a, p) / whole)
            steps += Step.Press(false)
        } else {
            steps += Step.Glide(x to y, 1.0)
            if (kind == "click") repeat(max(1, min(3, count))) { steps += Step.Tap }
            else if (kind == "scroll") steps += Step.Hint(dx, dy)
        }
        moved = now()
        seen = moved
    }

    private fun moving() = steps.isNotEmpty() || step != null || marks.isNotEmpty()
    private fun glowing(now: Double) = acting || moving() || now - moved < GLOW_HOLD_S
    private fun shown(now: Double) = (at != null || steps.isNotEmpty()) &&
        (acting || steps.isNotEmpty() || step != null || now - seen < IDLE_S)

    /** Whether anything's moving, or about to: else what's drawn stays as it is. */
    fun busy(): Boolean {
        val now = now()
        return moving() || (alpha != 0.0 && alpha != 1.0) || (glow != 0.0 && glow != 1.0) || (glow > 0) != glowing(now) ||
            (alpha > 0) != shown(now) || now - moved < GLOW_HOLD_S
    }

    fun look(): Look {
        val now = now()
        val dt = looked?.let { max(0.0, min(0.1, now - it)) } ?: 0.0
        looked = now
        advance(now)
        val want = if (shown(now)) 1.0 else 0.0
        alpha = if (want > alpha) min(want, alpha + dt / APPEAR_S) else max(want, alpha - dt / FADE_S)
        if (moving()) moved = now
        val g = if (glowing(now)) 1.0 else 0.0
        glow = if (g > glow) min(g, glow + dt / GLOW_IN_S) else max(g, glow - dt / GLOW_OUT_S)
        var x = at?.first
        var y = at?.second
        var scale = if (pressed) 0.82 else 1.0
        val s = step
        if (s != null && s.kind == "glide" && at != null) {
            val (sx, sy) = s.from!!
            val (tx, ty) = s.to!!
            val k = if (s.dur > 0) min(1.0, (now - stepT) / s.dur) else 1.0
            val e = if (k < 0.5) 4 * k * k * k else 1 - (-2 * k + 2).pow(3) / 2 // (eased in and out, as a hand moves)
            x = sx + (tx - sx) * e
            y = sy + (ty - sy) * e
        } else if (s != null && s.kind == "tap") {
            val k = (now - stepT) / PRESS_S
            scale = 1.0 - 0.18 * sin(PI * min(1.0, k))
        }
        val rings = ArrayList<DoubleArray>()
        val hints = ArrayList<DoubleArray>()
        for (m in marks) {
            val k = (now - m.t0) / (if (m.ring) RING_S else HINT_S)
            if (m.ring) rings += doubleArrayOf(m.x, m.y, 5 + 22 * (1 - (1 - k).pow(2)), 0.9 * (1 - k))
            else hints += doubleArrayOf(m.x, m.y, m.dx, m.dy, k, 1 - k * k)
        }
        marks.removeAll { now - it.t0 >= (if (it.ring) RING_S else HINT_S) }
        return Look(x, y, alpha, scale, rings, hints, glow)
    }

    private fun advance(now: Double) {
        while (true) {
            val s = step
            if (s != null) {
                val dur = when (s.kind) {
                    "glide" -> s.dur
                    "tap" -> PRESS_S
                    else -> 0.0
                }
                if (now - stepT < dur) return
                if (s.kind == "glide") at = s.to
                step = null
            }
            if (steps.isEmpty()) return
            val next = steps.removeAt(0)
            val quick = if (steps.size > BEHIND) 0.35 else 1.0
            when (next) {
                is Step.Glide -> {
                    val from = at
                    if (from == null) { // (its first: shown there, not glided from nowhere)
                        at = next.to
                        continue
                    }
                    val d = dist(from, next.to)
                    val dur = (if (next.share == 1.0) min(GLIDE_S.second, GLIDE_S.first + d / 1500)
                    else max(0.03, next.share)) * quick
                    step = Doing("glide", from, next.to, dur)
                }
                is Step.Press -> {
                    pressed = next.down
                    continue
                }
                is Step.Tap -> {
                    step = Doing("tap")
                    at?.let { marks += Mark(true, it.first, it.second, now) }
                }
                is Step.Hint -> {
                    if (next.dx != 0.0 || next.dy != 0.0) at?.let { marks += Mark(false, it.first, it.second, now, next.dx, next.dy) }
                    continue
                }
            }
            stepT = now
        }
    }

    companion object { // (schema/tokens' pointer, as emission's is)
        val GLIDE_S = Tokens.Pointer.glideShortestS to Tokens.Pointer.glideLongestS
        const val PRESS_S = Tokens.Pointer.pressS
        const val RING_S = Tokens.Pointer.ringS
        const val HINT_S = Tokens.Pointer.hintS
        const val IDLE_S = Tokens.Pointer.idleS
        const val FADE_S = Tokens.Pointer.fadeS
        const val APPEAR_S = Tokens.Pointer.appearS
        const val GLOW_IN_S = Tokens.Pointer.glowInS
        const val GLOW_OUT_S = Tokens.Pointer.glowOutS
        const val GLOW_HOLD_S = Tokens.Pointer.glowHoldS
        const val BEHIND = Tokens.Pointer.behind

        // (emission's shapes, in the page's points: its pointer, its tip at 0, 0, and its shadow; lines' widths)
        val ARROW = Tokens.Pointer.arrow
        val ARROW_SHADOW = Tokens.Pointer.arrowShadow
        val RING_EDGE = Tokens.Pointer.ringEdge.toFloat()
        val ARROW_EDGE = Tokens.Pointer.arrowEdge.toFloat()
        val HINT_EDGE = Tokens.Pointer.hintEdge.toFloat()
        val HINT_HALO = Tokens.Pointer.hintHalo.toFloat()
        const val GLOW_STRENGTH = Tokens.Pointer.glowStrength
        const val GLOW_REACH = Tokens.Pointer.glowReach

        fun dist(a: Pair<Double, Double>, b: Pair<Double, Double>) = hypot(a.first - b.first, a.second - b.second)

        /** The glow's strength d in from an edge: strong at it, nothing by GLOW_REACH in. */
        fun glowAt(d: Double) = if (d < GLOW_REACH) GLOW_STRENGTH * (1 - d / GLOW_REACH).pow(2.4) else 0.0
    }
}

/** Over the Bot's page (laid over its WebView, the same size, never hit: touches go through to the page, and it's
 * not in what the Bot's shown of it): its pointer and the glow, drawn as Emission draws them, k view pixels to the
 * page's CSS pixel. */
class PointerOverlay(ctx: Context, private val accent: Int) : View(ctx) {
    val pointer = BotPointer()
    var k = 1f
    var shownTab: String? = null // (the tab in front: the pointer's drawn only if it's in it, the glow whichever)
    private var glowPicture: Bitmap? = null
    private var glowKey: Float? = null
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeJoin = Paint.Join.ROUND
        strokeCap = Paint.Cap.ROUND
    }
    private val glowPaint = Paint(Paint.FILTER_BITMAP_FLAG)

    init {
        isClickable = false
        isFocusable = false
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
    }

    /** Drawn each frame while anything moves. */
    fun kick() = postInvalidateOnAnimation()

    /** The glow's corners and edges (emissionpointer.glow_pixels'): a square 2 * its reach and a pixel across, each
     * pixel as strong as its two nearest edges make it together, so its corners are as soft as its sides; its middle
     * row and column stretched to the page's size as it's drawn. Made once for each scale. */
    private fun glow(): Bitmap {
        glowPicture?.takeIf { glowKey == k }?.let { return it }
        val reach = (BotPointer.GLOW_REACH * k).toInt().coerceAtLeast(1)
        val n = 2 * reach + 1
        val edge = DoubleArray(n) { BotPointer.glowAt(min(it, n - 1 - it) / k.toDouble()) }
        val r = android.graphics.Color.red(accent)
        val g = android.graphics.Color.green(accent)
        val b = android.graphics.Color.blue(accent)
        val pixels = IntArray(n * n) { i ->
            val a = 1 - (1 - edge[i % n]) * (1 - edge[i / n])
            android.graphics.Color.argb((255 * a).roundToInt(), r, g, b)
        }
        glowPicture?.recycle()
        glowPicture = Bitmap.createBitmap(pixels, n, n, Bitmap.Config.ARGB_8888)
        glowKey = k
        return glowPicture!!
    }

    private fun drawGlow(canvas: Canvas, w: Int, h: Int) {
        val pic = glow()
        val n = pic.width
        val reach = n / 2
        val rx = min(reach, w / 2)
        val ry = min(reach, h / 2)
        val xs = intArrayOf(0, reach, reach + 1, n) // (the picture's: its left, its middle column, its right)
        val ys = xs
        val dx = intArrayOf(0, rx, w - rx, w)       // (the page's)
        val dy = intArrayOf(0, ry, h - ry, h)
        for (i in 0..2) for (j in 0..2) {
            if (dx[i + 1] <= dx[i] || dy[j + 1] <= dy[j]) continue
            val sx0 = if (i == 0) 0 else if (i == 1) xs[1] else n - (dx[3] - dx[2])
            val sx1 = if (i == 0) dx[1] else if (i == 1) xs[2] else n
            val sy0 = if (j == 0) 0 else if (j == 1) ys[1] else n - (dy[3] - dy[2])
            val sy1 = if (j == 0) dy[1] else if (j == 1) ys[2] else n
            canvas.drawBitmap(pic, android.graphics.Rect(sx0, sy0, sx1, sy1),
                android.graphics.Rect(dx[i], dy[j], dx[i + 1], dy[j + 1]), glowPaint)
        }
    }

    override fun onDraw(canvas: Canvas) {
        val look = pointer.look()
        if (look.glow > 0 && width > 0 && height > 0) {
            glowPaint.alpha = (255 * look.glow).roundToInt()
            drawGlow(canvas, width, height)
        }
        val a = if (pointer.tab == null || pointer.tab == shownTab) look.alpha else 0.0
        canvas.save()
        canvas.scale(k, k)
        for (ring in look.rings) {
            val (x, y, r, ra) = ring.toList()
            fill.color = withAlpha(accent, 0.22 * ra * a)
            canvas.drawCircle(x.toFloat(), y.toFloat(), r.toFloat(), fill)
            line.strokeWidth = BotPointer.RING_EDGE
            line.color = withAlpha(accent, ra * a)
            canvas.drawCircle(x.toFloat(), y.toFloat(), r.toFloat(), line)
        }
        for (hint in look.hints) {
            val (x, y, dx, dy) = hint.toList()
            val kk = hint[4]
            val ha = hint[5]
            val n = hypot(dx, dy).takeIf { it > 0 } ?: 1.0
            val ux = dx / n
            val uy = dy / n
            val d = 30 + 12 * kk
            val cx = x + 9 + ux * d
            val cy = y + 13 + uy * d
            val path = Path()
            val px = -uy
            for (back in doubleArrayOf(-4.5, 3.5)) { // (two chevrons, one after the other, pointing the wheel's way)
                val bx = cx + ux * back
                val by = cy + uy * back
                path.moveTo((bx - ux * 4 + px * 7).toFloat(), (by - uy * 4 + ux * 7).toFloat())
                path.lineTo((bx + ux * 3).toFloat(), (by + uy * 3).toFloat())
                path.lineTo((bx - ux * 4 - px * 7).toFloat(), (by - uy * 4 - ux * 7).toFloat())
            }
            line.strokeWidth = BotPointer.HINT_HALO
            line.color = withAlpha(android.graphics.Color.WHITE, ha * a)
            canvas.drawPath(path, line)
            line.strokeWidth = BotPointer.HINT_EDGE
            line.color = withAlpha(accent, ha * a)
            canvas.drawPath(path, line)
        }
        val x = look.x
        val y = look.y
        if (x != null && y != null && a > 0) {
            fill.color = withAlpha(android.graphics.Color.BLACK, 0.28 * a)
            canvas.drawPath(arrow(x + BotPointer.ARROW_SHADOW.first, y + BotPointer.ARROW_SHADOW.second, look.scale), fill)
            val arrow = arrow(x, y, look.scale)
            fill.color = withAlpha(accent, a)
            canvas.drawPath(arrow, fill)
            line.strokeWidth = BotPointer.ARROW_EDGE
            line.color = withAlpha(android.graphics.Color.WHITE, a)
            canvas.drawPath(arrow, line)
        }
        canvas.restore()
        if (pointer.busy()) postInvalidateOnAnimation()
    }

    private fun arrow(x: Double, y: Double, scale: Double) = Path().apply {
        BotPointer.ARROW.forEachIndexed { i, (px, py) ->
            val ax = (x + px * scale).toFloat()
            val ay = (y + py * scale).toFloat()
            if (i == 0) moveTo(ax, ay) else lineTo(ax, ay)
        }
        close()
    }

    private fun withAlpha(color: Int, a: Double) =
        (color and 0x00FFFFFF) or ((255 * a.coerceIn(0.0, 1.0)).roundToInt() shl 24)
}
