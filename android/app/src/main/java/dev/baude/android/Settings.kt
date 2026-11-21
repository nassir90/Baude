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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import kotlin.math.cbrt
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToInt

/** The phone's settings (Baude's settings.json's, those that mean something here, and the phone's own), kept in its
 * prefs ("settings") and taken as they're changed: the accent (null: the system's) and whether it's light or dark
 * (BaudeTheme), the text's size, the icons' and everything's (Global Scale: BaudeTheme's density), Full-width text
 * (the chat's), the chats that are read-only, Meta's model, effort and Run commands, the code font (null: the
 * default), notifications (Mesh's), and whether updates download on a metered network by themselves (Update's). */
object Settings {
    val accent = mutableStateOf<String?>(null)
    val appearance = mutableStateOf("system") // ("system", "light", "dark")
    val textScale = mutableStateOf(1f)
    val iconScale = mutableStateOf(1f)
    val globalScale = mutableStateOf(1f)
    val fullWidth = mutableStateOf(true)
    val readOnly = mutableStateListOf<String>() // (the sessions that can't be written to: their ids)
    val metaModel = mutableStateOf<String?>(null)
    val metaEffort = mutableStateOf<String?>(null)
    val metaCommands = mutableStateOf(true)
    val codeFont = mutableStateOf<String?>(null)
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
    const val BASE_SCALE = 1.2f // (everything's size at Global Scale 1.0: a phone's held further off than a screen)

    private var loaded = false

    fun load(ctx: Context) {
        if (loaded) return
        loaded = true
        val p = ctx.applicationContext.getSharedPreferences("settings", Context.MODE_PRIVATE)
        accent.value = p.getString("accent", null)?.takeIf { it in ACCENTS }
        appearance.value = p.getString("appearance", "system")!!
        textScale.value = p.getFloat("text_scale", 1f)
        iconScale.value = p.getFloat("icon_scale", 1f)
        globalScale.value = p.getFloat("global_scale", 1f)
        fullWidth.value = p.getBoolean("full_width", true)
        val sessions = try {
            p.getString("read_only_sessions", null)?.let { j -> JSONArray(j).let { a -> (0 until a.length()).map(a::getString) } }
        } catch (_: Exception) {
            null
        }.orEmpty()
        if (sessions != readOnly.toList()) {
            readOnly.clear()
            readOnly.addAll(sessions)
        }
        metaModel.value = p.getString("meta_model", null)
        metaEffort.value = p.getString("meta_effort", null)
        metaCommands.value = p.getBoolean("meta_commands", true)
        codeFont.value = p.getString("code_font", null)
        notify.value = p.getBoolean("notify", true)
        meteredUpdates.value = p.getBoolean("metered_updates", false)
    }

    fun save(ctx: Context) {
        ctx.applicationContext.getSharedPreferences("settings", Context.MODE_PRIVATE).edit()
            .putString("accent", accent.value).putString("appearance", appearance.value)
            .putFloat("text_scale", textScale.value).putFloat("icon_scale", iconScale.value)
            .putFloat("global_scale", globalScale.value).putBoolean("full_width", fullWidth.value)
            .putString("read_only_sessions", JSONArray(readOnly.toList()).toString())
            .putString("meta_model", metaModel.value).putString("meta_effort", metaEffort.value)
            .putBoolean("meta_commands", metaCommands.value).putString("code_font", codeFont.value)
            .putBoolean("notify", notify.value).putBoolean("metered_updates", meteredUpdates.value).apply()
    }

