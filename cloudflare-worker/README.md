# Cloudflare Worker — complete guide (no computer, no certificate, one profile)

## Do users have to set up Cloudflare? **No.**

BariSign deploys **one shared Worker** and every user gets a real HTTPS endpoint without doing
anything: the Shortcut asks for a session, shows a QR, and polls the result. Self-hosting this file is
only for someone who does not want their UDID to pass through BariSign's endpoint at all — both paths
use the same code, and the guide below is the self-hosted one.

### How the shared endpoint is kept safe

| Concern | What the code does |
|---|---|
| Someone else reads your result | Every session has a **`session`** (travels in the QR) and a separate **`key`** that never leaves the signing device. `/result` requires both. A leaked QR is useless on its own. |
| Someone overwrites your UDID with a fake | **Write-once.** The first valid POST for a session wins; any later one gets `409`, so a real UDID can never be replaced. |
| Leftover data | The result is **deleted the moment it is read**, and expires after **15 minutes** even if it is never read. Sessions expire after 30 minutes. |
| Abuse / cost | A best-effort per-IP throttle on `/new`, plus Cloudflare's own rate limiting (set **10 requests/minute per IP** on `/new` in the dashboard). |
| Stored secrets | **None.** No Apple ID, password, token or pairing record ever reaches the endpoint — only the device's own identifier, briefly. |
| Honest limit | The UDID *does* pass through the endpoint in clear inside TLS. Anyone unwilling to accept that self-hosts this same file. |

## First, the thing you were worried about

**In this flow there is no CA and no second certificate. Nothing gets installed except one profile.**

- udid.tech worked because udid.tech's server has a **real, publicly trusted certificate** (Let's
  Encrypt). Your phone trusts it already, so you install **only the UDID profile** and nothing else.
- Our earlier attempt failed exactly because we tried to be our own authority: a certificate *we* made
  has to be installed and explicitly trusted first → **two profiles**, and iOS still refused it
  (*"MDM certificate is invalid"*). That approach is **retired**.
- A Cloudflare Worker gives you the same thing udid.tech has — **a real public certificate** — for free,
  under your own control. So: **one profile, zero certificate installs.**

---

## What you need

- A **Cloudflare account** (free, no card). You can sign up in Safari on the iPad.
- The file `cloudflare-worker/worker.js` from this repository.
- 5 minutes, once.

> The Cloudflare dashboard's wording changes from time to time. If a label differs, look for the
> equivalent: *Workers* / *Workers & Pages*, *KV* / *Storage*, *Bindings* / *Variables*.

---

## Part 1 — Create the Worker

1. Open **Safari** and go to `https://dash.cloudflare.com` and sign in (or **Sign up**).
2. In the left sidebar tap **Compute (Workers)** — on older layouts it is **Workers & Pages**.
3. Tap **Create** → **Workers** → **Start with Hello World!** (or *Create Worker*).
4. Give it a name: `barisign`. Tap **Deploy**.
   - Ignore the random `*.workers.dev` name it may show; after deploy it will be `barisign.<something>`.
5. Tap **Edit code** (or **</> Edit**).
6. Select everything in the editor and **delete it**.
7. Open `cloudflare-worker/worker.js` from this repository (on the same device: GitHub → the file →
   the **Raw** button → select all → copy). Paste it into the editor.
8. Tap **Deploy** (top right). Wait for "Deployed".

---

## Part 2 — Create the storage the Worker needs

The Worker has to hold the UDID for the few seconds between the target reporting it and the signer
reading it.

1. In the sidebar go to **Storage & Databases** → **KV** (older layouts: **Workers & Pages → KV**).
2. Tap **Create namespace** (older: **Create a namespace**).
3. Name it exactly **`BARISIGN`**. Create it.

---

## Part 3 — Connect the storage to the Worker

1. Go back to **Compute (Workers)** → **barisign**.
2. Tap **Settings** → **Bindings** (older: **Settings → Variables and Secrets**).
3. Tap **Add** → choose **KV namespace**.
4. **Variable name:** `UDID_KV` — exactly this, capital letters. The Worker reads this name.
5. **KV namespace:** choose **`BARISIGN`** from the list.
6. Tap **Save** / **Deploy** so the binding takes effect.

