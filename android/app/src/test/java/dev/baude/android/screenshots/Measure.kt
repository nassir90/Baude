package dev.baude.android.screenshots

import android.view.View
import android.view.WindowManager
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.layout.LayoutInfo
import androidx.compose.ui.node.RootForTest
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** The popups' rig (tests/parity/popups.json's scenes): every window composited over the activity where it sits, and
 * each window's laid-out content (layout nodes, not pixels) dumped as JSON for tests/parity/popups.py to measure. */
object Measure {
    private fun composeRoot(v: View): RootForTest? = v as? RootForTest
        ?: (v as? android.view.ViewGroup)?.let { g -> (0 until g.childCount).firstNotNullOfOrNull { composeRoot(g.getChildAt(it)) } }

    private fun layoutRoot(r: RootForTest): LayoutInfo? = r.javaClass.methods.firstOrNull { it.name == "getRoot" && it.parameterCount == 0 }
        ?.invoke(r) as? LayoutInfo

    @Suppress("UNCHECKED_CAST")
    private fun children(n: LayoutInfo): List<LayoutInfo> {
        val m = n.javaClass.methods.firstOrNull { (it.name.startsWith("getChildren") || it.name.startsWith("getZSortedChildren")) &&
            it.parameterCount == 0 && List::class.java.isAssignableFrom(it.returnType) }
            ?: n.javaClass.declaredMethods.first { it.name.startsWith("getChildren") && it.parameterCount == 0 }.apply { isAccessible = true }
        val got = m.invoke(n)
        return when (got) {
            is List<*> -> got as List<LayoutInfo>
            else -> (got.javaClass.getMethod("asMutableList").invoke(got) as List<LayoutInfo>)
        }
    }

    /** Where a window is drawn on the screen: its window manager's x/y, or centred / at the gravity's edge. */
    fun place(v: View, screenW: Int, screenH: Int): IntArray {
        val lp = v.layoutParams as? WindowManager.LayoutParams ?: return intArrayOf(0, 0)
        if (v.javaClass.name.endsWith("PopupLayout")) { // (a panel of the window its owner's in: x/y within that window)
            val owner = generateSequence<Class<*>>(v.javaClass) { it.superclass }.mapNotNull { c ->
                runCatching { c.getDeclaredField("composeView").apply { isAccessible = true }.get(v) as View }.getOrNull() }.firstOrNull()
            val root = owner?.rootView
            val at = if (root != null && root !== v && root.layoutParams is WindowManager.LayoutParams &&
                (root.layoutParams as WindowManager.LayoutParams).type != WindowManager.LayoutParams.TYPE_BASE_APPLICATION &&
                root.javaClass.name.contains("DecorView") && root.width < screenW + 1 && root.height < screenH) place(root, screenW, screenH)
                else intArrayOf(0, 0)
            return intArrayOf(lp.x + at[0], lp.y + at[1])
        }
        val loc = IntArray(2).also { v.getLocationOnScreen(it) }
        if (loc[0] != 0 || loc[1] != 0) return loc
        val g = lp.gravity
        val hg = g and android.view.Gravity.HORIZONTAL_GRAVITY_MASK
        val vg = g and android.view.Gravity.VERTICAL_GRAVITY_MASK
        val x = when {
            hg == android.view.Gravity.CENTER_HORIZONTAL -> (screenW - v.width) / 2 + lp.x
            hg == android.view.Gravity.RIGHT || hg == android.view.Gravity.END -> screenW - v.width - lp.x
            else -> lp.x
        }
        val y = when {
            vg == android.view.Gravity.CENTER_VERTICAL -> (screenH - v.height) / 2 + lp.y
            vg == android.view.Gravity.BOTTOM -> screenH - v.height - lp.y
            else -> lp.y
        }
        return intArrayOf(x, y)
    }

    /** A dialog's window told where it is on the screen (the rig's windows all say 0,0): what a menu opened from it
     * is placed against. */
    fun pin(v: View, screenW: Int, screenH: Int) {
        if (v.width <= 0 || v.layoutParams !is WindowManager.LayoutParams) return
        if (v.javaClass.name.contains("PopupLayout")) return
        val lp = v.layoutParams as WindowManager.LayoutParams
        val hg = lp.gravity and android.view.Gravity.HORIZONTAL_GRAVITY_MASK
        val vg = lp.gravity and android.view.Gravity.VERTICAL_GRAVITY_MASK
        val x = if (hg == android.view.Gravity.CENTER_HORIZONTAL) (screenW - v.width) / 2 + lp.x else lp.x
        val y = if (vg == android.view.Gravity.CENTER_VERTICAL) (screenH - v.height) / 2 + lp.y else lp.y
        val ai = View::class.java.getDeclaredField("mAttachInfo").apply { isAccessible = true }.get(v) ?: return
        ai.javaClass.getDeclaredField("mWindowLeft").apply { isAccessible = true }.setInt(ai, x)
        ai.javaClass.getDeclaredField("mWindowTop").apply { isAccessible = true }.setInt(ai, y)
    }

