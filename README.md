# BariSign

Install **SideStore** — or **LiveContainer + SideStore**, or **AltStore Classic** — on an iPhone or iPad
**with no computer**, using your own free Apple ID. Everything runs **on the device that will get the app**,
in [a-Shell](https://holzschu.github.io/a-Shell_iOS/).

> **Status:** the UDID, the Apple sign-in and the AirDrop install are verified on a real device
> (iPhone 13, iOS 27). The **signer** is written and checked on Linux against real IPAs, but its first
> install on a device has not happened yet.

---

## What works

| | |
|---|---|
| **UDID** | ✅ verified — one-tap profile, paste the value |
| **Apple sign-in** (free ID, 2FA) | ✅ verified |
| **Installing a signed IPA** | ✅ verified — AirDrop, iOS installs it itself |
| **Device, certificate, profile** | ⚠️ written, untested against Apple |
| **Download + sign + zip** | ⚠️ written, untested on device |

## Get it — no git needed, just `curl`

In a-Shell:

```
mkdir -p ~/Documents/barisign && cd ~/Documents/barisign
curl -sSL "https://github.com/barismd/barisign/blob/main/install.sh?raw=true" -o install.sh
sh install.sh
```

Run `sh install.sh` again (or `sh bin/update`) any time to update. Your saved state in `state/` is kept.

Then one command drives everything and remembers each result:

```
sh bin/barisign          # guided: check -> variant -> UDID -> Apple -> portal -> download -> sign -> zip
sh bin/barisign status   # what is already known
sh bin/barisign --help   # every command
```

---

## Requirements

| | |
|---|---|
| **iOS** | **27 or newer** (hard requirement) |
| Apple account | a **free** Apple ID — no paid Developer Program membership |
| The device that gets the app | **a-Shell** (free, App Store) — every step runs here |
| A second device | only for the **AirDrop round trip** (any iPhone, iPad or Mac): iOS cannot AirDrop to itself, so the finished `.zip` goes out and comes back |
| Computer | **none** |
| Network | internet |

---

## The UDID, in one paragraph

A free Apple ID signs apps for **specific devices**, so your **UDID** must be known. Take it with a
one-tap profile, paste it into a-Shell, done:

```
sh bin/barisign udid
```

It prints the steps, waits for **Return**, then **opens Safari** at `median.co/udid` for you.
`docs/UDID_OPTIONS.md` lists **three** ways to get it — median.co, other third-party sites (not affiliated
with us), or **your own HTTPS server** — with the caveats of each. We run no server and receive nothing.

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
  on the device. It is never written to disk.
- **The anisette server never sees your account.** It is asked only for device-identity headers, with a
  random identifier we generate — never your Apple ID, password or token.
- **No BariSign server exists.** Nothing is collected: no analytics, no telemetry, no accounts.
- TLS verification is never disabled anywhere in this project.

---

## Documentation

| File | What it is |
|---|---|
| **`GUIDE.md`** | step-by-step, in plain language |
| `docs/UDID_OPTIONS.md` | the three ways to read a UDID, and how to serve one yourself |
| `docs/ANISETTE.md` | anisette servers: what they see, which ones we use, how to pin or self-host |
| `docs/PLAN_V2.md` | the current architecture and why |
| `docs/FORK_PLAN.md` | the pairing module inside the forked apps: licences, CI, risks |
| `docs/RESULTS.md` | what is verified on device, and the device quirks we had to work around |
| `variants.json` | the variant registry and runtime resolution rules |
| `bin/barisign` | the single entry point for every stage |
| `THIRD_PARTY_NOTICES.md` | licences and attribution |

## Licences — please read

BariSign's own code is **MIT**. `third_party/sideinstaller/` is under that project's own
**non-commercial** licence, so **the repository as a whole may not be used commercially**. The forks
planned in `docs/FORK_PLAN.md` are **AGPL-3.0** (SideStore, AltStore and LiveContainer are all AGPL-3.0).
Details in `THIRD_PARTY_NOTICES.md`.
