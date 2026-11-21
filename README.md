<p align="center"><img src="assets/baristore.svg" width="128" alt="BariStore"></p>

<h1 align="center">BariStore</h1>

An app store for iPhone and iPad that needs no computer. It runs in
[Open Minis](https://apps.apple.com/app/id6759188481), signs apps with your
own free Apple ID and installs them on the same device.

> **Status: 0.10 alpha.** On a device (iOS 27): pairing, Apple ID sign-in with
> two-factor codes, signing and installing work. Refresh and the newer options
> are still being tested.

## What you need

| | |
|---|---|
| Device | iPhone or iPad on **iOS / iPadOS 27 or newer** |
| Apps | **Open Minis 1.16+** and **LocalDevVPN** (both free on the App Store) |
| Apple ID | a **free** one is enough |
| Settings | **Developer Mode** on, **Wi-Fi** on, LocalDevVPN connected |

## Install

In Open Minis's terminal:

```sh
wget -qO- https://raw.githubusercontent.com/barismd/BariStore/main/install.sh | sh
```

After that, `baristore` opens the store over the terminal; press Return (or
Ctrl-C) in the terminal, or Options › Quit, to close it. If iOS stops it in the
background, it starts again by itself and the page reconnects. `baristore
update` (or Options › Update BariStore) installs the newest build; Options ›
Log gives a report to paste when something goes wrong (no password, token or
key in it).

## First run

The store walks you through it:

1. **Wi-Fi and LocalDevVPN**: BariStore checks that the loopback VPN answers
   (it does not work on mobile data) and moves on by itself.
2. **Pairing**: open Settings › Privacy & Security › Developer Mode (it
   appears there once pairing starts, even on an iPhone that never had it),
   tap **Pair with BariStore** and enter your passcode: the 6-digit code
   arrives as a notification, and you type it in Settings. BariStore keeps
   running meanwhile with inaudible speech; nothing covers the screen.
3. **Apple ID** sign-in, two-factor code included. BariStore never signs you out.

"Start again" on the setup page starts over from the first step.

## Using it

- **Featured**: search, Add IPA (any IPA from Files), Sources (add, remove,
  and add back the suggested ones), and BariStore's picks: AltStore Classic,
  SideStore, LiveContainer + SideStore and LiveContainer, stable and nightly,
  resolved live from each project's GitHub releases. When an app is offered by
  several sources you choose which one to install from.
- **My Apps**: what BariStore installed, days left, refresh one or all,
  delete, put the pairing file into SideStore / LiveContainer / AltStore /
  StikDebug; your App IDs, how many are left, and which are free to reuse. SideStore, AltStore and
  LiveContainer + SideStore also get the signing certificate
  (`ALTCertificate.p12`), so they can refresh themselves without asking Apple
  for a new one.
- **Options**: Apple ID, pairing-file export, install defaults (Increased
  Memory Limit, remove extensions), appearance, update, log, clear cache,
  reset, quit. Each install
  sheet also lets you reuse the App ID of an app that is no longer on the
  iPhone (and its extensions' ones).

JIT: install **StikDebug** from Featured (StikDebug source). BariStore gives it
the pairing file, and StikDebug mounts the developer disk image and enables JIT
for the apps that support it.

Opening the store needs no network. Installing and refreshing check LocalDevVPN,
the pairing and the Apple ID first, say what is missing, and let you retry.

Free Apple ID limits (Apple's): apps expire after **7 days** (refresh them),
**3** sideloaded apps at a time (BariStore itself does not count, it runs in
Minis), **10** new App IDs per 7 days (each app extension needs one; "Remove
extensions" saves them).

## Your Apple ID

- **The password never leaves the device.** Apple's sign-in is SRP: the password
  becomes a one-time proof on the iPhone. It is not stored.
- **The anisette server never sees your account.** It supplies Apple's
  device-identity headers from a random identifier, nothing else. Since
  October 2026 Apple refuses servers built on its Android library, so BariStore
  uses the macOS- and Windows-library ones, in siboot's order:
  `new.ani.sidestore.io`, `mac-ani.jkcoxson.com`, `ani.iloader.app`,
  `ani3.stikstore.app`, `ani.jkcoxson.com`, `ani2.stikstore.app`,
  `win.ani.sidestore.io`, then any new ones on SideStore's public list. Each
  is tried as the Mac it reports and as a current Mac until Apple accepts one;
  `baristore doctor` shows which one Apple takes from your network today.
- Two-factor codes: on your trusted devices, or by text message if none arrives.
- The session token, the pairing record and the signing key stay in
  `~/.baristore` inside Minis (mode 600). **No BariStore server exists**: no
  accounts, no analytics.

## How it works

One static Rust binary (Alpine aarch64 in Minis's iSH). Our own code for: the
pairable host (SRP-6a, TLV8, OPACK, Ed25519, ChaCha20-Poly1305) advertised
through `minis-bonjour`; pair-verify, the TLS 1.2 PSK tunnel and a userspace
IPv6/TCP stack to the iPhone over LocalDevVPN; RemoteXPC/RSD; AFC,
installation_proxy, house_arrest, AMFI; anisette v3 and GrandSlam sign-in; the
developer portal; Mach-O code signing; the store and its page.

Build: `./scripts/build.sh` (Rust + `pip install ziglang`), tests: `cargo test`.

## Licence

MIT, see [LICENSE](LICENSE). Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
