# BariSign UDID collector — a free HTTPS endpoint you create **in Safari**, no PC

This is the missing piece. Apple still supports the `Profile Service` configuration profile on iOS 27 —
our own on-device tests proved it (the target really does POST to the callback URL). What iOS **requires**
is that the callback is **HTTPS with a publicly trusted certificate**:

- `http://` → refused;
- `https://` with a certificate **you installed yourself** (a CA profile) → refused,
  *"MDM certificate is invalid"*;
- `https://` with an ordinary public certificate → **works**.

A free **Cloudflare Worker** gives exactly that: a real, publicly trusted HTTPS URL, free, with no
computer, no server of your own, and no certificate to manage. This is the same mechanism the commercial
"get my UDID" services have used for years (they served this payload over HTTPS).

## What it gives you

**One profile. No app on the target. No Developer Mode. No certificate. No computer.**

```
Device A (signer)                     Device B (target, nothing installed)
 opens /s/<token>  ── shows a QR ────►  Safari opens it
                                        taps "Download the UDID profile" → Install
                                        iOS POSTs UDID/PRODUCT/VERSION/SERIAL over HTTPS
 polls GET /result/<token> ◄──────────┘
 gets the UDID → registers it → signs → AirDrops the app
```

## Deploy it (browser only, ~5 minutes, once ever)

1. On any device, sign in at <https://dash.cloudflare.com> (free account).
2. **Workers & Pages → Create → Worker**, name it `barisign`, **Deploy** the hello-world.
3. **Edit code**, delete what is there, paste the whole of `worker.js` from this folder, **Deploy**.
4. **Storage & Databases → KV → Create namespace**, name it `BARISIGN`.
5. Back in the Worker: **Settings → Bindings → Add → KV namespace**,
   Variable name **`UDID_KV`**, namespace `BARISIGN`. **Deploy** once more.
6. Note your URL: `https://barisign.<your-subdomain>.workers.dev`.

## Use it

Easiest — let the Worker create the session (`GET /new`, valid 30 minutes):

```
GET  https://…/new                    -> { "session": "…", "url": "…/s/…" }
open https://…/s/<session>            on Device B in Safari (or show it as a QR)
POST /udid/<session>                  is done by iOS when the profile is installed
GET  https://…/result/<session>       -> { "udid": "…", "product": "…", … }
```

`/new` answers JSON when asked with `Accept: application/json` (that is what the signer's script uses)
and an HTML page otherwise. Or pick your own long random token and skip `/new`:
open `…/s/<TOKEN>` on Device B, then read `…/result/<TOKEN>` on Device A.

On Device B: **Download the UDID profile** → Settings → General → VPN & Device Management → the
profile → **Install** → passcode. The result is returned **once** and expires after 15 minutes even if
never read.

## Notes that matter

- **Safari only.** Chrome/Firefox/in-app browsers save the profile as a download instead of handing it to
  Settings, which looks like nothing happening.
- **iOS 26.4+ / 27 Security Delay:** on an iPhone, installing *any* configuration profile at a location
  iOS does not consider familiar triggers a one-hour delay (Face ID → wait → Face ID). At home it does
  not, and **iPad is unaffected**.
- The profile is unsigned, asks only for UDID / PRODUCT / VERSION / SERIAL, and installs nothing else.
  Delete it on Device B afterwards (it is not always removed automatically).
- The token is the only secret: anyone who knows it can read the result, so use a long random one and
  treat the URL as one-shot.
- **Privacy:** the UDID goes to *your own* Worker, over HTTPS, once. Apple ID, password and pairing
  records are never involved in this step at all.

*Status: written and syntax-checked; **untested on a device**. The payload shape and the HTTPS
requirement are verified from our own device tests plus Apple's archived Over-the-Air Profile Delivery
guide.*
