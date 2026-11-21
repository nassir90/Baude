package dev.baude.android

import androidx.compose.runtime.Composable

/** Database Schemas (schemaview.SchemaView): the running databases' tables as a diagram, read through the host. */
@Composable
fun DatabaseScreen(hostId: String, back: () -> Unit, table: String? = null, column: String? = null, hint: String = "",
                   code: Wanted? = null) {
    Bar(Wording.Databases.title, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) })
}
