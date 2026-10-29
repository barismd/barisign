# BariSign

Install **SideStore** — or **LiveContainer + SideStore**, or **AltStore** — on an iPhone or iPad **with no
computer**, driven entirely by an **iOS Shortcut**.

> **Status: the hard parts are proven; the signer is not built yet, so you cannot install SideStore with
> this today.** Below, each capability is marked **verified on device** or **to build**. Nothing here is
> claimed that has not been tested on a real device.

---

## What already works — verified on real devices (iPhone 13 + iPad, iOS 27)

Everything happens **on the device the app will be installed on**. No second phone is needed to sign, and
no computer is needed at any point.

| Stage | State | How |
|---|---|---|
| **Read the UDID** | ✅ **verified** (both routes) | one-tap profile from `median.co/udid`, then paste it; `udid --local` serves our own profile over the LAN |
| **Sign in to Apple with a free Apple ID** (anisette + SRP-6a + 2FA) | ✅ **verified** (`LOGIN: PASS`) | on the device, in a-Shell (`poc/poc_login.py`) |
| **Install a signed IPA** | ✅ **verified** | AirDrop: iOS installs it by itself, no extra tap |
| Register the UDID, create the App ID, certificate and provisioning profile | ⚠️ **written, untested** | `poc/poc_portal.py` |
| **Sign the IPA** (Mach-O + Apple CMS signature) and package it as a `.zip` | 🚧 **to build** | the last big piece |
| Pairing file for SideStore / AltStore, on-device | 🔎 **researched, not built** | see `docs/PLAN_V2.md` |

Until the signer exists, BariSign cannot produce something your device will launch. Everything above it
is done.

**Getting it** — no git needed, just `curl`:

```
mkdir -p ~/Documents/barisign && cd ~/Documents/barisign
curl -sSL "https://github.com/barismd/barisign/blob/main/install.sh?raw=true" -o install.sh
sh install.sh
```

Run `sh install.sh` again (or `sh bin/update`) any time to update.

Then one command drives every stage, passing each stage's result to the next:

```
sh bin/barisign          # guided: check -> pick a variant -> UDID -> Apple -> portal
sh bin/barisign status   # what is already known
```

---

## Requirements

| | |
|---|---|
| **iOS version** | **iOS 27 or newer** (hard requirement) |
| Apple account | a **free** Apple ID is enough — no paid Developer Program membership |
| The device that gets the app | **a-Shell** (free, App Store) — this is where every step runs |
| A second device | only for the **AirDrop round trip** (any iPhone/iPad/Mac). iOS cannot AirDrop to itself, so the finished `.zip` goes out and comes back |
| Network | internet; the same Wi-Fi as the second device for the AirDrop step |
| Computer | **none** |
| LocalDevVPN | not needed by BariSign's own steps; SideStore uses it afterwards to refresh itself |

SideStore and LiveContainer need iOS 27 to work without a computer at all — on iOS 17.4–26.x the first
install requires a pairing file made by a computer.

---

## How the UDID step works — and why it is private

A free Apple ID signs apps for **specific devices**, so the device's **UDID** has to be known. BariSign
takes it from the profile **median.co** serves: you open `https://median.co/udid` **on the device that
will get the app**, tap install, finish the install in Settings, and Safari shows the UDID with a copy
button. Paste it into a-Shell and stage 1 is done.

- **Nothing is sent to us.** There is no BariSign server. The profile only reports a hardware ID.
- **The Apple ID is not involved at this stage** — it is never given to median.co or to anyone else.
- The profile can be **removed right afterwards**: Settings → General → VPN & Device Management.
- If median.co is unreachable, `sh bin/barisign udid --local` still works: it serves an equivalent
  profile from the device itself over the LAN with a certificate authority of our own (that route needs
  one trust switch in Settings, and is the one we verified first).

Step-by-step instructions are in **`GUIDE.md`**.

---

## Which variant you get

| # | Variant | Source |
|---|---|---|
| 1 | **SideStore** (stable) | `SideStore/SideStore` |
| 2 | **SideStore** (nightly — *may be unstable*) | `SideStore/SideStore`, tag `nightly` |
| 3 | **LiveContainer + SideStore** (stable) | `LiveContainer/LiveContainer` |
| 4 | **LiveContainer + SideStore** (nightly) | `LiveContainer/LiveContainer`, tag `nightly` |
| 5 | **AltStore Classic** (stable) | `altstoreio/AltStore` |

Every version is resolved **at runtime** from the project's official release page — never pinned here —
and you are shown which version you are about to get. `variants.json` is the machine-readable list.

---

## Your Apple ID and your data

- **Your password is never transmitted.** Apple's sign-in is SRP-6a: the password becomes a one-time
  proof on the device and Apple receives only that. It is never written to disk.
- **The anisette server never sees your account.** It is asked only for device-identity headers, and it
  receives a random identifier we generate — never your Apple ID, password or token.
- **Your UDID never leaves your own network.**
- **No analytics, no telemetry, no accounts on our side.**
- TLS verification is never disabled anywhere in this project.

---

## Documentation

| File | What it is |
|---|---|
| **`GUIDE.md`** | the step-by-step guide for what works today |
| `docs/PHASE0_REPORT.md` | all research, device results, dead ends and why, with dates |
| `variants.json` | the variant registry and the runtime resolution rules |
| `bin/barisign` | the single entry point for every stage |
| `poc/` | the scripts behind them: `udid_share.py`, `poc_login.py`, `poc_portal.py` |
| `THIRD_PARTY_NOTICES.md` | licences and attribution |
| `AGENTS.md` | the working rules for this project |

## Licences — please read

**BariSign contains non-commercial-licensed parts** because it reuses SideInstaller's sign-in code.
BariSign's own code is MIT; `third_party/sideinstaller/` is under that project's own non-commercial licence.
**The repository as a whole therefore may not be used commercially.** Details in
`THIRD_PARTY_NOTICES.md`.
