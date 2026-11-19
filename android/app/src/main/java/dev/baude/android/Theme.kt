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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Baude's colours (libadwaita's, as GTK resolves them, light and dark: widgets.CSS's baude_frame, _inside, _rule and
 * _raised made of them), so the app looks as Baude does. */
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
) {
    val dim: Color get() = fg.copy(alpha = fg.alpha * 0.6f)        // (.sysnote, .dim-label: 0.55–0.6)
    val bubble: Color get() = accentBg.copy(alpha = 0.15f)          // (.user-bubble)
    val rowSelected: Color get() = fg.copy(alpha = 0.10f)           // (a navigation sidebar's selected row)
}

private val ink = Color(0xFF000006)

val LightBaude = BaudeColors(
    frame = Color(0xFFEBEBED), inside = Color(0xFFFFFFFF), window = Color(0xFFFAFAFB), fg = ink.copy(alpha = 0.8f),
    rule = ink.copy(alpha = 0.12f * 0.9f), raised = ink.copy(alpha = 0.8f * 0.06f), card = Color(0xFFFFFFFF),
    accentBg = Color(0xFF3584E4), accentFg = Color.White, accent = Color(0xFF0461BE), destructive = Color(0xFFC30000),
    warning = Color(0xFF905400), success = Color(0xFF007C3D), dialog = Color(0xFFFAFAFB),
)

val DarkBaude = BaudeColors(
    frame = Color(0xFF2E2E32), inside = Color(0xFF1D1D20), window = Color(0xFF222226), fg = Color.White,
    rule = Color.White.copy(alpha = 0.15f * 0.9f), raised = Color.White.copy(alpha = 0.06f),
    card = Color.White.copy(alpha = 0.08f), accentBg = Color(0xFF3584E4), accentFg = Color.White,
    accent = Color(0xFF81D0FF), destructive = Color(0xFFFF938C), warning = Color(0xFFFFC252),
    success = Color(0xFF78E9AB), dialog = Color(0xFF36363A),
)

/** Baude's measures (widgets.CSS and baude.py's margins), in dp. */
object Gap {
    val sidebarRowV = 6.dp      // (.session-row: padding 6px 4px)
    val sidebarRowH = 4.dp
    val botRow = 52.dp          // (row.bot-row: min-height 52px)
    val avatar = 32.dp          // (bot_row's Adw.Avatar, size 32)
    val avatarGap = 8.dp        // (bot_row's box spacing)
    val headerTop = 6.dp        // (header_row: margin-top 6, start 4)
    val headerStart = 4.dp
    val chatH = 16.dp           // (the chat's box: margin start and end 16, top and bottom 12)
    val chatV = 12.dp
    val messageGap = 8.dp       // (spacing between a chat's parts)
    val bubbleRadius = 12.dp    // (.user-bubble: radius 12px, padding 8px 12px)
    val bubbleV = 8.dp
    val bubbleH = 12.dp
    val composerRadius = 12.dp  // (.composer-box: radius 12px, a 1px ring of the rule)
    val composerButton = 34.dp  // (.composer-actions button: 34px)
    val cardRadius = 8.dp       // (.tool-card: border 1px, radius 8px, padding 6px 10px)
    val cardV = 6.dp
    val cardH = 10.dp
    val codeRadius = 6.dp       // (.codeblock)
    val inside = 6.dp           // (widgets.INSIDE_GAP: the inside set this far into the frame)
    val bar = 46.dp             // (an Adw.HeaderBar's height)
}

val LocalBaude = staticCompositionLocalOf { LightBaude }

/** Text as Baude's is: libadwaita's 11pt body (about 15sp), .caption and .sysnote at 0.85em, .heading bold. */
object Type {
    val body = TextStyle(fontSize = 15.sp, lineHeight = 21.sp)
    val bold = body.copy(fontWeight = FontWeight.SemiBold)  // (.bot-name: 600)
    val caption = TextStyle(fontSize = 12.5.sp, lineHeight = 16.sp)
    val folder = TextStyle(fontSize = 12.5.sp, fontWeight = FontWeight.Bold)  // (.sidebar-folder: 0.85em, bold, 0.6)
    val title = TextStyle(fontSize = 15.sp, fontWeight = FontWeight.Bold)
    val mono = TextStyle(fontSize = 13.5.sp, fontFamily = FontFamily.Monospace)  // (.mono: 0.9em)
}

@Composable
fun BaudeTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val c = if (dark) DarkBaude else LightBaude
    val scheme = (if (dark) darkColorScheme() else lightColorScheme()).copy(
        primary = c.accentBg, onPrimary = c.accentFg, background = c.inside, onBackground = c.fg,
        surface = c.inside, onSurface = c.fg, surfaceVariant = c.frame, onSurfaceVariant = c.dim,
        surfaceContainer = c.frame, surfaceContainerHigh = c.dialog, surfaceContainerHighest = c.dialog,
        surfaceContainerLow = c.window, outline = c.rule, outlineVariant = c.rule, error = c.destructive,
        secondaryContainer = c.rowSelected, onSecondaryContainer = c.fg,
    )
    CompositionLocalProvider(LocalBaude provides c) {
        MaterialTheme(colorScheme = scheme, typography = Typography(bodyLarge = Type.body, bodyMedium = Type.body),
            content = content)
    }
}

/** libadwaita's avatar colours (AdwAvatar's .color1 to .color14: a gradient's two ends, and its initials'), picked
 * as it picks them: g_str_hash of the name, mod 14. */
private val AVATARS = listOf(
    Triple(0xFF83B6EC, 0xFF337FDC, 0xFFCFE1F5), Triple(0xFF7AD9F1, 0xFF0F9AC8, 0xFFCAEAF2),
    Triple(0xFF8DE6B1, 0xFF29AE74, 0xFFCEF8D8), Triple(0xFFB5E98A, 0xFF6AB85B, 0xFFE6F9D7),
    Triple(0xFFF8E359, 0xFFD29D09, 0xFFF9F4E1), Triple(0xFFFFCB62, 0xFFD68400, 0xFFFFEAD1),
    Triple(0xFFFFA95A, 0xFFED5B00, 0xFFFFE5C5), Triple(0xFFF78773, 0xFFE62D42, 0xFFF8D2CE),
    Triple(0xFFE973AB, 0xFFE33B6A, 0xFFFAC7DE), Triple(0xFFCB78D4, 0xFF9945B5, 0xFFE7C2E8),
    Triple(0xFF9E91E8, 0xFF7A59CA, 0xFFD5D2F5), Triple(0xFFE3CF9C, 0xFFB08952, 0xFFF2EADC),
    Triple(0xFFBE916D, 0xFF785336, 0xFFE5D6CA), Triple(0xFFC0BFBC, 0xFF6E6D71, 0xFFD8D7D3),
)

fun avatarColors(name: String): Triple<Color, Color, Color> {
    var h = 5381u
    for (b in name.toByteArray(Charsets.UTF_8)) h = (h shl 5) + h + b.toInt().toUInt()  // (glib: signed char)
    val (top, bottom, fg) = AVATARS[(h % 14u).toInt()]
    return Triple(Color(top), Color(bottom), Color(fg))
}

/** Initials as AdwAvatar makes them: the first letter of the first word and of the last. */
fun initials(name: String): String {
    val words = name.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
    if (words.isEmpty()) return ""
    val first = words.first().first().uppercase()
    return if (words.size > 1) first + words.last().first().uppercase() else first
}
