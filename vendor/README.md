# vendor

Libraries Baude and Record use, kept here so they work without installing anything. `widgets.py` puts this folder
first on `sys.path`.

| Library | Version | Licence | From |
|---|---|---|---|
| [Pygments](https://pygments.org) (`pygments/`): syntax highlighting of code in chats and the Code page | 2.21.0 | BSD-2-Clause ([pygments.LICENSE](pygments.LICENSE), [pygments.AUTHORS](pygments.AUTHORS)) | `pygments-2.21.0-py3-none-any.whl` from PyPI, sha256 `2363c69b61c4a97c838da3b130dcd6468f4848992b21a82f2a63ec34377137d9`, unchanged |
| Model family icons (`model-icons/`): the monotone icon beside each model in the model pickers (`widgets.ModelPicker`) | 1.102.0 | MIT ([LobeHub icons](https://github.com/lobehub/lobe-icons)); the logos are their owners' trademarks | `@lobehub/icons-static-svg` from npm, renamed `baude-model-<name>-symbolic.svg`, sized 16, `<title>` and `style` dropped |
