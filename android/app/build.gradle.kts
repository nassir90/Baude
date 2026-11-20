plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "dev.baude.android"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.baude.android"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
    }
    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
        resources.excludes += "/META-INF/versions/**"
    }
}

kotlin {
    jvmToolchain(17)
}

/** emission.py's PAGE_JS and REPL_RUN, as the phone's browser runs them (assets emission/page.js, emission/repl.js):
 * read from it as the app's built, so its pages are read and driven by the one script Emission's are. */
abstract class EmissionJs : DefaultTask() {
    @get:InputFile abstract val source: RegularFileProperty
    @get:OutputDirectory abstract val outputDir: DirectoryProperty
    private val EM_SIGNATURES = linkedMapOf("ref" to "el", "get" to "id", "read" to "(root, all, max, near)",
        "onscreen" to "()", "came" to "(before, most, max)", "find" to "(q, limit)", "centre" to "async el",
        "line" to "(el, depth)", "editable" to "el", "setValue" to "(el, v)", "sleep" to "ms", "visible" to "el")

    @TaskAction
    fun write() {
        val py = source.get().asFile.readText()
        fun string(name: String): String {
            val m = Regex("(?s)\\n$name = (r?)\"\"\"(.*?)\"\"\"").find(py) ?: throw GradleException("no $name in emission.py")
            return if (m.groupValues[1] == "r") m.groupValues[2] else m.groupValues[2].replace("\\\\", "\\")
        }
        val page = string("PAGE_JS")
        // (em's functions as the phone's Control calls them, and the text it changes as em's put in a page: the
        // build fails if PAGE_JS's differ, so the Kotlin copied round it is looked at again)
        val returned = Regex("return \\{([a-zA-Z, ]+)\\};\\n\\}\\)\\(\\);").find(page)?.groupValues?.get(1)
            ?.split(",")?.map { it.trim() }
        if (returned != EM_SIGNATURES.keys.toList()) throw GradleException("PAGE_JS's em has changed: $returned")
        for ((name, args) in EM_SIGNATURES) if ("const $name = $args =>" !in page)
            throw GradleException("PAGE_JS's em.$name has changed: not $args")
        for (text in listOf("if (!globalThis.__em) globalThis.__em = ", "\nconst em = globalThis.__em;", "id = 'ref_' + (++n)"))
            if (text !in page) throw GradleException("PAGE_JS has changed: $text")
        val dir = outputDir.get().asFile.resolve("emission")
        dir.mkdirs()
        dir.resolve("page.js").writeText(page)
        dir.resolve("repl.js").writeText(string("REPL_RUN"))
    }
}

val emissionJs = tasks.register<EmissionJs>("emissionJs") {
    source.set(rootProject.layout.projectDirectory.file("../emission.py"))
    outputDir.set(layout.buildDirectory.dir("generated/emissionJs"))
}

androidComponents {
    onVariants { variant -> variant.sources.assets?.addGeneratedSourceDirectory(emissionJs, EmissionJs::outputDir) }
}

/** schema/tokens (W3C Design Tokens, 2025.10: baude.resolver.json, its sets and its theme's light and dark contexts),
 * as Kotlin made as the app's built (dev.baude.android.Tokens, in build/generated: never kept): Tokens, the tokens that
 * are the same light and dark, and Tokens.light and Tokens.dark (ThemeTokens: the groups the theme's contexts have, as
 * each resolves them). A colour's a Color; a dimension dp, sp or a Double as its group's $extensions has it
 * (dev.baude.android); a duration a number in its own unit (fadeS, expandMs); a group of 1, 2, 3... a list, and one of
 * x and y a Pair. */
abstract class BaudeTokens : DefaultTask() {
    @get:InputDirectory abstract val tokens: DirectoryProperty
    @get:OutputDirectory abstract val outputDir: DirectoryProperty

    private class Token(val type: String, val value: Any?, val ext: Map<String, Any?>)

    private fun plain(v: Any?): Any? = when (v) {
        is Map<*, *> -> v.entries.associateTo(LinkedHashMap<String, Any?>()) { it.key as String to plain(it.value) }
        is List<*> -> v.map { plain(it) }
        else -> v
    }

