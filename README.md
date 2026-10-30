# BariSign

Install **SideStore** — or **LiveContainer + SideStore**, or **AltStore Classic** — on an iPhone or iPad
**with no computer**, using your own free Apple ID. Two devices, two roles:

| Role | What it is | What it runs |
|---|---|---|
| **Signer** | the device that signs and installs | **a-Shell** + BariSign |
| **Target** | the device that gets the app | Safari + Settings only |

Both devices may be an **iPhone or an iPad**; iOS and iPadOS both work. The role is chosen at run time.
They must be on the **same Wi-Fi**. The target never installs a-Shell or BariSign.

> **Status:** the signer path is verified on a real device (iPhone 13, iOS 27): Apple sign-in, the
> developer portal, signing, and installing the IPA over USB with `ideviceinstaller` (AltStore then
> launches). The new **target UDID page** and the **signer → target pairing install** are written but
> not yet verified on a device.

---

## How the flow works

1. On the **signer**, install a-Shell and BariSign, then run `sh bin/barisign`.
2. At the UDID step the signer serves a short-lived page on its Wi-Fi address:
   `http://<signer-ip>:<port>/`.
3. On the **target**, open that address in Safari. The page links to `median.co/udid`; install the
   profile, copy the UDID, paste it into the page and submit.
4. The signer receives the UDID and continues: Apple sign-in, developer portal (device + App ID +
   certificate + profile), download + sign the chosen IPA.
5. Install from the signer: `sh bin/pair`. The signer advertises as a pairing host; on the target open
   **Settings → Privacy & Security → Developer Mode → Paired Macs**, pick **BariSign**, enter the
   code. The signer installs over Wi-Fi with `installation_proxy`.
6. On the target, trust the developer and open the app.

**AirDrop and OTA are not used.** iOS 27 refuses a free-profile app from those sources
(`MIInstallerErrorDomain` 111); the pairing tunnel is the developer source that works.

---

## What works

| | |
|---|---|
| **Apple sign-in** (free ID, 2FA) | ✅ verified |
| **Device, certificate, profile** (developer portal) | ✅ verified |
| **Download + sign + verify** | ✅ verified — the signed IPA installs over USB |
| **USB install** (`ideviceinstaller`) | ✅ verified — Trust Developer, AltStore launches |
| **UDID page** served by the signer | ⚠️ written, untested on a device |
| **Signer → target pairing install** (`sh bin/pair`) | ⚠️ written, untested on a device |
| **AirDrop / OTA install** | ❌ refused by iOS for free profiles |

---

## Get it — on the signer only

In a-Shell on the **signer**:

```
mkdir -p ~/Documents/barisign && cd ~/Documents/barisign
curl -sSL "https://github.com/barismd/barisign/blob/main/install.sh?raw=true" -o install.sh
sh install.sh
```

Run `sh install.sh` again (or `sh bin/update`) any time to update. Your saved state in `state/` is kept.

```
sh bin/barisign          # guided: check -> variant -> UDID page -> Apple -> portal -> download -> sign
sh bin/pair              # pair the target and install over Wi-Fi
sh bin/barisign status   # what is already known
sh bin/barisign --help   # every command
```

---

## Requirements

| | |
|---|---|
| **Both devices** | iOS/iPadOS **27 or newer** |
| Apple account | a **free** Apple ID — no paid Developer Program membership |
| **Signer** | **a-Shell** (free, App Store) — BariSign runs here |
| **Target** | no extra app; Safari and Settings only |
| Computer | **none** |
| Network | both devices on the same **Wi-Fi**; internet for Apple and the IPA download |
| On the target | LocalDevVPN for SideStore to refresh apps after install |

---

## The UDID, in one paragraph

A free Apple ID signs apps for **specific devices**, so the **target's UDID** must be known. At the UDID
step the signer serves a page on its Wi-Fi address. On the target, open it in Safari, install the
`median.co/udid` profile, copy the UDID, and paste it into the page's box. The signer receives it and
continues; no manual terminal paste is needed.

`sh bin/barisign udid --manual` keeps a paste-on-the-signer fallback for testing.
`docs/UDID_OPTIONS.md` lists other UDID sites (not affiliated with us) and self-hosting.

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

- **Your password is never transmitted.** Apple's sign-in is SRP-6a: the password becomes a one-time proof
  on the signer. It is never written to disk.
- **The anisette server never sees your account.** It is asked only for device-identity headers, with a
  random identifier we generate — never your Apple ID, password or token.
- **No BariSign server exists.** The UDID page is served only from the signer on your own Wi-Fi for the
  duration of that step. Nothing is collected: no analytics, no telemetry, no accounts.
- TLS verification is never disabled anywhere in this project.

---

## Documentation

| File | What it is |
|---|---|
| **`GUIDE.md`** | step-by-step, in plain language |
| `docs/PAIRING_INSTALL.md` | how the signer installs on the target without AirDrop/OTA |
| `docs/UDID_OPTIONS.md` | UDID sources, including the signer page |
| `docs/ANISETTE.md` | anisette servers: what they see, which ones we use, how to pin or self-host |
| `docs/PLAN_V2.md` | the current architecture and why |
| `docs/FORK_PLAN.md` | the pairing module inside the forked apps: licences, CI, risks |
| `docs/RESULTS.md` | what is verified on device, and the device quirks we had to work around |
| `variants.json` | the variant registry and runtime resolution rules |
| `bin/barisign` | the single entry point on the signer |
| `bin/pair` | the signer → target pairing install |
| `THIRD_PARTY_NOTICES.md` | licences and attribution |

## Licences — please read

BariSign's own code is **MIT**. `third_party/sideinstaller/` is under that project's own
**non-commercial** licence, so **the repository as a whole may not be used commercially**. The forks
planned in `docs/FORK_PLAN.md` are **AGPL-3.0** (SideStore, AltStore and LiveContainer are all AGPL-3.0).
`bin/pair` installs `pymobiledevice3` (GPL-3.0) separately on the signer; it is not vendored.
Details in `THIRD_PARTY_NOTICES.md`.
