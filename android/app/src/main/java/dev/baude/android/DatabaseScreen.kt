package dev.baude.android

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.layout.size

/** A name cut in the middle with … past n characters (the pickers' faces). */
private fun cutMiddle(s: String, n: Int) = if (s.length > n) s.take((n - 1) / 2) + "…" + s.takeLast(n - 1 - (n - 1) / 2) else s

/** The cut of a cell's text the rows grid shows. */
private const val CELL_MAX = 120

/** Database Schemas (schemaview.SchemaView): the running databases' tables as a diagram, read through the host: pick a
 * server, a database and a schema; a table's first rows under it. table and column (and hint, code) name what to find. */
@Composable
fun DatabaseScreen(hostId: String, back: () -> Unit, table: String? = null, column: String? = null, hint: String = "",
                   code: Wanted? = null) {
    val c = LocalBaude.current
    val density = LocalDensity.current
    val measure = rememberTextMeasurer(256)
    val diagram = remember { SchemaDiagram(density) }
    val t = themeTokens().schema
    var servers by remember { mutableStateOf<List<Msg.DbServer>>(emptyList()) }
    var dbs by remember { mutableStateOf<Map<String, List<String>>>(emptyMap()) }
    var server by remember { mutableStateOf<String?>(null) }
    var database by remember { mutableStateOf<String?>(null) }
    var namespace by remember { mutableStateOf("public") }
    var all by remember { mutableStateOf<Pair<List<Msg.DbTable>, List<Msg.DbLink>>?>(null) }
    var status by remember { mutableStateOf(Wording.Databases.lookingForServers) }
    var lacks by remember { mutableStateOf<Msg.Dep?>(null) } // (Docker isn't on the host, and no server was found)
    var tries by remember { mutableStateOf(0) } // (Try again's)
    var summary by remember { mutableStateOf("") }
    var faded by remember { mutableStateOf(true) }
    var find by remember { mutableStateOf("") }
    var matchAt by remember { mutableIntStateOf(-1) }
    var rowsOf by remember { mutableStateOf<Msg.DbTable?>(null) }
    var rowsTitle by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf<List<org.json.JSONObject>>(emptyList()) }
    var rowsFaded by remember { mutableStateOf(false) }
    var rowsFraction by remember { mutableFloatStateOf(0.4f) }
    var frame by remember { mutableIntStateOf(0) }
    var framing by remember { mutableStateOf<String?>(null) }
    var selected by remember { mutableStateOf<Msg.DbTable?>(null) }
    var gen by remember { mutableIntStateOf(0) }
    var rowsGen by remember { mutableIntStateOf(0) }
    diagram.onSelect = { selected = it }

    fun showSummary() { status = summary }

    fun cards(tables: List<Msg.DbTable>): List<SchemaCard> = tables.map {
        measureCard(it, measure, density, with(density) { t.headH.toPx() }, with(density) { t.rowH.toPx() },
            with(density) { t.pad.toPx() }, with(density) { t.maxW.toPx() })
    }

    fun showNamespace(ns: String, focusTable: String?, took: Double?) {
        val (tables, links) = all ?: return
        namespace = ns
        val shown = tables.filter { it.schema == ns }
        val cs = cards(shown)
        val index = shown.withIndex().associate { (i, tb) -> tb.name to i }
        val ls = links.filter { it.fromSchema == ns && it.toSchema == ns && it.fromTable in index && it.toTable in index }
            .map { SchemaLink(index.getValue(it.fromTable), it.fromColumn.orEmpty(), index.getValue(it.toTable), it.toColumn.orEmpty()) }
        val pos = schemaLayout(cs.map { it.w to it.h }, ls.map { it.a to it.b }, 56f * density.density)
        for ((card, p) in cs.zip(pos)) {
            card.x = p.first
            card.y = p.second
        }
        diagram.set(cs, ls)
        summary = listOfNotNull(database, ns, Wording.Databases.tables(shown.size), Wording.Databases.foreignKeys(ls.size),
            Wording.Databases.readOnly, took?.takeIf { it > 0 }?.let { Wording.Databases.readIn("%.2f".format(it)) }).joinToString(" · ")
        showSummary()
        framing = focusTable ?: ""
        frame++
    }

    fun load(s: String, db: String, ns: String? = null, focusTable: String? = null, quiet: Boolean = false) {
        server = s
        database = db
        val mine = ++gen
        faded = true
        if (!quiet) status = Wording.Databases.reading(db)
        Mesh.ask(hostId, Msg.Databases(what = "schema", container = s, database = db).json()) { r ->
            if (mine != gen) return@ask
            faded = false
            val g = Msg.DatabasesReply.from(r)
            if (r.optString("op") == Op.ERROR || !g.error.isNullOrBlank()) {
                status = "$db: ${g.error ?: Msg.Error.from(r).said.orEmpty()}"
                return@ask
            }
            all = g.tables.orEmpty() to g.links.orEmpty()
            val spaces = g.tables.orEmpty().map { it.schema.orEmpty() }.distinct().sortedWith(compareBy({ it != "public" }, { it }))
            showNamespace(if (ns in spaces) ns!! else spaces.firstOrNull() ?: "public", focusTable, g.took?.toDouble())
        }
    }

    LaunchedEffect(hostId, tries) {
        status = Wording.Databases.lookingForServers
        Mesh.ask(hostId, Msg.Databases(what = "servers").json()) { r ->
            faded = false
            val g = Msg.DatabasesReply.from(r)
            g.missing?.let { status = ""; lacks = it; return@ask }
            val list = g.servers.orEmpty()
            if (r.optString("op") == Op.ERROR || !g.error.isNullOrBlank() || list.isEmpty()) {
                status = g.error ?: (if (r.optString("op") == Op.ERROR) Msg.Error.from(r).said else null) ?: Wording.Databases.noPostgres
                return@ask
            }
            servers = list
            dbs = g.databases?.let { o -> o.keys().asSequence().associateWith { k -> o.optJSONArray(k).strings() } }.orEmpty()
            val s = list.first().container.orEmpty()
            val names = dbs[s].orEmpty()
            val pick = names.filter { it != "postgres" }.ifEmpty { names }
            if (pick.isNotEmpty()) load(s, pick.first(), null, table, quiet = table != null || column != null) else status = ""
        }
    }

    // (laid out: the table asked for centred, else all of it fitted)
    LaunchedEffect(frame, diagram.width) {
        val want = framing ?: return@LaunchedEffect
        if (diagram.width == 0f) return@LaunchedEffect
        framing = null
        val target = if (want.isNotEmpty()) diagram.cards.indexOfFirst { it.table.name == want }.takeIf { it >= 0 } else null
            ?: if (column != null && want.isEmpty()) diagram.cards.indexOfFirst { cd -> cd.table.columns.orEmpty().any { it.name == column } }
                .takeIf { it >= 0 } else null
        if (target != null) diagram.focus(target) else diagram.fit()
    }
    LaunchedEffect(column) { diagram.markColumn = column }

    fun nextMatch(step: Int) {
        val m = diagram.matches
        if (m.isEmpty()) return
        matchAt = ((matchAt + step) % m.size + m.size) % m.size
        val (i, row) = m[matchAt]
        diagram.markColumn = row?.let { diagram.cards[i].rows[it].first.name }
        diagram.focus(i)
        status = Wording.Glossary.count(matchAt + 1, m.size)
    }

    fun findChanged(text: String) {
        find = text
        val m = diagram.setQuery(text)
        val q = diagram.query
        matchAt = -1
        if (q.isEmpty()) {
            diagram.markColumn = column
            showSummary()
            return
        }
        if (m.isEmpty()) {
            status = Wording.Glossary.count(0, 0)
            return
        }
        val exact = m.indexOfFirst { (i, row) -> row == null && diagram.cards[i].table.name.orEmpty().lowercase() == q }.coerceAtLeast(0)
        matchAt = exact - 1
        nextMatch(1)
    }

    fun loadRows(tb: Msg.DbTable) {
        val mine = ++rowsGen
        rowsOf = tb
        rowsFaded = true
        rowsTitle = "${tb.schema}.${tb.name}: " + Wording.Databases.readingRows
        Mesh.ask(hostId, Msg.Databases(what = "rows", container = server, database = database, schema = tb.schema, table = tb.name).json()) { r ->
            if (mine != rowsGen) return@ask
            val g = Msg.DatabasesReply.from(r)
            val err = g.error ?: if (r.optString("op") == Op.ERROR) Msg.Error.from(r).said else null
            rows = if (err == null) g.rows.orEmpty() else emptyList()
            rowsTitle = "${tb.schema}.${tb.name}: " + (err ?: Wording.Databases.firstRows(rows.size))
            rowsFaded = false
        }
    }

    BackHandler(rowsOf != null) { rowsOf = null }
    Column(Modifier.fillMaxSize().background(c.inside)) {
        Bar(Wording.Databases.title, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) },
            end = { IconButton(Adwaita.WindowClose, Wording.Images.close, onClick = back) })
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(6.dp), verticalAlignment = Alignment.CenterVertically) {
            fun engine(s: Msg.DbServer) = if (s.engine == "arango") "ArangoDB" else "Postgres"
            fun face(s: Msg.DbServer) = if (!s.version.isNullOrEmpty()) "${s.container} (${engine(s)} ${s.version})" else s.container.orEmpty()
            val current = servers.firstOrNull { it.container == server }
            Picker(cutMiddle(current?.let(::face).orEmpty(), 34), Wording.Databases.serverTip, servers.map { face(it) to it.container.orEmpty() },
                server) { pick ->
                val names = dbs[pick].orEmpty()
                val first = names.filter { it != "postgres" }.ifEmpty { names }.firstOrNull()
                if (first != null) load(pick, first)
            }
            val names = dbs[server].orEmpty()
            Picker(cutMiddle(database.orEmpty(), 26), Wording.Databases.database, names.map { it to it }, database) { load(server.orEmpty(), it) }
            val spaces = all?.first.orEmpty().map { it.schema.orEmpty() }.distinct().sortedWith(compareBy({ it != "public" }, { it }))
            Picker(cutMiddle(namespace, 18), Wording.Databases.schemaTip, spaces.map { it to it }, namespace) { showNamespace(it, null, null) }
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 6.dp, vertical = 2.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(Modifier.weight(1f).clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f)).padding(horizontal = 8.dp, vertical = 7.dp),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Icon(Adwaita.SystemSearch, null, tint = c.dim, modifier = Modifier.size(16.dp.icon))
                Box(Modifier.weight(1f)) {
                    if (find.isEmpty()) Text(Wording.Common.search, style = Type.body, color = c.dim, maxLines = 1)
                    BasicTextField(find, ::findChanged, singleLine = true, textStyle = Type.body.copy(color = c.fg),
                        cursorBrush = SolidColor(c.accentBg), keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                        keyboardActions = KeyboardActions(onSearch = { nextMatch(1) }), modifier = Modifier.fillMaxWidth())
                }
            }
            if (code != null) Button(Wording.Databases.definition) { back(); FileLinks.open(code) }
            Button(Wording.Databases.rowsButton, enabled = selected != null) { selected?.let(::loadRows) }
            IconButton(Adwaita.ZoomOut, Wording.Databases.zoomOutTip) { diagram.zoomBy(1f / 1.2f) }
            IconButton(Adwaita.ZoomIn, Wording.Databases.zoomInTip) { diagram.zoomBy(1.2f) }
            IconButton(Adwaita.ZoomFitBest, Wording.Databases.fitTip) { diagram.fit() }
        }
        BoxWithConstraints(Modifier.weight(1f).fillMaxWidth()) {
            val total = constraints.maxHeight.toFloat()
            val shownRows = rowsOf != null
            Column(Modifier.fillMaxSize()) {
                Box(Modifier.weight(1f).fillMaxWidth()) {
                    val lacking = lacks
                    if (lacking != null) MissingDep(lacking, Wording.Window.databaseSchemas) { lacks = null; tries++ }
                    else Box(Modifier.fillMaxSize().alpha(if (faded) 0.4f else 1f)) { SchemaCanvas(diagram) { loadRows(it) } }
                    Text(status, style = Type.caption, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.align(Alignment.BottomStart).padding(start = 12.dp, bottom = 10.dp, end = 12.dp))
                }
                if (shownRows) {
                    Box(Modifier.fillMaxWidth().height(6.dp).background(c.rule).pointerInput(total) {
                        detectVerticalDragGestures { _, dy -> rowsFraction = (rowsFraction - dy / total).coerceIn(0.1f, 0.8f) }
                    })
                    Column(Modifier.fillMaxWidth().height(with(density) { (total * rowsFraction).toDp() })) {
                        Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
                            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(rowsTitle, style = Type.title, color = c.fg, modifier = Modifier.weight(1f), maxLines = 2,
                                overflow = TextOverflow.Ellipsis)
                            IconButton(Adwaita.WindowClose, Wording.Databases.hideRows) { rowsOf = null }
                        }
                        Box(Modifier.weight(1f).fillMaxWidth().alpha(if (rowsFaded) 0.4f else 1f)) {
                            rowsOf?.let { RowsGrid(it.columns.orEmpty().map { col -> col.name.orEmpty() }, rows) }
                        }
                    }
                }
            }
        }
    }
}

