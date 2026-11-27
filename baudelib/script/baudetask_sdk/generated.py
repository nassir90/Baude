"""The task-script SDK's generated half: made by baudelib/tasksdk.py from schema/types and the Bot's own tools'
definitions (baudelib/emissiontools.py, baudelib/multiplextools.py). Don't change it here: change those, and run
`python3 -m baudelib.tasksdk` again."""
from .client import Area, Obj


class AccessChat(Obj):
    """A chat's own rules for managing it, each going before its host's of the same name; one not there is the host's.
    Checked as a pair: write lets in only those read does, whatever the host's rules are.

        manage_read (AccessRule): Who may read it.
        manage_write (AccessRule): Who may change it (and read it).
    """
    _refs = {"manage_read": "AccessRule", "manage_write": "AccessRule"}


class AccessMember(Obj):
    """A member a rule can name: one the host reaches, has lately, or a rule names.

        id (string): Its id.
        name (string): Its name.
    """


class AccessRule(Obj):
    """Who in the mesh may use one of a host's capabilities: every member, none, or those chosen. A head on the host's
    own machine, or one over ssh, is never refused.

        who ('everybody' | 'nobody' | 'chosen'): Every member, none, or those in members.
        members (array of string): chosen's: the members' ids.
    """


class AccessRules(Obj):
    """A host's rule for each of its capabilities, by name: a capability split into what reads and what changes has
    two, <capability>_read and <capability>_write (write lets a member read too); one with nothing to split has one.
    Everybody's where none's set, but access_write's, nobody's.

        shell (AccessRule): A shell on its machine: its terminals (terminal ops) and Multiplex's, through a
            Bot's terminal tools (multiplex), and a command run in a pod's or a container's (kube's and docker's
            exec).
        browser_read (AccessRule): Its Bots' Emission browsers watched: the watch stream (emission_watch) and
            what the pane says back on it (emission_watch_say).
        browser_write (AccessRule): Its Bots' Emission browsers driven: their tools' calls (emission), a phone
            showing one and answering its calls (emission_serve, emission_answer), Emission's own window brought
            up on its screen (browser_open), or watched and driven from elsewhere (emission_watch and emission,
            default), and a Bot made or driven (with Shell). Lets a member watch too.
        providers (AccessRule): Its inference providers' models, run through the mesh (infer).
        model_files (AccessRule): Its models' files, read through the mesh (files).
        spawn (AccessRule): New chats and agents started on it (start, agent).
        manage_read (AccessRule): Its chats as they are, read: listed (and told of their turns' ends), attached
            to and watched, their transcripts, sizes, subagents, senders, a Bot's picture, memory and tasks
            read, their Auto-fix as it is, their folders read (read_file, list_dir, git, signal_names,
            catalogue), forked from; Record's notebooks here listed, and the edits waiting on their pages
            (notes, edits). A chat's own rule (AccessChat's manage_read) goes before this.
        manage_write (AccessRule): Its chats as they are, changed: written in, held messages sent or dropped,
            asks answered, stopped, renamed, archived, deleted, their folders moved, tools allowed always
            (attach's only where it adds some, its env only where it changes it), made Bots, their Auto-fix
            turned on or off, a Bot's memory written and tasks set, run and stopped, a chat adopted, moved to
            another subscription (with Subscriptions' write); Record's notebooks here written, and edits to
            their pages proposed, revised, withdrawn, decided and adopted (notes' write, edit_*). Lets a member
            read them too. A chat's own rule (AccessChat's manage_write) goes before this.
        kube_read (AccessRule): Its machine's Kubernetes clusters read: contexts, lists, YAML, owners, a pod's
            log (kube) and logs and watches (kube_follow), and the clusters' parts of @.
        kube_write (AccessRule): Its machine's Kubernetes clusters changed: a pod deleted, objects applied
            (kube's delete, apply). Lets a member read them too.
        docker_read (AccessRule): Its machine's Docker read: contexts, lists, an object inspected, a log as it
            is now (docker) and logs (docker_follow). A shell in a container is a terminal: Shell's.
        docker_write (AccessRule): Its machine's Docker changed: a container or Compose project started,
            stopped, restarted or removed, an image, volume or network removed (docker's start, stop, restart,
            remove). Lets a member read it too.
        databases (AccessRule): Its machine's databases, read (databases): their schemas, a table's rows, a
            query run read-only.
        subscriptions_read (AccessRule): Its subscription accounts read: listed, and their usage (accounts' list
            and usage).
        subscriptions_write (AccessRule): Its subscription accounts changed: added, renamed, a token replaced,
            removed (signed out), a provider's default picked, a chat moved to one (with the chat's Agent
            Management write too), signed in to (accounts' sign_in and code, login, login_code). Lets a member
            read them too.
        access_read (AccessRule): These rules read (access), and the host's own: its telemetry (telemetry_read)
            and its Ongoing Feedback listed (fixes' list and shot).
        access_write (AccessRule): These rules changed (access_set), and the host itself: stopped, linked and
            unlinked, deactivated, its telemetry, themes and feedback (quit, link, unlink, deactivate, activate,
            telemetry_set, theme_save, theme_remove, feedback), its Ongoing Feedback acted on (fixes' merge,
            discard, reopen, reply, fix, stop). Lets a member read them too. A head on its own machine, or one
            over ssh, may always.
    """
    _refs = {"shell": "AccessRule", "browser_read": "AccessRule", "browser_write": "AccessRule",
             "providers": "AccessRule", "model_files": "AccessRule", "spawn": "AccessRule",
             "manage_read": "AccessRule", "manage_write": "AccessRule", "kube_read": "AccessRule",
             "kube_write": "AccessRule", "docker_read": "AccessRule", "docker_write": "AccessRule",
             "databases": "AccessRule", "subscriptions_read": "AccessRule", "subscriptions_write": "AccessRule",
             "access_read": "AccessRule", "access_write": "AccessRule"}


class AccessState(Obj):
    """A host's access rules, as access and access_set answer them.

        host (string): The host's name.
        rules (AccessRules): Each capability's.
        members (array of AccessMember): The members its rules can name, by name.
        editable (boolean): The connection asking may change them (access_set): a head on its machine or over
            ssh, else a member the access_write rule allows.
    """
    _refs = {"rules": "AccessRules", "members": "AccessMember"}


class Account(Obj):
    """A Claude or Codex subscription account kept on a host (Settings → Subscriptions): what a head sees of it; its
    credential never leaves the host.

        id (string): Its id on its host (what a session's kept with).
        provider ('claude' | 'codex'): Whose subscription it is.
        name (string): Its name, the user's, unique for its provider on its host.
        kind ('folder' | 'token' | 'profile'): folder: a Claude account that is a config folder of its own on
            its host (CLAUDE_CONFIG_DIR), signed in to with claude auth login run there with it (signed in again
            the same way); token: a Claude account signed in with a token from claude setup-token or an
            Anthropic API key (signed in again by replacing it); profile: a Codex account, a CODEX_HOME of its
            own, signed in with ChatGPT or an OpenAI API key (signed in again with codex login).
    """


class Ask(Obj):
    """A permission its agent's asking for, not answered yet. Answered with input: a control_response for its
    request_id, as claude's stream-json has it.

        request_id (string): The control request's id.
        tool (string or None): The tool it asks to use.
        input (object or None): The tool's input (long strings in it cut short).
    """


class AutofixPull(Obj):
    """A chat's Auto-fix while it's on: the pull request of the chat's branch its host follows, every five minutes,
    sending the chat a failing check, a merge conflict or a new comment.

        repo (string): Its repository, owner/name.
        number (integer): The pull request's number.
        url (string): Its page.
        title (string): Its title.
        error (string or None): Why its last read or message didn't work (gh signed out, offline, ...), till one
            does; null if it did.
    """


class BotFields(Obj):
    """What bot sets: a name and, maybe, a picture.

        name (string): Its name.
        picture (string or None): A PNG, base64; "" to drop it; left out, kept.
    """


class BotListing(Obj):
    """A Bot, as a session lists it.

        name (string): Its name.
        since (number or None): When it was made a Bot, epoch seconds.
        picture (number or None): Its picture's mtime; null for none.
    """


class BotRef(Obj):
    """A Bot, as its browser's ops name it.

        id (string): Its session's id.
        name (string or None): Its name.
    """


class Branch(Obj):
    """A checkout's branch.

        name (string or None): Its name.
        upstream (string or None): What it tracks.
        ahead (integer): Commits ahead of it.
        behind (integer): Commits behind it.
        rev (string): What names it to git.
        label (string): How the branch list names it.
        sha (string or None): Its tip.
        gone (boolean): Its upstream is gone from the remote.
        worktree (string or None): The checkout it's in.
        remote (boolean): A remote branch.
        touched (boolean): This chat touched it (the list's first part).
    """


class CallbackPage(Obj):
    """What a Codex browser sign-in's server on the host answered the address a browser ended on (accounts callback),
    for the head to show that browser: its own redirects back to itself followed.

        status (integer): Its HTTP status.
        type (string): Its Content-Type.
        body (string): The page.
        location (string): Where it sends the browser on to, if it does (a page of OpenAI's).
    """


class ChatDraft(Obj):
    """A chat's draft, as its host keeps it: the text written in its box and not sent, the same on every head that
    opens the chat, on any machine. Only the text: what's attached is a file on the head's own machine, kept there.

        text (string): What's written (empty once it's sent).
        rev (integer): Its revision: one more with every save, never less (a head drops one it's seen a later
            revision than).
        head (string or None): The box that wrote it (draft_save's head): what that box knows as its own save,
            coming back.
        at (number or None): When it was written, epoch seconds.
    """


class ChatLogin(Obj):
    """A sign-in, as its host has it: a chat's to Claude, from which every head draws the chat's sign-in card alone
    (LoginCard), whichever head started what's going on; or a Codex account's sign-in in Subscriptions (accounts
    sign_in's login), from which the head that asked draws its card.

        state ('needed' | 'signing_in' | 'signed_in' | 'failed'): needed: a turn failed for want of signing in
            (and every sign-in started since was cancelled); signing_in: claude auth login (codex login) runs on
            the host for it; signed_in: it finished; failed: it ended without signing in.
        method ('browser' | 'device'): A Codex sign-in's: browser, codex login's own (its page sends the browser
            back to codex's server on the host's 127.0.0.1, at port); device, codex login --device-auth's (a
            code entered on its page).
        url (string): signing_in, a Codex sign-in's: the page to open, once codex has said it.
        code (string): signing_in, a Codex device-code sign-in's: the one-time code to enter on that page.
        port (integer): signing_in, a Codex browser sign-in's: the port of the host's 127.0.0.1 its page sends
            the browser back to (1455, else 1457).
        opened (boolean): signing_in, a Codex browser sign-in's: codex opened the page in the host's own browser
            itself (on macOS, where it doesn't go by BROWSER).
        expired (boolean): needed because the sign-in there was ran out (expired, reached its time limit, can't
            be renewed), not because there's none.
        reason (string or None): needed: the first sentence of Claude's own error; failed: why the sign-in
            didn't finish.
        who (string or None): signed_in: who as, as claude auth status says it (Codex's: the email its sign-in
            names); null if it didn't say, or the head may not read Subscriptions.
        turn (integer): needed: which of the chat's turns that failed for want of signing in it's for, counted
            on its host since it started: a new one each time one fails, so every head moves the card to that
            turn.
    """


class Clock(Obj):
    """The host's clock.

        zone (string or None): Its time zone's name.
        offset (integer): Seconds from UTC.
    """


class Command(Obj):
    """What its agent is running: a Bash or PowerShell call (its subagents' too) from the call to its result (one
    waiting on its permission ask isn't running yet), or a background task but a subagent from its start to its
    notification.

        id (string): The call's tool_use_id, or the task's task_id.
        what (string): What it runs, 80 characters at most.
        since (number): When it started, epoch seconds.
        background (boolean): Run in the background.
    """


class Commit(Obj):
    """A commit, as git's log has it.

        sha (string): Its sha.
        parents (array of string): Its parents.
        author (string): Its author.
        email (string): Their email.
        at (integer): When, epoch seconds.
        refs (array of string): Branches and tags at it.
        subject (string): Its first line.
        ct (integer): When it was committed, epoch seconds.
        title (string): What the log calls it (a merged pull request's name).
        files (integer): How many files it changed.
        added (integer): Lines it put in.
        removed (integer): Lines it took out.
    """


class ContextCategory(Obj):
    """One kind of what a session's context holds (ContextUsage's categories): the system prompt, tools, messages, free
    space.

        name (string): What it is, as its agent names it.
        tokens (integer): The tokens it takes up.
        kind (string or None): Its agent's kind for it (deferred: not in the context yet, left out of the count
            shown).
    """


class ContextUsage(Obj):
    """How much of its context window a session's agent last said it's used (Claude Code's get_context_usage answer,
    Codex's token count, pi-claude's count of what it sends), kept by its host: asked of its agent as it starts,
    through a turn while a chat has it open (at most once a second), as the turn ends, after a compaction and after
    its model's changed, and kept while its agent's stopped; given another model's window (or none) if one's picked
    while it's stopped. Every app's context meter shows it.

        totalTokens (integer or None): The tokens its conversation takes up now; null where its agent hasn't
            said.
        maxTokens (integer or None): Its model's window, in tokens; null or 0 where its agent doesn't know it.
        showWithoutLimit (boolean): Shown though there's no window (Codex's before it says one): the tokens used
            alone.
        categories (array of ContextCategory): What the tokens are, as its agent splits them (its meter's
            tooltip). Left out of a session's listing: the chat that's open asks for them (get_context_usage).
    """
    _refs = {"categories": "ContextCategory"}


class DbColumn(Obj):
    """A table's column.

        name (string): Its name.
        type (string): Its type.
        null (boolean): Whether it may be null (null: not known).
        pk (boolean): Whether it is in the primary key.
        fk (string or None): The column it references, as the diagram's card says it (schema omitted in its
            own), or null.
    """


class DbLink(Obj):
    """A foreign key: a column that references another table's.

        from_schema (string): The referencing table's schema.
        from_table (string): The referencing table.
        from_column (string): The referencing column.
        to_schema (string): The referenced table's schema.
        to_table (string): The referenced table.
        to_column (string): The referenced column.
    """


class DbServer(Obj):
    """A database server in a running container, as the database view lists it.

        container (string): The container (or pod) it runs in.
        engine (string): postgres or arango.
        version (string): Its version, or empty.
    """


class DbTable(Obj):
    """A table (or view, or collection) and its columns.

        schema (string): Its schema (an ArangoDB server's: collections).
        name (string): Its name.
        kind (string): r table, p partitioned table, v view, m materialized view, f foreign table, e edge
            collection.
        rows (integer): About how many rows it has.
        columns (array of DbColumn): Its columns, in order.
    """
    _refs = {"columns": "DbColumn"}


class DecideQuestion(Obj):
    """One of a decision's questions, as System One has them: noul (yes or no: the probability it's true), choice (one
    of criteria's options, by name) or score (an ordered rubric, criteria's levels from low to high).

        type ('choice' | 'noul' | 'score'): What kind of question it is.
        instructions (string or object or array): Text, or structured data the decider renders as it does text.
        criteria (object or array or None): choice: each option's name and what it means (null for a bare name);
            score: its levels, lowest first; noul: what true and false mean, or nothing.
    """


class Decider(Obj):
    """A decider a host knows (baudelib/deciders.py): a server that answers Strands Decider's System One API (POST
    /v1/systemone, GET /health), as the deciders op lists it, and as a member's record says it (no url). What a
    task's decider variable binds, and what decide's decider names, is its value.

        value (string): What decide's decider names it as, on the host that listed it: its id for one of that
            host's own, <its host's name>/<its id> for another member's.
        id (string): Its id on its host.
        name (string): Its name.
        host (string or None): The member that has it, by its id (the listing host's own: null).
        host_name (string or None): That member's name.
        url (string or None): Where it's served (the listing host's own only).
        reachable (boolean): It answered as a decider when it was last looked at: a variable bound to one that
            didn't is warned of before a run.
        model (string or None): The model it serves, as its /health says.
        error (string or None): Why it didn't answer.
    """


class Decision(Obj):
    """A decider's answer to one question, as System One gives it.

        type ('choice' | 'noul' | 'score'): Its question's kind.
        choice (string): choice: the option picked.
        noul (number): noul: the probability it's true.
        score (number): score: the expected level, over the levels' indices (0 the lowest).
        probabilities (object): choice: each option's probability; score: each level's, by its index.
        legend (object): score: each level's index and what it is.
        confidence (number): How sure it is, calibrated (choice and score; a noul's probability says it).
    """


class Dep(Obj):
    """A program or library a feature needs (schema/deps), as a host or an app has it: whether it's there, and how it's
    installed on that machine.

        dep (string): Its key in schema/deps (vte, kubectl, docker, …).
        name (string): Its name, as it's shown.
        where ('app' | 'host'): What needs it: an app's own process, or the host process.
        present (boolean): It's there.
        fix (string or None): The command that installs it on that machine (a check's: readies it; Ollama
            installed and not running: starts it), or null (another Linux: link).
        link (string or None): Its install page (none for a check).
        group (string or None): The schema/copy key of what Settings → Dependencies lists it under
            (deps.providers, window.kubernetes, …).
        check (boolean): It's a check of an installed program (kubeconfig, GitHub Account): it has no page of
            its own.
        ready (string or None): What's said of it while it's there (Installed, Signed in, Running, 3 models), as
            its host says it; "Couldn't check" when looking raised; null when it isn't there.
    """


class DirEntry(Obj):
    """An entry in a folder.

        name (string): Its name.
        dir (boolean): A folder.
        size (integer): A file's size.
        ignored (boolean): git ignores it.
    """


class DockerColumn(Obj):
    """A table's column.

        title (string): Its title.
        kind (string or None): name, status, age, or null.
    """


class DockerContext(Obj):
    """A Docker context the host can reach: one of docker context ls, or Colima's or Podman's socket found besides.

        name (string): Its name.
        current (boolean): docker's current context.
    """


class DockerFault(Obj):
    """Why Docker can't be reached, as docker's failure says.

        title (string): What went wrong.
        why (string): What docker said that says why.
        fix (string or None): A command to run in a terminal that fixes it (Docker started), or null.
        said (string): All docker wrote.
    """


class DockerPage(Obj):
    """A page of the Docker view: what one of its sidebar's rows lists.

        title (string): Its name, as the sidebar has it.
        key (string): What it's asked for by: running, containers, images, volumes, networks or compose.
        what (string): What it lists, in a word for a count: containers, images…
    """


class DockerRow(Obj):
    """An object's row.

        id (string): What it's asked for by: a container's, image's, volume's or network's id, a Compose
            project's name.
        name (string): Its name.
        cells (array of string): Each column's text.
        keys (array of any): Each column's sort key: a number or text.
        tones (array of string): Each column's tone (ok, warn, bad), or empty.
        tips (array of string): Each column's tooltip, or empty (an age's: its time in full).
        filters (array of string): What each column's filter goes by, where it isn't the column's text, else
            empty (a container's Status: its state, Running or Exited, where the text says for how long).
        actions (array of string): What can be done to it as it is: start, stop, restart, remove.
        logs (boolean): It has a log to follow (docker_follow).
        shell (array of string): A running container's: the command that opens a shell in it (bash if it has it,
            else sh), for a terminal on the host; else empty.
        running (boolean): A container that's running, or a Compose project with one running.
    """


class DockerSection(Obj):
    """A category of the Docker view's sidebar.

        title (string): Its name.
        icon (string): Its icon's name, or empty.
        pages (array of DockerPage): Its pages (one with the category's own name: the category is that page).
    """
    _refs = {"pages": "DockerPage"}


class Edit(Obj):
    """An edit proposed to a page of a notebook, kept by the notebook's host (edits.json, beside records.json) until
    the user accepts or rejects it in Record.

        id (string): Its id.
        notebook (string): Its notebook's id.
        page (string): Its page's name.
        old (string): What it replaces (empty: it adds at the page's end).
        new (string): What it puts in.
        start (integer): Where what it replaces starts in the page's text.
        status ('pending' | 'accepted' | 'rejected' | 'withdrawn'): pending: waiting for the user; accepted,
            rejected; withdrawn: taken back.
        t (number): When it was proposed, in seconds since the epoch.
        by (string): Who proposed it, or revised it last: Meta, an agent's own name.
        gone (boolean): (edits) What it replaces isn't on the page as it was any more.
    """


class FoundLink(Obj):
    """A link straight to a member learned of through others (mesh's Found).

        id (string): The member's id.
        name (string or None): Its name.
        state ('waiting' | 'connecting' | 'linked' | 'dropped'): How it is.
        error (string or None): What's wrong.
    """


class GitFound(Obj):
    """Where a commit a link points at is in the log.

        sha (string): The commit.
        merge (string or None): The merge on the branch's own line it came in by, if it's not on that line.
    """


class GitMade(Obj):
    """A commit a chat's own commands made.

        sha (string): Its sha, as the command's output wrote it (short).
        subject (string): Its first line.
        tool_use_id (string): The tool call that made it.
        branch (string or None): The branch it was made on.
    """


class GitParent(Obj):
    """The branch a branch most likely came from.

        name (string): Its name.
        base (string): The commit they share.
        ahead (integer): Commits the branch has on it.
        behind (integer): Commits it has on the branch.
    """


class Gone(Obj):
    """A member reached lately, not now (restarting, say).

        id (string): Its id.
        name (string): Its name.
    """


class GoneFolder(Obj):
    """A session's folder gone and not made again.

        why (string): Why.
        folder (string or None): Where to carry on instead; null for nowhere to say.
    """


class Head(Obj):
    """An app on a host's own machine connected to its host process (one that's subscribed: Baude, Record; or an
    Emission there), by the code it runs. Those on the same code are one.

        client (string): What it is, as its hello says (baude, record, emission, baude-android, ...).
        commit (string or None): The commit its code is, as its hello says (null: it didn't say).
        made (integer or None): When that commit was made, in seconds, as the host's git has it (null: it hasn't
            it).
        behind (integer or None): In a host's info only: how many commits it lacks of the mesh's newest code
            (MeshView's newest), as that host's git counts them (null: it can't).
    """


class HeldMessage(Obj):
    """A message held for a session: one its host had for it while nobody was there to say whether it should go (a
    subagent's answer after a restart, say).

        id (string): Its id, as held takes it.
        text (string): What it says: what its agent's sent, as it's sent.
        shown (string): What a person's shown of it: the answer itself, or what became of it in a line (still
            working, an error). Heads show text where it's missing.
        sender (Sender or None): Who it's from.
        at (number or None): When it was held, epoch seconds.
    """
    _refs = {"sender": "Sender"}


