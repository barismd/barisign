# What is verified — and what we learned the hard way

Replaces the old phase-0 report (the two-device plan it described no longer exists; see `PLAN_V2.md`).
Everything here was confirmed on a real **iPhone 13, iOS 27** unless marked otherwise.

## Verified on device

| Thing | Result |
|---|---|
| Apple sign-in: anisette v3 + SRP-6a + 2FA, free Apple ID | works (`LOGIN: PASS`) |
| Installing an **AirDropped** signed IPA | iOS installs it by itself; no extra "Install" tap |
| a-Shell running our Python stack (`cryptography` 3.4.8, stdlib only) | works; no native binaries, no `openssl`/`unzip` |

## Written but **never tested on a device**

- `portal`: register UDID → App ID → certificate → provisioning profile.
- The signer (Mach-O + entitlements + CodeResources + Apple CMS) and the `.zip` packaging. On Linux it was
  checked three ways: every signature verifies with an independent codesign implementation
  (apple-codesign 0.29), every CMS verifies with openssl, and the generated `CodeResources` matches
  Apple's own for AltStore (same key sets, same rules, identical hashes for files we did not touch).
  Nothing has been installed from it yet.

## Not built

- The pairing module in the forked apps.

## a-Shell quirks that shaped the code (all device-confirmed)

| Symptom | Consequence |
|---|---|
| `getpass` hangs (it opens `/dev/tty`) | never used; prompts use a cbreak byte reader |
| `termios` ECHO-off + `readline()` hangs | same; masked passwords are read byte by byte |
| plain `input()` works, but not after a masked read | prompts fall back to `input()` carefully |
| `\r` and `\n` are not interchangeable | the byte reader accepts both |
| the `open` command is broken (deprecated `keyWindow`, silent `openurl` fallback) | use `view <abs path>` (opens a preview with a share button) |
| there is **no** `share` command | sharing goes through the preview |
| only `~/Documents` is writable | state, CA and profiles all live inside the install folder |
| Python's TLS trust store is **empty** | Apple's root and a CA bundle ship in `third_party/` and are loaded explicitly |
| `raw.githubusercontent.com` serves stale files after a push | the installer fetches through the GitHub API, with the web URL only as a fallback |
| the `+x` bit does not survive git on device | always run `sh bin/...` |

## Dead ends — do not retry without new information

| Idea | Why it failed |
|---|---|
| `itms-services://` pointing at the device itself (loopback OTA) | iOS rejects it |
| `itms-services://` over the LAN with a trusted cert | needs an Ad Hoc/Enterprise certificate; free accounts cannot |
| One profile that both installs the CA and reads the UDID | nested `Profile Service` → iOS reports "unknown data" |
| An MDM profile to read the UDID in one step | iOS keeps answering "the password is not correct" even with a hand-built legacy PKCS#12 identical to `openssl pkcs12 -export -legacy` |
| A pure on-device install with no second device | blocked by Apple's Developer Mode gate ("Developer Mode only appears if you initiate pairing or previously connected the device to Xcode") |
| Cloudflare Worker / SSH tunnel / DuckDNS + ACME | unnecessary; deleted |
| Our own UDID server (persistent CA + local HTTPS, served from a-Shell) | it worked, and it is still verified — but it cost a second install, a trust switch and a CA file to carry. Removed; a pasted UDID from any profile service replaces it (`docs/UDID_OPTIONS.md`) |

## Apple's October 2026 sign-in changes (why login broke and what fixes it)

Apple changed the rules twice in one month, and both changes are about the **platform we claim**
(`X-Mme-Client-Info`) relative to the platform the anisette data was minted on:

| Date | What Apple did | What broke |
|---|---|---|
| 2026-09-11 | answers **HTTP 503** to any sign-in whose `X-Mme-Client-Info` contains `com.apple.dt.Xcode` | everything that still claimed Xcode ([SideStore #1506](https://github.com/SideStore/SideStore/issues/1506)) |
| 2026-10-12 | refuses a sign-in whose claimed platform differs from the anisette data (**-80045**) | claiming Mac while the anisette came from Android code |
| ~2026-10-22 | refuses the **Android** claim itself (**-80046**, sometimes a bare 503) | claiming Android with Mac anisette |
| 2026-10-24 | working setup: an anisette server that **emulates a Mac** + claim what it says | — |

So the only reliable combination now is: a **Mac-emulating anisette server**, and the
**client_info/user_agent that server reports** — not a hardcoded string. Verified live (2026-10-29):
`mac-ani.jkcoxson.com`, `ani.jkcoxson.com` and `ani.sidestore.io` all report a Mac client info; BariSign
uses `mac-ani.jkcoxson.com` first, adopts its strings, and fails over to a new identity on
-80045 / -80046 / 503.

The servers themselves, and what they can see: `docs/ANISETTE.md`.

Sources: [SideStore #1506](https://github.com/SideStore/SideStore/issues/1506),
[iloader #921](https://github.com/nab138/iloader/issues/921), and Ixion's write-up
*"iOS sideloading error -80046"* (2026-10-24), which lists the tools that shipped the fix
(xtool 1.23.0, iloader 2.4.0, atvloadly 0.7.4).

## Facts about the apps and the account

- **Free Apple ID:** 3 apps installed at a time (SideStore counts as one), a 10 App ID limit, 7-day signing.
- **Licences:** SideStore, AltStore and LiveContainer are **AGPL-3.0**; `FrizzleM/SideInstaller` is a
  non-commercial "Other" licence, kept isolated in `third_party/sideinstaller/`.
- **SideStore releases:** `/releases/latest` returns `0.7.0-alpha` (with `prerelease: false`), so the
  resolver must list releases and reject `alpha`/`beta`/`rc` tags.
- **Nightly releases** are rolling: their `published_at` is stale — show the asset's `updated_at` instead.
- **AltStore Classic** embeds the version in the asset name — resolve by pattern, never by filename.
- **`median.co/udid`** serves a one-tap UDID profile (verified by their documentation; the BariSign flow
  that pastes its output is written, not yet device-tested).
