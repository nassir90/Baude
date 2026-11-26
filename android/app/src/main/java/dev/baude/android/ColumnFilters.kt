package dev.baude.android

import android.content.Context
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject

// (kubeview's KUBE_FILTER_MOST and KUBE_FILTER_LONGEST: more values than this and a column is a list to search, not one
// to tick through; a value longer than this is a sentence, not a word)
private const val FILTER_MOST = 30
private const val FILTER_LONGEST = 40

/** Whether a column offers a filter (kubeview.kube_filterable): it isn't a name, age or containers column, it sorts by
 * its text (not a number or a time), each of its values has a letter in it and is a word or a few, it has FILTER_MOST
 * or fewer of them, and some value is had by more than one row (empty cells aside), but for a status column. One that's
 * filtered keeps its button. Of n rows, value(r) is row r's value in the column and key(r) what it sorts by; the rows
 * are read only until the column has more than FILTER_MOST values. */
fun filterable(kind: String?, n: Int, value: (Int) -> String, key: (Int) -> Any?, filtered: Boolean): Boolean {
    if (filtered) return true
    if (kind == "name" || kind == "age" || kind == "containers" || n == 0) return false
    val seen = HashSet<String>()
    var words = 0
    for (r in 0 until n) {
        val k = key(r)
        if (k != null && k != JSONObject.NULL && k != "" && k !is String) return false
        val v = value(r)
        if (v.isEmpty()) continue
        if (v.length > FILTER_LONGEST || v.none { it.isLetter() }) return false
        words++
        if (seen.add(v) && seen.size > FILTER_MOST) return false
    }
    return seen.isNotEmpty() && (kind == "status" || seen.size < words)
}

/** A table's column filters (kubeview.ColumnFilters): the values hidden of each column, by title, for the kind shown,
 * remembered by kind in the settings under setting ({kind: {column title: [values hidden]}}, as the desktop keeps it). */
class ColumnFilters(private val setting: String) {
    var kind by mutableStateOf<String?>(null)
        private set
    var hidden by mutableStateOf<Map<String, Set<String>>>(emptyMap())
        private set

    private fun saved(): JSONObject = runCatching { JSONObject(prefs().getString(setting, null) ?: "{}") }.getOrDefault(JSONObject())

    /** The filters of a kind, as they were left. */
    fun load(kind: String?) {
        if (kind == this.kind) return
        this.kind = kind
        val mine = kind?.let { saved().optJSONObject(it) }
        hidden = mine?.keys()?.asSequence()?.associateWith { t ->
            mine.optJSONArray(t)?.let { a -> (0 until a.length()).map { a.optString(it) }.toSet() }.orEmpty()
        }?.filterValues { it.isNotEmpty() }.orEmpty()
    }

    /** Whether the filters (but skip's column) keep a row, whose value in column i is value(i). titles: the columns
     * in the table, by title ("" for one not in it, whose filter doesn't hide anything while it's away). */
    fun keeps(titles: List<String>, value: (Int) -> String, skip: Int = -1): Boolean = titles.indices.none { i ->
        i != skip && titles[i].isNotEmpty() && hidden[titles[i]]?.contains(value(i)) == true
    }

    /** A column's values hidden, and remembered. */
    fun hide(title: String, gone: Set<String>) {
        hidden = if (gone.isEmpty()) hidden - title else hidden + (title to gone)
        val k = kind ?: return
        val all = saved()
        val mine = JSONObject()
        for ((t, v) in hidden) mine.put(t, JSONArray(v.sorted()))
        if (mine.length() == 0) all.remove(k) else all.put(k, mine)
        prefs().edit().putString(setting, all.toString()).apply()
    }

    /** The checks of column i (MenuChecks' items and shown): its values in the rows the other filters keep (rows: each
     * row's value in the column, and whether the rest keep it), and any it hides, with their counts. */
    fun checks(title: String, rows: List<Pair<String, Boolean>>): Pair<List<Pair<String, String>>, List<String>?> {
        val counts = rows.filter { it.second }.groupingBy { it.first }.eachCount()
        val gone = hidden[title].orEmpty()
        val values = (counts.keys + gone).sortedWith(compareBy({ it.isEmpty() }, { it.lowercase() }))
        val items = values.map { it to Wording.Kube.valueCount(it.ifEmpty { Wording.Kube.noValue }, counts[it] ?: 0) }
        return items to if (gone.isEmpty()) null else values.filter { it !in gone }
    }

    /** MenuChecks picked (shown: the values ticked, null for every one) over the values listed. */
    fun pick(title: String, listed: List<String>, shown: List<String>?) {
        val gone = (hidden[title].orEmpty() - listed.toSet()) + (listed.toSet() - (shown ?: listed).toSet())
        hide(title, gone)
    }

    private fun prefs() = Mesh.app.getSharedPreferences("settings", Context.MODE_PRIVATE)
}

/** A column's funnel just after its heading's title (the desktop's .kube-col-filter is at the heading's end, which a
 * wide column on the phone puts out of sight): in the accent while it filters, else while its checks are open; tapped,
 * its checks. */
@Composable
fun FilterMark(title: String, filtering: Boolean, onClick: () -> Unit) {
    val c = LocalBaude.current
    Icon(Adwaita.Filter, Wording.Kube.filterColumn(title), tint = if (filtering) c.accent else c.fg,
        modifier = Modifier.size(16.dp.icon).clickable(onClick = onClick))
}

/** A column's checks, as the desktop's funnel opens them: All over a check per value with its count (MenuChecks, the
 * namespaces button's). rows: each row's value in the column, and whether the other filters keep it. */
@Composable
fun FilterMenu(open: Boolean, dismiss: () -> Unit, f: ColumnFilters, title: String, rows: () -> List<Pair<String, Boolean>>) {
    Popover(open, dismiss, ticks = Ticks.START) {
        val listed = rows()
        val (items, shown) = f.checks(title, listed)
        val had = listed.filter { it.second }.map { it.first }.toSet()
        MenuChecks(Wording.Fixes.all, items, shown, { f.pick(title, items.map { it.first }, it) },
            dimmed = items.map { it.first }.filter { it !in had }.toSet())
    }
}
