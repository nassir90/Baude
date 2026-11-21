package dev.baude.android

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import java.util.regex.Matcher
import java.util.regex.Pattern

/* The code highlighter: widgets.py's code_spans, regex_spans, grep_patterns and numbered_spans, ported. Pygments'
 * lexers are replaced by small regex lexers (one per language family) that classify as Pygments does. */

/** A span's token class: Pygments' token types the desktop's styles colour (schema/tokens' syntax). */
enum class Tok {
    KEYWORD, KEYWORD_CONSTANT, KEYWORD_TYPE, BUILTIN, FUNCTION, CLASS, DECORATOR, NAMESPACE, VARIABLE, CONSTANT, TAG,
    ATTRIBUTE, EXCEPTION, ENTITY, STRING, ESCAPE, INTERPOL, DOC, REGEX, NUMBER, COMMENT, PREPROC, OPERATOR,
    OPERATOR_WORD, INSERTED, DELETED, HEADING, NAME, LINE_NUMBER,
}

private typealias Span = Triple<Int, Int, Tok>
private typealias Emit = (Int, Int, Tok) -> Unit

private const val CODE_HIGHLIGHT_MAX = 20_000  // characters of a code block highlighted: its first

// ---------------------------------------------------------------------------------------------------------------
// The lexer engine

/** A string's insides (escapes, interpolations): group i of [regex] is coloured [toks][i - 1]. */
private class Inner(regex: String, val toks: List<Tok>) {
    val pattern: Pattern = Pattern.compile(regex)
}

/** What to do where [regex] matches: colour the match [tok], or its groups by [groups], or by [word] of its text; a
 * string's [inner] parts differently; or [custom]. With none of them the match is plain. */
private class Rule(
    regex: String,
    val tok: Tok? = null,
    val groups: List<Tok?>? = null,
    val word: ((String) -> Tok?)? = null,
    val inner: Inner? = null,
    val custom: ((Matcher, Emit) -> Unit)? = null,
) {
    val pattern: Pattern = Pattern.compile(regex)
}

private class Lang(val rules: List<Rule>, val shell: Boolean = false)

/** text's spans by [rules], tried in order at each position; what none matches is plain. */
private fun lex(text: String, rules: List<Rule>): List<Span> {
    val out = ArrayList<Span>()
    val emit: Emit = { a, b, t ->
        if (a < b) {
            val last = out.lastOrNull()
            if (last != null && last.third == t && last.second == a) out[out.size - 1] = Triple(last.first, b, t)
            else out.add(Triple(a, b, t))
        }
    }
    val n = text.length
    try {
        val ms = rules.map { it.pattern.matcher(text).useAnchoringBounds(false).useTransparentBounds(true) }
        var pos = 0
        while (pos < n) {
            val c = text[pos]
            if ((c == ' ' || c == '\t' || c == '\n') && pos > 0 && text[pos - 1] != '\n') {
                pos++  // (blanks inside a line start nothing; a line's first character is tried)
                continue
            }
            var hit = false
            for (i in rules.indices) {
                val m = ms[i]
                m.region(pos, n)
                if (m.lookingAt() && m.end() > pos) {
                    applyRule(rules[i], m, emit)
                    pos = m.end()
                    hit = true
                    break
                }
            }
            if (!hit) pos++
        }
    } catch (e: StackOverflowError) {
        // (a pathological text: what's been found stands)
    } catch (e: RuntimeException) {
    }
    return out
}

private fun applyRule(r: Rule, m: Matcher, emit: Emit) {
    val groups = r.groups
    val word = r.word
    val custom = r.custom
    val inner = r.inner
    when {
        custom != null -> custom(m, emit)
        groups != null -> for ((g, t) in groups.withIndex())
            if (t != null && m.start(g + 1) >= 0) emit(m.start(g + 1), m.end(g + 1), t)
        word != null -> word(m.group())?.let { emit(m.start(), m.end(), it) }
        r.tok != null && inner != null -> splitInner(m, inner, r.tok, emit)
        r.tok != null -> emit(m.start(), m.end(), r.tok)
    }
}

/** A string literal (the match) as its own token with the escapes and interpolations in it as theirs. */
private fun splitInner(m: Matcher, inner: Inner, base: Tok, emit: Emit) {
    val s = m.start()
    val e = m.end()
    val body = m.group()
    val im = inner.pattern.matcher(body)
    var at = 0
    while (im.find()) {
        if (im.end() == im.start()) continue
        val g = (1..im.groupCount()).firstOrNull { im.start(it) >= 0 } ?: continue
        if (im.start() > at) emit(s + at, s + im.start(), base)
        emit(s + im.start(), s + im.end(), inner.toks[g - 1])
        at = im.end()
    }
    if (s + at < e) emit(s + at, e, base)
}

private fun words(s: String): Set<String> = s.trim().split(Regex("\\s+")).toSet()

/** A word's token by its group (first group wins); [other] for a word in none of them. */
private fun classify(vararg groups: Pair<Tok, Set<String>>, ci: Boolean = false, other: Tok? = Tok.NAME): (String) -> Tok? {
    val map = HashMap<String, Tok>()
    for ((t, ws) in groups) for (w in ws) map.putIfAbsent(if (ci) w.lowercase() else w, t)
    return { w -> map[if (ci) w.lowercase() else w] ?: other }
}

private const val ID = "[\\p{L}_][\\p{L}\\p{N}_]*"
private const val NOT_IN_WORD = "(?<![\\p{L}\\p{N}_])"
private const val NOT_AFTER_DOT = "(?<![.\\p{L}\\p{N}_])"

// ---------------------------------------------------------------------------------------------------------------
// Python

private val PY_ESCAPE = """\\(?:[\\'"abfnrtv]|\n|N\{[^}]*\}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2}|[0-7]{1,3})"""
private val PY_FORMAT = """(%(?:\(\w+\))?[-#0 +]*(?:[0-9]+|\*)?(?:\.(?:[0-9]+|\*))?[hlL]?[E-GXc-giorsaux%])""" +
    """|(\{(?:\w+(?:\.\w+|\[[^\]]+\])*)?(?:![sra])?(?::[^{}\n]*)?\})"""

private val PY_KEYWORDS = words(
    "assert async await break continue del elif else except finally for global if lambda pass raise nonlocal return try " +
        "while yield as with from import def class"
)
private val PY_CONSTANTS = words("True False None")
private val PY_OPERATOR_WORDS = words("in is and or not")
private val PY_BUILTINS = words(
    "__import__ abs aiter all any bin bool bytearray breakpoint bytes callable chr classmethod compile complex delattr " +
        "dict dir divmod enumerate eval filter float format frozendict frozenset getattr globals hasattr hash hex id " +
        "input int isinstance issubclass iter len list locals map max memoryview min next object oct open ord pow print " +
        "property range repr reversed round sentinel set setattr slice sorted staticmethod str sum super tuple type " +
        "vars zip self Ellipsis NotImplemented cls"
)
private val PY_EXCEPTIONS = words(
    "ArithmeticError AssertionError AttributeError BaseException BufferError BytesWarning DeprecationWarning EOFError " +
        "EnvironmentError Exception FloatingPointError FutureWarning GeneratorExit IOError ImportError ImportWarning " +
        "IndentationError IndexError KeyError KeyboardInterrupt LookupError MemoryError NameError NotImplementedError " +
        "OSError OverflowError PendingDeprecationWarning ReferenceError ResourceWarning RuntimeError RuntimeWarning " +
        "StopIteration SyntaxError SyntaxWarning SystemError SystemExit TabError TypeError UnboundLocalError " +
        "UnicodeDecodeError UnicodeEncodeError UnicodeError UnicodeTranslateError UnicodeWarning UserWarning " +
        "ValueError VMSError Warning WindowsError ZeroDivisionError BlockingIOError ChildProcessError ConnectionError " +
        "BrokenPipeError ConnectionAbortedError ConnectionRefusedError ConnectionResetError FileExistsError " +
        "FileNotFoundError InterruptedError IsADirectoryError NotADirectoryError PermissionError ProcessLookupError " +
        "TimeoutError StopAsyncIteration ModuleNotFoundError RecursionError EncodingWarning"
)
private val PY_MAGIC_VARS = words(
    "__annotations__ __bases__ __class__ __closure__ __code__ __defaults__ __dict__ __doc__ __file__ __func__ " +
        "__globals__ __kwdefaults__ __module__ __mro__ __name__ __objclass__ __qualname__ __self__ __slots__ __weakref__"
)

