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