    @Suppress("UNCHECKED_CAST")
    private fun read(name: String) =
        plain(groovy.json.JsonSlurper().parse(tokens.get().asFile.resolve(name))) as MutableMap<String, Any?>

    @Suppress("UNCHECKED_CAST")
    private fun merge(into: MutableMap<String, Any?>, from: Map<String, Any?>) {
        for ((k, v) in from) {
            val old = into[k]
            if (v is Map<*, *> && "\$value" !in v && old is MutableMap<*, *> && "\$value" !in old)
                merge(old as MutableMap<String, Any?>, v as Map<String, Any?>)
            else into[k] = v
        }
    }

    @Suppress("UNCHECKED_CAST")
    @TaskAction
    fun write() {
        val resolver = read("baude.resolver.json")
        val theme = (resolver["modifiers"] as Map<String, Map<String, Any?>>).values.singleOrNull()
            ?: throw GradleException("baude.resolver.json: one modifier (the theme), not more")
        val contexts = theme["contexts"] as Map<String, List<Map<String, Any?>>>
        fun source(s: Map<String, Any?>) = if ("\$ref" in s) read(s["\$ref"] as String) else plain(s) as Map<String, Any?>
        fun tree(context: String): MutableMap<String, Any?> {
            val merged = LinkedHashMap<String, Any?>()
            for (step in resolver["resolutionOrder"] as List<Map<String, Any?>>) {
                val (kind, name) = (step["\$ref"] as String).split("/").drop(1)
                val part = (resolver[kind] as Map<String, Map<String, Any?>>).getValue(name)
                val sources = if (kind == "modifiers") (part["contexts"] as Map<String, List<Map<String, Any?>>>)
                    .getValue(context) else part["sources"] as List<Map<String, Any?>>
                for (s in sources) merge(merged, source(s))
            }
            return merged
        }
        val themed = contexts.values.flatten().flatMap { source(it).keys }.filter { !it.startsWith("$") }.toSet()

        // (every token at its path, its type and $extensions as its groups give them; aliases resolved)
        fun flatten(node: Map<String, Any?>, path: List<String>, type: String?, ext: Map<String, Any?>,
                    into: MutableMap<String, Token>) {
            val t = node["\$type"] as String? ?: type
            val e = ext + (node["\$extensions"] as Map<String, Any?>? ?: emptyMap())
            if ("\$value" in node) into[path.joinToString(".")] = Token(t ?: "", node["\$value"], e)
            else for ((k, v) in node) if (!k.startsWith("$")) flatten(v as Map<String, Any?>, path + k, t, e, into)
        }
        fun resolve(all: Map<String, Token>, path: String, seen: List<String> = emptyList()): Token {
            if (path in seen) throw GradleException("tokens: an alias's loop, ${(seen + path).joinToString(" -> ")}")
            val t = all[path] ?: throw GradleException("tokens: no $path")
            val to = (t.value as? String)?.takeIf { it.startsWith("{") && it.endsWith("}") }?.drop(1)?.dropLast(1)
                ?: return t
            val target = resolve(all, to, seen + path)
            return Token(t.type.ifEmpty { target.type }, target.value, t.ext)
        }
        val resolved = contexts.keys.associateWith { context ->
            val flat = LinkedHashMap<String, Token>()
            flatten(tree(context), emptyList(), null, emptyMap(), flat)
            flat.keys.associateWith { resolve(flat, it) }
        }

        fun camel(name: String) = name.split('-', '_').filter { it.isNotEmpty() }.mapIndexed { i, w ->
            if (i == 0) w.replaceFirstChar { it.lowercase() } else w.replaceFirstChar { it.uppercase() }
        }.joinToString("")
        fun pascal(name: String) = camel(name).replaceFirstChar { it.uppercase() }
        fun double(n: Any?) = (n as? Number)?.toDouble()?.toString() ?: throw GradleException("tokens: $n isn't a number")
        fun whole(n: Any?) = (n as? Number)?.takeIf { it.toDouble() % 1.0 == 0.0 }?.toLong()?.toString()
            ?: throw GradleException("tokens: $n isn't a whole number")
        val weights = mapOf("thin" to 100, "hairline" to 100, "extra-light" to 200, "ultra-light" to 200,
            "light" to 300, "normal" to 400, "regular" to 400, "book" to 400, "medium" to 500, "semi-bold" to 600,
            "demi-bold" to 600, "bold" to 700, "extra-bold" to 800, "ultra-bold" to 800, "black" to 900,
            "heavy" to 900, "extra-black" to 950, "ultra-black" to 950)

        // (a token's Kotlin: its value, its type, and the suffix its name takes)
        fun kotlin(path: String, t: Token): Triple<String, String, String> {
            val v = t.value as? Map<String, Any?>
            return when (t.type) {
                "color" -> {
                    val parts = (v!!["components"] as List<Any?>) + (v["alpha"] ?: 1)
                    if (v["colorSpace"] != "srgb") throw GradleException("tokens: $path isn't sRGB")
                    val (r, g, b, a) = parts.map { ((it as Number).toDouble() * 255 + 0.5).toInt().coerceIn(0, 255) }
                    val hex = "%02X%02X%02X".format(r, g, b)
                    if (v["hex"] != null && (v["hex"] as String).uppercase() != "#$hex")
                        throw GradleException("tokens: $path's hex ${v["hex"]} isn't its components' #$hex")
                    Triple("Color(0x%02X$hex)".format(a), "Color", "")
                }
                "dimension" -> {
                    if (v!!["unit"] != "px") throw GradleException("tokens: $path isn't in px")
                    val n = v["value"]
                    when (t.ext["dev.baude.android"]) {
                        "sp" -> Triple("${(n as Number).toString().removeSuffix(".0")}.sp", "TextUnit", "")
                        "double" -> Triple(double(n), "Double", "")
                        else -> Triple("${(n as Number).toString().removeSuffix(".0")}.dp", "Dp", "")
                    }
                }
                "duration" -> when (v!!["unit"]) {
                    "ms" -> Triple(whole(v["value"]), "Int", "Ms")
                    "s" -> Triple(double(v["value"]), "Double", "S")
                    else -> throw GradleException("tokens: $path's unit ${v["unit"]}")
                }
                "number" -> if (t.value is Int) Triple(t.value.toString(), "Int", "") else Triple(double(t.value), "Double", "")
                "fontWeight" -> Triple("FontWeight(${(t.value as? Int) ?: weights[t.value]
                    ?: throw GradleException("tokens: $path's weight ${t.value}")})", "FontWeight", "")
                else -> throw GradleException("tokens: $path is a ${t.type}, which isn't made Kotlin")
            }
        }