class HostHello(Obj):
    """A host process as it says it is on a link (mesh._hello_of).

        id (string): Its id.
        name (string): Its name.
        group (string): Its group.
        host (string): Its machine's host name.
        version (integer): Its wire version.
        bonjour (string): Its name on the local network, if it said one.
        dial (boolean): false: it can't be linked to, only from (a phone, with no sshd).
        key (string): Its key (Ed25519's public half, in base64): what signs the tunnels it opens.
    """


class Info(Obj):
    """What a host is, as its hello says (and sessions carries).

        id (string): Its id.
        name (string): Its name.
        group (string): Its default group.
        version (integer): The wire version it speaks.
        models (array of object): Every model it can run: {value, displayName}, a Codex model's as its catalogue
            has it.
        host (string): Its machine's host name.
        home (string): Its user's home folder.
        pid (integer): Its process's id.
        checkout (string or None): Where its code is (a head here run from elsewhere is told so).
        groups (array of string): Its sessions' groups.
        init (object or None): What a session's program last said to initialize, with every model it can run in
            it.
        commit (string or None): The git commit its code was as it started (null: not a checkout).
        made (integer or None): When that commit was made, in seconds (null: not a checkout).
        behind (integer or None): How many commits it lacks of the mesh's newest code (mesh's newest), as its
            git counts them (null: it can't).
        heads (array of Head): The apps on its machine connected to it, by the code they run (each counted
            behind as its own commit is).
        update (Update or None): Its own updater's newer version, if it has one ready.
        restart (RestartState or None): Its restart, kept till nothing blocks it (restart), or null: none
            pending.
        providers (array of Provider): This machine's model providers (with base_url).
        mesh (MeshView): The members it reaches and the links it's made.
        notes (string): Record's notebooks here, as written last (notes.stamp: "" for none).
        edits (string): The edits proposed to the pages of Record's notebooks here, as written last
            (notes.edits_stamp: "" for none).
        phone (boolean): A phone's host.
        ops (array of string): The ops it takes, given by a host that takes only part of the wire (a phone's),
            with what its sessions take besides (start's ephemeral; control requests: control,
            set_permission_mode, set_model).
        telemetry (TelemetryState): Its telemetry state: the apps on its machine tell it telemetry only while
            it's on, and profile what it says.
        tool_names (ToolNames): How its MCP tools are named: what every head shows for one ("Mesh / Tasks").
    """
    _refs = {"heads": "Head", "update": "Update", "restart": "RestartState", "providers": "Provider",
             "mesh": "MeshView", "telemetry": "TelemetryState", "tool_names": "ToolNames"}


class KubeColumn(Obj):
    """A table's column.

        title (string): Its title.
        kind (string or None): name, namespace, owner, containers, age, status or null.
    """


class KubeContainer(Obj):
    """A pod's container, as its square shows it.

        name (string): Its name.
        look (string): ok, starting, restarted, bad or done.
        words (string): What its state is, in words.
        init (boolean): An init container.
    """


class KubeContext(Obj):
    """A context of the host's kubeconfig.

        name (string): Its name.
        server (string): Its cluster's API server (no credentials): with its name, which cluster it is. A
            context deleted and made again under the same name is another cluster.
        current (boolean): The kubeconfig's current context.
        local (boolean): Its API server is on the host itself.
    """


class KubeFault(Obj):
    """Why a cluster can't be read, as kubectl's failure is diagnosed.

        title (string): What went wrong.
        why (string): What kubectl said that says why.
        fix (string or None): A command to run in a terminal that fixes it, or null.
        said (string): All kubectl wrote.
    """


class KubePage(Obj):
    """A kind's page of the Kubernetes view.

        title (string): Its name, as the sidebar has it.
        resource (string): What kubectl gets it by.
        crd (string or None): A custom resource's definition (plural.group), or null.
        namespaced (boolean): Its objects are in namespaces.
        served (boolean): The cluster serves it.
        optional (boolean): Listed only on a cluster that serves it.
        source (string): kubectl, secrets or helm.
    """


class KubeRef(Obj):
    """A name in an object's YAML that is another object.

        line (integer): Its line (from 0).
        column (integer): Its column (from 0).
        length (integer): Its length.
        kind (string): The object's kind.
        name (string): Its name.
        namespace (string): Its namespace, or empty.
    """


class KubeRow(Obj):
    """An object's row.

        cells (array of string): Each column's text.
        keys (array of any): Each column's sort key: a number, a list of numbers or text.
        tones (array of string): Each column's tone (ok, warn, bad), or empty.
        tips (array of string): Each column's tooltip, or empty (an age's: its time in full).
        name (string): The object's name.
        namespace (string): Its namespace, or empty.
        uid (string): What it is known by across reads.
        version (string): Its resource version.
        owner_kind (string): What controls it: the kind, or empty.
        owner_name (string): What controls it: the name, or empty.
        containers (array of KubeContainer): A pod's containers, init ones first.
        default (string): A pod's default container (its annotation), or empty.
    """
    _refs = {"containers": "KubeContainer"}


class KubeSample(Obj):
    """The stack's totals at one time, in the namespaces asked for.

        at (number): When (seconds since 1970).
        cpu_requests (number): Cores asked for.
        cpu_used (number or None): Cores used, or null without metrics.
        memory_requests (number): Bytes asked for.
        memory_used (number or None): Bytes used, or null without metrics.
    """


class KubeSection(Obj):
    """A category of the Kubernetes view's sidebar.

        title (string): Its name.
        icon (string): Its icon's name, or empty.
        pages (array of KubePage): Its kinds.
    """
    _refs = {"pages": "KubePage"}


class KubeShare(Obj):
    """A namespace's or a node's part of the stack's totals, a row of its table.

        name (string): The namespace or node.
        cells (array of string): Each column's text.
        keys (array of any): Each column's number (the name's, its text in lower case), or null where there's
            none (no limit set, nothing requested, no metrics API): what it's sorted by, and what an "Other (n)"
            row adds up.
    """


class KubeShareColumn(Obj):
    """A column of Home's namespaces' or nodes' table: its title, and what its numbers are, so a head can sort by it
    and add rows up (the "Other (n)" row).

        title (string): Its title.
        kind ('name' | 'count' | 'cores' | 'bytes' | 'share'): What it holds: the namespace's or node's name, a
            count, cores (0.00), bytes (1.2GiB), or a share (82%): column of[0] as a share of column of[1].
        of (array of integer): A share's columns: what's used, and what it's a share of (its requests or
            limits).
    """


class KubeSummary(Obj):
    """Home: the whole stack's CPU and memory, in the namespaces asked for: the pods that hold on to a node (not those
    that finished), added up, by namespace and by node, and how the totals went.

        cpu (KubeUse): CPU, in cores.
        memory (KubeUse): Memory, in bytes.
        metrics (boolean): Whether the cluster has a metrics API (else nothing is used).
        pods (integer): How many pods are added up.
        namespace_columns (array of KubeShareColumn): The namespaces table's columns.
        namespaces (array of KubeShare): Each namespace's part, most CPU used first (then most requested).
        node_columns (array of KubeShareColumn): The nodes table's columns.
        nodes (array of KubeShare): Each node's part (against its own allocatable), most CPU used first (then
            most requested).
        history (array of KubeSample): The totals every `every` seconds from the first time Home was read for
            the context, oldest first, kept in the host's memory: those after the request's since (every one
            without).
        every (integer): How often the host samples them (and a head reads them), in seconds.
        minutes (integer): How far back the history goes, in minutes.
        sorted_by (integer): The column both tables come sorted by, most first: CPU used.
        shown (integer): How many rows a table shows, most first by the column it's sorted by; the rest are
            added up in one "Other (n)" row.
        began (number): When the host began this history (seconds since 1970): a context named for another
            cluster now (its cluster or server changed) begins anew, and a head drops the samples it holds from
            before.
    """
    _refs = {"cpu": "KubeUse", "memory": "KubeUse", "namespace_columns": "KubeShareColumn",
             "namespaces": "KubeShare", "node_columns": "KubeShareColumn", "nodes": "KubeShare",
             "history": "KubeSample"}


class KubeUse(Obj):
    """One resource's totals (CPU in cores, memory in bytes): what the pods ask for, use and are held to, and what the
    nodes can give them.

        title (string): Its name: CPU, Memory.
        requests (number): What the pods ask for (their containers' requests).
        used (number or None): What they use now (the metrics API's), or null without one.
        limits (number or None): What they're held to (the limits their containers set), or null when none sets
            one.
        allocatable (number or None): What the nodes can give pods, or null when the nodes can't be listed.
        cells (array of string): requests, used, limits and allocatable as they're shown.
        shares (array of string): requests, used and limits as shares of allocatable (28%), or empty.
    """


class Member(Obj):
    """A member of the mesh, as info's mesh lists it.

        id (string): Its id.
        name (string): Its name.
        group (string): Its group.
        host (string): Its machine's host name.
        version (integer): Its wire version.
        bonjour (string): Its name on the local network.
        dial (boolean): false: it can't be linked to, only from (a phone, with no sshd).
        key (string): Its key (Ed25519's public half, in base64): what signs the tunnels it opens.
        providers (array of Provider): Its model providers.
        hops (integer): Links away.
        through (string or None): The member it's reached through, by name; null for one linked to here.
        commit (string or None): The commit its code is (null: not a checkout, or it didn't say).
        made (integer or None): When that commit was made, in seconds (null: not known).
        heads (array of Head): The apps on its machine connected to it, by the code they run.
        behind (integer or None): How many commits it lacks of the mesh's newest code (MeshView's newest), as
            this host's git counts them (null: it can't).
        deciders (array of Decider): Its deciders, and whether each answered when it last looked.
    """
    _refs = {"providers": "Provider", "heads": "Head", "deciders": "Decider"}


class MemberInfo(Obj):
    """Who a member is, in its record.

        id (string): Its id.
        name (string): Its name.
        group (string): Its group.
        host (string): Its machine's host name.
        version (integer): Its wire version.
        bonjour (string): Its name on the local network.
        dial (boolean): false: it can't be linked to, only from (a phone, with no sshd).
        key (string): Its key (Ed25519's public half, in base64): what signs the tunnels it opens.
        providers (array of Provider): Its model providers.
        commit (string or None): The commit its code is (null: not a checkout, or it didn't say).
        made (integer or None): When that commit was made, in seconds (null: not known).
        heads (array of Head): The apps on its machine connected to it, by the code they run.
        deciders (array of Decider): Its deciders, and whether each answered when it last looked.
    """
    _refs = {"providers": "Provider", "heads": "Head", "deciders": "Decider"}


class MeshView(Obj):
    """The mesh as a host sees it (mesh.Mesh.view).

        members (array of Member): The members it reaches.
        links (array of OutLink): The links made from here.
        proxy (Proxy or None): Its Proxy: null till a member has a provider.
        inactive (array of string): The ids of members deactivated here.
        found (array of FoundLink): Links straight to members learned of through others.
        gone (array of Gone): Members reached lately, not now (not one deactivated here).
        newest (string or None): The commit of the newest code in the mesh (the latest made of every member's
            and app's), what each is counted behind (null: none's known).
    """
    _refs = {"members": "Member", "links": "OutLink", "proxy": "Proxy", "found": "FoundLink", "gone": "Gone"}


class Notebook(Obj):
    """One of Record's notebooks, as another machine's Record is given it.

        id (string or None): Its id.
        name (string): Its name.
        pages (object): Its pages.
    """


class OutLink(Obj):
    """A link made from here.

        where (Where): Where it goes.
        id (string or None): The member it reaches.
        name (string or None): Its name.
        state ('linked' | 'connecting' | 'dropped' | 'inactive'): How it is.
        error (string or None): What's wrong.
        active (boolean): Not deactivated.
    """
    _refs = {"where": "Where"}


class PageNow(Obj):
    """The tab in front, as it is now.

        title (string): Its title.
        url (string): Its address.
        progress (number): How far it's loaded, 0 to 1.
        loading (boolean): Loading.
        back (boolean): It can go back.
        forward (boolean): It can go forward.
    """


class PointerEvent(Obj):
    """What a Bot's tools did with the mouse, numbered (seq starts again from 1 with each Emission).

        seq (integer): Its number.
        kind ('hover' | 'click' | 'drag' | 'scroll'): What it was.
        x (number): Where, in CSS pixels.
        y (number): Where, in CSS pixels.
        path (array of array of number): A drag's path from there, 40 points at most.
        count (integer): Clicks, more than one.
        dx (number): A scroll's.
        dy (number): A scroll's.
    """


class Provider(Obj):
    """A model provider, as the mesh is told of it (mesh.published): no address or key of its machine's.

        id (string): Its id.
        kind (string): What it is (openai, ollama, apple, mlx, ...).
        name (string): Its name.
        http (boolean): It serves HTTP, run through a member's Proxy.
        models (array of ProviderModel): Its models.
        base_url (string or None): Where it's served, on its own machine (info's providers only).
    """
    _refs = {"models": "ProviderModel"}


class ProviderModel(Obj):
    """A model a provider has, as the mesh is told of it (mesh._model).

        value (string): What it's run as.
        displayName (string): Its name.
        id (string): Its provider's id for it.
        tools (boolean): It takes tools.
        thinking (boolean): It thinks.
        window (integer): Its context window.
        version (string): Its version.
        digest (string): Its digest.
        input (array of string): What it takes in.
    """


class Proxy(Obj):
    """This host's Proxy, which runs other members' models over HTTP.

        url (string): Where it is.
        key (string): What it's asked with.
    """


class PullInfo(Obj):
    """A GitHub pull request, as the Git page keeps it.

        number (integer): Its number.
        title (string): Its title.
        state (string): Where it stands: open, draft, changes, merged or closed.
        url (string): Its page.
        head (string or None): The branch it's from (null for a fork's).
    """


class PullLink(Obj):
    """A pull request a chat's replies name.

        repo (string or None): Its repository, owner/name (null for a bare #12).
        number (integer): Its number.
    """


class Record(Obj):
    """A member's record: who it is and the members it's linked to. A host sends its own when it changes (seq higher
    than any it sent before) and passes each one newer than it had on to its other links.

        id (string): The member's id.
        seq (integer): Its number: higher, newer.
        info (MemberInfo): Who it is.
        links (array of string): The members it's linked to.
    """
    _refs = {"info": "MemberInfo"}


class RestartBlocker(Obj):
    """One thing a pending restart waits for: a chat whose agent is running a command, a task's run, an agent run on
    this host for another host's chat (an Apple or pi model), or a terminal on this host that someone is attached
    to.

        kind ('chat' | 'task' | 'agent' | 'terminal'): A chat running a command, a task running, an agent run
            for a chat elsewhere, or an attached terminal.
        session (string): The chat's id: a chat's, a task's Bot's, or the chat elsewhere an agent run is for.
        host (string): An agent run's: the id of the host its chat is on.
        task (string): A task run's: its task's id.
        run (string): A task run's: its id.
        terminal (string): A terminal's: its id (terminal's attach).
        name (string): The chat's title, the task's name, the agent's model, or the terminal's title.
        waiting (boolean): A task run's: true while it waits for input (an ask of its Bot, asking); a restart
            would stop it.
        what (string): What it runs: the chat's oldest command, the agent run's chat's host, or the terminal's
            program.
    """


class RestartState(Obj):
    """A restart of the host process it's keeping till nothing blocks it: what asked for it, whether a head's holding
    it, and what it waits for. Every head shows the same.

        by ('head' | 'update'): What asked for it: a head (Restart Daemon), or its own updater (a newer
            version).
        to (string): The commit it'll restart onto, when its updater asked.
        paused (boolean): A head is showing what it waits for: it won't restart till that head's let go.
        blockers (array of RestartBlocker): What it waits for, oldest session first.
    """
    _refs = {"blockers": "RestartBlocker"}


class Running(Obj):
    """A command a session's agent is running, as quit's busy and info's update list them.

        session (string): The session's id.
        what (string): What it runs.
    """


class Sender(Obj):
    """Who a message to a session is from, if not its user, filled in by the host from what it knows itself
    (wire.clean_sender: words, cut short).

        kind ('bot' | 'chat' | 'subagent' | 'host'): What it is.
        name (string): Its name.
        host (string): Its host's id.
        host_name (string): Its host's name.
        session (string): Its session's id, on its host.
        bot (string): A chat a task's run started (a subagent sender, its report of a turn): the session of the
            run's Bot, on its host.
        task (string): A task's run's: the id of its task, on its Bot's Tasks page.
        task_name (string): A chat a task's run started: its task's name, as the from line says it ("<task>
            run").
        run (string): A task's run's: the id of the run, on its Bot's Tasks page (its Bot: bot, else its
            session).
        over ('finished' | 'stopped' | 'failed'): A chat a task's run started: how the turn it reports ended
            (finished; stopped: interrupted or stopped; failed: an error, or its agent ended before its turn
            did).
    """


class Session(Obj):
    """A session as a host lists it.

        id (string): A UUID the host made as it started it (claude's --session-id: what it's resumed by).
        title (string or None): Its title: given, its agent's, or its prompt's start.
        cwd (string or None): The folder its agent runs in.
        model (string or None): Its model.
        effort (string or None): Its effort.
        mode (string or None): Its permission mode.
        group (string or None): Its group.
        created (number or None): When it was made, epoch seconds: the earlier of when the host took it up and
            its transcript's first record.
        said (number or None): When something was last said in it, epoch seconds.
        updated (number or None): Its transcript's mtime.
        size (integer): Its transcript's size.
        state ('working' | 'waiting' | 'idle' | 'stopped'): working: at a turn; waiting: asking permission;
            idle: its agent running, between turns; stopped: no agent running.
        prompts (integer): How many prompts it's had.
        cli_id (string or None): The id claude has for it where that's another (a fork's: its transcript's named
            so).
        transcript (string or None): Where its transcript is on its host's machine (what's on the same machine
            reads it there rather than fetching a copy).
        archived (boolean): Archived.
        draft (boolean): A new chat not written in yet (a head leaves it out of its lists).
        written (integer or None): Its transcript's size as its own agent last left it (more since, with it
            stopped: another program's writing to it).
        subagents (number): The mtime of its subagents' newest file (0 for none: what a client's copy is checked
            against).
        commands (array of Command): What its agent is running; none once its agent's gone.
        cut (number or None): When the host process stopped its agent at a turn or asking (shutting down, a
            quit, a restart), so a head can offer to carry it on; null once a user message is sent to it.
        bot (BotListing or None): A Bot's; null for a chat.
        rotated (number or None): A Bot's: when it was last rotated (its conversation archived as a past thread,
            a new one begun), epoch seconds; null if it never has. A head's copy of its transcript is let go of
            as it changes.
        autofix (AutofixPull or None): Its Auto-fix: the pull request its host follows for it; null while it's
            off.
        acting (boolean): Whether its turn is at its tools: from a tool call's start till text starts after it,
            the turn ends or its agent goes; only while it's working, not asking.
        context (ContextUsage or None): How much of its context window its agent last said it's used, kept while
            its agent's stopped; null till it's said.
        on_phone (boolean): A Bot's browser is on a phone now.
        fork_from (string or None): The session it was forked from.
        spawned_by (string or None): The chat whose mesh tools started it (start's spawned_by), or the Bot whose
            task's run started it.
        task_asks (array of string): A Bot's: the uuids of the messages its tasks' runs have sent it as asks,
            each run waiting for input (asking) till the turn that message began ends. Empty for every other.
        task_run (TaskRunRef or None): The task's run that started it (start's task_run), on its Bot's host.
        desktop (boolean): Claude Desktop's: a chat in Claude Desktop's own list on its host's machine. Every
            app lists it in the Desktop group.
        taken_up (boolean): false for a Claude Desktop chat its host only lists: nothing of it runs on the host
            till it's written in, which takes it up (as adopt does). Missing for every other.
        elsewhere (boolean): Claude Desktop is writing it now (written in the last minute, not by the host's own
            agent): every app keeps it read-only while it is.
        start_key (string or None): Its start's start_key, if it was given one.
        account (Account or None): The subscription account its agent runs under on its host, fixed as it's made
            (the one the host's new chats take for its provider then, accounts' defaults, or as it's first
            written in), or the one picked for it since (accounts' use), from the moment it's picked; null for
            the CLI's own sign-in, or an account since removed (account_gone).
        account_gone (boolean): Its subscription account was removed from its host after it was picked for it:
            its agent can't start till another is picked.
        held (array of HeldMessage): Messages held for it, oldest first.
        senders (integer): Its sender store's revision (0 for none): what a head's copy is checked against.
        always_allow (array of string): The tools allowed whenever it asks for them, sorted.
        asks (array of Ask): What it's asking, not answered yet.
        ended (TurnEnd or None): Its last turn's end, as its host keeps it; null before one has ended (and
            always for a chat another chat's mesh tools, or a task's run, started).
        seen (TurnEnd or None): The last turn's end that's been seen: a head showed it on screen (seen), or its
            user wrote in it since. Ended and not seen: every head keeps its notification of it, and its dot,
            till it is; then every head takes them back.
    """
    _refs = {"commands": "Command", "bot": "BotListing", "autofix": "AutofixPull", "context": "ContextUsage",
             "task_run": "TaskRunRef", "account": "Account", "held": "HeldMessage", "asks": "Ask",
             "ended": "TurnEnd", "seen": "TurnEnd"}


class SignalRef(Obj):
    """A name Signal's glossary has.

        surface (string): As it's written.
        kind (string): What it names.
        canonical (string or None): What it is.
        status (string): Its status.
        summary (string or None): What's said of it.
        locations (array of object): Where it's mentioned: {role, path, line, note, rev, state, commit_sha,
            branch}.
        reason (string): Why it wasn't found, or how its matches were told apart.
        data (object): What was found (lookup.Ref.data): a commit's subject and branches, a pull request's
            state, a tool's inputs…
    """


class StoredSender(Obj):
    """Who a message to a session is from, as its host's sender store keeps it (by the message's uuid): a Sender, and
    what heads show of the message in place of its text, if the host says (shown).

        kind ('bot' | 'chat' | 'subagent' | 'host'): What it is.
        name (string): Its name.
        host (string): Its host's id.
        host_name (string): Its host's name.
        session (string): Its session's id, on its host.
        bot (string): A chat a task's run started (a subagent sender, its report of a turn): the session of the
            run's Bot, on its host.
        task (string): A task's run's: the id of its task, on its Bot's Tasks page.
        task_name (string): A chat a task's run started: its task's name, as the from line says it ("<task>
            run").
        run (string): A task's run's: the id of the run, on its Bot's Tasks page (its Bot: bot, else its
            session).
        over ('finished' | 'stopped' | 'failed'): A chat a task's run started: how the turn it reports ended
            (finished; stopped: interrupted or stopped; failed: an error, or its agent ended before its turn
            did).
        shown (string): What heads show of the message in place of its text: set by the host only (a held answer
            sent: its shown, its agent having been told more).
    """


