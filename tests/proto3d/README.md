# 3D cards prototype rig (not for merging)

macOS, offscreen (macos.stay_offscreen; windows put in only unseen), temp HOME/XDG, fake claude:

    clang -dynamiclib -framework AppKit -o tests/proto3d/fakeloc.dylib tests/proto3d/fakeloc.m
    source tests/proto3d/env.sh            # (set R first)
    W=$W run tests/proto3d/input.py        # clicks on real buttons through the 3D view, a synthesized throw
    APPROACH=a|b|c N=10|50|200 W=$W run tests/proto3d/perf.py
    W=$W run tests/proto3d/rec.py $W OUTDIR # orbit and throw frames, 30 fps

Phone: `./gradlew --offline testDebugUnitTest --tests '*Cards3DShots*'` (frames in app/build/cards3d), and the
debug-only `Cards3DDemo` activity for measuring on a device (`am start -n dev.baude.android/.Cards3DDemo --ei n 50
--ez throw true --ez cached true`, then `dumpsys gfxinfo`).
