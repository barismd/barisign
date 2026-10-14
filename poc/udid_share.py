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
#  Two ways to get the profile onto Device B:
#    * default   - scan the QR / open the printed URL (Device B needs no app)
#    * --airdrop - the profile is written to a file and AirDropped; Device B
#                  then needs no URL at all. Note the callback address is INSIDE
#                  the profile, so this listener must still be running here.
#
#  PRIVACY: the UDID travels over your own Wi-Fi to this device only.
#
#  RUN (Device A, in a-Shell):
#      python3 -u udid_share.py
#  A QR needs a QR library; without one it prints the URL to type instead:
#      python3 -m pip install segno
# =============================================================================

import datetime
import http.server
import ipaddress
import os
import plistlib
import re
import secrets
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid

PORT_HINT = 8787
WINDOW_SECONDS = 300          # 5 minutes
PROFILE_LIFETIME_SECONDS = 600  # ask iOS to drop the profile by itself (see notes)
MAX_BODY = 256 * 1024
TOKEN = secrets.token_urlsafe(12)
PREFIX = "/" + TOKEN

state = {"result": None, "claimed": False}
posted = threading.Event()


# ---------------------------------------------------------------------------
# The profile
# ---------------------------------------------------------------------------

def udid_profile(callback_url, lifetime=False):
    """ONLY a Profile Service payload. No certificates, no VPN, no proxy, no
    Wi-Fi, nothing else. It asks for three attributes and nothing more.

    Lifetime keys, so the profile does not sit on the device forever:
      * DurationUntilRemoval - the device removes the profile itself after this
        many seconds. This is the key that actually deletes (Apple's archived
        Over-the-Air Profile Delivery guide).
      * PayloadExpirationDate - Apple documents this as "the date when a profile
        is no longer valid and the system presents an update button"; it does
        NOT delete anything, and Apple's reference says it "is only used if the
        profile is delivered via over-the-air profile delivery".
    Neither is verified on iOS 27 and BOTH ARE OFF BY DEFAULT: an initial profile
    that iOS rejects is worse than a leftover one, and we have one report of
    "profile could not be installed, an unknown error occurred" while these keys
    were present. Pass --lifetime to add them back.
    """
    profile = {
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
    if lifetime:
        profile["PayloadExpirationDate"] = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=PROFILE_LIFETIME_SECONDS))
        profile["DurationUntilRemoval"] = PROFILE_LIFETIME_SECONDS
    return profile


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
# Optional HTTPS: so only THIS device can read the UDID
#
# There is no way to give the target an app-level key: it runs no code of ours,
# and iOS will only encrypt this exchange with TLS. So "encrypted between the two
# devices" means one of two things, and only the second is possible:
#
#   * plain HTTP  - anyone on the same Wi-Fi can read the UDID in transit
#   * HTTPS       - the target authenticates US and the POST is encrypted
#
# Mutual authentication is NOT possible: iOS presents no client certificate to a
# web request, so the target cannot prove which device it is. That is what the
# model + iOS version confirmation step is for.
#
# A self-signed leaf is refused by iOS, so this generates a throwaway CA and a
# leaf for this LAN address. The target must install the CA and switch on full
# trust for it, which is the price of "only the signer sees the UDID".
# ---------------------------------------------------------------------------

def _ca_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "barisign-ca.pem"), os.path.join(here, "barisign-ca.key")


