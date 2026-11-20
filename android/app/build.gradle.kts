import java.time.Duration

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

/** git in the checkout the app's built from (the folder above android/): what it printed, or null (it failed). */
fun git(vararg args: String): String? = try {
    val run = providers.exec {
        commandLine("git", "-C", rootDir.parentFile.path, *args)
        isIgnoreExitValue = true
    }
    if (run.result.get().exitValue == 0) run.standardOutput.asText.get().trim() else null
} catch (e: Exception) {
    null
}

val FROM = 1000

/** What goes into the APK (android/, emission.py's scripts, schema/'s tokens and types): its commits count. */
val APP_PATHS = arrayOf("android", "emission.py", "schema")

/** The build's version, as Baude's Version is the commit it runs: (versionCode, versionName). versionCode is how many
 * commits (not merges) have changed what goes into the APK, so each build of main that changes it installs over the
 * one before (Update.kt; build_shots.py publishes it only if it's above the live one's), and one that doesn't keeps
 * its number; versionName is the short hash, "-dirty" if those files have changes not committed (never published).
 * Outside a full git checkout the build fails, unless it's given one: -PbaudeVersionCode=N. FROM is added to the
 * count: the builds published first counted every commit (889 by then), and a phone only takes a higher one. */
fun version(): Pair<Int, String> {
    (findProperty("baudeVersionCode") as String?)?.let { return it.toInt() to "$it-local" }
    fun fail(why: String): Nothing = throw GradleException(
        "versionCode comes from git, and $why: build in a full checkout of Baude, or pass -PbaudeVersionCode=N")
    val top = git("rev-parse", "--show-toplevel") ?: fail("there's no git checkout at ${rootDir.parentFile}")
    if (File(top).canonicalFile != rootDir.parentFile.canonicalFile) fail("${rootDir.parentFile} isn't a checkout's top")
    if (git("rev-parse", "--is-shallow-repository") != "false") fail("this checkout is shallow")
    val count = git("rev-list", "--count", "--no-merges", "--full-history", "HEAD", "--", *APP_PATHS)?.toIntOrNull()
        ?: fail("git rev-list failed")
    val hash = git("rev-parse", "--short", "HEAD") ?: fail("git rev-parse failed")
    val dirty = git("status", "--porcelain", "--", *APP_PATHS) ?: fail("git status failed")
    return FROM + count to (if (dirty.isEmpty()) hash else "$hash-dirty")
}
val (appVersionCode, appVersionName) = version()
val appCommitTime = if (findProperty("baudeVersionCode") != null) "0" else git("log", "-1", "--format=%ct") ?: "0"