/** A table's rows as Baude draws one (md-table): a bold head, each cell selectable and cut at 120 characters, NULL dim. */
@Composable
private fun RowsGrid(names: List<String>, rows: List<org.json.JSONObject>) {
    val c = LocalBaude.current
    val measure = rememberTextMeasurer(512)
    val density = LocalDensity.current
    val bold = Type.body.copy(fontWeight = FontWeight.Bold)
    fun cut(s: String) = if (s.length > CELL_MAX) s.take(CELL_MAX) + "…" else s
    val cells = rows.map { r -> names.map { n -> if (r.isNull(n)) null else cut(r.optString(n)) } }
    val widths = names.indices.map { i ->
        val longest = (cells.map { it[i] ?: Wording.Databases.`null` } + listOf(names[i])).maxByOrNull { it.length }.orEmpty()
        with(density) { measure.measure(longest, if (longest == names[i]) bold else Type.body, softWrap = false).size.width.toDp() } + 24.dp
    }
    SelectionContainer {
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).horizontalScroll(rememberScrollState())
            .padding(start = 12.dp, end = 12.dp, bottom = 12.dp)) {
            Row(Modifier.background(c.raised)) {
                names.forEachIndexed { i, n -> Text(n, style = bold, color = c.fg, maxLines = 1,
                    modifier = Modifier.width(widths[i]).padding(horizontal = 12.dp, vertical = 5.dp)) }
            }
            for (r in cells) Row {
                r.forEachIndexed { i, v -> Text(v ?: Wording.Databases.`null`, style = Type.body, color = if (v == null) c.dim else c.fg,
                    maxLines = 1, modifier = Modifier.width(widths[i]).padding(horizontal = 12.dp, vertical = 4.dp)) }
            }
        }
    }
}
