package dev.baude.android

import androidx.compose.foundation.isSystemInDarkTheme
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

/** Baude's colours (libadwaita's, as GTK resolves them, light and dark: widgets.CSS's baude_frame, _inside, _rule and
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
    val warning: Color,
    val success: Color,
    val dialog: Color,     // (dialog_bg_color)
    val removed: Color,    // (diff.removed: a diff's line taken out, red)
    val added: Color,      // (diff.added: one put in, green)
) {
    val dim: Color get() = fg.copy(alpha = fg.alpha * 0.6f)        // (.sysnote, .dim-label: 0.55–0.6)
    val bubble: Color get() = accentBg.copy(alpha = 0.15f)          // (.user-bubble)
    val quiet: Color get() = fg.copy(alpha = fg.alpha * 0.07f)      // (.queued-bubble, .sender-bubble)
    val quietFg: Color get() = fg.copy(alpha = fg.alpha * 0.55f)    // (.queued-bubble's text)
    val rowSelected: Color get() = fg.copy(alpha = 0.10f)           // (a navigation sidebar's selected row)
}

private fun baudeColors(t: ThemeTokens) = with(t.libadwaita) {
    BaudeColors(
        frame = t.baude.frame, inside = t.baude.inside, window = windowBgColor, fg = windowFgColor, rule = t.baude.rule,
        raised = t.baude.raised, card = cardBgColor, accentBg = accentBgColor, accentFg = accentFgColor,
        accent = accentColor, destructive = destructiveColor, warning = warningColor, success = successColor,
        dialog = dialogBgColor, removed = t.diff.removed, added = t.diff.added,
    )
}

val LightBaude = baudeColors(Tokens.light)

val DarkBaude = baudeColors(Tokens.dark)

/** Baude's measures (widgets.CSS and baude.py's margins: schema/tokens'), in dp. */
object Gap {
    val sidebarRowV = Tokens.Gap.sidebarRowV        // (.session-row's padding)
    val sidebarRowH = Tokens.Gap.sidebarRowH
    val botRow = Tokens.Gap.botRow                  // (row.bot-row's min-height)
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
    val composerButton = Tokens.Gap.composerButton  // (.composer-actions button)
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
    val mono = TextStyle(fontSize = Tokens.Font.mono, fontFamily = FontFamily.Monospace)  // (.mono)
}

/** The app's look as Settings has it: light or dark (Appearance: the system's, or one of them), the accent (Accent
 * Colour: accent_bg_color, and accent_color made of it), and the sizes (Text Scale: the text's, Global Scale:
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
        MaterialTheme(colorScheme = scheme, typography = Typography(bodyLarge = Type.body, bodyMedium = Type.body),
            content = content)
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
