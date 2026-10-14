#!/usr/bin/env python3
# =============================================================================
#  BariSign — read ANOTHER device's UDID, with nothing installed on it
#  MIT licence.  STATUS: UNTESTED on a device.
# =============================================================================
#
#  Runs on the SIGNER (Device A, the one with a-Shell) and asks Device B for its
#  UDID using Apple's "Profile Service" configuration profile — the only route we
#  found that needs NO app on Device B (just Safari and Settings).
#
#  Hardening in this version
#  ------------------------
#    * one-time random token in every URL; anything else is a 404
#    * the token is generated fresh on every run and dies with the process
#    * binds ONLY to this device's Wi-Fi/hotspot address, never 0.0.0.0
#    * serves exactly two things: the profile, and the POST endpoint
#    * 5-minute window, then it gives up
#    * stops accepting after the FIRST valid POST; a second POST is refused
#    * request bodies capped, no access log, nothing written to disk
#    * the UDID is NOT printed until you confirm the device model and iOS
#      version are the device sitting in front of you
#
#  PRIVACY: the UDID travels over your own Wi-Fi to this device only.
#
#  RUN (Device A, in a-Shell):
#      python3 -u udid_share.py
#  A QR needs a QR library; without one it prints the URL to type instead:
#      python3 -m pip install segno
# =============================================================================

import http.server
import plistlib
import re
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import uuid

PORT_HINT = 8787
WINDOW_SECONDS = 300          # 5 minutes
MAX_BODY = 256 * 1024
TOKEN = secrets.token_urlsafe(12)
PREFIX = "/" + TOKEN

state = {"result": None, "claimed": False}
posted = threading.Event()


# ---------------------------------------------------------------------------
# The profile
# ---------------------------------------------------------------------------

def udid_profile(callback_url):
    """ONLY a Profile Service payload. No certificates, no VPN, no proxy, no
    Wi-Fi, nothing else. It asks for three attributes and nothing more."""
    return {
        "PayloadContent": {
            "URL": callback_url,
            "DeviceAttributes": ["UDID", "PRODUCT", "VERSION"],
        },
        "PayloadType": "Profile Service",
        "PayloadVersion": 1,
        "PayloadIdentifier": "io.barisign.udid",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadDisplayName": "BariSign - read this device's UDID",
        "PayloadDescription":
            "Temporary and unsigned. Sends this device's UDID, model and iOS version to "
            "the BariSign script on your own Wi-Fi. Nothing else. Delete it afterwards.",
        "PayloadOrganization": "BariSign",
    }


def done_profile():
    """Returned to the device once it has sent its details. An empty configuration
    profile, so nothing is configured and nothing new is installed."""
    return {
        "PayloadContent": [],
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": "io.barisign.udid.done",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadDisplayName": "BariSign - done (delete me)",
        "PayloadDescription": "Nothing to install. Safe to delete.",
        "PayloadOrganization": "BariSign",
    }


def as_xml(profile):
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML)


# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

def local_address():
    """This device's Wi-Fi/hotspot address. Sends no traffic."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def extract(body, key):
    """Pull <key>K</key><string>V</string> out of a (possibly CMS-wrapped) plist."""
    pattern = rb"<key>%s</key>\s*<(string|data)>([^<]*)</\1>" % key.encode()
    match = re.search(pattern, body)
    return match.group(2).decode("utf-8", "replace") if match else None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "BariSign"

    def log_message(self, *args):
        pass                                            # never log anything

    def _send(self, body, ctype, status=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == PREFIX + "/udid.mobileconfig":
            self._send(state["profile_xml"], "application/x-apple-aspen-config")
        else:
            self._send("not found", "text/plain", status=404)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path != PREFIX + "/udid":
            self._send("not found", "text/plain", status=404)
            return
        if state["claimed"]:
            self._send("already used", "text/plain", status=409)   # refuse a 2nd POST
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._send("bad request", "text/plain", status=400)
            return
        body = self.rfile.read(length)
        udid = extract(body, "UDID")
        if not udid:
            self._send("no UDID in the request", "text/plain", status=400)
            return
        state["claimed"] = True
        state["result"] = {
            "udid": udid,
            "product": extract(body, "PRODUCT") or "(not sent)",
            "version": extract(body, "VERSION") or "(not sent)",
        }
        self._send(as_xml(done_profile()), "application/x-apple-aspen-config")
        posted.set()


def show_qr(url):
    try:
        import segno                                        # noqa: PLC0415
        segno.make(url, error="m").terminal(compact=True)
        return True
    except Exception:                                       # noqa: BLE001
        pass
    try:
        import qrcode                                       # noqa: PLC0415
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii()
        return True
    except Exception:                                       # noqa: BLE001
        return False


def ask_confirm(prompt):
    """Plain input(): no masked reads here, so a-Shell's terminal stays normal."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        return input("").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return ""


