# a throwaway environment: temp HOME/XDG, no ~/.local/bin on PATH, fake claude
R=${R:?set R to a scratch folder holding the worktree as w-3d}
W=$R/w-3d
H=$(mktemp -d /tmp/b3d.XXXX)
export HOME=$H XDG_CONFIG_HOME=$H/c XDG_DATA_HOME=$H/d XDG_CACHE_HOME=$H/k XDG_STATE_HOME=$H/s
export BAUDE_RUNTIME_DIR=$H/rt BAUDE_SSH=false BAUDE_MENU_BAR=0 BAUDE_BINS=$H/bin BAUDE_LAUNCHERS=$H/apps
export BAUDE_CLAUDE=$W/tests/fake_claude.py BAUDE_UPDATE=0 BAUDE_GH= CLAUDE_CONFIG_DIR=$H/cc
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
mkdir -p $BAUDE_RUNTIME_DIR
run() { perl -e "alarm ${T:-150}; exec @ARGV" python3 -u "$@" -ApplePersistenceIgnoreState YES; }