    /** A popup's window placed again (once its dialog's told where it sits: a phone's knows from the start). */
    fun replace(v: View) {
        if (!v.javaClass.name.endsWith("PopupLayout")) return
        v.javaClass.methods.firstOrNull { it.name.startsWith("updatePosition") && it.parameterCount == 0 }?.invoke(v)
    }

    /** A dialog's window given the width a phone's window manager gives it: a WRAP_CONTENT window is measured first at
     * config_prefDialogWidth (ViewRootImpl.measureHierarchy) and its frame is that size; the rig's window manager
     * hands every window the whole screen instead, so a dialog came out the screen's width. */
    fun dialogWidth(v: View) {
        val lp = v.layoutParams as? WindowManager.LayoutParams ?: return
        if (v.javaClass.name.contains("PopupLayout") || lp.type == WindowManager.LayoutParams.TYPE_BASE_APPLICATION) return
        if (lp.width != WindowManager.LayoutParams.WRAP_CONTENT) return
        val tv = android.util.TypedValue()
        v.resources.getValue(v.resources.getIdentifier("config_prefDialogWidth", "dimen", "android"), tv, true)
        val pref = tv.getDimension(v.resources.displayMetrics).toInt()
        if (pref <= 0) return
        lp.width = pref
        val g = Class.forName("android.view.WindowManagerGlobal")
        g.getMethod("updateViewLayout", View::class.java, android.view.ViewGroup.LayoutParams::class.java)
            .invoke(g.getMethod("getInstance").invoke(null), v, lp)
    }

    fun composite(root: View, windows: List<View>, file: File): List<JSONObject> {
        val bitmap = android.graphics.Bitmap.createBitmap(root.width, root.height, android.graphics.Bitmap.Config.ARGB_8888)
        val canvas = android.graphics.Canvas(bitmap)
        val info = ArrayList<JSONObject>()
        for (v in windows) {
            if (v.width <= 0 || v.height <= 0) continue
            val lp = v.layoutParams as? WindowManager.LayoutParams
            val at = if (v === root) intArrayOf(0, 0) else place(v, root.width, root.height)
            if (v !== root && lp != null && lp.flags and WindowManager.LayoutParams.FLAG_DIM_BEHIND != 0)
                canvas.drawColor(android.graphics.Color.argb((lp.dimAmount * 255).toInt(), 0, 0, 0))
            canvas.save()
            canvas.translate(at[0].toFloat(), at[1].toFloat())
            v.draw(canvas)
            canvas.restore()
            info.add(JSONObject().put("x", at[0]).put("y", at[1]).put("w", v.width).put("h", v.height)
                .put("gravity", lp?.gravity ?: 0).put("lpw", lp?.width ?: 0).put("lph", lp?.height ?: 0).put("pref", runCatching { val tv = android.util.TypedValue(); v.resources.getValue(v.resources.getIdentifier("config_prefDialogWidth", "dimen", "android"), tv, true); tv.getDimension(v.resources.displayMetrics) }.getOrNull() ?: -1f).put("lpx", lp?.x ?: 0).put("lpy", lp?.y ?: 0)
                .put("dim", lp != null && lp.flags and WindowManager.LayoutParams.FLAG_DIM_BEHIND != 0)
                .put("activity", v === root).put("class", v.javaClass.name))
        }
        file.parentFile?.mkdirs()
        file.outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
        return info
    }

    private fun kindOf(names: List<String>): String? = when {
        names.any { it.contains("TextField") || it.contains("CoreText") } -> "field-text"
        names.any { it.contains("Text") && (it.contains("Element") || it.contains("Modifier")) } -> "text"
        names.any { it.contains("Painter") } -> "icon"
        names.any { it.contains("Background") || it.contains("DrawBehind") || it.contains("Border") || it.contains("DrawWithContent") ||
            it.contains("Canvas") || it.contains("DrawModifier") || it.contains("Shadow") || it.contains("GraphicsLayer") } -> "draw"
        else -> null
    }

    private fun all(n: SemanticsNode): List<SemanticsNode> = listOf(n) + n.children.flatMap { all(it) }