    /** Whether a chat is read-only (Read-only session). */
    fun isReadOnly(sid: String) = sid.isNotEmpty() && sid in readOnly

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

/** How wide the chat's column is in room w while Full-width text is off, as Adw.Clamp holds it: all of w up to its
 * tightening threshold, then narrowing less and less to its maximum (adw_clamp's, the same cubic). */
fun chatColumn(w: Dp): Dp {
    val max = Tokens.Gap.chatMax.value
    val lower = Tokens.Gap.chatTighten.value
    val amp = max - lower
    val upper = 3 * amp + lower
    val x = w.value
    return when {
        x <= lower -> w
        x >= upper -> max.dp
        else -> (lower + amp * (1 - (1 - (x - lower) / (upper - lower)).pow(3))).dp
    }
}

/** An icon's size at Icon Scale (widgets.set_scale's icon_scale: 16 px at 1.0). */
val Dp.icon: Dp get() = this * Settings.iconScale.value

/** ⋮ → Settings…, as Baude's Settings, its groups in its order: Meta's, Full-width text, Code Font, the accent and
 * whether it's light or dark, how updates download, notifications and the version; the sizes; Send Feedback…. */
@Composable
fun SettingsScreen(back: () -> Unit, feedback: () -> Unit, chat: List<String> = emptyList()) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    val save = { Settings.save(ctx) }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar(Wording.Window.settingsTitle, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) })
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).navigationBarsPadding()) {
            MetaSettingsGroup()
            BoxedList(listOf<@Composable () -> Unit>(
                { FullWidthRow() },
                { CodeFontRow(chat) },
                { AccentRow() },
                {
                    val looks = linkedMapOf("system" to Wording.PhoneSettings.followSystem, "light" to Wording.PhoneSettings.light,
                        "dark" to Wording.PhoneSettings.dark)
                    Choice(Wording.PhoneSettings.appearance, looks.keys.toList(), Settings.appearance.value, { looks.getValue(it) }) {
                        Settings.appearance.value = it
                        save()
                    }
                },
                { MeteredRow() },
                {
                    SwitchRow(Wording.SharedSettings.notifications, Wording.SharedSettings.notificationsSubtitle,
                        Settings.notify.value) {
                        Settings.notify.value = it
                        save()
                    }
                },
                { VersionRow() },
            ), "") { it() }
            Heading(Wording.Window.size)
            BoxedList(listOf<@Composable () -> Unit>(
                { ScaleRow("text_scale") },
                { ScaleRow("icon_scale") },
                { ScaleRow("global_scale") },
            ), "") { it() }
            BoxedList(listOf<@Composable () -> Unit>({ ButtonRow(Wording.Fixes.sendFeedback, Adwaita.GoNext, onClick = feedback) }), "") { it() }
        }
    }
}

/** Settings' Full-width text row; fallback: its subtitle, where one's wanted; extra: after its switch. */
@Composable
fun FullWidthRow(fallback: String? = null, extra: (@Composable () -> Unit)? = null) {
    val ctx = LocalContext.current
    SwitchRow(Wording.MetaSettings.fullWidth, fallback, Settings.fullWidth.value, extra = extra) {
        Settings.fullWidth.value = it
        Settings.save(ctx)
    }
}

/** Settings' Code Font row (chat: the chats Restart keeps open). */
@Composable
fun CodeFontRow(chat: List<String> = emptyList(), fallback: String? = null, extra: (@Composable () -> Unit)? = null) {
    val ctx = LocalContext.current
    val shown = Settings.codeFont.value?.takeIf { it in CodeFont.FONTS } ?: CodeFont.DEFAULT
    Choice(Wording.SharedSettings.codeFont, CodeFont.FONTS.keys.toList(), shown, { it },
        subtitle = if (shown == CodeFont.running) fallback
            else Wording.SharedSettings.codeFontNextStart(font = shown, app = Wording.MeshVersions.baude),
        before = if (shown == CodeFont.running) null else ({
            Button(Wording.SharedSettings.restart) { CodeFont.restart(ctx, chat) }
        }), extra = extra) {
        Settings.codeFont.value = it
        Settings.save(ctx)
    }
}

/** Settings' Accent Colour row. */
@Composable
fun AccentRow(fallback: String? = null, extra: (@Composable () -> Unit)? = null) {
    val ctx = LocalContext.current
    val names = listOf("system") + Settings.ACCENTS.keys
    Choice(Wording.SharedSettings.accentColour, names, Settings.accent.value ?: "system", { it.replaceFirstChar(Char::uppercase) },
        dot = { if (it == "system") Settings.accentBg(ctx) else Settings.ACCENTS.getValue(it) }, subtitle = fallback, extra = extra) {
        Settings.accent.value = it.takeIf { n -> n in Settings.ACCENTS }
        Settings.save(ctx)
    }
}

/** Settings' Update on metered connections row. */
@Composable
fun MeteredRow(fallback: String? = null, extra: (@Composable () -> Unit)? = null) {
    val ctx = LocalContext.current
    SwitchRow(Wording.Updates.metered, Wording.Updates.meteredSubtitle, Settings.meteredUpdates.value, extra = extra) {
        Settings.meteredUpdates.value = it
        Settings.save(ctx)
        if (it) Update.check(ctx, force = true)
    }
}

