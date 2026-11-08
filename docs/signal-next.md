# Baude Signal: where the work stands

Notes for picking the work up again: what's open, what was found so far, and how to measure it again.

## The state of this branch

- **Baude Signal** is the feature that turns the names in Claude's replies into links. It was called
  Referencer, and old settings keys and `.baude/referencer.db` are still read and renamed. The link icon is
  unchanged.
- **The reader** is one Claude Code session per chat, Sonnet by default. It reads the chat in order and decides
  every name per reply through Signal's MCP tools (`known`, `lookup`, `schema`, `decide`).
- **The weak-spot fixes** are in:
  - every name gets a decision, external and not-in-code ones included;
  - `decide` lists what's still undecided, and won't take "plain" for a name written as code;
  - the rules place commands, flags and routes however they're written, including in code blocks.
- **Results.** On a long session (276 replies), the reader got 79% of names right with 70 wrong links. The
  old three-step pass got 73% with 76 wrong. It cost $8.30 (API prices, as Claude Code reports them) and
  took 25 minutes.
- **Kubernetes names** are the rules' too, with a `kube` tool for the reader: manifests, Helm templates rendered for
  the repository's releases, kustomize overlays, pods, namespaces, kubectl and helm commands, chart values and
  kubeconfig contexts (names only). `tests/kube_fixture.py` is a chat and repository
  to measure it on (rules only: 15.6% → 81.2% right). Not yet measured with the real reader.
- **Two switches** on the header's link button: Show links and Activate Signal, apart.
- **Tests.** `tests/run.sh` passes (104 tests, against `tests/fake_claude.py`). They run on macOS too, outside Xvfb
  (`python3 tests/test_baude.py`), but for three that are about image decoding and the trash there.

## Why the reader takes so long and costs so much

The first 28 of 42 parts of the long session were replayed with the reader's stream recorded
(`tests/trace_claude.py`) and broken down with `tests/trace_report.py`. The report's cost matches Claude Code's
own to the cent ($4.16), with Sonnet 5.5's prices fitted from the result messages: $2 per million input tokens,
$10 output, $0.10 cache reads, and $4 cache writes. Claude Code writes its cache for an hour, which costs twice
the input price; that's the default on a subscription.

**Cost ($4.16 for 28 parts):**

| | tokens | cost | share |
|---|---|---|---|
| cache reads | 18.6 M | $1.86 | 45% |
| cache writes (1-hour) | 362 k | $1.45 | 35% |
| output | 85 k | $0.85 | 20% |

- **The session never compacts.** Sonnet 5.5's window is 1M tokens, and Claude Code compacts at about 967k.
  The context starts at 39k tokens (Claude Code's system prompt and tools, the instructions, the first part)
  and grows about 13k a part, to 377k at part 28 and about 550k by the end.
- **Every API call re-reads all of it.** A part takes 3.7 calls on average:
  - one per tool call (`decide` 1.6, read-only Bash 0.9, `lookup` 0.1);
  - one for the closing one-line answer, which a turn needs after its last tool result.

  So reads grow with the square of the chat's length, and they're most of the cost in later parts.
- **Writes are the context's growth,** about 13k tokens a part:
  - the part's text, about 20k characters, of which Claude's tool calls and results are about half or more,
    the replies 15%, your messages 7% and the rules' names 7%;
  - `decide`'s input, about 5k characters a part;
  - tool results and thinking.
- **Output is mostly `decide`'s JSON:** 140k characters for 794 decisions, about 175 characters each. The
  notes are only 10% of that; the rest is keys and full repository paths. Thinking is 11.6k tokens (14%).

**Time (589 s inside the 28 parts):**

| | time | share |
|---|---|---|
| generating | 389 s | 66% |
| waiting for each call's response to start | 177 s | 30% |
| waiting on Signal's tools | 25 s | 4% |

- **Generating** runs at about 220 output tokens a second, so it's mostly writing `decide`'s JSON. A part
  with 108 decisions took 68 s.
- **Waiting for a call to start** is about 1.7 s per call. This is sending the request and reading the cached
  context.
- **Signal's tools aren't the bottleneck.** `decide` answers in 0.2 s, and `lookup` in 3 s when a language
  server is involved. Their worker thread is shared with the rules' lookups, but it isn't what slows the reader.

The rest of the full run's time (1340 s inside 42 parts) came from later, heavier parts: one took 118 s for
13 decisions. Those weren't recorded.

**What would cut it.** These are estimates for the whole session from a model fitted to the recorded parts:
3.7 calls a part, 13k tokens of growth a part, 3k output tokens a part, 1.7 s per call to start, 220 output
tokens a second. The model gives $8.04 against the measured $8.30.

| change | cost | time | how |
|---|---|---|---|
| 5-minute cache | −10% | 0 | `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` in the reader's environment: parts come every ~20 s, so an hour isn't needed |
| compact at 200k | −25% | +8% | `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000`: Claude Code summarises the reading so far instead of carrying it all; earlier decisions stay in Signal's store (`known`) |
| no closing answer | −16% | −8% | decisions as the turn's final message instead of a `decide` call (Baude checks them, and rejections go back with the next part), or an interrupt once `decide` comes back clean (whether the tool result survives an interrupt isn't documented) |
| undecided names sent with the part | −10% | −5% | Baude knows them before the reader starts: list them in the part, so a second `decide` isn't needed |
| compact `decide` format | −10% | −20% | one line per decision (`reply · name · verdict · path:line · role`), no kind, notes only where useful |
| tool transcripts halved | −18% | 0 | trim tool results further in full context. It may cost accuracy: full context cut wrong links before |
| all but the transcript trim | −54% | −25% | |

