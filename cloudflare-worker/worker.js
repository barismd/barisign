// BariSign UDID collector — one shared public endpoint, zero setup for users.
//
// Users never touch Cloudflare. BariSign deploys this once; every user's Shortcut
// just asks for a session, shows a QR, and polls the result.
//
// WHY THIS SHAPE
// Apple's `Profile Service` configuration profile still works on iOS 27; it only
// requires an HTTPS callback with a **publicly trusted** certificate. http:// is
// refused, and a certificate the user installed themselves is refused too
// ("MDM certificate is invalid"). A Worker gives a real public HTTPS URL for free.
//
// SECURITY DESIGN (read this before changing anything)
//   * Two secrets per session: `session` travels in the QR (public), `key` never
//     leaves the signing device. Without the key the result cannot be read.
//   * Write-once: the first valid POST for a session wins; later ones are refused,
//     so nobody can overwrite a real UDID with a fake one.
//   * The result is deleted the moment it is read, and expires in 15 minutes even
//     if it never is. Sessions expire in 30 minutes.
//   * No logging of bodies. Nothing sensitive is stored: never an Apple ID,
//     password, token or pairing record — only the identifier the device reports.
//   * Best-effort per-IP throttle on session creation, plus Cloudflare's own rate
//     limiting in the dashboard (recommended: 10 requests/minute per IP on /new).
//   * Honest limit: the UDID does pass through this endpoint, in clear, inside TLS.
//     Anyone who does not want that runs their own copy of this same file instead.

const hits = new Map();                      // best-effort, per isolate

function throttle(ip, limit = 10, windowMs = 60000) {
  const now = Date.now();
  const entry = hits.get(ip);
  if (!entry || now - entry.start > windowMs) {
    hits.set(ip, { start: now, count: 1 });
    if (hits.size > 5000) hits.clear();      // do not grow without bound
    return true;
  }
  entry.count += 1;
  return entry.count <= limit;
}

function grab(body, key) {
  const m = body.match(new RegExp("<key>" + key + "<\\/key>\\s*<(string|data)>([^<]*)<\\/\\1>"));
  return m ? m[2] : null;
}

function profile(origin, session) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>PayloadContent</key><dict>
    <key>URL</key><string>${origin}/udid/${session}</string>
    <key>DeviceAttributes</key><array>
      <string>UDID</string><string>PRODUCT</string><string>VERSION</string>
    </array>
  </dict>
  <key>PayloadType</key><string>Profile Service</string>
  <key>PayloadVersion</key><integer>1</integer>
  <key>PayloadIdentifier</key><string>io.barisign.udid.${session}</string>
  <key>PayloadUUID</key><string>${crypto.randomUUID().toUpperCase()}</string>
  <key>PayloadDisplayName</key><string>BariSign - read this device's UDID</string>
  <key>PayloadDescription</key><string>Temporary. Reports this device's UDID to the BariSign endpoint over HTTPS, once. Delete it afterwards.</string>
  <key>PayloadOrganization</key><string>BariSign</string>
</dict></plist>`;
}

// Apple expects the collection endpoint to answer with a profile; an empty one is fine.
function done() {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>PayloadContent</key><array/>
  <key>PayloadType</key><string>Configuration</string>
  <key>PayloadVersion</key><integer>1</integer>
  <key>PayloadIdentifier</key><string>io.barisign.udid.done</string>
  <key>PayloadUUID</key><string>${crypto.randomUUID().toUpperCase()}</string>
  <key>PayloadDisplayName</key><string>BariSign - done (delete me)</string>
  <key>PayloadDescription</key><string>Nothing to install. Safe to delete.</string>
  <key>PayloadOrganization</key><string>BariSign</string>
</dict></plist>`;
}

const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status,
  headers: { "content-type": "application/json", "cache-control": "no-store" },
});