private fun pythonWord(w: String): Tok = when {
    w in PY_OPERATOR_WORDS -> Tok.OPERATOR_WORD
    w in PY_CONSTANTS -> Tok.KEYWORD_CONSTANT
    w in PY_KEYWORDS -> Tok.KEYWORD
    w in PY_EXCEPTIONS -> Tok.EXCEPTION
    w in PY_BUILTINS -> Tok.BUILTIN
    w in PY_MAGIC_VARS -> Tok.VARIABLE
    w.length > 4 && w.startsWith("__") && w.endsWith("__") -> Tok.FUNCTION
    else -> Tok.NAME
}

private fun pythonLang(): Lang {
    val normal = Inner("($PY_ESCAPE)|$PY_FORMAT", listOf(Tok.ESCAPE, Tok.INTERPOL, Tok.INTERPOL))
    val raw = Inner("(\\\\[\\\\\"'\n])|$PY_FORMAT", listOf(Tok.ESCAPE, Tok.INTERPOL, Tok.INTERPOL))
    val fstr = Inner("($PY_ESCAPE)|(\\{[^{}\n]*\\})", listOf(Tok.ESCAPE, Tok.INTERPOL))
    fun str(prefix: String, inner: Inner) = Rule(
        NOT_IN_WORD + prefix + """(?:"{3}(?:[^"\\]++|\\[\s\S]|"(?!"{2}))*+(?:"{3}|\z)|'{3}(?:[^'\\]++|\\[\s\S]|'(?!'{2}))*+(?:'{3}|\z)""" +
            """|"(?:[^"\\\n]++|\\[\s\S])*+"?|'(?:[^'\\\n]++|\\[\s\S])*+'?)""",
        Tok.STRING, inner = inner,
    )
    return Lang(
        listOf(
            Rule("""(?m)^([ \t]*)((?i:[rub]{0,2}))("{3}[\s\S]*?(?:"{3}|\z)|'{3}[\s\S]*?(?:'{3}|\z))""",
                groups = listOf(null, Tok.DOC, Tok.DOC)),
            Rule("""\A#![^\n]*""", Tok.COMMENT),
            Rule("#[^\\n]*", Tok.COMMENT),
            str("(?i:rb|br|rf|fr|rt|tr|r)", raw),
            str("(?i:f|t)", fstr),
            str("(?i:b|u)?", normal),
            Rule("(def)(\\s+)($ID)", groups = listOf(Tok.KEYWORD, null, Tok.FUNCTION)),
            Rule("(class)(\\s+)($ID)", groups = listOf(Tok.KEYWORD, null, Tok.CLASS)),
            Rule("(from|import)(\\s+)(?!None\\b)([\\p{L}\\p{N}_.]+)", groups = listOf(Tok.KEYWORD, null, Tok.NAMESPACE)),
            Rule(
                NOT_IN_WORD + "(?:0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+|" +
                    "(?:[0-9][0-9_]*\\.?[0-9_]*|\\.[0-9][0-9_]*)(?:[eE][+-]?[0-9][0-9_]*)?j?)",
                Tok.NUMBER,
            ),
            Rule("@$ID(?:\\.$ID)*", Tok.DECORATOR),
            Rule(NOT_AFTER_DOT + ID, word = ::pythonWord),
            Rule(ID, Tok.NAME),
            Rule("!=|==|<<|>>|:=|[-~+/*%=<>&^|.@]", Tok.OPERATOR),
        )
    )
}

// ---------------------------------------------------------------------------------------------------------------
// Shell

private val SH_KEYWORDS = words("if fi else while in do done for then return function case select break continue until esac elif")
private val SH_BUILTINS = words(
    "alias bg bind builtin caller cd command compgen complete declare dirs disown echo enable eval exec exit export false " +
        "fc fg getopts hash help history jobs kill let local logout popd printf pushd pwd read readonly set shift shopt " +
        "source suspend test time times trap true type typeset ulimit umask unalias unset wait"
)

private fun shellLang(): Lang {
    val dq = Inner(
        """(\$\{#?[^}]*\})|(\$[a-zA-Z_]\w*|\$(?:\d+|[#$?!_*@-]))""",
        listOf(Tok.INTERPOL, Tok.VARIABLE),
    )
    val sq = Inner("""(\\(?:[0-7]+|[\s\S]))""", listOf(Tok.ESCAPE))
    return Lang(
        listOf(
            Rule("""\A#![^\n]*""", Tok.COMMENT),
            Rule("(?<![^\\s;&|(])#[^\\n]*", Tok.COMMENT),
            Rule("<<<", Tok.OPERATOR),
            Rule("""<<-?[ \t]*(['"]?)\\?(\w+)[\s\S]+?\2""", Tok.STRING),
            Rule("\"(?:[^\"\\\\]|\\\\[\\s\\S])*+\"?", Tok.STRING, inner = dq),
            Rule("\\$'(?:[^'\\\\]|\\\\[\\s\\S])*+'?", Tok.STRING, inner = sq),
            Rule("'[^']*+'?", Tok.STRING),
            Rule("`", Tok.STRING),
            Rule("\\\\[\\s\\S]", Tok.ESCAPE),
            Rule("\\$\\(\\(?", Tok.KEYWORD),
            Rule("(\\$\\{#?)(\\w*)([^}]*)(\\})", groups = listOf(Tok.INTERPOL, Tok.VARIABLE, null, Tok.INTERPOL)),
            Rule("\\$[a-zA-Z_]\\w*|\\$(?:\\d+|[#$?!_*@-])", Tok.VARIABLE),
            Rule("\\b(?:${SH_KEYWORDS.joinToString("|")})(?![\\w#$?!*@-])", Tok.KEYWORD),
            Rule("\\b(?:${SH_BUILTINS.joinToString("|")})(?=[\\s)`]|\\z)", Tok.BUILTIN),
            Rule("(\\w+)([ \\t]*)(\\+?=)", groups = listOf(Tok.VARIABLE, null, Tok.OPERATOR)),
            Rule("[\\[\\]{}()=]|&&|\\|\\|", Tok.OPERATOR),
            Rule("[0-9]+\\b", Tok.NUMBER),
            Rule("[^=\\s\\[\\]{}()\$\"'`\\\\<&|;]+"),  // (a plain word: it's one, so no keyword starts inside it)
        ),
        shell = true,
    )
}

