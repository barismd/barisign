# BariSign — complete guide

**State of the world right now.** The hardest unknown is solved: on your iOS 27 device, Apple's
`Profile Service` configuration profile **does** report the UDID (you saw it on udid.tech). Everything
below is the honest, current picture: what works today, what you can do right now, and what is still
being built.

| Stage | What it does | Status |
|---|---|---|
| 1 | Get the target device's **UDID** with no computer, no app | ✅ **works** (tested on your device) |
| 2 | Sign in to Apple with the **free Apple ID** (anisette + SRP + 2FA) | ✅ **works** (tested on your device) |
| 3 | Register the UDID, create App ID + certificate + provisioning profile | 🚧 being built (code exists in the reference projects; not in BariSign yet) |
| 4 | **Sign the IPA** (Mach-O + Apple CMS signature) | 🚧 being built — the last big piece |
| 5 | Install by **AirDrop** | ✅ **works** (tested: a signed IPA installs) |

So right now you can do stages 1, 2 and 5 by hand. Stages 3 and 4 are what the signer will automate.

---

## Step 1 — Get the UDID **privately** (your own endpoint, 5 minutes, once)

udid.tech works, but it sees your UDID. The same mechanism on **your own** Cloudflare Worker keeps it
private. Full detail in `cloudflare-worker/README.md`; the short version:

1. In **Safari**, sign in at `dash.cloudflare.com` (free account).
2. **Workers & Pages → Create → Worker**, name `barisign`, **Deploy**.
3. **Edit code** → delete everything → paste all of `cloudflare-worker/worker.js` → **Deploy**.
4. **Storage & Databases → KV → Create namespace**, name `BARISIGN`.
5. Worker → **Settings → Bindings → Add → KV namespace**: Variable name **`UDID_KV`**,
   namespace `BARISIGN` → **Deploy**.
6. Your endpoint is `https://barisign.<your-subdomain>.workers.dev`.

**Test it (use the iPad first — iPhones may hit the one-hour Security Delay):**

```
https://barisign.<subdomain>.workers.dev/new          → shows a session URL
```
Open the `…/s/<session>` URL **in Safari**, tap *Download the UDID profile*, then
**Settings → General → VPN & Device Management → the profile → Install** → passcode.

Then read the result:

```
https://barisign.<subdomain>.workers.dev/result/<session>
→ {"udid":"00008110-…","product":"iPad15,7","version":"27.0","serial":"…"}
```

The result is returned **once** and expires after 15 minutes. `/new` sessions last 30 minutes.

**Afterwards, on the target:** Settings → General → VPN & Device Management → delete the BariSign
profile. It configures nothing and installs nothing, but it should not be left lying around.

---

## Step 2 — Sign in to Apple (works today)

In a-Shell, in `~/Documents/barisign`:

```
curl -sL "https://github.com/barismd/barisign/blob/main/poc/poc_login.py?raw=true" -o poc_login.py
curl -sL "https://github.com/barismd/barisign/blob/main/third_party/sideinstaller/signin.py?raw=true" -o signin.py
curl -sL "https://github.com/barismd/barisign/blob/main/third_party/apple-roots/apple-roots.pem?raw=true" -o apple-roots.pem
curl -sL "https://github.com/barismd/barisign/blob/main/third_party/ca-bundle/cacert.pem?raw=true" -o cacert.pem
python3 -u poc_login.py --selftest
python3 -u poc_login.py --login YOUR-APPLE-ID
```

It stops after sign-in: no device registered, nothing installed. Your password is never transmitted
(SRP-6a) and never written to disk.

---

## Step 3 — Register the UDID and get a profile (being built)

The plan, once built, runs entirely from the iPad:

1. a-Shell (`poc/…`) asks Apple for your teams and certificates.
2. It registers the UDID you collected in Step 1 (a free account can hold a limited number of devices).
3. It creates an App ID for the chosen variant and downloads a provisioning profile listing that device.
4. It creates (or reuses) a development certificate tied to a key generated **on the device**.

**Manual alternative in the meantime:** open `developer.apple.com` in Safari → *Certificates, Identifiers
& Profiles* → **Devices → +** → paste the UDID from Step 1. That works from the phone today; the rest
(App ID, certificate, profile) is fiddly by hand but possible.

---

## Step 4 — Sign the IPA (being built — the last big piece)

An IPA is a zip; the app inside must carry a valid Apple code signature: a CodeDirectory over every page,
entitlements, and a CMS signature with Apple's two custom attributes. This is the one component that has
to be written from scratch in pure Python, and it is what §13 of `PHASE0_REPORT.md` flagged as the
make-or-break item. Until it exists, the pipeline cannot produce something the device will launch.

---

## Step 5 — Install by AirDrop (works today)

Once the IPA is validly signed for the target's UDID:

1. On the signing device: `view /absolute/path/App.ipa` (a-Shell's `view` opens a preview with a **share**
   button), or use the Files app.
2. Share → **AirDrop** → pick the other device.
3. On the target: accept. **iOS installs it by itself — there is no extra "Install" tap.**
4. Open the app.

If it installs but will not open, the signature is not valid for that device (wrong UDID, expired
certificate, or a broken signature).

---

## Caveats worth knowing (all verified)

- **Safari only.** Chrome and in-app browsers save the profile as a download instead of handing it to
  Settings, which looks like nothing happening.
- **Security Delay.** On an **iPhone** running iOS 26.4+, installing *any* configuration profile at a
  location iOS does not consider familiar triggers a **one-hour** delay (Face ID → 1 hour → Face ID). At
  home it does not, and **iPad is unaffected**. If you hit it: tap *Start Security Delay*, keep the page
  open, and come back when iOS notifies you.
- **Free Apple ID limits:** 3 apps installed at a time (SideStore counts as one), a limited number of App
  IDs, and signing expires every 7 days.
- **The UDID is not a credential.** It is an identifier developers paste into portals every day. It is not
  an Apple ID, password, or pairing record, and none of those are involved in Step 1.

---

## What happens after Step 1 and 2 are done

The remaining work is code, not discovery: the developer-portal calls (Stage 3) and the signer (Stage 4),
then a Shortcut that chains Step 1 → 2 → 3 → 4 → 5 with the UDID step done by scanning a QR that the
signing device displays. When those exist, this guide collapses into: *tap the Shortcut, scan a code,
accept an AirDrop.*
