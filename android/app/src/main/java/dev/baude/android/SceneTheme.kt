package dev.baude.android

import android.opengl.Matrix
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.PI
import kotlin.math.asin
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.math.tan

/** A scene theme on the phone (a prototype): a theme's scene.json (schema/types/Scene.json) drawn from its data alone:
 * the phone runs none of a theme's code (its desktop/ Python and shaders are the desktop apps'). Its shapes (plane,
 * box, cylinder, sphere) are drawn flat-shaded on a Canvas, lit as the desktop lights them (ambient, point and
 * directional lights, specular, emissive); bloom is a soft halo round what's brighter than its threshold. Each panel
 * is the real composable for its thing (a chat, a host, a Meta card), in a graphicsLayer fitted to the scene's camera
 * at the panel's centre, so taps land on its buttons (Compose maps touches through the layer); a panel far off the
 * view's centre is drawn with the perspective it would have at the centre. Slots, states, hidden and pick (open,
 * reveal) are as the desktop's (scenehost.py). Models (.obj) and textures aren't drawn here yet. */
class SceneNode3(spec: JSONObject, val obj: JSONObject?, val parent: String?) {
    val id: String = spec.getString("id")
    val position = vec(spec.optJSONArray("position"), 0f)
    val rotation = vec(spec.optJSONArray("rotation"), 0f)
    val scale = vec(spec.optJSONArray("scale"), 1f)
    val mesh: String? = spec.optString("mesh").ifEmpty { null }
    val material: String? = spec.optString("material").ifEmpty { null }
    val states: JSONObject? = spec.optJSONObject("states")
    val panel: JSONObject? = spec.optJSONObject("panel")
    val slots: JSONObject? = spec.optJSONObject("slots")
    var hidden by androidx.compose.runtime.mutableStateOf(spec.optBoolean("hidden"))
    val pick: String = spec.optString("pick", "open")

    fun local(): FloatArray { // (column vectors: T · Rz · Ry · Rx · S, as the desktop's S, Rx, Ry, Rz, T)
        val m = FloatArray(16)
        Matrix.setIdentityM(m, 0)
        Matrix.translateM(m, 0, position[0], position[1], position[2])
        Matrix.rotateM(m, 0, rotation[2], 0f, 0f, 1f)
        Matrix.rotateM(m, 0, rotation[1], 0f, 1f, 0f)
        Matrix.rotateM(m, 0, rotation[0], 1f, 0f, 0f)
        Matrix.scaleM(m, 0, scale[0], scale[1], scale[2])
        return m
    }

    companion object {
        fun vec(a: JSONArray?, d: Float) = FloatArray(3) { if (a != null && it < a.length()) a.getDouble(it).toFloat() else d }
    }
}

/** A scene and the things in it: its nodes, slots made into nodes for each thing that fits (scenehost.sync's). */
class SceneModel(val scene: JSONObject, world: List<JSONObject>) {
    val nodes = LinkedHashMap<String, SceneNode3>()

