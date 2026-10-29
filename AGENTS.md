# AGENTS.md — BariSign

Project goal: a public GitHub repo that installs **SideStore** (and LiveContainer+SideStore)
on an iPhone/iPad **with no computer**, driven entirely by an **iOS Shortcut**.

## Confirmed decisions (user)

- **Audience = everyone, no computer.** Users are expected to sign in with their **main Apple ID**
  (2FA supported); the README must say plainly why that is safe.
- **Single-device flow (2026-10-29 pivot):** every step runs on the device that will get the app.
  No signer device, no target device. The finished signed IPA is packaged in a **`.zip`**; the user sends
  the zip to any second device, unzips it there and AirDrops the `.ipa` back — iOS installs an AirDropped
  IPA by itself. The round trip exists only because iOS cannot AirDrop to itself.
- **UDID = the median.co profile**, one install tap, then paste the value into a-Shell. `udid --local`
  keeps the verified local-CA route as a fallback only.
- **Shortcuts are suspended** (user decision). The interface is a plain text flow in a-Shell.
- **Open item:** pairing file for SideStore / AltStore / LiveContainer. iOS 27's
  "Developer Mode → Pair with <app>" makes it possible on-device; the choice between a small helper app
  (recommended) and forking the three AGPL-3.0 apps is the user's. See `docs/PLAN_V2.md`.

- **Target = iOS 27+ only.** iOS 17.4–26.x is out of scope (pairing file needs a PC). Stated in `README.md`.
- **AirDrop is the primary install route.** Private-HTTPS `itms-services` is a documented fallback only;
  loopback `itms-services` is rejected (see `docs/PHASE0_REPORT.md` §5).
- **Languages:** English only for now; English + Turkish in the final phase, after it works.
- Test devices: **iPhone 13 (iOS 27)** = main target; **iPad (A16)** = AirDrop receiver.
- Phase 0/1: the UDID stage is verified (`poc/udid_share.py`); Apple sign-in is verified
  (`poc/poc_login.py`). Next: the developer portal (stage 3) and the signer (stage 4).
- Never use GitHub `releases/latest` for SideStore (it returns `0.7.0-alpha`); list releases and
  reject `alpha`/`beta`/`rc` tags.
- **Licence constraint:** code ported from `FrizzleM/SideInstaller` is **isolated** in
  `third_party/sideinstaller/` under its own **non-commercial, source-only** licence (with the
  licence text, attribution and a list of our changes), so the repo as a whole cannot be MIT or
  commercial. Never copy that code into MIT files; prefer reimplementing from MIT sources
  (`nab138/isideload`, `jkcoxson/idevice`). See `THIRD_PARTY_NOTICES.md`.
- Step 1 PoC: `poc/poc_login.py` (MIT) + `third_party/sideinstaller/signin.py` (non-MIT).

## What we are building

Five fixed variants, chosen in the a-Shell menu:

1. SideStore stable
2. SideStore nightly
3. LiveContainer + SideStore stable
4. LiveContainer + SideStore nightly
5. AltStore Classic stable

One flow, five IPAs. **No generic IPA signer.**

The a-Shell interface (no Shortcuts):
- asks readiness questions (LocalDevVPN installed & connected? Developer Mode on? Apple ID ready?),
- asks which variant,
- points to a complete beginner GUIDE,
- runs the heavy steps on the device itself: UDID (median.co profile), Apple login with anisette
  (retry with another anisette server on failure), download + sign the chosen IPA, package it as a zip.
- Ends with "AirDrop this zip to another device, unzip it, AirDrop the .ipa back, tap Install".

## Hard constraints

- Everything must be drivable from an iOS Shortcut and run **on-device, no computer**.
  - Default compute candidate: **a-Shell mini** (pure Python, JS, WASM; no native binaries,
    no C-extension pip packages).
  - Evaluate alternatives (pure Shortcuts actions, other free on-device runtimes) and justify.
- **Never send the Apple ID or password to any remote server we do not control.**
- A **free Apple ID must be enough** (no paid developer account).
- Development runs in a GitHub Codespace (Linux). We **cannot test on an iPhone** — the user
  tests and reports back. Label untested code **untested**; never claim something works without
  the user's on-device confirmation.
- Code, comments, commit messages and the main README are in **English**.
- **Localization**: all user-facing text (script messages, README) lives in
  per-language files so adding a language = adding a file. English first; translations only in
  the final phase, using the cheap model, after asking which languages.
- Interface: a plain text flow in a-Shell (`sh bin/barisign`), **no Shortcuts** and no generated
  `.shortcut` files. Every prompt is one line and every answer is a number, `y`/`n` or a pasted value,
  so a first-time user can follow it without help. All user-facing strings live in the i18n files.

## Variant rules

- Find the **official release sources** for each variant ourselves and verify them.
- Resolve each IPA URL **at runtime**, never hardcode versions. Show the user which version
  they are getting.
- Verify download integrity when checksums exist.
- Warn that nightly builds may be unstable.
- README must document (verified, not from memory) how the variants differ, including free
  Apple ID limits on App IDs and active apps.

## Secrets & safety

- Never commit Apple ID, passwords, pairing files, signing certs or API keys. `.gitignore` them.
- Never print environment variables or secrets, never write them to a file, and check staged
  changes for keys before every commit.
- Ask before destructive commands.
- Respect licenses: keep attribution and license files for ported code; list which licenses
  constrain this repo.

## Budget / process

- Budget ~5 USD total. Be token-frugal: grep/head/tail instead of dumping whole files.
- If the same problem fails twice, **stop** and report so the user can switch to a stronger model.
- Phases 0–3 are done (`docs/PHASE0_REPORT.md`); the current plan is `docs/PLAN_V2.md`.
- Next: the signer (stage 4), then the pairing-file decision, then i18n + Turkish last.

## Verified / unverified convention

Anything not confirmed by reading real source or authoritative docs is marked **unverified**.