        val base = resolved.getValue(theme["default"] as String? ?: contexts.keys.first())
        fun children(prefix: String, tokens: Map<String, Token>) = tokens.keys
            .filter { prefix.isEmpty() || it.startsWith("$prefix.") }
            .map { (if (prefix.isEmpty()) it else it.removePrefix("$prefix.")).substringBefore('.') }.distinct()
        fun join(prefix: String, name: String) = if (prefix.isEmpty()) name else "$prefix.$name"
        fun numbered(names: List<String>) = names.isNotEmpty() && names.all { n -> n.all(Char::isDigit) }
        // (a list's item: a token's value, a pair of x and y, or a list of its tokens')
        fun item(path: String, tokens: Map<String, Token>): String {
            tokens[path]?.let { return kotlin(path, it).first }
            val names = children(path, tokens)
            return when {
                names.toSet() == setOf("x", "y") -> "${item("$path.x", tokens)} to ${item("$path.y", tokens)}"
                numbered(names) -> "listOf(${names.sortedBy { it.toInt() }.joinToString(", ") { item("$path.$it", tokens) }})"
                else -> "listOf(${names.joinToString(", ") { item("$path.$it", tokens) }})"
            }
        }
        // (a group's: its tokens as constants, its groups as objects, lists or pairs)
        fun members(prefix: String, tokens: Map<String, Token>, out: StringBuilder, indent: String) {
            for (name in children(prefix, tokens)) {
                val path = join(prefix, name)
                val t = tokens[path]
                val names = if (t == null) children(path, tokens) else emptyList()
                if (t != null) {
                    val (value, type, suffix) = kotlin(path, t)
                    val const = if (type == "Int" || type == "Double") "const val" else "val"
                    out.append("$indent$const ${camel(name)}$suffix = $value\n")
                } else if (numbered(names) || names.toSet() == setOf("x", "y")) {
                    val line = "${indent}val ${camel(name)} = ${item(path, tokens)}"
                    out.append(if (line.length <= 120) "$line\n" else "${indent}val ${camel(name)} = listOf(\n" +
                        names.sortedBy { it.toInt() }.joinToString("") { "$indent    ${item("$path.$it", tokens)},\n" } +
                        "$indent)\n")
                } else {
                    out.append("${indent}object ${pascal(name)} {\n")
                    members(path, tokens, out, "$indent    ")
                    out.append("$indent}\n")
                }
            }
        }
        fun themedNames(prefix: String, tokens: Map<String, Token>) =
            children(prefix, tokens).filter { prefix.isNotEmpty() || it in themed }
        // (the theme's groups: a class of them, made once for each of the theme's contexts)
        fun classes(prefix: String, out: StringBuilder, indent: String) {
            val names = themedNames(prefix, base)
            out.append("${indent}class ${if (prefix.isEmpty()) "ThemeTokens" else pascal(prefix.substringAfterLast('.'))}(\n")
            for (name in names) {
                val t = base[join(prefix, name)]
                if (t == null) out.append("$indent    val ${camel(name)}: ${pascal(name)},\n")
                else kotlin(join(prefix, name), t).let { (_, type, suffix) ->
                    out.append("$indent    val ${camel(name)}$suffix: $type,\n")
                }
            }
            val groups = names.filter { join(prefix, it) !in base }
            if (groups.isEmpty()) {
                out.append("$indent)\n")
                return
            }
            out.append("$indent) {\n")
            for (name in groups) classes(join(prefix, name), out, "$indent    ")
            out.append("$indent}\n")
        }
        fun instance(prefix: String, tokens: Map<String, Token>, owner: String, indent: String): String {
            val names = themedNames(prefix, tokens)
            if (names != themedNames(prefix, base))
                throw GradleException("tokens: the theme's contexts differ in what ${prefix.ifEmpty { "they" }} have")
            val type = if (prefix.isEmpty()) "ThemeTokens" else "$owner.${pascal(prefix.substringAfterLast('.'))}"
            return "$type(\n" + names.joinToString("") { name ->
                val t = tokens[join(prefix, name)]
                if (t == null) "$indent    ${camel(name)} = ${instance(join(prefix, name), tokens, type, "$indent    ")},\n"
                else kotlin(join(prefix, name), t).let { (value, _, suffix) -> "$indent    ${camel(name)}$suffix = $value,\n" }
            } + "$indent)"
        }

