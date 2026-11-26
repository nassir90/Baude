#!/bin/sh
# Signs Baude's notifier apps on macOS with a code-signing certificate made on this Mac, so Notification Center lets
# them post. See docs/macos-signing.md.
#
#   tools/macos-sign.sh [APP ...]   make the certificate if there isn't one, then sign each APP (default: the notifier
#                                   apps Baude has built)
#   tools/macos-sign.sh --identity  print the certificate's SHA-1 if there is one (exit 1 if not); makes nothing
#   tools/macos-sign.sh --remove    delete the certificate's keychain and its password file
#
# Everything lives in one keychain file of its own, $BAUDE_SIGNING (default ~/.local/state/baude/signing), never the
# login keychain: signing.keychain-db, holding the certificate and its private key, and signing.password, the random
# password that unlocks it (mode 0600). BAUDE_SIGN_IDENTITY names another identity to sign with instead (your Apple
# Developer ID's name or SHA-1, from your own keychains); then nothing is made.
set -eu

NAME="Baude Local Code Signing"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/baude"
DIR="${BAUDE_SIGNING:-$STATE/signing}"
KEYCHAIN="$DIR/signing.keychain-db"
PASSFILE="$DIR/signing.password"

say() { printf '%s\n' "$*"; }
fail() { printf 'macos-sign: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || fail "this is for macOS"

identity() {
    # The certificate's SHA-1, if the keychain has it (it's unlocked first).
    [ -f "$KEYCHAIN" ] && [ -f "$PASSFILE" ] || return 1
    security unlock-keychain -p "$(cat "$PASSFILE")" "$KEYCHAIN" >/dev/null 2>&1 || return 1
    security find-certificate -c "$NAME" -Z "$KEYCHAIN" 2>/dev/null | awk '/^SHA-1 hash:/ {print $3; exit}' | grep .
}

make_identity() {
    # A self-signed certificate for code signing (ten years), its private key made here and kept only in the keychain.
    umask 077
    mkdir -p "$DIR"
    chmod 700 "$DIR"
    if [ ! -f "$KEYCHAIN" ]; then
        [ -f "$PASSFILE" ] || { /usr/bin/openssl rand -hex 32 >"$PASSFILE"; say "created $PASSFILE (the keychain's password, mode 0600)"; }
        chmod 600 "$PASSFILE"
        before=$(security list-keychains -d user)
        security create-keychain -p "$(cat "$PASSFILE")" "$KEYCHAIN"
        # (kept out of the search list: codesign is pointed at the file with --keychain)
        if [ "$(security list-keychains -d user)" != "$before" ]; then
            eval "security list-keychains -d user -s $before"
        fi
        security set-keychain-settings "$KEYCHAIN"  # (no locking after a timeout or on sleep)
        say "created $KEYCHAIN (a keychain of its own, not in the keychain search list)"
    fi
    security unlock-keychain -p "$(cat "$PASSFILE")" "$KEYCHAIN"
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
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
    trap - EXIT
    say "created the certificate \"$NAME\" and its private key in $KEYCHAIN (self-signed, for code signing, valid ten years)"
}

case "${1:-}" in
--identity)
    if [ -n "${BAUDE_SIGN_IDENTITY:-}" ]; then say "$BAUDE_SIGN_IDENTITY"; exit 0; fi
    identity || exit 1
    exit 0
    ;;
--remove)
    [ -f "$KEYCHAIN" ] && { security delete-keychain "$KEYCHAIN"; say "deleted $KEYCHAIN"; }
    [ -f "$PASSFILE" ] && { rm -f "$PASSFILE"; say "deleted $PASSFILE"; }
    rmdir "$DIR" 2>/dev/null || true
    exit 0
    ;;
-h|--help)
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac

if [ -n "${BAUDE_SIGN_IDENTITY:-}" ]; then
    sign_as="$BAUDE_SIGN_IDENTITY"
    keychain_args=""
else
    sign_as=$(identity) || { make_identity; sign_as=$(identity) || fail "the certificate isn't in $KEYCHAIN"; }
    keychain_args="--keychain $KEYCHAIN"
fi

if [ $# -eq 0 ]; then
    set -- "$STATE"/notifier/*.app
    [ -d "$1" ] || { set --; say "no notifier app built yet: Baude builds and signs it the next time it starts"; }
fi
for app in "$@"; do
    [ -d "$app" ] || fail "no app at $app"
    # shellcheck disable=SC2086
    codesign --force --options runtime --timestamp=none $keychain_args --sign "$sign_as" "$app" 2>&1 \
        | grep -v ': replacing existing signature$' || true
    codesign --verify --strict "$app" || fail "$app didn't verify"
    say "signed $app with $sign_as"
done
