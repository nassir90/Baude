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
