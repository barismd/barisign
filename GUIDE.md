# BariSign — guide

What works **today**, verified on real devices (iPhone 13 + iPad, iOS 27), and what is still being built.

| Stage | What it does | Status |
|---|---|---|
| 1 | Get the target device's **UDID** — no computer, no app on the target, no third party | ✅ works |
| 2 | Sign in to Apple with a **free Apple ID** (anisette + SRP-6a + 2FA) | ✅ works |
| 3 | Register the UDID; create App ID + certificate + provisioning profile | ⚠️ **written, untested** (`bin/portal`) |
| 4 | **Sign the IPA** (Mach-O + Apple CMS signature) | 🚧 to build — the last big piece |
| 5 | Install by **AirDrop** | ✅ works |

---

## Get it (no git needed)

In a-Shell: `mkdir` and `curl` only.

```
mkdir -p ~/Documents/barisign && cd ~/Documents/barisign
curl -sSL "https://github.com/barismd/barisign/blob/main/install.sh?raw=true" -o install.sh
sh install.sh
```

The installer downloads every file it needs and checks that they compile. Run it again any time to
**update** — it overwrites the program files and leaves your certificate authority
(`poc/barisign-ca.*`) alone, so already-trusted devices stay trusted. `sh bin/update` does the same.

> `git` also works if your copy is a git checkout (`git pull`), but it is not required.

### One command for everything

`bin/barisign` drives every stage, and the stages **share what they learn** (the UDID, your Apple ID, the
team, the certificate, the profile) through a small state file, so nothing is ever retyped:

```
sh bin/barisign            # guided run: check -> variant -> UDID -> Apple -> portal
sh bin/barisign check      # runtime + crypto + anisette + Apple TLS, nothing else
sh bin/barisign udid       # stage 1 — read another device's UDID
sh bin/barisign login      # stage 2 — sign in to Apple
sh bin/barisign portal     # stage 3 — register the UDID + certificate + profile
sh bin/barisign status     # what is already known
sh bin/barisign forget     # clear the saved state (the local CA is kept)
sh bin/update              # git pull + compile check
```

The state lives in `state/barisign.json` next to the scripts, and never holds a password.

The individual scripts are still there (`poc/udid_share.py`, `poc/poc_login.py`, `poc/poc_portal.py`) and
`bin/udid`, `bin/login`, `bin/portal` run them directly if you prefer.

> **Shortcuts for your fingers (optional):** a-Shell runs `~/.profile` in every new window, so aliases
> can live there. First check it is writable: `touch ~/.profile && echo writable`. If it says writable,
> add lines such as `alias update='cd ~/Documents/barisign && sh bin/update'` and then just type
> `update`. If it is read-only (likely — only `~/Documents` is writable in a-Shell), `cd
> ~/Documents/barisign` once per session and use the `sh bin/...` forms.

---

## Step 1 — Get the UDID (private, no third party) — **two short commands**

A free Apple ID signs apps for a specific device, so the device's **UDID** must be known. iOS reports it
from a `Profile Service` profile, but only over HTTPS with a certificate iOS trusts. The script serves
exactly that from your own iPad over your own Wi-Fi.

The certificate authority is a **one-time** step, so it is a command of its own. There is **nothing to
press and no timeout** — each command finishes by itself.

### 1a — the certificate authority (once per device)

```
cd ~/Documents/barisign
sh bin/barisign udid --ca
```

It writes `BariSign-CA.mobileconfig` and tries to open a preview; share it to the other device with the
preview's **top-right button → AirDrop** and install it. *(If no preview opens, run
`view BariSign-CA.mobileconfig` yourself.)*

Then, **on that device**: **Settings → General → About → Certificate Trust Settings → switch ON full
trust** for the row named **"BariSign CA XX:XX"** (the script prints the exact name).

### 1b — the UDID

```
sh bin/barisign udid
```

Same again with `BariSign-UDID.mobileconfig`: share it, install it. iOS will say the profile is
**empty** — expected; the UDID has already been sent at that point. a-Shell then shows the model, you
answer `y`, and the **UDID is printed and remembered** for the portal stage.