class SubagentFile(Obj):
    """One of a session's subagents' files, from where the client's copy is.

        name (string): Its name.
        size (integer): Its size.
        mtime (integer or None): Its mtime (ns).
        offset (integer): Where data starts.
        next (integer): Where the next read starts.
        data (string): What's new of it, in whole lines (a meta.json whole).
    """


class Tab(Obj):
    """A tab of a Bot's browser.

        tab (string): Its id.
        title (string): Its title.
        url (string): Its address.
        loading (boolean): Loading.
        selected (boolean): In front.
        prompts (array of object): What it's waiting on an answer to.
    """


class Task(Obj):
    """One of a Bot's tasks (baudelib.tasks): a script its host runs, by hand or as a trigger fires, its form filled,
    its Bot asked when it can't decide or fails.

        id (string): Its id.
        name (string): Its name.
        runtime ('python' | 'shell' | 'bot'): How its script's run: Python 3, /bin/sh, or given to its Bot as a
            turn (a prompt).
        script (string or None): Its script (task's answer, and a set's; tasks' listing leaves it out).
        version (string or None): Its script's version: the first 12 hex of its SHA-256.
        form (array or None of TaskField): Its form's fields.
        triggers (array or None of TaskTrigger): What starts it.
        variables (array or None of TaskVariable): Its variables, each bound once (bindings), its script reading
            them (baudetask.vars).
        bindings (object or None): A variable's name: what it's bound to (one not here is unbound).
        warnings (object or None): A bound variable's name: why what it's bound to isn't there now (a model not
            served, a host out of reach, a pod gone), as the host last looked (the host's to say: not set).
        notify_failure (boolean or None): A run that fails is sent to its Bot, with its log.
        fallback (boolean or None): Notify on failure under its old name, for older heads: read when
            notify_failure isn't given, and sent as notify_failure is.
        notify_exit (boolean or None): A run that finishes, exit 0 and not stopped, is told to its Bot, with the
            end of its output; the Bot isn't asked to act.
        timeout_s (integer or None): The longest a run may take; it's stopped after.
        paused (boolean or None): Its triggers don't fire (it can still be run by hand).
        last (TaskRun or None): Its latest run.
        next (number or None): When a schedule of its fires next.
    """
    _refs = {"form": "TaskField", "triggers": "TaskTrigger", "variables": "TaskVariable", "last": "TaskRun"}


class TaskBinding(Obj):
    """What a task's variable is bound to: value, and what else its type has. Picked from what its host offers
    (task_choices), or typed in as any text.

        value (string): What it is: a model's value, a decider's value (what decide's model takes), a host's id
            or name, a context's, pod's, container's or database's name, or the text.
        label (string or None): What it's shown as (a model's name, a host's), if not its value.
        effort (string or None): A model's: its effort.
        host (string or None): A model's: the member of the mesh that serves it, by name (its provider), if it
            isn't this host.
        model (string or None): A decider's: the model it serves, as it said.
        context (string or None): A pod's: its Kubernetes context.
        namespace (string or None): A pod's: its namespace.
        container (string or None): A database's: the container it runs in.
    """


class TaskChoice(Obj):
    """One thing a task's variable could be bound to, as its host offers it (task_choices): its own models, the mesh's
    hosts, its kubeconfig's contexts, the workloads running in its local clusters, its Docker containers, the
    databases running on it (what ⌘K lists), its deciders and the mesh's.

        binding (TaskBinding): What it's bound to, picked.
        detail (string or None): Where it is, beside its name (a pod's context and namespace).
        efforts (array or None of string): A model's: the efforts it takes.
    """
    _refs = {"binding": "TaskBinding"}


class TaskField(Obj):
    """A field of a task's form: filled by hand, by a trigger's payload, or by its Bot.

        name (string): Its name, as the script reads it (letters, digits, _).
        label (string or None): What it's called on the form (its name, if none).
        type ('text' | 'number' | 'bool' | 'choice'): What it takes.
        choices (array or None of string): A choice's choices.
        default (any): Its value when nothing fills it.
        required (boolean or None): It must be filled for a run to start.
    """


class TaskLogLine(Obj):
    """A line of a run's log.

        at (number): When.
        kind ('out' | 'err' | 'say' | 'ask' | 'answer' | 'status' | 'agent' | 'warning'): What it is: the
            script's output or errors, a line it said, an ask of its Bot, the Bot's answer, the run's state
            changing, or what a chat it started said as its first turn ended (told its Bot too), or a bound
            variable not there as the run started.
        text (string): What it says.
        values (object or None): An answer's values, from a form.
        error (boolean or None): An ask's: it was an error handed over. An agent's: the chat failed, or was
            stopped.
        session (string): An agent's: the chat's id.
        over ('finished' | 'stopped' | 'failed'): An agent's: how the chat's turn ended (Sender's over).
        variable (string): A warning's: the variable's name.
    """


class TaskRun(Obj):
    """A task's run: what started it, what it was given, how it went (a run's record is its telemetry).

        id (string): Its id.
        task (string): The task's id.
        name (string or None): The task's name as it ran.
        version (string or None): The script's version it ran.
        trigger (string or None): The trigger that fired it (its id), or null: run by hand.
        kind ('manual' | 'schedule' | 'git_push'): What started it.
        payload (object or None): The trigger's event.
        inputs (object or None): Its form as filled.
        status ('running' | 'asking' | 'done' | 'failed' | 'stopped' | 'skipped'): How it is, or ended.
        started (number): When it started.
        offset (integer or None): How far ahead of UTC its host's clock was as it started (seconds): its times
            shown as they were there.
        ended (number or None): When it ended.
        seconds (number or None): How long it took.
        exit (integer or None): Its script's exit code.
        error (string or None): Why it failed or was skipped.
        skips (integer or None): A skipped run's: how many firings it stands for (its trigger fired again while
            the same run went on: one record, not one each).
        asks (integer or None): How many times it asked its Bot.
        handed (boolean or None): Its failure was handed to its Bot.
        chats (array or None of string): The chats it started on its Bot's host (start's task_run), by their
            ids, oldest first: they go on after it's over, their states as the sessions listing has them.
        command (string or None): What it runs, as a command line: its interpreter, then its script's path on
            its host; null: a bot task's, given its Bot as a turn.
        pid (integer or None): Its script's process id, as it runs.
        pgid (integer or None): Its script's process group, as it runs: what's left of it killed as its host
            starts again, if its host was killed as it ran.
    """


class TaskRunRef(Obj):
    """The task's run a chat was started by (Session's task_run), on its Bot's host.

        bot (string): The Bot's session id.
        task (string): The task's id.
        run (string): The run's id.
        name (string): The task's name as it ran.
    """


class TaskTrigger(Obj):
    """What starts a task (besides a run by hand): a schedule, or a git push to a branch of a repo on the host, seen by
    polling its head. Its events' payloads fill the task's form (fills).

        id (string): Its id.
        kind ('schedule' | 'git_push'): What it is.
        every (integer or None): A schedule's: minutes between runs.
        at (string or None): A schedule's: the host's local time it runs at, HH:MM.
        days (array or None of integer): A schedule's at's days, 0 Monday to 6 Sunday.
        next (number or None): A schedule's: when it fires next.
        repo (string or None): A git push's: the repo's folder on the host.
        branch (string or None): A git push's: the branch watched.
        poll_s (integer or None): A git push's: seconds between looks at the branch.
        seen (string or None): A git push's: the branch's head as last seen.
        fills (object or None): A form field's name: the payload's key that fills it (a field not named takes
            the payload's key of its own name).
        fired (number or None): When it last fired.
        error (string or None): Why it couldn't look or fire, last time.
    """


class TaskVariable(Obj):
    """A variable a task declares: a name its script reads it by (baudetask.vars), and a type, bound once for the task
    (its bindings), by its Bot or a person, unlike its form's inputs, which each run fills.

        name (string): Its name, as its row and the script say it ("Translation Model").
        type ('model' | 'decider' | 'host' | 'browser_host' | 'terminal_host' | 'kube_context' | 'pod' |
            'container' | 'database' | 'text'): What it's bound to: a model (with its effort), a decider
            (decide's), a host of the mesh (one to browse on, one to run terminals on), a Kubernetes context, a
            pod, a container, a database, or text. What's offered as it's bound is the host's own of that type;
            any text can be given all the same.
        description (string or None): What it's for, as its Bot said.
    """


class TelemetryDay(Obj):
    """A day's tokens, from the turns that ended in it.

        day (string): The day, YYYY-MM-DD, the host's time.
        tokens (integer): Tokens read and written.
        turns (integer): Turns that ended.
    """


class TelemetryEvent(Obj):
    """Something done in a chat, as an app saw it happen: kept by its machine's host as it's told (telemetry), read
    back with telemetry_read. A chat is named by its host's id and its session's id, so what every host keeps can be
    put together.

        at (number): When, in seconds since the epoch (to the millisecond).
        app ('host' | 'baude' | 'record' | 'phone'): What saw it: Baude, Record, the phone's app, or the chat's
            host itself (a turn's end, as it ran there: what tokens are added up from).
        kind ('chat_opened' | 'chat_front' | 'chat_left' | 'turn_sent' | 'turn_ended'): What happened: a chat
            opened in a window or screen; a chat came in front (its window or screen in front, showing it); it
            stopped being in front; a turn sent in it; a turn of it ended.
        host (string): The id of the host the chat is on.
        session (string): The chat's session id.
        seconds (number or None): A turn_ended's: how long the turn took.
        turn (string or None): A turn_ended's: the id of the turn's result, so a turn two apps saw end is
            counted once.
        model (string or None): A turn_ended's: the model it ran on.
        effort (string or None): A turn_ended's: the effort it ran at.
        tokens_in (integer or None): A turn_ended's: the tokens it read (input, cache reads and writes).
        tokens_out (integer or None): A turn_ended's: the tokens it wrote.
    """


class TelemetryLine(Obj):
    """A line of code line_profiler timed.

        file (string): Its file: under the checkout, relative to it.
        line (integer): Its line number.
        function (string): The function it's in.
        code (string or None): The line's text, stripped.
        hits (integer): Times it ran.
        seconds (number): Time spent on it.
    """


class TelemetryModel(Obj):
    """A model's turns, or a model and effort's.

        model (string): The model.
        effort (string or None): The effort (null: a model's turns at any effort).
        turns (integer): Turns that ended on it.
        tokens (integer): Their tokens, read and written.
    """


class TelemetryProc(Obj):
    """A process's use of its machine, in one sample.

        app ('host' | 'baude' | 'record' | 'emission' | 'multiplex' | 'phone'): Which: the host process, Baude,
            Record, Emission, Multiplex, or the phone's app (its host too).
        pid (integer): Its process id.
        cpu (number or None): Its CPU since the sample before, in percent of one core (null in its first
            sample).
        rss (integer or None): Its resident memory, in bytes.
    """


class TelemetryProfile(Obj):
    """What line_profiler timed in a process over a stretch (saved every 30 seconds while profiling, and as it's
    switched off).

        at (number): When the stretch ended, in seconds since the epoch.
        from (number): When it began.
        app ('host' | 'baude' | 'record'): The process profiled: the host's, Baude's or Record's.
        lines (array of TelemetryLine): Its lines that ran, the most time first (at most 200).
    """
    _refs = {"lines": "TelemetryLine"}


class TelemetryProfiling(Obj):
    """What a process is told to profile.

        app ('host' | 'baude' | 'record'): The process: the host's, Baude's or Record's.
        functions (array of string): What's profiled: functions (module.function, module.Class.method) and
            modules (every function in it); none: off.
        error (string or None): Why it isn't profiling what it's told to (a name not found; line_profiler not
            installed).
    """


class TelemetrySample(Obj):
    """A host's machine and its processes, sampled every few seconds while telemetry is on.

        at (number): When, in seconds since the epoch.
        load (array or None of number): The machine's load average over 1, 5 and 15 minutes (null where it has
            none: a phone).
        memory_total (integer or None): The machine's memory, in bytes.
        memory_used (integer or None): Its memory in use, in bytes (macOS: Activity Monitor's Memory Used, app
            memory, wired and compressed; Linux and a phone: the total less what's available to start new work
            without swapping).
        swap_total (integer or None): Its swap, in bytes (macOS grows it as it's needed; a phone's is zram); 0
            or null where it has none.
        swap_used (integer or None): Its swap in use, in bytes.
        memory_pressure (string or None): How pressed for memory the system says it is: "normal", "warning" or
            "critical" (macOS's memory pressure level; a phone's low memory: critical, else normal); null where
            it has no level (Linux).
        memory_stall (number or None): The share of the last 10 seconds, in percent, some task stalled waiting
            on memory (Linux's pressure stall information, some avg10); null where it has none.
        procs (array of TelemetryProc): The host process, each app on its machine that told it telemetry (Baude,
            Record, the phone's app), and each Emission and Multiplex running there (the host finds those
            itself).
    """
    _refs = {"procs": "TelemetryProc"}


class TelemetryState(Obj):
    """A host's telemetry: on or off, and what each process on its machine profiles.

        on (boolean): Events kept and the machine sampled.
        line_profiler (boolean): line_profiler is installed for the host's Python.
        profiling (array of TelemetryProfiling): Each process's: the host's, Baude's, Record's.
    """
    _refs = {"profiling": "TelemetryProfiling"}


class TelemetryUsage(Obj):
    """The turns that ended in a stretch (turn_ended events, each turn once), added up by the host.

        days (array of TelemetryDay): Each day with a turn, the earliest first.
        models (array of TelemetryModel): Each model, the most turns first.
        combos (array of TelemetryModel): Each model and effort together, the most turns first.
    """
    _refs = {"days": "TelemetryDay", "models": "TelemetryModel", "combos": "TelemetryModel"}


class TerminalInfo(Obj):
    """A terminal running on a host.

        id (string): Its id there, for attach.
        title (string): Its title.
        argv (array of string): What it runs.
        cwd (string): Where it started.
        columns (integer): Its width.
        rows (integer): Its height.
        viewers (integer): Connections attached to it now.
        running (boolean): Whether its program is still running.
    """


class Theme(Obj):
    """A theme as its host has it (its themes folder's <name>/: theme.css and tokens.json), worked out by the host for
    every app alike: the desktop apps draw its CSS over theirs and its colours, the phone its colours.

        name (string): Its name: its folder's.
        css (string): Its theme.css (GTK CSS, the desktop apps': drawn over all of theirs), empty if it has
            none.
        light (object): Each colour of schema/tokens' libadwaita and baude groups the theme makes other than
            Baude's own in light, by its token's path: #rrggbb, or #rrggbbaa where it's see-through. A colour
            made of another (baude.rule of libadwaita.borders) is here when that one's set.
        dark (object): Each colour of schema/tokens' libadwaita and baude groups the theme makes other than
            Baude's own in dark, by its token's path: #rrggbb, or #rrggbbaa where it's see-through. A colour
            made of another (baude.rule of libadwaita.borders) is here when that one's set.
        error (string): Why its files can't be used, in place of the rest.
    """


class ThemeFiles(Obj):
    """A theme's files as they're written: what's kept to put one back (an undo).

        css (string or None): theme.css's text, or null with none.
        tokens (object or None): tokens.json's: a resolver document (schema/tokens/baude.resolver.json's format,
            its sources written in it) whose sets go over Baude's tokens, then its theme modifier's light or
            dark context over Baude's light or dark; colours of the libadwaita and baude groups only. null with
            none.
    """


class ToolNames(Obj):
    """How a host's MCP tools are named, for its heads to show (mcp__<server>__<tool> as "Server / Tool"): what its MCP
    servers and their tools say they're called, and the names its claude says its servers are configured as.

        servers (object): Each server's title, by its name as it's put in its tools' names (each character not a
            letter, digit, _ or - made _): mesh: Mesh, claude_ai_Slack: Slack.
        tools (object): A tool's title, by its whole name, where its server gives one
            (mcp__emission__javascript_tool: Run JavaScript).
    """


class TurnEnd(Obj):
    """A turn's end, as its host keeps it: what every head's notification of it is known by, and what's seen of a chat
    is said in.

        uuid (string): The uuid of the message the turn ended with (its result's, as its agent said it; the
            host's own where it said none).
        size (integer): Its transcript's size as the turn ended: a head that shows that much of it shows the
            turn's end.
    """


class Update(Obj):
    """Its own updater has a newer version ready, and waits for those commands to end before it restarts onto it.

        to (string): The commit it'll restart onto.
        waiting (array of Running): The commands it waits for.
    """
    _refs = {"waiting": "Running"}


class UsageLimit(Obj):
    """One of a subscription's limits, as Claude Code's get_usage says it, or Codex's App Server's rate limits
    (account/rateLimits/read).

        name (string): Which limit (Five hour, Seven day…).
        key (string): The limit's own name in Claude Code's usage (five_hour, seven_day…); a Codex limit's, by
            its window's length (five_hour and seven_day as Claude's, else 2_hour, 3_day…).
        resets (number): When it resets, in seconds since 1970 (left out where the usage doesn't say).
        percent (number): How much of it is used, in percent.
    """


class UsageReading(Obj):
    """A subscription's usage as its host last read it: its limits and when.

        usage (array of UsageLimit): The limits as that read said them.
        read (number): When it was read, in seconds since 1970.
    """
    _refs = {"usage": "UsageLimit"}


class WaitTarget(Obj):
    """Another chat's answer a session's agent waits on.

        session (string): That chat.
        host (string or None): The member it's on; null for this host.
        kind ('subagent' | 'chat'): What it is.
        text (string or None): What it was asked.
        offset (integer): Its transcript's size as it was asked.
        until (number): When the wait ends, epoch seconds.
        title (string): The chat's title.
    """


class WatchSay(Obj):
    """What a Browser pane says back to the Emission it watches.

        ack (integer): A frame taken (its frame).
        max_w (integer): The pane's width.
        max_h (integer): The pane's height.
    """


class WatchState(Obj):
    """A watched Bot's browser as it is (emission's watch_state).

        profile (string or None): Its profile.
        agent (boolean): A Bot's (its tools drive it).
        view (array or None of integer): The page's size, in CSS pixels.
        selected (string or None): The tab in front.
        tabs (array of Tab): Its tabs.
        prompts (array of object): What's waiting on an answer: {id, tab, kind, ...} (a dialog's).
        downloads (array of object): Its last downloads: {name, path, error?}.
        now (PageNow or None): The tab in front.
        pointer (array of PointerEvent): What the Bot's tools last did with the mouse, its last 8, oldest first.
    """
    _refs = {"tabs": "Tab", "now": "PageNow", "pointer": "PointerEvent"}


class Where(Obj):
    """Somewhere wire.reach can go: this machine's host process, one listening on another socket, one over ssh, or a
    member of the mesh through this machine's.

        via ('local' | 'unix' | 'ssh' | 'mesh'): How it's reached.
        path (string): unix: its socket; mesh: the host process it's reached through.
        dest (string): ssh: where (an alias in ~/.ssh/config, a host, user@host); ssh runs HOST_COMMAND there.
        strict (boolean): ssh: its key must be known already.
        host (string): mesh: the member's id.
    """


class Accepted(Obj):
    """input's answer, given a req.

        session (string): The session's id.
    """


class AccessReply(Obj):
    """access's answer.

        state (AccessState): The host's rules now.
        session (string or None): The chat asked about.
        chat (AccessChat or None): That chat's own rules for managing it (null, or a side not there: the
            host's).
    """
    _refs = {"state": "AccessState", "chat": "AccessChat"}


class AccessSetReply(Obj):
    """access_set's answer: the rules as they are now.

        state (AccessState): The host's rules now.
        session (string or None): The chat asked about.
        chat (AccessChat or None): That chat's own rules for managing it (null, or a side not there: the
            host's).
    """
    _refs = {"state": "AccessState", "chat": "AccessChat"}


class AccountsReply(Obj):
    """accounts' answer: the accounts and defaults as they are after it, and whether the host has claude and what it
    lacks of each provider's program and each subscription's last read usage (with error, if it couldn't be done);
    usage's, the limits; sign_in's, each line Claude's sign-in writes (line alone), or Codex's state as it goes
    (login alone), then its end; callback's, the page codex's server answered (page alone), or error.

        what (string): What was asked.
        error (string): Why it couldn't be done, for the user as it is.
        accounts (array of Account): The host's accounts, by provider then name.
        defaults (object): The account each provider's new chats take: the one a chat's provider picker
            (accounts' use) last picked there, else the CLI's own sign-in.
        usage (array or None of UsageLimit): usage: the subscription's limits; for a Claude sign-in that can't
            be asked (a setup token's), as its chats' turns last said them (as_of); null where none can be said
            (an API key's, a Codex sign-in that isn't a ChatGPT plan's, a setup token's before its first turn:
            after_turn).
        as_of (number): usage: where its limits are as the subscription's chats' turns last said them (its sign-
            in can't be asked: a setup token's), when they last said them, in seconds since 1970; shown as "Last
            update <how long ago>" under them.
        after_turn (boolean): usage: true where its limits can only be said by its chats' turns (a setup
            token's) and none has said them yet (usage null).
        api_key (boolean): usage: true where the account is signed in with an API key (paid per use), so it has
            no subscription limits (usage null).
        line (string): sign_in (Claude's): a line the sign-in wrote (the page to open among them).
        login (ChatLogin): sign_in (Codex's): its state as it goes (login alone: signing_in, then with the page,
            and the device code's code), then on its end, signed_in or failed.
        page (CallbackPage): callback: what the Codex sign-in's server answered the address.
        cb (integer): callback: the request's cb, carried back.
        signed_in (boolean): sign_in: its end, signed in.
        claude_cli (boolean): Whether the host has claude: a Claude account there is a folder signed in to with
            claude auth login; without it, a token from claude setup-token.
        codex_usage (boolean): Whether the host reads a Codex subscription's usage (usage with provider codex,
            and last_usage's "codex"): a client shows Codex's CLI default card and Codex usage only where it
            does; an older host's usage read of "" is Claude's.
        missing (object): What the host lacks of a provider's program, by provider (codex: its Codex CLI, as
            deps says it); a provider that's there isn't in it. Shown in place of Add account.
        last_usage (object): Each Claude and Codex subscription's usage as the host last read it, by id ("" for
            Claude's CLI's own sign-in, "codex" for Codex's), where that read said its limits: a client shows it
            greyed till its own usage read answers.
        who (string or None): sign_in, signed in: who as, as claude auth status says (Codex's: the email its
            sign-in names).
    """
    _refs = {"accounts": "Account", "usage": "UsageLimit", "login": "ChatLogin", "page": "CallbackPage"}