// A shell command's grep and rg: which of their options take a value (the word after them, or the rest of the
// word), and so which word's the pattern
private val GREP_SHORT = mapOf("grep" to "ABCDdefm", "rg" to "ABCEMTdefgjmrt")
private val GREP_LONG = words(
    "--regexp --file --include --exclude --exclude-dir --max-count --context --after-context --before-context --label " +
        "--binary-files --devices --directories --glob --iglob --type --type-not --type-add --max-depth --replace " +
        "--encoding --max-columns --threads --sort --sortr --engine --max-filesize --pre --ignore-file"
)
private val RG_LONG = GREP_LONG + words(
    "--color --colors --context-separator --path-separator --field-match-separator --pre-glob --dfa-size-limit " +
        "--regex-size-limit"
)
private val GREP_DIALECT = mapOf("grep" to "bre", "zgrep" to "bre", "egrep" to "ere", "fgrep" to null, "rg" to "ere")
private val DIALECT_OPTIONS = mapOf(
    "--fixed-strings" to null, "--extended-regexp" to "ere", "--basic-regexp" to "bre", "--perl-regexp" to "ere",
    "--pcre2" to "ere", "--files" to null, "--type-list" to null,
)
private val SH_TOKEN: Pattern = Pattern.compile(
    "(?s)(&&|\\|\\||\\$\\(|[;&|()`\\n])|([ \\t]+|\\\\\\n)|#[^\\n]*" +
        "|((?:'[^']*+'?|\"(?:[^\"\\\\]|\\\\.)*+\"?|\\\\.|[^\\s'\"\\\\;&|()`])++)|."
)
private val SH_PREFIX = words("sudo xargs time nice env command exec then do else if elif while until ! {")

private class Word(val s: Int, val e: Int, val text: String)

/** Where the patterns of a shell command's grep, egrep, git grep and rg calls are: (start, end, bre), the text inside
 * a pattern's quotes. None for a fixed string (-F), a pattern read from a file (-f), or one the shell changes before
 * grep sees it (a $ or ` in double quotes, a backslash or $ in a bare word). */
private fun grepPatterns(cmd: String): List<Triple<Int, Int, Boolean>> {
    val words = ArrayList<Word?>()
    val m = SH_TOKEN.matcher(cmd)
    while (m.find()) {
        if (m.start(1) >= 0) words.add(null)
        else if (m.start(3) >= 0) words.add(Word(m.start(), m.end(), m.group()))
    }
    val out = ArrayList<Triple<Int, Int, Boolean>>()
    var atCmd = true
    var k = 0
    while (k < words.size) {
        val w = words[k]
        k++
        if (w == null) {
            atCmd = true
            continue
        }
        if (!atCmd) continue
        var name = w.text.substringAfterLast('/')
        if (name in SH_PREFIX || Regex("\\w+=.*", RegexOption.DOT_MATCHES_ALL).matches(w.text) || w.text.startsWith("-"))
            continue  // (still before the command: sudo, an option of xargs', VAR=value)
        atCmd = false
        if (name == "git" && k < words.size && words[k]?.text == "grep") {
            name = "grep"
            k++
        }
        if (name !in GREP_DIALECT) continue
        var end = k
        while (end < words.size && words[end] != null) end++
        out += grepArgs(cmd, if (name == "rg") "rg" else "grep", GREP_DIALECT[name], words.subList(k, end).map { it!! })
        k = end
    }
    return out
}

/** A grep's (or rg's) patterns, of its words: (start, end, bre). */
private fun grepArgs(cmd: String, tool: String, dialect0: String?, args: List<Word>): List<Triple<Int, Int, Boolean>> {
    var dialect = dialect0
    val given = ArrayList<IntArray?>()  // (-e's patterns: (start, end) in cmd; -f's: null)
    var first: IntArray? = null         // (the first word that's no option's)
    val takes = if (tool == "rg") RG_LONG else GREP_LONG
    var i = 0
    while (i < args.size) {
        val s = args[i].s
        val e = args[i].e
        val w = args[i].text
        i++
        if (w == "--") {
            if (first == null && i < args.size) first = intArrayOf(args[i].s, args[i].e)
            break
        }
        if (w.startsWith("--")) {
            val eq = w.indexOf('=')
            val opt = if (eq >= 0) w.substring(0, eq) else w
            if (opt in DIALECT_OPTIONS) dialect = DIALECT_OPTIONS[opt]
            val value = when {
                eq >= 0 -> intArrayOf(s + opt.length + 1, e)
                opt in takes && i < args.size -> intArrayOf(args[i].s, args[i].e)
                else -> null
            }
            if (opt in takes && eq < 0) i++
            if ((opt == "--regexp" || opt == "--file") && value != null) given.add(if (opt == "--regexp") value else null)
        } else if (w.startsWith("-") && w.length > 1) {
            for (j in 1 until w.length) {
                val ch = w[j]
                if (ch == 'F') dialect = null
                else if (ch in "EGP" && !(tool == "rg" && ch == 'E')) dialect = if (ch == 'G') "bre" else "ere"
                if (ch in GREP_SHORT.getValue(tool)) {
                    val value = when {
                        j + 1 < w.length -> intArrayOf(s + j + 1, e)
                        i < args.size -> intArrayOf(args[i].s, args[i].e)
                        else -> null
                    }
                    if (j + 1 == w.length) i++
                    if ((ch == 'e' || ch == 'f') && value != null) given.add(if (ch == 'e') value else null)
                    break
                }
            }
        } else if (first == null) {
            first = intArrayOf(s, e)
        }
    }
    if (dialect == null) return emptyList()
    val out = ArrayList<Triple<Int, Int, Boolean>>()
    val spans = if (given.isNotEmpty()) given else listOf(first)
    for (span in spans) {
        if (span == null) continue
        val inside = shellLiteral(cmd, span[0], span[1]) ?: continue
        out.add(Triple(inside.first, inside.second, dialect == "bre"))
    }
    return out
}

/** (start, end) of what a shell word (cmd[s:e]) is to the command it's given to, if that's the word's own text inside
 * its quotes, else null. */
private fun shellLiteral(cmd: String, s: Int, e: Int): Pair<Int, Int>? {
    val w = cmd.substring(s, e)
    if (w.length >= 2 && w[0] == '\'' && w.last() == '\'' && '\'' !in w.substring(1, w.length - 1)) return s + 1 to e - 1
    if (w.length >= 2 && w[0] == '"' && w.last() == '"' &&
        !Regex("[\$`\"]|\\\\[\"\\\\\$`]").containsMatchIn(w.substring(1, w.length - 1))
    ) return s + 1 to e - 1
    if (w.isNotEmpty() && !Regex("[\\\\\$'\"`]").containsMatchIn(w)) return s to e
    return null
}

/** A shell command's spans with its greps' patterns drawn as regexes, in place of the strings they're in. */
private fun withRegexes(text: String, spans: List<Span>): List<Span> {
    val cuts = grepPatterns(text).filter { it.first < it.second }
        .map { (a, b, bre) -> Triple(a, b, regexSpans(text.substring(a, b), bre)) }
    if (cuts.isEmpty()) return spans
    val starts = cuts.map { it.first }
    val out = ArrayList<Span>()
    for ((s, e, t) in spans) {  // (what of each is outside the patterns, their quotes say: the patterns are in order)
        var k = maxOf(bisectRight(starts, s) - 1, 0)
        var x = s
        while (k < cuts.size && cuts[k].first < e) {
            val a = cuts[k].first
            val b = cuts[k].second
            if (b > x) {
                if (a > x) out.add(Triple(x, a, t))
                x = b
            }
            k++
        }
        if (x < e) out.add(Triple(x, e, t))
    }
    for ((a, _, found) in cuts) for ((x, y, t) in found) out.add(Triple(a + x, a + y, t))
    return out.sortedBy { it.first }
}

