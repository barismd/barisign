// BariSign UDID collector — a free HTTPS endpoint, created in a browser, no PC.
//
// WHY THIS EXISTS
// Apple retired nothing: the `Profile Service` configuration profile still works
// on iOS 27. What it requires is a callback over **HTTPS with a publicly trusted
// certificate**. `http://` is refused, and a certificate the user installed
// themselves (a CA profile) is refused too — iOS answers "MDM certificate is
// invalid". A free Cloudflare Worker gives a real, publicly trusted HTTPS URL
// with no computer, no server of your own, and no certificate to manage.
//
// FLOW
//   1. Device B opens  GET /s/<TOKEN>  in Safari  (the page shows one button)
//   2. iOS downloads this profile, the user taps Install
//   3. iOS POSTs the device attributes to /udid/<TOKEN>   (HTTPS, one shot)
//   4. This Worker stores them and answers with a minimal profile (as Apple expects)
//   5. Device A reads  GET /result/<TOKEN>  and gets the UDID
//
// One profile. No app on the target. No Developer Mode. No CA. No computer.
//
// DEPLOY (browser only): Cloudflare dashboard -> Workers & Pages -> Create ->
// paste this file -> Create a KV namespace called BARISIGN -> bind it as
// UDID_KV -> Deploy. Full steps in README.md next to this file.

const UDID_RE = /<key>UDID<\/key>\s*<(string|data)>([^<]*)<\/\1>/;

function attributes(body) {
  const grab = (key) => {
    const m = body.match(new RegExp("<key>" + key + "<\\/key>\\s*<(string|data)>([^<]*)<\\/\\1>"));
    return m ? m[2] : null;
  };
  return {
    udid: grab("UDID"),
    product: grab("PRODUCT"),
    version: grab("VERSION"),
    serial: grab("SERIAL"),
    received_at: new Date().toISOString(),
  };
}

function profile(origin, token) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>PayloadContent</key><dict>
    <key>URL</key><string>${origin}/udid/${token}</string>
    <key>DeviceAttributes</key><array>
      <string>UDID</string><string>PRODUCT</string><string>VERSION</string><string>SERIAL</string>
    </array>
  </dict>
  <key>PayloadType</key><string>Profile Service</string>
  <key>PayloadVersion</key><integer>1</integer>
  <key>PayloadIdentifier</key><string>io.barisign.udid.${token}</string>
  <key>PayloadUUID</key><string>${crypto.randomUUID().toUpperCase()}</string>
  <key>PayloadDisplayName</key><string>BariSign - read this device's UDID</string>
  <key>PayloadDescription</key><string>Temporary. Reports this device's UDID to your own BariSign endpoint over HTTPS. Delete it afterwards.</string>
  <key>PayloadOrganization</key><string>BariSign</string>
</dict></plist>`;
}

// Apple expects the enrolment endpoint to answer with a profile; an empty one is fine.
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

const page = (title, inner) => new Response(
  `<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>${title}</title><style>body{font:17px -apple-system,sans-serif;background:#111;color:#eee;
margin:0;padding:2rem 1.25rem}a.btn{display:block;text-align:center;background:#2ea44f;color:#fff;
text-decoration:none;padding:1rem;border-radius:14px;font-weight:600;margin:1.5rem 0}
p{line-height:1.5;color:#bbb}code{background:#222;padding:.15rem .35rem;border-radius:5px}</style>
<h1>${title}</h1>${inner}`,
  { headers: { "content-type": "text/html; charset=utf-8" } });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);
    const origin = url.origin;

    if (parts[0] === "new") {
      const id = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
      await env.UDID_KV.put("session:" + id, "1", { expirationTtl: 1800 });
      const target = origin + "/s/" + id;
      if ((request.headers.get("accept") || "").includes("application/json")) {
        return new Response(JSON.stringify({ session: id, url: target }),
          { headers: { "content-type": "application/json" } });
      }
      return page("BariSign \u2014 new session", `
        <p>Show this page to the device that needs the app, or scan it if you have a QR
        reader. It is valid for 30 minutes.</p>
        <a class=btn href="${target}">${target}</a>
        <p><small>Result: <code>${origin}/result/${id}</code></small></p>`);
    }

    if (parts[0] === "s" && parts[1]) {
      const token = parts[1];
      if (url.searchParams.has("profile")) {
        return new Response(profile(origin, token), {
          headers: { "content-type": "application/x-apple-aspen-config" },
        });
      }
      return page("BariSign — read this device's UDID", `
        <a class=btn href="/s/${token}?profile=1">Download the UDID profile</a>
        <p>Then open <b>Settings &rarr; General &rarr; VPN &amp; Device Management</b>,
        tap the downloaded profile, and tap <b>Install</b>. The UDID is reported once,
        over HTTPS, and the profile can be deleted afterwards.</p>
        <p><small>Must be opened in <b>Safari</b>. On iPhone (iOS 26.4+) installing a
        profile somewhere iOS considers unfamiliar can trigger a one-hour Security
        Delay; at home it does not, and iPad is unaffected.</small></p>`);
    }

    if (parts[0] === "udid" && parts[1] && request.method === "POST") {
      const body = await request.text();
      const attrs = attributes(body);
      if (attrs.udid) {
        // one shot: the result self-deletes after 15 minutes even if never read
        await env.UDID_KV.put("udid:" + parts[1], JSON.stringify(attrs),
          { expirationTtl: 900 });
      }
      return new Response(done(), {
        headers: { "content-type": "application/x-apple-aspen-config" },
      });
    }

    if (parts[0] === "result" && parts[1]) {
      const stored = await env.UDID_KV.get("udid:" + parts[1]);
      if (!stored) return new Response(JSON.stringify({ status: "waiting" }),
        { headers: { "content-type": "application/json" } });
      await env.UDID_KV.delete("udid:" + parts[1]);      // read once, then gone
      await env.UDID_KV.delete("session:" + parts[1]);
      return new Response(stored, { headers: { "content-type": "application/json" } });
    }

    return page("BariSign", "<p>Nothing here. Open the link you were given.</p>");
  },
};