class Activated(Obj):
    """activate's answer.

        any (boolean): There was one.
    """


class AgentStarted(Obj):
    """agent's answer.

        run (string): The run's id.
    """


class Allowed(Obj):
    """always_allow's answer.

        session (string): The session's id.
        tools (array of string): All of them now, sorted.
    """


class Archived(Obj):
    """archive's answer.

        session (string): The session's id.
    """


class Attached(Obj):
    """attach's answer.

        session (Session): The session, as it's listed now.
        asks (array of object): What it's asking, not answered yet: its agent's control requests (can_use_tool)
            as they came.
        login (ChatLogin or None): Its sign-in, if a turn failed for want of it and that's not been put right
            yet (ChatLogin): the sign-in card every head draws.
        writes (boolean): Whether the head may change the chat (Agent Management's write, the chat's own first):
            false, it only reads it, and answers none of its asks.
        draft (ChatDraft or None): Its draft, as its host keeps it (draft_save); null if none's been written, or
            the head may only read the chat.
    """
    _refs = {"session": "Session", "login": "ChatLogin", "draft": "ChatDraft"}


class AutofixReply(Obj):
    """autofix's answer: the chat's Auto-fix as it is now.

        session (string): The session's id.
        autofix (AutofixPull or None): The pull request it follows; null while it's off.
    """
    _refs = {"autofix": "AutofixPull"}


class BotPictureReply(Obj):
    """A Bot's picture.

        session (string): The session's id.
        picture (string or None): A PNG; null for none.
    """


class BotSetReply(Obj):
    """bot's answer.

        session (Session): The session, as it's listed now.
    """
    _refs = {"session": "Session"}


class BrowserOpened(Obj):
    """browser_open's answer: Emission's window is in front there.

        started (boolean): Whether an Emission was started for it (none had the profile).
    """


class Busy(Obj):
    """quit's if_idle: commands are running, so it goes on.

        sessions (array of string): The sessions running them.
        commands (array of Running): What they run.
    """
    _refs = {"commands": "Running"}


class CatalogueReply(Obj):
    """catalogue's answer.

        cwd (string or None): The folder asked for.
        items (array of object): lookup's items.
        busy (boolean): There's more to come (ask again, with wait).
        error (string): What's wrong.
    """


class ChatSizeReply(Obj):
    """chat_size's answer.

        session (string): The session's id.
        bytes (integer): Its transcript's size.
        messages (integer): Its messages: each prompt sent and each reply, a tool's result not counted.
        images (integer): The images in it.
    """


class CwdSet(Obj):
    """set_cwd's answer.

        session (string): The session's id.
        cwd (string): The folder, real.
    """


class DatabasesReply(Obj):
    """databases' answer: servers {servers, databases}; schema {tables, links, took}; rows and query {rows}.

        what (string): What was read.
        error (string): What's wrong.
        servers (array of DbServer): servers: the servers found.
        databases (object): servers: each server's (by container) databases.
        tables (array of DbTable): schema: its tables.
        links (array of DbLink): schema: their foreign keys.
        took (number): schema: seconds it took to read, 0 when it was kept from a read before.
        rows (array of object): rows: the first rows; query: its rows (QUERY_ROWS_MAX at most); each {column:
            value}, objects and lists as JSON text.
        missing (Dep): servers: no server was found and Docker isn't on the host: Databases shows this in its
            place.
    """
    _refs = {"servers": "DbServer", "tables": "DbTable", "links": "DbLink", "missing": "Dep"}


class Deactivated(Obj):
    """deactivate's answer.

        any (boolean): There was one.
    """


class Decided(Obj):
    """decide's answer: each question's, by name; or, unavailable, why the decider couldn't be asked (not running, not
    answering, not there, its host out of reach), the script's to deal with: nothing else is asked instead.

        decider (string): The decider asked, by its name.
        host (string): Its host's name.
        model (string): The model that answered.
        answers (object): Each question's answer, by name.
        latency_ms (number): How long it took its server.
        unavailable (string): Why it couldn't be asked.
    """


class DecidersReply(Obj):
    """deciders' answer: the host's own deciders (with their URLs), then the other members'.

        what ('list' | 'add' | 'remove' | 'test'): What was asked.
        deciders (array of Decider): The deciders.
        tested (Decider): add, test: the one looked at.
        error (string): Why it couldn't be done (a name taken, a URL that isn't one).
    """
    _refs = {"deciders": "Decider", "tested": "Decider"}


class Deleted(Obj):
    """delete's answer (and an event to those attached: nothing to carry on).

        session (string): The session's id.
        trashed (array of string): What was moved to the trash.
    """


class DepsReply(Obj):
    """deps' answer: each of the host's dependencies on its platform.

        deps (array of Dep): Each one.
    """
    _refs = {"deps": "Dep"}


class Dir(Obj):
    """list_dir's answer: its entries, folders first (.git left out), DIR_LIMIT at most.

        path (string): The folder.
        entries (array of DirEntry): Its entries.
        more (boolean): There are more.
        error (string): Why it can't be read.
    """
    _refs = {"entries": "DirEntry"}


class DockerEnd(Obj):
    """docker_follow's last answer: docker ended.

        code (integer): Its exit code.
        said (string): What it wrote, for a failure.
    """


class DockerReply(Obj):
    """docker's answer: contexts {contexts, sections}; list {columns, rows}; inspect {text, file}; logs {text}; exec
    {text, status}; start, stop, restart and remove {said}; fault when Docker can't be reached; missing when there's
    no docker.

        what (string): What was done.
        error (string): What's wrong with the request.
        fault (DockerFault): Docker can't be reached: why.
        contexts (array of DockerContext): contexts: docker's contexts, and Colima's and Podman's found besides.
        sections (array of DockerSection): contexts: the sidebar's categories.
        columns (array of DockerColumn): list: the page's columns.
        rows (array of DockerRow): list: the objects.
        text (string): inspect: what docker inspect says of it; logs: its last lines; exec: what the command
            wrote, its stdout and stderr together.
        file (string): inspect: what it is called, for its highlighting.
        said (string): start, stop, restart, remove: what docker said.
        status (integer): exec: the command's exit status (docker's, if it couldn't run it).
        missing (Dep): docker isn't on the host: the Docker view shows this in its place.
    """
    _refs = {"fault": "DockerFault", "contexts": "DockerContext", "sections": "DockerSection",
             "columns": "DockerColumn", "rows": "DockerRow", "missing": "Dep"}


class DockerText(Obj):
    """docker_follow logs: more of the log.

        text (string): The lines.
    """


class DraftSaved(Obj):
    """draft_save's answer.

        session (string): The session's id.
        draft (ChatDraft): Its draft as kept now.
    """
    _refs = {"draft": "ChatDraft"}


class EditReply(Obj):
    """edit_propose's, edit_revise's, edit_withdraw's and edit_decide's answer: the edit as it is now.

        edit (Edit): The edit.
        took_in (array of string): The edits a proposal reached into, taken back as it was revised to take them
            in: their ids.
    """
    _refs = {"edit": "Edit"}


class EditsReply(Obj):
    """edits's answer, and edit_adopt's (its edits: those taken in).

        edits (array of Edit): The edits waiting.
        decided (array of Edit): With all: those accepted, rejected or taken back, the last kept.
        stamp (string): The edits here as written last (info's edits).
    """
    _refs = {"edits": "Edit", "decided": "Edit"}


class EmissionFrame(Obj):
    """emission_watch's answer, as it goes: a frame of the page in front, or its state as it changes.

        frame (integer): Its number (ack it).
        w (integer): Its width.
        h (integer): Its height.
        jpeg (string): The frame.
        stride (integer): Its rows' length in bytes, in shared memory (shm).
        shm (string): The shared memory it's in (a pane on Emission's own machine), not jpeg.
        view (array or None of integer): The page's size, in CSS pixels.
        error (string): Emission said no: why.
        state (WatchState): The browser's state.
    """
    _refs = {"state": "WatchState"}


class EmissionReply(Obj):
    """The tool's answer.

        result (object): What Emission answered.
    """


class EmissionServing(Obj):
    """emission_serve's answer.

        bot (string): The Bot's id.
        on (boolean): Its requests come here.
    """


class EmissionWatchEnd(Obj):
    """emission_watch's last answer: the watch is over."""


class EmissionWatching(Obj):
    """emission_watch's answer: it's watched: emission_frame follows, many, till emission_watch_end."""


class Error(Obj):
    """Anything wrong, with the request's req. A request whose op the host doesn't take is answered with code
    unsupported_operation (error "unsupported operation"), and one whose fields aren't as the schema has them with
    code invalid_data_type (error "invalid data type"): the same from every host, what was wrong in detail. One a
    host's access rules refuse is answered with code access_denied (error "access denied"), denied naming the rule.

        error (string): What's wrong.
        code ('unsupported_operation' | 'invalid_data_type' | 'access_denied'): One of the schema's own
            refusals.
        detail (string): A refusal's: what was wrong (the op, or the field and why), for a person to read; code,
            not this, says which refusal it is.
        denied ('shell' | 'browser_read' | 'browser_write' | 'providers' | 'model_files' | 'spawn' |
            'manage_read' | 'manage_write' | 'kube_read' | 'kube_write' | 'docker_read' | 'docker_write' |
            'databases' | 'subscriptions_read' | 'subscriptions_write' | 'access_read' | 'access_write'):
            access_denied's: the rule refused (AccessRules'): a capability, or one side of one split into read
            and write.
        plain (boolean): Said for the user as it is.
        gone (GoneFolder): A session's folder gone and not made again.
        missing (Dep): What a chat's program needs that the host hasn't (claude, codex): error says what to
            install.
        session (string): memory's refusal: the session.
        text (string): memory's refusal: the memory as the Bot left it.
        seen (string or None): memory's refusal: what the user's accepted of it.
        mtime (number or None): memory's refusal: its mtime (ns; a phone's, seconds).
        path (string): memory's refusal: where it is.
    """
    _refs = {"gone": "GoneFolder", "missing": "Dep"}


class FeedbackFiled(Obj):
    """feedback's answer: filed.

        url (string): The issue.
        number (integer or None): Its number.
        repo (string): Where.
    """


class FeedbackTarget(Obj):
    """feedback's answer, given check: where it would be filed.

        repo (string): Where.
    """


class File(Obj):
    """read_file's answer.

        path (string): The path asked for.
        size (integer): Its size.
        truncated (boolean): Cut short.
        data (string): What it holds.
        error (string): Why it can't be read.
    """


class FixesReply(Obj):
    """fixes' answer: list {repo, items, can_write, locations}, or an error (gh's, or what was asked for); shot {data};
    the rest {}.

        what (string): What was done.
        error (string): What went wrong.
        repo (string or None): list: where feedback is read from (owner/name); null: GitHub isn't connected
            here.
        items (array of object): list: the issues and where each stands, in the order they're listed: {number,
            key, issue_location, title, body, author, url, created, updated (seconds), status (open, working,
            ready, merged, rejected, closed), thread [{author, at, text, mark {state, round, machine, shots
            [{label, before, after}]} or null, trusted}], rounds, working {machine, round, at} or null, here,
            error, pr {number, url, state, head, sha, cross} or null}.
        can_write (boolean): list: whether you can merge and fix on it.
        locations (array of string): list: the repositories the issues are on.
        data (string): shot: the image's PNG, base64.
    """


class GitReply(Obj):
    """git's answer: log {top, branch, commits, skip, more}; card {sha, parents, author, email, at, ct, committer,
    message, files, more_files, on, remotes, url, login}, or with github {sha, login, prs}; diff {sha, path, text,
    truncated}; page {top, folder, branches, picked, rev, mine, branch, parent, commits, own, theirs, revs, repo,
    every, fetched, found, more, made, heads, opened, links, prs}; pulls {repo, prs, gh, signed_in, pr, avatars}.

        what (string): What was read.
        error (string): What's wrong.
        top (string): log, page: the checkout's top folder.
        branch (Branch): log, page: its branch.
        commits (array of Commit): log, page: the commits.
        skip (integer): log: after how many.
        more (boolean): log, page: there are more.
        sha (string): card, diff: the commit.
        parents (array of string): card: its parents.
        author (string): card: its author.
        email (string): card: their email.
        at (integer): card: when, epoch seconds.
        committer (string): card: its committer.
        message (string): card: its message.
        files (array of array of any): card: [path, lines added, removed] (null for a binary file's).
        more_files (boolean): card: more files than listed.
        on (array of string): card: the local branches it's on.
        branches (array of Branch): page: the branches to choose from.
        path (string or None): diff: the file.
        text (string): diff: the diff.
        truncated (boolean): diff: cut short.
        folder (string): page: the folder asked about.
        picked (array of string): page: the branches shown.
        rev (string): page: the first branch shown.
        mine (string or None): page: the branch the chat's checkout has.
        parent (GitParent or None): page: the branch the one shown came from.
        own (array of string): page: the shown branch's own commits.
        theirs (array of string): page: the commits its parent has that it hasn't.
        revs (array of string): page: what the log was read from.
        repo (string or None): page, pulls: the GitHub repository, owner/name.
        every (boolean): page: every branch is shown.
        made (array of GitMade): page: the commits this chat made.
        heads (array of string): page: the branches this chat touched.
        opened (array of integer): page: the pull requests this chat opened.
        links (array of PullLink): page: the pull requests its replies name.
        prs (any): page, pulls: the repository's pull requests (null: not known). card: the commit's.
        fetched (boolean or None): page: whether the fetch went (null: none asked).
        found (GitFound or None): page: where the commit asked for is.
        gh (boolean): pulls: gh is there and not signed out.
        signed_in (boolean): pulls: gh is signed in.
        pr (PullInfo or None): pulls: the pull request of the branch asked.
        avatars (object): pulls: {email: picture URL}.
        remotes (array of string): card: the remote branches it's on.
        url (string or None): card: its page on GitHub.
        login (string or None): card: its author's GitHub login.
        ct (integer): card: when committed, epoch seconds.
        missing (Dep): git isn't on the host: the Git page shows this in its place.
    """
    _refs = {"branch": "Branch", "commits": "Commit", "branches": "Branch", "parent": "GitParent",
             "made": "GitMade", "links": "PullLink", "found": "GitFound", "pr": "PullInfo", "missing": "Dep"}


class HeldReply(Obj):
    """held's answer.

        session (string): The session's id.
    """


class HelloReply(Obj):
    """The host's answer to hello.

        server (Info): What the host is.
    """
    _refs = {"server": "Info"}


class InferData(Obj):
    """infer's and files's answer: some of its body.

        data (string): The bytes.
    """


class InferEnd(Obj):
    """infer's and files's answer's end."""


class InferHead(Obj):
    """infer's and files's answer's status and headers.

        status (integer): Its status.
        headers (object): Its headers.
    """


class Joined(Obj):
    """join's answer.

        host (HostHello): Who this is.
    """
    _refs = {"host": "HostHello"}


class KubeContexts(Obj):
    """kube_follow contexts' answer: the kubeconfig's contexts, as the follow starts and each time they change.

        contexts (array of KubeContext): The kubeconfig's contexts.
        where (string): The kubeconfig files, or what stands for none.
    """
    _refs = {"contexts": "KubeContext"}


class KubeEnd(Obj):
    """kube_follow's last answer: kubectl ended.

        code (integer): Its exit code.
        said (string): What it wrote, for a failure.
        fault (KubeFault): Why, when the cluster can't be read.
        removed (boolean): Its context went from the kubeconfig (its cluster was deleted, or made again under
            the same name).
    """
    _refs = {"fault": "KubeFault"}


class KubeReply(Obj):
    """kube's answer: contexts {contexts, where}; connect {sections, namespaces, nodes}; list {columns, rows, metrics,
    page}; yaml {text, file, refs}; owner {kind, name, resource}; summary {summary}; delete and apply {said}; logs
    {text}; exec {text, status}; fault when a cluster can't be read; removed when its context has gone from the
    kubeconfig.

        what (string): What was done.
        error (string): What's wrong with the request.
        fault (KubeFault): The cluster can't be read: why.
        removed (boolean): The context isn't in the kubeconfig (any more): nothing was read.
        contexts (array of KubeContext): contexts: the kubeconfig's.
        where (string): contexts: the kubeconfig files, or what stands for none.
        sections (array of KubeSection): connect: the sidebar's categories, each kind with whether the cluster
            serves it.
        namespaces (array of string): connect: the cluster's namespaces.
        nodes (integer): connect: how many nodes it has.
        page (KubePage): list of a definition: its kind's page.
        columns (array of KubeColumn): list: the page's columns on this cluster.
        rows (array of KubeRow): list: the objects.
        metrics (boolean or None): list: whether CPU and memory could be read (pods, nodes), or null for another
            kind.
        text (string): yaml: the YAML (a Secret's keys only, a Helm release's JSON); logs: the log; exec: what
            the command wrote, its stdout and stderr together.
        file (string): yaml: what it is called, for its highlighting.
        refs (array of KubeRef): yaml: the names in it that are other objects.
        kind (string): owner: the controlling kind, climbed up from a ReplicaSet or a Job.
        name (string): owner: its name.
        resource (string): owner: its kind's resource.
        summary (KubeSummary): summary: the stack's CPU and memory.
        said (string): delete, apply: what kubectl said.
        status (integer): exec: the command's exit status (kubectl's, if it couldn't run it).
        missing (Dep): kubectl isn't on the host: the Kubernetes view shows this in its place.
    """
    _refs = {"fault": "KubeFault", "contexts": "KubeContext", "sections": "KubeSection", "page": "KubePage",
             "columns": "KubeColumn", "rows": "KubeRow", "refs": "KubeRef", "summary": "KubeSummary",
             "missing": "Dep"}


class KubeRows(Obj):
    """kube_follow watch: the page's objects as they are now.

        rows (array of KubeRow): The objects.
        metrics (boolean or None): Whether CPU and memory could be read, or null.
        note (string): Why it can't be watched and is read every few seconds instead, or empty.
    """
    _refs = {"rows": "KubeRow"}


class KubeText(Obj):
    """kube_follow logs: more of the log.

        text (string): The lines.
    """


class Linking(Obj):
    """link's answer.

        where (Where): Where.
    """
    _refs = {"where": "Where"}


class LoginEnd(Obj):
    """login's last answer: claude auth login ended.

        code (integer or None): Its exit code; null if it couldn't be started.
        why (string): Why it couldn't be started.
        who (string or None): Signed in (code 0): who as, as claude auth status says it; null if it didn't say.
    """


class LoginSaid(Obj):
    """login: a line claude auth login wrote (the sign-in page's address among them).

        line (string): The line, as it wrote it.
    """


class MemoryReply(Obj):
    """A Bot's memory.

        session (string): The session's id.
        text (string): Its text.
        seen (string or None): What the user's accepted of it.
        mtime (number or None): Its mtime (ns; a phone's, seconds).
        path (string): Where it is, on the host.
    """


class MultiplexReply(Obj):
    """The tool's answer.

        result (object): What the terminals' process answered.
    """


class NotesReply(Obj):
    """notes's answer: listed {notebooks, ids}, or written {done, by}.

        notebooks (array of Notebook): Notebooks.
        ids (array of string): Every one's id, in order.
        done (array of string): The changes made.
        by (string): record: the Record running there made them.
        error (string): What's wrong.
    """
    _refs = {"notebooks": "Notebook"}


class Quitting(Obj):
    """quit's answer: it's stopping."""


class Relayed(Obj):
    """relay's answer.

        to (string): The member's id.
    """


class Relaying(Obj):
    """mcp_relay's answer."""


class Renamed(Obj):
    """rename's answer.

        session (Session): The session, as it's listed now.
    """
    _refs = {"session": "Session"}


class RestartPending(Obj):
    """restart's answer: the restart it keeps, as info's restart says it (it goes once nothing blocks it and nobody's
    holding it).

        restart (RestartState or None): Its restart, or null: none pending.
    """
    _refs = {"restart": "RestartState"}


class Rotated(Obj):
    """rotate's answer.

        session (Session): The Bot, as it's listed now: in its new conversation.
        thread (Session): Its past thread, as it's listed now: archived.
    """
    _refs = {"session": "Session", "thread": "Session"}


class SeenSet(Obj):
    """seen's answer.

        session (string): The session's id.
        seen (TurnEnd or None): The last turn's end seen now (null: none yet).
    """
    _refs = {"seen": "TurnEnd"}


class SendersReply(Obj):
    """Its sender store.

        session (string): The session's id.
        senders (object): By message uuid.
        rev (integer): Its revision.
    """


class SignalNamesReply(Obj):
    """signal_names's answer.

        glossary (string or None): The glossary's path; null for none.
        root (string or None): The checkout's top folder the locations' paths are relative to.
        refs (array of SignalRef): The names.
        error (string): What's wrong.
    """
    _refs = {"refs": "SignalRef"}


class Started(Obj):
    """start's answer, before the prompt's sent (attach: this connection's attached live to it first).

        session (Session): The session, as it's listed now.
        forked (boolean): Forked from fork_from.
    """
    _refs = {"session": "Session"}


class Stopped(Obj):
    """stop's answer.

        session (string): The session's id.
    """


class SubagentsReply(Obj):
    """Each one's transcript from where have says the client's copy is, in whole lines; its meta.json whole, if it's
    changed.

        session (string): The session's id.
        files (array of SubagentFile): The files.
        more (boolean): There's more, past limit; those it didn't reach aren't listed.
    """
    _refs = {"files": "SubagentFile"}


class TaskAnswer(Obj):
    """task_ask's answer: the Bot's.

        text (string): What it said.
        values (object or None): A form's values, as the Bot filled them.
    """


class TaskAnswered(Obj):
    """answer_task's answer: the ask answered, its run going on.

        session (string): The Bot's session id.
        run (string): The run's id.
        task (string): The run's task, by its name.
        values (object): A form's values, as they were taken (checked against its fields).
        text (string): The answer to an ask with no form.
    """


class TaskChoicesReply(Obj):
    """task_choices' answer.

        session (string): The session's id.
        type (string): The type asked for.
        choices (array of TaskChoice): What it could be bound to, best first.
    """
    _refs = {"choices": "TaskChoice"}


class TaskReply(Obj):
    """task's answer.

        session (string): The session's id.
        task (Task): The task, with its script.
        runs (array of TaskRun): Its latest runs, newest first.
        clock (Clock): The host's clock.
    """
    _refs = {"task": "Task", "runs": "TaskRun", "clock": "Clock"}


class TaskReported(Obj):
    """task_report's answer."""


