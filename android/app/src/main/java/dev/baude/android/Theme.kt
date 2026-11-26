package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.displayCutout
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.em

/** Baude's colours (libadwaita's, as GTK resolves them, light and dark: theming.CSS's baude_frame, _inside, _rule and
 * _raised made of them; schema/tokens', Tokens.light and Tokens.dark), so the app looks as Baude does. */
data class BaudeColors(
    val frame: Color,      // (sidebar_bg_color: the frame, where the chats are listed)
    val inside: Color,     // (view_bg_color: where a chat's read and written)
    val window: Color,     // (window_bg_color)
    val fg: Color,         // (window_fg_color)
    val rule: Color,       // (borders at 0.9: every edge)
    val raised: Color,     // (view_fg_color at 0.06: a code block, a table's head)
    val card: Color,       // (card_bg_color)
    val accentBg: Color,   // (accent_bg_color: a suggested button)
    val accentFg: Color,   // (accent_fg_color)
    val accent: Color,     // (accent_color: accent as text)
    val destructive: Color,
    val error: Color,      // (error_color: the context bar past 85%)
    val warning: Color,
    val success: Color,
    val dialog: Color,     // (dialog_bg_color)
    val removed: Color,    // (diff.removed: a diff's line taken out, red)
    val added: Color,      // (diff.added: one put in, green)
    val dark: Boolean,     // (drawn for dark: Tokens.dark's)
) {
    val dim: Color get() = fg.copy(alpha = fg.alpha * 0.6f)        // (.sysnote, .dim-label: 0.55–0.6)
    val bubble: Color get() = accentBg.copy(alpha = 0.15f)          // (.user-bubble)
    val quiet: Color get() = fg.copy(alpha = fg.alpha * 0.07f)      // (.queued-bubble, .sender-bubble)
    val quietFg: Color get() = fg.copy(alpha = fg.alpha * 0.55f)    // (.queued-bubble's text)
    val rowSelected: Color get() = fg.copy(alpha = 0.10f)           // (a navigation sidebar's selected row)
}

private fun baudeColors(t: ThemeTokens, dark: Boolean) = with(t.libadwaita) {
    BaudeColors(
        frame = t.baude.frame, inside = t.baude.inside, window = windowBgColor, fg = windowFgColor, rule = t.baude.rule,
        raised = t.baude.raised, card = cardBgColor, accentBg = accentBgColor, accentFg = accentFgColor,
        accent = accentColor, destructive = destructiveColor, error = errorColor, warning = warningColor, success = successColor,
        dialog = dialogBgColor, removed = t.diff.removed, added = t.diff.added, dark = dark,
    )
}

val LightBaude = baudeColors(Tokens.light, false)

val DarkBaude = baudeColors(Tokens.dark, true)

/** These colours with a theme's over them (Settings → Theme: its host's, worked out there: Theme.json's light or
 * dark, each of schema/tokens' libadwaita and baude colours that isn't Baude's own, by its token's path). A theme's
 * accent is taken only while Accent Colour is System, as the desktop's is (one picked is drawn over it), and
 * accent_color is made of its accent_bg_color if the theme doesn't give one. */
fun BaudeColors.themed(theme: Msg.Theme?): BaudeColors {
    val accents = setOf("libadwaita.accent_bg_color", "libadwaita.accent_color", "libadwaita.accent_fg_color")
    val over = (if (dark) theme?.dark else theme?.light).orEmpty()
        .filterKeys { Settings.accent.value == null || it !in accents }
    if (over.isEmpty()) return this
    fun c(path: String, was: Color) = over[path]?.let(::hexColor) ?: was
    val accentBg = c("libadwaita.accent_bg_color", accentBg)
    return copy(
        frame = c("baude.frame", frame), inside = c("baude.inside", inside), window = c("libadwaita.window_bg_color", window),
        fg = c("libadwaita.window_fg_color", fg), rule = c("baude.rule", rule), raised = c("baude.raised", raised),
        card = c("libadwaita.card_bg_color", card), accentBg = accentBg, accentFg = c("libadwaita.accent_fg_color", accentFg),
        accent = over["libadwaita.accent_color"]?.let(::hexColor)
            ?: if ("libadwaita.accent_bg_color" in over) Settings.standalone(accentBg, dark) else accent,
        destructive = c("libadwaita.destructive_color", destructive), error = c("libadwaita.error_color", error),
        warning = c("libadwaita.warning_color", warning), success = c("libadwaita.success_color", success),
        dialog = c("libadwaita.dialog_bg_color", dialog),
    )
}