android {
    namespace = "dev.baude.android"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.baude.android"
        minSdk = 26
        targetSdk = 35
        versionCode = appVersionCode // (version(): from git)
        versionName = appVersionName
        resValue("string", "commit_time", appCommitTime) // (Settings → Version's when: the commit's, in seconds)
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
    testOptions {
        unitTests {
            isIncludeAndroidResources = true // (the screenshot rig: src/test/.../screenshots)
            all {
                it.maxHeapSize = "3g"
                it.systemProperty("roborazzi.test.record", "true") // (Roborazzi saves what it's given)
            }
        }
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

/** schema/types and schema/variables, the wire Baude's heads and hosts speak, as Kotlin (WireSchema.kt): Op's names,
 * WireConstants (the variables, the wire's version among them), Msg's classes, one for each message and entity (each
 * a file of schema/types, named for it: Name.json, or Name.kind.json for a message), built with their fields and made
 * JSON (json) or read from it (from), and WireCheck, what's sent and what a host's asked looked at as wire.py's check
 * and refusal do. The types themselves go in the app as a resource (wire-types.json, by name), for WireCheck's types. */
abstract class WireSchema : DefaultTask() {
    @get:InputDirectory abstract val typesDir: DirectoryProperty
    @get:InputDirectory abstract val variablesDir: DirectoryProperty
    @get:InputDirectory abstract val usesDir: DirectoryProperty // (the app's code: each constant it uses is there)
    @get:OutputDirectory abstract val outputDir: DirectoryProperty
    @get:OutputDirectory abstract val resourcesDir: DirectoryProperty

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

    private fun ref(p: Map<String, Any?>): String? = (p["\$ref"] as String?)?.removeSuffix(".json")
        ?: (p["anyOf"] as List<*>?)?.firstNotNullOfOrNull { (map(it)["\$ref"] as String?)?.removeSuffix(".json") }

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
            val each = "o.optJSONArray(\"$key\")?.let { a -> (0 until a.length()).mapNotNull { i -> a.opt"
            if (types(items) == listOf("integer") || types(items) == listOf("number"))
                return Triple("List<Number>", { "JSONArray($it)" }, "$each(i) as? Number } }")
            if (types(items) == listOf("object")) // (objects not the wire's own)
                return Triple("List<JSONObject>", { "JSONArray($it)" }, "${each}JSONObject(i) } }")
            return Triple("JSONArray", { it }, "o.optJSONArray(\"$key\")")
        }
        val values = p["additionalProperties"]
        if (t == listOf("object") && values is Map<*, *>) { // (a map of entities, or of strings)
            val vr = ref(map(values))
            if (vr != null) return Triple("Map<String, $vr>", { "JSONObject($it.mapValues { e -> e.value.json() })" },
                "o.optJSONObject(\"$key\")?.let { m -> m.keys().asSequence()" +
                    ".mapNotNull { k -> m.optJSONObject(k)?.let { k to $vr.from(it) } }.toMap() }")
            if (types(map(values)) == listOf("string")) return Triple("Map<String, String>", { "JSONObject($it)" },
                "o.optJSONObject(\"$key\")?.let { m -> m.keys().asSequence().associateWith { k -> m.optString(k) } }")
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

    private fun fail(what: String): Nothing = throw GradleException(what)

    /** f, made of the file n (a type's or a variable's): what goes wrong in it said with n's name. */
    private fun <T> about(n: String, f: () -> T): T = try {
        f()
    } catch (e: GradleException) {
        throw e
    } catch (e: Exception) {
        fail("$n: $e")
    }

    /** What's wrong with a JSON text that JsonSlurper lets by, where wire.py's loader (Python's json) refuses it, if
     * anything: a key twice in one object, and where (its path; JsonSlurper keeps the last one), a text cut short or
     * with more after its end, a } or ] that closes nothing or the other, a comma before one. */
    private fun lax(text: String): String? {
        class At(val keys: HashSet<String>?, var key: String? = null) // (an object's keys so far, and its last; null: an array)
        val at = ArrayList<At>()
        var i = 0
        var last = ' ' // (the last character that isn't white space)
        var done = false // (the top's closed)
        while (i < text.length) {
            val ch = text[i]
            if (done && !ch.isWhitespace()) return "more after its end"
            when (ch) {
                '{' -> at.add(At(HashSet()))
                '[' -> at.add(At(null))
                '}', ']' -> {
                    val open = at.removeLastOrNull() ?: return "a $ch that closes nothing"
                    if ((open.keys != null) != (ch == '}')) return "a $ch that closes a ${if (ch == '}') '[' else '{'}"
                    if (last == ',') return "a comma before a $ch"
                    done = at.isEmpty()
                }
                '"' -> {
                    val start = i
                    i++
                    while (i < text.length && text[i] != '"') i += if (text[i] == '\\') 2 else 1
                    if (i >= text.length) return "a string that isn't closed"
                    var j = i + 1
                    while (j < text.length && text[j].isWhitespace()) j++
                    val here = at.lastOrNull()
                    if (j < text.length && text[j] == ':' && here?.keys != null) {
                        val k = (groovy.json.JsonSlurper().parseText("[" + text.substring(start, i + 1) + "]") as List<*>)[0] as String
                        if (!here.keys.add(k))
                            return "a key twice in one object: '$k' at " +
                                at.dropLast(1).mapNotNull { it.key }.joinToString("/").ifEmpty { "the top" }
                        here.key = k
                    }
                }
            }
            if (!ch.isWhitespace()) last = ch
            i++
        }
        return if (at.isNotEmpty()) "cut short: a ${if (at.last().keys != null) '{' else '['} that isn't closed" else null
    }

    /** Each file of dir (schema/<folder>), by its name: (its kind, as its file's name says it, or null; its JSON; its
     * path). Others (a README, an editor's backup) are passed over. Fails, naming the file, for a folder in it, one not
     * named Name.json or Name.kind.json, or JSON that can't be read (lax's too). */
    private fun load(dir: File, folder: String): Map<String, Triple<String?, Any?, String>> {
        val out = LinkedHashMap<String, Triple<String?, Any?, String>>()
        for (f in dir.listFiles()!!.filter { !it.name.startsWith(".") }.sortedBy { it.name }) {
            val path = "schema/$folder/${f.name}"
            if (f.isDirectory) fail("$path: a folder, not a JSON file")
            if (!f.name.endsWith(".json")) continue
            val m = Regex("([A-Za-z_][A-Za-z0-9_]*)(?:\\.([a-z]+))?\\.json").matchEntire(f.name)
                ?: fail("$path: not named Name.json or Name.kind.json")
            val name = m.groupValues[1]
            out[name]?.let { fail("$path: ${it.third} is $name too") }
            val text = f.readText()
            about(path) { lax(text) }?.let { fail("$path: $it") }
            val json = about(path) { plain(groovy.json.JsonSlurper().parseText(text)) }
            out[name] = Triple(m.groupValues[2].ifEmpty { null }, json, path)
        }
        return out
    }

    @Suppress("UNCHECKED_CAST")
    @TaskAction
    fun write() {
        val loaded = load(typesDir.get().asFile, "types")
        for ((_, d, path) in loaded.values) { // (an object, each of its properties an object)
            val p = if (d is Map<*, *>) d["properties"] ?: emptyMap<String, Any?>() else null
            if (p !is Map<*, *> || p.values.any { it !is Map<*, *> })
                fail("$path: not a type (an object, each of its properties an object)")
        }
        val files = loaded.mapValues { (_, f) -> Triple(f.first, map(f.second), f.third) }
        val vars = load(variablesDir.get().asFile, "variables").mapValues { (_, f) ->
            val c = (f.second as? Map<*, *>)?.get("const")
            if (f.first != null || c !is Number) fail("${f.third}: not a variable (Name.json, an object whose const is a number)")
            Triple(f.first, map(f.second), f.third)
        }
        if ("VERSION" !in vars) fail("schema/variables/VERSION.json: not there") // (the wire's, as a host's hello says it)
        for (src in usesDir.get().asFile.walk().filter { it.extension == "kt" }) // (each constant the app uses there)
            for (u in Regex("WireConstants\\.([A-Za-z_][A-Za-z0-9_]*)").findAll(src.readText()).map { it.groupValues[1] })
                if (u !in vars) fail("schema/variables/$u.json: not there (${src.name} uses WireConstants.$u)")
        val kinds = listOf("request", "reply", "event", "frame") // (what a message is, as its file's name says)
        val defs = files.mapValues { it.value.second }
        val kind = files.filterValues { it.first != null }.mapValues { it.value.first!! }
        val messages = kind.keys.toList()
        fun props(d: Map<String, Any?>) = map(d["properties"]).mapValues { map(it.value) }
        fun op(d: Map<String, Any?>) = props(d)["op"]?.get("const") as String
        for ((k, d, path) in files.values) { // (a message with a kind and an op, an entity without, every $ref a type)
            val o = props(d)["op"]?.get("const")
            if (k != null && k !in kinds) fail("$path: $k isn't one of ${kinds.joinToString()}")
            if (k != null && o !is String) fail("$path: a message with no op const")
            if (k == null && "op" in props(d)) fail("$path: an op, but no kind in its name (Name.request.json, ...)")
            fun refs(v: Any?): Unit = when (v) {
                is Map<*, *> -> {
                    if ("\$ref" in v) (v["\$ref"] as? String).let { r ->
                        if (r == null || !r.endsWith(".json") || r.removeSuffix(".json") !in defs) fail("$path: no type ${v["\$ref"]}") }
                    v.values.forEach { refs(it) }
                }
                is List<*> -> v.forEach { refs(it) }
                else -> {}
            }
            refs(d)
        }

        val out = StringBuilder()
        out.append("package dev.baude.android\n\nimport org.json.JSONArray\nimport org.json.JSONObject\n\n")
        out.append("// Made from schema/types and schema/variables as the app's built (build.gradle.kts's WireSchema).\n\n")
        out.append("/** The wire's ops. */\nobject Op {\n")
        for (o in messages.map { op(defs[it]!!) }.distinct()) out.append("    const val ${upper(o)} = \"$o\"\n")
        out.append("}\n\n")
        out.append("/** The protocol's constants (schema/variables), the wire's version among them. */\nobject WireConstants {\n")
        for ((k, f) in vars) about(f.third) {
            val v = f.second["const"] as Number
            out.append(doc(f.second["description"], "    "))
            out.append("    const val $k = ${if (v is Number && v.toDouble() % 1.0 == 0.0) v.toLong().toString() else "${v}"}\n")
        }
        out.append("}\n\n")
        // (WireCheck: what wire.check does, a field or op the schema hasn't, in what's sent; entities looked into)
        fun closed(n: String) = defs[n]?.get("additionalProperties") == false
        fun nested(p: Map<String, Any?>): String? {
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
        for ((n, d) in defs) if (n !in messages && closed(n)) about(files.getValue(n).third) {
            val inner = props(d).mapNotNull { (f, p) -> nested(p)?.let { f to it } }.toMap()
            out.append("        \"$n\" to ${shape(props(d).keys, inner)},\n")
        }
        out.append("    )\n\n    private val OPS = mapOf(\n")
        for (o in messages.map { op(defs[it]!!) }.distinct()) about(messages.filter { op(defs[it]!!) == o }.joinToString()) {
            val lines = messages.filter { op(defs[it]!!) == o }.map { props(defs[it]!!) }
            val fields = lines.flatMap { it.keys }.toSet()
            val inner = fields.mapNotNull { f ->
                val said = lines.mapNotNull { it[f] }.map { nested(it) }.distinct()
                said.singleOrNull()?.let { f to it } }.toMap()
            out.append("        \"$o\" to ${shape(fields, inner)},\n")
        }
        out.append("    )\n\n")
        val requests = messages.filter { kind[it] == "request" }
        fun nullable(p: Map<String, Any?>) = p["type"] == "null" || (p["type"] as? List<*>)?.contains("null") == true ||
            (p["anyOf"] as? List<*>)?.any { map(it)["type"] == "null" } == true
        out.append("    /** The ops a host takes. */\n")
        out.append("    val TAKEN = setOf(${requests.map { op(defs[it]!!) }.distinct().joinToString { "\"$it\"" }})\n")
        out.append("    private val LINES = mapOf( // (an op's messages: each one's name, and its kind)\n")
        for (o in messages.map { op(defs[it]!!) }.distinct())
            out.append("        \"$o\" to listOf(${messages.filter { op(defs[it]!!) == o }.joinToString {
                "\"$it\" to \"${kind[it]}\"" }}),\n")
        out.append("    )\n    private val NULLABLE = mapOf( // (an op's fields that may be null, in any of its lines)\n")
        for (o in messages.map { op(defs[it]!!) }.distinct()) {
            val fields = messages.filter { op(defs[it]!!) == o }
                .flatMap { n -> props(defs[n]!!).filterValues { nullable(it) }.keys }.distinct()
            if (fields.isNotEmpty()) out.append("        \"$o\" to setOf(${fields.joinToString { "\"$it\"" }}),\n")
        }
        out.append("    )\n    private val REQUEST = setOf(\"request\")\n\n")
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

    /** msg without the nulls its op's fields can't have (at its top: a field whose type hasn't null), as what's sent
     * leaves them out; msg itself if it has none. */
    fun unnulled(msg: JSONObject): JSONObject {
        val keep = NULLABLE[msg.optString("op")].orEmpty()
        val names = msg.keys().asSequence().filter { !msg.isNull(it) || it in keep }.toList()
        return if (names.size == msg.length()) msg else JSONObject(msg, names.toTypedArray())
    }

    const val UNSUPPORTED = "unsupported_operation" // (the schema's own refusals: their codes, and what each says)
    const val INVALID = "invalid_data_type"
    private val SAID = mapOf(UNSUPPORTED to "unsupported operation", INVALID to "invalid data type")

    /** One of the schema's own refusals (UNSUPPORTED, INVALID), for request req: an error, the same from every host,
     * with what was wrong in detail. */
    fun refused(code: String, req: Any?, detail: String? = null): JSONObject = JSONObject().put("op", Op.ERROR)
        .apply { if (req != null && req != JSONObject.NULL) put("req", req) }.put("error", SAID.getValue(code)).put("code", code)
        .apply { if (detail != null) put("detail", detail) }

    /** What a request a host takes (one of takes' ops, as a request's line has it) is answered with if it can't be:
     * refused(UNSUPPORTED) for an op it doesn't take, refused(INVALID) for fields not as the schema has them; null for
     * one it takes as it is (wire.refusal). What's wrong, in the log too. */
    fun refusal(msg: JSONObject, takes: Set<String> = TAKEN): JSONObject? {
        val op = msg.optString("op")
        val bad = if (op !in takes) "no op '" + op + "' here" else invalid(msg) ?: return null
        android.util.Log.w("wire", "refused: " + bad)
        return refused(if (op !in takes) UNSUPPORTED else INVALID, msg.opt("req"), bad)
    }

    private val defs: JSONObject by lazy { // (each message and entity, by its name: schema/types)
        JSONObject(WireCheck::class.java.getResourceAsStream("/wire-types.json")!!.bufferedReader().use { it.readText() })
    }

    /** What of a request isn't as its op's request lines have it, types too; null if one of them fits. */
    fun invalid(msg: JSONObject): String? =
        if (LINES[msg.optString("op")].orEmpty().none { it.second in REQUEST }) "no request " + msg.optString("op")
        else problems(msg, REQUEST).firstOrNull()

    /** Everything about a message that isn't as the schema has it, types too (wire.problems): as the one of its op's
     * lines (of those kinds, if kinds is given) it's nearest; none if it's one of them. */
    fun problems(msg: JSONObject, kinds: Set<String>? = null): List<String> {
        val op = msg.optString("op")
        val lines = LINES[op] ?: return listOf(problem(msg) ?: "not a message")
        var best: List<String>? = null
        for ((name, kind) in lines) {
            if (kinds != null && kind !in kinds) continue
            val found = ArrayList<String>()
            valid(defs.getJSONObject(name), msg, op, found)
            if (found.isEmpty()) return found
            if (best == null || found.size < best.size) best = found
        }
        return best.orEmpty()
    }

    private fun valid(s: JSONObject, v: Any?, at: String, out: MutableList<String>) {
        val ref = s.optString("\${'$'}ref")
        if (ref.isNotEmpty()) return valid(defs.getJSONObject(ref.removeSuffix(".json")), v, at, out)
        s.optJSONArray("anyOf")?.let { a ->
            if ((0 until a.length()).none { ArrayList<String>().also { t -> valid(a.getJSONObject(it), v, at, t) }.isEmpty() })
                out.add(at + ": " + v.toString().take(60) + " is none of what it may be")
            return
        }
        if (s.has("const") && v != s.get("const")) {
            out.add(at + ": not " + s.get("const"))
            return
        }
        s.optJSONArray("enum")?.let { e ->
            if ((0 until e.length()).none { e.get(it) == v }) {
                out.add(at + ": " + v + " isn't one of " + e)
                return
            }
        }
        if (s.has("type")) {
            val kinds = s.optJSONArray("type")?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: listOf(s.getString("type"))
            if (kinds.none { fits(it, v) }) {
                out.add(at + ": " + (v?.javaClass?.simpleName ?: "nothing") + " where it's " + kinds.joinToString("/"))
                return
            }
        }
        if (v is JSONObject) {
            val props = s.optJSONObject("properties")
            if (props != null) {
                s.optJSONArray("required")?.let { r ->
                    for (i in 0 until r.length()) if (!v.has(r.getString(i))) out.add(at + "." + r.getString(i) + ": missing") }
                for (k in v.keys()) {
                    val p = props.optJSONObject(k)
                    if (p != null) valid(p, v.opt(k), at + "." + k, out)
                    else if (s.opt("additionalProperties") == false) out.add(at + "." + k + ": no such field")
                }
            } else s.optJSONObject("additionalProperties")?.let { each ->
                for (k in v.keys()) valid(each, v.opt(k), at + "[" + k + "]", out) }
        }
        if (v is JSONArray) s.optJSONObject("items")?.let { each ->
            for (i in 0 until v.length()) valid(each, v.opt(i), at + "[" + i + "]", out) }
    }

    private fun fits(kind: String, v: Any?) = when (kind) {
        "string" -> v is String
        "integer" -> v is Int || v is Long || v is java.math.BigInteger
        "number" -> v is Number
        "boolean" -> v is Boolean
        "object" -> v is JSONObject
        "array" -> v is JSONArray
        "null" -> v == null || v == JSONObject.NULL
        else -> true
    }
}

