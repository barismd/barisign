# BariSign

Install **SideStore** — or **LiveContainer + SideStore** — on an iPhone or iPad **with no computer**,
driven by an **iOS Shortcut**.

> **Status: early development.** Nothing is installable yet. The on-device tests are passing one by
> one and this README describes the target experience. Anything not yet confirmed on a real device is
> labelled *untested*.

---

## What you get

Four variants, chosen in the Shortcut:

| # | Variant | Notes |
|---|---|---|
| 1 | **SideStore** | stable release |
| 2 | **SideStore nightly** | *may be unstable* |
| 3 | **LiveContainer + SideStore** | stable release |
| 4 | **LiveContainer + SideStore nightly** | *may be unstable* |

You are shown **exactly which version** you are about to get, before anything is downloaded.

---

## Requirements

| | |
|---|---|
| **iOS version** | **iOS 27 or newer** (hard requirement — see below) |
| Apple account | A **free** Apple ID is enough. Your main one is fine, and safe (see below). |
| Two free apps | **a-Shell** (the terminal that does the work) and **LocalDevVPN** (or StosVPN) |
| Connection | Wi-Fi |
| Computer | **none** |

### Why iOS 27 or newer

On iOS 17.4 – 26.x, installing the first app without a computer needs a "pairing file" that normally
has to be produced by a computer. **iOS 27 removes that requirement**, which is what makes a
zero-computer install possible at all. Older versions are out of scope.

---

## How it works — two devices, one of them does the work

The Shortcut runs on the device **you build on**, and the finished app is **AirDropped** to the device
that will use it. Nothing is uploaded anywhere.

```
   DEVICE A — the "signer"                  DEVICE B — the "receiver"
   (iPad, or an iPhone)                     (your iPhone, no computer)
   ┌──────────────────────────┐             ┌──────────────────────────┐
   │ 1. You run the Shortcut  │             │                          │
   │ 2. It checks readiness   │             │                          │
   │ 3. It asks which variant │             │                          │
   │ 4. a-Shell downloads,    │             │                          │
   │    signs the app         │             │                          │
   │ 5. AirDrop  ─────────────┼────────────►│ 6. Tap "Install"         │
   │                          │             │ 7. SideStore appears 🎉  │
   └──────────────────────────┘             └──────────────────────────┘
```

**Step by step**

1. On **Device A**: run the **BariSign Shortcut**.
2. Answer the readiness questions (LocalDevVPN installed and connected? Developer Mode on? Apple ID
   ready?).
3. Pick one of the four variants.
4. BariSign downloads the official build, signs it with your Apple ID, and shows you the version.
5. Choose **AirDrop** and send it to Device B.
6. On **Device B**: accept the file and tap **Install**.
7. SideStore is on Device B. Open it once, done.

**One device only?** That works too: the same device can sign the app and install it from the Files
app or by AirDropping it to itself. The two-device split is just the easy way to do it.

---

## Is my Apple ID safe? — yes, your main account is fine

BariSign is built so that **your password never leaves your device, and never reaches any server we
control**. This is not a promise, it is how the protocol works:

- **Your password is never transmitted.** Apple's sign-in uses **SRP-6a**: your password is turned
  into a one-time mathematical *proof* on the device. Apple receives the proof and verifies it, but
  the password itself is never sent, not even to Apple. Neither we nor anyone else can see it.
- **The anisette server never sees your account.** A public anisette server is asked only for
  device-identity headers. It receives a **random 16-byte identifier** we generate, and Apple's
  provisioning blobs — never your Apple ID, never your password, never your token.
- **Nothing sensitive is written to disk.** The password exists only in memory for the few seconds of
  sign-in. Signing keys and certificates stay on your device.
- **No third party signs for you.** There is no "signing service", no shared certificate, no upload.
  Your own free Apple ID issues the certificate.
- **TLS verification is never disabled.** BariSign ships the public CA certificates it needs and
  verifies every connection.
- **No analytics, no telemetry, no accounts on our side.**

**Hosts contacted, complete list:** `gsa.apple.com` and `developerservices2.apple.com` (Apple, for
sign-in and the developer portal), your chosen anisette server (identity headers only),
`servers.sidestore.io` (the public anisette server list) and `github.com` / `api.github.com` (to fetch
the official build). Nothing else.

Every authenticated request is additionally checked to make sure it can only go to an `apple.com`
host, so even a tampered reply cannot redirect your sign-in proof or a verification code elsewhere.

---

## Two-factor authentication

**Supported.** If your account has 2FA (most do), Apple will ask for a verification code:

- a code is pushed to your other Apple devices (or shown as a prompt), or
- you can receive it by SMS.

The Shortcut will ask you to type the code, and then continues. Your main Apple ID with 2FA on is
expected to work — this is the recommended setup.

*(This path is implemented and has been reviewed, but it has not yet been exercised on a real device
by BariSign — reported as **untested** until someone runs it.)*

---

## Free Apple ID limits — documented, not guessed

Taken from SideStore's own FAQ (<https://docs.sidestore.io/docs/faq>):

- A **free Apple Account can only have 3 apps installed at a time, including SideStore itself.**
- There is also a **10 App ID limit** (a free account can create a limited number of App IDs).
- Free-account signing **expires every 7 days** and must be refreshed (SideStore does this for you in
  the background). A paid Apple Developer account ($99/year) removes the 3-app limit and extends the
  expiry to a year.

Practical meaning: with a free account, **SideStore counts as one of your three slots.** Variant 3/4
(LiveContainer + SideStore) is one app; apps you run inside LiveContainer do not each take a slot,
which is the point of LiveContainer.

---

## Important: which devices can run the app

A free Apple ID signs apps for **specific devices**. Every device that will *run* SideStore has to be
registered once with your Apple ID, which needs that device's UDID.

- The device that runs BariSign can be registered automatically.
- Registering a **second** device (Device B, the AirDrop receiver) is the one step still being worked
  out for a computer-free setup, and is marked **unverified** on iOS 27.

This is called out plainly rather than hidden: the two-device AirDrop flow above is the goal, and this
registration step is the remaining piece. If you only care about one device, there is no issue at all.

---

## Safety notes

- **Nightly builds may be unstable.** They come straight from the projects' own nightly pipelines.
- BariSign downloads builds from the **official** SideStore and LiveContainer release pages. It never
  hosts, mirrors or modifies them.

---

## Licences — please read

**BariSign contains non-commercial-licensed parts, because it reuses SideInstaller's sign-in code.**

- BariSign's own code is MIT.
- `third_party/sideinstaller/` is code from
  [SideInstaller by FrizzleM](https://github.com/FrizzleM/SideInstaller), under its own licence
  (non-commercial, source-form redistribution, attribution required). **Because of this the repository
  as a whole may not be used commercially.**
- SideStore and LiveContainer are **AGPL-3.0**; their released apps are used as-is.

Full details: `THIRD_PARTY_NOTICES.md`.

---

## Documentation

| File | What it is |
|---|---|
| `PHASE0_REPORT.md` | research, verified release sources, device test results, fixes |
| `POC_GUIDE.md` | the on-device checks (PoC-A/B/C) |
| `THIRD_PARTY_NOTICES.md` | attribution and licences |
| `AGENTS.md` | the working rules for this project |
| Beginner guide + Shortcut build guide | coming in Phase 1 |