class TaskRunReply(Obj):
    """task_run's and task_stop's answer.

        session (string): The session's id.
        run (TaskRun): The run.
        log (array of TaskLogLine): Its log (its last 2000 lines).
        bot (string or None): Its Bot's name.
    """
    _refs = {"run": "TaskRun", "log": "TaskLogLine"}


class TaskStarted(Obj):
    """run_task's answer: the run started.

        session (string): The session's id.
        run (TaskRun): The run.
    """
    _refs = {"run": "TaskRun"}


class TasksReply(Obj):
    """tasks' answer: a Bot's tasks (their scripts left out).

        session (string): The session's id.
        tasks (array of Task): Its tasks.
        clock (Clock): The host's clock.
        saved (string or None): The id of the task set, if one was.
    """
    _refs = {"tasks": "Task", "clock": "Clock"}


class TelemetryKept(Obj):
    """telemetry's answer: kept."""


class TelemetryReadReply(Obj):
    """telemetry_read's answer, each list the earliest first.

        host (string): The host's id.
        state (TelemetryState): Its telemetry state.
        events (array of TelemetryEvent): Its chat events.
        metrics (array of TelemetrySample): Its samples.
        profiles (array of TelemetryProfile): Its line profiles.
        usage (TelemetryUsage or None): Its turns added up.
    """
    _refs = {"state": "TelemetryState", "events": "TelemetryEvent", "metrics": "TelemetrySample",
             "profiles": "TelemetryProfile", "usage": "TelemetryUsage"}


class TelemetrySetReply(Obj):
    """telemetry_set's answer: the host's telemetry state now.

        state (TelemetryState): Its telemetry state.
    """
    _refs = {"state": "TelemetryState"}


class TerminalEnd(Obj):
    """The terminal's program has ended (or never started: said).

        status (integer or None): Its exit status (a signal's: minus its number).
        said (string): Why it couldn't start.
    """


class TerminalOpened(Obj):
    """The terminal's: what it is.

        id (string): Its id here, for attach.
        title (string): Its title.
        argv (array of string): What it runs.
        cwd (string): Where it started.
    """


class TerminalOut(Obj):
    """What the terminal's program wrote, as it came (its escapes too), base64.

        data (string): The bytes, base64.
    """


class TerminalsReply(Obj):
    """The terminals running on this host.

        terminals (array of TerminalInfo): Each one.
    """
    _refs = {"terminals": "TerminalInfo"}


class ThemeRemoved(Obj):
    """theme_remove's answer: what it had.

        before (ThemeFiles or None): Its files, or null if it wasn't there.
    """
    _refs = {"before": "ThemeFiles"}


class ThemeSaved(Obj):
    """theme_save's answer: the theme as it is now, and what it had before.

        theme (Theme): It, worked out.
        before (ThemeFiles or None): Its files before, or null if it's new.
    """
    _refs = {"theme": "Theme", "before": "ThemeFiles"}


class ThemesReply(Obj):
    """themes' answer: each theme, by name.

        themes (array of Theme): Each one, in order.
    """
    _refs = {"themes": "Theme"}


class TranscriptReply(Obj):
    """Whole lines of its transcript, as they are, byte for byte.

        session (string): The session's id.
        offset (integer): Where data starts.
        next (integer): Where the next read starts.
        size (integer): The transcript's size.
        data (string): The lines.
    """


class Unarchived(Obj):
    """unarchive's answer.

        session (string): The session's id.
    """


class Unlinked(Obj):
    """unlink's answer.

        any (boolean): There were any.
    """


class WaitsOnReply(Obj):
    """waits_on's answer.

        session (string): The session's id.
    """


TYPES = {"AccessChat": AccessChat, "AccessMember": AccessMember, "AccessRule": AccessRule,
         "AccessRules": AccessRules, "AccessState": AccessState, "Account": Account, "Ask": Ask,
         "AutofixPull": AutofixPull, "BotFields": BotFields, "BotListing": BotListing, "BotRef": BotRef,
         "Branch": Branch, "CallbackPage": CallbackPage, "ChatDraft": ChatDraft, "ChatLogin": ChatLogin,
         "Clock": Clock, "Command": Command, "Commit": Commit, "ContextCategory": ContextCategory,
         "ContextUsage": ContextUsage, "DbColumn": DbColumn, "DbLink": DbLink, "DbServer": DbServer,
         "DbTable": DbTable, "DecideQuestion": DecideQuestion, "Decider": Decider, "Decision": Decision, "Dep": Dep,
         "DirEntry": DirEntry, "DockerColumn": DockerColumn, "DockerContext": DockerContext,
         "DockerFault": DockerFault, "DockerPage": DockerPage, "DockerRow": DockerRow,
         "DockerSection": DockerSection, "Edit": Edit, "FoundLink": FoundLink, "GitFound": GitFound,
         "GitMade": GitMade, "GitParent": GitParent, "Gone": Gone, "GoneFolder": GoneFolder, "Head": Head,
         "HeldMessage": HeldMessage, "HostHello": HostHello, "Info": Info, "KubeColumn": KubeColumn,
         "KubeContainer": KubeContainer, "KubeContext": KubeContext, "KubeFault": KubeFault, "KubePage": KubePage,
         "KubeRef": KubeRef, "KubeRow": KubeRow, "KubeSample": KubeSample, "KubeSection": KubeSection,
         "KubeShare": KubeShare, "KubeShareColumn": KubeShareColumn, "KubeSummary": KubeSummary, "KubeUse": KubeUse,
         "Member": Member, "MemberInfo": MemberInfo, "MeshView": MeshView, "Notebook": Notebook, "OutLink": OutLink,
         "PageNow": PageNow, "PointerEvent": PointerEvent, "Provider": Provider, "ProviderModel": ProviderModel,
         "Proxy": Proxy, "PullInfo": PullInfo, "PullLink": PullLink, "Record": Record,
         "RestartBlocker": RestartBlocker, "RestartState": RestartState, "Running": Running, "Sender": Sender,
         "Session": Session, "SignalRef": SignalRef, "StoredSender": StoredSender, "SubagentFile": SubagentFile,
         "Tab": Tab, "Task": Task, "TaskBinding": TaskBinding, "TaskChoice": TaskChoice, "TaskField": TaskField,
         "TaskLogLine": TaskLogLine, "TaskRun": TaskRun, "TaskRunRef": TaskRunRef, "TaskTrigger": TaskTrigger,
         "TaskVariable": TaskVariable, "TelemetryDay": TelemetryDay, "TelemetryEvent": TelemetryEvent,
         "TelemetryLine": TelemetryLine, "TelemetryModel": TelemetryModel, "TelemetryProc": TelemetryProc,
         "TelemetryProfile": TelemetryProfile, "TelemetryProfiling": TelemetryProfiling,
         "TelemetrySample": TelemetrySample, "TelemetryState": TelemetryState, "TelemetryUsage": TelemetryUsage,
         "TerminalInfo": TerminalInfo, "Theme": Theme, "ThemeFiles": ThemeFiles, "ToolNames": ToolNames,
         "TurnEnd": TurnEnd, "Update": Update, "UsageLimit": UsageLimit, "UsageReading": UsageReading,
         "WaitTarget": WaitTarget, "WatchSay": WatchSay, "WatchState": WatchState, "Where": Where,
         "Accepted": Accepted, "AccessReply": AccessReply, "AccessSetReply": AccessSetReply,
         "AccountsReply": AccountsReply, "Activated": Activated, "AgentStarted": AgentStarted, "Allowed": Allowed,
         "Archived": Archived, "Attached": Attached, "AutofixReply": AutofixReply,
         "BotPictureReply": BotPictureReply, "BotSetReply": BotSetReply, "BrowserOpened": BrowserOpened,
         "Busy": Busy, "CatalogueReply": CatalogueReply, "ChatSizeReply": ChatSizeReply, "CwdSet": CwdSet,
         "DatabasesReply": DatabasesReply, "Deactivated": Deactivated, "Decided": Decided,
         "DecidersReply": DecidersReply, "Deleted": Deleted, "DepsReply": DepsReply, "Dir": Dir,
         "DockerEnd": DockerEnd, "DockerReply": DockerReply, "DockerText": DockerText, "DraftSaved": DraftSaved,
         "EditReply": EditReply, "EditsReply": EditsReply, "EmissionFrame": EmissionFrame,
         "EmissionReply": EmissionReply, "EmissionServing": EmissionServing, "EmissionWatchEnd": EmissionWatchEnd,
         "EmissionWatching": EmissionWatching, "Error": Error, "FeedbackFiled": FeedbackFiled,
         "FeedbackTarget": FeedbackTarget, "File": File, "FixesReply": FixesReply, "GitReply": GitReply,
         "HeldReply": HeldReply, "HelloReply": HelloReply, "InferData": InferData, "InferEnd": InferEnd,
         "InferHead": InferHead, "Joined": Joined, "KubeContexts": KubeContexts, "KubeEnd": KubeEnd,
         "KubeReply": KubeReply, "KubeRows": KubeRows, "KubeText": KubeText, "Linking": Linking,
         "LoginEnd": LoginEnd, "LoginSaid": LoginSaid, "MemoryReply": MemoryReply, "MultiplexReply": MultiplexReply,
         "NotesReply": NotesReply, "Quitting": Quitting, "Relayed": Relayed, "Relaying": Relaying,
         "Renamed": Renamed, "RestartPending": RestartPending, "Rotated": Rotated, "SeenSet": SeenSet,
         "SendersReply": SendersReply, "SignalNamesReply": SignalNamesReply, "Started": Started, "Stopped": Stopped,
         "SubagentsReply": SubagentsReply, "TaskAnswer": TaskAnswer, "TaskAnswered": TaskAnswered,
         "TaskChoicesReply": TaskChoicesReply, "TaskReply": TaskReply, "TaskReported": TaskReported,
         "TaskRunReply": TaskRunReply, "TaskStarted": TaskStarted, "TasksReply": TasksReply,
         "TelemetryKept": TelemetryKept, "TelemetryReadReply": TelemetryReadReply,
         "TelemetrySetReply": TelemetrySetReply, "TerminalEnd": TerminalEnd, "TerminalOpened": TerminalOpened,
         "TerminalOut": TerminalOut, "TerminalsReply": TerminalsReply, "ThemeRemoved": ThemeRemoved,
         "ThemeSaved": ThemeSaved, "ThemesReply": ThemesReply, "TranscriptReply": TranscriptReply,
         "Unarchived": Unarchived, "Unlinked": Unlinked, "WaitsOnReply": WaitsOnReply}
Obj._types.update(TYPES)


CLI_DEFAULT = "CLI default"  # (a subscription that's its CLI's own sign-in, as every app names it: schema/copy)


class Kube(Area):
    """A cluster's objects as Freelens lists them, read through the host's kubeconfig and kubectl: its contexts
    (contexts), whether a context is still there as the head knows it (check: answered removed, or with nothing
    else; kubectl isn't run), one connected to (connect), a kind's objects (list), an object's YAML (yaml), what
    controls an object (owner), a pod deleted (delete), objects applied from YAML (apply), a pod's log as it is now
    (logs), a command run in a pod's container (exec: no terminal, its output when it ends), the whole stack's CPU
    and memory (summary, a read as list is). Secrets' values are never read.

    Each method is one kube request (its what), to host: this one if none's given. Answers KubeReply.
    """

    def contexts(self, *, context=None, server=None, host=None):
        """kube's contexts: answers KubeReply {contexts, where}.

            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
        """
        return self._call("kube", {"what": "contexts", "context": context, "server": server}, host, KubeReply)

    def check(self, *, context=None, server=None, host=None):
        """kube's check: answers KubeReply.

            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
        """
        return self._call("kube", {"what": "check", "context": context, "server": server}, host, KubeReply)

    def connect(self, *, context=None, server=None, host=None):
        """kube's connect: answers KubeReply {sections, namespaces, nodes}.

            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
        """
        return self._call("kube", {"what": "connect", "context": context, "server": server}, host, KubeReply)

    def list(self, *, context=None, server=None, page=None, crd=None, selector=None, host=None):
        """kube's list: answers KubeReply {columns, rows, metrics, page}.

            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
            page (string): list, yaml: the kind's page, by its resource.
            crd (string): list, yaml: a custom resource's definition (plural.group) for a kind that isn't
                listed.
            selector (string): list: only the objects whose labels match this selector (kubectl's -l:
                app=web,tier!=cache); a Secret's or a Helm release's page lists every one.
        """
        return self._call("kube", {"what": "list", "context": context, "server": server, "page": page, "crd": crd,
                                   "selector": selector}, host, KubeReply)

    def yaml(self, *, context=None, server=None, page=None, crd=None, namespace=None, name=None, host=None):
        """kube's yaml: answers KubeReply {text, file, refs}.

            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
            page (string): list, yaml: the kind's page, by its resource.
            crd (string): list, yaml: a custom resource's definition (plural.group) for a kind that isn't
                listed.
            namespace (string): yaml, owner, delete, logs, exec: the object's namespace; apply: where objects
                that name none go.
            name (string): yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's).
        """
        return self._call("kube", {"what": "yaml", "context": context, "server": server, "page": page, "crd": crd,
                                   "namespace": namespace, "name": name}, host, KubeReply)

    def owner(self, *, context=None, server=None, namespace=None, name=None, kind=None, host=None):
        """kube's owner: answers KubeReply {kind, name, resource}.

            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
            namespace (string): yaml, owner, delete, logs, exec: the object's namespace; apply: where objects
                that name none go.
            name (string): yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's).
            kind (string): owner: the object's kind.
        """
        return self._call("kube", {"what": "owner", "context": context, "server": server, "namespace": namespace,
                                   "name": name, "kind": kind}, host, KubeReply)

    def summary(self, *, context=None, server=None, namespaces=None, since=None, fresh=None, host=None):
        """kube's summary: answers KubeReply {summary}.

            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
            namespaces (array of string): summary: the namespaces it's the totals of (every one when it's left
                out; none when it's empty).
            since (number): summary: the newest history sample the head has (its at): only later ones are sent
                (every one when it's left out).
            fresh (boolean): summary: read the cluster now (Refresh), not a read the host kept or the fault it
                holds while it waits to try a failing cluster again.
        """
        return self._call("kube", {"what": "summary", "context": context, "server": server,
                                   "namespaces": namespaces, "since": since, "fresh": fresh}, host, KubeReply)

    def delete(self, *, context=None, server=None, namespace=None, name=None, force=None, host=None):
        """kube's delete: answers KubeReply {said}.

            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
            namespace (string): yaml, owner, delete, logs, exec: the object's namespace; apply: where objects
                that name none go.
            name (string): yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's).
            force (boolean): delete: at once, not waiting for the pod to stop.
        """
        return self._call("kube", {"what": "delete", "context": context, "server": server, "namespace": namespace,
                                   "name": name, "force": force}, host, KubeReply)

    def apply(self, *, context=None, server=None, namespace=None, manifest=None, host=None):
        """kube's apply: answers KubeReply {said}.

            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
            namespace (string): yaml, owner, delete, logs, exec: the object's namespace; apply: where objects
                that name none go.
            manifest (string): apply: the objects, as YAML or JSON (kubectl apply -f -).
        """
        return self._call("kube", {"what": "apply", "context": context, "server": server, "namespace": namespace,
                                   "manifest": manifest}, host, KubeReply)

    def logs(self, *, context=None, server=None, namespace=None, name=None, container=None, previous=None,
             tail=None, host=None):
        """kube's logs: answers KubeReply {text}.

            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
            namespace (string): yaml, owner, delete, logs, exec: the object's namespace; apply: where objects
                that name none go.
            name (string): yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's).
            container (string): logs, exec: the container (the pod's default if none).
            previous (boolean): logs: its previous run's.
            tail (integer): logs: its last this many lines (the Kubernetes view's number if none).
        """
        return self._call("kube", {"what": "logs", "context": context, "server": server, "namespace": namespace,
                                   "name": name, "container": container, "previous": previous,
                                   "tail": tail}, host, KubeReply)

    def exec(self, *, context=None, server=None, namespace=None, name=None, container=None, command=None,
             timeout=None, host=None):
        """kube's exec: answers KubeReply {text, status}.

            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
            namespace (string): yaml, owner, delete, logs, exec: the object's namespace; apply: where objects
                that name none go.
            name (string): yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's).
            container (string): logs, exec: the container (the pod's default if none).
            command (array of string): exec: what's run, its argv (kubectl exec --, with no terminal and nothing
                on its stdin).
            timeout (number): exec: seconds it may run (60 if none; 600 at most): then it's stopped.
        """
        return self._call("kube", {"what": "exec", "context": context, "server": server, "namespace": namespace,
                                   "name": name, "container": container, "command": command,
                                   "timeout": timeout}, host, KubeReply)


class Docker(Area):
    """Docker's containers, images, volumes, networks and Compose projects on the host, read and acted on through its
    docker CLI: its contexts and the sidebar's categories (contexts), a category's objects (list), an object
    inspected (inspect), a container's or Compose project's log as it is now (logs), a command run in a running
    container (exec: no terminal, its output when it ends), and a container or Compose project started, stopped,
    restarted or removed (start, stop, restart, remove; an image, a volume or a network removed).

    Each method is one docker request (its what), to host: this one if none's given. Answers DockerReply.
    """

    def contexts(self, *, context=None, host=None):
        """docker's contexts: answers DockerReply {contexts, sections}.

            context (string): The context, by its name, as contexts lists it.
        """
        return self._call("docker", {"what": "contexts", "context": context}, host, DockerReply)

    def list(self, *, context=None, page=None, host=None):
        """docker's list: answers DockerReply {columns, rows}.

            context (string): The context, by its name, as contexts lists it.
            page (string): list, inspect, logs, exec and the actions: the category's page, by its key.
        """
        return self._call("docker", {"what": "list", "context": context, "page": page}, host, DockerReply)

    def inspect(self, *, context=None, page=None, id=None, format=None, host=None):
        """docker's inspect: answers DockerReply {text, file}.

            context (string): The context, by its name, as contexts lists it.
            page (string): list, inspect, logs, exec and the actions: the category's page, by its key.
            id (string): inspect, logs, exec and the actions: the object, by its id (a Compose project's, its
                name).
            format ('json' | 'yaml'): inspect: as JSON (the default) or YAML.
        """
        return self._call("docker", {"what": "inspect", "context": context, "page": page, "id": id,
                                     "format": format}, host, DockerReply)

    def start(self, *, context=None, page=None, id=None, host=None):
        """docker's start: answers DockerReply {said}.

            context (string): The context, by its name, as contexts lists it.
            page (string): list, inspect, logs, exec and the actions: the category's page, by its key.
            id (string): inspect, logs, exec and the actions: the object, by its id (a Compose project's, its
                name).
        """
        return self._call("docker", {"what": "start", "context": context, "page": page,
                                     "id": id}, host, DockerReply)

    def stop(self, *, context=None, page=None, id=None, host=None):
        """docker's stop: answers DockerReply {said}.

            context (string): The context, by its name, as contexts lists it.
            page (string): list, inspect, logs, exec and the actions: the category's page, by its key.
            id (string): inspect, logs, exec and the actions: the object, by its id (a Compose project's, its
                name).
        """
        return self._call("docker", {"what": "stop", "context": context, "page": page, "id": id}, host, DockerReply)

    def restart(self, *, context=None, page=None, id=None, host=None):
        """docker's restart: answers DockerReply {said}.

            context (string): The context, by its name, as contexts lists it.
            page (string): list, inspect, logs, exec and the actions: the category's page, by its key.
            id (string): inspect, logs, exec and the actions: the object, by its id (a Compose project's, its
                name).
        """
        return self._call("docker", {"what": "restart", "context": context, "page": page,
                                     "id": id}, host, DockerReply)

    def remove(self, *, context=None, page=None, id=None, host=None):
        """docker's remove: answers DockerReply {said}.

            context (string): The context, by its name, as contexts lists it.
            page (string): list, inspect, logs, exec and the actions: the category's page, by its key.
            id (string): inspect, logs, exec and the actions: the object, by its id (a Compose project's, its
                name).
        """
        return self._call("docker", {"what": "remove", "context": context, "page": page,
                                     "id": id}, host, DockerReply)

    def logs(self, *, context=None, page=None, id=None, tail=None, host=None):
        """docker's logs: answers DockerReply {text}.

            context (string): The context, by its name, as contexts lists it.
            page (string): list, inspect, logs, exec and the actions: the category's page, by its key.
            id (string): inspect, logs, exec and the actions: the object, by its id (a Compose project's, its
                name).
            tail (integer): logs: its last this many lines (the Docker view's number if none).
        """
        return self._call("docker", {"what": "logs", "context": context, "page": page, "id": id,
                                     "tail": tail}, host, DockerReply)

    def exec(self, *, context=None, page=None, id=None, command=None, timeout=None, host=None):
        """docker's exec: answers DockerReply {text, status}.

            context (string): The context, by its name, as contexts lists it.
            page (string): list, inspect, logs, exec and the actions: the category's page, by its key.
            id (string): inspect, logs, exec and the actions: the object, by its id (a Compose project's, its
                name).
            command (array of string): exec: what's run, its argv (docker exec, with no terminal and nothing on
                its stdin).
            timeout (number): exec: seconds it may run (60 if none; 600 at most): then it's stopped.
        """
        return self._call("docker", {"what": "exec", "context": context, "page": page, "id": id, "command": command,
                                     "timeout": timeout}, host, DockerReply)


class Databases(Area):
    """The running databases, read-only: their servers and databases (servers), one database's schema (schema), a
    table's first rows (rows), a query's rows (query: Postgres's, in a read-only transaction). Secret-looking
    columns are masked here.

    Each method is one databases request (its what), to host: this one if none's given. Answers DatabasesReply.
    """

    def servers(self, *, host=None):
        """databases's servers: answers DatabasesReply {servers, databases}."""
        return self._call("databases", {"what": "servers"}, host, DatabasesReply)

    def schema(self, *, container=None, database=None, host=None):
        """databases's schema: answers DatabasesReply {tables, links, took}.

            container (string): schema, rows, query: the server's container.
            database (string): schema, rows, query: the database.
        """
        return self._call("databases", {"what": "schema", "container": container,
                                        "database": database}, host, DatabasesReply)

    def rows(self, *, container=None, database=None, schema=None, table=None, host=None):
        """databases's rows: answers DatabasesReply {rows}.

            container (string): schema, rows, query: the server's container.
            database (string): schema, rows, query: the database.
            schema (string): rows: the table's schema.
            table (string): rows: the table.
        """
        return self._call("databases", {"what": "rows", "container": container, "database": database,
                                        "schema": schema, "table": table}, host, DatabasesReply)

    def query(self, *, container=None, database=None, sql=None, host=None):
        """databases's query: answers DatabasesReply {rows}.

            container (string): schema, rows, query: the server's container.
            database (string): schema, rows, query: the database.
            sql (string): query: one statement that reads (SELECT, WITH, VALUES, TABLE), run read-only; its rows
                come back as rows' do, secret-looking columns masked, QUERY_ROWS_MAX of them at most.
        """
        return self._call("databases", {"what": "query", "container": container, "database": database,
                                        "sql": sql}, host, DatabasesReply)