    init {
        val list = scene.getJSONArray("nodes")
        for (i in 0 until list.length()) add(list.getJSONObject(i), null, null)
        for (s in nodes.values.toList()) {
            val slots = s.slots ?: continue
            val fits = query(world, slots)
            val p = slots.optJSONObject("panel")
            val size = p?.optJSONArray("size")
            val w = size?.optDouble(0, 1.6)?.toFloat() ?: 1.6f
            val h = size?.optDouble(1, 1.0)?.toFloat() ?: 1.0f
            val gap = slots.optDouble("spacing", 0.2).toFloat()
            val cols = slots.optInt("columns", max(1, sqrt(fits.size.toDouble()).toInt()))
            for ((i, o) in fits.withIndex()) {
                val gid = "${s.id}/$i"
                val pos = when (slots.optString("layout", "row")) {
                    "ring" -> { val a = 2 * PI * i / max(fits.size, 1); floatArrayOf((gap * sin(a)).toFloat(), 0f, (gap * cos(a)).toFloat()) }
                    "grid" -> floatArrayOf((i % cols - (cols - 1) / 2f) * (w + gap), -(i / cols) * (h + gap), 0f)
                    "stack" -> floatArrayOf(0f, i * (h + gap), 0f)
                    else -> floatArrayOf((i - (fits.size - 1) / 2f) * (w + gap), 0f, 0f)
                }
                val spec = JSONObject().put("id", gid).put("position", JSONArray(pos.toList()))
                if (slots.optString("layout") == "ring") spec.put("rotation", JSONArray(listOf(0.0, Math.toDegrees(2 * PI * i / max(fits.size, 1)), 0.0)))
                add(spec, o, s.id)
                if (p != null) add(JSONObject().put("id", "$gid/panel").put("panel", p).put("hidden", slots.optBoolean("hidden")), null, gid)
                val with = slots.optJSONArray("with") ?: JSONArray()
                for (k in 0 until with.length()) {
                    val wn = JSONObject(with.getJSONObject(k).toString())
                    val own = wn.optString("parent").ifEmpty { null }
                    wn.put("id", "$gid/${wn.getString("id")}")
                    add(wn, null, if (own != null) "$gid/$own" else gid)
                }
            }
        }
    }

    private fun add(spec: JSONObject, obj: JSONObject?, parent: String?) {
        nodes[spec.getString("id")] = SceneNode3(spec, obj, parent ?: spec.optString("parent").ifEmpty { null })
    }

    private fun query(world: List<JSONObject>, slots: JSONObject): List<JSONObject> {
        val where = slots.optJSONObject("where")
        var out = world.filter { o ->
            o.optString("kind") == slots.getString("of") &&
                (where == null || where.keys().asSequence().all { k -> o.opt(k)?.toString().equals(where.get(k).toString(), true) })
        }
        out = if (slots.optString("sort") == "name") out.sortedBy { it.optString("name").lowercase() }
        else out.sortedByDescending { it.optDouble("used", 0.0) }
        val limit = slots.optInt("limit", 0)
        return if (limit > 0) out.take(limit) else out
    }

    fun objOf(n: SceneNode3?): JSONObject? {
        var at = n
        while (at != null) { if (at.obj != null) return at.obj; at = nodes[at.parent] }
        return null
    }

    fun shown(n: SceneNode3): Boolean {
        var at: SceneNode3? = n
        while (at != null) { if (at.hidden) return false; at = nodes[at.parent] }
        return true
    }

    fun world(n: SceneNode3): FloatArray {
        var m = n.local()
        var p = nodes[n.parent]
        while (p != null) { val r = FloatArray(16); Matrix.multiplyMM(r, 0, p.local(), 0, m, 0); m = r; p = nodes[p.parent] }
        return m
    }

    fun material(n: SceneNode3): JSONObject {
        val mats = scene.optJSONObject("materials") ?: JSONObject()
        val state = objOf(n)?.optString("state")
        var at: SceneNode3? = n
        while (at != null) { val s = at.states; if (state != null && s != null && s.has(state)) return mats.optJSONObject(s.getString(state)) ?: JSONObject(); at = nodes[at.parent] }
        at = n
        while (at != null) { if (at.material != null) return mats.optJSONObject(at.material) ?: JSONObject(); at = nodes[at.parent] }
        return JSONObject()
    }

    /** A click on the scene, not on a panel: the node does what its pick says (a reveal here; open is the app's). */
    fun picked(n: SceneNode3, open: (JSONObject) -> Unit) {
        when (n.pick) {
            "reveal" -> { n.hidden = true; for (k in nodes.values) if (k.parent == n.parent && k !== n) k.hidden = false }
            "open" -> objOf(n)?.let(open)
        }
    }
}