    /** Every window's layout: each node that draws (text, icons, fills) with its bounds in its window (px, unclipped),
     * its modifiers' names, its depth; and the clickable semantics nodes. */
    fun dump(root: View, windows: List<View>, places: List<JSONObject>): JSONArray {
        val out = JSONArray()
        windows.filter { it.width > 0 && it.height > 0 }.forEachIndexed { wi, v ->
            val r = composeRoot(v) ?: return@forEachIndexed
            val texts = HashMap<Int, String>()
            val descs = HashMap<Int, String>()
            val unmerged = all(r.semanticsOwner.unmergedRootSemanticsNode)
            for (s in unmerged) {
                s.config.getOrNull(SemanticsProperties.Text)?.joinToString(" ") { it.text }?.let { texts[s.id] = it }
                s.config.getOrNull(SemanticsProperties.EditableText)?.text?.let { texts[s.id] = (texts[s.id]?.let { t -> "$t|" } ?: "") + it }
                s.config.getOrNull(SemanticsProperties.ContentDescription)?.joinToString(" ")?.let { descs[s.id] = it }
            }
            val layouts = HashMap<Int, androidx.compose.ui.text.TextLayoutResult>()
            for (s in unmerged) s.config.getOrNull(SemanticsActions.GetTextLayoutResult)?.action?.let { a ->
                val got = ArrayList<androidx.compose.ui.text.TextLayoutResult>()
                runCatching { a.invoke(got) }
                got.firstOrNull()?.let { layouts[s.id] = it }
            }
            val nodes = JSONArray()
            var count = 0
            fun walk(n: LayoutInfo, depth: Int, parent: Int) {
                if (!n.isPlaced || !n.isAttached) return
                val id = count++
                val c = n.coordinates
                val p = c.localToWindow(Offset.Zero) // (inner: after the node's own padding)
                val mi = n.getModifierInfo()
                val outer = mi.firstOrNull()?.coordinates?.takeIf { it.isAttached } ?: c
                val q = outer.localToWindow(Offset.Zero)
                val names = mi.map { it.modifier.javaClass.name.substringAfterLast('.') }
                val kids = children(n)
                val o = JSONObject().put("i", id).put("parent", parent).put("depth", depth)
                    .put("l", q.x).put("t", q.y).put("w", n.width).put("h", n.height)
                    .put("il", p.x).put("it", p.y).put("iw", c.size.width).put("ih", c.size.height)
                    .put("mods", JSONArray(names)).put("leaf", kids.none { it.isPlaced })
                    .put("density", n.density.density.toDouble()).put("sem", n.semanticsId)
                kindOf(names)?.let { o.put("kind", it) }
                mi.firstOrNull { it.modifier.javaClass.name.contains("Background") }?.coordinates?.takeIf { it.isAttached }?.let { bc ->
                    val b = bc.localToWindow(Offset.Zero) // (where the fill is drawn: inside any layout modifier that moves it)
                    o.put("bg", JSONArray(listOf(b.x.toDouble(), b.y.toDouble(), bc.size.width.toDouble(), bc.size.height.toDouble())))
                }
                texts[n.semanticsId]?.let { o.put("text", it) }
                layouts[n.semanticsId]?.let { t -> // (the text's own lines: where its ink starts and ends)
                    if (t.lineCount > 0) {
                        o.put("tl", p.x + (0 until t.lineCount).minOf { t.getLineLeft(it) })
                            .put("tr", p.x + (0 until t.lineCount).maxOf { t.getLineRight(it) }).put("lines", t.lineCount)
                            .put("fontPx", t.layoutInput.style.fontSize.value * t.layoutInput.density.fontScale * t.layoutInput.density.density)
                    }
                }
                descs[n.semanticsId]?.let { o.put("desc", it) }
                nodes.put(o)
                for (k in kids) walk(k, depth + 1, id)
            }
            layoutRoot(r)?.let { walk(it, 0, -1) }
            val clicks = JSONArray()
            for (s in all(r.semanticsOwner.rootSemanticsNode)) {
                val cfg = s.config
                val click = cfg.contains(SemanticsActions.OnClick)
                val long = cfg.contains(SemanticsActions.OnLongClick)
                val edit = cfg.contains(SemanticsActions.SetText)
                if (!click && !long && !edit) continue
                val p = s.positionInWindow
                clicks.put(JSONObject().put("id", s.id).put("l", p.x).put("t", p.y).put("w", s.size.width).put("h", s.size.height)
                    .put("text", cfg.getOrNull(SemanticsProperties.Text)?.joinToString(" ") { it.text } ?: "")
                    .put("desc", cfg.getOrNull(SemanticsProperties.ContentDescription)?.joinToString(" ") ?: "")
                    .put("edit", edit).put("disabled", cfg.contains(SemanticsProperties.Disabled))
                    .put("role", cfg.getOrNull(SemanticsProperties.Role)?.toString() ?: ""))
            }
            out.put(JSONObject().put("window", wi).put("activity", v === root).put("place", places.getOrNull(wi) ?: JSONObject())
                .put("w", v.width).put("h", v.height).put("nodes", nodes).put("clicks", clicks))
        }
        return out
    }
}
