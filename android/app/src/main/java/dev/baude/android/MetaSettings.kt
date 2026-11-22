package dev.baude.android

import androidx.compose.runtime.Composable

/** The host Meta runs on (MetaPage's choice): the one it's on, else the first that can run it. */
fun metaHost(): String? = Meta.host.value ?: Mesh.reached("app_tools").firstOrNull()?.first

/** What Intelligence lists (meta.listed_models): what a chat on h lists (meshModels), Meta's own copy where it's of a
 * kind listed. */
fun metaModels(h: String?): List<ModelChoice> = meshModels(h, Settings.metaModel.value)

/** The entry of list for a model value (meta.model_entry): its own; the same model served another way (a copy in the
 * mesh); else, for an alias (haiku), the first listed model with it as a word of its value. */
fun metaEntry(model: String, list: List<ModelChoice>, h: String? = null): ModelChoice? {
    chosen(list, model)?.let { return it }
    h?.let { host ->
        val all = copies(host)
        all.firstOrNull { it.value == model }?.let { at ->
            list.firstOrNull { l -> all.firstOrNull { it.value == l.value }?.let { sameModel(it, at) } == true }?.let { return it }
        }
    }
    if (model !in setOf("opus", "fable", "sonnet", "haiku")) return null
    val word = Regex("(^|-)${Regex.escape(model)}(-|\\[|$)")
    return list.firstOrNull { word.containsMatchIn(it.value) }
}

/** Whether Meta runs commands on a model (meta.takes_commands): Claude's, run by claude on its host. */
fun metaTakesCommands(h: String?, model: String?): Boolean = program(model) == "claude" && copiesOf(h.orEmpty(), model).isEmpty()

private fun effortLabel(level: String) = EFFORT_LABELS[level] ?: level.replaceFirstChar(Char::uppercase)

/** Settings → Meta (meta.settings_group), the same rows Meta's page has on the desktop: Intelligence, Provider, Effort
 * and Run commands, each applied from Meta's next message. */
@Composable
fun MetaSettingsGroup() {
    val h = metaHost()
    Mesh.tick.value
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val list = metaModels(h)
    val wanted = Settings.metaModel.value ?: "haiku"
    val entry = metaEntry(wanted, list, h)
    val value = entry?.value ?: wanted
    Heading(Wording.MetaSettings.title)
    val copies = copiesOf(h.orEmpty(), value)
    BoxedList(listOfNotNull<@Composable () -> Unit>(
        { IntelligenceRow() },
        if (copies.isEmpty()) null else ({
            ActionRow(Wording.MetaSettings.provider, Wording.MetaSettings.providerDetail, suffix = {
                Picker(copies.firstOrNull { it.value == value }?.let { "${it.host} / ${it.provider}" } ?: Wording.MetaSettings.provider,
                    Wording.MetaSettings.providerPicker, copies.map { "${it.host} / ${it.provider}" to it.value }, value,
                    enabled = copies.size > 1) { v ->
                    Settings.metaModel.value = v
                    Settings.save(ctx)
                }
            })
        }),
        { EffortRow() },
        { RunCommandsRow() },
    ), "") { it() }
}

/** Meta's Provider, Intelligence and Effort as pickers (ControlsRow), where a chat's own are: each applied from
 * Meta's next message, as their rows in Settings → Meta are. */
