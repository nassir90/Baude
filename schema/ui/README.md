# schema/ui: what the apps show and do

One JSON file per surface a user sees, beside `types/` (the wire), `tokens/` (design tokens) and `copy/` (wording).
The desktop apps (Baude, Record) are the reference: another app (the phone) does what an entry says, with the same
structure and wording, unless the entry carries an override for it. A fix is briefed and reviewed against its entries;
`tests/parity/` renders the same scenes on both apps to check.

## Names

`<Name>.<kind>.json`, `<Name>` in UpperCamelCase. `<kind>` is one of:

- `region`: a fixed part of the main window (header bar, sidebar, composer, status bar, banners).
- `panel`: a page of the side panel.
- `page`: something that takes over the main area or opens in a tab.
- `dialog`: a dialog.
- `menu`: a menu or popover menu.
- `element`: one kind of thing drawn in a chat's transcript.

## Shape

Plain JSON, two-space indent, no `x-` keys. Fields, in this order, each left out when it doesn't apply:

- `description`: one or two sentences: what it is and where it sits.
- `apps`: which apps have it: `baude`, `record`, `phone`. An entry with only `phone` is a phone-only feature the owner
  asked for.
- `opens`: every way a user reaches it (a menu path with `→`, a key, a click).
- `code`: where it's built, `file.py:Class.method` (never line numbers).
- `copy`: the `schema/copy` files its wording comes from.
- `phone`: an override (below).
- `parts`: every row, button, field and section, in screen order. Each has `name`, `shows`, and as they apply `copy`
  (message keys), `text` (literal strings not in `schema/copy`), `actions` (`[{"by": "click", "does": "…"}]`),
  `phone`, `apps` (only when not every app of the entry has this part), and nested `parts`.
- `states`: distinct looks: empty, loading, error, working, asking, signed out, host gone.
- `removed`: parts taken out on purpose, so no app brings them back: each `{name, apps, why, decided}`, `apps` naming
  every app it's gone from (`baude`, `record`, `phone`).
- `decisions`: the owner's complaints and corrections an entry or part answers, so no app repeats the mistake: each `{said, rule, decided, source}`, `rule` written to hold for every app, `source` what it can be audited by: `{"issue": "BaudeInternal#N"}` or `{"session": …, "message": <the owner's message's uuid>}`.
- `scenes`: `tests/screenshots.py` scenes that show it.

## Overrides: where the phone differs on purpose

An entry or a part may carry:

```json
"phone": {"does": "what the phone does instead, or \"nothing\"", "why": "the reason", "decided": "2026-11-21"}
```

- `does: "nothing"`: the phone leaves it out (window management, keyboard shortcuts, launching desktop programs).
- Anything else: the phone does this instead (a bottom-bar pager for the side panel's switcher, a long-press for a
  hover or a right-click).
- Only the owner decides overrides. A change may propose one in its PR; it lands when the owner agrees.
- Where there's no override, the phone matches the entry. Anything on the phone with no entry is invented.

## Rules every entry follows

