# BariSign

Install **SideStore** — or **LiveContainer + SideStore** — on your iPhone or iPad **with no computer**,
driven entirely by an **iOS Shortcut**.

> **Status: early development (Phase 0 complete).** Nothing is installable yet. This README will grow
> as the build progresses; untested parts are labelled *untested* until they are confirmed on a real device.

## Requirements

| Requirement | Detail |
|---|---|
| **iOS version** | **iOS 27 or newer.** This is a hard requirement. |
| Device | iPhone or iPad |
| Computer | **None required** |
| Apple account | A **free** Apple ID is enough (no paid Developer Program membership) |
| VPN app | **LocalDevVPN** (or StosVPN) — free, from the App Store |
| Runtime | **a-Shell** — free, from the App Store |
| Connection | Wi‑Fi, for the first install |

### Why iOS 27 or newer

On iOS 17.4 – 26.x, installing the first app entirely without a computer requires a "pairing file"
that normally has to be produced with a computer. **iOS 27 removes that step**, which is what makes a
zero-computer install possible at all. Older iOS versions are explicitly **out of scope**.
(iOS 17.4–26.x support is **unverified** and currently not planned.)

## What you will be able to install

Four variants, chosen inside the Shortcut:

1. **SideStore** (stable)
2. **SideStore** (nightly — may be unstable)
3. **LiveContainer + SideStore** (stable)
4. **LiveContainer + SideStore** (nightly — may be unstable)

The exact version you are about to get is shown to you before anything is downloaded.

## How it will work

1. You run the **BariSign Shortcut**. It asks a few readiness questions (LocalDevVPN installed and
   connected? Developer Mode on? Apple ID ready?) and which of the four variants you want.
2. The Shortcut runs the real work in **a-Shell** (a free terminal app): it resolves the correct
   official release at that moment, downloads the IPA, signs it with your free Apple ID, and prepares
   it for install.
3. You install it by **AirDrop** (the IPA is sent to your iPhone/iPad and iOS installs it).

## Privacy

- Your **Apple ID and password never go to any server we control, and never to an anisette server.**
  They are used only to talk to Apple, from your device.
- No analytics, no telemetry, no accounts on our side.
- Pairing files, certificates, passwords and keys are never stored in this repository.

## Safety notes

- **Nightly builds may be unstable.** They are pre-release builds straight from the projects' own
  nightly pipelines.
- BariSign downloads IPAs from the **official** SideStore and LiveContainer release pages and does not
  host or mirror them.

## Documentation

- `PHASE0_REPORT.md` — research, verified release sources, feasibility verdicts, architecture.
- `POC_GUIDE.md` — the on-device checks (PoC-A/B/C) used before the build starts.
- `AGENTS.md` — the project's constraints and working rules.
- Beginner step-by-step guide and the Shortcut build guide: **coming in Phase 1**.

## Licences — important

**BariSign is not MIT throughout, and it cannot be used commercially.**

- **BariSign's own code** is MIT (final `LICENSE` file added in Phase 1).
- **`third_party/sideinstaller/` is not.** That folder contains code copied from
  [SideInstaller by FrizzleM](https://github.com/FrizzleM/SideInstaller) and is governed by that
  project's own licence (`third_party/sideinstaller/LICENSE.md`). In short, it permits
  non-commercial use, modification, and **source-form-only** redistribution with attribution, and
  forbids commercial use and redistribution of SideInstaller's official binaries. **Because these
  files are part of this repository, the repository as a whole may not be used commercially.**
- We keep that code fenced in `third_party/sideinstaller/` on purpose, and we prefer reimplementing
  from MIT sources (`nab138/isideload`, `jkcoxson/idevice`) wherever they cover the same logic.

Full details and attribution: `THIRD_PARTY_NOTICES.md`.

SideStore and LiveContainer are **AGPL-3.0**; their released apps are consumed as-is and are neither
modified nor redistributed by this repository.
