package dev.baude.android

import android.content.Context
import android.os.Build
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlin.math.cbrt
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToInt

/** The phone's settings (Baude's settings.json's, those that mean something here, and the phone's own), kept in its
 * prefs ("settings") and taken as they're changed: the accent (null: the system's) and whether it's light or dark
 * (BaudeTheme), the text's size and everything's (Global Scale: BaudeTheme's density), notifications (Mesh's), and
 * whether updates download on a metered network by themselves (Update's). */
object Settings {
    val accent = mutableStateOf<String?>(null)
    val appearance = mutableStateOf("system") // ("system", "light", "dark")
    val textScale = mutableStateOf(1f)
    val globalScale = mutableStateOf(1f)
    val notify = mutableStateOf(true)
    val meteredUpdates = mutableStateOf(false)

    /** libadwaita's accents (widgets.ACCENTS, schema/tokens' accent): each name and its colour. */
    val ACCENTS = linkedMapOf("blue" to Tokens.Accent.blue, "teal" to Tokens.Accent.teal, "green" to Tokens.Accent.green,
        "yellow" to Tokens.Accent.yellow, "orange" to Tokens.Accent.orange, "red" to Tokens.Accent.red,
        "pink" to Tokens.Accent.pink, "purple" to Tokens.Accent.purple, "slate" to Tokens.Accent.slate)
    const val SCALE_MIN = 0.7f // (widgets.SCALE_MIN, SCALE_MAX: the text's)
    const val SCALE_MAX = 2.0f
    const val ZOOM_MIN = 0.6f // (widgets.ZOOM_MIN, ZOOM_MAX: Global Scale's)
    const val ZOOM_MAX = 2.5f
    const val STEP = 0.1f // (widgets.ZOOM_STEP)

    private var loaded = false

    fun load(ctx: Context) {
        if (loaded) return
        loaded = true
        val p = ctx.applicationContext.getSharedPreferences("settings", Context.MODE_PRIVATE)
        accent.value = p.getString("accent", null)?.takeIf { it in ACCENTS }
        appearance.value = p.getString("appearance", "system")!!
        textScale.value = p.getFloat("text_scale", 1f)
        globalScale.value = p.getFloat("global_scale", 1f)
        notify.value = p.getBoolean("notify", true)
        meteredUpdates.value = p.getBoolean("metered_updates", false)
    }

    fun save(ctx: Context) {
        ctx.applicationContext.getSharedPreferences("settings", Context.MODE_PRIVATE).edit()
            .putString("accent", accent.value).putString("appearance", appearance.value)
            .putFloat("text_scale", textScale.value).putFloat("global_scale", globalScale.value)
            .putBoolean("notify", notify.value).putBoolean("metered_updates", meteredUpdates.value).apply()
    }

    /** The accent's colour, as @accent_bg_color has it: the one picked, or the system's (Android 12's on: its
     * palette's accent; before it, Baude's blue). */
    fun accentBg(ctx: Context): Color = ACCENTS[accent.value] ?: if (Build.VERSION.SDK_INT >= 31)
        Color(ctx.getColor(android.R.color.system_accent1_500)) else Tokens.Accent.blue

    /** An accent as text (@accent_color): libadwaita's adw_rgba_to_standalone, in Oklab, its lightness at most 0.5
     * on light, at least 0.85 on dark. */
    fun standalone(bg: Color, dark: Boolean): Color {
        fun lin(c: Float) = if (c <= 0.04045f) c / 12.92f else ((c + 0.055f) / 1.055f).pow(2.4f)
        fun gam(c: Float) = if (c <= 0.0031308f) 12.92f * c else 1.055f * c.pow(1 / 2.4f) - 0.055f
        val r = lin(bg.red)
        val g = lin(bg.green)
        val b = lin(bg.blue)
        val l1 = cbrt(0.4122214708f * r + 0.5363325363f * g + 0.0514459929f * b)
        val m1 = cbrt(0.2119034982f * r + 0.6806995451f * g + 0.1073969566f * b)
        val s1 = cbrt(0.0883024619f * r + 0.2817188376f * g + 0.6299787005f * b)
        var lL = 0.2104542553f * l1 + 0.7936177850f * m1 - 0.0040720468f * s1
        val aa = 1.9779984951f * l1 - 2.4285922050f * m1 + 0.4505937099f * s1
        val bb = 0.0259040371f * l1 + 0.7827717662f * m1 - 0.8086757660f * s1
        lL = if (dark) max(lL, 0.85f) else min(lL, 0.5f)
        val l = (lL + 0.3963377774f * aa + 0.2158037573f * bb).pow(3)
        val m = (lL - 0.1055613458f * aa - 0.0638541728f * bb).pow(3)
        val s = (lL - 0.0894841775f * aa - 1.2914855480f * bb).pow(3)
        fun out(v: Float) = gam(v.coerceIn(0f, 1f)).coerceIn(0f, 1f)
        return Color(out(4.0767416621f * l - 3.3077115913f * m + 0.2309699292f * s),
            out(-1.2684380046f * l + 2.6097574011f * m - 0.3413193965f * s),
            out(-0.0041960863f * l - 0.7034186147f * m + 1.7076147010f * s))
    }
}

/** ⋮ → Settings…, as Baude's Settings: the accent and whether it's light or dark, how updates download, notifications
 * and the version; Mesh Versions; the sizes; Send Feedback…. */
