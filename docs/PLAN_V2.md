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

## Pairing file — the open decision

SideStore, AltStore and LiveContainer all need a **pairing file** (`ALTPairingFile.mobiledevicepairing`) to
install and refresh apps. Facts, all checked 2026-10-29:

- iOS **27** added **Settings → Privacy & Security → Developer Mode → "Pair with <app>"** (six-digit code).
  That is the mechanism SideInstaller uses to pair with no computer.
- SideStore, AltStore and LiveContainer all accept an **imported** pairing file from their own UI.
- Licences: SideStore **AGPL-3.0**, AltStore **AGPL-3.0**, LiveContainer **AGPL-3.0**
  (`FrizzleM/SideInstaller` is "Other"/non-commercial, already isolated in `third_party/sideinstaller/`).

So there are two ways to give the user the "generate the pairing file from the app" experience:

| | A — helper app (recommended) | B — fork the three apps |
|---|---|---|
| How | our own small app triggers the iOS 27 pairing UI and exports the file; the user imports it once in SideStore / AltStore | add a pairing module to each app's start screen |
| Licence | MIT, no AGPL obligations | AGPL-3.0 forks, source published, notices |
| Maintenance | none beyond the pairing call | rebuild three AGPL projects on every upstream release, macOS CI, keep patches alive |
| Risk | user sees two extra taps | fork rot; three moving targets |

**Recommendation: A.** It reaches the same user-visible outcome on iOS 27 without owning three forks. The
final call is the user's; nothing in stages 1–6 depends on it.

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