        for ((path, t) in base) if (path.substringBefore('.') !in themed)
            for ((context, tokens) in resolved) if (tokens.getValue(path).value != t.value)
                throw GradleException("tokens: $path is $context's own, but its group isn't in the theme's contexts")
        val out = StringBuilder()
        out.append("// Made from schema/tokens as the app's built (build.gradle.kts's baudeTokens): never edited, never kept.\n")
        out.append("package dev.baude.android\n\n")
        for (i in listOf("androidx.compose.ui.graphics.Color", "androidx.compose.ui.text.font.FontWeight",
                "androidx.compose.ui.unit.Dp", "androidx.compose.ui.unit.TextUnit", "androidx.compose.ui.unit.dp",
                "androidx.compose.ui.unit.sp")) out.append("import $i\n")
        out.append("\n")
        classes("", out, "")
        out.append("\nobject Tokens {\n")
        for ((context, tokens) in resolved)
            out.append("    val ${camel(context)} = ${instance("", tokens, "ThemeTokens", "    ")}\n")
        members("", base.filterKeys { it.substringBefore('.') !in themed }, out, "    ")
        out.append("}\n")
        val dir = outputDir.get().asFile.resolve("dev/baude/android")
        dir.mkdirs()
        dir.resolve("Tokens.kt").writeText(out.toString())
    }
}

