#!/usr/bin/env python3
"""The nouns a chat's replies take as known that nothing introduced: "the platform", "the current job", when no
"a platform" or "a job" is said anywhere in it. This is conversational-lab-articles/noun-tracking's NLTK version
(track.py: the strict rule with the situation list). Each sentence's noun phrases come from NLTK's tokenizer,
tagger and chunker. A head noun (WordNet's lemma) is introduced by "a" or "an" before it anywhere in the chat, or is
one of user, conversation and chat. Every "the" phrase whose head isn't introduced is flagged, unless it names itself:
a code name (with _ in it) or a capitalised name. Your notes introduce every noun in them, whatever comes before
it: what you've written down, you know.

Record keeps it running in uv's environment with NLTK (record.py: Unexplained), one request a line on stdin and
one answer a line on stdout:
    {"texts": [{"id": ..., "text": markdown, "mark": true}, ...], "known": [markdown, ...]}
    {"flagged": {id: ["the platform", ...]}}      (or {"error": "..."})
NLTK's data (the sentence splitter's, the tagger's, WordNet) is fetched once into $RECORD_NLTK_DATA. For a look at
what it finds in some files' text: python3 nouns.py FILE...
"""
import contextlib
import hashlib
import json
import os
import re
import sys

SITUATION = {"user", "conversation", "chat"}
NAMED = re.compile(r"_|^[A-Z]")
GRAMMAR = r"NP: {<DT|PRP\$>?<JJ.*|VBN|VBG|NN.*|CD|POS>*<NN.*>}"
DATA = (("tokenizers/punkt_tab/english/", "punkt_tab"),
        ("taggers/averaged_perceptron_tagger_eng/", "averaged_perceptron_tagger_eng"),
        ("corpora/wordnet", "wordnet"))