/** #rrggbb or #rrggbbaa as a Color; null if it isn't one. */
fun hexColor(hex: String): Color? {
    if (!Regex("#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?").matches(hex)) return null
    val rgb = hex.substring(1, 7).toLong(16)
    val a = if (hex.length == 9) hex.substring(7, 9).toLong(16) else 0xFF
    return Color((a shl 24 or rgb).toInt())
}

/** Baude's measures (theming.CSS and baude.py's margins: schema/tokens'), in dp. */
object Gap {
    val sidebarRowV = Tokens.Gap.sidebarRowV        // (.session-row's padding)
    val sidebarRowH = Tokens.Gap.sidebarRowH
    val botRow = Tokens.Gap.botRow                  // (row.bot-row's min-height)
    // (Home's rows, the phone's own heights (schema/ui's Sidebar.region): a chat's Android's least touch target, 48dp
    // at Global Scale 1.0 (BASE_SCALE's 1.2 of these), a Bot's twice that, as on the desktop)
    val homeRow = androidx.compose.ui.unit.Dp(40f)
    val homeBotRow = homeRow * 2f
    val avatar = Tokens.Gap.avatar                  // (bot_row's Adw.Avatar)
    val avatarGap = Tokens.Gap.avatarGap            // (bot_row's box spacing)
    val headerTop = Tokens.Gap.headerTop            // (header_row's margins, top and start)
    val headerStart = Tokens.Gap.headerStart
    val chatH = Tokens.Gap.chatH                    // (the chat's box: its margins, start and end, top and bottom)
    val chatV = Tokens.Gap.chatV
    val messageGap = Tokens.Gap.message             // (spacing between a chat's parts)
    val bubbleRadius = Tokens.Radius.bubble         // (.user-bubble: its radius and padding)
    val bubbleV = Tokens.Gap.bubbleV
    val bubbleH = Tokens.Gap.bubbleH
    val composerRadius = Tokens.Radius.composer     // (.composer-box: a 1px ring of the rule)
    val barButton = Tokens.Gap.barButton            // (a flat button in a bar: IconButton)
    val composerButton = Tokens.Composer.button     // (.composer-actions button: + and send)
    val composerInset = Tokens.Composer.inset       // (those buttons, in from the box's edges)
    val cardRadius = Tokens.Radius.card             // (.tool-card: border 1px, its radius and padding)
    val cardV = Tokens.Gap.cardV
    val cardH = Tokens.Gap.cardH
    val codeRadius = Tokens.Radius.code             // (.codeblock)
    val inside = Tokens.Gap.inside                  // (widgets.INSIDE_GAP: the inside set this far into the frame)
    val bar = Tokens.Gap.bar                        // (an Adw.HeaderBar's height)
    val dot = Tokens.Gap.dot                        // (StatusDot's)
}

val LocalBaude = staticCompositionLocalOf { LightBaude }