val baudeTokens = tasks.register<BaudeTokens>("baudeTokens") {
    tokens.set(rootProject.layout.projectDirectory.dir("../schema/tokens"))
    outputDir.set(layout.buildDirectory.dir("generated/baudeTokens"))
}

androidComponents {
    onVariants { variant -> variant.sources.java?.addGeneratedSourceDirectory(baudeTokens, BaudeTokens::outputDir) }
}

/** schema/wire.schema.json, the wire Baude's heads and hosts speak, as Kotlin (WireSchema.kt): Op's names, Since's
 * versions (each op's and field's, as Feature), WireConstants, and Msg's classes, one for each message and entity,
 * built with their fields and made JSON (json) or read from it (from). The build fails if the schema's versions don't
 * add up (a version claimed twice, one whose history isn't what's marked with it), as wire.py's import does. */
abstract class WireSchema : DefaultTask() {
    @get:InputFile abstract val schema: RegularFileProperty
    @get:OutputDirectory abstract val outputDir: DirectoryProperty

    private fun plain(v: Any?): Any? = when (v) {
        is Map<*, *> -> v.entries.associateTo(LinkedHashMap<String, Any?>()) { it.key as String to plain(it.value) }
        is List<*> -> v.map { plain(it) }
        else -> v
    }

    @Suppress("UNCHECKED_CAST")
    private fun map(v: Any?) = (v ?: emptyMap<String, Any?>()) as Map<String, Any?>

    private val keywords = setOf("as", "break", "class", "continue", "do", "else", "false", "for", "fun", "if", "in",
        "interface", "is", "null", "object", "package", "return", "super", "this", "throw", "true", "try", "typealias",
        "typeof", "val", "var", "when", "while")

    private fun camel(name: String) = name.split("_").mapIndexed { i, w ->
        if (i == 0) w else w.replaceFirstChar { it.uppercase() } }.joinToString("").let { if (it in keywords) "`$it`" else it }

    private fun upper(key: String) = key.replace(Regex("([a-z0-9])([A-Z])"), "$1_$2").replace(".", "_").uppercase()

    private fun ref(p: Map<String, Any?>): String? = (p["\$ref"] as String?)?.substringAfterLast("/")
        ?: (p["anyOf"] as List<*>?)?.firstNotNullOfOrNull { (map(it)["\$ref"] as String?)?.substringAfterLast("/") }

    private fun types(p: Map<String, Any?>): List<String> = when (val t = p["type"]) {
        is String -> listOf(t)
        is List<*> -> t.map { it as String }.filter { it != "null" }
        else -> emptyList()
    }

    /** (Kotlin's type for a field, how it's made JSON, how it's read) */
    private fun kind(p: Map<String, Any?>, key: String): Triple<String, (String) -> String, String> {
        val r = ref(p)
        val t = types(p)
        if (r != null && "array" !in t) return Triple(r, { "$it.json()" }, "o.optJSONObject(\"$key\")?.let { $r.from(it) }")
        if ("array" in t) {
            val items = map(p["items"])
            val ir = ref(items)
            if (ir != null) return Triple("List<$ir>", { "JSONArray($it.map { x -> x.json() })" },
                "o.optJSONArray(\"$key\")?.let { a -> (0 until a.length()).mapNotNull { i -> a.optJSONObject(i)?.let { $ir.from(it) } } }")
            if (types(items) == listOf("string")) return Triple("List<String>", { "JSONArray($it)" },
                "o.optJSONArray(\"$key\")?.let { a -> (0 until a.length()).map { i -> a.optString(i) } }")
            return Triple("JSONArray", { it }, "o.optJSONArray(\"$key\")")
        }
        return when {
            "const" in p || "enum" in p || t == listOf("string") -> Triple("String", { it }, "o.wireStr(\"$key\")")
            t == listOf("integer") || t == listOf("number") -> Triple("Number", { it }, "o.opt(\"$key\") as? Number")
            t == listOf("boolean") -> Triple("Boolean", { it }, "o.opt(\"$key\") as? Boolean")
            t == listOf("object") -> Triple("JSONObject", { it }, "o.optJSONObject(\"$key\")")
            else -> Triple("Any", { it }, "o.opt(\"$key\")?.takeIf { it != JSONObject.NULL }")
        }
    }