""")
        out.append("private fun JSONObject.wireStr(key: String): String? = if (isNull(key)) null else optString(key)\n\n")
        out.append("/** The wire's messages and the entities in them: each built with the fields it's given (those left null aren't " +
            "sent), json() as it goes, from() as it came (a field it doesn't have left out). */\nobject Msg {\n")
        for ((name, d) in defs) about(files.getValue(name).third) {
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
        resourcesDir.get().asFile.mkdirs()
        resourcesDir.get().asFile.resolve("wire-types.json").writeText(groovy.json.JsonOutput.toJson(defs))
    }
}

val wireSchema = tasks.register<WireSchema>("wireSchema") {
    typesDir.set(rootProject.layout.projectDirectory.dir("../schema/types"))
    variablesDir.set(rootProject.layout.projectDirectory.dir("../schema/variables"))
    usesDir.set(layout.projectDirectory.dir("src/main/java"))
    outputDir.set(layout.buildDirectory.dir("generated/wireSchema"))
    resourcesDir.set(layout.buildDirectory.dir("generated/wireSchemaResources"))
}

androidComponents {
    onVariants { variant ->
        variant.sources.java?.addGeneratedSourceDirectory(wireSchema, WireSchema::outputDir)
        variant.sources.resources?.addGeneratedSourceDirectory(wireSchema, WireSchema::resourcesDir)
    }
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
    // the screenshot rig (./gradlew --offline screenshots): Robolectric draws the app's screens, Roborazzi saves them
    testImplementation(libs.junit)
    testImplementation(libs.robolectric)
    testImplementation(libs.roborazzi)
    constraints { // (Robolectric's androidx.test, at the versions the offline cache has)
        testImplementation("androidx.test:core:1.6.1")
        testImplementation("androidx.test.services:storage:1.5.0")
    }
}

/** The screenshot rig: the app's screens drawn with fixture data (src/test/.../screenshots/Screens.kt), each time it's
 * run, into app/build/screenshots/<theme>/<name>.png. No emulator: Robolectric draws them on the JVM. */
tasks.register("screenshots") {
    group = "verification"
    description = "Renders the app's screens with fixture data to app/build/screenshots"
    dependsOn("testDebugUnitTest")
}
tasks.matching { it.name == "testDebugUnitTest" }.configureEach {
    val out = layout.buildDirectory.dir("screenshots")
    doFirst { out.get().asFile.deleteRecursively() } // (only what this run drew)
}
tasks.withType<Test>().configureEach {
    outputs.upToDateWhen { false }
    timeout.set(Duration.ofMinutes(10)) // (a screen that never settles: stopped, not left running)
}
