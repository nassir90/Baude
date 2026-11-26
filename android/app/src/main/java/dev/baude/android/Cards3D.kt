package dev.baude.android

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.AnimationEndReason
import androidx.compose.animation.core.AnimationVector1D
import androidx.compose.animation.core.exponentialDecay
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.util.VelocityTracker
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/** A prototype (not for merging): cards in a 3D room. The room (a floor the cards lie on) is one graphicsLayer tilted
 * back and turning (rotationX/Y, cameraDistance): Compose maps touches through its inverse, so each card (the real
 * composable: Meta's tool card, change box, form card) still takes taps on its buttons. A card dragged follows the
 * finger on the floor (pointerInput's drag, in its own coordinates, turned into the floor's); let go, it's flung
 * with a decay animation (Animatable.animateDecay) that bounces off the floor's edges and knocks the cards it meets. */
class Card3D(x: Float, y: Float, spin: Float) {
    val x = Animatable(x)
    val y = Animatable(y)
    var spin by mutableFloatStateOf(spin)
    var z by mutableFloatStateOf(0f)
    var w by mutableFloatStateOf(0f)
    var h by mutableFloatStateOf(0f)
    var held = false
}

object Room3D {
    var orbit = true
    var time by mutableFloatStateOf(0f) // (the camera's clock, s)
    var frames by mutableLongStateOf(0L)
    var cached = false // (each card kept as a layer of its own (a texture), drawn again only when it changes)
    val pitch get() = 28f + 8f * sin(time * 0.23f)
    val yaw get() = 14f * sin(time * 0.35f)
    val pan get() = 16f * sin(time * 0.13f)
    var throwOne: () -> Unit = {} // (a card flung at random, for measuring: set by the room shown)
    const val FRICTION = 2.2f
    const val BOUNCE = 0.6f
}

private suspend fun flingAxis(a: Animatable<Float, AnimationVector1D>, v0: Float) {
    var v = v0
    while (abs(v) > 30f) {
        val r = a.animateDecay(v, exponentialDecay(frictionMultiplier = Room3D.FRICTION))
        v = if (r.endReason == AnimationEndReason.BoundReached) -r.endState.velocity * Room3D.BOUNCE else 0f
    }
}

private fun fling(scope: CoroutineScope, c: Card3D, vx: Float, vy: Float) {
    scope.launch { flingAxis(c.x, vx) }
    scope.launch { flingAxis(c.y, vy) }
}

/** count cards (card(i) draws the i-th) on a floor the size of the room, spread in a jittered grid, the view slowly
 * orbiting. */
