#!/bin/sh
# Signs Baude's notifier apps on macOS with a code-signing certificate made on this Mac, so their signature stays the
# same from one build to the next and macOS keeps the notification permission you gave them. See docs/macos-signing.md.
#
#   tools/macos-sign.sh [APP ...]   make the certificate if there isn't one, then sign each APP (default: the notifier
#                                   apps Baude has built)
#   tools/macos-sign.sh --identity  print the signing identity's SHA-1 if there is one (exit 1 if not); makes nothing
#   tools/macos-sign.sh --remove    delete the certificate's keychain, its password file and a kept BAUDE_SIGN_IDENTITY
#
# Everything lives in one keychain file of its own, $BAUDE_SIGNING (default ~/.local/state/baude/signing), never the
# login keychain: signing.keychain-db, holding the certificate and its private key, and signing.password, the random
# password that unlocks it (mode 0600). The keychain is locked again once it's used, and locks itself after five
# minutes and on sleep. BAUDE_SIGN_IDENTITY names another identity to sign with instead (your Apple Developer ID's name
# or SHA-1, from your own keychains); then nothing is made, and it's kept in signing.identity, so a start of Baude that
# doesn't see the variable signs with it too.
set -eu

NAME="Baude Local Code Signing"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/baude"
DIR="${BAUDE_SIGNING:-$STATE/signing}"
KEYCHAIN="$DIR/signing.keychain-db"
PASSFILE="$DIR/signing.password"
CHOSEN="$DIR/signing.identity"
LOCK='security lock-keychain "$KEYCHAIN" 2>/dev/null || true'

say() { printf '%s\n' "$*"; }
fail() { printf 'macos-sign: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || fail "this is for macOS"

own_dir() {
    # The folder made, mode 0700, if it isn't there (one that is is left as it is).
    umask 077
    [ -d "$DIR" ] || { mkdir -p "$DIR"; chmod 700 "$DIR"; }
}

identity() {
    # The certificate's SHA-1, if the keychain has it (a certificate's readable with the keychain locked).
    [ -f "$KEYCHAIN" ] || return 1
    security find-certificate -c "$NAME" -Z "$KEYCHAIN" 2>/dev/null | awk '/^SHA-1 hash:/ {print $3; exit}' | grep .
}

chosen() {
    # BAUDE_SIGN_IDENTITY's SHA-1 (else the one kept from it), if it's a valid code-signing identity in your keychains;
    # a newly named one's kept. Exit 1: none named; 2: named, and not there.
    want="${BAUDE_SIGN_IDENTITY:-}"
    if [ -z "$want" ] && [ -f "$CHOSEN" ]; then want=$(cat "$CHOSEN"); fi
    [ -n "$want" ] || return 1
    sha=$(security find-identity -v -p codesigning | awk -v want="$want" '
        /^ *[0-9]+\) [0-9A-Fa-f]+ "/ {
            sha = $2; name = $0; sub(/^[^"]*"/, "", name); sub(/"[^"]*$/, "", name)
            if (toupper(want) == toupper(sha) || want == name) { print sha; exit }
        }')
    [ -n "$sha" ] || return 2
    if [ -n "${BAUDE_SIGN_IDENTITY:-}" ] && [ "$(cat "$CHOSEN" 2>/dev/null)" != "$want" ]; then
        own_dir
        printf '%s\n' "$want" >"$CHOSEN"
    fi
    printf '%s\n' "$sha"
}

not_there() {
    fail "no valid code-signing identity \"${BAUDE_SIGN_IDENTITY:-$(cat "$CHOSEN" 2>/dev/null)}\" in your keychains" \
        "(security find-identity -v -p codesigning; tools/macos-sign.sh --remove forgets a kept one)"
}