private fun bisectRight(list: List<Int>, v: Int): Int {
    var lo = 0
    var hi = list.size
    while (lo < hi) {
        val mid = (lo + hi) / 2
        if (v < list[mid]) hi = mid else lo = mid + 1
    }
    return lo
}

// ---------------------------------------------------------------------------------------------------------------
// Regexes (Baude's own: Pygments has no lexer for one)

private val RE_ESCAPE: Pattern = Pattern.compile(
    "(?s)\\\\(?:x\\{[0-9A-Fa-f]*\\}|x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|[pPN]\\{[^}]*\\}|[pP][A-Za-z]|k<\\w+>|[0-9]+|.)"
)
private val RE_GROUP: Pattern = Pattern.compile(
    "\\((?:\\?(?:P?<\\w+>|'\\w+'|P=\\w+\\)|<[=!]|[:=!>|]|#[^)]*\\)|[a-zA-Z^-]+[:)]))?"
)
private val RE_COUNT: Pattern = Pattern.compile("\\{(?:[0-9]+(?:,[0-9]*)?|,[0-9]+)\\}")
private val RE_BRE_COUNT = Regex("[0-9]+(,[0-9]*)?|,[0-9]+")

private fun matchEnd(p: Pattern, text: String, at: Int): Int? {
    val m = p.matcher(text).region(at, text.length)
    return if (m.lookingAt()) m.end() else null
}

/** A regex's parts as code_spans' spans: escapes, character classes, groups and alternation, quantifiers, anchors and
 * the dot (what's left is literal: plain). bre: grep's basic syntax, where \( \) \| \{ \} \+ \? are the operators and
 * the bare ones literal. */
private fun regexSpans(text: String, bre: Boolean = false): List<Span> {
    val spans = ArrayList<Span>()
    var i = 0
    val n = text.length
    var unclosed = false  // (a "[" that never closed; none after it does)
    fun add(a: Int, b: Int, t: Tok) { spans.add(Triple(a, b, t)) }
    while (i < n) {
        val c = text[i]
        val ce = if (c == '[' && !unclosed) classEnd(text, i, bre) else null
        if (c == '\\' && i + 1 < n) {
            val d = text[i + 1]
            val braceEnd = if (bre && d == '{') text.indexOf("\\}", i + 2) else -1
            if (bre && (d == '(' || d == ')' || d == '|')) {
                add(i, i + 2, Tok.KEYWORD)
                i += 2
            } else if (bre && (d == '+' || d == '?')) {
                add(i, i + 2, Tok.FUNCTION)
                i += 2
            } else if (braceEnd > 0 && RE_BRE_COUNT.matches(text.substring(i + 2, braceEnd))) {
                add(i, braceEnd + 2, Tok.FUNCTION)
                i = braceEnd + 2
            } else {
                val end = matchEnd(RE_ESCAPE, text, i) ?: (i + 2)
                add(i, end, Tok.ESCAPE)
                i = end
            }
        } else if (ce != null) {
            var j = i
            var k = i + 1  // (escapes in it drawn as escapes, the rest the class's)
            while (k < ce - 1) {
                if (text[k] == '\\' && !bre && k + 1 < ce - 1) {
                    val e = minOf(matchEnd(RE_ESCAPE, text, k) ?: (k + 2), ce - 1)
                    if (k > j) add(j, k, Tok.STRING)
                    add(k, e, Tok.ESCAPE)
                    j = e
                    k = e
                } else k++
            }
            add(j, ce, Tok.STRING)
            i = ce
        } else if (!bre && c == '(') {
            val end = matchEnd(RE_GROUP, text, i) ?: (i + 1)
            add(i, end, Tok.KEYWORD)
            i = end
        } else if (!bre && (c == ')' || c == '|')) {
            add(i, i + 1, Tok.KEYWORD)
            i++
        } else if (c == '*' || !bre && (c == '+' || c == '?' || c == '{' && matchEnd(RE_COUNT, text, i) != null)) {
            var end = if (c == '{') matchEnd(RE_COUNT, text, i)!! else i + 1
            if (!bre && end < n && (text[end] == '?' || text[end] == '+')) end++  // (lazy, possessive)
            add(i, end, Tok.FUNCTION)
            i = end
        } else if (c == '^' || c == '$' || c == '.') {
            add(i, i + 1, Tok.ENTITY)
            i++
        } else {
            unclosed = unclosed || c == '['
            i++
        }
    }
    return spans
}

/** Where the character class opened at text[i] ("[") ends (just after its "]"), or null if it doesn't. */
private fun classEnd(text: String, i: Int, bre: Boolean): Int? {
    var j = i + 1
    val n = text.length
    if (j < n && text[j] == '^') j++
    if (j < n && text[j] == ']') j++  // (first, a "]" is one of its characters)
    while (j < n) {
        val k = if (text.startsWith("[:", j) || text.startsWith("[=", j) || text.startsWith("[.", j))
            text.indexOf("${text[j + 1]}]", j + 2) else -1
        if (text[j] == '\\' && !bre) j += 2
        else if (k > 0) j = k + 2  // ([:alpha:])
        else if (text[j] == ']') return j + 1
        else j++
    }
    return null
}

// ---------------------------------------------------------------------------------------------------------------
// C-like languages

private class CSpec(
    val kw: String,
    val types: String = "",
    val consts: String = "true false null",
    val builtins: String = "",
    val fnKw: String = "",
    val classKw: String = "",
    val importKw: String = "",
    val preproc: Boolean = false,
    val sq: Boolean = false,          // 'text' is a string (not just a character)
    val template: Boolean = false,    // `text ${x}`
    val dollar: Boolean = false,      // "$x ${x}" interpolation
    val swiftInterp: Boolean = false, // "\(x)"
    val triple: Boolean = false,      // """text"""
    val rawRust: Boolean = false,
    val goRaw: Boolean = false,
    val annot: Boolean = false,
    val rustAttr: Boolean = false,
    val regexLit: Boolean = false,
    val goReceiver: Boolean = false,
)

