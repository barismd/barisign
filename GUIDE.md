# BariSign — guide

What works **today**, verified on real devices (iPhone 13 + iPad, iOS 27), and what is still being built.

| Stage | What it does | Status |
|---|---|---|
| 1 | Get the target device's **UDID** — no computer, no app on the target, no third party | ✅ works |
| 2 | Sign in to Apple with a **free Apple ID** (anisette + SRP-6a + 2FA) | ✅ works |
| 3 | Register the UDID, create App ID + certificate + provisioning profile | 🚧 to build |
| 4 | **Sign the IPA** (Mach-O + Apple CMS signature) | 🚧 to build — the last big piece |
| 5 | Install by **AirDrop** | ✅ works |

Stages 1, 2 and 5 can be done by hand today. Stages 3 and 4 are what automation still has to provide, so
**you cannot install SideStore with this yet.**

---

## Step 1 — Get the UDID (private, no third party)

A free Apple ID signs apps for a specific device, so the device's **UDID** must be known. iOS reports it
from a `Profile Service` profile, but only over HTTPS with a certificate iOS trusts. This script serves
exactly that from your own iPad over your own Wi-Fi.

```
cd ~/Documents/barisign
curl -sSL "https://raw.githubusercontent.com/barismd/barisign/main/poc/udid_share.py" -o udid_share.py
python3 -c "compile(open('udid_share.py').read(),'x','exec'); print('file OK')"
python3 -u udid_share.py
```

The script writes two files and opens a preview of each. **Do these in order — the order is the whole
trick:**

1. Share **`BariSign-CA.mobileconfig`** to the other device (the preview's top-right button → AirDrop) and
   install it.
2. On that device: **Settings → General → About → Certificate Trust Settings → switch ON full trust** for
   the row named **"BariSign CA XX:XX"** (the script prints the exact name).
3. Check it: open the `…/tls-check` address the script prints, in **Safari** on that device. A padlock and
   `TLS OK` means the trust is right.
4. Press **Return** in a-Shell, share **`BariSign-UDID.mobileconfig`**, and install it.
5. iOS will say the profile is **empty** — that is expected. The UDID has already been sent.
6. Back in a-Shell: the model and iOS build are shown; answer `y` and the **UDID** is printed.

The whole exchange stays on your Wi-Fi. Nothing is sent to any server, and there is no DNS, no account and
no certificate to manage.

### From the second time onwards — one profile

The CA only has to be installed and trusted **once per device**. After that:

```
python3 -u udid_share.py --udid-only
```

That shares **only** `BariSign-UDID.mobileconfig`. If it fails, run the plain command once to reinstall the
CA.

### Cleaning up

On the target: **Settings → General → VPN & Device Management** → remove every *BariSign* entry. The UDID
profile asks iOS to delete itself ten minutes after install (`DurationUntilRemoval`), but do not rely on
it.

---

## Step 2 — Sign in to Apple (works today)

In a-Shell, in `~/Documents/barisign`:

```
curl -sSL "https://raw.githubusercontent.com/barismd/barisign/main/poc/poc_login.py" -o poc_login.py
curl -sSL "https://raw.githubusercontent.com/barismd/barisign/main/third_party/sideinstaller/signin.py" -o signin.py
curl -sSL "https://raw.githubusercontent.com/barismd/barisign/main/third_party/apple-roots/apple-roots.pem" -o apple-roots.pem
curl -sSL "https://raw.githubusercontent.com/barismd/barisign/main/third_party/ca-bundle/cacert.pem" -o cacert.pem
python3 -u poc_login.py --selftest
python3 -u poc_login.py --login YOUR-APPLE-ID
```

It stops after sign-in: no device registered, nothing installed. The password is never transmitted
(SRP-6a) and never written to disk. With 2FA, Apple sends a code and the script prompts for it.

---

## Step 3 — Register the UDID and get a profile (to build)

Planned, and it will run entirely from the signing device:

1. ask Apple for your teams and certificates;
2. register the UDID from Step 1 (a free account holds a limited number of devices);
3. create an App ID for the chosen variant and download a provisioning profile listing that device;
4. create or reuse a development certificate tied to a key generated **on the device**.

**Manual alternative today:** open `developer.apple.com` in Safari → *Certificates, Identifiers & Profiles*
→ **Devices → +** → paste the UDID. The rest (App ID, certificate, profile) is doable by hand but fiddly.

---

## Step 4 — Sign the IPA (to build — the last big piece)

An IPA is a zip; the app inside must carry a valid Apple code signature: a CodeDirectory over every page,
entitlements, and a CMS signature with Apple's two custom attributes. This must be written from scratch in
pure Python and is the make-or-break item (`docs/PHASE0_REPORT.md`, §13). Until it exists, nothing this
project produces will launch.

---

## Step 5 — Install by AirDrop (works today)

Once an IPA is validly signed for the target's UDID:

1. On the signing device: `python3 -u udid_share.py --view /absolute/path/App.ipa` (a preview opens), or
   use the Files app.
2. Share → **AirDrop** → the other device.
3. On the target: accept. **iOS installs it by itself — there is no extra "Install" tap.**
4. Open the app. If it installs but will not open, the signature is not valid for that device.

---

## Caveats (all verified on device)

- **Safari only.** Chrome and in-app browsers save a profile as a download instead of handing it to
  Settings, which looks like nothing happening.
- **Security Delay.** On an **iPhone** running iOS 26.4+, installing *any* profile at a location iOS does
  not consider familiar triggers a **one-hour** delay (Face ID → 1 hour → Face ID). Do it at home, or use
  an **iPad**, which is unaffected.
- **Free Apple ID limits:** 3 apps installed at a time (SideStore counts as one), a limited number of App
  IDs, and signing expires every 7 days.
- **The UDID is not a credential** — it is an identifier developers paste into portals daily. No Apple ID,
  password or pairing record is involved in Step 1.

## Troubleshooting Step 1

| What you see | What it means |
|---|---|
| `RESULT: UDID received` + the model you expected | ✅ done |
| `no UDID arrived within 300 seconds` | the profile was not installed, or the trust switch is off — check the `…/tls-check` padlock |
| *"MDM certificate is invalid"* on the target | the CA's trust switch is OFF, or an **older** BariSign CA is installed — delete it in VPN & Device Management and install the current one |
| the profile screen never appears | you are not in Safari, or the preview was closed — use `python3 -u udid_share.py --view BariSign-UDID.mobileconfig` |
| iOS says the profile is **empty** | expected — the UDID was already sent |
| `*** … is not a private address` | both devices must be on your own Wi-Fi |

## What comes next

Stage 3 (developer portal) and Stage 4 (the signer), then a Shortcut that chains everything. At that point
this guide collapses into: *tap the Shortcut, scan a code, accept an AirDrop.*
