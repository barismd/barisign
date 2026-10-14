#!/usr/bin/env python3
# =============================================================================
#  BariSign — read another device's UDID, with nothing installed on it
#  MIT licence.
#
#  Verified working on device (iPhone 13 + iPad, iOS 27).
#
#  WHAT IT DOES
#  ------------
#  A free Apple ID signs apps for a specific device, so that device's UDID must be
#  known. iOS reports it from a "Profile Service" configuration profile, but only
#  over HTTPS with a certificate iOS trusts. This script serves exactly that, from
#  this device, over the local network — no third party, no DNS, no account.
#
#      Device A (this one)                    Device B (nothing installed)
#      serves HTTPS with a throwaway CA  ───► Safari opens the profile, Install
#      iOS posts the UDID back  ◄────────────┘   (never leaves the Wi-Fi)
#      confirms the model, prints the UDID
#
#  USAGE
#  -----
#      python3 -u udid_share.py                  first time: install the CA, trust it,
#                                                then install the UDID profile
#      python3 -u udid_share.py --udid-only      later runs: one profile, nothing else
#      python3 -u udid_share.py --view <file>    just open a preview of a file
#
#  The CA is created once and reused, so it only has to be installed and trusted
#  ONCE per device. iOS will call the UDID profile "empty" — that is expected: a
#  Profile Service profile configures nothing, and the request has already been sent.
# =============================================================================

import datetime
import hashlib
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
WINDOW_SECONDS = 300            # how long to wait for the other device
PROFILE_LIFETIME_SECONDS = 600  # ask iOS to delete the profile by itself
MAX_BODY = 256 * 1024
TOKEN = secrets.token_urlsafe(12)
PREFIX = "/" + TOKEN

CA_CERT_NAME = "barisign-ca.pem"
CA_KEY_NAME = "barisign-ca.key"

state = {"claimed": False, "result": None}
posted = threading.Event()

# Set when a UDID is received, so a caller (poc/barisign.py) can read it without
# scraping the printed output.
LAST_UDID = None


# ---------------------------------------------------------------------------
# The profiles we hand over
# ---------------------------------------------------------------------------

def udid_profile(callback_url):
    """The whole request: one payload, three identifiers, nothing else."""
    return {
        "PayloadContent": {
            "URL": callback_url,
            "DeviceAttributes": ["UDID", "PRODUCT", "VERSION", "SERIAL"],
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
        "DurationUntilRemoval": PROFILE_LIFETIME_SECONDS,
    }


def done_profile():
    """What iOS expects back from the collection endpoint. Installs nothing."""
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


def ca_profile(ca_der, name_suffix):
    """The throwaway CA, named after its own fingerprint so the right entry gets
    trusted in Settings (older runs left several identically-named CAs behind)."""
    return {
        "PayloadContent": [{
            "PayloadCertificateFileName": "BariSignCA.cer",
            "PayloadContent": ca_der,
            "PayloadDescription": "Temporary BariSign certificate authority.",
            "PayloadDisplayName": "BariSign CA %s" % name_suffix,
            "PayloadIdentifier": "io.barisign.ca",
            "PayloadType": "com.apple.security.root",
            "PayloadUUID": str(uuid.uuid4()).upper(),
            "PayloadVersion": 1,
        }],
        "PayloadDescription":
            "Temporary, throwaway CA for the BariSign scripts. Delete it afterwards, and "
            "switch its trust off in Certificate Trust Settings.",
        "PayloadDisplayName": "BariSign CA %s" % name_suffix,
        "PayloadIdentifier": "io.barisign.ca.profile",
        "PayloadType": "Configuration",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadVersion": 1,
        "PayloadOrganization": "BariSign",
    }


def as_xml(profile):
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML)


# ---------------------------------------------------------------------------
# The certificate authority (persistent) and the per-run server certificate
# ---------------------------------------------------------------------------

def _ca_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, CA_CERT_NAME), os.path.join(here, CA_KEY_NAME)