class Emission(Area):
    """A Bot's browser tools: an MCP server on stdin and stdout (as baudelib.meshtools is), run on the Bot's host. Each
    call drives the Bot's own Emission profile on a host of the mesh (this one if none's named): through that host's
    process (wire's emission op), which starts Emission there for the Bot when it isn't running. A Bot only ever
    reaches its own profile; the sites it's signed in to are the ones its owner signed in to there, or copied in.
    Each is called by the agent as mcp__emission__<tool>.

    Each method is one of the Bot's own emission tools, as its emission op takes it, to host: this one if none's
    given.
    """

    def tabs(self, *, sites=None, host=None):
        """Your browser's tabs (started if need be) and recent downloads; sites: the sites it's signed in to. A tab's
        id is the tabId the tools take (none: the front tab). Each host has its own browser and sign-ins. (the
        tabs_context tool)

            sites (boolean):
        """
        return self._tool("emission", {"op": "tabs", "sites": sites}, host)

    def open(self, *, url=None, host=None):
        """Open a tab (on url: an address or a search) and wait for it to load. (the tabs_create tool)

            url (string):
        """
        return self._tool("emission", {"op": "new_tab", "url": url}, host)

    def navigate(self, *, url=None, tab=None, host=None):
        """Go to url (an address, a search, or back, forward, reload) and wait for it to load. (the navigate tool)

            url (string, required):
            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "navigate", "url": url, "tab": tab}, host)

    def close(self, *, tab=None, host=None):
        """Close a tab. (the tabs_close tool)

            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "close_tab", "tab": tab}, host)

    def screenshot(self, *, tab=None, host=None):
        """A picture of the page, for what text can't show: layout, images, a canvas, how it looks. read_page, find and
        get_page_text read pages for less. Its pixels are click, hover, drag, scroll and zoom's x, y. (the
        screenshot tool)

            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "screenshot", "tab": tab}, host)

    def zoom(self, *, region=None, tab=None, host=None):
        """The page as it is now, at full detail, in region [x0, y0, x1, y1] of the last screenshot's pixels (which x,
        y stay in). (the zoom tool)

            region (array of number, required):
            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "zoom", "region": region, "tab": tab}, host)

    def read_page(self, *, tab=None, filter=None, ref=None, max_chars=None, host=None):
        """How to read a page: its elements on screen and a screen above and below (links, buttons, fields, headings;
        filter all: the rest, text too), each with its role, name and ref; refs stay the same between reads. ref_id:
        only that element's part. (the read_page tool)

            tab (string): tabs_context's
            filter ('interactive' | 'all'):
            ref (string):
            max_chars (number):
        """
        return self._tool("emission", {"op": "read_page", "tab": tab, "filter": filter, "ref": ref,
                                       "max_chars": max_chars}, host)

    def find(self, *, query=None, tab=None, host=None):
        """Elements anywhere on the page matching words (role, name, placeholder, id, link), with refs. (the find tool)

            query (string, required):
            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "find", "query": query, "tab": tab}, host)

    def text(self, *, tab=None, max_chars=None, host=None):
        """The page's text (its main part's, if it has one). (the get_page_text tool)

            tab (string): tabs_context's
            max_chars (number):
        """
        return self._tool("emission", {"op": "get_text", "tab": tab, "max_chars": max_chars}, host)

    def click(self, *, ref=None, x=None, y=None, tab=None, button=None, count=None, modifiers=None, host=None):
        """Click ref's element (its middle) or x, y, as a person would (x, y: the last screenshot's pixels, CSS ones
        before a tab's first); count 2: a double click. (the click tool)

            ref (string): ref_12: from read_page, find or scroll
            x (number):
            y (number):
            tab (string): tabs_context's
            button ('left' | 'right'):
            count (number):
            modifiers (array of 'shift' | 'ctrl' | 'alt' | 'cmd'):
        """
        return self._tool("emission", {"op": "click", "ref": ref, "x": x, "y": y, "tab": tab, "button": button,
                                       "count": count, "modifiers": modifiers}, host)

    def drag(self, *, path=None, tab=None, host=None):
        """Drag along path [[x, y], ...] (x, y: the last screenshot's pixels, CSS ones before a tab's first): select
        text, move a slider, drag into place. (the drag tool)

            path (array of array of number, required):
            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "drag", "path": path, "tab": tab}, host)

    def hover(self, *, ref=None, x=None, y=None, tab=None, host=None):
        """Move the mouse over ref's element or x, y (x, y: the last screenshot's pixels, CSS ones before a tab's
        first). (the hover tool)

            ref (string): ref_12: from read_page, find or scroll
            x (number):
            y (number):
            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "hover", "ref": ref, "x": x, "y": y, "tab": tab}, host)

    def type(self, *, text=None, ref=None, tab=None, host=None):
        """Type text, key by key, into the focused field (or ref's, focused first). (the type tool)

            text (string, required):
            ref (string): ref_12: from read_page, find or scroll
            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "type", "text": text, "ref": ref, "tab": tab}, host)

    def key(self, *, keys=None, tab=None, host=None):
        """Press keys in turn, a space between: Enter, Tab, Escape, Backspace, Delete, Up, Down, Left, Right, Home,
        End, PageUp, PageDown, a character, or a combination like cmd+a. (the key tool)

            keys (string, required):
            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "key", "keys": keys, "tab": tab}, host)

    def scroll(self, *, dy=None, dx=None, ref=None, x=None, y=None, tab=None, host=None):
        """Turn the mouse wheel over ref's element, x, y (x, y: the last screenshot's pixels, CSS ones before a tab's
        first) or the middle, by dy, dx CSS pixels (dy 600 if none; up is negative). Says what came into view, with
        refs. (the scroll tool)

            dy (number):
            dx (number):
            ref (string): ref_12: from read_page, find or scroll
            x (number):
            y (number):
            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "scroll", "dy": dy, "dx": dx, "ref": ref, "x": x, "y": y,
                                       "tab": tab}, host)

    def form_input(self, *, ref=None, value=None, tab=None, host=None):
        """Set ref's field: a select's option (value or text), a checkbox or radio (true, false), or a value. (the
        form_input tool)

            ref (string, required): ref_12: from read_page, find or scroll
            value (any, required):
            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "form_input", "ref": ref, "value": value, "tab": tab}, host)

    def javascript(self, *, text=None, tab=None, host=None):
        """Run text in the page as a REPL does: top-level await works, and the last expression's value comes back (no
        return needed). Its coordinates are CSS pixels, not the screenshot's. (the javascript_tool tool)

            text (string, required):
            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "javascript", "text": text, "tab": tab}, host)

    def console(self, *, tab=None, pattern=None, onlyErrors=None, limit=None, clear=None, host=None):
        """The page's console messages and errors, newest last: pattern, a regex they match; onlyErrors; clear: forget
        them after. (the read_console_messages tool)

            tab (string): tabs_context's
            pattern (string):
            onlyErrors (boolean):
            limit (number):
            clear (boolean):
        """
        return self._tool("emission", {"op": "console", "tab": tab, "pattern": pattern, "onlyErrors": onlyErrors,
                                       "limit": limit, "clear": clear}, host)

    def network(self, *, tab=None, urlPattern=None, limit=None, clear=None, host=None):
        """What the page loaded: each URL, its kind, status, time and size; urlPattern: words in the URL; clear: forget
        them after. (the read_network_requests tool)

            tab (string): tabs_context's
            urlPattern (string):
            limit (number):
            clear (boolean):
        """
        return self._tool("emission", {"op": "network", "tab": tab, "urlPattern": urlPattern, "limit": limit,
                                       "clear": clear}, host)

    def resize(self, *, width=None, height=None, tab=None, host=None):
        """Make the page's viewport width by height CSS pixels. (the resize_window tool)

            width (number, required):
            height (number, required):
            tab (string): tabs_context's
        """
        return self._tool("emission", {"op": "resize", "width": width, "height": height, "tab": tab}, host)

    def prompts(self, *, host=None):
        """What pages wait on you to answer: an alert, confirm or prompt, a site's sign-in box, files to upload. (the
        prompts tool)
        """
        return self._tool("emission", {"op": "prompts"}, host)

    def answer_prompt(self, *, id=None, accept=None, text=None, user=None, password=None, files=None, host=None):
        """Answer prompt id: accept (false: Cancel), text for a prompt, user and password for a sign-in, files (paths
        on that host) for an upload. (the answer_prompt tool)

            id (string):
            accept (boolean):
            text (string):
            user (string):
            password (string):
            files (array of string):
        """
        return self._tool("emission", {"op": "answer_prompt", "id": id, "accept": accept, "text": text,
                                       "user": user, "password": password, "files": files}, host)


class Terminal(Area):
    """A Bot's terminal tools: an MCP server on stdin and stdout (as baudelib.emissiontools is), run on the Bot's host.
    Each call reaches the terminals of a host of the mesh (this one if none's named) through that host's process
    (wire's multiplex op): Multiplex's own (multiplex.py, started there for open if it isn't running) and the shells
    Baude's and Record's Kubernetes view has open in pods. The terminal glows round its edges while the Bot's at it.
    Each is called by the agent as mcp__multiplex__<tool>.

    Each method is one of the Bot's own multiplex tools, as its multiplex op takes it, to host: this one if none's
    given.
    """

    def list(self, *, query=None, host=None):
        """The terminals open on a host: Multiplex's tabs and the Kubernetes view's pod shells, the user's and yours,
        each with its id, title, folder, command and size. query: only those whose title, folder, command or text
        (screen and scrollback) has all its words, with the lines that do. (the list_terminals tool)

            query (string):
        """
        return self._tool("multiplex", {"op": "list", "query": query}, host)

    def open(self, *, command=None, cwd=None, title=None, host=None):
        """A new tab in Multiplex (its window opened if it isn't): command run by your shell there, or the shell itself
        if none; in cwd. Answers once it's drawn its first output, with its screen. (the open_terminal tool)

            command (string):
            cwd (string):
            title (string):
        """
        return self._tool("multiplex", {"op": "open", "command": command, "cwd": cwd, "title": title}, host)

    def read(self, *, terminal=None, lines_above=None, host=None):
        """What's on a terminal's screen now and where its cursor is; lines_above: that many lines of scrollback above
        the screen too. (the read_terminal tool)

            terminal (string, required): list_terminals' id
            lines_above (number):
        """
        return self._tool("multiplex", {"op": "read", "terminal": terminal, "lines_above": lines_above}, host)

    def type(self, *, terminal=None, text=None, enter=None, host=None):
        """Type text into a terminal, as if typed (a newline is Enter); enter: press Enter after it. Answers with its
        screen once output stops for a moment (wait_for for longer). (the send_text tool)

            terminal (string, required): list_terminals' id
            text (string, required):
            enter (boolean):
        """
        return self._tool("multiplex", {"op": "type", "terminal": terminal, "text": text, "enter": enter}, host)

    def keys(self, *, terminal=None, keys=None, host=None):
        """Press keys in a terminal in turn, a space between: Enter, Tab, Escape, Backspace, Delete, Up, Down, Left,
        Right, Home, End, PageUp, PageDown, F1–F12, a character, or a combination like ctrl+c, ctrl+d, alt+b,
        shift+Tab. Answers with its screen once output stops for a moment. (the send_keys tool)

            terminal (string, required): list_terminals' id
            keys (string, required):
        """
        return self._tool("multiplex", {"op": "key", "terminal": terminal, "keys": keys}, host)

    def wait(self, *, terminal=None, pattern=None, quiet=None, timeout=None, host=None):
        """Wait till pattern (a regex) shows on a terminal's screen, or its output's been quiet for quiet seconds (2 if
        no pattern), or its program ends, or timeout seconds (30; 120 at most) pass: which it was, and the screen.
        (the wait_for tool)

            terminal (string, required): list_terminals' id
            pattern (string):
            quiet (number):
            timeout (number):
        """
        return self._tool("multiplex", {"op": "wait", "terminal": terminal, "pattern": pattern, "quiet": quiet,
                                        "timeout": timeout}, host)

    def close(self, *, terminal=None, host=None):
        """End a terminal's program (as closing its tab would) and close it. (the close_terminal tool)

            terminal (string, required): list_terminals' id
        """
        return self._tool("multiplex", {"op": "close", "terminal": terminal}, host)