def main():
    try:
        ip = local_address()
    except Exception as exc:                                # noqa: BLE001
        print("Could not work out this device's Wi-Fi address: %r" % (exc,))
        print("Connect to Wi-Fi (or a Personal Hotspot) and try again.")
        return 2
    if ip.startswith("127."):
        print("No Wi-Fi/hotspot address found. Refusing to listen on every")
        print("interface. Connect to Wi-Fi and try again.")
        return 2

    # Ephemeral port: avoids clashes and makes the URL meaningless to guess.
    server = None
    for port in [PORT_HINT] + [0]:
        try:
            server = http.server.ThreadingHTTPServer((ip, port), Handler)
            break
        except OSError:
            continue
    if server is None:
        print("Could not open a listening socket on %s." % ip)
        return 2

    port = server.server_address[1]
    base = "http://%s:%d%s" % (ip, port, PREFIX)
    profile_url = base + "/udid.mobileconfig"
    state["profile_xml"] = as_xml(udid_profile(base + "/udid"))

    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("=" * 70)
    print(" BariSign - ask the other device for its UDID")
    print("=" * 70)
    print()
    print(" What Device B will install (this is the WHOLE profile, nothing else):")
    print()
    for line in state["profile_xml"].decode().splitlines():
        print("   " + line)
    print()
    print(" Notice: one payload, type 'Profile Service', asking only for UDID,")
    print(" PRODUCT and VERSION. No certificate, no VPN, no proxy, no Wi-Fi.")
    print()
    print(" Listening only on %s, for %d seconds, with a one-time path." % (ip, WINDOW_SECONDS))
    print(" Device A: keep a-Shell in the FOREGROUND and the screen awake.")
    print()
    print(" Device B: scan this, then follow the prompts.")
    print()
    if not show_qr(profile_url):
        print(" (no QR library - type this address on Device B instead:")
        print("  %s )" % profile_url)
        print("  (or run: python3 -m pip install segno)")
        print()
    print(" Address: %s" % profile_url)
    print()
    sys.stdout.flush()

    started = time.time()
    got = posted.wait(WINDOW_SECONDS)
    try:
        server.shutdown()
        server.server_close()
    except Exception:                                       # noqa: BLE001
        pass
    print()
    print(" Server closed after %.0f s." % (time.time() - started))
    print()

    if not got:
        print("RESULT: no UDID arrived within %d seconds." % WINDOW_SECONDS)
        print("Tell apart which happened on Device B:")
        print("  A) the profile was never installed        -> try again, same Wi-Fi")
        print("  B) it installed but nothing was sent      -> iOS 27 dropped the")
        print("                                               Profile Service UDID, and")
        print("                                               this route is dead")
        return 1

    result = state["result"]
    print("A device sent its details. Before the UDID is shown, confirm it is")
    print("the device in front of you (another device on the same Wi-Fi could")
    print("have posted instead):")
    print()
    print("    model (PRODUCT): %s" % result["product"])
    print("    iOS  (VERSION) : %s" % result["version"])
    print("    UDID           : hidden until you confirm")
    print()
    answer = ask_confirm("Is that the device in front of you? [y/N] ")
    print()
    if answer not in ("y", "yes"):
        state["result"]["udid"] = None
        print("RESULT: rejected, and the UDID was discarded. Nothing was kept.")
        return 3

    print("RESULT: UDID received from Device B:")
    print()
    print("    %s" % result["udid"])
    print()
    print("This was printed once and written to no file and sent nowhere else.")
    print("On Device B: delete the BariSign profile(s), see the notes.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
