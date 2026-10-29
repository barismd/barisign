# BariSign — step by step

Plain language, no jargon. Everything happens in **a-Shell** on the iPhone or iPad that will get the app.

**Read this first:** stages 1 and 2 work today; stage 3 is written but has never talked to Apple; stage 4
(the signer) is not built yet, so nothing can be installed yet. Each stage below says which it is.

---

## Before you start

- **iOS 27 or newer.** On older versions the first install needs a computer; BariSign does not cover that.
- Install **a-Shell** from the App Store (the full a-Shell, not a-Shell mini).
- Have your **Apple ID and password** ready. A free account is enough. Two-factor codes will be asked for.
- Keep the device **unlocked** during sign-in.

## Install BariSign

```
mkdir -p ~/Documents/barisign && cd ~/Documents/barisign
curl -sSL "https://github.com/barismd/barisign/blob/main/install.sh?raw=true" -o install.sh
sh install.sh
```

It downloads the files and checks that they run. Afterwards, two commands are all you need:

```
sh bin/barisign status     # what is already saved
sh bin/barisign            # guided run: one question at a time
```

The guided run does every stage below in order. You can also run one stage at a time — that is what the
rest of this guide describes.

---

## Step 1 — Your UDID (✅ works)

Your UDID is the device's hardware ID. A free Apple ID can only sign apps for devices registered on it, so
this has to be known. It is not a password and it is not tied to your Apple ID.

```
sh bin/barisign udid
```

The easiest way, in short:

1. Open **Safari** on this device: **https://median.co/udid**
2. Install the profile it offers (Settings → General → VPN & Device Management → Downloaded Profile).
3. Safari shows your UDID — tap **Copy UDID to Clipboard**.
4. Back in a-Shell, paste it at the prompt (or: `sh bin/barisign udid 00008110-001a2c3e0e91801e`).
5. Remove the profile again: Settings → General → VPN & Device Management → Remove Profile.

Prefer not to use a third party? `docs/UDID_OPTIONS.md` shows two alternatives, including **serving the
profile yourself** from this device (`sh bin/barisign udid --local`) — that is the route verified first on
this project.

Either way it ends with the UDID saved; `sh bin/barisign status` shows it.

---

## Step 2 — Sign in to Apple (✅ works)

```
sh bin/barisign login
```

- The password is used locally to build a one-time proof (SRP-6a). It is **not** sent anywhere and **not**
  saved.
- Anisette headers (Apple's device-identity check) come from a public anisette server, which never receives
  your Apple ID. If one server is down the script tries the next.
- If your account has two-factor authentication, a code is asked for; it is typed into a-Shell, not into a
  browser.
- Success prints `LOGIN: ok`.

## Step 3 — Developer portal (⚠️ written, not tested)

```
sh bin/barisign portal
```

This registers the UDID on your account, creates an App ID and a certificate, and downloads a provisioning
profile. First-run surprises to expect, with what they mean:

| What you see | What it means |
|---|---|
| *"no developer team"* | accept the free developer agreement once at `developer.apple.com` in Safari |
| error **7460** | the account is at its certificate limit (free accounts get two); the script asks before revoking one |
| *"already registered"* | that UDID is already on the team — harmless |

## Step 4 — Sign the IPA (🚧 to build)

The last big piece. It turns the official IPA into one your device will launch: a Mach-O signature over
every page, entitlements, `CodeResources`, and Apple's CMS signature. Until it exists, nothing this project
produces will install and launch.

## Step 5 — Install (✅ AirDrop verified; the round trip 🚧)

iOS installs an **AirDropped** IPA by itself, with no extra "Install" tap. It cannot AirDrop to itself, so
the finished app travels:

1. BariSign writes the signed app as a **`.zip`**.
2. Share that zip to **any second device** (phone, iPad or Mac) and unzip it there.
3. From there, **AirDrop the `.ipa` back** to this device.
4. Accept it — iOS installs it.
5. If it installs but will not open, the signature is not valid for this device.

---

## Free Apple ID limits (verified)

- **3 apps** installed at a time — SideStore counts as one.
- **10 App IDs** per account.
- Apps are signed for **7 days** and must be refreshed.

---

## Troubleshooting

| What you see | What it means |
|---|---|
| `PASS - safe to continue` | runtime, crypto, anisette and Apple's TLS all work |
| anisette: one server fails, the next works | normal; the working one is remembered |
| `That does not look like a UDID` | 40 hex characters, or 24 with a dash after the first 8 |
| profile says **empty** | expected: a `Profile Service` profile configures nothing |
| `--local` route: *trust switch is off* | Settings → General → About → Certificate Trust Settings → switch on **BariSign CA …** |
| `sh bin/update` cannot update | run `sh install.sh` in `~/Documents/barisign` again |
| nothing works at all | `sh bin/barisign check` prints what is broken, and nothing else |