restore_search_list() {
    # The user keychain search list put back to $1 (security list-keychains' own output: one quoted path a line).
    list=$1
    set --
    while IFS= read -r line; do
        line=${line#"${line%%[![:space:]]*}"}
        line=${line#\"}
        line=${line%\"}
        [ -n "$line" ] && set -- "$@" "$line"
    done <<END
$list
END
    security list-keychains -d user -s "$@"
}

unlock() {
    # The keychain unlocked for codesign, and locked again as this exits.
    trap "$LOCK" EXIT
    security unlock-keychain -p "$(cat "$PASSFILE")" "$KEYCHAIN"
}

make_identity() {
    # A self-signed certificate for code signing (ten years), its private key made here and kept only in the keychain.
    own_dir
    if [ -f "$KEYCHAIN" ] && [ ! -f "$PASSFILE" ]; then
        fail "$PASSFILE is missing, so $KEYCHAIN can't be unlocked." \
            "Run tools/macos-sign.sh --remove, then this again, to make a new certificate"
    fi
    if [ ! -f "$KEYCHAIN" ]; then
        [ -f "$PASSFILE" ] || { /usr/bin/openssl rand -hex 32 >"$PASSFILE"; say "created $PASSFILE (the keychain's password, mode 0600)"; }
        chmod 600 "$PASSFILE"
        before=$(security list-keychains -d user)
        security create-keychain -p "$(cat "$PASSFILE")" "$KEYCHAIN"
        # (create-keychain adds it to the search list: taken out again at once, codesign is pointed at the file)
        if [ "$(security list-keychains -d user)" != "$before" ]; then
            restore_search_list "$before"
        fi
        security set-keychain-settings -l -u -t 300 "$KEYCHAIN"  # (locks itself after five minutes and on sleep)
        say "created $KEYCHAIN (a keychain of its own, taken out of the keychain search list as it's made)"
    fi
    unlock
    work=$(mktemp -d)
    trap 'rm -rf "$work"; '"$LOCK" EXIT
    cat >"$work/req.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
subjectKeyIdentifier = hash
EOF
    /usr/bin/openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 -nodes -config "$work/req.cnf" \
        -keyout "$work/key.pem" -out "$work/cert.pem" 2>/dev/null
    p12pass=$(/usr/bin/openssl rand -hex 16)
    /usr/bin/openssl pkcs12 -export -inkey "$work/key.pem" -in "$work/cert.pem" -name "$NAME" \
        -passout "pass:$p12pass" -out "$work/id.p12"
    security import "$work/id.p12" -k "$KEYCHAIN" -f pkcs12 -P "$p12pass" -T /usr/bin/codesign >/dev/null
    # (codesign may use the key without asking: Apple's tools are in its partition list)
    security set-key-partition-list -S apple-tool:,apple: -s -k "$(cat "$PASSFILE")" "$KEYCHAIN" >/dev/null
    rm -rf "$work"
    trap "$LOCK" EXIT
    say "created the certificate \"$NAME\" and its private key in $KEYCHAIN (self-signed, for code signing, valid ten years)"
}

case "${1:-}" in
--identity)
    if sha=$(chosen); then say "$sha"; exit 0; elif [ $? -eq 2 ]; then not_there; fi
    identity || exit 1
    exit 0
    ;;
--remove)
    [ -f "$KEYCHAIN" ] && { security delete-keychain "$KEYCHAIN"; say "deleted $KEYCHAIN"; }
    [ -f "$PASSFILE" ] && { rm -f "$PASSFILE"; say "deleted $PASSFILE"; }
    [ -f "$CHOSEN" ] && { rm -f "$CHOSEN"; say "deleted $CHOSEN"; }
    [ -n "${BAUDE_SIGNING:-}" ] || rmdir "$DIR" 2>/dev/null || true  # (only the folder it makes itself)
    exit 0
    ;;
-h|--help)
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac

own=
if sign_as=$(chosen); then
    :
elif [ $? -eq 2 ]; then
    not_there
else
    own=1
    if sign_as=$(identity) && [ -f "$PASSFILE" ]; then
        unlock
    else
        make_identity
        sign_as=$(identity) || fail "the certificate isn't in $KEYCHAIN"
    fi
fi

if [ $# -eq 0 ]; then
    set -- "$STATE"/notifier/*.app
    [ -d "$1" ] || { set --; say "no notifier app built yet: Baude builds and signs it the next time it starts"; }
fi
for app in "$@"; do
    [ -d "$app" ] || fail "no app at $app"
    if [ -n "$own" ]; then
        codesign --force --options runtime --timestamp=none --keychain "$KEYCHAIN" --sign "$sign_as" "$app" 2>&1
    else
        codesign --force --options runtime --timestamp=none --sign "$sign_as" "$app" 2>&1
    fi | grep -v ': replacing existing signature$' || true
    codesign --verify --strict "$app" || fail "$app didn't verify"
    say "signed $app with $sign_as"
done
