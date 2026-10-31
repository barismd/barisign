# AGENTS.md — BariSign

Project goal: a public GitHub repo that installs **SideStore** (and LiveContainer+SideStore)
on an iPhone/iPad **with no computer**, using two devices with roles:
the **signer** (runs a-Shell + BariSign) and the **target** (the device that gets the app).
Both may be an iPhone or an iPad; only the role matters. Same Wi-Fi required.

## Confirmed decisions (user)

- **Audience = everyone, no computer.** Users are expected to sign in with their **main Apple ID**
  (2FA supported); the README must say plainly why that is safe.
- **Two-role flow (2026-10-31 pivot):** a-Shell runs **only on the signer**. The signer does
  login, portal, signing, packaging and the install. The **target** only uses Safari and Settings.
  Both devices may be iOS or iPadOS; they must be on the same Wi-Fi. No computer, no AirDrop, no OTA.
- **UDID = pairing first.** The signer pairs the target; the pairing/tunnel reports the target's UDID,
  which the signer saves and registers. No median profile in the normal path. If pairing cannot provide
  it, fall back to a short-lived HTTP page served by the signer: the target opens
  `http://<signer-ip>:<port>/`, installs the **median.co/udid** profile, copies the UDID and submits it.
  `docs/UDID_OPTIONS.md` lists other sites; no permanent server of ours.
- **Shortcuts are suspended** (user decision). The interface is a plain text flow in a-Shell.
- **Pairing file = a module inside the apps (user decision, 2026-10-29):** fork SideStore, AltStore
  Classic and LiveContainer+SideStore, add a pairing-file step to each start screen, rebuild on every
  upstream release. All three are **AGPL-3.0**, so our patch is published under AGPL-3.0 in
  `forks/<app>/`; the non-commercial `third_party/sideinstaller/` code may **not** be mixed into them —
  the pairing call is reimplemented clean-room. Design, risks and order: `docs/FORK_PLAN.md`.

- **Target = iOS 27+ only.** iOS 17.4–26.x is out of scope (pairing file needs a PC). Stated in `README.md`.
- **Install = pairing + `installation_proxy` from the signer.** AirDrop and OTA are dead for
  free-profile apps on iOS 27: `MIInstallerErrorDomain` 111, *"free provisioning profile ... not
  allowed to be installed from this source"*. The signer runs `sh bin/barisign pair`; the target picks
  **BariSign** under Settings → Developer → Paired Macs and enters the code. See
  `docs/PAIRING_INSTALL.md` and `docs/RESULTS.md`.
- **Languages:** English only for now; English + Turkish in the final phase, after it works.
- Test devices are roles, not hardware: either device can be the signer or the target. Both run
  iOS/iPadOS 27+; a-Shell is installed only on the signer.
- On device and verified: UDID, Apple sign-in, the **developer portal**, the signer's signed IPA
  over USB (`ideviceinstaller`), and AltStore launching after the app-group fix. The pair-first UDID,
  the fallback UDID page and the signer→target pairing install are written but not yet device-verified.
- Sign-in requires **Mac-emulating anisette** and the server's own client_info (Apple's Oct 2026
  -80045/-80046/503 rules). `mac-ani.jkcoxson.com` and `ani.jkcoxson.com` come first. Details:
  `docs/ANISETTE.md`, `docs/RESULTS.md`.
- The UI is deliberately terse: one `== STAGE ==` header per screen, one moving line `(2/5) label ....`,
  `ESC[2J ESC[3J` on stage change, `ESC[2K` for the moving line. No long narration.
- Terminal: never change modes more than the password prompt needs (ECHO only, restored and verified);
  the 2FA code is read with our own visible echo so it works even when echo is off.
- Never use GitHub `releases/latest` for SideStore (it returns `0.7.0-alpha`); list releases and
  reject `alpha`/`beta`/`rc` tags.
- **Licence constraint:** code ported from `FrizzleM/SideInstaller` is **isolated** in
  `third_party/sideinstaller/` under its own **non-commercial, source-only** licence (with the
  licence text, attribution and a list of our changes), so the repo as a whole cannot be MIT or
  commercial. Never copy that code into MIT files; prefer reimplementing from MIT sources
  (`nab138/isideload`, `jkcoxson/idevice`). See `THIRD_PARTY_NOTICES.md`.
- **pymobiledevice3** (GPL-3.0) is installed **separately** on the signer by `bin/barisign pair`; it is not
  vendored and no code from it is copied into this repository. The wrapper is MIT; if redistributed
  with the library, treat that combination as GPL-3.0.
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
- runs the heavy steps on the **signer**: download the original (optional Developer Mode trigger),
  pair the target and take its UDID, Apple login with anisette (retry with another anisette server on
  failure), sign the chosen IPA.
- Ends with `sh bin/barisign pair`: the signer advertises as a pairable host, the **target** opens
  Settings → Developer → Paired Macs, enters the code, and the signer installs over Wi-Fi.

## Hard constraints

- Everything on the **signer** must run in a-Shell, no computer. The **target** needs only Safari and Settings.
  - Compute layer: **a-Shell (full)** — stdlib-only Python plus the `cryptography` that ships with it;
    no native binaries, no `openssl`, no `unzip`.
  - Evaluate alternatives (other free on-device runtimes) and justify.
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
- Stages 1–2 are verified on device, 3 is written; the plan is `docs/PLAN_V2.md`, the verified facts
  are in `docs/RESULTS.md`.
- Next: the pair-first UDID and `bin/barisign pair` on a device, then the forks, then i18n + Turkish last.

## Verified / unverified convention

Anything not confirmed by reading real source or authoritative docs is marked **unverified**.