---

## Part 4 — Find your endpoint

On the Worker's page, find its address. It looks like:

```
https://barisign.<your-subdomain>.workers.dev
```

Write it down. That is your private UDID service. Nobody else can use it, and nothing is shared with
udid.tech or anyone else.

---

## Part 5 — Test it end to end

**Do the first test on the iPad.** (On an iPhone running iOS 26.4+, installing *any* profile away from a
location iOS considers familiar triggers a **one-hour Security Delay**. At home it does not, and the
iPad is unaffected.)

1. On the iPad, in **Safari**, open:

   ```
   https://barisign.<your-subdomain>.workers.dev/new
   ```

   It answers `{"session":"…","key":"…","url":"…"}` when asked with `Accept: application/json`
   (that is what the Shortcut and the a-Shell script use), and an HTML page otherwise. **Keep the
   `key` private** — it is what makes the result readable only by the signing device.

2. Tap that link → **Download the UDID profile**.
3. Open **Settings → General → VPN & Device Management**, tap the **BariSign** profile, tap **Install**,
   enter the passcode.
4. Go back to Safari and open:

   ```
   https://barisign.<your-subdomain>.workers.dev/result/<session>?key=<key>
   ```

5. You should see JSON (the `key` from step 1 is required):

   ```json
   {"udid":"00008110-…","product":"iPad15,7","version":"27.0","serial":"…","received_at":"…"}
   ```

### What success and failure look like

| You see | Meaning |
|---|---|
| the JSON with `udid` | ✅ **It works.** The whole UDID stage is solved and it is yours. |
| `{"status":"waiting"}` | The profile was not installed yet (or the install was blocked) — install it, then reload. |
| `{"status":"forbidden"}` | the `key` is missing or wrong |
| `{"status":"expired"}` | the session was older than 30 minutes — start a new one |
| an error page from Cloudflare | The binding is missing or wrongly named — `UDID_KV` must match exactly. |
| the profile screen never appears | You are not in Safari, or the page was opened inside another app. |
| Safari says the profile could not be installed | Security Delay (see below), or an already-managed device. |

Afterwards: **delete the profile on the target** — Settings → General → VPN & Device Management → the
BariSign entry → *Remove Profile*. (Some services remove it automatically; ours does not, so delete it.)

---

## Part 6 — Limits and behaviour you should know

- `/new` creates a session that stays valid **30 minutes**.
- The collected result is returned **once** and expires after **15 minutes** even if nobody reads it.
- The URL's session id is the only secret. Use `/new` (random) rather than inventing a short token.
- Cloudflare's free tier is far more than enough for personal use.
- Nothing sensitive is stored: no Apple ID, no password, no pairing record — only the identifier the
  device itself reports, briefly.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Cannot read properties of undefined (reading 'put')` | The KV binding is missing or not named `UDID_KV`. Redo Part 3 and Deploy. |
| A 404 page at `/new` | You pasted an older copy of `worker.js`, or the deploy did not finish. Paste the file again and Deploy. |
| The result says `waiting` forever | The profile was never installed on the device. Check VPN & Device Management. |
| Chrome / Instagram / in-app browser | Only **Safari** hands a downloaded profile to Settings. Open the link in Safari. |
| "Security Delay" / "wait an hour" | Apple's Stolen Device Protection (iOS 26.4+). Tap *Start Security Delay*, keep the link, come back when iOS notifies you — or do it on the iPad / at home. |
| You want it on your own domain | Worker → **Settings → Domains & Routes → Add custom domain**. Not needed for testing. |

---

## Later: making it public for everyone

`/new` is exactly the endpoint a public BariSign service would expose: the Shortcut asks for a session,
shows the QR, and polls `/result/<session>`. If BariSign ever hosts one shared Worker, users deploy
nothing at all — they only scan a code. Someone who does not want their UDID to pass through a shared
endpoint keeps this self-hosted copy instead. Same file, both ways.

*Status: written and syntax-checked; **untested on a device**. The underlying mechanism is confirmed
working on the user's own iOS 27 device via udid.tech.*
