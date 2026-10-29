# BariSign — plan v2 (single device, no Shortcuts)

Supersedes the two-device / Shortcut plan in `PHASE0_REPORT.md`. Written 2026-10-29 after the user's
decision to rebuild the flow around one device.

## The shape of it

Everything runs **on the device that will get the app**, inside a-Shell Python. There is no "signer"
device and no "target" device, and no Apple ID ever touches a server we do not control.

```
a-Shell on the iPhone/iPad
  1. UDID        median.co profile -> paste 40/24 hex characters
  2. Apple ID    anisette + SRP-6a + 2FA                        (verified)
  3. portal      register UDID, App ID, certificate, profile    (written, untested)
  4. download    the chosen variant's IPA, resolved at runtime
  5. sign        Mach-O + entitlements + CodeResources + CMS    (to build - the big piece)
  6. package     signed .ipa inside a .zip
       |
       +--> AirDrop the zip to any second device, unzip, AirDrop the .ipa back
            iOS installs it on arrival (verified with a hand-made test IPA)
```

The zip round trip exists only because iOS cannot AirDrop a file to itself. Any second device works; a Mac
or another iPad is enough.

## Why the UDID came off our own server

The local certificate authority worked (device-verified), but it cost the user a two-step install and a
trust switch, and it broke whenever the CA file was not carried over. The median.co profile is one tap and
shows a copy button, so the script just asks for the pasted value. The local route stays as
`udid --local` for when median.co is unreachable; nothing else depends on it.

## Pairing file — decided: fork the three apps

**Decision (2026-10-29): fork.** The pairing module goes into the start screen of SideStore, of the
SideStore inside LiveContainer+SideStore, and of AltStore Classic. `docs/FORK_PLAN.md` holds the design,
the licence rules (clean-room reimplementation — the non-commercial SideInstaller code may not be mixed
into AGPL forks) and the risks.

Facts that make it possible, checked 2026-10-29:

- iOS **27** added **Settings → Privacy & Security → Developer Mode → "Pair with <app>"** with a six-digit
  code. SideInstaller uses exactly this to pair with no computer.
- SideStore, AltStore and LiveContainer also accept an **imported** pairing file, which is how the module's
  output gets consumed.
- Licences: SideStore **AGPL-3.0**, AltStore **AGPL-3.0**, LiveContainer **AGPL-3.0**; SideInstaller is
  "Other"/non-commercial and stays isolated in `third_party/sideinstaller/`.

Cost accepted with the decision: our patch to three AGPL projects, rebuilt on every upstream release, on
GitHub-hosted macOS CI.

## Licence consequences (verified)

Forks would force AGPL-3.0 on those parts and make "keep up with every release" a permanent job; that is
the main reason A is recommended. Whatever is chosen, `third_party/sideinstaller/` stays isolated under
its non-commercial licence, so the repo as a whole stays **non-commercial**.

## Order of work

1. UDID via median.co + single-device wording — **done** (untested on device).
2. Portal against Apple — first run will show what breaks.
3. **Signer** — Mach-O signature injection, entitlements, CodeResources, CMS SignedData with Apple's
   `1.2.840.113635.100.9.1`/`.9.2` OIDs; then zip packaging. This is the whole project.
4. Pairing: pick A or B, then build it.
5. Turkish translation of every user-facing string, last.

## Explicitly dropped

- Shortcut management (`SHORTCUT_BUILD_GUIDE.md`): suspended at the user's request.
- Cloudflare Worker / SSH tunnel / DuckDNS fallbacks: deleted earlier, still unnecessary.
- `itms-services` loopback: rejected by iOS, documented in `PHASE0_REPORT.md` §5.
