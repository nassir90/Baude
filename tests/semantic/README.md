# Semantic themes rig (a prototype)

macOS, offscreen (macos.stay_offscreen; windows put in only unseen), throwaway HOME/XDG, fake claude: use
tests/proto3d/env.sh's environment and build its pointer shim first (`clang -dynamiclib -framework AppKit -o
tests/proto3d/fakeloc.dylib tests/proto3d/fakeloc.m`).

    GSK_RENDERER=gl run tests/semantic/render.py OUT     # the sample (three-chats), light and dark; a chat used through
                                                         # the scene (click, type, Send, a tool card opened); boxes a
                                                         # click opens (pick reveal) and a tag that opens a chat
    N=3|50|200 GSK_RENDERER=gl run tests/semantic/perf.py  # frame time, CPU, picture uploads, N live panels
    MODE=a|a2|c N=200 GSK_RENDERER=gl run tests/semantic/gsk200.py  # where a frame of #454's stage goes

    GSK_RENDERER=gl OUT=dir run tests/semantic/christmas.py   # the Christmas scene installed as its .baudetheme
                                                         # through the host, light and dark: the room, a host's place,
                                                         # a present opened and its letter used, the dashboard
    SCENE=christmas [OPEN=1 N=9] GSK_RENDERER=gl run tests/semantic/perf.py  # its frames (OPEN: N letters out)

A scene's chat panels are the app's own chat pages (scenehost.ChatPage): the rigs make a BaudeApp with a host in this
process (tests/test_baude.py's App) and write each fixture chat as a session for it (rig.write_chats).

perf.py takes NOBLOOM=1, RENDER_ONLY=1 (GL each frame, no layout), LAYOUT_ONLY=1, SCENE_FINISH=1 (glFinish after each
frame, to time the GPU) and SAMPLE=file (macOS's sample of the run).

Phone: `./gradlew --offline testDebugUnitTest --tests '*SceneThemeShots*'` (pictures in app/build/scenetheme).
