# Semantic themes rig (a prototype)

macOS, offscreen (macos.stay_offscreen; windows put in only unseen), throwaway HOME/XDG, fake claude: use
tests/proto3d/env.sh's environment and build its pointer shim first (`clang -dynamiclib -framework AppKit -o
tests/proto3d/fakeloc.dylib tests/proto3d/fakeloc.m`).

    GSK_RENDERER=gl run tests/semantic/render.py OUT     # the sample (three-chats), light and dark; a chat used through
                                                         # the scene (click, type, Send, a tool card opened); boxes a
                                                         # click opens (pick reveal) and a tag that opens a chat
    N=3|50|200 GSK_RENDERER=gl run tests/semantic/perf.py  # frame time, CPU, picture uploads, N live panels
    MODE=a|a2|c N=200 GSK_RENDERER=gl run tests/semantic/gsk200.py  # where a frame of #454's stage goes

    GSK_RENDERER=gl run tests/semantic/station_render.py OUT  # the Space Station theme (space-station/, data alone),
                                                         # installed from its .baudetheme, light and dark; a chat, a
                                                         # tool card and a sessions row used through the scene
    SCENE=station GSK_RENDERER=gl run tests/semantic/perf.py  # the same measures, the station's 14 panels
    python3 tests/semantic/make_station.py               # writes space-station/scene.json (its ring, its stars)

A scene's chat panels are the app's own chat pages (scenehost.ChatPage): the rigs make a BaudeApp with a host in this
process (tests/test_baude.py's App) and write each fixture chat as a session for it (rig.write_chats).

The Space Station's scene says `"renderer": "gfx"`: the desktop draws it with pygfx (scenegfx.py) where pygfx is
installed, else with scenegl as before. BAUDE_SCENE_RENDERER=gfx|gl picks one whatever the scene says (perf.py's N
grid too); perf.py adds gfx_render_ms (pygfx's frame, to the GPU's finish) and gfx_readback_ms when pygfx draws.

pygfx (with wgpu: Metal on macOS, Vulkan on Linux) isn't one of the apps' needs yet: a venv that sees the system's
PyGObject, `python3 -m venv --system-site-packages VENV && VENV/bin/pip install pygfx` (macOS: Homebrew's python;
Linux: python3-gi, and a Vulkan driver: `apt install libvulkan1 mesa-vulkan-drivers`), then run with VENV/bin/python.
The station's assets are CC0, credited in its manifest.json (credits, ThemeCredit.json).

perf.py takes NOBLOOM=1, RENDER_ONLY=1 (GL each frame, no layout), LAYOUT_ONLY=1, SCENE_FINISH=1 (glFinish after each
frame, to time the GPU) and SAMPLE=file (macOS's sample of the run).

Phone: `./gradlew --offline testDebugUnitTest --tests '*SceneThemeShots*'` and `'*StationShots*'` (pictures in
app/build/scenetheme).