/** Text as Baude's is: libadwaita's 11pt body (about 15sp), .caption and .sysnote smaller, .heading bold. */
object Type {
    val body = TextStyle(fontSize = Tokens.Font.body, lineHeight = Tokens.Font.bodyLine)
    val bold = body.copy(fontWeight = Tokens.Font.semibold)  // (.bot-name)
    val caption = TextStyle(fontSize = Tokens.Font.caption, lineHeight = Tokens.Font.captionLine)
    val folder = TextStyle(fontSize = Tokens.Font.caption, fontWeight = Tokens.Font.bold)  // (.sidebar-folder, at 0.6)
    val title = TextStyle(fontSize = Tokens.Font.body, fontWeight = Tokens.Font.bold)
    val dialogTitle = TextStyle(fontSize = Tokens.Font.title, fontWeight = Tokens.Font.bold)  // (Adw.AlertDialog's heading)
    val mono = TextStyle(fontSize = Tokens.Font.mono, fontFamily = CodeFont.family)  // (.mono: Settings → Code Font's, from the next start)
    val deviceCode = TextStyle(fontSize = Tokens.Font.deviceCode, fontFamily = CodeFont.family, fontWeight = Tokens.Font.bold,
        letterSpacing = 0.08.em)  // (.device-code: a device-code sign-in's one-time code)
}

/** The app's look as Settings has it: light or dark (Appearance: the system's, or one of them), the accent (Accent
 * Colour: accent_bg_color, and accent_color made of it), the theme's colours over them (Theme), and the sizes (Text Scale: the text's, Global Scale:
 * everything's, as the density's). */
@Composable
fun BaudeTheme(content: @Composable () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    Settings.load(ctx)
    val dark = when (Settings.appearance.value) {
        "light" -> false
        "dark" -> true
        else -> isSystemInDarkTheme()
    }
    val accent = Settings.accentBg(ctx)
    val c = (if (dark) DarkBaude else LightBaude).copy(accentBg = accent, accent = Settings.standalone(accent, dark))
        .themed(Settings.themeColours.value)
    val scheme = (if (dark) darkColorScheme() else lightColorScheme()).copy(
        primary = c.accentBg, onPrimary = c.accentFg, background = c.inside, onBackground = c.fg,
        surface = c.inside, onSurface = c.fg, surfaceVariant = c.frame, onSurfaceVariant = c.dim,
        surfaceContainer = c.frame, surfaceContainerHigh = c.dialog, surfaceContainerHighest = c.dialog,
        surfaceContainerLow = c.window, outline = c.rule, outlineVariant = c.rule, error = c.destructive,
        secondaryContainer = c.rowSelected, onSecondaryContainer = c.fg,
    )
    val density = androidx.compose.ui.platform.LocalDensity.current
    CompositionLocalProvider(LocalBaude provides c, androidx.compose.ui.platform.LocalDensity provides
        androidx.compose.ui.unit.Density(density.density * Settings.BASE_SCALE * Settings.globalScale.value,
            density.fontScale * Settings.textScale.value)) {
        MaterialTheme(colorScheme = scheme, typography = Typography(bodyLarge = Type.body, bodyMedium = Type.body)) {
            // (turned on its side: kept clear of the phone's buttons and a camera cut-out at either edge, on every screen)
            androidx.compose.foundation.layout.Box(androidx.compose.ui.Modifier.fillMaxSize().background(c.frame)
                .windowInsetsPadding(androidx.compose.foundation.layout.WindowInsets.systemBars
                    .union(androidx.compose.foundation.layout.WindowInsets.displayCutout)
                    .only(androidx.compose.foundation.layout.WindowInsetsSides.Horizontal))) { content() }
        }
    }
}

/** libadwaita's avatar colours (AdwAvatar's .color1 to .color14, Tokens.avatar: a gradient's two ends, and its
 * initials'), picked as it picks them: g_str_hash of the name, mod 14. */
fun avatarColors(name: String): Triple<Color, Color, Color> {
    var h = 5381u
    for (b in name.toByteArray(Charsets.UTF_8)) h = (h shl 5) + h + b.toInt().toUInt()  // (glib: signed char)
    val (top, bottom, fg) = Tokens.avatar[(h % Tokens.avatar.size.toUInt()).toInt()]
    return Triple(top, bottom, fg)
}

/** Initials as AdwAvatar makes them: the first letter of the first word and of the last. */
fun initials(name: String): String {
    val words = name.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
    if (words.isEmpty()) return ""
    val first = words.first().first().uppercase()
    return if (words.size > 1) first + words.last().first().uppercase() else first
}
