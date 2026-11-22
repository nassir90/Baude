# icons

Every icon Baude, Record, Emission and the Android app draw, an SVG file each, in a folder for each set. The desktop
apps read them as they run (`baudelib/icons.py`); the Android app's build makes Kotlin of them (`build.gradle.kts`'s
`baudeIcons`). A symbolic icon is named as GTK's icon theme looks it up (`<name>-symbolic.svg`), drawn on a 16 square.

| Folder | What | Licence | From |
|---|---|---|---|
| `adwaita/` | Adwaita's symbolic icons: the Android app's (`Adwaita.GoPrevious`), the ones the desktop draws for the same things from the desktop's theme (these behind it) | LGPL-3.0 or CC-BY-SA-3.0 ([adwaita/LICENSE](adwaita/LICENSE)), GNOME Project | GNOME's `adwaita-icon-theme` 51.0, `symbolic/*/<name>-symbolic.svg`, unchanged |
| `baude/` | Baude's own symbolic icons, on both | Baude's; `baude-meta-symbolic` is Material's build icon (Apache-2.0); `baude-no-connection-symbolic` is Adwaita's `weather-overcast` cloud outline with a slash cut through it (CC-BY-SA-3.0 or LGPL-3.0, as `adwaita/`) | drawn for Baude |
| `kube/` | The Kubernetes view's sidebar categories, as Freelens draws them | Freelens' nodes, workloads and helm (MIT); the rest Material's (Apache-2.0) | their 24-px (helm's 500) drawings, scaled to 16; each file says which |
| `models/` | The monotone icon beside each model in the model pickers (`pickers.ModelPicker`) | MIT ([models/LICENSE](models/LICENSE), [LobeHub icons](https://github.com/lobehub/lobe-icons)); the logos are their owners' trademarks | `@lobehub/icons-static-svg` 1.102.0 from npm, renamed `baude-model-<name>-symbolic.svg`, sized 16, `<title>` and `style` dropped |
| `links/` | A link's logo, drawn before it in the text's colour (`currentColor`): `widgets.REF_ICON_SVG` | Baude's | drawn for Baude |
| `apps/` | The apps' logos, and the phone's notification icon. A logo's tile is its first `rect`; with an accent picked (Settings → Accent Colour), Baude's and Record's are drawn with every use of the tile's colour in the accent's (`icons.logo`, schema/tokens' `accent`). The phone's launcher icon is made of `baude.svg` (the tile its background, the rest its foreground) and `baude-notification.svg` (its monochrome layer) | Baude's | drawn for Baude |