@Composable
fun MetaControls(inBar: Boolean = false) {
    val h = metaHost()
    Mesh.tick.value
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val list = metaModels(h)
    val wanted = Settings.metaModel.value ?: "haiku"
    val entry = metaEntry(wanted, list, h)
    val value = entry?.value ?: wanted
    ControlsRow(inBar) {
        val copies = copiesOf(h.orEmpty(), value)
        if (copies.isNotEmpty()) Picker(copies.firstOrNull { it.value == value }?.let { "${it.host} / ${it.provider}" }
            ?.let(::middleCut) ?: Wording.MetaSettings.provider, Wording.MetaSettings.providerPicker,
            copies.map { "${it.host} / ${it.provider}" to it.value }, value, enabled = copies.size > 1) { v -> putMetaModel(ctx, v) }
        ModelPicker((entry?.name ?: wanted).let { if (it.length > 30) it.take(29) + "…" else it }, list, entry?.value ?: wanted) { v ->
            putMetaModel(ctx, v)
        }
        val levels = entry?.efforts.orEmpty()
        val level = Settings.metaEffort.value?.takeIf { it in levels }
        Picker(level?.let(::effortLabel) ?: Wording.MetaSettings.effort,
            if (levels.isEmpty()) Wording.MetaSettings.noEffort(model = entry?.name ?: wanted) else Wording.MetaSettings.effortDetail,
            listOf(Wording.MetaSettings.effortDefault to "") + levels.map { effortLabel(it) to it }, level ?: "",
            enabled = levels.isNotEmpty()) {
            Settings.metaEffort.value = it.ifEmpty { null }
            Settings.save(ctx)
        }
    }
}

/** Meta's Intelligence row (fallback: a subtitle where the row has none; extra: after its picker). */
@Composable
fun IntelligenceRow(fallback: String? = null, extra: (@Composable () -> Unit)? = null) {
    val h = metaHost()
    Mesh.tick.value
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val list = metaModels(h)
    val wanted = Settings.metaModel.value ?: "haiku"
    val entry = metaEntry(wanted, list, h)
    ActionRow(Wording.MetaSettings.intelligence, Wording.MetaSettings.intelligenceDetail, suffix = {
        ModelPicker(entry?.name ?: wanted, list, entry?.value ?: wanted) { v -> putMetaModel(ctx, v) }
        extra?.invoke()
    })
}

/** Meta's model picked (its effort dropped if the new one doesn't take it). */
fun putMetaModel(ctx: android.content.Context, v: String) {
    val h = metaHost()
    Settings.metaModel.value = v
    val now = metaEntry(v, metaModels(h), h)
    if (Settings.metaEffort.value !in (now?.efforts.orEmpty())) Settings.metaEffort.value = null
    Settings.save(ctx)
}

/** Meta's Effort row. */
@Composable
fun EffortRow(fallback: String? = null, extra: (@Composable () -> Unit)? = null) {
    val h = metaHost()
    Mesh.tick.value
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val entry = metaEntry(Settings.metaModel.value ?: "haiku", metaModels(h), h)
    val levels = entry?.efforts.orEmpty()
    Choice(Wording.MetaSettings.effort, listOf("") + levels, Settings.metaEffort.value?.takeIf { it in levels } ?: "",
        { if (it.isEmpty()) Wording.MetaSettings.effortDefault else effortLabel(it) },
        subtitle = if (levels.isEmpty()) Wording.MetaSettings.noEffort(model = entry?.name ?: (Settings.metaModel.value ?: "haiku"))
            else Wording.MetaSettings.effortDetail, enabled = levels.isNotEmpty(), extra = extra) {
        Settings.metaEffort.value = it.ifEmpty { null }
        Settings.save(ctx)
    }
}

/** Meta's Run commands row. */
@Composable
fun RunCommandsRow(fallback: String? = null, extra: (@Composable () -> Unit)? = null) {
    val h = metaHost()
    Mesh.tick.value
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val wanted = Settings.metaModel.value ?: "haiku"
    val entry = metaEntry(wanted, metaModels(h), h)
    val value = entry?.value ?: wanted
    val name = entry?.name ?: wanted
    val takes = metaTakesCommands(h, value)
    SwitchRow(Wording.MetaSettings.runCommands,
        if (!takes) Wording.MetaSettings.onlyReads(model = name)
        else if (value in Meta.noAuto) Wording.MetaSettings.asksEach(note = Wording.MetaSettings.noAuto(model = name))
        else Wording.MetaSettings.runCommandsDetail,
        takes && Settings.metaCommands.value, enabled = takes, extra = extra) {
        Settings.metaCommands.value = it
        Settings.save(ctx)
    }
}