### Measured: glossary-only history, and smaller parts

**What glossary-only means.** `"signal_history": "glossary"` gives each part a fresh session that doesn't see
the parts before it. What it knows of them is the glossary: the decisions already made for the names in its
part come with it, and `known` has the rest. Its context stays small all through the chat.

**The setup in all three runs.** Every session was lean: the reader's instructions as the system prompt
instead of Claude Code's, only Read, Grep, Glob and Bash, no skills or commands listed, and Signal's tools
loaded at the start (`ENABLE_TOOL_SEARCH=false`). Without that, each new session rewrote about 10k tokens of
Claude Code's own context, and glossary-only cost more per part than a shared session.

The whole long session (42 parts at 18k characters), Sonnet, full context, medium effort, scored with
`tests/signal_score.py`. The three runs ran at the same time:

| | right | wrong | not in the code | external | cost | time |
|---|---|---|---|---|---|---|
| one session, before the lean setup | **79.0%** | 70 | 54.4% | 65.8% | $8.30 | 1521 s |
| one session, lean | 77.0% | **65** | 52.9% | 57.9% | $7.85 | **1138 s** |
| glossary-only, 18k parts | 73.2% | 83 | 51.5% | 71.1% | **$4.10** | 1408 s |
| glossary-only, 6k parts (107 parts) | 68.3% | 88 | 41.2% | **78.9%** | $5.80 | 1891 s |

**Glossary-only halves the cost.** Cache reads fall from $4.59 to $0.37, because no call re-reads the chat so
far. What's left is mostly cache writes ($2.69): each new session writes its part, plus Claude Code's own
first-turn context.

**But it's less accurate.** It got 3.8 points less right and 18 more wrong links. The history the shared
session carries helps it place names: what the chat is working on, and which of several same-named files is
meant.

**It isn't faster, and a new session per part costs about 2 s.** A part's first call starts about 3.2 s after
the part is sent, against 1.2 s in the shared session:
- **Claude Code's own setup** (login, settings, plugins, Signal's MCP relay) takes about 0.7 s on a quiet
  machine and 1.2–1.6 s with three replays running. The binary itself starts in 0.01 s, so there's nothing to
  gain by "freezing" it.
- **The first call** has to write its part to the cache, which adds the rest.

Most of the measured gap (1408 s against 1138 s) wasn't this. It was a network stall during these runs: one
session's first call waited 233 s through seven API retries, and another's 110 s. Without them, glossary-only
took about as long as the shared session.