private fun cLikeLang(s: CSpec): Lang {
    val escape = """\\(?:[\\abfnrtv"'0$]|x[0-9a-fA-F]{2,4}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|u\{[0-9a-fA-F]+\}|[0-7]{1,3})"""
    val parts = ArrayList<String>()
    val toks = ArrayList<Tok>()
    parts.add("($escape)")
    toks.add(Tok.ESCAPE)
    if (s.dollar) { parts.add("""(\$\{[^}\n]*\}|\$[\p{L}_][\p{L}\p{N}_]*)"""); toks.add(Tok.INTERPOL) }
    if (s.template) { parts.add("""(\$\{[^}]*\})"""); toks.add(Tok.INTERPOL) }
    if (s.swiftInterp) { parts.add("""(\\\([^)\n]*\))"""); toks.add(Tok.INTERPOL) }
    val inner = Inner(parts.joinToString("|"), toks)
    val rules = ArrayList<Rule>()
    rules += Rule("//[^\\n]*", Tok.COMMENT)
    rules += Rule("/\\*[\\s\\S]*?(?:\\*/|\\z)", Tok.COMMENT)
    if (s.preproc) rules += Rule("(?m)^[ \\t]*#[ \\t]*[a-z]+[^\\n]*", Tok.PREPROC)
    if (s.rustAttr) rules += Rule("#!?\\[[^\\]\\n]*\\]", Tok.PREPROC)
    if (s.rawRust) rules += Rule("""$NOT_IN_WORD(?:b?r(#*)"[\s\S]*?(?:"\1|\z))""", Tok.STRING)
    if (s.triple) rules += Rule(
        if (s.sq) "\"{3}[\\s\\S]*?(?:\"{3}|\\z)|'{3}[\\s\\S]*?(?:'{3}|\\z)" else "\"{3}[\\s\\S]*?(?:\"{3}|\\z)",
        Tok.STRING, inner = inner,
    )
    rules += Rule(
        (if (s.rawRust) NOT_IN_WORD + "b?" else "") + "\"(?:[^\"\\\\\\n]++|\\\\.)*+\"?", Tok.STRING, inner = inner
    )
    if (s.template) rules += Rule("`(?:[^`\\\\]++|\\\\[\\s\\S])*+`?", Tok.STRING, inner = inner)
    if (s.goRaw) rules += Rule("`[^`]*`?", Tok.STRING)
    rules += Rule(
        if (s.sq) "'(?:[^'\\\\\\n]++|\\\\.)*+'?" else "'(?:\\\\[^'\\n]+|[^'\\\\\\n])'",
        Tok.STRING, inner = inner,
    )
    if (s.regexLit) rules += Rule(
        "(?m)(?:(?<=[=(,:;!&|?{}\\[])|(?<=[=(,:;!&|?{}\\[] )|(?<=return )|(?<=^))" +
            "/(?![/*])(?:[^/\\\\\\n\\[]|\\\\.|\\[(?:[^\\]\\\\\\n]|\\\\.)*\\])+/[dgimsuvy]*",
        Tok.REGEX,
    )
    if (s.annot) rules += Rule("@$ID(?:\\.$ID)*", Tok.DECORATOR)
    if (s.importKw.isNotEmpty())
        rules += Rule("\\b(${s.importKw.replace(' ', '|')})(\\s+)([\\p{L}_][\\p{L}\\p{N}_.*]*)",
            groups = listOf(Tok.KEYWORD, null, Tok.NAMESPACE))
    if (s.goReceiver)
        rules += Rule("\\b(func)(\\s*\\([^)\\n]*\\))(\\s+)($ID)", groups = listOf(Tok.KEYWORD, null, null, Tok.FUNCTION))
    if (s.fnKw.isNotEmpty())
        rules += Rule("\\b(${s.fnKw.replace(' ', '|')})(\\s+)(?:<[^>\\n]*>\\s*)?(?:$ID\\.)*($ID)",
            groups = listOf(Tok.KEYWORD, null, Tok.FUNCTION))
    if (s.classKw.isNotEmpty())
        rules += Rule("\\b(${s.classKw.replace(' ', '|')})(\\s+)(?!(?:class|struct|interface|enum)\\b)($ID)",
            groups = listOf(Tok.KEYWORD, null, Tok.CLASS))
    rules += Rule(
        "$NOT_IN_WORD(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|(?:[0-9][0-9_]*(?:\\.[0-9][0-9_]*)?|\\.[0-9][0-9_]*)" +
            "(?:[eE][+-]?[0-9]+)?)[uUlLfFdDnm]*",
        Tok.NUMBER,
    )
    rules += Rule(
        NOT_AFTER_DOT + ID,
        word = classify(
            Tok.KEYWORD_CONSTANT to words(s.consts), Tok.KEYWORD to words(s.kw),
            Tok.KEYWORD_TYPE to words(s.types), Tok.BUILTIN to words(s.builtins),
        ),
    )
    rules += Rule(ID, Tok.NAME)
    rules += Rule("[-+*/%=<>!&|^~?]+", Tok.OPERATOR)
    return Lang(rules)
}

private val C_SPEC = CSpec(
    kw = "auto break case const continue default do else enum extern for goto if inline register restrict return sizeof " +
        "static struct switch typedef union volatile while",
    types = "int long float short double char unsigned signed void bool _Bool size_t ssize_t uint8_t uint16_t uint32_t " +
        "uint64_t int8_t int16_t int32_t int64_t FILE",
    consts = "true false NULL", classKw = "struct union enum", preproc = true,
)
private val CPP_SPEC = CSpec(
    kw = "alignas auto break case catch class const constexpr continue default delete do else enum explicit export " +
        "extern final for friend goto if inline mutable namespace new noexcept operator override private protected " +
        "public register return sizeof static static_cast dynamic_cast reinterpret_cast const_cast struct switch " +
        "template this throw try typedef typename union using virtual volatile while co_await co_return co_yield decltype",
    types = "int long float short double char unsigned signed void bool wchar_t size_t string vector map",
    consts = "true false NULL nullptr", classKw = "class struct union enum namespace", preproc = true,
)
private val JAVA_SPEC = CSpec(
    kw = "abstract assert break case catch class const continue default do else enum extends final finally for goto if " +
        "implements import instanceof interface native new package private protected public return static strictfp " +
        "super switch synchronized this throw throws transient try volatile while var record sealed permits yield",
    types = "boolean byte char double float int long short void",
    classKw = "class interface enum record", importKw = "import package", annot = true, triple = true,
)
private val KOTLIN_SPEC = CSpec(
    kw = "as break by catch class companion constructor continue crossinline data do else enum external finally for fun " +
        "get if import in infix init inline inner interface internal is lateinit noinline object open operator out " +
        "override package private protected public reified return sealed set super suspend tailrec this throw try " +
        "typealias typeof val var vararg when where while abstract annotation const final",
    types = "Int Long Short Byte Float Double Boolean Char String Unit Any Nothing Array List Map Set",
    fnKw = "fun", classKw = "class interface object", importKw = "import package",
    annot = true, dollar = true, triple = true,
)
private val JS_KW = "async await break case catch class const continue debugger default delete do else export extends " +
    "finally for function if import in instanceof let new of return super switch this throw try typeof var void while " +
    "with yield"
