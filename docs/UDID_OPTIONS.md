# Reading your UDID — three ways

BariSign needs your device's **UDID** because a free Apple ID can only sign apps for devices that are
registered on it. There is nothing secret about a UDID: it is a hardware identifier developers paste into
portals every day. It is **not** a credential, and no Apple ID, password or pairing record is involved in
getting it.

iOS will only hand it over through a **Profile Service** profile, and iOS only accepts such a profile from
**HTTPS with a certificate it trusts**. That is the whole reason every option below needs *some* trusted
endpoint. Pick one:

---

## A — median.co (what BariSign asks for by default)

One tap, no trust switch, works on any iOS 27 device.

1. Open **Safari** (not Chrome, not an in-app browser) on the device that will get the app:
   **https://median.co/udid**
2. Tap the install button → **Allow** the profile download → then finish it:
   **Settings → General → VPN & Device Management → Downloaded Profile → Median UDID Retrieval →
   Install → passcode → Install**.
3. Safari comes back showing the UDID. Tap **Copy UDID to Clipboard**.
4. In a-Shell: `sh bin/barisign udid` and paste it (or `sh bin/barisign udid 00008110-001a2c3e0e91801e`).
5. Remove the profile: **Settings → General → VPN & Device Management → Median UDID Retrieval →
   Remove Profile**.

**Privacy:** median.co sees your UDID and the device model — nothing else. Your Apple ID is not involved.
**We are not affiliated with median.co** and we do not control what it logs; if you would rather not use a
third party at all, use option C.

---

## B — any other third-party site

Several services do the same thing. At the time of writing `udid.io` and `get.udid.io` both redirect to
**udid.tech** (checked 2026-10-29), which is a working example.

- **We have no connection with any of these sites.** They are not vetted, endorsed or paid by BariSign, and
  they can change owners or behaviour at any time.
- They **do** see the UDID you send them. That is unavoidable with this route; it is not a password, but
  treat it as something you would not post publicly.
- **Never type your Apple ID or password into a "get your UDID" site.** No legitimate one needs it. If it
  asks, close the page.
- Use Safari, and remove the profile afterwards as in step 5 above.

Whatever the site gives you, BariSign accepts it as long as it is the right shape (see the end of this
file).

---

## C — serve the profile yourself (no third party)

This is what `sh bin/barisign udid --local` does, and it is the route verified first on this project. It
needs a certificate authority that **you** generate, installed and trusted on the device once — after that
iOS accepts your HTTPS endpoint.

### C1 — let BariSign do it

```
sh bin/barisign udid --local --ca     # writes BariSign-CA.mobileconfig; share it to the device and install it
                                      # then: Settings -> General -> About -> Certificate Trust Settings
                                      #       -> switch ON "BariSign CA XX:XX"
sh bin/barisign udid --local          # serves the UDID profile from this device, on your Wi-Fi, and waits
```

The certificate authority persists in `poc/barisign-ca.*` (git-ignored, mode 0600), so the trust switch is a
one-time job. Nothing leaves your network.

### C2 — write your own

Any HTTPS server with a trusted certificate works. Two pieces are needed.

**1. The profile** — a `.mobileconfig` containing a `Profile Service` payload. iOS does not use `URL` for
anything except the POST target:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>PayloadType</key><string>Profile Service</string>
  <key>PayloadIdentifier</key><string>example.udid</string>
  <key>PayloadUUID</key><string>6E1B2E45-1A0E-4E3E-9E4B-4E3B7F0A1C22</string>
  <key>PayloadVersion</key><integer>1</integer>
  <key>PayloadDisplayName</key><string>Get UDID</string>
  <key>PayloadOrganization</key><string>example</string>
  <key>PayloadContent</key><dict>
    <key>URL</key><string>https://your-server.example/udid</string>
    <key>DeviceAttributes</key><array>
      <string>UDID</string><string>PRODUCT</string><string>VERSION</string><string>SERIAL</string>
    </array>
  </dict>
</dict></plist>
```

**2. An HTTPS endpoint that accepts the POST.** iOS sends a plist body; this prints it:

```python
import http.server, ssl

class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        print(body.decode("utf-8", "replace"))          # <key>UDID</key><string>...</string>
        self.send_response(200); self.end_headers()
        self.wfile.write(b"<html><body>Done. Check the terminal.</body></html>")

srv = http.server.HTTPServer(("0.0.0.0", 8443), Handler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain("fullchain.pem", "privkey.pem")     # a chain iOS already trusts
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
```

Rules that matter, learned the hard way:

- The chain must be **trusted** by the device. A self-signed root works **only** after you install it and
  switch its trust ON in **Certificate Trust Settings** — that is exactly what option C1 sets up.
- Serve the profile from the **same host** the profile's `URL` names, and keep the server running: iOS
  posts the moment the profile finishes installing.
- **Safari only.** Other browsers save the file instead of handing it to Settings.
- iOS says the installed profile is **empty** — expected: a `Profile Service` payload configures nothing,
  and the UDID has already been posted by then.

---

## What a valid UDID looks like

- **40 hex characters** on older devices, e.g. `a1b2c3d4e5f60718293a4b5c6d7e8f9012345678`
- **8 + 16 hex characters** on A12-and-later devices, written `00008110-001a2c3e0e91801e` (the dash is
  optional).

BariSign lowercases it, restores the dash when needed, and refuses anything else — three attempts, then it
stops without saving.
