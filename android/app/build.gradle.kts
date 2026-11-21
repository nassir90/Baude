import java.math.BigDecimal
import java.math.MathContext
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
/** The commit it's built from, whole ("" outside a checkout): said in its hello, as Baude's is (Mesh Versions). */
val appCommit = if (findProperty("baudeVersionCode") != null) "" else git("rev-parse", "HEAD") ?: ""
/** The same commit, short, and what update.changelog reads of it: every commit it has but merges, newest first, as
 * short hash, time and subject on a line each (the phone's Changelog, from the asset changelog.txt). */
val appShort = if (findProperty("baudeVersionCode") != null) "" else git("log", "-1", "--format=%h") ?: ""
val appChangelog = if (findProperty("baudeVersionCode") != null) "" else
    git("log", "--no-merges", "--format=%h%x1f%ct%x1f%s", "HEAD") ?: ""

android {
    namespace = "dev.baude.android"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.baude.android"
        minSdk = 26
        targetSdk = 35
        versionCode = appVersionCode // (version(): from git)
        versionName = appVersionName
        buildConfigField("String", "COMMIT", "\"$appCommit\"")
        buildConfigField("String", "SHORT", "\"$appShort\"")
        buildConfigField("long", "MADE", "${appCommitTime}L")
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
        buildConfig = true // (BuildConfig.COMMIT, MADE: the commit it's built from)
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

/** fonts/ (the code fonts Settings → Code Font offers, open fonts with their licences) as the app's assets, a
 * variable font's "[wght]" in its file's name made "-VF". */
abstract class AppFonts : DefaultTask() {
    @get:InputDirectory abstract val source: DirectoryProperty
    @get:OutputDirectory abstract val outputDir: DirectoryProperty

    @TaskAction
    fun write() {
        val from = source.get().asFile
        val to = outputDir.get().asFile.resolve("fonts")
        to.deleteRecursively()
        from.walkTopDown().filter { it.isFile && it.extension != "md" }.forEach { f ->
            val dest = to.resolve(f.parentFile.relativeTo(from).path).resolve(f.name.replace(Regex("\\[.*\\]"), "-VF"))
            dest.parentFile.mkdirs()
            f.copyTo(dest, true)
        }
    }
}

val appFontsTask = tasks.register<AppFonts>("appFonts") {
    source.set(rootProject.layout.projectDirectory.dir("../fonts"))
    outputDir.set(layout.buildDirectory.dir("generated/fonts"))
}

androidComponents {
    onVariants { variant -> variant.sources.assets?.addGeneratedSourceDirectory(appFontsTask, AppFonts::outputDir) }
}

/** update.changelog's list for the commit the app's built from (asset changelog.txt): the phone's ☰ → Changelog. */
abstract class AppChangelog : DefaultTask() {
    @get:Input abstract val log: Property<String>
    @get:OutputDirectory abstract val outputDir: DirectoryProperty

    @TaskAction
    fun write() {
        val dir = outputDir.get().asFile
        dir.mkdirs()
        dir.resolve("changelog.txt").writeText(log.get())
    }
}

val appChangelogTask = tasks.register<AppChangelog>("appChangelog") {
    log.set(appChangelog)
    outputDir.set(layout.buildDirectory.dir("generated/changelog"))
}

androidComponents {
    onVariants { variant -> variant.sources.assets?.addGeneratedSourceDirectory(appChangelogTask, AppChangelog::outputDir) }
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

/** schema/copy (each file a group of messages, each message Unicode MessageFormat 2's, as baudelib/wording.py reads
 * them), as Kotlin made as the app's built (dev.baude.android.Wording, in build/generated: never kept): an object
 * for each group (hosts.json: Wording.Hosts) and in it each message (a const val, or a fun of what it's given, by name:
 * Wording.Hosts.looking(host = name)). A {$n :number}'s a Number, said with its thousands grouped; a .match's variants
 * are a when, the best fit first. */
abstract class BaudeCopy : DefaultTask() {
    @get:InputDirectory abstract val copy: DirectoryProperty
    @get:OutputDirectory abstract val outputDir: DirectoryProperty

    private class Message(val declared: Map<String, String>, val selectors: List<String>,
                          val variants: List<Pair<List<String>, List<Any>>>) // (a part's text, or a Pair: name, function)

    private class Parser(val s: String, val where: String) {
        var i = 0
        fun fail(what: String): Nothing = throw GradleException("schema/copy/$where: $what at $i")
        fun peek(w: String) = s.startsWith(w, i)
        fun take(w: String) { if (!peek(w)) fail("'$w' expected"); i += w.length }
        fun space(needed: Boolean = false) {
            val start = i
            while (i < s.length && s[i] in " \t\n\r\u3000") i++
            if (needed && i == start) fail("a space expected")
        }
        fun name(): String {
            val m = Regex("[a-z][a-z0-9_]*").matchAt(s, i) ?: fail("a name expected")
            i = m.range.last + 1
            return m.value
        }
        fun literal(): String {
            if (peek("|")) {
                val end = s.indexOf('|', i + 1).takeIf { it >= 0 } ?: fail("an unclosed |literal|")
                return s.substring(i + 1, end).also { i = end + 1 }
            }
            val m = Regex("-?\\d+(?:\\.\\d+)?(?:[eE][-+]?\\d+)?|[A-Za-z_][\\w.-]*").matchAt(s, i) ?: fail("a key expected")
            i = m.range.last + 1
            return m.value
        }
        fun expression(): Pair<String, String?> {
            take("{"); space(); take("$")
            val v = name()
            space()
            var fn: String? = null
            if (peek(":")) {
                i++
                fn = name()
                if (fn != "number" && fn != "string") fail("no function :$fn")
                space()
            }
            take("}")
            return v to fn
        }
        fun pattern(quoted: Boolean): List<Any> {
            val parts = ArrayList<Any>()
            val text = StringBuilder()
            while (i < s.length) {
                val c = s[i]
                when {
                    c == '\\' -> {
                        if (i + 1 >= s.length || s[i + 1] !in "\\{}|") fail("an escape of \\, {, } or | expected")
                        text.append(s[i + 1]); i += 2
                    }
                    quoted && peek("}}") -> break
                    c == '{' -> {
                        if (text.isNotEmpty()) { parts.add(text.toString()); text.clear() }
                        parts.add(expression())
                    }
                    c == '}' -> fail("a lone }")
                    else -> { text.append(c); i++ }
                }
            }
            if (text.isNotEmpty()) parts.add(text.toString())
            return parts
        }
        fun message(): Message {
            space()
            if (!peek(".")) { i = 0; return Message(emptyMap(), emptyList(), listOf(emptyList<String>() to pattern(false))) }
            val declared = LinkedHashMap<String, String>()
            while (peek(".input")) {
                i += ".input".length; space()
                val (v, fn) = expression()
                if (v in declared || fn == null) fail("\$$v declared twice, or with no function")
                declared[v] = fn
                space()
            }
            if (peek("{{")) {
                i += 2
                val out = pattern(true)
                take("}}"); space()
                if (i != s.length) fail("more after the pattern")
                return Message(declared, emptyList(), listOf(emptyList<String>() to out))
            }
            take(".match")
            val selectors = ArrayList<String>()
            while (true) {
                space(needed = true)
                if (!peek("$")) break
                i++
                val v = name()
                if (v !in declared) fail("\$$v is matched on, but not declared with .input")
                selectors.add(v)
            }
            if (selectors.isEmpty()) fail("a selector expected")
            val variants = ArrayList<Pair<List<String>, List<Any>>>()
            while (i < s.length) {
                val keys = ArrayList<String>()
                while (!peek("{{")) {
                    if (peek("*")) { i++; keys.add("*") } else keys.add(literal())
                    space(needed = !peek("{{"))
                }
                if (keys.size != selectors.size) fail("${selectors.size} keys expected")
                i += 2
                val out = pattern(true)
                take("}}")
                variants.add(keys to out)
                space()
            }
            if (variants.none { (keys, _) -> keys.all { it == "*" } }) fail("no variant for the rest (* for each selector)")
            return Message(declared, selectors, variants)
        }
    }

    private val keywords = setOf("as", "break", "class", "continue", "do", "else", "false", "for", "fun", "if", "in",
        "interface", "is", "null", "object", "package", "return", "super", "this", "throw", "true", "try", "typealias",
        "typeof", "val", "var", "when", "while")

    private fun camel(name: String) = name.split("_").mapIndexed { i, w ->
        if (i == 0) w else w.replaceFirstChar { it.uppercase() } }.joinToString("").let { if (it in keywords) "`$it`" else it }

    private fun pascal(name: String) = camel(name).trim('`').replaceFirstChar { it.uppercase() }

    /** The keys JSON text has twice in one object, which JsonSlurper takes the last of. */
    private fun twice(text: String): List<String> {
        val keys = ArrayList<HashSet<String>?>() // (each open object's keys; null for an array)
        val out = ArrayList<String>()
        var i = 0
        while (i < text.length) {
            when (text[i]) {
                '{' -> keys.add(HashSet())
                '[' -> keys.add(null)
                '}', ']' -> keys.removeLastOrNull()
                '"' -> {
                    val start = ++i
                    while (i < text.length && text[i] != '"') i += if (text[i] == '\\') 2 else 1
                    var j = i + 1
                    while (j < text.length && text[j].isWhitespace()) j++
                    if (j < text.length && text[j] == ':' && keys.lastOrNull()?.add(text.substring(start, i)) == false)
                        out.add(text.substring(start, i))
                }
            }
            i++
        }
        return out
    }

    private fun comment(text: String) = text.replace("*/", "* /").replace("/*", "/ *").replace("\n", " ")

    private fun quote(text: String) = text.replace("\\", "\\\\").replace("\"", "\\\"").replace("$", "\\$")
        .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

    @TaskAction
    fun write() {
        val out = StringBuilder()
        out.append("// Made from schema/copy as the app's built (build.gradle.kts's baudeCopy): never edited, never kept.\n")
        out.append("package dev.baude.android\n\n")
        out.append("object Wording {\n")
        out.append("    fun number(n: Number): String = java.text.NumberFormat.getNumberInstance(java.util.Locale.ENGLISH).format(n)\n")
        out.append("    fun category(n: Number) = if (n.toDouble() == 1.0) \"one\" else \"other\"\n")
        val seen = HashMap<String, String>()
        val objects = HashSet<String>()
        val files = copy.get().asFile.listFiles { f -> f.name.endsWith(".json") && !f.name.startsWith(".") }!!
            .sortedBy { it.name }
        for (f in files) {
            val group = f.name.removeSuffix(".json")
            if (!Regex("[a-z][a-z0-9_]*").matches(group)) throw GradleException("schema/copy/${f.name}: not named in lower case")
            @Suppress("UNCHECKED_CAST")
            val doc = groovy.json.JsonSlurper().parse(f) as Map<String, Any?>
            twice(f.readText()).firstOrNull()?.let { throw GradleException("schema/copy/${f.name}: $it is there twice") }
            if (doc.keys != setOf("description", "messages"))
                throw GradleException("schema/copy/${f.name}: a description and messages, and nothing else")
            out.append("\n    /** ${comment(doc["description"] as String)} */\n")
            if (!objects.add(pascal(group))) throw GradleException("schema/copy/${f.name}: another group's Kotlin name too")
            out.append("    object ${pascal(group)} {\n")
            val members = HashSet<String>()
            @Suppress("UNCHECKED_CAST")
            for ((key, text) in doc["messages"] as Map<String, Any?>) {
                val where = "${f.name}: $key"
                if (text !is String || !Regex("[a-z][a-z0-9_]*").matches(key))
                    throw GradleException("schema/copy/$where: a lower-case key and a message's text")
                seen[text]?.let { throw GradleException("schema/copy/$where: says what $it says") }
                seen[text] = "$group.$key"
                if (!members.add(camel(key))) throw GradleException("schema/copy/$where: another key's Kotlin name too")
                val m = Parser(text, where).message()
                val types = LinkedHashMap<String, String>()
                for ((v, fn) in m.declared) types[v] = if (fn == "number") "Number" else "String"
                for ((_, parts) in m.variants) for (p in parts) if (p is Pair<*, *>) {
                    val v = p.first as String
                    if (v !in types) types[v] = if (p.second == "number") "Number" else "Any"
                }
                fun say(parts: List<Any>) = "\"" + parts.joinToString("") { p ->
                    if (p is String) quote(p) else {
                        val (v, fn) = p as Pair<*, *>
                        val number = (fn ?: m.declared[v]) == "number"
                        "\${" + (if (number) "Wording.number(${camel(v as String)})" else camel(v as String)) + "}"
                    }
                } + "\""
                out.append("        /** ${comment(text)} */\n")
                if (types.isEmpty()) {
                    out.append("        const val ${camel(key)} = ${say(m.variants.single().second)}\n")
                    continue
                }
                val args = types.entries.joinToString(", ") { (v, t) -> "${camel(v)}: $t" }
                if (m.selectors.isEmpty()) {
                    out.append("        fun ${camel(key)}($args): String = ${say(m.variants.single().second)}\n")
                    continue
                }
                fun rank(sel: String, k: String) = when {
                    k == "*" -> 2
                    m.declared[sel] == "number" && k.toDoubleOrNull() == null -> 1
                    else -> 0
                }
                fun test(sel: String, k: String): String? {
                    val v = camel(sel)
                    return when {
                        k == "*" -> null
                        m.declared[sel] != "number" -> "$v == \"${quote(k)}\""
                        k.toDoubleOrNull() != null -> "$v.toDouble() == ${k.toDouble()}"
                        else -> "Wording.category($v) == \"${quote(k)}\""
                    }
                }
                val ordered = m.variants.sortedWith { a, b ->
                    m.selectors.indices.map { rank(m.selectors[it], a.first[it]).compareTo(rank(m.selectors[it], b.first[it])) }
                        .firstOrNull { it != 0 } ?: 0
                }
                out.append("        fun ${camel(key)}($args): String = when {\n")
                for ((keys, parts) in ordered) {
                    val tests = m.selectors.indices.mapNotNull { test(m.selectors[it], keys[it]) }
                    out.append("            ${if (tests.isEmpty()) "else" else tests.joinToString(" && ")} -> ${say(parts)}\n")
                    if (tests.isEmpty()) break
                }
                out.append("        }\n")
            }
            out.append("    }\n")
        }
        out.append("}\n")
        val dir = outputDir.get().asFile.resolve("dev/baude/android")
        dir.mkdirs()
        dir.resolve("Wording.kt").writeText(out.toString())
    }
}

val baudeCopy = tasks.register<BaudeCopy>("baudeCopy") {
    copy.set(rootProject.layout.projectDirectory.dir("../schema/copy"))
    outputDir.set(layout.buildDirectory.dir("generated/baudeCopy"))
}

androidComponents {
    onVariants { variant -> variant.sources.java?.addGeneratedSourceDirectory(baudeCopy, BaudeCopy::outputDir) }
}

/** schema/icons as the app's built (in build/generated: never kept): the symbolic sets the phone draws, adwaita
 * (GNOME's Adwaita icons) and baude (Baude's own), as dev.baude.android.Adwaita, an ImageVector each by its name
 * (go-previous-symbolic.svg: Adwaita.GoPrevious; baude-return-symbolic.svg: Adwaita.Return), its 16-square drawing in
 * the middle of a 19.2 square (so drawn at the sizes Material's 24-square ones were, it's as big as they looked); and
 * links (a link's logo, drawn in the text's colour) as dev.baude.android.LinkLogos, each logo's shapes, filled or
 * stroked, and what its mask cuts from them; and apps/baude-notification.svg as the notification's icon (drawable
 * ic_baude). The SVG they use: path, rect and circle, filled or stroked, in groups that scale, translate or rotate,
 * with opacity, and a mask (white shows, black cuts) for a cut-out. */
abstract class BaudeIcons : DefaultTask() {
    @get:InputDirectory abstract val icons: DirectoryProperty
    @get:OutputDirectory abstract val outputDir: DirectoryProperty
    @get:OutputDirectory abstract val resDir: DirectoryProperty

    private fun read(f: File): org.w3c.dom.Element = javax.xml.parsers.DocumentBuilderFactory.newInstance()
        .apply { isNamespaceAware = false }.newDocumentBuilder().parse(f).documentElement

    private fun org.w3c.dom.Element.attr(k: String): String? = if (hasAttribute(k)) getAttribute(k) else null

    private fun org.w3c.dom.Element.kids() = (0 until childNodes.length).map { childNodes.item(it) }
        .filterIsInstance<org.w3c.dom.Element>()

    private fun num(s: String?) = s?.takeIf { it.isNotEmpty() }?.toDouble() ?: 0.0

    /** v as Python's repr says a float (a whole one with its ".0"), as the paths it's in were always written. */
    private fun f(v: Double) = v.toString()

    /** v as %g says it: six significant figures, no trailing zeros. */
    private fun g(v: Double) = BigDecimal(v).round(MathContext(6)).stripTrailingZeros()
        .toPlainString()

    private fun rectD(e: org.w3c.dom.Element): String {
        val (x, y, w, h) = listOf("x", "y", "width", "height").map { num(e.attr(it)) }
        val r = minOf(num(e.attr("rx")?.takeIf { it.isNotEmpty() } ?: e.attr("ry")), w / 2, h / 2)
        if (r == 0.0) return "M${f(x)},${f(y)}h${f(w)}v${f(h)}h${f(-w)}z"
        return "M${f(x + r)},${f(y)}H${f(x + w - r)}A${f(r)},${f(r)} 0 0 1 ${f(x + w)},${f(y + r)}V${f(y + h - r)}" +
            "A${f(r)},${f(r)} 0 0 1 ${f(x + w - r)},${f(y + h)}H${f(x + r)}A${f(r)},${f(r)} 0 0 1 ${f(x)},${f(y + h - r)}" +
            "V${f(y + r)}A${f(r)},${f(r)} 0 0 1 ${f(x + r)},${f(y)}z"
    }

    private fun circleD(e: org.w3c.dom.Element): String {
        val (cx, cy, r) = listOf("cx", "cy", "r").map { num(e.attr(it)) }
        return "M${f(cx - r)},${f(cy)}a${f(r)},${f(r)} 0 1 0 ${f(2 * r)},0a${f(r)},${f(r)} 0 1 0 ${f(-2 * r)},0z"
    }

    /** An SVG transform as the group's arguments (scale, translate and rotate about a point, which is all these use). */
    private fun transform(t: String?, where: String): Map<String, Double> {
        val out = LinkedHashMap<String, Double>()
        for (m in Regex("(\\w+)\\(([^)]*)\\)").findAll(t ?: "")) {
            val a = m.groupValues[2].trim().split(Regex("[ ,]+")).map { it.toDouble() }
            when (m.groupValues[1]) {
                "scale" -> { out["scaleX"] = a[0]; out["scaleY"] = a.getOrElse(1) { a[0] } }
                "translate" -> { out["translationX"] = a[0]; out["translationY"] = a.getOrElse(1) { 0.0 } }
                "rotate" -> {
                    out["rotate"] = a[0]
                    if (a.size == 3) { out["pivotX"] = a[1]; out["pivotY"] = a[2] }
                }
                else -> throw GradleException("schema/icons/$where: transform ${m.groupValues[1]} isn't drawn here")
            }
        }
        return out
    }

    private fun alpha(e: org.w3c.dom.Element): Double {
        val style = (e.attr("style") ?: "").replace(" ", "").split(";").filter { ":" in it }
            .associate { it.substringBefore(":") to it.substringAfter(":") }
        var a = 1.0
        for (k in listOf("opacity", "fill-opacity")) (e.attr(k)?.takeIf { it.isNotEmpty() } ?: style[k])?.let { a *= it.toDouble() }
        return a
    }

    private fun group(t: Map<String, Double>) = "addGroup(${t.entries.joinToString(", ") { "${it.key} = ${g(it.value)}f" }})"

    /** Kotlin lines drawing e's shapes (groups for transforms), each path's alpha as its own and its groups' times. */
    private fun walk(e: org.w3c.dom.Element, out: MutableList<String>, depth: Int, inherited: Double, where: String) {
        val tag = e.tagName.substringAfter(':')
        val a = inherited * alpha(e)
        if (e.attr("fill") == "none" || tag in setOf("defs", "title", "metadata", "clipPath", "style")) return
        val pad = "            " + "    ".repeat(depth)
        if (tag in setOf("path", "rect", "circle")) {
            val d = when (tag) { "path" -> e.attr("d") ?: ""; "rect" -> rectD(e); else -> circleD(e) }
            if (d.isEmpty()) return
            val rule = if ((e.attr("fill-rule")?.takeIf { it.isNotEmpty() } ?: e.attr("clip-rule")) == "evenodd") "EvenOdd"
                else "NonZero"
            val line = "p(\"$d\", ${g(a)}f, PathFillType.$rule)"
            val t = transform(e.attr("transform"), where)
            if (t.isEmpty()) out.add(pad + line)
            else { out.add(pad + group(t)); out.add("$pad    $line"); out.add("${pad}clearGroup()") }
            return
        }
        if (tag != "svg" && tag != "g") throw GradleException("schema/icons/$where: <$tag> isn't drawn here")
        val t = if (tag == "g") transform(e.attr("transform"), where) else emptyMap()
        if (t.isNotEmpty()) out.add(pad + group(t))
        for (c in e.kids()) walk(c, out, if (t.isNotEmpty()) depth + 1 else depth, a, where)
        if (t.isNotEmpty()) out.add("${pad}clearGroup()")
    }

    private fun pascal(name: String) = name.split("-").joinToString("") { it.replaceFirstChar { c -> c.uppercase() } }

    /** apps/baude-notification.svg as a vector drawable: its paths, in its colours, as Android's notifications take. */
    private fun drawable(f: File): String {
        val svg = read(f)
        val box = svg.getAttribute("viewBox").trim().split(Regex("[ ,]+"))
        val out = StringBuilder("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n")
        out.append("<!-- Made from schema/icons/apps/${f.name} as the app's built (build.gradle.kts's baudeIcons). -->\n")
        out.append("<vector xmlns:android=\"http://schemas.android.com/apk/res/android\"\n")
        out.append("    android:width=\"${svg.getAttribute("width")}dp\" android:height=\"${svg.getAttribute("height")}dp\"")
        out.append(" android:viewportWidth=\"${box[2]}\" android:viewportHeight=\"${box[3]}\">\n")
        for (p in svg.kids()) {
            if (p.tagName != "path") throw GradleException("schema/icons/apps/${f.name}: <${p.tagName}> isn't drawn here")
            val hex = (p.attr("fill") ?: "#000").removePrefix("#")
            val rgb = if (hex.length == 3) hex.map { "$it$it" }.joinToString("") else hex
            out.append("    <path android:fillColor=\"#FF${rgb.uppercase()}\"")
            if (p.attr("fill-rule") == "evenodd") out.append(" android:fillType=\"evenOdd\"")
            out.append("\n        android:pathData=\"${p.getAttribute("d")}\" />\n")
        }
        return out.append("</vector>\n").toString()
    }

    /** A shape of a logo as LinkLogos has it: P (a path), C (a circle) or Rr (a rectangle), stroked if it has a
     * stroke, else filled. */
    private fun shape(e: org.w3c.dom.Element, where: String): String {
        fun n(k: String) = "${num(e.attr(k))}f"
        val stroke = e.attr("stroke")?.takeIf { it != "none" } != null
        val how = if (stroke) "width = ${num(e.attr("stroke-width") ?: "1")}f" else "fill = true"
        return when (e.tagName) {
            "path" -> "P(\"${e.getAttribute("d")}\", $how)"
            "circle" -> "C(${n("cx")}, ${n("cy")}, ${n("r")}, $how)"
            "rect" -> "Rr(${n("x")}, ${n("y")}, ${n("width")}, ${n("height")}, ${n("rx")}, $how)"
            else -> throw GradleException("schema/icons/$where: <${e.tagName}> isn't drawn here")
        }
    }

    /** LinkLogos: schema/icons/links' logos, by their files' names. */
    private fun linkLogos(dir: File): String {
        val out = StringBuilder()
        out.append("// Made from schema/icons/links as the app's built (build.gradle.kts's baudeIcons): never edited, never kept.\n")
        out.append("package dev.baude.android\n\n")
        out.append("/** A link's logos (schema/icons/links, as the desktop draws them), in a 16 by 16 square. */\n")
        out.append("object LinkLogos {\n")
        out.append("    /** A logo's shape: a path, circle or rectangle, filled or stroked in the text's colour. */\n")
        out.append("    sealed interface Shape\n")
        out.append("    class P(val d: String, val fill: Boolean = false, val width: Float = 1.35f) : Shape\n")
        out.append("    class C(val x: Float, val y: Float, val r: Float, val fill: Boolean = false, val width: Float = 1.35f) : Shape\n")
        out.append("    class Rr(val x: Float, val y: Float, val w: Float, val h: Float, val rx: Float, val fill: Boolean = false,\n")
        out.append("             val width: Float = 1.35f) : Shape\n\n")
        out.append("    /** A logo: its shapes, and for a cut-out one (git, Kubernetes, a database) what's cut from them. */\n")
        out.append("    class Logo(val shapes: List<Shape>, val cut: List<Shape> = emptyList())\n\n")
        out.append("    /** Each logo, by its file's name. */\n")
        out.append("    val all: Map<String, Logo> = mapOf(\n")
        for (f in dir.listFiles { f -> f.name.endsWith(".svg") }!!.sortedBy { it.name }) {
            val where = "links/${f.name}"
            val svg = read(f)
            val masks = svg.getElementsByTagName("mask").let { m -> (0 until m.length).map { m.item(it) as org.w3c.dom.Element } }
            val shapes = ArrayList<String>()
            val cut = ArrayList<String>()
            for (e in svg.kids()) {
                if (e.tagName == "defs") continue
                shapes.add(shape(e, where))
                val ref = e.attr("mask") ?: continue
                val mask = masks.firstOrNull { "url(#${it.getAttribute("id")})" == ref }
                    ?: throw GradleException("schema/icons/$where: no mask $ref")
                for (c in mask.kids()) if (c.attr("fill")?.lowercase() !in setOf("#fff", "#ffffff", "white")) cut.add(shape(c, where))
            }
            val name = f.name.removeSuffix(".svg")
            out.append("        \"$name\" to Logo(listOf(${shapes.joinToString(", ")})")
            if (cut.isNotEmpty()) out.append(", listOf(${cut.joinToString(", ")})")
            out.append("),\n")
        }
        return out.append("    )\n}\n").toString()
    }

    @TaskAction
    fun write() {
        val root = icons.get().asFile
        val blocks = StringBuilder()
        val seen = HashMap<String, String>()
        for (set in listOf("adwaita", "baude")) {
            val files = root.resolve(set).listFiles { f -> f.name.endsWith("-symbolic.svg") }!!.sortedBy { it.name }
            for (f in files) {
                val name = f.name.removeSuffix("-symbolic.svg")
                val where = "$set/${f.name}"
                val member = pascal(if (set == "baude") name.removePrefix("baude-") else name)
                seen.put(member, where)?.let { throw GradleException("schema/icons/$where: Adwaita.$member, as $it is") }
                val lines = ArrayList<String>()
                walk(read(f), lines, 0, 1.0, where)
                if (lines.isEmpty()) throw GradleException("schema/icons/$where: nothing drawn")
                blocks.append("\n    val $member: ImageVector by lazy {\n        icon(\"$name\") {\n")
                lines.forEach { blocks.append(it).append('\n') }
                blocks.append("        }\n    }\n")
            }
        }
        val dir = outputDir.get().asFile.resolve("dev/baude/android")
        dir.mkdirs()
        dir.resolve("Adwaita.kt").writeText("""
            |// Made from schema/icons as the app's built (build.gradle.kts's baudeIcons): never edited, never kept. GNOME's
            |// Adwaita icons (schema/icons/adwaita: CC-BY-SA-3.0 or LGPL-3.0, its LICENSE) and Baude's own (schema/icons/baude).
            |package dev.baude.android
            |
            |import androidx.compose.ui.graphics.Color
            |import androidx.compose.ui.graphics.PathFillType
            |import androidx.compose.ui.graphics.SolidColor
            |import androidx.compose.ui.graphics.vector.ImageVector
            |import androidx.compose.ui.graphics.vector.PathParser
            |import androidx.compose.ui.unit.dp
            |
            |/** The icons the desktop draws (Adwaita's symbolic ones, and Baude's own), for the same things here. */
            |object Adwaita {
            |    private class Draw(val b: ImageVector.Builder) {
            |        fun p(d: String, alpha: Float, rule: PathFillType) {
            |            b.addPath(PathParser().parsePathString(d).toNodes(), fill = SolidColor(Color.Black), fillAlpha = alpha,
            |                pathFillType = rule)
            |        }
            |        fun addGroup(rotate: Float = 0f, pivotX: Float = 0f, pivotY: Float = 0f, scaleX: Float = 1f, scaleY: Float = 1f,
            |                     translationX: Float = 0f, translationY: Float = 0f) {
            |            b.addGroup(rotate = rotate, pivotX = pivotX, pivotY = pivotY, scaleX = scaleX, scaleY = scaleY,
            |                translationX = translationX, translationY = translationY)
            |        }
            |        fun clearGroup() { b.clearGroup() }
            |    }
            |
            |    /** A 16-square drawing in the middle of a 19.2 square: at Material's sizes, as big as its 24-square ones looked. */
            |    private fun icon(name: String, draw: Draw.() -> Unit): ImageVector {
            |        val b = ImageVector.Builder(name, 24.dp, 24.dp, 19.2f, 19.2f)
            |        b.addGroup(translationX = 1.6f, translationY = 1.6f)
            |        Draw(b).draw()
            |        b.clearGroup()
            |        return b.build()
            |    }
            |""".trimMargin() + blocks + "}\n")
        dir.resolve("LinkLogos.kt").writeText(linkLogos(root.resolve("links")))
        val res = resDir.get().asFile.resolve("drawable")
        res.mkdirs()
        res.resolve("ic_baude.xml").writeText(drawable(root.resolve("apps/baude-notification.svg")))
    }
}

val baudeIcons = tasks.register<BaudeIcons>("baudeIcons") {
    icons.set(rootProject.layout.projectDirectory.dir("../schema/icons"))
    outputDir.set(layout.buildDirectory.dir("generated/baudeIcons"))
    resDir.set(layout.buildDirectory.dir("generated/baudeIconsRes"))
}

androidComponents {
    onVariants { variant ->
        variant.sources.java?.addGeneratedSourceDirectory(baudeIcons, BaudeIcons::outputDir)
        variant.sources.res?.addGeneratedSourceDirectory(baudeIcons, BaudeIcons::resDir)
    }
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