- **One word-wrap icon.** Every wrap toggle (code views, Kubernetes logs, on every app) draws `baude-wrap-symbolic` from schema/icons; the owner asked that all of them come from one place, with a clearer icon (2026-11-24). Source: `[{"issue": "BaudeInternal#207"}]`.
- **A closed window leaves nothing listening.** Anything an entry draws that follows the theme (dark or light, the accent) stops following it when its window closes; the owner found Baude and Record growing in memory the longer they run (2026-11-24). Source: `[{"issue": "BaudeInternal#213"}]`.
- **What closes is freed.** A closed window, a closed tab (a file's code, a chat, a page of notes, the Feedback tab) and a closed dialog are freed with everything they hold, and changing the accent doesn't keep each logo drawn for it; the owner found Baude and Record growing in memory the longer they run (2026-11-24). Source: `[{"issue": "BaudeInternal#213"}]`.
- **A Bot has only its Name.** Wherever an app lists or shows a Bot (a sidebar or home row, the open chat's header and
  window title, a notification, the macOS menu bar item, This phone, a share target, the @ and ⌘K lists, Meta's lists, captions and trash button, a sender), it shows the Bot's name and
  nothing of the chat under it: no chat title, no second line. The name is set when the chat is made a Bot
  (BotSettings.dialog.json) and changed with Rename… or Bot Settings…. A new entry that shows a Bot says so. The owner: "Bot's just have a Name. When you promote to a bot you have the option
  of setting the Bot's new name." (2026-11-22). Source: `[{"session": "f24332de-9555-4279-8489-ffa7dbcf54c9", "message":
  "30af64bb-6804-4040-984b-d3fd2a444e53"}]`.
- **A picture is set into the page.** Wherever an app shows someone's or a Bot's picture, or their initials on a colour
  (the Git page's authors, a Bot's sidebar or home row, Bot Settings, This phone's Bots, the phone's chat header), it's round, under the inset shade
  `picture-shade` in schema/tokens: a blurred shadow round its rim, one from its top edge and a hairline ring just inside
  its edge, black at the tokens' strengths in light and dark alike (imageviews.shaded_picture; the phone's Avatar).
  Pictures and initials alike get it. Launcher shortcut icons (ShareSheet.dialog.json) get none either: the launcher
  cuts them to its own shape. The owner asked for an inset shadow on GitHub profile pictures, and later Bots' (2026-11-17),
  then: "On mobile, add an inset border to git profile pictures, Bot icons. Make sure this is applied everywhere." and
  "Git users, bots that are just letters should get inset shadow too, make shadow slightly lighter." (2026-11-22).
  Source: `[{"issue": "BaudeInternal#140"}, {"session": "f24332de-9555-4279-8489-ffa7dbcf54c9", "message":
  "30af64bb-6804-4040-984b-d3fd2a444e53"}, {"session": "b3845b21-55ac-4aab-a769-fb8517403c51", "message":
  "5d9296b7-3bd3-4521-ba9f-f69dbe09d930"}, {"session": "b3845b21-55ac-4aab-a769-fb8517403c51", "message":
  "a638b09c-3bf9-41e8-8a27-ade2faa9d9e1"}]`.
- **Popups are one kind of thing.** Every menu, picker's list, popover and dialog is drawn the size of the page it opens
  from (on the phone: at the page's density, so Global Scale and Text size reach it; android's Popups.kt), with the
  values in schema/tokens' `popup`:
  - A menu's or picker's row is `popup.row` high, whatever it holds; its first thing (a check, dot, logo or its text) and
    its last (a picker's tick, a submenu's ▸) are `popup.inset` in from the menu's sides, `popup.gap` from its text.
    Headings start at `popup.inset` too; a divider has `popup.pad` either side, and the list `popup.pad` above and below.
  - Ticks: a picker (one of its rows picked: effort, mode, a host, an accent, a branch) keeps room for its tick at every
    row's end. A menu of choices (Group by, the namespaces shown) has a check column before every row's text, its
    actions' rows too, so all its text lines up. A menu of things to do with toggles among them (the main menu) has the
    column too.
  - A menu sits under the thing it opens from, at its start (or ending at its end), else over it; on the phone it stays
    `popup.margin` off the screen's edges, however deep in a dialog it's opened, and is no taller than the room it has.
  - A dialog on the phone is the chat's column wide: its sides `popup.dialog-gutter` in from the chat's border, where the
    chat's text starts. Its heading (`font.title`), body (dim) and buttons (at its end) are `popup.dialog-inset` in from
    its edges.
  - The desktop's are libadwaita's (Gtk.PopoverMenu, Adw.AlertDialog) and theming's CSS: menu and dropdown rows 30 px
    (the phone's `popup.row` is 34, a bar button's side, for a finger), modelbutton's 12 px sides (the phone's
    `popup.inset` too), a toggle's check before its text, and
    Adw.AlertDialog's own width (the phone's follows the chat).
  - The owner asked for every dropdown's and modal's row heights and side padding to be measured, then: "Central modals
    need to be wider, they should be a width such that were they to be activated when a chat is active the gap between
    the border and the modal border would be the same width as the 'gutter' we keep at the sides of the chat." and
    "Otherwise fix inconsistencies as you see fit.", then: "All modals look wayy too wide, I think you got the wrong idea.
    Where is the margin padding at the sides?" (2026-11-22). Source: `[{"session": "b3845b21-55ac-4aab-a769-fb8517403c51",
    "message": "0e663697-47c4-403d-a7a6-c99e167825d4"}, {"session": "b3845b21-55ac-4aab-a769-fb8517403c51", "message":
    "94fe8190-5ead-48a2-bfe8-83493bb7bbad"}, {"session": "b3845b21-55ac-4aab-a769-fb8517403c51", "message":
    "96b6b609-ce76-4e05-ac4f-d263c29199e9"}]`.
- **A missing dependency is shown one way.** Where a feature needs a program or library that isn't there
  (schema/deps: VTE, WebKitGTK, kubectl, Docker, Claude Code, Codex CLI, Git, GitHub CLI, Node.js, Xvfb, …), every app
  shows the same thing in the feature's place, never a toast alone, a raw error or nothing: a package icon
  (`package-x-generic-symbolic`), "<Feature> needs <dependency>" ("Git isn't installed" where they share a name), the
  command that installs it on that machine in monospace with Copy (else its install page), and Try Again, or Restart
  for the app's own library (loaded as it starts). A host's are the host's to say (its deps request, a reply's
  `missing`), so the phone and another machine's apps show what that host lacks with that host's command; where only a
  line fits (a chat's note, stderr, Signal's note) it's that sentence with the command. Settings → Dependencies lists
  them all, as the host says them, under what needs each (Providers, Git and GitHub, Kubernetes, …), with the checks
  of what's installed beside them (a kubeconfig with a context, gh signed in, Ollama running and its models, MLX's
  models), each not ready shown as a missing one is, with its fix (Dependencies.dialog.json). `BAUDE_HIDE_DEPS=kubectl,…`
  makes any of them missing, to see each case. The owner asked for proper guidance for installing what Baude depends
  on and for how it behaves without it (2026-11-22), and for the page grouped by what needs each, with readiness and
  the providers' status (2026-11-25).
  Source: `[{"issue": "BaudeInternal#184"}, {"issue": "BaudeInternal#183"}, {"session": "f1afb37a-90af-47d8-8693-d537464b4022", "message": "a1bc42a8-97d0-4dad-98b0-d3a42bfa155e"}, {"issue": "BaudeInternal#227"}]`.
- **Telemetry is a timestamped log that stays on the user's machines.** Every app tells its own machine's host only
  (the phone's app its own host), as it happens and each with its own time: each chat opened, in front, left, each turn
  sent and ended (how long, model, effort, tokens), by the chat's host id and session id; a host keeps its own sessions'
  turn ends too, samples its machine and its processes every 5 seconds, and keeps line profiles of what it's told to
  profile (line_profiler, optional). All of it only while that host's telemetry is on, switched at run time from
  Settings → Telemetry or telemetry_set. Any member reads any host's over a stretch (telemetry_read), so one machine
  pulls every host's and puts them together by time (baudelib/telemetry.py's gather); a chat's size is worked out
  from its transcript when asked (chat_size), never kept. No screen time or menu counts. Nothing is sent outside the
  mesh. The owner: "Once a minute is completely bonkers and quite useless", "Sreen time is a pretty useless metric,
  how is that going to help me make the app better (in isolation)", "Store chat activity yes with timestamps", "Yes
  store system metrics", "Yes build in line_profiler", "Yes add teh wire messags" (2026-11-26). Source:
  `[{"issue": "BaudeInternal#205"}, {"session": "b3845b21-55ac-4aab-a769-fb8517403c51", "message":
  "41aef9c4-02f6-4496-ac8a-bb9737df5262"}, {"issue": "BaudeInternal#228"}]`.
- **Every piece of text starts with a capital letter.** Whatever an app puts on screen starts with a capital: notes and
  messages (a chat's SystemNote, an error, a status line) in sentence case ("Interrupted", "Model → Opus", "Committed on
  main", "Claude couldn't save this chat: the disk is full"), buttons, menu items and links in Title Case ("Show in
  Git"). "Claude" as a word in a note is "Claude"; a command, path or name put in keeps its own spelling. The words come
  from schema/copy, so every app says them alike. The owner: "Casing should be consistent mate" (source: session
  b3845b21-55ac-4aab-a769-fb8517403c51, message e824fbc9-b33f-4a3a-8e0c-8ebf94acd120).
- **A theme is one for every app.** A theme is its host's (a folder in its data folder, themes/<name>/: theme.css,
  GTK CSS drawn over all of the desktop apps' own; tokens.json, colours over schema/tokens', light and dark, of the
  libadwaita and baude groups only), and the host works out what it comes to for every app alike (the themes request,
  Theme.json: each colour it makes, a colour made of another included). Every app draws those colours (the phone's
  BaudeColors, the desktop's named colours and their CSS variables), and the desktop apps its CSS too; its accent is
  drawn only while Accent Colour is System (one picked is drawn over it). A theme whose text can't be read on a probe
  (a label and a button, drawn unseen) isn't drawn: Default is, for that run, with a toast saying why; BAUDE_NO_THEME=1
  (an app's --no-theme) starts an app in Default whatever's picked. Settings → Theme picks one on every app; Meta makes, installs and previews them
  (create_theme, install_theme, preview_theme), undone as its other actions are. A theme sets colours, not sizes: the
  phone's measures are made as it's built. The owner asked for a way, from inside the app, for Meta to create, install
  and preview themes in Settings, as generic as it can be while staying fast (2026-11-24). Source:
  `[{"issue": "BaudeInternal#218"}]`.
- **A theme is colours, a skin or a scene, each with the ones under it.** One model covers every theme
  (schema/types/ThemeManifest.json), a folder on its host (themes/<name>/) or that folder zipped as one file
  (<name>.baudetheme) to pass round, installed on a host as the rule above has it (theme_install), its kind its highest
  layer:
  - colours: tokens.json, the colours every app draws (the rule above);
  - skin: colours, and how the desktop apps draw their own widgets: theme.css over theirs, or a GTK theme in place of
    libadwaita's stylesheet (Windows XP's, Windows 95's: gtk/<name>/ in the theme, or one on the machine, taken as an
    app starts), its corners and title bar probed as they are now (theming.py); the phone draws the colours;
  - scene: a skin, and a space (scene.json, schema/types/Scene.json) every app draws, Baude's own things placed in it
    as live widgets (chats with their state, hosts, Meta cards, windows: SceneObject.json), lit, with bloom. The
    widgets in a scene are drawn with the theme's colours and skin, so a scene of Windows 95 panels is one theme.
  What each app takes from a theme: every app its data (tokens.json, scene.json, its models, textures and fonts);
  the desktop apps its theme.css and GTK theme too, and its code (desktop/*.py, given the scene's SceneAPI, and
  shaders/*.glsl), only once its host's user has trusted that code as it is (theme_trust: its sha256; changed, it's
  not run till it's trusted again, and the scene's drawn from its data alone). The phone never runs a theme's code.
  Anyone on the mesh may trust a theme's code (theme_trust is taken from any of the mesh's apps and heads, as every
  other request is): `{"said": "Anyone on the mesh", "rule": "Any member of the mesh may trust a theme's code on any
  host; no app or host asks for more than its being in the mesh", "decided": "2026-11-26", "source": [{"session":
  "b3845b21-55ac-4aab-a769-fb8517403c51", "message": "368e4c39-2ac3-4d3f-8367-b69de411b295"}]}`.
  The owner: "If both of these can be done, and they can be distributed as some kind of plugin, we can merge the
  semantic "theme" framework. Ontologically we want this to meld with the work we did on the windows XP themes and
  such too" (2026-11-26). Source: `[{"session": "b3845b21-55ac-4aab-a769-fb8517403c51", "message":
  "96faec93-ffcb-4edc-a5bb-b9af7c972ea9"}]`.
- **A scene's chat is the chat's own page.** A chat in a scene is drawn as the app's own chat page of that chat, the
  widgets its window shows (its messages, tool cards, the box to write in and the status bar), live, differing from
  the window's only by the scene's light and the panel's edge; a stand-in made of parts is drawn only where there's no
  app to give the page (the sample's rig without one). The owner, of the Space Station scene: "Space station UI looked
  100% different, quite concerning" (2026-11-26). Source: `[{"session": "b3845b21-55ac-4aab-a769-fb8517403c51",
  "message": "ef0e29ca-95e4-410d-8a14-fb11966725c7"}]`.
- **A scene's look is a real renderer's, not one the app rolls itself.** A scene that says `"renderer": "gfx"` is
  drawn on the desktop by pygfx (wgpu): physically based materials lit by its lights and by its environment (an HDR
  image's light, reflected), a sun's shadows, its backdrop behind everything, glTF models in its meshes' places, bloom
  from what glows, a tone curve and sRGB out, antialiased; its assets are free ones (CC0) from their makers' own sites,
  credited in its manifest. The panels stay the widgets' own pictures, kept out of the bloom and the tone curve so
  their text is as the widget draws it; clicks still go through the scene's own ray picking. The owner, of the Space
  Station drawn in GL: "If I asked for a threejs render it would look better", and "you don't need to home roll an
  attractive opengl renderer… you can probably find some online" (2026-11-26). Source: `[{"session":
  "b3845b21-55ac-4aab-a769-fb8517403c51", "message": "ef0e29ca-95e4-410d-8a14-fb11966725c7"}, {"session":
  "b3845b21-55ac-4aab-a769-fb8517403c51", "message": "c4e1231d-71c1-46cd-b6e6-2ae12e1be371"}]`.
- **Apps are thin clients; the logic is in the wire.** What an app or an agent's tool does to shared things (a
  notebook's pages and the edits proposed to them, a chat, the mesh) is the host's to work out, through typed wire
  messages in schema/types; an app or an MCP server asks the host and shows its answer. An agent's interface (an MCP
  tool) can be shaped for agents rather than be the wire itself, but stays a thin client of it. The owner: "Well the
  external interface need not be the wire but I think most of the operational logic should be in hte wire. protocol.
  Agent MCP requirements are different to that of the wire (which can be as detailed as we want). They still are
  basically thin clients." (source: BaudeInternal#200; session b3845b21-55ac-4aab-a769-fb8517403c51, message
  868770b1-06c0-44a3-bdb9-c1851cf4b39a)
- **A scene theme is a test of how thin the apps are.** A scene (the Space Station's, tests/semantic/space-station)
  places Baude's own widgets and windows in a lit space, and what it shows of them is what the wire and schema/types
  say: SceneObject.json's things, made from the host's answers. Whatever a scene had to take from an app's own code
  instead (its state, its rules) is listed with the scene, under Leaks, with the wire message it would need, till the
  wire carries it. The owner asked for a "Really basic 3D space station with Baude widgets and windows in differnet
  placies, 3D, specular diffuse lighting, bloom" (2026-11-26), and: "Baude GTK normal is merely a cradle for limmering
  functionality. The true value / edge is revealed by swapping, stripping and rebuilding the frontend and seeing what
  of our hard work remains of it." Source: `[{"session": "b3845b21-55ac-4aab-a769-fb8517403c51", "message":
  "96faec93-ffcb-4edc-a5bb-b9af7c972ea9"}, {"session": "b3845b21-55ac-4aab-a769-fb8517403c51", "message":
  "1d3ef2c7-6d97-4480-a1e2-b1526eab2287"}]`.
- **A pull request is a pill.** Wherever an app draws a pull request (the Git page's list and log, a reply's or
  message's GitHub pull-request link or bare #12, Signal's links, Record's chat and notes), it's a pill: git's merge
  logo, a space, then the number, never a #, coloured by where it stands (green open, grey draft or closed, red changes
  requested, purple merged), logo and number in the same colour. The owner asked for PR tags as the logo then the
  number, for PR links to be pills too, and for merged ones to be purple (2026-11-12). Source: `[{"issue":
  "BaudeInternal#21"}, {"issue": "BaudeInternal#28"}, {"issue": "BaudeInternal#29"}, {"issue": "BaudeInternal#32"},
  {"issue": "BaudeInternal#44"}]`.
- **Every box sits on one line.** The chat's composer, Meta's box and a side question's box (in the panel or maximised)
  start and end the same height above the window's foot: the row under each (the chat's status line, Meta's Clear and
  Copy and dots, a side question's dots and meter) is as tall as the chat's status line, and Clear isn't bold. The owner
  asked for Meta's box and the chat's to start on the same line (2026-11-12) and for side questions' to match them when
  maximised (2026-11-14). Source: `[{"issue": "BaudeInternal#50"}, {"issue": "BaudeInternal#85"}]`.
- **A dropdown opens as the button goes down.** Every dropdown (a picker's button, a choice row, a host picker) opens
  its list as the mouse button goes down on it; let go over a choice and that one's picked and the list closes, let go
  outside and it closes, let go on the dropdown itself (a plain click) and the list stays open to pick from
  (pickers.press_opens_dropdowns; a touch screen's tap is a plain click). The owner found dropdowns behaved unnaturally
  and asked for open on press, close on release (2026-11-14). Source: `[{"issue": "BaudeInternal#74"}]`.
- **A row opens by growing into its page.** Where a list's row opens a page in the side panel (a subagent, a process, a
  side question), the row's line grows into the page's header and its darker square grows to fill the panel, from
  wherever the row is scrolled to, and closes back the same way; never a slide. While it grows the row's hover highlight
  is dropped and nothing under it takes the pointer, so no hover jitters over the header (panels.ExpandStack). The owner
  asked for the expansion in place of a slide (2026-11-14) and for the hover to stop once it starts (2026-11-14).
  Source: `[{"issue": "BaudeInternal#77"}, {"issue": "BaudeInternal#83"}]`.
- **A theme never leaves a surface unfilled.** Under any theme, the chat, Meta's and a subagent's panes, Record's pages
  and the Kubernetes and Database views are filled with the view colour (taken from a GTK 3 theme's older names when it
  has no libadwaita ones), so text and highlights stay readable, and a dialog's sheet has the window's fill and a 1 px
  edge in the theme's border colour, so it never runs into what's under it. The owner found the Kubernetes and Database
  views see-through and dialogs edgeless under the Windows XP themes, and chat panes without a white background while
  Files had one (2026-11-14). Source: `[{"issue": "BaudeInternal#88"}, {"issue": "BaudeInternal#90"}]`.
- **Corners follow the look.** Every rounded corner an app draws (a bubble, the message box, a card, a table, a code
  block, a search field, a popover, Meta's forms such as Add a host) is held to one roundness: libadwaita's by default,
  and on the desktop apps under a GTK theme the radius the theme draws its own buttons with (theming.py:
  _probe_corners): square under Windows 95, a few pixels under XP, so nothing stays rounded under a square theme; where
  the theme's title bar is another colour than the window's frame, the frame shows above the inside as well as round it.
  The phone has one roundness. The owner found the search bars immune to the border-radius preference and the inside
  touching the title bar (2026-11-14), and Meta's Add a host form ignoring Windows 95's square corners (2026-11-17).
  Source: `[{"issue": "BaudeInternal#107"}, {"issue": "BaudeInternal#108"}, {"issue": "BaudeInternal#152"}]`.
- **Enter is a new line; Ctrl+Enter sends.** In every message box (a chat's composer, Meta's box, a side question's, the
  box over the side questions list), Enter starts a new line (continuing a list), Shift+Enter a plain line break, and
  Ctrl+Enter sends (⌘Enter too on a Mac); the send button's tooltip says so. The owner asked that Enter not submit in
  the agent, Meta and side-question boxes, so lists can be typed (2026-11-17). Source: `[{"issue":
  "BaudeInternal#146"}]`.
- **Bots, not agents.** Wherever an app names a Bot or what it starts (This phone's list, a fallback name, Meta's start
  form "Start a chat" and its rows, a Bot's instructions), it says "Bot" or "chat", never "agent"; only Claude Code's
  own terms (the Agent tool, subagents) keep the word. The owner: "They are not \"Agents\" the y are \"Bots\" I have no
  idea what an \"Agent\" is" (2026-11-22). Source: `[{"session": "b3845b21-55ac-4aab-a769-fb8517403c51", "message":
  "5d9296b7-3bd3-4521-ba9f-f69dbe09d930"}]`.
