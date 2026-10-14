# AGENTS.md — BariSign

Project goal: a public GitHub repo that installs **SideStore** (and LiveContainer+SideStore)
on an iPhone/iPad **with no computer**, driven entirely by an **iOS Shortcut**.

## What we are building

Four fixed variants, chosen by the user inside the Shortcut:

1. SideStore stable
2. SideStore nightly
3. LiveContainer + SideStore stable
4. LiveContainer + SideStore nightly

One flow, four IPAs. **No generic IPA signer.**

The Shortcut:
- asks readiness questions (LocalDevVPN installed & connected? Developer Mode on? Apple ID ready?),
- asks which variant,
- points to a complete beginner README,
- runs the heavy steps through the chosen on-device compute layer:
  Apple login with anisette (retry with another anisette server on failure),
  download + sign the chosen IPA, pairing file, install.
- Prefers **AirDrop of the signed IPA** for install (iOS installs AirDropped IPAs on 17.1+,
  but they only launch if validly signed). Other install routes only if demonstrably better.

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
- **Localization**: all user-facing text (script messages, Shortcut prompts, README) lives in
  per-language files so adding a language = adding a file. English first; translations only in
  the final phase, using the cheap model, after asking which languages.
- The Shortcut itself: the user builds it by hand on the iPhone and shares via iCloud link.
  **Do NOT generate a `.shortcut` file.** Instead research the web and write
  `SHORTCUT_BUILD_GUIDE.md`: a numbered, action-by-action guide naming every Shortcuts action,
  its settings, variables and branching, written for a first-time Shortcuts user. All
  user-facing strings live in the i18n files and are referenced from the guide.

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
- Phase 0 (this): environment check + research table + feasibility verdicts. **STOP and wait.**
- After the user's go: implement everything (scripts, English README, i18n structure,
  SHORTCUT_BUILD_GUIDE.md) in one pass, mark what is untested, then iterate on test reports.
- Translations come last.

## Verified / unverified convention

Anything not confirmed by reading real source or authoritative docs is marked **unverified**.