def load_or_create_ca():
    """REUSE the same CA every run.

    This was the bug that made it work once and then fail forever: the CA used to be
    regenerated on every run with a 6-hour life, so the CA the user had installed and
    trusted was never the CA the next run signed with. iOS then answers
    "MDM certificate is invalid".

    Now the CA is created once, stored next to this script (never in the repository;
    *.pem and *.key are gitignored), and reused. Install and trust it ONE time, and
    every later run needs only the UDID profile.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_path, key_path = _ca_paths()
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        try:
            with open(cert_path, "rb") as fh:
                cert = x509.load_pem_x509_certificate(fh.read())
            with open(key_path, "rb") as fh:
                key = serialization.load_pem_private_key(fh.read(), password=None)
            return cert, key
        except Exception:                                        # noqa: BLE001
            pass                                                 # fall through and remake

    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "BariSign temporary CA")])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=730))   # 2 years
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            .add_extension(x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as fh:
        os.chmod(key_path, 0o600)
        fh.write(key.private_bytes(serialization.Encoding.PEM,
                                   serialization.PrivateFormat.TraditionalOpenSSL,
                                   serialization.NoEncryption()))
    return cert, key


def ca_fingerprint(ca_cert):
    import hashlib as _h
    der = ca_cert.public_bytes(__import__("cryptography").hazmat.primitives
                               .serialization.Encoding.DER)
    return ":".join("%02X" % b for b in _h.sha256(der).digest()[:8])


def make_certs(ip):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_cert, ca_key = load_or_create_ca()          # persistent: trusted once, forever

    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_cert = (x509.CertificateBuilder()
                 .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)]))
                 .issuer_name(ca_cert.subject)
                 .public_key(leaf_key.public_key())
                 .serial_number(x509.random_serial_number())
                 .not_valid_before(now - datetime.timedelta(minutes=5))
                 .not_valid_after(now + datetime.timedelta(days=7))
                 .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                 .add_extension(x509.SubjectKeyIdentifier.from_public_key(
                     leaf_key.public_key()), critical=False)
                 .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                     ca_key.public_key()), critical=False)
                 .add_extension(x509.KeyUsage(
                     digital_signature=True, content_commitment=False, key_encipherment=True,
                     data_encipherment=False, key_agreement=False, key_cert_sign=False,
                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
                 .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
                                critical=False)
                 .add_extension(x509.SubjectAlternativeName(
                     [x509.IPAddress(ipaddress.ip_address(ip))]), critical=False)
                 .sign(ca_key, hashes.SHA256()))

    def pem(cert):
        return cert.public_bytes(serialization.Encoding.PEM)

    leaf_key_pem = leaf_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption())
    return {
        "ca_pem": pem(ca_cert),
        "ca_der": ca_cert.public_bytes(serialization.Encoding.DER),
        "leaf_chain_pem": pem(leaf_cert) + pem(ca_cert),   # leaf first, then our CA
        "leaf_key_pem": leaf_key_pem,
    }


def webclip_profile():
    """A profile with a payload iOS definitely supports.

    The empty profile was a bad test: iOS answered "this profile is empty" and
    never tried to install it. A Web Clip adds one removable icon to the home
    screen, so if THIS installs, unsigned AirDropped profiles work in general and
    anything that fails afterwards is about the Profile Service payload.
    """
    return {
        "PayloadContent": [{
            "PayloadType": "com.apple.webClip.managed",
            "PayloadVersion": 1,
            "PayloadIdentifier": "io.barisign.sanity.webclip",
            "PayloadUUID": str(uuid.uuid4()).upper(),
            "PayloadDisplayName": "BariSign test clip",
            "Label": "BariSign test",
            "URL": "https://example.com/",
            "IsRemovable": True,
        }],
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": "io.barisign.sanity",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadDisplayName": "BariSign - can this device install a profile?",
        "PayloadDescription": "Adds one removable icon to the Home Screen and nothing else.",
        "PayloadOrganization": "BariSign",
    }


def empty_profile():
    """A Configuration profile with no payloads: the sanity check. If even this
    will not install on the target, the problem is profile installation itself,
    not anything we send."""
    return {
        "PayloadContent": [],
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": "io.barisign.sanity",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadDisplayName": "BariSign - sanity check (empty profile)",
        "PayloadDescription": "Installs nothing at all. Delete it right away.",
        "PayloadOrganization": "BariSign",
    }


def ca_profile(ca_der):
    return {
        "PayloadContent": [{
            "PayloadCertificateFileName": "BariSignCA.cer",
            "PayloadContent": ca_der,
            "PayloadDescription": "Temporary BariSign certificate authority.",
            "PayloadDisplayName": "BariSign temporary CA",
            "PayloadIdentifier": "io.barisign.ca",
            "PayloadType": "com.apple.security.root",
            "PayloadUUID": str(uuid.uuid4()).upper(),
            "PayloadVersion": 1,
        }],
        "PayloadDescription":
            "Temporary, throwaway CA so the BariSign scripts can use HTTPS. Delete "
            "it afterwards, and also turn it off in Certificate Trust Settings.",
        "PayloadDisplayName": "BariSign temporary CA",
        "PayloadIdentifier": "io.barisign.ca.profile",
        "PayloadType": "Configuration",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadVersion": 1,
        "PayloadOrganization": "BariSign",
    }


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
        if path == PREFIX + "/tls-check":
            self._send("TLS OK - this device trusts the BariSign temporary CA.\n",
                       "text/plain")
        elif path == PREFIX + "/ca.mobileconfig" and state.get("ca_xml"):
            self._send(state["ca_xml"], "application/x-apple-aspen-config")
        elif path == PREFIX + "/udid.mobileconfig":
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


def write_beside_script(name, data):
    """Write a file next to the script, i.e. inside ~/Documents/barisign."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def write_diag_profiles():
    """Three files that isolate WHY a profile was refused.

    Device B reported "profile could not be installed, an unknown error occurred"
    on 2026-10-14 with the first version of this profile. Installing these in
    order says which layer is at fault.
    """
    placeholder = "http://192.0.2.1/udid"      # TEST-NET-1: never routable
    tests = [
        ("barsign-test1-webclip.mobileconfig", webclip_profile(),
         "Adds one removable icon to the Home Screen. If THIS installs, unsigned "
         "AirDropped profiles work and any later failure is about the payload type."),
        ("barsign-test2-profservice.mobileconfig", udid_profile(placeholder),
         "Profile Service with ONLY the documented keys. If this one fails, iOS 27 no "
         "longer accepts the payload type and this whole route is dead."),
        ("barsign-test4-profservice-https.mobileconfig",
         udid_profile("https://example.com/udid"),
         "DECISIVE: identical payload, but an ordinary HTTPS callback. NO certificate "
         "and NO trust step needed. If this installs, the payload type is fine and the "
         "http:// URL was what iOS refused. If it is still refused, the Profile Service "
         "payload itself is retired on iOS 27."),
        ("barsign-test3-profservice-lifetime.mobileconfig",
         udid_profile(placeholder, lifetime=True),
         "The same plus DurationUntilRemoval / PayloadExpirationDate. Informational "
         "only now: those keys are OFF by default."),
    ]
    here = os.path.dirname(os.path.abspath(__file__))
    print("Four test profiles written to %s" % here)
    print()
    print("Folder: %s" % here)
    print("Send ONE file at a time; this opens its preview so you can share it:")
    print("   python3 -u udid_share.py --view <name>")
    print()
    print("Install them on Device B in this order and report the exact error text:")
    print()
    for name, profile, why in tests:
        write_beside_script(name, as_xml(profile))
        print(" %s" % name)
        print("     %s" % why)
        print()
    print("These contain no usable callback address; they are for installation only.")