    private fun doc(text: Any?, indent: String) = if (text == null) "" else
        "$indent/** ${(text as String).replace("*/", "*&#47;")} */\n"

    @Suppress("UNCHECKED_CAST")
    @TaskAction
    fun write() {
        val s = map(plain(groovy.json.JsonSlurper().parse(schema.get().asFile)))
        val defs = map(s["\$defs"]).mapValues { map(it.value) }
        val messages = (s["oneOf"] as List<*>).map { (map(it)["\$ref"] as String).substringAfterLast("/") }
        fun props(d: Map<String, Any?>) = map(d["properties"]).mapValues { map(it.value) }
        fun op(d: Map<String, Any?>) = props(d)["op"]?.get("const") as String

        // (every op, entity and field with the version it came with, checked against the history, as wire.py does)
        val since = LinkedHashMap<String, Int>()
        for ((name, d) in defs) {
            val key = if (name in messages) op(d) else name
            val v = (d["x-since"] as Number?)?.toInt() ?: 1
            since[key]?.let { if (it != v) throw GradleException("wire schema: $key's lines say versions $it and $v") }
            since[key] = v
            for ((f, p) in props(d)) if (f != "op") since["$key.$f"] = maxOf((p["x-since"] as Number?)?.toInt() ?: 1, v)
        }
        val history = map(s["x-versions"]).mapValues { map(it.value) }
        val numbers = history.keys.map { it.toInt() }.sorted()
        if (numbers != (1..numbers.size).toList()) throw GradleException("wire schema: x-versions isn't 1 to ${numbers.size}")
        for (v in numbers.drop(1)) {
            val said = (history["$v"]!!["adds"] as List<String>?).orEmpty().toSet()
            val have = since.filter { (k, n) -> n == v && !('.' in k && since[k.substringBefore('.')] == v) }.keys
            if (said != have) throw GradleException("wire schema: version $v's history says it added ${said - have} that " +
                "don't say it, and not ${have - said} that do: is it claimed twice?")
        }
        val version = numbers.last()
        val features = map(s["x-features"]).mapValues { map(it.value) }
        val listedBy = features.filterValues { "field" in it }.entries.associate { (n, f) -> f["field"] as String to n }
        for ((n, f) in features) if (n in since || ("field" in f) == ("since" in f) || "field" in f && f["field"] !in since)
            throw GradleException("wire schema: x-features' $n is an op, or isn't a field or a version of its own")
        val hosts = map(s["x-hosts"]).mapValues { (map(it.value)["ops"] as List<*>).map { o -> o as String } }
        for ((n, ops) in hosts) ops.filter { it !in since && it !in features }.takeIf { it.isNotEmpty() }?.let {
            throw GradleException("wire schema: x-hosts' $n takes $it, which are neither ops nor features") }
        since.filterValues { it > version }.keys.takeIf { it.isNotEmpty() }?.let {
            throw GradleException("wire schema: these came with a version past $version: $it") }

        val out = StringBuilder()
        out.append("package dev.baude.android\n\nimport org.json.JSONArray\nimport org.json.JSONObject\n\n")
        out.append("// Made from schema/wire.schema.json as the app's built (android/app/build.gradle.kts's WireSchema).\n\n")
        out.append("/** The wire's ops. */\nobject Op {\n")
        for (o in messages.map { op(defs[it]!!) }.distinct()) out.append("    const val ${upper(o)} = \"$o\"\n")
        out.append("}\n\n")
        out.append("/** Something a host takes: from a version on, or, where its hello lists its ops, if it lists it (by listed, " +
            "the op's name or a feature's). */\n")
        out.append("class Feature(val key: String, val since: Int, val listed: String)\n\n")
        fun listed(k: String) = listedBy[k] ?: k.substringBefore('.')
        out.append("/** The version each op (START) and entity (SESSION) came with, and their fields (Start.EPHEMERAL). */\n")
        out.append("object Since {\n    const val VERSION = $version\n")
        val names = HashSet<String>()
        for ((k, v) in since) if ('.' !in k) {
            if (!names.add(upper(k))) throw GradleException("wire schema: two of its names are ${upper(k)} in Kotlin")
            out.append("    val ${upper(k)} = Feature(\"$k\", $v, \"${listed(k)}\")\n")
        }
        for ((n, f) in features) {
            if (!names.add(upper(n))) throw GradleException("wire schema: two of its names are ${upper(n)} in Kotlin")
            val v = (f["since"] as Number?)?.toInt() ?: since[f["field"] as String]!!
            out.append(doc(f["description"], "    "))
            out.append("    val ${upper(n)} = Feature(\"${f["field"] ?: n}\", $v, \"$n\")\n")
        }
        for (owner in since.keys.filter { '.' !in it }) {
            val obj = owner.split("_").joinToString("") { w -> w.replaceFirstChar { it.uppercase() } }
            if (!names.add(obj)) throw GradleException("wire schema: two of its names are $obj in Kotlin")
            out.append("\n    object $obj {\n")
            for ((k, v) in since) if (k.substringBefore('.') == owner && '.' in k)
                out.append("        val ${upper(k.substringAfter('.'))} = Feature(\"$k\", $v, \"${listed(k)}\")\n")
            out.append("    }\n")
        }
        out.append("}\n\n")
        out.append("/** Hosts that speak only part of the wire: what each takes, as its hello lists its ops. */\nobject WireHosts {\n")
        for ((n, ops) in hosts) out.append("    val ${upper(n)} = listOf(${ops.joinToString { "\"$it\"" }})\n")
        out.append("    /** What a host may take that isn't an op (x-features), by the name its ops list it by. */\n")
        out.append("    val FEATURES = setOf(${features.keys.joinToString { "\"$it\"" }})\n")
        out.append("}\n\n")
        out.append("/** The protocol's constants. */\nobject WireConstants {\n")
        for ((k, c) in map(s["x-constants"])) {
            val v = map(c)["value"]
            out.append(doc("${map(c)["description"]} (${map(c)["unit"]})", "    "))
            out.append("    const val $k = ${if (v is Number && v.toDouble() % 1.0 == 0.0) v.toLong().toString() else "${v}"}\n")
        }
        out.append("}\n\n")
        // (WireCheck: what wire.check does, a field or op the schema hasn't, in what's sent; entities looked into)
        fun closed(n: String) = defs[n]?.get("additionalProperties") == false
        fun nested(p: Map<String, Any?>): String? {
            if (p["x-open"] == true) return null
            ref(p)?.let { r -> return if ("array" in types(p) || !closed(r)) null else r }
            if ("array" in types(p)) return ref(map(p["items"]))?.takeIf { closed(it) }
            val values = p["additionalProperties"]
            if (values is Map<*, *>) return ref(map(values))?.takeIf { closed(it) }?.let { "{$it}" }
            return null
        }
        fun shape(fields: Set<String>, inner: Map<String, String>) =
            "Shape(setOf(${fields.joinToString { "\"$it\"" }}), mapOf(${inner.entries.joinToString { "\"${it.key}\" to \"${it.value}\"" }}))"
        out.append("/** What of a message isn't the schema's (an op, a field: wire.check's): what's sent is checked by it. */\n")
        out.append("object WireCheck {\n    private class Shape(val fields: Set<String>, val inner: Map<String, String>)\n\n")
        out.append("    private val ENTITIES = mapOf(\n")
        for ((n, d) in defs) if (n !in messages && closed(n)) {
            val inner = props(d).mapNotNull { (f, p) -> nested(p)?.let { f to it } }.toMap()
            out.append("        \"$n\" to ${shape(props(d).keys, inner)},\n")
        }
        out.append("    )\n\n    private val OPS = mapOf(\n")
        for (o in messages.map { op(defs[it]!!) }.distinct()) {
            val lines = messages.filter { op(defs[it]!!) == o }.map { props(defs[it]!!) }
            val fields = lines.flatMap { it.keys }.toSet()
            val inner = fields.mapNotNull { f ->
                val said = lines.mapNotNull { it[f] }.map { nested(it) }.distinct()
                said.singleOrNull()?.let { f to it } }.toMap()
            out.append("        \"$o\" to ${shape(fields, inner)},\n")
        }
        out.append("    )\n\n")
        out.append("""    fun problem(msg: JSONObject): String? {
        val op = msg.optString("op")
        val shape = OPS[op] ?: return "no op '${'$'}op' in the schema"
        return within(op, shape, msg)
    }

    private fun within(at: String, shape: Shape, o: JSONObject): String? {
        for (k in o.keys()) {
            if (k !in shape.fields) return "${'$'}at.${'$'}k: no such field"
            val e = shape.inner[k] ?: continue
            val v = o.opt(k)
            val bad = if (e.startsWith("{")) (v as? JSONObject)?.let { m ->
                m.keys().asSequence().firstNotNullOfOrNull { key -> entity("${'$'}at.${'$'}k[${'$'}key]", e.trim('{', '}'), m.opt(key)) } }
                else entity("${'$'}at.${'$'}k", e, v)
            if (bad != null) return bad
        }
        return null
    }

    private fun entity(at: String, name: String, v: Any?): String? = when (v) {
        is JSONObject -> within(at, ENTITIES.getValue(name), v)
        is JSONArray -> (0 until v.length()).firstNotNullOfOrNull { entity("${'$'}at[${'$'}it]", name, v.opt(it)) }
        else -> null
    }
}

""")
        out.append("private fun JSONObject.wireStr(key: String): String? = if (isNull(key)) null else optString(key)\n\n")
        out.append("/** The wire's messages and the entities in them: each built with the fields it's given (those left null aren't " +
            "sent), json() as it goes, from() as it came (a field it doesn't have left out). */\nobject Msg {\n")
        for ((name, d) in defs) {
            val fields = props(d).filterKeys { it != "op" }
            out.append(doc(d["description"], "    "))
            out.append("    class $name(\n")
            for ((f, p) in fields) {
                out.append(doc(p["description"], "        "))
                out.append("        val ${camel(f)}: ${kind(p, f).first}? = null,\n")
            }
            out.append("    ) {\n")
            out.append("        fun json(): JSONObject {\n")
            out.append("            val o = JSONObject()\n")
            if (name in messages) out.append("            o.put(\"op\", Op.${upper(op(d))})\n")
            for ((f, p) in fields) out.append("            ${camel(f)}?.let { o.put(\"$f\", ${kind(p, f).second("it")}) }\n")
            out.append("            return o\n        }\n\n")
            out.append("        companion object {\n")
            out.append("            fun from(o: JSONObject) = $name(\n")
            for ((f, p) in fields) out.append("                ${camel(f)} = ${kind(p, f).third},\n")
            out.append("            )\n        }\n    }\n\n")
        }
        out.append("}\n")
        val dir = outputDir.get().asFile.resolve("dev/baude/android")
        dir.mkdirs()
        dir.resolve("WireSchema.kt").writeText(out.toString())
    }
}

val wireSchema = tasks.register<WireSchema>("wireSchema") {
    schema.set(rootProject.layout.projectDirectory.file("../schema/wire.schema.json"))
    outputDir.set(layout.buildDirectory.dir("generated/wireSchema"))
}

androidComponents {
    onVariants { variant -> variant.sources.java?.addGeneratedSourceDirectory(wireSchema, WireSchema::outputDir) }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)
    debugImplementation(libs.androidx.compose.ui.tooling)
    implementation(libs.kotlinx.serialization.core)
    // ssh: JSch (mwiede's, BSD-style licence: libs/JSCH-LICENSE.txt), the jar as Gradle 8.9 ships it
    implementation(files("libs/jsch-0.2.16.jar"))
}
