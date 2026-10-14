# BariSign

Install **SideStore** — or **LiveContainer + SideStore**, or **AltStore** — on an iPhone or iPad **with no
computer**, driven entirely by an **iOS Shortcut**.

> **Status: the hard parts are proven; the signer is not built yet, so you cannot install SideStore with
> this today.** Below, each capability is marked **verified on device** or **to build**. Nothing here is
> claimed that has not been tested on a real device.

---

## What already works — verified on real devices (iPhone 13 + iPad, iOS 27)

| Stage | State | How |
|---|---|---|
| **Get the target device's UDID** — no computer, no app on the target, no third party | ✅ **verified** | a temporary configuration profile over the local network (`poc/udid_share.py`) |
| **Sign in to Apple with a free Apple ID** (anisette + SRP-6a + 2FA) | ✅ **verified** (`LOGIN: PASS`) | on the device, in a-Shell (`poc/poc_login.py`) |
| **Install a signed IPA** | ✅ **verified** | AirDrop: iOS installs it by itself, no extra tap |
| Register the UDID, create the App ID, certificate and provisioning profile | ⚠️ **written, untested** | `poc/poc_portal.py` |
| **Sign the IPA** (Mach-O + Apple CMS signature) | 🚧 **to build** | the last big piece |

Until the last row exists, BariSign cannot produce something your device will launch. Everything above it
is done.

**Getting it** (a-Shell ships `git`):

```
git clone https://github.com/barismd/barisign.git ~/Documents/barisign
cd ~/Documents/barisign
sh bin/update          # later: fetch the newest version and check it compiles
```

Four wrappers do the work: `sh bin/udid`, `sh bin/login`, `sh bin/portal`, `sh bin/update`.

---

## Requirements

| | |
|---|---|
| **iOS version** | **iOS 27 or newer** (hard requirement) |
| Apple account | a **free** Apple ID is enough — no paid Developer Program membership |
| On the device that signs | **a-Shell** (free, App Store) |
| On the device that receives | **nothing.** Safari and Settings only |
| Network | both devices on the **same Wi-Fi** for the UDID step |
| Computer | **none** |
| LocalDevVPN | not needed by BariSign's own steps; SideStore uses it afterwards to refresh itself |

SideStore and LiveContainer need iOS 27 to work without a computer at all — on iOS 17.4–26.x the first
install requires a pairing file made by a computer.

---

## How the UDID step works — and why it is private

A free Apple ID signs apps for **specific devices**, so the device's **UDID** has to be known. Apple's
over-the-air "Profile Service" mechanism reports it, but only over **HTTPS with a certificate iOS
trusts**. The internet "get my UDID" sites achieve that with a public certificate — which means *their*
server sees your UDID.

BariSign does it without any third party:

```
iPad (a-Shell)                                  iPhone (nothing installed)
 serves HTTPS with a throwaway CA ─────────────► Safari opens the profile, taps Install
 iOS posts the UDID over the local Wi-Fi ◄──────┘  (the request never leaves your network)
 prints the UDID after you confirm the model
```

- **The CA is generated on the iPad and is persistent**: install it and switch its trust ON **once**, and
  every later fetch is **a single profile**.
- **Nothing leaves your Wi-Fi.** No Cloudflare, no tunnel, no DNS, no account anywhere.
- iOS will call the profile **"empty"** — that is expected: a `Profile Service` profile configures nothing,
  and the UDID has already been sent when you see that message.

Step-by-step instructions, including the one-time trust switch, are in **`GUIDE.md`**.

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
| `bin/` | the four one-word commands: `update`, `udid`, `login`, `portal` |
| `poc/` | the scripts behind them: `udid_share.py`, `poc_login.py`, `poc_portal.py` |
| `THIRD_PARTY_NOTICES.md` | licences and attribution |
| `AGENTS.md` | the working rules for this project |

## Licences — please read

**BariSign contains non-commercial-licensed parts** because it reuses SideInstaller's sign-in code.
BariSign's own code is MIT; `third_party/sideinstaller/` is under that project's own non-commercial licence.
**The repository as a whole therefore may not be used commercially.** Details in
`THIRD_PARTY_NOTICES.md`.