def load_or_create_ca():
    """One CA, reused on every run.

    A per-run CA was the bug that made this work once and then never again: the CA
    installed and trusted on the device was never the CA the next run signed with,
    so iOS answered "MDM certificate is invalid".
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_path, key_path = _ca_paths()
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        try:
            with open(cert_path, "rb") as handle:
                cert = x509.load_pem_x509_certificate(handle.read())
            with open(key_path, "rb") as handle:
                key = serialization.load_pem_private_key(handle.read(), password=None)
            return cert, key
        except Exception:                                      # noqa: BLE001
            pass                                               # remake below

    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "BariSign temporary CA")])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=730))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            .add_extension(x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))
    with open(cert_path, "wb") as handle:
        handle.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as handle:
        os.chmod(key_path, 0o600)
        handle.write(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()))
    return cert, key


def make_server_certificate(ip):
    """A short-lived certificate for this LAN address, signed by the persistent CA.

    Served to the device as the LEAF ONLY: a TLS server must not send the root, and
    a chain carrying a self-signed root can be refused by strict validators.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    ca_cert, ca_key = load_or_create_ca()
    now = datetime.datetime.now(datetime.timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
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
    return {
        "ca_pem": ca_cert.public_bytes(serialization.Encoding.PEM),
        "ca_der": ca_cert.public_bytes(serialization.Encoding.DER),
        "ca_name": ":".join("%02X" % byte for byte in
                            hashlib.sha256(ca_cert.public_bytes(
                                serialization.Encoding.DER)).digest()[:4]),
        "leaf_pem": cert.public_bytes(serialization.Encoding.PEM),
        "leaf_key_pem": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()),
    }


# ---------------------------------------------------------------------------
# Networking and the two routes
# ---------------------------------------------------------------------------