private val JS_SPEC = CSpec(
    kw = JS_KW, consts = "true false null undefined NaN Infinity",
    builtins = "console window document Math JSON Object Array Promise String Number Boolean Date RegExp Map Set Symbol " +
        "Error require module exports process",
    fnKw = "function", classKw = "class", sq = true, template = true, regexLit = true,
)
private val TS_SPEC = CSpec(
    kw = "$JS_KW abstract as declare enum implements interface keyof namespace private protected public readonly type " +
        "is infer satisfies override module",
    types = "any boolean never number object string symbol unknown void bigint",
    consts = "true false null undefined NaN Infinity",
    builtins = JS_SPEC.builtins, fnKw = "function", classKw = "class interface enum type namespace",
    sq = true, template = true, regexLit = true, annot = true,
)
private val GO_SPEC = CSpec(
    kw = "break case chan const continue default defer else fallthrough for func go goto if import interface map package " +
        "range return select struct switch type var",
    types = "bool byte complex64 complex128 error float32 float64 int int8 int16 int32 int64 rune string uint uint8 " +
        "uint16 uint32 uint64 uintptr any",
    consts = "true false nil iota",
    builtins = "append cap close complex copy delete imag len make new panic print println real recover",
    fnKw = "func", classKw = "type", importKw = "package", goRaw = true, goReceiver = true,
)
private val RUST_SPEC = CSpec(
    kw = "as async await break const continue crate dyn else enum extern fn for if impl in let loop match mod move mut " +
        "pub ref return self Self static struct super trait type unsafe use where while union",
    types = "bool char f32 f64 i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize str String Vec Option Result Box",
    consts = "true false", builtins = "None Some Ok Err",
    fnKw = "fn", classKw = "struct enum trait union type impl", rawRust = true, rustAttr = true,
)
private val SWIFT_SPEC = CSpec(
    kw = "associatedtype class deinit enum extension fileprivate func import init inout internal let open operator " +
        "private protocol public rethrows static struct subscript typealias var break case continue default defer do " +
        "else fallthrough for guard if in repeat return switch where while as catch is super throw throws try async " +
        "await actor some any self Self mutating nonmutating lazy override final required convenience weak unowned",
    types = "Int Int8 Int16 Int32 Int64 UInt Float Double Bool String Character Array Dictionary Set Optional Any " +
        "AnyObject Void",
    consts = "true false nil", fnKw = "func", classKw = "class struct enum protocol extension actor",
    importKw = "import", annot = true, swiftInterp = true, triple = true,
)
private val CSHARP_SPEC = CSpec(
    kw = "abstract as base break case catch checked class const continue default delegate do else enum event explicit " +
        "extern finally fixed for foreach goto if implicit in interface internal is lock namespace new operator out " +
        "override params private protected public readonly ref return sealed sizeof stackalloc static struct switch " +
        "this throw try typeof unchecked unsafe using virtual volatile while var async await record yield",
    types = "bool byte char decimal double float int long object sbyte short string uint ulong ushort void dynamic",
    classKw = "class struct interface enum record", importKw = "using namespace", preproc = true, triple = true,
)
private val DART_SPEC = CSpec(
    kw = "abstract as assert async await break case catch class const continue covariant default deferred do dynamic " +
        "else enum export extends extension external factory final finally for get hide if implements import in " +
        "interface is late library mixin new on operator part required rethrow return set show static super switch " +
        "sync this throw try typedef var while with yield",
    types = "int double num bool String void List Map Set Object Future Stream",
    classKw = "class mixin enum extension", importKw = "library", sq = true, dollar = true, annot = true, triple = true,
)
private val SCALA_SPEC = CSpec(
    kw = "abstract case catch class def do else extends final finally for forSome if implicit import lazy match new " +
        "object override package private protected return sealed super this throw trait try type val var while with " +
        "yield given using enum export then",
    types = "Int Long Short Byte Float Double Boolean Char String Unit Any AnyRef AnyVal Nothing Null List Map Set " +
        "Option Seq Array",
    fnKw = "def", classKw = "class object trait enum", importKw = "import package",
    annot = true, triple = true, dollar = true,
)

// ---------------------------------------------------------------------------------------------------------------
// Data and markup languages

private fun jsonLang() = Lang(
    listOf(
        Rule("//[^\\n]*", Tok.COMMENT),
        Rule("/\\*[\\s\\S]*?(?:\\*/|\\z)", Tok.COMMENT),
        Rule("\"(?:[^\"\\\\\\n]++|\\\\.)*+\"(?=\\s*:)", Tok.TAG),
        Rule("\"(?:[^\"\\\\\\n]++|\\\\.)*+\"?", Tok.STRING),
        Rule("-?[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", Tok.NUMBER),
        Rule("\\b(?:true|false|null)\\b", Tok.KEYWORD_CONSTANT),
    )
)

private fun yamlLang() = Lang(
    listOf(
        Rule("(?m)^(?:---|\\.\\.\\.)(?=\\s|\\z)", Tok.NAMESPACE),
        Rule("(?<![^\\s])#[^\\n]*", Tok.COMMENT),
        Rule(
            "(?m)^([ \\t]*(?:-[ \\t]+)*)(\"(?:[^\"\\\\\\n]|\\\\.)*\"|'[^'\\n]*'|[^\\s#:'\"\\-?!&*|>%@`\\[\\]{},][^#:\\n]*?" +
                "|-[^\\s#:][^#:\\n]*?)([ \\t]*)(:)(?=[ \\t]|\\n|\\z)",
            groups = listOf(null, Tok.TAG, null, null),
        ),
        Rule("\"(?:[^\"\\\\\\n]|\\\\.)*+\"?", Tok.STRING, inner = Inner("(\\\\.)", listOf(Tok.ESCAPE))),
        Rule("'(?:[^'\\n]|'')*+'?", Tok.STRING),
        Rule("[&*][\\p{L}\\p{N}_-]+", Tok.VARIABLE),
        Rule("!{1,2}[\\w<>:./-]*", Tok.KEYWORD_TYPE),
        Rule("(?<![\\p{L}\\p{N}_.-])(?:true|false|null|True|False|Null|NULL|TRUE|FALSE|~)(?![\\p{L}\\p{N}_.-])",
            Tok.KEYWORD_CONSTANT),
        Rule("(?<![\\p{L}\\p{N}_.-])[-+]?(?:0x[0-9a-fA-F]+|0o[0-7]+|[0-9][0-9_]*(?:\\.[0-9]+)?(?:[eE][-+]?[0-9]+)?)" +
            "(?![\\p{L}\\p{N}_.-])", Tok.NUMBER),
    )
)

private fun tomlLang(ini: Boolean) = Lang(
    listOf(
        Rule(if (ini) "(?m)^[ \\t]*[#;][^\\n]*|(?<=[ \\t])[#;][^\\n]*" else "#[^\\n]*", Tok.COMMENT),
        Rule("(?m)^[ \\t]*\\[\\[?[^\\]\\n]*\\]\\]?", Tok.KEYWORD),
        Rule("(?m)^[ \\t]*([\\p{L}\\p{N}_.\\-]+|\"[^\"\\n]*\"|'[^'\\n]*')([ \\t]*)(${if (ini) "[=:]" else "="})",
            groups = listOf(Tok.ATTRIBUTE, null, Tok.OPERATOR)),
        Rule("\"{3}[\\s\\S]*?(?:\"{3}|\\z)|'{3}[\\s\\S]*?(?:'{3}|\\z)", Tok.STRING),
        Rule("\"(?:[^\"\\\\\\n]|\\\\.)*+\"?", Tok.STRING, inner = Inner("(\\\\.)", listOf(Tok.ESCAPE))),
        Rule("'[^'\\n]*'?", Tok.STRING),
        Rule("(?<![\\p{L}\\p{N}_.-])[-+]?(?:0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+|[0-9][0-9_]*(?:\\.[0-9_]+)?" +
            "(?:[eE][-+]?[0-9_]+)?)(?![\\p{L}\\p{N}_.-])", Tok.NUMBER),
        Rule("(?<![\\p{L}\\p{N}_.-])(?:true|false)(?![\\p{L}\\p{N}_.-])", Tok.KEYWORD_CONSTANT),
    )
)

private val HTML_ATTR: Pattern = Pattern.compile("([^\\s=<>\"'/]+)|(\"[^\"]*\"?|'[^']*'?)|(=)")