private fun rgb(hex: String?, k: Float = 1f): FloatArray {
    val h = (hex ?: "#000000").removePrefix("#")
    return FloatArray(3) { Integer.parseInt(h.substring(it * 2, it * 2 + 2), 16) / 255f * k }
}

/** What light comes off a point of a surface (Blinn-Phong, as scenegl's LIT_FRAG): (r, g, b) for albedo times this,
 * plus (r, g, b) added (shine and what it gives off itself). */
private fun light(scene: JSONObject, mat: JSONObject, p: FloatArray, n0: FloatArray, eye: FloatArray): Pair<FloatArray, FloatArray> {
    val v = norm(FloatArray(3) { eye[it] - p[it] })
    val n = if (dot(n0, v) < 0) FloatArray(3) { -n0[it] } else n0
    val albedo = rgb(mat.optString("color", "#ffffff"))
    val amb = rgb(scene.optString("ambient", "#202020"))
    val mul = FloatArray(3) { albedo[it] * amb[it] }
    val add = FloatArray(3)
    val lights = scene.optJSONArray("lights") ?: JSONArray()
    for (i in 0 until min(lights.length(), 8)) {
        val l = lights.getJSONObject(i)
        val col = rgb(l.optString("color", "#ffffff"), l.optDouble("intensity", 1.0).toFloat())
        var fall = 1f
        val dir = if (l.optString("type") == "directional") norm(SceneNode3.vec(l.optJSONArray("direction"), 0f).map { -it }.toFloatArray())
        else {
            val lp = SceneNode3.vec(l.optJSONArray("position"), 0f)
            val d = FloatArray(3) { lp[it] - p[it] }
            val dist = sqrt(dot(d, d))
            fall = (1f - dist / l.optDouble("range", 10.0).toFloat()).coerceIn(0f, 1f).pow(2)
            norm(d)
        }
        val diff = max(dot(n, dir), 0f)
        val hv = norm(FloatArray(3) { dir[it] + v[it] })
        val spec = if (diff > 0) max(dot(n, hv), 0f).pow(mat.optDouble("shininess", 32.0).toFloat()) * mat.optDouble("specular", 0.2).toFloat() else 0f
        for (c in 0..2) { mul[c] += albedo[c] * diff * col[c] * fall; add[c] += spec * col[c] * fall }
    }
    val em = rgb(mat.optString("emissive", "#000000"), mat.optDouble("glow", 1.0).toFloat())
    for (c in 0..2) mul[c] += em[c]
    return Pair(mul, add)
}

private fun dot(a: FloatArray, b: FloatArray) = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
private fun norm(a: FloatArray): FloatArray { val l = sqrt(dot(a, a)).takeIf { it > 0 } ?: 1f; return FloatArray(3) { a[it] / l } }

/** A shape's triangles in its own space (each three points), as scenegl's meshes. */
private fun triangles(mesh: String): List<Array<FloatArray>> {
    val out = ArrayList<Array<FloatArray>>()
    fun quad(a: FloatArray, b: FloatArray, c: FloatArray, d: FloatArray) { out.add(arrayOf(a, b, c)); out.add(arrayOf(a, c, d)) }
    when (mesh) {
        "plane" -> quad(floatArrayOf(-.5f, .5f, 0f), floatArrayOf(-.5f, -.5f, 0f), floatArrayOf(.5f, -.5f, 0f), floatArrayOf(.5f, .5f, 0f))
        "box" -> {
            val s = .5f
            val c = listOf(-s, s)
            for (axis in 0..2) for (side in c) {
                val p = Array(4) { k -> val u = if (k == 1 || k == 2) s else -s; val v = if (k >= 2) s else -s
                    FloatArray(3).also { it[axis] = side; it[(axis + 1) % 3] = u; it[(axis + 2) % 3] = v } }
                quad(p[0], p[1], p[2], p[3])
            }
        }
        "cylinder" -> for (i in 0 until 24) {
            val a0 = 2 * PI * i / 24; val a1 = 2 * PI * (i + 1) / 24
            fun at(a: Double, y: Float) = floatArrayOf((cos(a) * .5).toFloat(), y, (sin(a) * .5).toFloat())
            quad(at(a0, -.5f), at(a1, -.5f), at(a1, .5f), at(a0, .5f))
            out.add(arrayOf(floatArrayOf(0f, .5f, 0f), at(a0, .5f), at(a1, .5f)))
            out.add(arrayOf(floatArrayOf(0f, -.5f, 0f), at(a1, -.5f), at(a0, -.5f)))
        }
        "sphere" -> {
            fun at(r: Int, s: Int): FloatArray { val th = PI * r / 10; val ph = 2 * PI * s / 20
                return floatArrayOf((sin(th) * cos(ph) * .5).toFloat(), (cos(th) * .5).toFloat(), (sin(th) * sin(ph) * .5).toFloat()) }
            for (r in 0 until 10) for (s in 0 until 20) quad(at(r, s), at(r + 1, s), at(r + 1, s + 1), at(r, s + 1))
        }
    }
    return out
}