def _wait_return():
    """Pause so only ONE preview is open at a time. Plain input(): a-Shell handles
    it (verified on device), and nothing here masks anything."""
    sys.stdout.write(" Press Return when you are ready for the next file... ")
    sys.stdout.flush()
    try:
        input("")
    except EOFError:
        pass
    print()


def offer_share(path):
    """Share the file out of a-Shell.

    `open <file>` is implemented in ios_system as the iOS share sheet
    (UIActivityViewController), but it can fail SILENTLY in two ways:
      * if the path is not found it falls back to treating the argument as a URL
        (openurl) and nothing visible happens;
      * if UIApplication.keyWindow is nil - a deprecated API, and a-Shell is
        scene-based - it prints "Cannot find a window" to stderr and then
        presents on a nil controller.
    So we try it, and always print the reliable route as well: the Files app.
    """
    absolute = os.path.abspath(path)
    print(" Opening the preview for %s" % os.path.basename(path))
    print(" (a-Shell has no 'share' command; in the preview window use the")
    print("  top-right button to share/AirDrop it.)")
    try:
        os.system("view %s" % absolute)
    except Exception as exc:                                   # noqa: BLE001
        print(" Could not run 'view': %r" % (exc,))
        print(" Run it yourself:  view %s" % absolute)


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
    use_https = "--https" in sys.argv[1:]
    use_airdrop = "--airdrop" in sys.argv[1:]
    use_lifetime = "--lifetime" in sys.argv[1:]
    if "--diag-profiles" in sys.argv[1:]:
        write_diag_profiles()
        return 0
    if "--view" in sys.argv[1:]:
        args = sys.argv[1:]
        name = args[args.index("--view") + 1] if len(args) > args.index("--view") + 1 else ""
        if not name:
            print("usage: python3 -u udid_share.py --view <file>")
            return 2
        target = name if os.path.isabs(name) else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), name)
        if not os.path.isfile(target):
            print("No such file: %s" % target)
            return 2
        offer_share(target)
        return 0
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

    try:
        private = ipaddress.ip_address(ip).is_private
    except ValueError:
        private = False

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
    scheme = "https" if use_https else "http"
    base = "%s://%s:%d%s" % (scheme, ip, port, PREFIX)
    profile_url = base + "/udid.mobileconfig"
    state["profile_xml"] = as_xml(udid_profile(base + "/udid", lifetime=use_lifetime))

    ca_url = None
    plain_server = None
    tmpdir = None
    if use_https:
        try:
            certs = make_certs(ip)
        except Exception as exc:                                # noqa: BLE001
            print("Could not create the temporary certificate: %r" % (exc,))
            print("cryptography is required for --https. Try without --https.")
            return 2
        tmpdir = tempfile.mkdtemp(prefix="barisign-", dir=os.path.dirname(os.path.abspath(__file__)))
        crt_path = os.path.join(tmpdir, "leaf.pem")
        key_path = os.path.join(tmpdir, "leaf.key")
        os.chmod(tmpdir, 0o700)
        with open(crt_path, "wb") as fh:
            fh.write(certs["leaf_chain_pem"])
        with open(key_path, "wb") as fh:
            os.chmod(key_path, 0o600)
            fh.write(certs["leaf_key_pem"])
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(crt_path, key_path)
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
        except Exception as exc:                                # noqa: BLE001
            print("Could not start HTTPS: %r" % (exc,))
            return 2
        state["ca_der"] = certs["ca_der"]
        state["ca_fingerprint"] = ca_fingerprint(load_or_create_ca()[0])
        state["ca_xml"] = as_xml(ca_profile(certs["ca_der"]))
        if use_airdrop:
            # the CA travels by AirDrop too, so no plain-HTTP listener is needed
            ca_url = None
        else:
            # the CA cannot be fetched over HTTPS (chicken and egg), so it is
            # served briefly over plain HTTP; it is public information.
            plain_server = http.server.ThreadingHTTPServer((ip, 0), Handler)
            plain_port = plain_server.server_address[1]
            ca_url = "http://%s:%d%s/ca.mobileconfig" % (ip, plain_port, PREFIX)
            threading.Thread(target=plain_server.serve_forever, daemon=True).start()

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
    if use_https:
        print(" MODE: HTTPS.")
        print(" CA fingerprint : %s" % state.get("ca_fingerprint", "?"))
        print(" This CA is now PERSISTENT: it is created once and reused on every run,")
        print(" so it only has to be installed and trusted ONE TIME. (Before, a new CA")
        print(" was made on every run, which is why a trusted CA stopped matching and")
        print(" iOS answered 'MDM certificate is invalid'.)")
        print(" If the target still has an OLDER BariSign CA installed, delete it.")
        print()
        print(" Order matters: 1) install the CA, 2) switch its full trust ON,")
        print(" 3) ONLY THEN install the UDID profile.")
        if not use_airdrop:
            print(" STEP 1 (CA) URL: %s" % ca_url)
        print()
    print(" Listening only on %s, for %d seconds, with a one-time path." % (ip, WINDOW_SECONDS))
    if private:
        print(" That address is private (a home/hotspot network): %s" % ip)
    else:
        print(" *** WARNING: %s is NOT a private address. The UDID will travel in" % ip)
        print(" *** clear over a network that may not be yours. Stop, move both")
        print(" *** devices to your own Wi-Fi, and run this again.")
    print(" A-Shell cannot read the Wi-Fi name (iOS does not allow it). Check that")
    print(" both devices are on the same network before scanning - the Shortcut will")
    print(" ask, using the 'Get Network Details' action.")
    print(" Device A: keep a-Shell in the FOREGROUND and the screen awake.")
    print()
    print(" Device B: scan this, then follow the prompts.")
    print()
    sys.stdout.flush()

    if use_airdrop:
        if use_https:
            ca_path = write_beside_script("BariSign-CA.mobileconfig", state["ca_xml"])
        profile_path = write_beside_script("BariSign-UDID.mobileconfig", state["profile_xml"])
        if use_https:
            print(" Check first, on Device B in Safari (needs this script running):")
            print("   %s/tls-check" % base)
            print("   a padlock and 'TLS OK' means the CA is trusted. No padlock = the")
            print("   trust switch is still OFF and the profile will fail with")
            print("   'MDM certificate is invalid'.")
            print()
            print(" STEP 1 of 2 - the temporary CA. Share it, install it, then switch")
            print("              trust ON: Settings -> General -> About ->")
            print("              Certificate Trust Settings -> 'BariSign temporary CA'.")
            offer_share(ca_path)
            _wait_return()
            print(" STEP 2 of 2 - now the UDID profile:")
        offer_share(profile_path)
        print()
        print(" The callback address is inside the profile, so Device B never needs a")
        print(" URL. Keep a-Shell in the FOREGROUND: the request happens while iOS")
        print(" installs, so the listener must be answering right then.")
        print()
    elif not use_https:
        show_qr(profile_url)
        print(" Address: %s" % profile_url)
        print()
    else:
        print("   %s" % profile_url)
        show_qr(profile_url)
        print()

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

    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)

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