_FENCE = re.compile(r"^\s*(```|~~~)")
_RULE = re.compile(r"([-*_])(\s*\1){2,}")
_TABLE_RULE = re.compile(r":?-{2,}:?")
_BLOCK = re.compile(r"(#{1,6}\s+|(?:[-*+•◦]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)(.*)")
_INLINE = ((re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"),  # (a link or an image: its text)
           (re.compile(r"<https?://[^>]*>|https?://\S+|<[^>]+>"), " "),  # (addresses and HTML: nothing)
           (re.compile(r"`([^`]*)`"), r"\1"),  # (code spans: what they say)
           (re.compile(r"\*\*|__|~~|=="), ""),
           (re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])"), r"\1"),
           (re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"), r"\1"))


def prose_blocks(md):
    """md's prose a block at a time (a paragraph, a list item, a heading, a quote, a table's cell), without its
    markup: code blocks left out, a link's address, emphasis, a heading's #s and a list's marker taken away. Kept
    apart, so no noun phrase runs from one block into the next."""
    out, para, fenced = [], [], False

    def end():
        if para:
            out.append(" ".join(para))
            para.clear()
    for line in md.split("\n"):
        if _FENCE.match(line):
            end()
            fenced = not fenced
            continue
        s = line.strip()
        if fenced:
            continue
        if not s or _RULE.fullmatch(s):
            end()
        elif s.startswith("|"):
            end()
            out.extend(c.strip() for c in s.strip("|").split("|") if c.strip() and not _TABLE_RULE.fullmatch(c.strip()))
        elif m := _BLOCK.match(s):
            end()
            out.append(m.group(2))
        else:
            para.append(re.sub(r"^>\s?", "", s))
    end()
    blocks = []
    for b in out:
        for pattern, to in _INLINE:
            b = pattern.sub(to, b)
        if b.strip():
            blocks.append(b.strip())
    return blocks


def _spans(text, tokens):
    """Where each of word_tokenize's tokens is in text (None: not found), in order; it writes " as `` and ''."""
    out, at = [], 0
    for tok in tokens:
        for want in ('"', "“", "”", tok) if tok in ("``", "''") else (tok,):
            i = text.find(want, at)
            if i != -1 and not any(c.isalnum() for c in text[at:i]):
                out.append((i, i + len(want)))
                at = i + len(want)
                break
        else:
            out.append(None)
    return out


class Tracker:
    """NLTK's pieces, loaded once; a text's noun phrases kept by its hash (a chat's messages, read again as it grows,
    are tagged once)."""

    def __init__(self):
        import nltk
        from nltk.stem import WordNetLemmatizer
        here = os.environ.get("RECORD_NLTK_DATA")
        if here:
            os.makedirs(here, exist_ok=True)
            nltk.data.path.insert(0, here)
        for resource, package in DATA:
            try:
                nltk.data.find(resource)
            except LookupError:
                with contextlib.redirect_stdout(sys.stderr):  # (stdout is the answers')
                    if not nltk.download(package, download_dir=here or None, quiet=True, raise_on_error=True):
                        raise LookupError(f"NLTK's {package} couldn't be fetched")
        self.nltk, self.chunker, self.lemma = nltk, nltk.RegexpParser(GRAMMAR), WordNetLemmatizer().lemmatize
        self.cache = {}

    def phrases(self, md):
        """md's noun phrases: (determiner, words, head lemma, every noun's lemma, the phrase as md's prose has it)."""
        key = hashlib.sha1(md.encode("utf-8", "replace")).hexdigest()
        if (got := self.cache.get(key)) is not None:
            return got
        nltk, out = self.nltk, []
        for block in prose_blocks(md):
            for sentence in nltk.sent_tokenize(block):
                tokens = nltk.word_tokenize(sentence)
                where, k = _spans(sentence, tokens), 0
                for child in self.chunker.parse(nltk.pos_tag(tokens)):
                    if not isinstance(child, nltk.Tree):
                        k += 1
                        continue
                    leaves = child.leaves()
                    first, last = where[k], where[k + len(leaves) - 1]
                    k += len(leaves)
                    nouns = [self.lemma(w.lower()) for w, t in leaves if t.startswith("NN")]
                    det = leaves[0][0].lower() if leaves[0][1] in ("DT", "PRP$") else ""
                    out.append((det, [w for w, _ in leaves], nouns[-1], set(nouns),
                                sentence[first[0]:last[1]] if first and last else None))
        self.cache[key] = out
        return out

    def flag(self, texts, known=()):
        """{id: the "the" phrases of each text to mark whose head nothing introduced}: introduced by "a"/"an" in any
        of texts, or any noun in known (your notes)."""
        introduced = set(SITUATION)
        for md in known:
            for _det, _words, _head, nouns, _said in self.phrases(md):
                introduced |= nouns
        parsed = [(t, self.phrases(t["text"])) for t in texts]
        for _t, phrases in parsed:
            introduced |= {head for det, _w, head, _n, _s in phrases if det in ("a", "an")}
        flagged = {}
        for t, phrases in parsed:
            if not t.get("mark", True):
                continue
            said = [s for det, words, head, _n, s in phrases if det == "the" and head not in introduced and s
                    and not any(NAMED.search(w) for w in words[1:])]
            if said:
                flagged[t["id"]] = sorted(set(said), key=said.index)
        return flagged


def serve():
    tracker = None
    for line in sys.stdin:
        try:
            ask = json.loads(line)
            tracker = tracker or Tracker()
            answer = {"flagged": tracker.flag(ask["texts"], ask.get("known") or ())}
        except Exception as e:  # (said, and on to the next)
            answer = {"error": f"{type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps(answer) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    if sys.argv[1:]:
        texts = [{"id": p, "text": open(p, encoding="utf-8", errors="replace").read()} for p in sys.argv[1:]]
        for p, said in Tracker().flag(texts).items():
            print(p, *said, sep="\n  ")
    else:
        serve()