/** One drawn triangle: where it is on screen, its depth, its colour, and its node (for picks). */
private class Tri(val pts: FloatArray, val depth: Float, val colour: Color, val node: SceneNode3)

/** A scene drawn, with its things' panels (panel(show, thing) draws one: the app's own composables) live in it. */
@Composable
fun SceneRoom(model: SceneModel, modifier: Modifier = Modifier, time: Float? = null, open: (JSONObject) -> Unit = {},
              panel: @Composable (String, JSONObject) -> Unit) {
    val scene = model.scene
    var clock by remember { mutableFloatStateOf(0f) }
    var reveals by remember { mutableIntStateOf(0) }
    if (time == null) LaunchedEffect(model) {
        var last = 0L
        while (true) withFrameNanos { now -> if (last != 0L) clock += (now - last) / 1e9f; last = now }
    }
    val t = time ?: clock
    val density = LocalDensity.current.density
    BoxWithConstraints(modifier.fillMaxSize().background(Color(rgb(scene.optString("background")).let { android.graphics.Color.rgb(it[0], it[1], it[2]) }))) {
        val w = constraints.maxWidth.toFloat()
        val h = constraints.maxHeight.toFloat()
        val cam = scene.getJSONObject("camera")
        val target = SceneNode3.vec(cam.getJSONArray("target"), 0f)
        val pos = SceneNode3.vec(cam.getJSONArray("position"), 0f)
        val d = FloatArray(3) { pos[it] - target[it] }
        val r = sqrt(dot(d, d))
        var yaw = atan2(d[0], d[2])
        val pitch = asin((d[1] / r).coerceIn(-1f, 1f))
        cam.optJSONObject("orbit")?.let { o -> yaw += Math.toRadians(o.optDouble("yaw", 15.0)).toFloat() * sin(2 * PI.toFloat() * t / max(o.optDouble("period", 30.0).toFloat(), 1f)) }
        val eye = floatArrayOf(target[0] + r * cos(pitch) * sin(yaw), target[1] + r * sin(pitch), target[2] + r * cos(pitch) * cos(yaw))
        val view = FloatArray(16).also { Matrix.setLookAtM(it, 0, eye[0], eye[1], eye[2], target[0], target[1], target[2], 0f, 1f, 0f) }
        val given = cam.optDouble("fov", 45.0).toFloat() // (vertical where the view's wider than tall, else across)
        val fov = if (w >= h) given else Math.toDegrees(2 * kotlin.math.atan(tan(Math.toRadians(given / 2.0)) * h / w)).toFloat()
        val proj = FloatArray(16).also { Matrix.perspectiveM(it, 0, fov, w / h, 0.05f, 200f) }
        val vp = FloatArray(16).also { Matrix.multiplyMM(it, 0, proj, 0, view, 0) }
        val focal = h / 2 / tan(Math.toRadians(fov / 2.0)).toFloat() // (px per metre at a metre away)
        fun project(m: FloatArray, p: FloatArray): FloatArray { // (→ screen x, y, and depth along the view)
            val wp = FloatArray(4); Matrix.multiplyMV(wp, 0, m, 0, floatArrayOf(p[0], p[1], p[2], 1f), 0)
            val c = FloatArray(4); Matrix.multiplyMV(c, 0, vp, 0, wp, 0)
            return floatArrayOf((c[0] / c[3] + 1) / 2 * w, (1 - c[1] / c[3]) / 2 * h, c[3])
        }
        fun worldOf(m: FloatArray, p: FloatArray) = FloatArray(4).also { Matrix.multiplyMV(it, 0, m, 0, floatArrayOf(p[0], p[1], p[2], 1f), 0) }
        val bloom = scene.optJSONObject("bloom")
        val threshold = bloom?.optDouble("threshold", 1.0)?.toFloat() ?: Float.MAX_VALUE
        if (reveals < 0) return@BoxWithConstraints // (read, so a reveal draws the scene again)
        val tris = ArrayList<Tri>()
        val halos = ArrayList<Triple<Offset, Float, Color>>()
        for (n in model.nodes.values) {
            val mesh = n.mesh ?: continue
            if (!model.shown(n)) continue
            val m = model.world(n)
            val mat = model.material(n)
            var bright = 0f
            for (tri in triangles(mesh)) {
                val wp = tri.map { worldOf(m, it) }
                val e1 = FloatArray(3) { wp[1][it] - wp[0][it] }
                val e2 = FloatArray(3) { wp[2][it] - wp[0][it] }
                val nrm = norm(floatArrayOf(e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]))
                val centre = FloatArray(3) { (wp[0][it] + wp[1][it] + wp[2][it]) / 3 }
                val (mul, add) = light(scene, mat, centre, nrm, eye)
                val c = FloatArray(3) { mul[it] + add[it] }
                bright = max(bright, c.max())
                val sp = tri.map { project(m, it) }
                if (sp.any { it[2] <= 0.05f }) continue
                tris.add(Tri(floatArrayOf(sp[0][0], sp[0][1], sp[1][0], sp[1][1], sp[2][0], sp[2][1]), (sp[0][2] + sp[1][2] + sp[2][2]) / 3,
                    Color(c[0].coerceIn(0f, 1f), c[1].coerceIn(0f, 1f), c[2].coerceIn(0f, 1f)), n))
            }
            if (bright > threshold) { // (bloom: a halo the size of what glows)
                val c = project(m, floatArrayOf(0f, 0f, 0f))
                val s = project(m, floatArrayOf(.5f, .5f, 0f))
                val radius = 2.2f * sqrt((s[0] - c[0]).pow(2) + (s[1] - c[1]).pow(2)) + (bloom?.optDouble("radius", 4.0)?.toFloat() ?: 4f) * density * 4
                val tint = rgb(mat.optString("emissive", mat.optString("color", "#ffffff")))
                halos.add(Triple(Offset(c[0], c[1]), radius, Color(tint[0], tint[1], tint[2], ((bright - threshold) * (bloom?.optDouble("strength", 0.6)?.toFloat() ?: 0.6f)).coerceIn(0f, 0.8f))))
            }
        }
        tris.sortByDescending { it.depth }
        Canvas(Modifier.fillMaxSize().pointerInput(model) {
            detectTapGestures { p -> // (the nearest shape under the tap: its pick)
                fun inside(t: Tri): Boolean {
                    val q = t.pts
                    fun s(ax: Float, ay: Float, bx: Float, by: Float) = (p.x - bx) * (ay - by) - (ax - bx) * (p.y - by)
                    val d1 = s(q[0], q[1], q[2], q[3]); val d2 = s(q[2], q[3], q[4], q[5]); val d3 = s(q[4], q[5], q[0], q[1])
                    return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0))
                }
                tris.lastOrNull { it.node.pick != "none" && inside(it) }?.let { model.picked(it.node, open); reveals++ }
            }
        }) {
            for (tri in tris) {
                val q = tri.pts
                val path = Path().apply { moveTo(q[0], q[1]); lineTo(q[2], q[3]); lineTo(q[4], q[5]); close() }
                drawPath(path, tri.colour)
            }
            for ((c, rad, col) in halos) drawCircle(Brush.radialGradient(listOf(col, Color.Transparent), c, rad), rad, c, blendMode = BlendMode.Plus)
        }
        // the panels, far first: each the real composable, in a layer fitted to the camera at its centre
        val panels = model.nodes.values.filter { it.panel != null && model.shown(it) }.mapNotNull { n ->
            val obj = model.objOf(n) ?: return@mapNotNull null
            val p = n.panel!!
            val size = p.optJSONArray("size")
            val sw = size?.optDouble(0, 1.6)?.toFloat() ?: 1.6f
            val sh = size?.optDouble(1, 1.2)?.toFloat() ?: 1.2f
            val px = p.optJSONArray("px")
            val pw = px?.optDouble(0, 480.0)?.toFloat() ?: 480f
            val ph = px?.optDouble(1, 360.0)?.toFloat() ?: 360f
            val m = model.world(n)
            val c = project(m, floatArrayOf(0f, 0f, 0f))
            val centre = worldOf(m, floatArrayOf(0f, 0f, 0f))
            val toEye = norm(FloatArray(3) { eye[it] - centre[it] })
            val nw = worldOf(m, floatArrayOf(0f, 0f, 1f)).let { q -> norm(FloatArray(3) { q[it] - centre[it] }) }
            val camView = FloatArray(4).also { Matrix.multiplyMV(it, 0, view, 0, floatArrayOf(nw[0], nw[1], nw[2], 0f), 0) }
            val (mul, add) = light(scene, model.material(n), floatArrayOf(centre[0], centre[1], centre[2]), nw, eye)
            Triple(n, obj, listOf(c[0], c[1], c[2], sw, sh, pw, ph, Math.toDegrees(atan2(camView[0], camView[2]).toDouble()).toFloat(),
                Math.toDegrees(asin(camView[1].coerceIn(-1f, 1f)).toDouble()).toFloat(), mul[0], mul[1], mul[2], add[0], add[1], add[2], dot(toEye, nw)))
        }.sortedByDescending { it.third[2] }
        for ((k, entry) in panels.withIndex()) {
            val (n, obj, f) = entry
            val (cx, cy, depth) = Triple(f[0], f[1], f[2])
            val pwPx = f[5] * density
            val scale = focal / depth * f[3] / pwPx // (the widget's px → screen px at its depth)
            Box(Modifier.zIndex(1f + k).size((f[5]).dp, (f[6]).dp).graphicsLayer {
                transformOrigin = TransformOrigin.Center
                translationX = cx - pwPx / 2
                translationY = cy - f[6] * density / 2
                scaleX = scale; scaleY = scale
                rotationY = -f[7]
                rotationX = f[8]
                cameraDistance = focal / scale / 72f // (SkCamera's units: 72 a inch, in the layer's own px)
            }.clip(RoundedCornerShape(10.dp)).drawWithContent {
                drawContent()
                val mul = Color(f[9].coerceIn(0f, 1f), f[10].coerceIn(0f, 1f), f[11].coerceIn(0f, 1f))
                drawRect(mul, blendMode = BlendMode.Multiply)
                drawRect(Color(f[12].coerceIn(0f, 1f), f[13].coerceIn(0f, 1f), f[14].coerceIn(0f, 1f)), blendMode = BlendMode.Plus)
            }) { panel(n.panel!!.optString("show"), obj) }
        }
    }
}