You can confirm the trust at any point with the `…/tls-check` address the script prints: open it in
**Safari** on the other device and look for a padlock and `TLS OK`.

**From then on**, step 1b alone is enough — the CA is never needed again on that device.

### Cleaning up

On the target: **Settings → General → VPN & Device Management** → remove every *BariSign* entry.

## Step 2 — Sign in to Apple

```
cd ~/Documents/barisign
sh bin/barisign check                 # runtime, crypto, anisette, Apple TLS
sh bin/barisign login                 # asks for the Apple ID and password
```

It stops after sign-in: no device registered, nothing installed. The password is never transmitted
(SRP-6a) and never written to disk. With 2FA, Apple sends a code and the script prompts for it.

---

## Step 3 — Developer portal (written, not yet tested against Apple)

Registers the UDID, creates an App ID, gets a development certificate for a key generated **on this
device**, and downloads the matching provisioning profile. Nothing is installed, and nothing is revoked
without asking.

```
cd ~/Documents/barisign
sh bin/barisign portal        # uses the UDID and Apple ID saved by the earlier stages
```

A first run with the defaults is a safe smoke test: it registers the UDID and uses the throwaway
identifier `com.barisign.smoke`. For a real variant, pass the identifier it needs (see `variants.json`),
for example:

```
sh bin/barisign portal --bundle-id com.SideStore.SideStore
```

You will be asked for your Apple ID password again (nothing is kept between runs). The script prints the
team, the registered device, the App ID, whether the certificate was **created or reused**, and where the
private key and `profile.mobileprovision` were written — plus the profile's own name, expiry, how many
devices it lists (and whether yours is among them) and its entitlements.

**Notes that will matter**

- The bundle identifier gets your team ID appended, exactly as SideStore and AltStore do
  (`com.SideStore.SideStore.<TEAMID>`). The provisioning profile must match the app you later install.
- A free account gets **two** development certificates and a limited number of App IDs and devices. If
  Apple says the certificate limit is reached (error 7460) the script asks before revoking anything.
- If the account has never accepted the free developer agreement, Apple returns no team and the script
  says so — open `developer.apple.com` in Safari once and accept it.

---

## Step 4 — Sign the IPA (to build — the last big piece)

An IPA is a zip; the app inside must carry a valid Apple code signature: a CodeDirectory over every page,
entitlements, and a CMS signature with Apple's two custom attributes. It has to be written from scratch in
pure Python and is the make-or-break item (`docs/PHASE0_REPORT.md`, §13). Until it exists, nothing this
project produces will launch.

---

## Step 5 — Install by AirDrop (works today)

Once an IPA is validly signed for the target's UDID:

1. On the signing device: `sh bin/udid --view /absolute/path/App.ipa` (a preview opens), or use Files.
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
- **Free Apple ID limits:** 3 apps installed at a time (SideStore counts as one), limited App IDs, 7-day
  signing.
- **The UDID is not a credential** — an identifier developers paste into portals daily. No Apple ID,
  password or pairing record is involved in Step 1.

## Troubleshooting

| What you see | What it means |
|---|---|
| `RESULT: UDID received` + the model you expected | ✅ step 1 done |
| `no UDID arrived within 300 seconds` | the profile was not installed, or the CA trust switch is off — check the `…/tls-check` padlock |
| *"MDM certificate is invalid"* | the CA's trust switch is OFF, or an **older** BariSign CA is installed — delete it and install the current one |
| the profile screen never appears | you are not in Safari, or the preview was closed — `python3 poc/udid_share.py --view BariSign-UDID.mobileconfig` |
| iOS says the profile is **empty** | expected — the UDID was already sent |
| portal: *"no developer team"* | accept the free developer agreement once at `developer.apple.com` in Safari |
| portal: error **7460** | the account is at its certificate limit (free accounts get two) — the script asks before revoking |
| portal: *already registered* | that UDID is already on the team; harmless |
| `bin/update` says "git pull failed" | the checkout has local changes — `git status` in the folder will show them |

## What comes next

Stage 4 (the signer), then a Shortcut that chains everything. At that point this guide collapses into:
*tap the Shortcut, scan a code, accept an AirDrop.*
