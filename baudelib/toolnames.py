"""A tool's name as every app says it. Claude Code names an MCP server's tool mcp__<server>__<tool>, its server's name
made safe (each character not a letter, digit, _ or - made _): "Mesh / Tasks" is what's shown, the server's name as
it says it and the tool's title. Where they come from, best first: Baude's own servers' (SERVERS, TITLES: each says
them in its initialize and tools/list, as MCP's title); the servers a host's claude said it has (its system init's
mcp_servers, by their names as configured: "claude.ai Slack"), which the host keeps and says in its info's tool_names
for its heads; and only then the name itself made words. No GTK."""
import re

# (Baude's own MCP servers, by the name each is configured as: its title)
SERVERS = {"mesh": "Mesh", "chat": "Chat", "host": "Host", "emission": "Emission", "multiplex": "Multiplex",
           "baude": "Baude", "meta": "Meta", "signal": "Signal"}
# (a tool of theirs whose name, made words, doesn't say it: its title)
TITLES = {"emission": {"tabs_context": "Tabs", "javascript_tool": "Run JavaScript"}}
# (a well-known server whose configured name is all lower case: as its makers write it)
BRANDS = {"github": "GitHub", "gitlab": "GitLab", "gmail": "Gmail", "gdrive": "Google Drive",
          "google_drive": "Google Drive", "gcal": "Google Calendar", "slack": "Slack", "notion": "Notion",
          "linear": "Linear", "jira": "Jira", "figma": "Figma", "postgres": "Postgres", "sqlite": "SQLite",
          "playwright": "Playwright", "sentry": "Sentry"}
UUID = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")

_servers = {}  # (key: its title, as a host or claude said it)
_tools = {}  # (mcp__<server>__<tool>: its title, as a host said it)


def key(server):
    """A server's name as Claude Code puts it in its tools' names."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", server)


def humanize(identifier):
    """snake_case / camelCase / kebab-case -> 'words like this'."""
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier).replace("_", " ").replace("-", " ")
    return " ".join(words.split()).lower()


def server_title(name):
    """A server's title from the name it's configured as: a claude.ai connector's ("claude.ai Slack") and a plugin's
    ("plugin:github:github") without what says where it's from; one written all in lower case, in words."""
    name = re.sub(r"^claude\.ai\s+", "", name)
    if name.startswith("plugin:"):
        name = name.rpartition(":")[2]
    if name in SERVERS:
        return SERVERS[name]
    if name.lower() in BRANDS:
        return BRANDS[name.lower()]
    if name == name.lower():
        return " ".join(w[:1].upper() + w[1:] for w in humanize(name).split()) or name
    return name


def learn(table):
    """A host's tool_names (its info's): {"servers": {key: title}, "tools": {name: title}}."""
    if isinstance(table, dict):
        _servers.update({k: v for k, v in (table.get("servers") or {}).items() if isinstance(v, str) and v})
        _tools.update({k: v for k, v in (table.get("tools") or {}).items() if isinstance(v, str) and v})


def learn_servers(servers):
    """The servers a claude said it has (its system init's mcp_servers: [{name, status}])."""
    for s in servers or ():
        name = s.get("name") if isinstance(s, dict) else s
        if isinstance(name, str) and name:
            _servers.setdefault(key(name), server_title(name))


def table(servers=()):
    """What a host says of the tools' names (its info's tool_names): Baude's own servers' and their tools' titles,
    and the servers its claude has said it has (names as configured)."""
    out = {key(n): server_title(n) for n in servers if isinstance(n, str) and n}
    return {"servers": {**out, **SERVERS},
            "tools": {f"mcp__{s}__{t}": title for s, titles in TITLES.items() for t, title in titles.items()}}


def split(name):
    """mcp__<server>__<tool>: (its server's key, its tool's name); None for a tool not an MCP server's. The server's
    the longest known key it starts with (a key can have __ in it), else what's before the next __."""
    if not isinstance(name, str) or not name.startswith("mcp__") or name.count("__") < 2:
        return None
    rest = name[5:]
    for k in sorted({**SERVERS, **_servers}, key=len, reverse=True):
        if rest.startswith(k + "__") and len(rest) > len(k) + 2:
            return k, rest[len(k) + 2:]
    server, _, tool = rest.partition("__")
    return server, tool


def describe(name):
    """(the server's title, the tool's title) for an MCP server's tool; None for any other."""
    got = split(name)
    if got is None:
        return None
    server, tool = got
    if name in _tools:
        title = _tools[name]
    elif tool in TITLES.get(server, {}):
        title = TITLES[server][tool]
    else:
        bare = re.sub(r"^(claude_ai_|plugin_[^_]+_)", "", server)
        if tool.lower().startswith(bare.lower() + "_"):  # (slack_send_message on the Slack server)
            tool = tool[len(bare) + 1:]
        title = humanize(tool)
        title = title[:1].upper() + title[1:] or tool
    if server in _servers or server in SERVERS:
        source = _servers.get(server) or SERVERS[server]
    else:
        bare = re.sub(r"^(claude_ai_|plugin_[^_]+_)", "", server)
        source = None if UUID.fullmatch(bare) else BRANDS.get(bare.lower()) or \
            " ".join(w[:1].upper() + w[1:] for w in humanize(bare).split())
    return source, title


def label(name, unnamed="MCP"):
    """A tool's name as it's shown: an MCP server's as "Server / Tool" (unnamed: what a server known only by an id is
    called), any other as it is."""
    got = describe(name)
    if got is None:
        return name
    return f"{got[0] or unnamed} / {got[1]}"


def selected(query):
    """ToolSearch's "select:a,b" query: the tools it names, each as a name to show; None for a search."""
    if not isinstance(query, str) or not query.startswith("select:"):
        return None
    return [n.strip() for n in query[len("select:"):].split(",") if n.strip()]
