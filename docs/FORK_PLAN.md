# Fork plan — pairing module inside SideStore, LiveContainer+SideStore, AltStore Classic

**Decision (2026-10-29, user): fork the three apps** instead of shipping a helper app. This file records
what that means, so nobody re-discovers it later.

## What the module has to do

On a fresh install the user must be able to produce `ALTPairingFile.mobiledevicepairing` **without a
computer**, then let the app use it:

1. First screen offers **"Set up pairing"**.
2. It starts the **iOS 27 pairing flow** — iOS shows `Settings → Privacy & Security → Developer Mode →
   Pair with <app>` and a six-digit code; the app displays or accepts that code.
3. The resulting record is written to `Documents/ALTPairingFile.mobiledevicepairing` and offered in a
   share sheet (so StikDebug and friends can use the same file).
4. The app's normal pairing-file loader picks it up without the user hunting through Files.

SideInstaller already proves the flow works on-device on iOS 27; that is the reference behaviour, not the
source we copy (see licences).

## Licence rules — non-negotiable

| Part | Licence | Rule |
|---|---|---|
| Forked SideStore / AltStore / LiveContainer | **AGPL-3.0** (verified) | our patch to them is published under AGPL-3.0, source in this repo, notices kept |
| `third_party/sideinstaller/` (Python, non-commercial "Other") | non-commercial | **never** linked into or copied into the forked apps: a non-commercial term cannot be added to AGPL, so the two cannot be combined |
| Pairing code in the fork | ours | **clean-room reimplementation** of the iOS 27 pairing call, written from Apple's public framework surface (`RemotePairing`/`RPPairing`) and from MIT sources (`jkcoxson/idevice`, `nab138/isideload`), with the sources named in `THIRD_PARTY_NOTICES.md` |

Consequence: the repo keeps MIT for the Python tool, holds AGPL forks in `forks/<app>/`, and keeps the
non-commercial SideInstaller derivation isolated. Nothing from one bucket is copied into another.

## Build and release

- Each upstream already publishes a **nightly** release built by its own CI, so a fork inherits a working
  build recipe: `.github/workflows` on GitHub-hosted macOS runners, free for public repos.
- Our patch is kept as a **small, rebased commit series** on top of the upstream tag — never a merge.
- `bin/update` style tooling must resolve, for each variant:
  - the upstream tag we forked (`<app>-base.json`),
  - our release asset (our own repo's release, same variant names as `variants.json`),
  - and refuse to hand out a build whose base tag is older than the upstream release it replaces.
- `variants.json` is the single place that maps variant → repo → asset pattern; forked builds simply point
  `repo` at our fork and keep the same variant ids.

## Risks, in order of damage

1. **Entitlements.** The pairing API may need entitlements a free-cert profile cannot grant. SideInstaller
   works with free certificates, so it is very likely fine — **must be verified on device before any UI
   work**, because if it fails the whole fork is pointless.
2. **Three codebases.** AltStore Classic (UIKit + AltStoreCore), SideStore (its fork of the same), and
   LiveContainer (container app) have different entry points; the module must be written once and adapted
   three times — small patches, but three of them.
3. **Upstream churn.** Every release can move the setup screens. Rebase cost per release is minutes when
   the patch is small; hours when it touches the setup flow.
4. **Build environment drift.** Xcode/Swift versions of the upstream CI are not ours to control; pin them.

## Order of work

1. **Signer** — nothing can be installed, forked or not, until an IPA can be signed on-device.
2. **Proof of the pairing call** — a throwaway app built in CI that only starts pairing and prints the
   result. This answers risk 1 before any UI is written.
3. **Module + three patches**, one app at a time: SideStore → AltStore Classic → LiveContainer+SideStore.
4. **Release pipeline** and `variants.json` wiring.
5. Turkish, last.
