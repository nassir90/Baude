# Signing Baude's notifier on macOS

Baude and Record post macOS notifications through a small notifier app each, `Baude Notifier.app` and
`Record Notifier.app`, kept in `~/.local/state/baude/notifier/`. Baude builds them from `tools/notifier/main.swift` with
`swiftc` (from the Xcode Command Line Tools: `xcode-select --install`) when it starts or is installed, and again when the
source or the logo changes. Through them a notification shows the app's own name and logo, an Open button that opens
the chat, and a Bot's picture.

macOS remembers that you allowed an app's notifications by the app's signature. An app signed only ad hoc (as
`swiftc` leaves it) gets a new signature each time it's built, so macOS would forget the permission at every rebuild.
So the notifiers are signed with a certificate made on your Mac, once, which gives them the same signature from one
build to the next:

```bash
tools/macos-sign.sh
```

It prints each thing it makes. Run it again any time: it makes nothing a second time, and signs the notifiers that are
built. If none are built yet, Baude builds and signs them the next time it starts. Until then, and on a Mac where the
notifiers can't post, notifications come from Script Editor as before.

## What it makes

All of it is in `~/.local/state/baude/signing/` (the folder is mode 0700):

- `signing.keychain-db`: a keychain of its own, holding one certificate, "Baude Local Code Signing", and its private
  key. It isn't your login keychain. `security create-keychain` adds every keychain it makes to your keychain search
  list, so the tool writes that list briefly and puts the list it read just before back at once; after that the
  keychain isn't in it, and `codesign` is pointed at the file directly. The keychain is locked again as soon as the
  tool has signed, and locks itself after five minutes and when the Mac sleeps.
- `signing.password`: the random password that unlocks that keychain (mode 0600). A file beside the keychain rather
  than an item in your login keychain, so that making and using the certificate never writes to or prompts from your
  login keychain. Any process running as you can read it, the same as the rest of your home folder.
- `signing.identity`, only if you sign with your own identity instead (below): the identity you named.

The certificate is self-signed: RSA 3072, for code signing only, valid for ten years. The private key is made on your
Mac by `openssl` and goes straight into that keychain.

## What it is and isn't

It is a signature that stays the same each time the notifiers are rebuilt, so macOS knows them as the same apps and
remembers that you allowed their notifications.

It isn't trusted for anything else, by macOS or by anyone. It isn't an Apple Developer ID, it isn't notarized, and it
doesn't vouch for Baude to Gatekeeper, on this Mac or any other. It isn't added to any trust settings, so nothing
accepts it for TLS, mail or installers.

It isn't a lock against programs you run. Any process running as you can read `signing.password`, unlock the keychain
and silently sign an app that macOS treats as your notifier, and that app inherits the notification permission you
gave it: it can post notifications as Baude or Record. That's what the certificate is worth to someone who already
runs code as you, and why it's kept to that one use.

It stays on your Mac: nothing is uploaded or sent anywhere, and Baude never copies it to a host or the phone.

**Never share the private key**, `signing.keychain-db` or `signing.password`. Whoever has them can sign an app that macOS
will treat as your notifier. Don't commit them, back them up to a shared place, or copy them to another Mac; on another
Mac, run the tool there to make that Mac's own.

## What macOS asks

- The first time each notifier posts, macOS asks whether **Baude** (and, separately, **Record**) may send
  notifications. Choose Allow. You can change it later in System Settings → Notifications.
- Nothing else: the tool doesn't ask for your password, doesn't touch the login keychain and doesn't change any trust
  settings. The keychain search list is written only for the moment described above.

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

# your keychain search list: the signing keychain isn't in it
security list-keychains -d user
```

## Removing it

```bash
tools/macos-sign.sh --remove                 # deletes signing.keychain-db, signing.password and signing.identity
rm -rf ~/.local/state/baude/notifier         # the notifier apps
```

Baude then posts through Script Editor again. To take Baude and Record out of System Settings → Notifications as well,
turn them off there.

## Signing with your own Apple Developer ID instead

If you have an Apple Developer account, you can sign the notifiers with your Developer ID Application certificate
from your own keychain, and the tool makes nothing. It checks that the identity is a valid code-signing identity in
your keychains, and keeps its name in `signing.identity`:

```bash
security find-identity -v -p codesigning          # find your identity's name or SHA-1
export BAUDE_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
tools/macos-sign.sh
```

Baude builds and signs the notifiers itself as it starts, with the kept identity whether or not that start sees
`BAUDE_SIGN_IDENTITY`, so the signer doesn't change with how Baude was started. To go back to the certificate made on
your Mac, run `tools/macos-sign.sh --remove` and then `tools/macos-sign.sh`. The first time `codesign` uses that key,
macOS asks whether codesign may use it; choose Always Allow for codesign only.

If a build or signing fails, Baude says why once and doesn't try again at each start until the source, the logo or
the signing identity changes.
