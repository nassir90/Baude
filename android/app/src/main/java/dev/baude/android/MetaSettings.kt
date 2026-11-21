package dev.baude.android

import androidx.compose.runtime.Composable

/** The host Meta runs on (MetaPage's choice): the one it's on, else the first that can run it. */
fun metaHost(): String? = Meta.host.value ?: Mesh.reached("app_tools").firstOrNull()?.first

/** What Intelligence lists (meta.listed_models): the models h offers (Claude's first, then its own others), then each
 * model another provider in the mesh runs that's not of a kind listed already. */
fun metaModels(h: String?): List<ModelChoice> {
    h ?: return emptyList()
    val out = ArrayList(models(h))
    val all = copies(h)
    val listed = all.filter { c -> out.any { it.value == c.value } }.toMutableList()
    for (c in all) {
        if (out.any { it.value == c.value } || listed.any { sameModel(it, c) }) continue
        listed.add(c)
        out.add(ModelChoice(c.value, c.m.displayName?.ifBlank { null } ?: c.value,
            if (c.m.thinking == true) listOf("off", "low", "medium", "high") else emptyList()))
    }
    return out
}

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
    val name = entry?.name ?: wanted
    Heading(Wording.MetaSettings.title)
    val copies = copiesOf(h.orEmpty(), value)
    val levels = entry?.efforts.orEmpty()
    val takes = metaTakesCommands(h, value)
    BoxedList(listOfNotNull<@Composable () -> Unit>(
        {
            ActionRow(Wording.MetaSettings.intelligence, Wording.MetaSettings.intelligenceDetail, suffix = {
                ModelPicker(entry?.name ?: wanted, list, entry?.value) { v ->
                    Settings.metaModel.value = v
                    val now = metaEntry(v, list, h)
                    if (Settings.metaEffort.value !in (now?.efforts.orEmpty())) Settings.metaEffort.value = null
                    Settings.save(ctx)
                }
            })
        },
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
        {
            Choice(Wording.MetaSettings.effort, listOf("") + levels, Settings.metaEffort.value?.takeIf { it in levels } ?: "",
                { if (it.isEmpty()) Wording.MetaSettings.effortDefault else effortLabel(it) },
                subtitle = if (levels.isEmpty()) Wording.MetaSettings.noEffort(model = name) else Wording.MetaSettings.effortDetail,
                enabled = levels.isNotEmpty()) {
                Settings.metaEffort.value = it.ifEmpty { null }
                Settings.save(ctx)
            }
        },
        {
            SwitchRow(Wording.MetaSettings.runCommands,
                if (!takes) Wording.MetaSettings.onlyReads(model = name)
                else if (value in Meta.noAuto) Wording.MetaSettings.asksEach(note = Wording.MetaSettings.noAuto(model = name))
                else Wording.MetaSettings.runCommandsDetail,
                takes && Settings.metaCommands.value, enabled = takes) {
                Settings.metaCommands.value = it
                Settings.save(ctx)
            }
        },
    ), "") { it() }
}
