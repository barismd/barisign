#!/usr/bin/env python3
# =============================================================================
#  BariSign — read ANOTHER device's UDID with nothing installed on it
#  MIT licence.  STATUS: UNTESTED on a device.
# =============================================================================
#
#  WHAT THIS IS FOR
#  ----------------
#  To install a signed app on a second device, that device's UDID must be in the
#  provisioning profile. This runs on the SIGNER (Device A, the one with a-Shell)
#  and asks Device B for its UDID using the only route we found that needs NO app
#  at all on Device B: Apple's "Profile Service" configuration profile.
#
#  HOW IT WORKS
#  ------------
#    1. This script listens on your Wi-Fi address and shows a QR code.
#    2. Device B's Camera scans the QR -> opens the page in Safari.
#    3. Device B downloads a temporary profile and installs it
#       (Settings -> General -> VPN & Device Management).
#    4. Device B posts its UDID back here. The QR/URL is all Device B needs:
#       no a-Shell, nothing installed. Only Safari and the Settings app.
#    5. Delete the profile on Device B afterwards.
#
#  If step 3 never happens on Device B, iOS 27 no longer honours the
#  "Profile Service / DeviceAttributes" mechanism, and this route is dead.
#  That is the ONE thing this script exists to find out. Nothing else is claimed.
#
#  PRIVACY: the UDID travels over your own Wi-Fi to this device only. It is
#  printed and never written to a file and never sent anywhere else.
#
#  RUN (on Device A, in a-Shell):
#      python3 -u udid_share.py
#  QR display needs a QR library; if it is missing the script prints the URL:
#      python3 -m pip install segno     # pure Python, or:  pip install qrcode
# =============================================================================

import http.server
import re
import socket
import sys
import threading
import urllib.parse
import uuid
import plistlib

PORT = 8787
TIMEOUT_SECONDS = 600
UDID_RE = re.compile(rb"<key>UDID</key>\s*<string>([^<]+)</string>")

found = threading.Event()
state = {"udid": None}


def lan_ip():
    """The address Device B can reach. Sends no traffic."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:                                          # noqa: BLE001
        return "127.0.0.1"
    finally:
        s.close()


def udid_profile(callback_url):
    """The temporary profile that makes Device B report its UDID."""
    payload = {
        "PayloadContent": {
            "URL": callback_url,
            "DeviceAttributes": ["UDID", "PRODUCT", "VERSION", "DEVICE_NAME"],
        },
        "PayloadType": "Profile Service",
        "PayloadVersion": 1,
        "PayloadIdentifier": "io.barisign.udid",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadDisplayName": "BariSign - read this device's UDID",
        "PayloadDescription":
            "Temporary. Sends this device's UDID to the BariSign script on your own "
            "Wi-Fi. Delete this profile afterwards.",
        "PayloadOrganization": "BariSign",
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML)


def empty_profile():
    """What the profile service expects to receive back."""
    return plistlib.dumps({
        "PayloadContent": [],
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": "io.barisign.udid.done",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadDisplayName": "BariSign - done",
        "PayloadDescription": "Nothing to install. Safe to delete.",
        "PayloadOrganization": "BariSign",
    }, fmt=plistlib.FMT_XML)


PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>BariSign</title><style>body{font:17px -apple-system,sans-serif;margin:0;
padding:2rem 1.25rem;background:#111;color:#eee}a.btn{display:block;text-align:center;
background:#2ea44f;color:#fff;text-decoration:none;padding:1rem;border-radius:14px;
font-weight:600;margin:1.5rem 0}p{line-height:1.5;color:#bbb}</style>
<h1>%s</h1>%s"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "BariSign"

    def log_message(self, *args):
        pass

    def _send(self, body, ctype, status=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/udid.mobileconfig":
            self._send(state["profile"], "application/x-apple-aspen-config")
        elif path == "/":
            base = state["base"]
            self._send(PAGE % ("Step 1 of 2", (
                '<a class=btn href="/udid.mobileconfig">Download the UDID profile</a>'
                "<p>Then install it under <b>Settings &rarr; General &rarr; "
                "VPN&nbsp;&amp;&nbsp;Device Management</b>, and tap <b>Allow</b> when asked "
                "to send the device information.</p>"
                "<p>Nothing else is installed.</p>"
                "<p><small>%s</small></p>" % base)), "text/html; charset=utf-8")
        else:
            self._send("not found", "text/plain", status=404)

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path != "/udid":
            self._send("not found", "text/plain", status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        match = UDID_RE.search(body)
        if match:
            state["udid"] = match.group(1).decode("utf-8", "replace")
            found.set()
            self._send(empty_profile(), "application/x-apple-aspen-config")
        else:
            self._send("no UDID in the request", "text/plain", status=400)

    def do_PUT(self):
        self.do_POST()


def show_qr(url):
    for module, render in (("segno", lambda m, u: m.make(u).terminal(compact=True)),
                           ("qrcode", lambda m, u: m.QRCode(border=1).print_ascii())):
        try:
            mod = __import__(module)
        except ImportError:
            continue
        try:
            if module == "qrcode":
                qr = mod.QRCode(border=1)
                qr.add_data(url)
                qr.make(fit=True)
                qr.print_ascii()
            else:
                render(mod, url)
            return True
        except Exception:                                      # noqa: BLE001
            continue
    return False


def main():
    ip = lan_ip()
    base = "http://%s:%d" % (ip, PORT)
    state["base"] = base
    state["profile"] = udid_profile(base + "/udid")

    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError as exc:
        print("Could not listen on port %d: %s" % (PORT, exc))
        print("Another copy may be running. Close it and try again.")
        return 2

    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("=" * 68)
    print(" BariSign - ask another device for its UDID (no app on that device)")
    print("=" * 68)
    print()
    print(" On Device B (the phone/tablet that should get the app):")
    print("   1. Open the Camera and scan this code (or type the address).")
    print("   2. Tap the link, then install the profile:")
    print("      Settings -> General -> VPN & Device Management -> Allow.")
    print("   3. Afterwards DELETE that profile. It is temporary.")
    print()
    print(" Address: %s" % base)
    print()
    if not show_qr(base):
        print(" (no QR library found - type the address on Device B instead,")
        print("  or run: python3 -m pip install segno)")
        print()
    print(" Waiting up to %d seconds. Ctrl-C to stop." % TIMEOUT_SECONDS)
    print()
    sys.stdout.flush()

    got = found.wait(TIMEOUT_SECONDS)
    server.shutdown()

    print()
    if not got:
        print("RESULT: no UDID arrived.")
        print("Two possibilities, and they are worth telling apart:")
        print("  * Device B never installed the profile -> try again (same Wi-Fi?).")
        print("  * Device B installed it but iOS 27 did NOT send the UDID ->")
        print("    the Profile Service route is dead on iOS 27 and we must use the")
        print("    Settings-based pairing route instead. Report which one it was.")
        return 1

    udid = state["udid"]
    print("RESULT: UDID received from Device B:")
    print()
    print("    %s" % udid)
    print()
    print("Device B is then done: delete the temporary profile there.")
    print("This UDID is NOT written to any file and was not sent anywhere else.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