**The reader now hides the setup with a warm spare** (`SignalReader.warm_spare`). As each part is sent, the next
part's session is started, so its setup overlaps the current part's API calls. An idle session costs no
tokens. The spare is dropped when the chat has been read, when the reader stops, or at the budget cap. Reader
sessions also run with `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, which saves about 0.1 s per start and
keeps telemetry and update checks out of them.

Measured on 17 parts of the same session, run on its own: a part's first call started after a median 1.6 s
(at most 2.5 s), against 3.2 s without the spare. That's close to the 1.4 s of any later call, and each
spare had a median 10 s head start. The 3.2 s came from three replays running at once. On a quiet
machine, a cold start against a pre-started session measured 1.9 s against 1.4 s. So the spare saves about
0.5 s per part on a quiet machine and more under load: about 20–65 s over the session's 42 parts, more with
smaller parts. What's left over a shared session (about 0.5 s) is the first call writing its part to the
cache.

**Smaller parts are worse on every count.** With 6k parts, the 107 sessions each pay the per-session writes,
so it costs more ($5.80). It's less right (68.3%), and it puts fewer names "not in the code", because each part
sees less of the conversation. It does mark more names external (78.9%).

**One run each.** Run-to-run variation on this session is a point or two, so the difference between the two
shared-session rows may be noise. The gaps to glossary-only (4–9 points, 18–23 wrong links) are bigger than
that.

**Since these runs, each reader session is also clean.** It runs as Baude's `signal-reader` agent (`--agents`
and `--agent`), with `--setting-sources user`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`,
`ENABLE_CLAUDEAI_MCP_SERVERS=false` and `"disableClaudeAiConnectors": true`. It doesn't inherit the user's
auto memory, the project's agents, hooks or CLAUDE.md, or claude.ai connectors (`--strict-mcp-config` had
already kept MCP servers out). A test session wrote about 2.4k fewer tokens on its first call, which is about
$0.01 less per new session.

**Next to try:** keep the shared session but bound it.
- `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000` makes Claude Code summarise the reading so far instead of
  carrying it all. That keeps a gist of the history, which glossary-only loses, and avoids a process per part.
- Glossary-only with a short "story so far" in each part would be the same idea, done by Baude.

### Recording a run

`--no-session-persistence` means Claude Code keeps no transcript of the reader's session, so record its
stream instead. Replay a session under Xvfb, as `tests/run.sh` does, with the recording wrapper as its claude:

```bash
env -u WAYLAND_DISPLAY GDK_BACKEND=x11 GSK_RENDERER=cairo TRACE_DIR=/tmp/signal-trace \
  xvfb-run --auto-servernum --server-args="-screen 0 1400x1000x24" \
  python3 tests/ref_replay.py SESSION.jsonl FOLDER /tmp/out.json --lsp --sonnet 4 --model sonnet \
  --context full --effort medium --claude "$PWD/tests/trace_claude.py" --work /tmp/replay-work --timeout 3600
python3 tests/trace_report.py /tmp/signal-trace
```

`--claude` needs an absolute path, because the reader runs in the session's folder. `--sonnet 4` caps the run
at about $4, about 28 parts of a 42-part session. To try a setting, put it in the environment of the replay
(the reader's claude inherits it) and compare the report and `tests/signal_score.py`'s numbers.

### Other notes

`ENABLE_TOOL_SEARCH=false` (now set) also saves the ToolSearch call each session made to load Signal's tools. The two settings need no code beyond the reader's environment. Compaction
and the transcript trim may change accuracy, so score them with `tests/signal_score.py`. The cache setting and
the format changes shouldn't.

## Measuring accuracy

`tests/signal_score.py RESULT.json MESSAGES.json REFERENCE.json...` scores a replay per mention against
reference lists; its docstring and `tests/ref_score.py`'s give the list format. The lists used so far
describe a private repository and aren't in this one. To measure again, pick sessions of your own that are
about as demanding:

- long (a few hundred replies), in a large repository (a monorepo with many services and packages);
- many tool calls, and subagents;
- a move into a worktree partway;
- plenty of routes, commands and flags, database tables and columns, config keys, env vars and commits;
- names that aren't in the code: proposed ones, ones in other checkouts, outside tools.

Then have Opus subagents list every entity a reader might want to jump to in each reply, with its class and
real place, or why it isn't in the repository. Check a sample by hand. Save the
replies' text as a JSON list for `MESSAGES.json`. Keep the lists, transcripts and results outside the
repository.