@Composable
fun Cards3DRoom(count: Int, modifier: Modifier = Modifier, cardWidth: Int = 300, card: @Composable (Int) -> Unit) {
    val c = LocalBaude.current
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current.density
    BoxWithConstraints(modifier.fillMaxSize()) {
        val rw = constraints.maxWidth * 1.0f
        val rh = constraints.maxHeight * 0.95f
        val cards = remember(count) {
            val rng = java.util.Random(1)
            val cols = maxOf(1, Math.round(sqrt(count * rw / rh / 1.6f)))
            val rows = (count + cols - 1) / cols
            List(count) { i ->
                Card3D(((i % cols + 0.5f) / cols - 0.5f) * (rw - cardWidth * density) + rng.nextFloat() * 30 - 15,
                    ((i / cols + 0.5f) / rows - 0.5f) * (rh - 120 * density) + rng.nextFloat() * 20 - 10,
                    rng.nextFloat() * 16 - 8).also { it.z = i.toFloat() }
            }
        }
        var top by remember { mutableFloatStateOf(count.toFloat()) }
        val pick = remember { java.util.Random(3) }
        Room3D.throwOne = {
            val k = cards[pick.nextInt(cards.size)]
            val a = pick.nextDouble() * 2 * Math.PI
            top += 1f; k.z = top
            fling(scope, k, (1600 * density / 2 * cos(a)).toFloat(), (1600 * density / 2 * sin(a)).toFloat())
        }
        LaunchedEffect(cards) { // (the camera's clock, and the cards that move knocking the ones they meet)
            var last = 0L
            while (true) withFrameNanos { now ->
                val dt = if (last == 0L) 0f else ((now - last) / 1e9f).coerceAtMost(0.05f)
                last = now
                if (Room3D.orbit) Room3D.time += dt
                Room3D.frames++
                for (m in cards) {
                    if (!m.x.isRunning && !m.y.isRunning) continue
                    for (o in cards) {
                        if (o === m || o.held) continue
                        val dx = o.x.value - m.x.value
                        val dy = o.y.value - m.y.value
                        val px = (m.w + o.w) / 2 - abs(dx)
                        val py = (m.h + o.h) / 2 - abs(dy)
                        if (px <= 0 || py <= 0) continue
                        val vx = m.x.velocity
                        val vy = m.y.velocity
                        if (px < py && (o.x.velocity - vx) * dx < 0) {
                            scope.launch { o.x.snapTo(o.x.value + (if (dx >= 0) px else -px)) }
                            fling(scope, o, vx * 0.5f, o.y.velocity)
                        } else if (py <= px && (o.y.velocity - vy) * dy < 0) {
                            scope.launch { o.y.snapTo(o.y.value + (if (dy >= 0) py else -py)) }
                            fling(scope, o, o.x.velocity, vy * 0.5f)
                        }
                    }
                }
            }
        }
        // the floor: tilted back and turning, its grid drawn on it; the cards lie on it
        Box(Modifier.fillMaxSize().graphicsLayer {
            rotationX = Room3D.pitch
            rotationY = Room3D.yaw
            translationX = Room3D.pan * density
            cameraDistance = 9f * density
        }.drawBehind {
            val line = Color(0x40808CA6)
            val x0 = (size.width - rw) / 2
            val y0 = (size.height - rh) / 2
            drawRect(Color(0x1A808CA6), Offset(x0, y0), androidx.compose.ui.geometry.Size(rw, rh))
            var x = 0f
            while (x <= rw) { drawLine(line, Offset(x0 + x, y0), Offset(x0 + x, y0 + rh), 1f); x += 64 * density }
            var y = 0f
            while (y <= rh) { drawLine(line, Offset(x0, y0 + y), Offset(x0 + rw, y0 + y), 1f); y += 64 * density }
        }) {
            for ((i, k) in cards.withIndex()) {
                Box(Modifier.align(Alignment.Center).zIndex(k.z)
                    .onSizeChanged { s ->
                        k.w = s.width.toFloat(); k.h = s.height.toFloat()
                        k.x.updateBounds(-rw / 2 + k.w / 2, rw / 2 - k.w / 2)
                        k.y.updateBounds(-rh / 2 + k.h / 2, rh / 2 - k.h / 2)
                    }
                    .graphicsLayer {
                        translationX = k.x.value
                        translationY = k.y.value
                        rotationZ = k.spin
                        shadowElevation = (if (k.held) 18f else 6f) * density
                        shape = RoundedCornerShape(10.dp)
                        clip = false
                        if (Room3D.cached) compositingStrategy = androidx.compose.ui.graphics.CompositingStrategy.Offscreen
                    }
                    .pointerInput(k) {
                        val tracker = VelocityTracker()
                        detectDragGestures(
                            onDragStart = {
                                k.held = true
                                top += 1f
                                k.z = top
                                tracker.resetTracking()
                                scope.launch { k.x.stop(); k.y.stop() }
                            },
                            onDragEnd = {
                                k.held = false
                                val v = tracker.calculateVelocity()
                                fling(scope, k, v.x, v.y)
                            },
                            onDragCancel = { k.held = false },
                        ) { change, drag ->
                            change.consume()
                            val r = Math.toRadians(k.spin.toDouble())
                            val dx = (drag.x * cos(r) - drag.y * sin(r)).toFloat()
                            val dy = (drag.x * sin(r) + drag.y * cos(r)).toFloat()
                            scope.launch { k.x.snapTo(k.x.value + dx); k.y.snapTo(k.y.value + dy) }
                            tracker.addPosition(change.uptimeMillis, Offset(k.x.value + dx, k.y.value + dy))
                        }
                    }
                    .width(cardWidth.dp)
                    .background(c.inside, RoundedCornerShape(10.dp))
                    .padding(8.dp)) { card(i) }
            }
        }
    }
}
