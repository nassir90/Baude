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
