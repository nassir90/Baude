# Signing Baude's notifier on macOS

Baude and Record post macOS notifications through a small notifier app each, `Baude Notifier.app` and
`Record Notifier.app`, kept in `~/.local/state/baude/notifier/`. Baude builds them from `tools/notifier/main.swift` with
`swiftc` (from the Xcode Command Line Tools: `xcode-select --install`) when it starts or is installed, and again when the
source or the logo changes. Through them a notification shows the app's own name and logo, an Open button that opens
the chat, and a Bot's picture.

macOS only lets a signed app post notifications, and an app it has let post is known by its signature. So the
notifiers are signed with a certificate made on your Mac, once:

```bash
tools/macos-sign.sh
```

It prints each thing it makes. Run it again any time: it makes nothing a second time, and signs the notifiers that are
built. If none are built yet, Baude builds and signs them the next time it starts. Until then, and on a Mac where the
notifiers can't post, notifications come from Script Editor as before.

## What it makes

All of it is in `~/.local/state/baude/signing/` (the folder is mode 0700):

- `signing.keychain-db`: a keychain of its own, holding one certificate, "Baude Local Code Signing", and its private
  key. It isn't your login keychain, and it isn't added to the keychain search list, so nothing else on your Mac sees
  it. `codesign` is pointed at the file directly.
- `signing.password`: the random password that unlocks that keychain (mode 0600). A file beside the keychain rather
  than an item in your login keychain, so that making and using the certificate never writes to or prompts from your
  login keychain. Anyone who can read your files can read it, the same as the rest of your home folder.

The certificate is self-signed: RSA 3072, for code signing only, valid for ten years. The private key is made on your
Mac by `openssl` and goes straight into that keychain.

## What it is and isn't

It is a signature that stays the same each time the notifiers are rebuilt, so macOS knows them as the same apps and
remembers that you allowed their notifications.

It isn't trusted by macOS or by anyone else. It isn't an Apple Developer ID, it isn't notarized, and it doesn't vouch
for Baude to Gatekeeper on any other Mac. It signs only the notifiers built on this Mac, and only you can sign with it.

It stays on your Mac: nothing is uploaded or sent anywhere, and Baude never copies it to a host or the phone.

**Never share the private key**, `signing.keychain-db` or `signing.password`. Whoever has them can sign an app that macOS
will treat as your notifier. Don't commit them, back them up to a shared place, or copy them to another Mac; on another
Mac, run the tool there to make that Mac's own.

## What macOS asks

- The first time each notifier posts, macOS asks whether **Baude** (and, separately, **Record**) may send
  notifications. Choose Allow. You can change it later in System Settings → Notifications.
- Nothing else: the tool doesn't ask for your password, doesn't touch the login keychain and doesn't change any trust
  settings.

If you remove the certificate and make a new one, the notifiers' signature changes and macOS may ask again.

## Checking it

```bash
# the certificate and its key, in its own keychain (it shows as CSSMERR_TP_NOT_TRUSTED: self-signed, as it should be)
security find-identity ~/.local/state/baude/signing/signing.keychain-db

# its SHA-1, as the tool uses it
tools/macos-sign.sh --identity

# a notifier's signature: Authority=Baude Local Code Signing, flags=0x10000(runtime) (the hardened runtime)
codesign -dv ~/.local/state/baude/notifier/"Baude Notifier.app"
codesign --verify --strict --verbose ~/.local/state/baude/notifier/"Baude Notifier.app"

# your keychain search list is as it was: the signing keychain isn't in it
security list-keychains -d user
```

## Removing it

```bash
tools/macos-sign.sh --remove                 # deletes signing.keychain-db and signing.password
rm -rf ~/.local/state/baude/notifier         # the notifier apps
```

Baude then posts through Script Editor again. To take Baude and Record out of System Settings → Notifications as well,
turn them off there.

## Signing with your own Apple Developer ID instead

If you have an Apple Developer account, you can sign the notifiers with your Developer ID Application certificate
from your own keychain, and the tool makes nothing:

```bash
security find-identity -v -p codesigning          # find your identity's name or SHA-1
export BAUDE_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
tools/macos-sign.sh
```

Baude builds and signs the notifiers itself as it starts, so set `BAUDE_SIGN_IDENTITY` where Baude's launcher will see
it too: the launchers start Baude through your login shell, so `~/.zprofile` (zsh) or `~/.bash_profile` (bash).
The first time `codesign` uses that key, macOS asks whether codesign may use it; choose Always Allow for codesign only.