def local_address():
    """This device's Wi-Fi/hotspot address. Sends no traffic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    finally:
        sock.close()


def extract(body, key):
    match = re.search(rb"<key>%s</key>\s*<(string|data)>([^<]*)</\1>" % key.encode(), body)
    return match.group(2).decode("utf-8", "replace") if match else None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "BariSign"

    def log_message(self, *args):                              # never log anything
        pass

    def _send(self, body, content_type, status=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == PREFIX + "/tls-check":
            self._send("TLS OK - this device trusts the BariSign CA.\n", "text/plain")
        elif path == PREFIX + "/udid.mobileconfig":
            self._send(state["profile_xml"], "application/x-apple-aspen-config")
        else:
            self._send("not found", "text/plain", status=404)

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path != PREFIX + "/udid":
            self._send("not found", "text/plain", status=404)
            return
        if state["claimed"]:
            self._send("already used", "text/plain", status=409)
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
            "serial": extract(body, "SERIAL") or "(not sent)",
        }
        self._send(as_xml(done_profile()), "application/x-apple-aspen-config")
        posted.set()


# ---------------------------------------------------------------------------
# Handing the files over
# ---------------------------------------------------------------------------

def write_beside_script(name, data):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def offer_share(path):
    """`view` is a-Shell's one working share path: a preview window whose top-right
    button shares the file. (`open` is broken - it presents on a deprecated
    keyWindow and silently does nothing.)"""
    absolute = os.path.abspath(path)
    print(" Opening a preview of %s - use the top-right button to share it."
          % os.path.basename(path))
    try:
        os.system("view %s" % absolute)
    except Exception as exc:                                   # noqa: BLE001
        print(" Could not run 'view' (%r). Run it yourself:  view %s"
              % (exc, os.path.basename(path)))


def wait_for_return():
    sys.stdout.write(" Press Return when the other device is ready for the next file... ")
    sys.stdout.flush()
    try:
        input("")
    except (EOFError, KeyboardInterrupt):
        pass
    print()


def ask_confirm(prompt):
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        return input("").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def open_preview_for(name):
    target = name if os.path.isabs(name) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), name)
    if not os.path.isfile(target):
        print("No such file: %s" % target)
        return 2
    offer_share(target)
    return 0


def main():
    udid_only = "--udid-only" in sys.argv[1:]

    if "--view" in sys.argv[1:]:
        args = sys.argv[1:]
        index = args.index("--view")
        if len(args) <= index + 1:
            print("usage: python3 -u udid_share.py --view <file>")
            return 2
        return open_preview_for(args[index + 1])

    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__)
        return 0

    try:
        ip = local_address()
    except Exception as exc:                                   # noqa: BLE001
        print("Could not work out this device's Wi-Fi address: %r" % (exc,))
        return 2
    if ip.startswith("127."):
        print("No Wi-Fi/hotspot address found. Connect to Wi-Fi and try again.")
        return 2
    if not ipaddress.ip_address(ip).is_private:
        print("*** %s is not a private address; the UDID would cross a network you may"
              % ip)
        print("*** not control. Move both devices to your own Wi-Fi and run again.")
        return 2

    certificates = make_server_certificate(ip)

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
    base = "https://%s:%d%s" % (ip, port, PREFIX)
    state["profile_xml"] = as_xml(udid_profile(base + "/udid"))

    tmpdir = tempfile.mkdtemp(prefix="barisign-", dir=os.path.dirname(os.path.abspath(__file__)))
    os.chmod(tmpdir, 0o700)
    crt_path = os.path.join(tmpdir, "leaf.pem")
    key_path = os.path.join(tmpdir, "leaf.key")
    with open(crt_path, "wb") as handle:
        handle.write(certificates["leaf_pem"])
    with open(key_path, "wb") as handle:
        os.chmod(key_path, 0o600)
        handle.write(certificates["leaf_key_pem"])
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(crt_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    except Exception as exc:                                   # noqa: BLE001
        shutil.rmtree(tmpdir, ignore_errors=True)
        print("Could not start HTTPS: %r" % (exc,))
        return 2

    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("=" * 70)
    print(" BariSign - ask the other device for its UDID")
    print("=" * 70)
    print()
    print(" This device serves HTTPS on %s, for %d seconds, with a one-time path."
          % (ip, WINDOW_SECONDS))
    print(" Keep a-Shell in the FOREGROUND and the screen awake: the request is made")
    print(" while iOS installs the profile.")
    print()
    print(" CA name on the device: \"BariSign CA %s\"" % certificates["ca_name"])

    if udid_only:
        print(" Mode: ONE PROFILE. The CA above is assumed installed and trusted; if it")
        print(" is not, run once without --udid-only.")
        print()
        profile_path = write_beside_script("BariSign-UDID.mobileconfig", state["profile_xml"])
        offer_share(profile_path)
    else:
        print(" Do these in order - the order is the whole trick:")
        print("   1. install BariSign-CA.mobileconfig")
        print("   2. Settings > General > About > Certificate Trust Settings -> switch ON")
        print("      the row named \"BariSign CA %s\"" % certificates["ca_name"])
        print("   3. check it: open this in Safari on the other device and look for a")
        print("      padlock and 'TLS OK':   %s/tls-check" % base)
        print("   4. press Return here, then install BariSign-UDID.mobileconfig")
        print("   5. iOS will call the profile empty - that is expected, the UDID has")
        print("      already been sent")
        print()
        ca_path = write_beside_script("BariSign-CA.mobileconfig",
                                      as_xml(ca_profile(certificates["ca_der"],
                                                        certificates["ca_name"])))
        offer_share(ca_path)
        wait_for_return()
        profile_path = write_beside_script("BariSign-UDID.mobileconfig", state["profile_xml"])
        offer_share(profile_path)

    print()
    sys.stdout.flush()
    got = posted.wait(WINDOW_SECONDS)
    for stop in (server.shutdown, server.server_close):
        try:
            stop()
        except Exception:                                      # noqa: BLE001
            pass
    shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    if not got:
        print("RESULT: no UDID arrived within %d seconds." % WINDOW_SECONDS)
        print("Check that: the profile was installed, both devices are on this Wi-Fi,")
        print("and the CA's trust switch is ON (step 2/3 above).")
        return 1

    result = state["result"]
    print("A device sent its details. Confirm it is the device in front of you:")
    print()
    print("    model (PRODUCT): %s" % result["product"])
    print("    iOS  (VERSION) : %s" % result["version"])
    print("    UDID           : hidden until you confirm")
    print()
    if ask_confirm("Is that the device in front of you? [y/N] ") not in ("y", "yes"):
        result["udid"] = None
        print()
        print("RESULT: rejected, and the UDID was discarded.")
        return 3

    print()
    global LAST_UDID
    LAST_UDID = result["udid"]
    print("RESULT: UDID received:")
    print()
    print("    %s" % result["udid"])
    print()
    print("Printed once, written to no file, sent nowhere else.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