@Composable
fun SettingsScreen(back: () -> Unit, feedback: () -> Unit, versions: () -> Unit = {}) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    Settings.load(ctx)
    val save = { Settings.save(ctx) }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar(Wording.Window.settingsTitle, start = { IconButton(Icons.AutoMirrored.Filled.ArrowBack, Wording.Browser.back, onClick = back) })
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).navigationBarsPadding()) {
            BoxedList(listOf<@Composable () -> Unit>(
                {
                    val names = listOf("system") + Settings.ACCENTS.keys
                    Choice(Wording.SharedSettings.accentColour, names, Settings.accent.value ?: "system", { it.replaceFirstChar(Char::uppercase) },
                        dot = { if (it == "system") Settings.accentBg(ctx) else Settings.ACCENTS.getValue(it) }) {
                        Settings.accent.value = it.takeIf { n -> n in Settings.ACCENTS }
                        save()
                    }
                },
                {
                    val looks = linkedMapOf("system" to Wording.PhoneSettings.followSystem, "light" to Wording.PhoneSettings.light,
                        "dark" to Wording.PhoneSettings.dark)
                    Choice(Wording.PhoneSettings.appearance, looks.keys.toList(), Settings.appearance.value, { looks.getValue(it) }) {
                        Settings.appearance.value = it
                        save()
                    }
                },
                {
                    SwitchRow(Wording.Updates.metered, Wording.Updates.meteredSubtitle,
                        Settings.meteredUpdates.value) {
                        Settings.meteredUpdates.value = it
                        save()
                        if (it) Update.check(ctx, force = true)
                    }
                },
                {
                    SwitchRow(Wording.SharedSettings.notifications, Wording.SharedSettings.notificationsSubtitle,
                        Settings.notify.value) {
                        Settings.notify.value = it
                        save()
                    }
                },
                { VersionRow() },
                {
                    ActionRow(Wording.MeshVersions.title, meshVersionsSummary(), onClick = versions, suffix = {
                        Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = c.dim, modifier = Modifier.size(18.dp))
                    })
                },
            ), "") { it() }
            Heading(Wording.Window.size)
            BoxedList(listOf<@Composable () -> Unit>(
                { SizeRow(Wording.MetaSettings.textScale, Settings.textScale.value, Settings.SCALE_MIN, Settings.SCALE_MAX) {
                    Settings.textScale.value = it
                    save()
                } },
                { SizeRow(Wording.MetaSettings.globalScale, Settings.globalScale.value, Settings.ZOOM_MIN, Settings.ZOOM_MAX) {
                    Settings.globalScale.value = it
                    save()
                } },
            ), "") { it() }
            BoxedList(listOf<@Composable () -> Unit>({
                ActionRow(Wording.Fixes.sendFeedback, null, onClick = feedback, suffix = {
                    Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = c.dim, modifier = Modifier.size(18.dp))
                })
            }), "") { it() }
        }
    }
}

/** Settings → Version (Baude's): the commit the app was built from, and when it was made. */
@Composable
private fun VersionRow() {
    val ctx = LocalContext.current
    val name = remember { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName }
    val made = remember { ctx.getString(R.string.commit_time).toLongOrNull()?.takeIf { it > 0 } }
    ActionRow(if (name != null) Wording.Updates.version(commit = name) else Wording.Updates.versionUnknown, made?.let {
        DateTimeFormatter.ofPattern("d MMMM yyyy, HH:mm").format(Instant.ofEpochSecond(it).atZone(ZoneId.systemDefault()))
    })
}

/** A row with a switch (Adw.SwitchRow). */
@Composable
private fun SwitchRow(title: String, subtitle: String?, on: Boolean, set: (Boolean) -> Unit) {
    val c = LocalBaude.current
    ActionRow(title, subtitle, onClick = { set(!on) }, suffix = {
        Switch(on, set, colors = SwitchDefaults.colors(checkedTrackColor = c.accentBg, checkedThumbColor = c.accentFg))
    })
}

/** A row whose value is one of a list (Adw.ComboRow): the one picked beside its name, the others in a menu; dot: a
 * colour before each (the accents'). */
@Composable
private fun Choice(title: String, values: List<String>, picked: String, label: (String) -> String,
                   dot: ((String) -> Color)? = null, pick: (String) -> Unit) {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    ActionRow(title, null, onClick = { open = true }, suffix = {
        Box {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                dot?.let { Dot(it(picked)) }
                Text(label(picked), style = Type.body, color = c.fg)
                Icon(Icons.Filled.ArrowDropDown, null, tint = c.fg, modifier = Modifier.size(18.dp))
            }
            Popover(open, { open = false }) {
                for (v in values) DropdownMenuItem({
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        dot?.let { Dot(it(v)) }
                        Text(label(v), style = Type.body, color = c.fg)
                    }
                }, { open = false; pick(v) }, trailingIcon = {
                    if (v == picked) Icon(Icons.Filled.Check, null, tint = c.accent,
                        modifier = Modifier.size(16.dp))
                })
            }
        }
    })
}

@Composable
private fun Dot(color: Color) = Box(Modifier.size(14.dp).clip(CircleShape).background(color))

/** A size, 1.0 the usual, in steps of 0.1 between low and high (Adw.SpinRow): − and + either side of it. */
@Composable
private fun SizeRow(title: String, size: Float, low: Float, high: Float, set: (Float) -> Unit) {
    val c = LocalBaude.current
    fun step(by: Int) = set(((size / Settings.STEP).roundToInt() + by).times(Settings.STEP).coerceIn(low, high)
        .let { (it * 100).roundToInt() / 100f })
    ActionRow(title, null, suffix = {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(Icons.Filled.Remove, Wording.PhoneSettings.smaller, enabled = size > low + 0.001f) { step(-1) }
            Text("%.1f".format(size), style = Type.body, color = c.fg, textAlign = TextAlign.Center,
                modifier = Modifier.widthIn(min = 36.dp))
            IconButton(Icons.Filled.Add, Wording.PhoneSettings.larger, enabled = size < high - 0.001f) { step(1) }
        }
    })
}