class Ops(Area):
    """Every request a host takes, as the schema has it: one method each, named as its op, its fields its keyword
    arguments, to host (this one if none's given). What a request answers with more than once (a terminal's output,
    a log followed) is a connection's own: these give its first answer only.
    """

    def access(self, *, session=None, host=None):
        """access (Access): A host's access rules, and a chat's own if session's given (refused from a member
        access_read doesn't allow).

            session (string or None): A chat of the host's: its own rule for managing it too.
        """
        return self._call("access", {"session": session}, host, AccessReply)

    def access_set(self, *, capability=None, session=None, rule=None, host=None):
        """access_set (AccessSet): One of a host's access rules set (capability: the rule's name), or a chat's own rule
        for managing it (session; capability manage_read or manage_write; rule null: the host's again). Refused
        (access_denied) from a member the access_write rule doesn't allow.

            capability (any): The rule's name (AccessRules'): a chat's, manage_read or manage_write.
            session (string or None): A chat of the host's: its own rule.
            rule (AccessRule or None): The rule (null, for a chat: the host's).
        """
        return self._call("access_set", {"capability": capability, "session": session,
                                         "rule": rule}, host, AccessSetReply)

    def accounts(self, *, what=None, provider=None, id=None, session=None, name=None, token=None, home=None,
                 folder=None, code=None, method=None, url=None, cb=None, host=None):
        """accounts (Accounts): The host's Claude and Codex subscription accounts (hostaccounts): list them; add one
        (Claude: name and an existing signed-in config folder, or name and token; Codex: name and an existing
        signed-in home, or name and token); rename one (id, name); replace a token account's token (id, token);
        remove one (id); use another for a chat (session, id; "" for the CLI's own sign-in: its agent started again
        under it, resuming the chat, once its turn's over; kept as the one the host's new chats on its provider's
        models take, defaults); read a subscription's usage (id; "" for the CLI's own sign-in of provider, Claude's
        where none is said); sign in (sign_in: provider and name for a new account, or id to sign in to one again;
        Claude's runs claude auth login with the account's own folder, its lines coming as they're written, the page
        to open among them; Codex's codex login (method: the browser's, by default, or the device code's), its state
        coming as it goes (login: the page to open, and the device code's code to enter there); then its end; the
        connection's going stops it); pass a code the sign-in page showed to the sign-in this connection runs (code:
        no answer of its own); pass the address a browser ended on after a Codex browser sign-in's page to the one
        this connection runs (callback: url; answered with the page codex's server gave, or error). Each other is
        answered with accounts.

            what ('list' | 'add' | 'rename' | 'token' | 'remove' | 'use' | 'usage' | 'sign_in' | 'code' |
                'callback'): What to do.
            provider ('claude' | 'codex'): add, sign_in (a new account): whose. usage with id "": whose CLI's
                own sign-in (Claude's where left out).
            id (string): rename, token, remove, use, usage, sign_in: the account ("" for the CLI's own sign-in,
                use and usage only).
            session (string): use: the chat whose account it is, one of the host's sessions whose model is the
                account's provider's. sign_in (a Claude account's) and token: the chat it's for, whose sign-in
                (ChatLogin) its host moves as it goes, as a login request's, if the asker may read the chat
                (Agent Management's read).
            name (string): add, rename, sign_in (a new account): its name.
            token (string): add (Claude, Codex), token: what signs the account in, pasted: Claude's a long-lived
                token from claude setup-token (sk-ant-oat…) or an Anthropic API key (sk-ant-api…); Codex's an
                OpenAI API key (sk-…), written to a new CODEX_HOME's auth.json as codex login --with-api-key
                writes it. Kept on the host only, owner-only; never said back.
            home (string): add (Codex): an existing CODEX_HOME on the host, signed in with ChatGPT and keeping
                its credentials in a file.
            folder (string): add (Claude): an existing Claude config folder on the host, signed in (claude auth
                status says so, run with it).
            code (string): code: what the sign-in page showed, pasted.
            method ('browser' | 'device'): sign_in, Codex's: browser (the default: codex login, its page sending
                the browser back to the host's 127.0.0.1) or device (codex login --device-auth).
            url (string): callback: the address the browser ended on (its path and query are what's passed on).
            cb (integer): callback: a number of the asker's pick, carried back on its answer (one sign-in can
                have several callbacks waiting).
        """
        return self._call("accounts", {"what": what, "provider": provider, "id": id, "session": session,
                                       "name": name, "token": token, "home": home, "folder": folder, "code": code,
                                       "method": method, "url": url, "cb": cb}, host, AccountsReply)

    def activate(self, *, id=None, where=None, host=None):
        """activate (Activate): A member taken up again.

            id (string): Its id.
            where (Where): Where a link goes.
        """
        return self._call("activate", {"id": id, "where": where}, host, Obj)

    def agent(self, *, model=None, mode=None, effort=None, resume=None, system_prompt=None, persist=None,
              append_system_prompt=None, servers=None, cwd=None, sessions=None, host=None):
        """agent (Agent): A model of this machine's, run here by its program (apple-claude, pi-claude) as an agent for
        a chat elsewhere (agentproxy), its tools there.

            model (string): Its model here.
            mode (string or None): Its permission mode.
            effort (string or None): Its effort.
            resume (string or None): The session it resumes.
            system_prompt (string or None): Its system prompt.
            persist (boolean): Its session's kept.
            append_system_prompt (string or None): More for its system prompt (a Bot's: who it is, its memory).
            servers (array of string): Its MCP servers, run on the caller's side.
            cwd (string): The chat's folder, on the caller's side.
            sessions (object): pi's session's files, for pi to take it up from.
        """
        return self._call("agent", {"model": model, "mode": mode, "effort": effort, "resume": resume,
                                    "system_prompt": system_prompt, "persist": persist,
                                    "append_system_prompt": append_system_prompt, "servers": servers, "cwd": cwd,
                                    "sessions": sessions}, host, Obj)

    def always_allow(self, *, session=None, tools=None, add=None, host=None):
        """always_allow (AlwaysAllow): Tools allowed whenever a session's agent asks for them, a head attached or not
        (kept in memory).

            session (string): The session's id.
            tools (array of string): These in place of any there were.
            add (array of string): These added to them.
        """
        return self._call("always_allow", {"session": session, "tools": tools, "add": add}, host, Obj)

    def answer_task(self, *, session=None, run=None, answer=None, text=None, host=None):
        """answer_task (AnswerTask): An ask of one of a Bot's tasks' runs (task_ask) answered: the Bot's answer_task
        tool's, or anyone's who may manage the Bot. Taken only for an ask of that run that's waiting on that Bot;
        the run goes on at once. Refused, with what's wrong, if it doesn't fit the ask's form.

            session (string): The Bot's session id: the one the ask's waiting on.
            run (string): The asking run's id.
            answer (object): An ask with a form: its fields' values, by name.
            text (string): An ask with no form: the answer.
        """
        return self._call("answer_task", {"session": session, "run": run, "answer": answer,
                                          "text": text}, host, Obj)

    def archive(self, *, session=None, host=None):
        """archive (Archive): A session archived: its agent stopped, and it's listed only to those subscribed with
        archived.

            session (string): The session's id.
        """
        return self._call("archive", {"session": session}, host, Obj)

    def attach(self, *, session=None, live=None, answers=None, app_tools=None, always_allow=None, env=None,
               host=None):
        """attach (Attach): This connection attached to a session.

            session (string): The session's id.
            live (boolean): claude, exited and deleted as they come.
            answers (boolean): (default true; kept for the connection, its last attach's or start's) false for a
                head that won't answer what the session asks: it isn't counted as there to answer.
            app_tools (boolean): The session's app_tools (start's) carried to this connection from now.
            always_allow (array of string): Tools added to those allowed whenever it asks.
            env (object): As start's.
        """
        return self._call("attach", {"session": session, "live": live, "answers": answers, "app_tools": app_tools,
                                     "always_allow": always_allow, "env": env}, host, Obj)

    def autofix(self, *, session=None, on=None, host=None):
        """autofix (Autofix): A chat's Auto-fix: on, its branch's open pull request found (gh) and followed; off; or as
        it is, on left out. Refused, saying why, if the branch has no open pull request or gh can't ask GitHub.

            session (string): The session's id.
            on (boolean): On or off.
        """
        return self._call("autofix", {"session": session, "on": on}, host, AutofixReply)

    def bot(self, *, session=None, bot=None, host=None):
        """bot (Bot): A session made a Bot, or its name or picture changed; bot null, a chat again (its memory and
        picture kept). Its agent's told who it is from its next start.

            session (string): The session's id.
            bot (BotFields or None): The Bot.
        """
        return self._call("bot", {"session": session, "bot": bot}, host, Obj)

    def bot_picture(self, *, session=None, host=None):
        """bot_picture (BotPicture): A Bot's picture.

            session (string): The session's id.
        """
        return self._call("bot_picture", {"session": session}, host, BotPictureReply)

    def browser_open(self, *, host=None):
        """browser_open (BrowserOpen): Emission's window on the host's own screen, on its Default profile (☰ →
        Browser): the one that has it brought to the front, else one started.
        """
        return self._call("browser_open", {}, host, Obj)

    def catalogue(self, *, cwd=None, wait=None, host=None):
        """catalogue (Catalogue): What @ lists for one of its sessions' folders, made there (lookup.items: its files,
        branches, Kubernetes objects, what's running on that machine, its pull requests and issues): what there is,
        at once; waited for while it's made if there's none yet, or with wait.

            cwd (string): The folder.
            wait (boolean): Wait for it.
        """
        return self._call("catalogue", {"cwd": cwd, "wait": wait}, host, CatalogueReply)

    def chat_size(self, *, session=None, host=None):
        """chat_size (ChatSize): How big a chat is, worked out by its host from its transcript as it is now (never kept
        with telemetry).

            session (string): The session's id.
        """
        return self._call("chat_size", {"session": session}, host, ChatSizeReply)

    def close(self, *, t=None, r=None, error=None, host=None):
        """close (Close): A tunnel closed, from either end; or (agent) the program's stdin closed.

            t (integer): The tunnel, by the number of the end that opened it (this end's).
            r (integer): The tunnel, by the number of the end that opened it (the other end's).
            error (string): Why.
        """
        return self._call("close", {"t": t, "r": r, "error": error}, host, Obj)

    def databases(self, *, what=None, container=None, database=None, schema=None, table=None, sql=None, host=None):
        """databases (Databases): The running databases, read-only: their servers and databases (servers), one
        database's schema (schema), a table's first rows (rows), a query's rows (query: Postgres's, in a read-only
        transaction). Secret-looking columns are masked here.

            what ('servers' | 'schema' | 'rows' | 'query'): What to read.
            container (string): schema, rows, query: the server's container.
            database (string): schema, rows, query: the database.
            schema (string): rows: the table's schema.
            table (string): rows: the table.
            sql (string): query: one statement that reads (SELECT, WITH, VALUES, TABLE), run read-only; its rows
                come back as rows' do, secret-looking columns masked, QUERY_ROWS_MAX of them at most.
        """
        return self._call("databases", {"what": what, "container": container, "database": database,
                                        "schema": schema, "table": table, "sql": sql}, host, DatabasesReply)

    def deactivate(self, *, id=None, where=None, host=None):
        """deactivate (Deactivate): A member deactivated here: its links let go of and not made, its joins and tunnels
        refused.

            id (string): Its id.
            where (Where): Where a link goes.
        """
        return self._call("deactivate", {"id": id, "where": where}, host, Obj)

    def decide(self, *, decider=None, state=None, questions=None, images=None, run=None, token=None, host=None):
        """decide (Decide): A decision asked of one of the host's deciders, or a member's through it
        (baudetask.decide): the state, and each question about it by name. Answered with decided once it's asked. A
        task's script asks on its host's own socket with its run's token; a member, as its access rules let it use
        the host's providers.

            decider (string or None): Which (a Decider's value: one of this host's by its id or name, another
                member's as <member>/<its id or name>); none: this host's first.
            state (string or object or array): Text, or structured data the decider renders as it does text.
            questions (object): The questions, by name.
            images (array of string): Base64 images, part of the state.
            run (string): A task's script's run (BAUDE_TASK_RUN).
            token (string): Its run's token (BAUDE_TASK_TOKEN).
        """
        return self._call("decide", {"decider": decider, "state": state, "questions": questions, "images": images,
                                     "run": run, "token": token}, host, Obj)

    def deciders(self, *, what=None, name=None, url=None, id=None, host=None):
        """deciders (Deciders): The host's deciders (Settings → Dependencies, a task's decider variable): list them,
        each with whether it answers, and the mesh's other members' as their records say; add one (name, url: a
        Strands Decider's server's base, http://127.0.0.1:8000), looked at as it's added; remove one (id); test one
        (id): looked at now. Each is answered with deciders.

            what ('list' | 'add' | 'remove' | 'test'): What to do (list where left out).
            name (string): add: its name.
            url (string): add: where it's served.
            id (string): remove, test: which.
        """
        return self._call("deciders", {"what": what, "name": name, "url": url, "id": id}, host, DecidersReply)

    def delete(self, *, session=None, host=None):
        """delete (Delete): A session gone: its agent stopped and waited for, its transcript and the folder beside it
        (its subagents'), pi's own copy of it, and a Bot's folder moved to the trash; then it's gone from the list.
        One whose files can't be moved stays: error, saying why.

            session (string): The session's id.
        """
        return self._call("delete", {"session": session}, host, Obj)

    def deps(self, *, host=None):
        """deps (Deps): The programs and libraries this host's features and its machine's apps need (schema/deps), each
        there or not, with the checks of what's installed (a kubeconfig's contexts, gh signed in, Ollama running).
        """
        return self._call("deps", {}, host, DepsReply)

    def detach(self, *, session=None, host=None):
        """detach (Detach): This connection detached from a session (an ephemeral one its head's done with dropped at
        once).

            session (string): The session's id.
        """
        return self._call("detach", {"session": session}, host, Obj)

    def docker(self, *, what=None, context=None, page=None, id=None, format=None, tail=None, command=None,
               timeout=None, host=None):
        """docker (Docker): Docker's containers, images, volumes, networks and Compose projects on the host, read and
        acted on through its docker CLI: its contexts and the sidebar's categories (contexts), a category's objects
        (list), an object inspected (inspect), a container's or Compose project's log as it is now (logs), a command
        run in a running container (exec: no terminal, its output when it ends), and a container or Compose project
        started, stopped, restarted or removed (start, stop, restart, remove; an image, a volume or a network
        removed).

            what ('contexts' | 'list' | 'inspect' | 'start' | 'stop' | 'restart' | 'remove' | 'logs' | 'exec'):
                What to do.
            context (string): The context, by its name, as contexts lists it.
            page (string): list, inspect, logs, exec and the actions: the category's page, by its key.
            id (string): inspect, logs, exec and the actions: the object, by its id (a Compose project's, its
                name).
            format ('json' | 'yaml'): inspect: as JSON (the default) or YAML.
            tail (integer): logs: its last this many lines (the Docker view's number if none).
            command (array of string): exec: what's run, its argv (docker exec, with no terminal and nothing on
                its stdin).
            timeout (number): exec: seconds it may run (60 if none; 600 at most): then it's stopped.
        """
        return self._call("docker", {"what": what, "context": context, "page": page, "id": id, "format": format,
                                     "tail": tail, "command": command, "timeout": timeout}, host, DockerReply)

    def docker_follow(self, *, what=None, context=None, page=None, id=None, host=None):
        """docker_follow (DockerFollow): A container's or a Compose project's log, followed (logs): one a connection,
        the answers docker_text till docker_end or the connection goes (docker is stopped then).

            what ('logs'): What to follow.
            context (string): The context, by its name.
            page (string): The category's page, by its key: a container's or a Compose project's.
            id (string): The container's id, or the Compose project's name.
        """
        return self._call("docker_follow", {"what": what, "context": context, "page": page, "id": id}, host, Obj)

    def draft_save(self, *, session=None, text=None, head=None, host=None):
        """draft_save (DraftSave): A chat's draft written (a change to the chat: Agent Management's write), kept by its
        host as the newest whatever was there (the last write wins), and told every other head attached to it
        (draft_changed). Sent a moment after typing stops, as the chat's left, and at once as the box is emptied by
        sending.

            session (string): The session's id.
            text (string): What's written in its box now (empty: nothing, as once it's sent).
            head (string or None): The box writing it, an id of the head's own pick (one each window or chat
                page): carried in the draft as it's told, so the box knows its own save.
        """
        return self._call("draft_save", {"session": session, "text": text, "head": head}, host, Obj)

    def edit_adopt(self, *, edits=None, host=None):
        """edit_adopt (EditAdopt): Edits waiting on this host's notebooks that another machine's Record kept (Meta's,
        from before edits were the notebook host's), handed to the host once, each with its own id, by and t. One
        whose id the host has (waiting or decided), or whose notebook it hasn't, is left out. Answered with edits:
        those taken in.

            edits (array of Edit): The edits, each pending.
        """
        return self._call("edit_adopt", {"edits": edits}, host, Obj)

    def edit_decide(self, *, notebook=None, id=None, accept=None, host=None):
        """edit_decide (EditDecide): A waiting edit accepted or rejected (the user's, in Record). Accepted: the one
        asking has put its new text in the page in its passage's place already (its own notes write, sent before
        this); the host keeps what became of it. Answered with edit.

            notebook (string): The notebook: its id, or its name (any case).
            id (string): The edit's id.
            accept (boolean): true: accepted; false: rejected.
        """
        return self._call("edit_decide", {"notebook": notebook, "id": id, "accept": accept}, host, Obj)

    def edit_propose(self, *, notebook=None, page=None, old_text=None, new_text=None, near=None, by=None,
                     host=None):
        """edit_propose (EditPropose): An edit proposed to a page of a notebook here, for the user to accept or reject:
        old_text replaced with new_text, nothing written to the page. old_text is found once (or the one nearest
        near) in the page as it reads with the edits waiting on it applied; an empty one adds new_text at the page's
        end. Text one of those edits puts in or replaces: that edit is revised to take this in (any others it
        reaches taken back), and is by by now. Answered with edit.

            notebook (string): The notebook: its id, or its name (any case).
            page (string): The page: its name (Parent:Child for one under another), any case, or its own name
                past its parents'.
            old_text (string): The text it replaces, as the page has it; empty or left out: added at the page's
                end.
            new_text (string): What it puts in.
            near (integer): A place in the page's text: of old_text there more than once, the one nearest it (a
                passage selected, say).
            by (string): Who's proposing it, by name (Meta, an agent's own name): kept with the edit, and shown
                with it.
        """
        return self._call("edit_propose", {"notebook": notebook, "page": page, "old_text": old_text,
                                           "new_text": new_text, "near": near, "by": by}, host, Obj)

    def edit_revise(self, *, notebook=None, id=None, new_text=None, old_text=None, by=None, host=None):
        """edit_revise (EditRevise): A waiting edit changed: what it puts in, and what it replaces if old_text is given
        (found once in the page, not in another waiting edit's passage). It stays one edit, by by now; new_text the
        same as what it replaces takes it back. Answered with edit.

            notebook (string): The notebook: its id, or its name (any case).
            id (string): The edit's id.
            new_text (string): What it puts in now.
            old_text (string): What it replaces now; left out: as it was.
            by (string): Who's proposing it, by name (Meta, an agent's own name): kept with the edit, and shown
                with it.
        """
        return self._call("edit_revise", {"notebook": notebook, "id": id, "new_text": new_text,
                                          "old_text": old_text, "by": by}, host, Obj)

    def edit_withdraw(self, *, notebook=None, id=None, host=None):
        """edit_withdraw (EditWithdraw): A waiting edit taken back. Answered with edit.

            notebook (string): The notebook: its id, or its name (any case).
            id (string): The edit's id.
        """
        return self._call("edit_withdraw", {"notebook": notebook, "id": id}, host, Obj)

    def edits(self, *, notebook=None, page=None, all=None, host=None):
        """edits (Edits): The edits waiting on the pages of the notebooks here (one notebook's, one page's, if given),
        page by page, each as its page has its passage now. Answered with edits.

            notebook (string): Only this notebook's: its id, or its name.
            page (string): Only this page's (with notebook).
            all (boolean): The decided ones kept here too (decided).
        """
        return self._call("edits", {"notebook": notebook, "page": page, "all": all}, host, EditsReply)

    def emission(self, *, bot=None, default=None, call=None, host=None):
        """emission (Emission): A Bot's browser tool's request (baudelib.emissiontools) to the Emission that has the
        Bot's own profile here, started for it if none has (or to the phone showing it).

            bot (BotRef): The Bot.
            default (boolean): The call is to Emission's own window here on its Default profile instead of a
                Bot's (☰ → Browser from another host, watched): what a Browser pane does (tabs, navigate,
                new_tab, close_tab, select_tab, click, hover, drag, type, key, scroll, answer_prompt), nothing
                else.
            call (object): The tool's request.
        """
        return self._call("emission", {"bot": bot, "default": default, "call": call}, host, EmissionReply)

    def emission_answer(self, *, id=None, result=None, error=None, host=None):
        """emission_answer (EmissionAnswer): A phone's answer to an emission_call.

            id (string): The call's id.
            result (object): What the browser answered.
            error (string): What went wrong.
        """
        return self._call("emission_answer", {"id": id, "result": result, "error": error}, host, Obj)

    def emission_serve(self, *, bot=None, on=None, urls=None, note=None, host=None):
        """emission_serve (EmissionServe): A phone showing a Bot of this host's browser (on), or not now: while it is,
        the Bot's browser tools' requests here are carried to it (emission_call).

            bot (string): The Bot's id.
            on (boolean): Showing it.
            urls (array of string): off: the pages it had open.
            note (string): off: what it hadn't told the Bot yet.
        """
        return self._call("emission_serve", {"bot": bot, "on": on, "urls": urls, "note": note}, host, Obj)

    def emission_watch(self, *, bot=None, default=None, max_w=None, max_h=None, host=None):
        """emission_watch (EmissionWatch): A Bot's browser here watched (Baude's Browser pane): one a connection.

            bot (BotRef): The Bot.
            default (boolean): Emission's own window here watched instead of a Bot's, on its Default profile (☰
                → Browser from another host): started (on this host's screen) if none has the profile; checked
                for browser_write.
            max_w (integer): The pane's width.
            max_h (integer): The pane's height.
        """
        return self._call("emission_watch", {"bot": bot, "default": default, "max_w": max_w,
                                             "max_h": max_h}, host, Obj)

    def emission_watch_say(self, *, line=None, host=None):
        """emission_watch_say (EmissionWatchSay): What the pane says back: a frame taken, its new size.

            line (WatchSay): What it says.
        """
        return self._call("emission_watch_say", {"line": line}, host, Obj)

    def feedback(self, *, note=None, text=None, context=None, check=None, host=None):
        """feedback (Feedback): Feedback a head with no gh of its own wrote (a phone's), filed as an issue by this
        host's gh where Send Feedback files it, once for each note.

            note (string): The note's id.
            text (string): What it says.
            context (array of string): Context, under it.
            check (boolean): Only where it would go: feedback_target.
        """
        return self._call("feedback", {"note": note, "text": text, "context": context, "check": check}, host, Obj)

    def files(self, *, method=None, path=None, headers=None, host=None):
        """files (Files): One of this machine's models' files, or where they are (modelfiles.serve), read-only.

            method (string): Its method.
            path (string): Its path.
            headers (object): Its headers.
        """
        return self._call("files", {"method": method, "path": path, "headers": headers}, host, Obj)

    def fixes(self, *, what=None, item=None, text=None, path=None, host=None):
        """fixes (Fixes): Ongoing Feedback, read and acted on with this host's gh, as Baude's Ongoing Feedback does:
        list, shot (path), merge, discard, reopen, reply (text), fix, stop; the item is as list gave it.

            what ('list' | 'shot' | 'merge' | 'discard' | 'reopen' | 'reply' | 'fix' | 'stop'): What to do.
            item (object): merge, discard, reopen, reply, fix, stop: the issue, as list gave it.
            text (string): reply: what to ask for.
            path (string): shot: an image's path, as a round's shot names it
                (<key>/<round>/<n>-<before|after>.png).
        """
        return self._call("fixes", {"what": what, "item": item, "text": text, "path": path}, host, FixesReply)

    def git(self, *, cwd=None, what=None, rev=None, limit=None, skip=None, sha=None, path=None, session=None,
            revs=None, follow=None, every=None, first_parent=None, fetch=None, then=None, repo=None, head=None,
            people=None, fresh=None, github=None, host=None):
        """git (Git): A session's folder's git: log (the host's own checkout's too, for a changelog), card (sha), diff
        (sha, path?), page (the Git page's whole read, fetching first and asking gh if asked), pulls (GitHub's pull
        requests, asked of gh).

            cwd (string): The folder.
            what ('log' | 'card' | 'diff' | 'page' | 'pulls'): What to read.
            rev (string): log: from where (default HEAD).
            limit (integer): log: how many. page: at least how many commits.
            skip (integer): log: after how many.
            sha (string): card, diff: the commit.
            path (string): diff: one file's only.
            session (string): page: the chat whose transcript says what it touched.
            revs (array of string): page: the branches picked. log: what to read, a ^sha allowed.
            follow (boolean): page: the branches the chat touched.
            every (boolean): page, log: every branch, remote branch and tag.
            first_parent (boolean): log: only the commits made on the branches themselves.
            fetch (boolean): page: git fetch first, GitHub read afresh.
            then (string): page: a commit to find, from a link.
            repo (string): pulls: owner/name, else the folder's own.
            head (string): pulls: a branch whose pull request is wanted.
            people (boolean): pulls: GitHub's pictures of the repository's authors.
            fresh (boolean): pulls: read afresh.
            github (boolean): card: GitHub's part only (the author's login, its pull requests).
        """
        return self._call("git", {"cwd": cwd, "what": what, "rev": rev, "limit": limit, "skip": skip, "sha": sha,
                                  "path": path, "session": session, "revs": revs, "follow": follow, "every": every,
                                  "first_parent": first_parent, "fetch": fetch, "then": then, "repo": repo,
                                  "head": head, "people": people, "fresh": fresh, "github": github}, host, GitReply)

    def held(self, *, session=None, id=None, send=None, host=None):
        """held (HeldOp): A message held for a session dropped, or sent as input would send it, from its sender.

            session (string): The session's id.
            id (string): The held message's id.
            send (boolean): Sent, not dropped.
        """
        return self._call("held", {"session": session, "id": id, "send": send}, host, HeldReply)

    def in_(self, *, line=None, host=None):
        """in (In): A line for an agent's program's stdin.

            line (string): The line.
        """
        return self._call("in", {"line": line}, host, Obj)

    def infer(self, *, provider=None, method=None, path=None, headers=None, body=None, host=None):
        """infer (Infer): An HTTP request to one of this machine's model providers (an OpenAI-compatible server's: path
        after its base_url), its answer as it comes.

            provider (string): The provider's id.
            method (string): Its method.
            path (string): Its path.
            headers (object): Its headers.
            body (string): Its body.
        """
        return self._call("infer", {"provider": provider, "method": method, "path": path, "headers": headers,
                                    "body": body}, host, Obj)

    def input(self, *, session=None, msg=None, as_agent=None, as_meta=None, as_=None, host=None):
        """input (Input): A stream-json message for the session's agent (started on it, with --resume, if it isn't
        running). A user message without a uuid is given one.

            session (string): The session's id.
            msg (object): The message.
            as_agent (string): From an agent outside the apps (baude-mcp), by its name, not the session's user:
                said as from a chat of that name, as as_meta is.
            as_meta (boolean): From a head's Meta (its mesh tools, which run in no chat), not the session's
                user: said as from a chat named Meta.
            as (string): The id of one of this host's chats whose mesh tools send it: it's from that chat, not
                the session's user (said_by, Senders).
        """
        return self._call("input", {"session": session, "msg": msg, "as_agent": as_agent, "as_meta": as_meta,
                                    "as": as_}, host, Obj)

    def kill(self, *, host=None):
        """kill (Kill): An agent's program stopped."""
        return self._call("kill", {}, host, Obj)

    def kube(self, *, what=None, context=None, server=None, page=None, crd=None, selector=None, namespaces=None,
             since=None, fresh=None, namespace=None, name=None, kind=None, force=None, manifest=None,
             container=None, previous=None, tail=None, command=None, timeout=None, host=None):
        """kube (Kube): A cluster's objects as Freelens lists them, read through the host's kubeconfig and kubectl: its
        contexts (contexts), whether a context is still there as the head knows it (check: answered removed, or with
        nothing else; kubectl isn't run), one connected to (connect), a kind's objects (list), an object's YAML
        (yaml), what controls an object (owner), a pod deleted (delete), objects applied from YAML (apply), a pod's
        log as it is now (logs), a command run in a pod's container (exec: no terminal, its output when it ends),
        the whole stack's CPU and memory (summary, a read as list is). Secrets' values are never read.

            what ('contexts' | 'check' | 'connect' | 'list' | 'yaml' | 'owner' | 'summary' | 'delete' | 'apply'
                | 'logs' | 'exec'): What to do.
            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
            page (string): list, yaml: the kind's page, by its resource.
            crd (string): list, yaml: a custom resource's definition (plural.group) for a kind that isn't
                listed.
            selector (string): list: only the objects whose labels match this selector (kubectl's -l:
                app=web,tier!=cache); a Secret's or a Helm release's page lists every one.
            namespaces (array of string): summary: the namespaces it's the totals of (every one when it's left
                out; none when it's empty).
            since (number): summary: the newest history sample the head has (its at): only later ones are sent
                (every one when it's left out).
            fresh (boolean): summary: read the cluster now (Refresh), not a read the host kept or the fault it
                holds while it waits to try a failing cluster again.
            namespace (string): yaml, owner, delete, logs, exec: the object's namespace; apply: where objects
                that name none go.
            name (string): yaml, owner, delete, logs, exec: the object's name (logs, exec: the pod's).
            kind (string): owner: the object's kind.
            force (boolean): delete: at once, not waiting for the pod to stop.
            manifest (string): apply: the objects, as YAML or JSON (kubectl apply -f -).
            container (string): logs, exec: the container (the pod's default if none).
            previous (boolean): logs: its previous run's.
            tail (integer): logs: its last this many lines (the Kubernetes view's number if none).
            command (array of string): exec: what's run, its argv (kubectl exec --, with no terminal and nothing
                on its stdin).
            timeout (number): exec: seconds it may run (60 if none; 600 at most): then it's stopped.
        """
        return self._call("kube", {"what": what, "context": context, "server": server, "page": page, "crd": crd,
                                   "selector": selector, "namespaces": namespaces, "since": since, "fresh": fresh,
                                   "namespace": namespace, "name": name, "kind": kind, "force": force,
                                   "manifest": manifest, "container": container, "previous": previous, "tail": tail,
                                   "command": command, "timeout": timeout}, host, KubeReply)

    def kube_follow(self, *, what=None, context=None, server=None, page=None, crd=None, namespace=None, pod=None,
                    container=None, previous=None, host=None):
        """kube_follow (KubeFollow): The kubeconfig's contexts followed (contexts: kube_contexts, sent again whenever
        tools add or delete clusters), a pod's log followed (logs), or a kind's list watched (watch): one a
        connection, the answers kube_contexts, kube_text and kube_rows till the connection goes (kubectl is stopped
        then), or till the context goes from the kubeconfig (kube_end, removed).

            what ('contexts' | 'logs' | 'watch'): What to follow: the kubeconfig's contexts (kube_contexts, as
                they change), a pod's log, or a kind's list.
            context (string): The kubeconfig context.
            server (string): The context's server as the head knows it (KubeContext's): a context of that name
                with another server is another cluster, and the request is answered removed.
            page (string): watch: the kind's page, by its resource.
            crd (string): watch: a custom resource's definition.
            namespace (string): logs: the pod's namespace.
            pod (string): logs: the pod.
            container (string): logs: the container.
            previous (boolean): logs: its previous run's log, not followed.
        """
        return self._call("kube_follow", {"what": what, "context": context, "server": server, "page": page,
                                          "crd": crd, "namespace": namespace, "pod": pod, "container": container,
                                          "previous": previous}, host, Obj)

    def link(self, *, where=None, host=None):
        """link (Link): A link made from this host process to where, kept and made again whenever it drops, and used
        both ways: the mesh.

            where (Where): Where (not local or mesh).
        """
        return self._call("link", {"where": where}, host, Obj)

    def list_dir(self, *, path=None, host=None):
        """list_dir (ListDir): A folder on the host: any on a computer's (a mesh member may start a session in any of
        them); on a phone's, one in its agents' folders.

            path (string): Its absolute path.
        """
        return self._call("list_dir", {"path": path}, host, Obj)

    def login(self, *, args=None, session=None, host=None):
        """login (Login): claude auth login run on the host (a chat's claude signed in again there): one a connection,
        its answers login_said as it says each line, then login_end; the connection's going stops it. session: the
        chat it's for, whose sign-in (ChatLogin) its host moves as it goes and tells every head attached to it
        (login_state).

            args (array of string): claude auth login's arguments (--console, --sso, --email <address>).
            session (string or None): The chat it signs in for, if any: ignored (the sign-in runs, for no chat)
                if the asker may not read it (Agent Management's read).
        """
        return self._call("login", {"args": args, "session": session}, host, Obj)

    def login_code(self, *, code=None, host=None):
        """login_code (LoginCode): The code the sign-in page showed, passed to the connection's claude auth login
        (login). No answer: what it says next comes as login_said or login_end.

            code (string): The code, as pasted.
        """
        return self._call("login_code", {"code": code}, host, Obj)

    def mcp(self, *, conn=None, line=None, host=None):
        """mcp (Mcp): A line for an MCP server, or from it.

            conn (integer): Its relay's number.
            line (string): The line.
        """
        return self._call("mcp", {"conn": conn, "line": line}, host, Obj)

    def mcp_close(self, *, conn=None, host=None):
        """mcp_close (McpClose): An MCP server's gone, on either side.

            conn (integer): Its relay's number.
        """
        return self._call("mcp_close", {"conn": conn}, host, Obj)

    def mcp_line(self, *, line=None, host=None):
        """mcp_line (McpLine): A line for a relayed MCP server, or from it.

            line (string): The line.
        """
        return self._call("mcp_line", {"line": line}, host, Obj)

    def mcp_relay(self, *, run=None, name=None, token=None, host=None):
        """mcp_relay (McpRelay): One of an agent's MCP servers (a relay, baude_headless.py mcp-relay): carried to its
        caller.

            run (string): The run's id.
            name (string): The server's name.
            token (string): The run's token for it.
        """
        return self._call("mcp_relay", {"run": run, "name": name, "token": token}, host, Obj)

    def memory(self, *, session=None, text=None, seen=None, base_mtime=None, host=None):
        """memory (Memory): A Bot's memory (the file its agent keeps it in, on the host), after writing text if it's
        given: refused (error, with the memory as the Bot left it) if the Bot's written it since base_mtime.

            session (string): The session's id.
            text (string): What to write.
            seen (string): What the user's accepted of it.
            base_mtime (number or None): The mtime the text was written against, as memory gave it.
        """
        return self._call("memory", {"session": session, "text": text, "seen": seen,
                                     "base_mtime": base_mtime}, host, MemoryReply)

    def multiplex(self, *, bot=None, call=None, host=None):
        """multiplex (Multiplex): A Bot's terminal tool's request (baudelib.multiplextools) to the terminals here
        (baudelib.multiplexlink): Multiplex's window's and the Kubernetes view's pod shells.

            bot (BotRef): The Bot.
            call (object): The tool's request.
        """
        return self._call("multiplex", {"bot": bot, "call": call}, host, MultiplexReply)

    def notes(self, *, action=None, have=None, changes=None, moved=None, host=None):
        """notes (Notes): Record's notebooks here, for another machine's Record: listed, or changed.

            action (string): write: changes made; else listed.
            have (object): Those the asker has, {id: digest}: only those it hasn't as they are are sent.
            changes (array of object): Changes to notebooks.
            moved (object): With write: pages moved or renamed, {notebook id: {old name: new name}}; the edits
                waiting on them are moved with them, in the same op.
        """
        return self._call("notes", {"action": action, "have": have, "changes": changes,
                                    "moved": moved}, host, NotesReply)

    def quit(self, *, if_idle=None, restart=None, host=None):
        """quit (Quit): The host process stops, its agents too (their sessions kept, resumed next time; one at a turn
        or asking is marked cut).

            if_idle (boolean): Only if no session's running a command, else busy and it goes on.
            restart (boolean): It's to start again.
        """
        return self._call("quit", {"if_idle": if_idle, "restart": restart}, host, Obj)

    def read_file(self, *, path=None, limit=None, host=None):
        """read_file (ReadFile): A file in one of the host's sessions' folders.

            path (string): Its absolute path.
            limit (integer): This much at most.
        """
        return self._call("read_file", {"path": path, "limit": limit}, host, Obj)

    def rename(self, *, session=None, title=None, host=None):
        """rename (Rename): A session renamed (by its agent if it's running; else as it would).

            session (string): The session's id.
            title (string): Its title.
        """
        return self._call("rename", {"session": session, "title": title}, host, Obj)

    def restart(self, *, do=None, host=None):
        """restart (Restart): The host process's restart, as a head asks for it: once nothing's running (its pending
        restart, info's restart, kept by the host), now, or held while this head shows what it waits for.

            do ('free' | 'now' | 'pause'): free: restart once no chat's running a command and no task's running
                (and this head's pause let go of); now: restart at once, what's running stopped and turns cut
                short; pause: no restart while this head holds it (till it says free, or its connection goes).
        """
        return self._call("restart", {"do": do}, host, Obj)

    def rotate(self, *, session=None, host=None):
        """rotate (Rotate): A Bot's conversation archived as a past thread of its own ("<Bot> / <its title>", read-
        only), and the Bot carried on in a new, empty one with a new agent: its id, name, picture, memory, tasks,
        browser and rules as they were. Refused while it's at a turn or one of its tasks is asking it.

            session (string): The Bot's session id.
        """
        return self._call("rotate", {"session": session}, host, Obj)

    def run_task(self, *, session=None, id=None, inputs=None, waited=None, host=None):
        """run_task (RunTask): One of a Bot's tasks run now, by hand.

            session (string): The session's id.
            id (string): The task's id.
            inputs (object): Its form, filled.
            waited (boolean or None): Its Bot waits for the run's end in a tool call (its own run_task, wait
                true): it isn't told the run finished.
        """
        return self._call("run_task", {"session": session, "id": id, "inputs": inputs, "waited": waited}, host, Obj)

    def seen(self, *, session=None, upto=None, host=None):
        """seen (Seen): A session seen: a head shows it on screen (its window the one in front, its chat shown; the
        phone's app in front, on its chat), up to upto. Its last turn's end is seen once upto reaches it, and every
        head is told so in its listing (seen): each takes back its notification of it, and its dot.

            session (string): The session's id.
            upto (integer): How much of its transcript the head shows (its size, as the head has read it).
        """
        return self._call("seen", {"session": session, "upto": upto}, host, Obj)

    def senders(self, *, session=None, host=None):
        """senders (SendersOp): A session's messages from others than its user.

            session (string): The session's id.
        """
        return self._call("senders", {"session": session}, host, SendersReply)

    def set_cwd(self, *, session=None, cwd=None, host=None):
        """set_cwd (SetCwd): The folder a session's agent runs in, from its next message on (not at a turn).

            session (string): The session's id.
            cwd (string): The folder.
        """
        return self._call("set_cwd", {"session": session, "cwd": cwd}, host, Obj)

    def signal_names(self, *, cwd=None, session=None, cli_id=None, host=None):
        """signal_names (SignalNames): The names Signal's glossary has for a session's folder, read as they are;
        nothing looked up or written.

            cwd (string): The folder.
            session (string): The chat's own mentions only.
            cli_id (string): Its id there, if it's another.
        """
        return self._call("signal_names", {"cwd": cwd, "session": session,
                                           "cli_id": cli_id}, host, SignalNamesReply)

    def start(self, *, prompt=None, cwd=None, model=None, effort=None, account=None, mode=None, group=None,
              title=None, attach=None, answers=None, spawned_by=None, task_run=None, system=None, add_dirs=None,
              allowed_tools=None, disallowed_tools=None, fork_from=None, ask_timeout=None, ask_default=None,
              draft=None, ephemeral=None, adopt=None, env=None, start_key=None, app_tools=None, grace=None,
              as_meta=None, as_=None, bot=None, host=None):
        """start (Start): A new session. Its prompt is sent once it's begun.

            prompt (string): The first message.
            cwd (string): Its folder (default: the forked session's, else home).
            model (string or None): Its model.
            effort (string or None): Its effort.
            account (string): The subscription its agent runs under, one of the host's accounts for its model's
                provider (Claude's or Codex's: Account's id or name; "" for the CLI's own sign-in), in place of
                the one it would take (the chat's that starts it, else the host's default for that provider).
                Refused for a model that runs on no subscription, or one that isn't an account there; a disabled
                one's refused as its chats are. Asks subscriptions_read of a member besides spawn.
            mode (string or None): Its permission mode.
            group (string or None): Its group.
            title (string or None): Its title.
            attach (boolean): This connection's attached live to it first.
            answers (boolean): As attach's.
            spawned_by (string): The id of the chat here whose mesh tools start it.
            task_run (object): A task's run going here that starts it, as the run's environment names it
                (BAUDE_TASK_RUN, BAUDE_TASK_TOKEN): nested under its Bot (spawned_by), kept in the run's chats,
                judged as the member that ran it, and its first turn's end told its Bot. Refused if no run's
                going with that token.
            system (string): More for claude's system prompt (--append-system-prompt).
            add_dirs (array of string): Folders it may use besides its own (--add-dir).
            allowed_tools (array of string): Tool rules (--allowedTools: ["Bash(git:*)", "Edit"]).
            disallowed_tools (array of string): Tool rules (--disallowedTools).
            fork_from (string): A session of this host's whose conversation this one starts as a copy of
                (--fork-session).
            ask_timeout (number): Seconds a permission it asks waits while nobody who answers is attached, then
                ask_default answers it.
            ask_default ('deny' | 'allow'): What answers when ask_timeout runs out.
            draft (boolean): A new chat, listed as draft and not kept till it's written in, and gone if nobody's
                attached before it is.
            ephemeral (boolean): A side question's, forked from fork_from (or fresh), keeping nothing, never
                listed, gone with its last head.
            adopt (string): A conversation claude has here already, by its id, made the host's.
            env (object): A head on this machine's environment for the session's agent (kept in memory only).
            start_key (string): An id the client picks, kept and listed, so it can tell whether a start whose
                answer it lost was taken.
            app_tools (array of string): Names of MCP servers this head runs itself, for an agent here: each
                relayed back to it over this connection (mcp_open, mcp, mcp_close), allowed without asking.
            grace (number): Seconds an ephemeral session waits for its head to come back (GRACE_MAX_S at most;
                else EPHEMERAL_S).
            as_meta (boolean): As input's as_meta, for the prompt; the session is held to Auto at most, as a
                subagent of a chat is.
            as (string): As input's, for the prompt.
            bot (BotFields): A Bot made of it as it starts (a phone's host takes it; picture left out).
        """
        return self._call("start", {"prompt": prompt, "cwd": cwd, "model": model, "effort": effort,
                                    "account": account, "mode": mode, "group": group, "title": title,
                                    "attach": attach, "answers": answers, "spawned_by": spawned_by,
                                    "task_run": task_run, "system": system, "add_dirs": add_dirs,
                                    "allowed_tools": allowed_tools, "disallowed_tools": disallowed_tools,
                                    "fork_from": fork_from, "ask_timeout": ask_timeout, "ask_default": ask_default,
                                    "draft": draft, "ephemeral": ephemeral, "adopt": adopt, "env": env,
                                    "start_key": start_key, "app_tools": app_tools, "grace": grace,
                                    "as_meta": as_meta, "as": as_, "bot": bot}, host, Obj)

    def stop(self, *, session=None, restart=None, host=None):
        """stop (Stop): A session's agent stopped: the next message resumes it.

            session (string): The session's id.
            restart (boolean): Those attached aren't told it went.
        """
        return self._call("stop", {"session": session, "restart": restart}, host, Obj)

    def subagents(self, *, session=None, have=None, limit=None, host=None):
        """subagents (Subagents): A session's subagents' files (in the folder beside its transcript).

            session (string): The session's id.
            have (object): Where the client's copy of each is: {name: [size, mtime]}.
            limit (integer): All of them together, at most.
        """
        return self._call("subagents", {"session": session, "have": have, "limit": limit}, host, SubagentsReply)

    def subscribe(self, *, archived=None, host=None):
        """subscribe (Subscribe): The host's sessions, now and on every change, and ping; turn_ended as each of its
        sessions' turns ends.

            archived (boolean): Those archived listed too, and their turns' ends (default: not).
        """
        return self._call("subscribe", {"archived": archived}, host, Obj)

    def task(self, *, session=None, id=None, script=None, host=None):
        """task (TaskGet): One of a Bot's tasks, its script and latest runs.

            session (string): The session's id.
            id (string): The task's id.
            script (boolean): Whether the task's script comes back with it (its version always does); left out,
                it doesn't.
        """
        return self._call("task", {"session": session, "id": id, "script": script}, host, TaskReply)

    def task_ask(self, *, run=None, token=None, text=None, form=None, error=None, host=None):
        """task_ask (TaskAsk): A running script asks its Bot (on the host's own socket, with its run's token):
        something it can't decide, or (error) a failure handed over. Sent the Bot as a turn of its own once it's
        idle, answered as the Bot calls its answer_task tool (answer_task); a turn that ends without it reminded
        once, then failed (refused, as a failure, for a task whose failures aren't handed over too).

            run (string): Its run's id.
            token (string): Its run's token.
            text (string): What's asked.
            form (array of TaskField): Fields the Bot's to fill: its answer's values.
            error (boolean): It's an error handed over.
        """
        return self._call("task_ask", {"run": run, "token": token, "text": text, "form": form,
                                       "error": error}, host, Obj)

    def task_bind(self, *, session=None, id=None, name=None, binding=None, host=None):
        """task_bind (TaskBind): One of a Bot's task's variables bound (binding), or let go of (none): answered as task
        is, with the variable's warning as it's looked at now.

            session (string): The session's id.
            id (string): The task's id.
            name (string): The variable's name.
            binding (TaskBinding or None): What it's bound to; none: unbound.
        """
        return self._call("task_bind", {"session": session, "id": id, "name": name, "binding": binding}, host, Obj)

    def task_choices(self, *, session=None, type=None, host=None):
        """task_choices (TaskChoices): What a task's variable of a type could be bound to, as the Bot's host has them.

            session (string): The session's id.
            type ('model' | 'decider' | 'host' | 'browser_host' | 'terminal_host' | 'kube_context' | 'pod' |
                'container' | 'database' | 'text'): The variable's type (TaskVariable's).
        """
        return self._call("task_choices", {"session": session, "type": type}, host, TaskChoicesReply)

    def task_output(self, *, session=None, path=None, limit=None, host=None):
        """task_output (TaskOutput): The last part of a background task's output file, one a session's transcript
        names. Answered as file.

            session (string): The session whose task it is.
            path (string): The output file a task's start or end named.
            limit (integer): Its last this many bytes (4000 if absent).
        """
        return self._call("task_output", {"session": session, "path": path, "limit": limit}, host, Obj)

    def task_report(self, *, run=None, token=None, say=None, host=None):
        """task_report (TaskReport): A running script's line in its run's log (on the host's own socket, with its run's
        token).

            run (string): Its run's id (BAUDE_TASK_RUN).
            token (string): Its run's token (BAUDE_TASK_TOKEN).
            say (string): The line.
        """
        return self._call("task_report", {"run": run, "token": token, "say": say}, host, Obj)

    def task_run(self, *, session=None, run=None, host=None):
        """task_run (TaskRunGet): One of a Bot's tasks' runs and its log.

            session (string): The session's id.
            run (string): The run's id.
        """
        return self._call("task_run", {"session": session, "run": run}, host, TaskRunReply)

    def task_stop(self, *, session=None, run=None, host=None):
        """task_stop (TaskStop): A run of one of a Bot's tasks stopped.

            session (string): The session's id.
            run (string): The run's id.
        """
        return self._call("task_stop", {"session": session, "run": run}, host, Obj)

    def tasks(self, *, session=None, set=None, delete=None, host=None):
        """tasks (Tasks): A Bot's tasks: set, one made or changed (by its id, with its script); delete, one gone (its
        id).

            session (string): The session's id.
            set (object): A task (as Task).
            delete (string): A task's id.
        """
        return self._call("tasks", {"session": session, "set": set, "delete": delete}, host, TasksReply)

    def telemetry(self, *, app=None, pid=None, events=None, profile=None, profile_error=None, host=None):
        """telemetry (Telemetry): An app's telemetry, told to its own machine's host as it happens (the phone's app to
        its own host), never on to another: what was done in chats, and what it profiled. Kept on that machine only
        (its state folder's telemetry/), while the host's telemetry is on. Its process id is how the host samples
        it.

            app ('baude' | 'record' | 'phone'): The app it's of: Baude, Record, or the phone's app.
            pid (integer): The app's process id.
            events (array of TelemetryEvent): What happened since it last told (each at its own time).
            profile (TelemetryProfile or None): What it profiled since it last told, if it's profiling.
            profile_error (string or None): Why it isn't profiling what it's told to, if it isn't.
        """
        return self._call("telemetry", {"app": app, "pid": pid, "events": events, "profile": profile,
                                        "profile_error": profile_error}, host, Obj)

    def telemetry_read(self, *, from_=None, to=None, what=None, limit=None, host=None):
        """telemetry_read (TelemetryRead): A host's telemetry over a stretch: its chat events, its samples, its line
        profiles, and its turns added up; its telemetry state with it. Any member of the mesh may ask any other: one
        machine pulls every host's and puts them together by their times.

            from (number): From, in seconds since the epoch.
            to (number or None): To (null: now).
            what (array of 'events' | 'metrics' | 'profiles' | 'usage'): What's wanted (none: only the state).
            limit (integer or None): The most events, samples and profiles each (the latest kept; default 5000).
        """
        return self._call("telemetry_read", {"from": from_, "to": to, "what": what,
                                             "limit": limit}, host, TelemetryReadReply)

    def telemetry_set(self, *, on=None, app=None, functions=None, host=None):
        """telemetry_set (TelemetrySet): A host's telemetry switched on or off, or what a process on its machine
        profiles set, at run time: kept by the host, and told to its machine's apps (Info's telemetry).

            on (boolean or None): Telemetry on or off (null: as it is).
            app (any): The process whose profiling is set: the host's, Baude's or Record's (null: none).
            functions (array or None of string): What it profiles: functions and modules by their dotted names;
                empty: off.
        """
        return self._call("telemetry_set", {"on": on, "app": app, "functions": functions}, host, TelemetrySetReply)

    def terminal(self, *, argv=None, cwd=None, title=None, columns=None, rows=None, attach=None, host=None):
        """terminal (Terminal): A terminal on this host (Multiplex's, baudelib.hostterminal): a program run in a pty
        here, or attach: one already running here seen again. One a connection: its answers are terminal_opened,
        then terminal_out as the program writes (first, on attach, what it wrote lately) and terminal_end once it
        ends. The connection going leaves the program running a while (TERMINAL_KEEP_S) to be attached to again.

            argv (array of string): What to run: the user's login shell if none.
            cwd (string): The folder it starts in: the home folder if none.
            title (string): What it's called till its program names it.
            columns (integer): Its width, in characters.
            rows (integer): Its height, in lines.
            attach (string): A terminal running here (terminals' id), seen again instead of a new one.
        """
        return self._call("terminal", {"argv": argv, "cwd": cwd, "title": title, "columns": columns, "rows": rows,
                                       "attach": attach}, host, Obj)

    def terminal_close(self, *, host=None):
        """terminal_close (TerminalClose): This connection's terminal closed: its program ended (SIGHUP, as a terminal
        closing sends).
        """
        return self._call("terminal_close", {}, host, Obj)

    def terminal_in(self, *, data=None, host=None):
        """terminal_in (TerminalIn): Typed into this connection's terminal: what a keyboard sends, base64.

            data (string): The bytes, base64.
        """
        return self._call("terminal_in", {"data": data}, host, Obj)

    def terminal_resize(self, *, columns=None, rows=None, host=None):
        """terminal_resize (TerminalResize): This connection's terminal is now this size.

            columns (integer): Its width, in characters.
            rows (integer): Its height, in lines.
        """
        return self._call("terminal_resize", {"columns": columns, "rows": rows}, host, Obj)

    def terminals(self, *, host=None):
        """terminals (Terminals): The terminals running on this host, to attach to."""
        return self._call("terminals", {}, host, TerminalsReply)

    def theme_remove(self, *, name=None, host=None):
        """theme_remove (ThemeRemove): A theme taken away (the undo of one made).

            name (string): Its name.
        """
        return self._call("theme_remove", {"name": name}, host, ThemeRemoved)

    def theme_save(self, *, name=None, css=None, tokens=None, host=None):
        """theme_save (ThemeSave): A theme made, or its files replaced, once its tokens are checked (Meta's
        create_theme and install_theme, and their undo).

            name (string): Its name: 1 to 40 letters, digits, spaces, - or _.
            css (string or None): Its theme.css; null or left out: none.
            tokens (object or None): Its tokens.json (ThemeFiles' tokens); null or left out: none.
        """
        return self._call("theme_save", {"name": name, "css": css, "tokens": tokens}, host, ThemeSaved)

    def themes(self, *, host=None):
        """themes (Themes): The themes this host keeps, each worked out (Settings → Theme, on every app)."""
        return self._call("themes", {}, host, ThemesReply)

    def transcript(self, *, session=None, offset=None, limit=None, host=None):
        """transcript (Transcript): Whole lines of a session's transcript.

            session (string): The session's id.
            offset (integer): From here.
            limit (integer): This much at most.
        """
        return self._call("transcript", {"session": session, "offset": offset,
                                         "limit": limit}, host, TranscriptReply)

    def unarchive(self, *, session=None, host=None):
        """unarchive (Unarchive): A session listed to all again.

            session (string): The session's id.
        """
        return self._call("unarchive", {"session": session}, host, Obj)

    def unlink(self, *, where=None, id=None, host=None):
        """unlink (Unlink): The links made from here to where, or to member id, let go of and forgotten.

            where (Where): Where.
            id (string): A member's id.
        """
        return self._call("unlink", {"where": where, "id": id}, host, Obj)

    def waits_on(self, *, session=None, on=None, done=None, host=None):
        """waits_on (WaitsOnOp): A session's agent waits on another chat's answer, or (done) no longer. Kept with the
        session (not listed) while its turn goes on: if the host stops meanwhile, it follows the wait once it's
        started again, and what the wait would have given is held for the session.

            session (string): The session's id.
            on (WaitTarget): What it waits on.
            done (boolean): No longer.
        """
        return self._call("waits_on", {"session": session, "on": on, "done": done}, host, WaitsOnReply)