const page = (title, inner) => new Response(
  `<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>${title}</title><style>body{font:17px -apple-system,sans-serif;background:#111;color:#eee;
margin:0;padding:2rem 1.25rem}a.btn{display:block;text-align:center;background:#2ea44f;color:#fff;
text-decoration:none;padding:1rem;border-radius:14px;font-weight:600;margin:1.5rem 0}
p{line-height:1.5;color:#bbb}code{background:#222;padding:.15rem .35rem;border-radius:5px}</style>
<h1>${title}</h1>${inner}`,
  { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);
    const origin = url.origin;
    const ip = request.headers.get("cf-connecting-ip") || "unknown";

    // --- create a session (the Shortcut calls this) ------------------------
    if (parts[0] === "new") {
      if (!throttle(ip)) return json({ error: "too many requests" }, 429);
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      const session = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
      const keyBytes = crypto.getRandomValues(new Uint8Array(16));
      const key = [...keyBytes].map((b) => b.toString(16).padStart(2, "0")).join("");
      await env.UDID_KV.put("key:" + session, key, { expirationTtl: 1800 });
      return json({ session, key, url: origin + "/s/" + session }, 201);
    }

    // --- the page the target opens (session only, no key) ------------------
    if (parts[0] === "s" && parts[1]) {
      const session = parts[1];
      if (url.searchParams.has("profile")) {
        return new Response(profile(origin, session), {
          headers: { "content-type": "application/x-apple-aspen-config",
                     "cache-control": "no-store" },
        });
      }
      return page("BariSign - read this device's UDID", `
        <a class=btn href="/s/${session}?profile=1">Download the UDID profile</a>
        <p>Then open <b>Settings &rarr; General &rarr; VPN &amp; Device Management</b>, tap the
        downloaded profile, and tap <b>Install</b>. The UDID is reported once over HTTPS.</p>
        <p><small>Must be opened in <b>Safari</b>. On an iPhone (iOS 26.4+) installing a profile
        away from a familiar location can trigger a one-hour Security Delay; at home it does not,
        and iPad is unaffected.</small></p>`);
    }

    // --- iOS posts the attributes here (write once) ------------------------
    if (parts[0] === "udid" && parts[1] && request.method === "POST") {
      const session = parts[1];
      const known = await env.UDID_KV.get("key:" + session);
      if (!known) {
        return new Response(done(), {   // unknown or expired: answer politely, store nothing
          headers: { "content-type": "application/x-apple-aspen-config" },
        });
      }
      if (await env.UDID_KV.get("udid:" + session)) {
        return json({ error: "already reported" }, 409);   // never overwrite a real UDID
      }
      const body = await request.text();
      const udid = grab(body, "UDID");
      if (!udid) return json({ error: "no UDID in request" }, 400);
      await env.UDID_KV.put("udid:" + session, JSON.stringify({
        udid,
        product: grab(body, "PRODUCT"),
        version: grab(body, "VERSION"),
        received_at: new Date().toISOString(),
      }), { expirationTtl: 900 });
      return new Response(done(), {
        headers: { "content-type": "application/x-apple-aspen-config" },
      });
    }

    // --- the signer reads the result (session + key) -----------------------
    if (parts[0] === "result" && parts[1]) {
      const session = parts[1];
      const expected = await env.UDID_KV.get("key:" + session);
      const given = url.searchParams.get("key") || "";
      if (!expected) return json({ status: "expired" }, 404);
      if (given.length !== expected.length || given !== expected) {
        return json({ status: "forbidden" }, 403);       // wrong or missing key
      }
      const stored = await env.UDID_KV.get("udid:" + session);
      if (!stored) return json({ status: "waiting" });
      await env.UDID_KV.delete("udid:" + session);        // read once, then gone
      await env.UDID_KV.delete("key:" + session);
      return new Response(stored, {
        headers: { "content-type": "application/json", "cache-control": "no-store" },
      });
    }

    return page("BariSign", "<p>Nothing here. Open the link you were given.</p>");
  },
};