/** One of Settings' sizes' rows: text_scale, icon_scale or global_scale. */
@Composable
fun ScaleRow(key: String, fallback: String? = null, extra: (@Composable () -> Unit)? = null) {
    val ctx = LocalContext.current
    val (title, field, range) = when (key) {
        "text_scale" -> Triple(Wording.MetaSettings.textScale, Settings.textScale, Settings.SCALE_MIN to Settings.SCALE_MAX)
        "icon_scale" -> Triple(Wording.MetaSettings.iconScale, Settings.iconScale, Settings.SCALE_MIN to Settings.SCALE_MAX)
        else -> Triple(Wording.MetaSettings.globalScale, Settings.globalScale, Settings.ZOOM_MIN to Settings.ZOOM_MAX)
    }
    SizeRow(title, field.value, range.first, range.second, fallback, extra) {
        field.value = it
        Settings.save(ctx)
    }
}

/** A button row (Adw.ButtonRow): its title, and an icon after it, together in the middle. */
@Composable
fun ButtonRow(title: String, end: ImageVector? = null, onClick: () -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().heightIn(min = 50.dp).clickable(onClick = onClick).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.Center) {
        Text(title, style = Type.body, color = c.fg, textAlign = TextAlign.Center)
        if (end != null) Icon(end, null, tint = c.dim, modifier = Modifier.size(18.dp.icon))
    }
}

/** Settings → Version (Baude's): the commit the app was built from, and when it was made; or that it's not a checkout. */
@Composable
private fun VersionRow() {
    val short = AppVersion.short
    ActionRow(if (short != null) Wording.Updates.version(commit = short) else Wording.Updates.versionUnknown,
        if (short != null) AppVersion.made?.let(::madeWhen) else Wording.Updates.notCheckout)
}

/** A row with a switch (Adw.SwitchRow). */
@Composable
fun SwitchRow(title: String, subtitle: String?, on: Boolean, enabled: Boolean = true,
              extra: (@Composable () -> Unit)? = null, set: (Boolean) -> Unit) {
    val c = LocalBaude.current
    Box(Modifier.alpha(if (enabled) 1f else 0.5f)) {
        ActionRow(title, subtitle, onClick = if (enabled) ({ set(!on) }) else null, suffix = {
            Switch(on, set, enabled = enabled,
                colors = SwitchDefaults.colors(checkedTrackColor = c.accentBg, checkedThumbColor = c.accentFg))
            extra?.invoke()
        })
    }
}

/** A row whose value is one of a list (Adw.ComboRow): the one picked beside its name, the others in a menu; dot: a
 * colour before each (the accents'). */
@Composable
fun Choice(title: String, values: List<String>, picked: String, label: (String) -> String,
           dot: ((String) -> Color)? = null, subtitle: String? = null, enabled: Boolean = true,
           before: (@Composable () -> Unit)? = null, extra: (@Composable () -> Unit)? = null, pick: (String) -> Unit) {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    Box(Modifier.alpha(if (enabled) 1f else 0.5f)) {
    ActionRow(title, subtitle, onClick = if (enabled) ({ open = true }) else null, suffix = {
        before?.invoke()
        Box {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                dot?.let { Dot(it(picked)) }
                Text(label(picked), style = Type.body, color = c.fg)
                Icon(Adwaita.PanDown, null, tint = c.fg, modifier = Modifier.size(18.dp.icon))
            }
            Popover(open, { open = false }) {
                for (v in values) DropdownMenuItem({
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        dot?.let { Dot(it(v)) }
                        Text(label(v), style = Type.body, color = c.fg)
                    }
                }, { open = false; pick(v) }, trailingIcon = {
                    if (v == picked) Icon(Adwaita.ObjectSelect, null, tint = c.accent,
                        modifier = Modifier.size(16.dp.icon))
                })
            }
        }
        extra?.invoke()
    })
    }
}

@Composable
private fun Dot(color: Color) = Box(Modifier.size(14.dp).clip(CircleShape).background(color))

/** A size, 1.0 the usual, in steps of 0.1 between low and high (Adw.SpinRow): − and + either side of it. */
@Composable
private fun SizeRow(title: String, size: Float, low: Float, high: Float, subtitle: String? = null,
                    extra: (@Composable () -> Unit)? = null, set: (Float) -> Unit) {
    val c = LocalBaude.current
    fun step(by: Int) = set(((size / Settings.STEP).roundToInt() + by).times(Settings.STEP).coerceIn(low, high)
        .let { (it * 100).roundToInt() / 100f })
    ActionRow(title, subtitle, suffix = {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(Adwaita.ListRemove, Wording.PhoneSettings.smaller, enabled = size > low + 0.001f) { step(-1) }
            Text("%.1f".format(size), style = Type.body, color = c.fg, textAlign = TextAlign.Center,
                modifier = Modifier.widthIn(min = 36.dp))
            IconButton(Adwaita.ListAdd, Wording.PhoneSettings.larger, enabled = size < high - 0.001f) { step(1) }
            extra?.invoke()
        }
    })
}