private fun htmlLang() = Lang(
    listOf(
        Rule("<!--[\\s\\S]*?(?:-->|\\z)", Tok.COMMENT),
        Rule("<!\\[CDATA\\[[\\s\\S]*?(?:\\]\\]>|\\z)", Tok.STRING),
        Rule("<![^>]*>?", Tok.PREPROC),
        Rule("<\\?[\\s\\S]*?(?:\\?>|\\z)", Tok.PREPROC),
        Rule("</?[\\p{L}][\\p{L}\\p{N}:._-]*(?:\"[^\"]*\"?|'[^']*'?|[^'\">])*+>?", custom = { m, emit ->
            val g = m.group()
            val s = m.start()
            val i = if (g.startsWith("</")) 2 else 1
            var j = i
            while (j < g.length && !g[j].isWhitespace() && g[j] != '/' && g[j] != '>') j++
            emit(s + i, s + j, Tok.TAG)
            val am = HTML_ATTR.matcher(g)
            am.region(j, g.length)
            while (am.find()) {
                when {
                    am.start(1) >= 0 -> emit(s + am.start(1), s + am.end(1), Tok.ATTRIBUTE)
                    am.start(2) >= 0 -> emit(s + am.start(2), s + am.end(2), Tok.STRING)
                    else -> emit(s + am.start(3), s + am.end(3), Tok.OPERATOR)
                }
            }
        }),
        Rule("&#?[\\p{L}\\p{N}]+;", Tok.ENTITY),
    )
)

private fun cssLang(scss: Boolean) = Lang(
    listOfNotNull(
        Rule("/\\*[\\s\\S]*?(?:\\*/|\\z)", Tok.COMMENT),
        if (scss) Rule("(?<!:)//[^\\n]*", Tok.COMMENT) else null,
        Rule("\"(?:[^\"\\\\\\n]|\\\\.)*+\"?|'(?:[^'\\\\\\n]|\\\\.)*+'?", Tok.STRING),
        Rule("@[\\w-]+", Tok.KEYWORD),
        Rule("!important\\b", Tok.PREPROC),
        Rule("([\\w-]+)([ \\t]*:)(?=[^{};\\n]*(?:;|\\}|\\n|\\z))", groups = listOf(Tok.BUILTIN, null)),
        Rule("#[0-9a-fA-F]{8}\\b|#[0-9a-fA-F]{6}\\b|#[0-9a-fA-F]{3,4}\\b", Tok.NUMBER),
        Rule("\\.[\\p{L}_-][\\w-]*", Tok.CLASS),
        Rule("#[\\p{L}_-][\\w-]*", Tok.NAMESPACE),
        Rule("::?[\\p{L}_-][\\w-]*", Tok.DECORATOR),
        Rule("[\\w-]+(?=\\()", Tok.BUILTIN),
        Rule("-?(?:[0-9]+\\.?[0-9]*|\\.[0-9]+)(?:%|[a-zA-Z]+)?", Tok.NUMBER),
    )
)

private val SQL_KEYWORDS = words(
    "select from where group by order having limit offset insert into values update set delete create alter drop table " +
        "index view database schema join inner left right full outer cross on using as distinct union all except " +
        "intersect case when then else end exists primary key foreign references default constraint unique check " +
        "begin commit rollback transaction with recursive returning asc desc if replace truncate grant revoke " +
        "add column rename to over partition window fetch next only cascade temporary temp explain analyze vacuum " +
        "pragma trigger function procedure return declare cursor for while loop do"
)
private val SQL_OPERATOR_WORDS = words("and or not in is like ilike between glob similar any some")
private val SQL_TYPES = words(
    "int integer bigint smallint tinyint serial bigserial real double precision float decimal numeric boolean bool " +
        "char varchar text date time timestamp timestamptz datetime interval blob bytea json jsonb uuid money"
)
private val SQL_FUNCTIONS = words(
    "count sum avg min max coalesce nullif now cast convert length lower upper trim substr substring replace concat " +
        "round floor ceil abs extract date_trunc row_number rank dense_rank lag lead ifnull isnull"
)

private fun sqlLang() = Lang(
    listOf(
        Rule("--[^\\n]*", Tok.COMMENT),
        Rule("/\\*[\\s\\S]*?(?:\\*/|\\z)", Tok.COMMENT),
        Rule("'(?:[^']|'')*+'?", Tok.STRING),
        Rule("\"[^\"\\n]*\"?|`[^`\\n]*`?", Tok.STRING),
        Rule("$NOT_IN_WORD[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", Tok.NUMBER),
        Rule(
            NOT_AFTER_DOT + ID,
            word = classify(
                Tok.OPERATOR_WORD to SQL_OPERATOR_WORDS, Tok.KEYWORD_CONSTANT to words("null true false"),
                Tok.KEYWORD to SQL_KEYWORDS, Tok.KEYWORD_TYPE to SQL_TYPES, Tok.BUILTIN to SQL_FUNCTIONS,
                ci = true, other = null,
            ),
        ),
        Rule("[-+*/%=<>!|&^~]+", Tok.OPERATOR),
    )
)

private fun diffLang() = Lang(
    listOf(
        Rule("(?m)^\\+[^\\n]*", Tok.INSERTED),
        Rule("(?m)^-[^\\n]*", Tok.DELETED),
        Rule("(?m)^@[^\\n]*", Tok.HEADING),
        Rule("(?m)^(?:[Ii]ndex|diff)[^\\n]*", Tok.HEADING),
        Rule("(?m)^=[^\\n]*", Tok.HEADING),
    )
)

private fun markdownLang() = Lang(
    listOf(
        Rule("(?m)^```[\\s\\S]*?(?:^```[^\\n]*|\\z)", Tok.STRING),
        Rule("(?m)^#{1,6}[^\\n]*", Tok.HEADING),
        Rule("``[^\\n]+?``|`[^`\\n]+`", Tok.STRING),
    )
)

// ---------------------------------------------------------------------------------------------------------------
// Which lexer

private class Registry {
    val byName = HashMap<String, Lang>()
    val byExt = HashMap<String, Lang>()
    fun put(lang: Lang, names: String, exts: String) {
        for (n in names.split(' ').filter { it.isNotEmpty() }) byName[n] = lang
        for (x in exts.split(' ').filter { it.isNotEmpty() }) byExt[x] = lang
    }
}

private val registry: Registry by lazy {
    Registry().apply {
        put(pythonLang(), "py python python3 pyi", "py pyi pyw")
        put(shellLang(), "bash sh shell zsh ksh", "sh bash zsh ksh bashrc zshrc profile bash_profile zprofile")
        put(cLikeLang(C_SPEC), "c h", "c h")
        put(cLikeLang(CPP_SPEC), "cpp c++ cc cxx hpp hxx", "cpp cc cxx hpp hh hxx")
        put(cLikeLang(JAVA_SPEC), "java", "java")
        put(cLikeLang(KOTLIN_SPEC), "kotlin kt kts", "kt kts")
        put(cLikeLang(JS_SPEC), "js javascript mjs cjs jsx", "js mjs cjs jsx")
        put(cLikeLang(TS_SPEC), "ts typescript tsx", "ts tsx")
        put(cLikeLang(GO_SPEC), "go golang", "go")
        put(cLikeLang(RUST_SPEC), "rust rs", "rs")
        put(cLikeLang(SWIFT_SPEC), "swift", "swift")
        put(cLikeLang(CSHARP_SPEC), "cs csharp c#", "cs")
        put(cLikeLang(DART_SPEC), "dart", "dart")
        put(cLikeLang(SCALA_SPEC), "scala", "scala sc")
        put(jsonLang(), "json jsonc json5", "json jsonc json5")
        put(yamlLang(), "yaml yml", "yaml yml")
        put(tomlLang(false), "toml", "toml")
        put(tomlLang(true), "ini cfg properties", "ini cfg conf properties editorconfig")
        put(htmlLang(), "html xml xhtml svg", "html htm xhtml xml svg xsd xsl plist")
        put(cssLang(false), "css", "css")
        put(cssLang(true), "scss less", "scss less")
        put(sqlLang(), "sql", "sql")
        put(diffLang(), "diff patch udiff", "diff patch")
        put(markdownLang(), "md markdown", "md markdown")
    }
}

