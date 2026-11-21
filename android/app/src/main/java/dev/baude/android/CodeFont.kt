package dev.baude.android

import android.content.Context
import android.content.Intent
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight

/** The code fonts (fontsetup.CODE_FONTS: the open fonts under fonts/, bundled in the APK as assets), the one Settings →
 * Code Font picked drawn as the code font (Type.mono) from the app's next start, as Baude's is. */
object CodeFont {
    const val DEFAULT = "JetBrains Mono" // (fontsetup.DEFAULT_CODE_FONT)

    /** A font's files in assets/fonts/<folder>: upright, italic and bold where it has files of its own (a variable
     * font's "-VF" file is every weight). */
    private class Files(val folder: String, val regular: String, val italic: String?, val bold: String? = null,
                        val boldItalic: String? = null)

    private val FILES = mapOf(
        "JetBrains Mono" to Files("JetBrainsMono", "JetBrainsMono-VF.ttf", "JetBrainsMono-Italic-VF.ttf"),
        "IBM Plex Mono" to Files("IBMPlexMono", "IBMPlexMono-Regular.ttf", "IBMPlexMono-Italic.ttf", "IBMPlexMono-Bold.ttf"),
        "Fira Code" to Files("FiraCode", "FiraCode-VF.ttf", null),
        "Source Code Pro" to Files("SourceCodePro", "SourceCodePro-VF.ttf", "SourceCodePro-Italic-VF.ttf"),
        "Geist Mono" to Files("GeistMono", "GeistMono-VF.ttf", "GeistMono-Italic-VF.ttf"),
        "Cascadia Code" to Files("CascadiaCode", "CascadiaCode-VF.ttf", "CascadiaCode-Italic-VF.ttf"),
        "Inconsolata" to Files("Inconsolata", "Inconsolata-VF.ttf", null),
        "CommitMono" to Files("CommitMono", "CommitMono-400-Regular.otf", "CommitMono-400-Italic.otf",
            "CommitMono-700-Regular.otf", "CommitMono-700-Italic.otf"),
    )

    /** The fonts there are to pick, by name. */
    val FONTS: Map<String, Any> = FILES

    private fun saved(ctx: Context): String =
        ctx.applicationContext.getSharedPreferences("settings", Context.MODE_PRIVATE).getString("code_font", null)
            ?.takeIf { it in FILES } ?: DEFAULT

    /** The code font this process draws with: what Settings said when it started (a change since is the next start's). */
    val running: String by lazy { try { saved(Mesh.app) } catch (_: Throwable) { DEFAULT } }

    /** The family code is drawn in: the running font, or the system's monospace where its files can't be read. */
    val family: FontFamily by lazy {
        try {
            val f = FILES.getValue(running)
            val assets = Mesh.app.assets
            fun font(file: String, weight: Int, style: FontStyle): Font =
                if (file.endsWith("-VF.ttf")) Font("fonts/${f.folder}/$file", assets, FontWeight(weight), style,
                    FontVariation.Settings(FontVariation.weight(weight)))
                else Font("fonts/${f.folder}/$file", assets, FontWeight(weight), style)
            FontFamily(listOfNotNull(
                font(f.regular, 400, FontStyle.Normal),
                f.italic?.let { font(it, 400, FontStyle.Italic) },
                (f.bold ?: f.regular.takeIf { it.endsWith("-VF.ttf") })?.let { font(it, 700, FontStyle.Normal) },
                (f.boldItalic ?: f.italic?.takeIf { it.endsWith("-VF.ttf") })?.let { font(it, 700, FontStyle.Italic) },
            ))
        } catch (_: Throwable) {
            FontFamily.Monospace
        }
    }

    /** The app started again, where it was (the chat open: its host and session): its launch intent started from the
     * foreground activity, then this process ended. */
    fun restart(ctx: Context, chat: List<String> = emptyList()) {
        val launch = ctx.packageManager.getLaunchIntentForPackage(ctx.packageName)
            ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK) ?: return
        if (chat.size == 2) launch.putExtra("host", chat[0]).putExtra("session", chat[1])
        ctx.startActivity(launch)
        var a = ctx
        while (a is android.content.ContextWrapper && a !is android.app.Activity) a = a.baseContext
        (a as? android.app.Activity)?.finish()
        Runtime.getRuntime().exit(0)
    }
}
