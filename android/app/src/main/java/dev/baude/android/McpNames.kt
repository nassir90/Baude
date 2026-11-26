package dev.baude.android

/** A tool's name as every app says it (baudelib/toolnames.py): an MCP server's tool (mcp__<server>__<tool>) as
 * "Server / Tool", its server's title and its tool's as the hosts say them (each host's info's tool_names: Baude's
 * own servers' titles, and the servers its claude says it has, by their configured names), else its name in words. */
object McpNames {
    private val uuid = Regex("[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")
    private val from = Regex("^(claude_ai_|plugin_[^_]+_)")

    private fun servers(): Map<String, String> = Mesh.infos.values.fold(HashMap()) { m, i -> i.toolNames?.servers?.let(m::putAll); m }
    private fun tools(): Map<String, String> = Mesh.infos.values.fold(HashMap()) { m, i -> i.toolNames?.tools?.let(m::putAll); m }

    /** (its server's key, its tool's name) for an MCP server's tool, the server the longest known key it starts with. */
    fun split(name: String): Pair<String, String>? {
        if (!name.startsWith("mcp__") || name.split("__").size < 3) return null
        val rest = name.removePrefix("mcp__")
        servers().keys.sortedByDescending { it.length }.firstOrNull { rest.startsWith("${it}__") && rest.length > it.length + 2 }
            ?.let { return it to rest.substring(it.length + 2) }
        return rest.substringBefore("__") to rest.substringAfter("__")
    }

    /** (its server's title, null for one known only by an id; its tool's title), or null for a tool not an MCP server's. */
    fun describe(name: String): Pair<String?, String>? {
        val (server, rawTool) = split(name) ?: return null
        val bare = server.replace(from, "")
        val title = tools()[name] ?: run {
            val tool = if (rawTool.lowercase().startsWith(bare.lowercase() + "_")) rawTool.substring(bare.length + 1) else rawTool
            humanize(tool).replaceFirstChar(Char::uppercase).ifBlank { rawTool }
        }
        val source = servers()[server] ?: if (uuid.matches(bare)) null
            else BRANDS[bare.lowercase()] ?: humanize(bare).split(" ").joinToString(" ") { it.replaceFirstChar(Char::uppercase) }
        return source to title
    }

    /** "Server / Tool" for an MCP server's tool, any other as it is. */
    fun label(name: String): String = describe(name)?.let { (source, title) -> "${source ?: Wording.ToolCalls.mcp} / $title" } ?: name

    /** ToolSearch's "select:a,b" query: the tools it names; null for a search. */
    fun selected(query: String): List<String>? =
        if (!query.startsWith("select:")) null else query.removePrefix("select:").split(",").map { it.trim() }.filter { it.isNotEmpty() }

    private val BRANDS = mapOf("github" to "GitHub", "gitlab" to "GitLab", "gmail" to "Gmail", "gdrive" to "Google Drive",
        "google_drive" to "Google Drive", "gcal" to "Google Calendar", "slack" to "Slack", "notion" to "Notion",
        "linear" to "Linear", "jira" to "Jira", "figma" to "Figma", "postgres" to "Postgres", "sqlite" to "SQLite",
        "playwright" to "Playwright", "sentry" to "Sentry")
}