private fun langFor(path: String, lang: String): Lang? {
    if (lang.isNotEmpty()) return registry.byName[lang.lowercase()]
    val file = path.substringAfterLast('/').substringAfterLast('\\')
    if (file.isEmpty() || '.' !in file && file.lowercase() !in registry.byExt) return null
    return registry.byExt[file.substringAfterLast('.').lowercase()]
}

// ---------------------------------------------------------------------------------------------------------------
// The API

/** What of a block's text is highlighted: its first CODE_HIGHLIGHT_MAX characters, to a line's end (a carriage return a
 * space, as the offsets after it would be off by one otherwise). */
private fun highlightPart(text: String): String {
    var t = text
    if (t.length > CODE_HIGHLIGHT_MAX) t = t.substring(0, maxOf(t.lastIndexOf('\n', CODE_HIGHLIGHT_MAX - 1), 0))
    return t.replace('\r', ' ')
}

/** Token spans for text: (start, end, token) in character offsets, in order, by [lang] (a language's name or alias:
 * bash, py, a code fence's tag; regex: a regex's parts) or else [path]'s file name. Empty for a language there's no
 * lexer for. In a shell command a grep's or rg's pattern is a regex. */
fun codeSpans(text: String, path: String = "", lang: String = ""): List<Triple<Int, Int, Tok>> {
    val part = highlightPart(text)
    if (lang.lowercase() == "regex") return regexSpans(part)
    val l = langFor(path, lang) ?: return emptyList()
    val spans = lex(part, l.rules)
    return if (l.shell) withRegexes(part, spans) else spans
}

private val READ_NUMBER = Regex(" *[0-9]+(?:→|\t)")  // Claude Code's Read: "   186→    text" (or a tab, as cat -n has it)

/** A Read's output highlighted as path's code: each line its number (dim: LINE_NUMBER), then the file's line. A line
 * that has no number (a note after the file) isn't code: it's left plain. */
fun numberedSpans(text: String, path: String): List<Triple<Int, Int, Tok>> {
    val cuts = ArrayList<Pair<Int, Int?>>()
    val code = ArrayList<String>()
    var at = 0
    for (line in text.split("\n")) {
        val m = READ_NUMBER.find(line)?.takeIf { it.range.first == 0 }
        cuts.add(at to m?.range?.last?.plus(1))
        code.add(if (m != null) line.substring(m.range.last + 1) else "")
        at += line.length + 1
    }
    val src = code.joinToString("\n")
    val perLine = lineSpans(src, codeSpans(src, path))
    val out = ArrayList<Span>()
    for ((idx, cutPair) in cuts.withIndex()) {
        val (lineAt, cut) = cutPair
        if (cut == null) continue
        out.add(Triple(lineAt, lineAt + cut, Tok.LINE_NUMBER))
        for ((s, e, t) in perLine[idx]) out.add(Triple(lineAt + cut + s, lineAt + cut + e, t))
    }
    return out
}

/** spans (in text) cut at its line ends: for each line, its (start, end, token) from the line's start. */
fun lineSpans(text: String, spans: List<Triple<Int, Int, Tok>>): List<List<Triple<Int, Int, Tok>>> {
    val starts = ArrayList<Int>()
    starts.add(0)
    for (i in text.indices) if (text[i] == '\n') starts.add(i + 1)
    val out = List(starts.size) { ArrayList<Span>() }
    for ((s0, e, t) in spans) {
        var s = s0
        var i = bisectRight(starts, s) - 1
        while (i < starts.size && s < e) {
            val stop = minOf(e, if (i + 1 < starts.size) starts[i + 1] - 1 else text.length)
            if (s < stop) out[i].add(Triple(s - starts[i], stop - starts[i], t))
            i++
            s = if (i < starts.size) maxOf(s, starts[i]) else e
        }
    }
    return out
}

// What's bold or italic, by theme (the desktop's Pygments styles: default in the light theme, github-dark in the dark)
private val LIGHT_BOLD = setOf(
    Tok.KEYWORD, Tok.KEYWORD_CONSTANT, Tok.CLASS, Tok.NAMESPACE, Tok.TAG, Tok.EXCEPTION, Tok.ENTITY, Tok.ESCAPE,
    Tok.INTERPOL, Tok.OPERATOR_WORD, Tok.HEADING,
)
private val LIGHT_ITALIC = setOf(Tok.DOC, Tok.COMMENT)
private val DARK_BOLD = setOf(
    Tok.FUNCTION, Tok.CLASS, Tok.DECORATOR, Tok.CONSTANT, Tok.EXCEPTION, Tok.PREPROC, Tok.OPERATOR, Tok.OPERATOR_WORD,
    Tok.HEADING,
)
private val DARK_ITALIC = setOf(Tok.COMMENT, Tok.PREPROC)

private fun colorOf(t: Tok, s: ThemeTokens.Syntax, c: BaudeColors): Color = when (t) {
    Tok.KEYWORD -> s.keyword
    Tok.KEYWORD_CONSTANT -> s.keywordConstant
    Tok.KEYWORD_TYPE -> s.keywordType
    Tok.BUILTIN -> s.builtin
    Tok.FUNCTION -> s.function
    Tok.CLASS -> s.className
    Tok.DECORATOR -> s.decorator
    Tok.NAMESPACE -> s.namespace
    Tok.VARIABLE -> s.variable
    Tok.CONSTANT -> s.constant
    Tok.TAG -> s.tag
    Tok.ATTRIBUTE -> s.attribute
    Tok.EXCEPTION -> s.exception
    Tok.ENTITY -> s.entity
    Tok.STRING -> s.string
    Tok.ESCAPE -> s.escape
    Tok.INTERPOL -> s.interpol
    Tok.DOC -> s.doc
    Tok.REGEX -> s.regex
    Tok.NUMBER -> s.number
    Tok.COMMENT -> s.comment
    Tok.PREPROC -> s.preproc
    Tok.OPERATOR -> s.operator
    Tok.OPERATOR_WORD -> s.operatorWord
    Tok.INSERTED -> s.inserted
    Tok.DELETED -> s.deleted
    Tok.HEADING -> s.heading
    Tok.NAME -> s.name
    Tok.LINE_NUMBER -> c.fg.copy(alpha = 0x7000 / 0xffff.toFloat())
}

/** text with each span in its token's colour (and weight and slant) in the theme's syntax colours. */
fun highlighted(text: String, spans: List<Triple<Int, Int, Tok>>, c: BaudeColors, dark: Boolean): AnnotatedString {
    val syntax = if (dark) Tokens.dark.syntax else Tokens.light.syntax
    val bold = if (dark) DARK_BOLD else LIGHT_BOLD
    val italic = if (dark) DARK_ITALIC else LIGHT_ITALIC
    return buildAnnotatedString {
        append(text)
        for ((s, e, t) in spans) {
            val end = minOf(e, text.length)
            if (s < 0 || s >= end) continue
            addStyle(
                SpanStyle(
                    color = colorOf(t, syntax, c),
                    fontWeight = if (t in bold) FontWeight.Bold else null,
                    fontStyle = if (t in italic) FontStyle.Italic else null,
                ),
                s, end,
            )
        }
    }
}
