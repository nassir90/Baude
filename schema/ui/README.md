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
- **A Bot has only its Name.** Wherever an app lists or shows a Bot (a sidebar or home row, the open chat's header and
  window title, a notification, the macOS menu bar item, This phone, a share target, the @ and ⌘K lists, Meta's lists, captions and trash button, a sender), it shows the Bot's name and
  nothing of the chat under it: no chat title, no second line. The name is set when the chat is made a Bot
  (BotSettings.dialog.json) and changed with Rename… or Bot Settings…. A new entry that shows a Bot says so.
- **A picture is set into the page.** Wherever an app shows someone's or a Bot's picture, or their initials on a colour
  (the Git page's authors, a Bot's sidebar or home row, Bot Settings, This phone's agents, the phone's chat header), it's round, under the inset shade
  `picture-shade` in schema/tokens: a blurred shadow round its rim, one from its top edge and a hairline ring just inside
  its edge, black at the tokens' strengths in light and dark alike (imageviews.shaded_picture; the phone's Avatar).
  Pictures and initials alike get it. Launcher shortcut icons (ShareSheet.dialog.json) get none either: the launcher
  cuts them to its own shape.
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
- **Apps are thin clients; the logic is in the wire.** What an app or an agent's tool does to shared things (a
  notebook's pages and the edits proposed to them, a chat, the mesh) is the host's to work out, through typed wire
  messages in schema/types; an app or an MCP server asks the host and shows its answer. An agent's interface (an MCP
  tool) can be shaped for agents rather than be the wire itself, but stays a thin client of it. The owner: "Well the
  external interface need not be the wire but I think most of the operational logic should be in hte wire. protocol.
  Agent MCP requirements are different to that of the wire (which can be as detailed as we want). They still are
  basically thin clients." (source: BaudeInternal#200; session b3845b21-55ac-4aab-a769-fb8517403c51, message
  868770b1-06c0-44a3-bdb9-c1851cf4b39a)
