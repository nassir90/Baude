package dev.baude.android.screenshots

import android.os.Looper
import org.robolectric.Shadows.shadowOf
import java.time.Duration

/** Compose's main-thread dispatchers made again, and its snapshot manager started again on them. Each keeps the
 * Choreographer it was made with, and Robolectric makes a new one each test: after the first test, nothing was
 * composed again once drawn (no effect ran, no state change showed, insets never arrived: a chat's bottom bar,
 * hidden while the keyboard's up, never showed). */
fun freshCompose() {
    val ui = androidx.compose.ui.platform.AndroidUiDispatcher::class.java
    (ui.getDeclaredField("currentThread").apply { isAccessible = true }.get(null) as ThreadLocal<*>).remove()
    val main = ui.getDeclaredField("Main\$delegate").apply { isAccessible = true }.get(null)
    val make = Class.forName("androidx.compose.ui.platform.AndroidUiDispatcher\$Companion\$Main\$2")
        .getDeclaredField("INSTANCE").apply { isAccessible = true }.get(null) as () -> Any
    main.javaClass.getDeclaredField("_value").apply { isAccessible = true }.set(main, make())
    (Class.forName("androidx.compose.ui.platform.GlobalSnapshotManager").getDeclaredField("started")
        .apply { isAccessible = true }.get(null) as java.util.concurrent.atomic.AtomicBoolean).set(false)
}

/** What's posted run, and the clock moved on a frame at a time (a frame each 16ms: an animation that asks for
 * frames, a menu opening, runs out instead of asking for them at one moment forever). */
fun settle() {
    repeat(40) { shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(16)) }
}
