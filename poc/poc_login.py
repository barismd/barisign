#!/usr/bin/env python3
# =============================================================================
#  BariSign — PoC-D : on-device Apple ID sign-in (no computer)
#  MIT licence (this file). It imports the vendored sign-in stack from
#  third_party/sideinstaller/signin.py, which is under a different licence.
# =============================================================================
#
#  RUN IT (on the iPad/iPhone, in a-Shell):
#
#      mkdir -p ~/Documents/barisign && cd ~/Documents/barisign
#      curl -sL <REPO>/third_party/sideinstaller/signin.py   -o signin.py
#      curl -sL <REPO>/poc/poc_login.py                      -o poc_login.py
#      curl -sL <REPO>/third_party/apple-roots/apple-roots.pem -o apple-roots.pem
#      curl -sL <REPO>/third_party/ca-bundle/cacert.pem      -o cacert.pem
#      python3 poc_login.py --selftest
#      python3 poc_login.py --login YOUR-THROWAWAY-APPLE-ID
#
#  NOTE: a-Shell can only write inside ~/Documents. Every file this PoC writes
#  (anisette identity, caches) goes under ~/Documents/barisign. Nothing else.
#
#  WHAT IT DOES
#  -----------
#    * Talks to an anisette server to get the device-identity headers Apple
#      requires. The anisette server receives a random 16-byte identifier we
#      generate and Apple's provisioning blobs — NOT the Apple ID, NOT the
#      password, NOT any token.
#    * Signs in to Apple with SRP-6a. The password is never transmitted (Apple
#      receives a zero-knowledge proof) and never written to disk.
#    * Stops after sign-in. No device registration, no certificate, no profile,
#      no signing, nothing installed.
#
#  STATUS: the six self-test checks were run on Linux during development; the
#          --login path has NOT been run against Apple by BariSign. UNTESTED.
# =============================================================================

import getpass
import os
import re
import socket
import ssl
import sys
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
# Works both when the files sit side by side (the on-device layout) and when
# this is run from a checkout of the repository.
for _candidate in (os.path.join(_HERE, "third_party", "sideinstaller"),
                   os.path.join(_HERE, "..", "third_party", "sideinstaller")):
    if os.path.isfile(os.path.join(_candidate, "signin.py")):
        sys.path.insert(0, _candidate)
        break
sys.path.insert(0, _HERE)

try:
    import signin
    from signin import (Fail, SRP_N, SRP_g, AppleAccount, aes_cbc_decrypt,
                        ask, connect_anisette, request)
except ImportError as exc:                                    # noqa: PERF203
    print("Could not import signin.py: %s" % exc)
    print("Download it next to this file (from the repository):")
    print("  curl -sL <REPO>/third_party/sideinstaller/signin.py -o signin.py")
    sys.exit(2)

# a-Shell only allows writes under ~/Documents; the rest of the home directory
# is read-only. Everything we create lives here.
POC_HOME = os.path.join(os.path.expanduser("~"), "Documents", "barisign")
POC_STATE = os.path.join(POC_HOME, "state")

# Apple's public root CA, as published (see third_party/apple-roots/README.md).
# Pin the SHA-256 so a swapped file cannot be trusted silently.
APPLE_ROOT_FINGERPRINTS = {
    "b0b1730ecbc7ff4505142c49f1295e6eda6bcaed7e2c68c5be91b5a11001f024":
        "Apple Root CA",
}


def _find_file(name):
    for base in (os.path.join(_HERE, "..", "third_party", "apple-roots"),
                 os.path.join(_HERE, "..", "third_party", "ca-bundle"),
                 os.path.join(_HERE, "third_party", "apple-roots"),
                 os.path.join(_HERE, "third_party", "ca-bundle"),
                 POC_HOME, _HERE):
        candidate = os.path.join(base, name)
        if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
            return os.path.abspath(candidate)
    return None


APPLE_ROOTS = _find_file("apple-roots.pem")
if APPLE_ROOTS:
    signin.APPLE_CA_FILE = APPLE_ROOTS


def _select_ca_bundle():
    """Pick a CA bundle.

    a-Shell's Python ships an EMPTY trust store: `ssl.create_default_context()`
    has no roots, so every https:// request fails ('no certificate or crl found'
    / 'self signed certificate in certificate chain'). We therefore load a real
    bundle. We never disable verification.
    """
    try:
        import certifi                                     # noqa: PLC0415
        where = certifi.where()
        if where and os.path.isfile(where):
            return where, "certifi (already on the device)"
    except Exception:                                      # noqa: BLE001
        pass
    shipped = _find_file("cacert.pem")
    if shipped:
        return shipped, "shipped bundle (third_party/ca-bundle/cacert.pem)"
    return None, "none found"


CA_BUNDLE, CA_BUNDLE_SOURCE = _select_ca_bundle()
if CA_BUNDLE:
    signin.set_ca_bundle(CA_BUNDLE)


def _tls_diagnostics():
    paths = ssl.get_default_verify_paths()
    print("default CA file : %s" % (paths.cafile or "(none)"))
    print("default CA dir  : %s" % (paths.capath or "(none)"))
    print("CA bundle used  : %s" % (CA_BUNDLE or "(none)"))
    print("CA bundle source: %s" % CA_BUNDLE_SOURCE)
    try:
        roots = ssl.create_default_context().get_ca_certs()
        print("roots in default context: %d" % len(roots))
    except Exception as exc:                               # noqa: BLE001
        print("roots in default context: unknown (%r)" % (exc,))


def _ensure_dir(path):
    """a-Shell can only write inside ~/Documents. Say so loudly when it cannot."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        print()
        print("COULD NOT CREATE DIRECTORY: %s" % path)
        print("a-Shell only permits writes inside ~/Documents.")
        traceback.print_exc()
        raise SystemExit(2)
    return path


def _state_path():
    return os.path.join(_ensure_dir(POC_STATE), "anisette.json")


def _hr(label):
    print()
    print("---- %s %s" % (label, "-" * max(0, 56 - len(label))))


def _selftest():
    failures = []

    _hr("1/6 runtime")
    print("python : %s" % sys.version.split()[0])
    print("ssl    : %s" % ssl.OPENSSL_VERSION)
    print("platform: %s %s" % (sys.platform, os.uname().machine if hasattr(os, "uname") else "?"))

    _hr("2/6 crypto toolkit (cryptography)")
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        started = time.time()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        keygen = time.time() - started
        csr = (x509.CertificateSigningRequestBuilder()
               .subject_name(x509.Name([
                   x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
                   x509.NameAttribute(NameOID.COMMON_NAME, "CN"),
               ]))
               .sign(key, hashes.SHA256()))
        der = csr.public_bytes(serialization.Encoding.DER)
        sig = key.sign(b"barisign", padding.PKCS1v15(), hashes.SHA256())
        key.public_key().verify(sig, b"barisign", padding.PKCS1v15(), hashes.SHA256())
        print("cryptography : %s" % __import__("cryptography").__version__)
        print("RSA-2048 keygen : %.1fs" % keygen)
        print("CSR (DER)       : %d bytes" % len(der))
        print("PKCS1v15 sign+verify: OK")
        print("SELFTEST 2/6: PASS")
    except Exception as exc:                                  # noqa: BLE001
        failures.append("crypto toolkit: %r" % (exc,))
        print("SELFTEST 2/6: FAIL -> %r" % (exc,))

    _hr("3/6 AES-CBC (used to decrypt Apple's sign-in reply)")
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        key = bytes(range(16))
        iv = bytes(range(16, 32))
        plain = b"barisign-selftest".ljust(32, b"\x00")
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        ciphertext = encryptor.update(plain) + encryptor.finalize()
        if aes_cbc_decrypt(key, iv, ciphertext) == plain:
            print("AES-CBC round-trip through signin.aes_cbc_decrypt: OK")
            print("SELFTEST 3/6: PASS")
        else:
            failures.append("AES-CBC mismatch")
            print("SELFTEST 3/6: FAIL -> decrypted text differs")
    except Exception as exc:                                  # noqa: BLE001
        failures.append("AES-CBC: %r" % (exc,))
        print("SELFTEST 3/6: FAIL -> %r" % (exc,))

    _hr("4/6 SRP group (RFC 5054 2048-bit, the one GrandSlam uses)")
    if SRP_N.bit_length() == 2048 and SRP_g == 2:
        print("SRP_N: 2048-bit, SRP_g = %d -> OK" % SRP_g)
        print("SELFTEST 4/6: PASS")
    else:
        failures.append("SRP group wrong")
        print("SELFTEST 4/6: FAIL")

    _hr("5/6 anisette server (no Apple ID involved)")
    print("Exercises the server list, provisioning and header minting.")
    print("This is the step most likely to be slow or flaky.")
    print("State directory: %s" % POC_STATE)
    _tls_diagnostics()
    try:
        anisette, _gs = connect_anisette("", _state_path())
        headers = anisette.headers()
        print("anisette server : %s (v%s)" % (anisette.url, anisette.version))
        print("headers obtained: %s" % ", ".join(sorted(headers)))
        print("SELFTEST 5/6: PASS")
    except Exception as exc:                                  # noqa: BLE001
        failures.append("anisette: %r" % (exc,))
        print("SELFTEST 5/6: FAIL -> %r" % (exc,))
        traceback.print_exc()

    _hr("6/6 Apple trust (gsa.apple.com)")
    print("a-Shell's Python does not ship Apple's root CA, so the handshake to")
    print("gsa.apple.com fails unless the shipped root is trusted. We verify that")
    print("file's SHA-256 against Apple's published fingerprint first.")
    print("trust file : %s" % (APPLE_ROOTS or "NOT FOUND"))
    try:
        if not APPLE_ROOTS:
            raise Fail("apple-roots.pem not found next to the script")
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        with open(APPLE_ROOTS, "rb") as handle:
            pem = handle.read()
        seen = []
        for block in re.findall(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
                                pem, re.DOTALL):
            cert = x509.load_pem_x509_certificate(block)
            fingerprint = cert.fingerprint(hashes.SHA256()).hex()
            seen.append(fingerprint)
            label = APPLE_ROOT_FINGERPRINTS.get(fingerprint)
            if label:
                pretty = ":".join(fingerprint[i:i + 2] for i in range(0, 64, 2)).upper()
                print("pinned root verified: %s" % label)
                print("  SHA-256: %s" % pretty)
        if not set(seen) & set(APPLE_ROOT_FINGERPRINTS):
            raise Fail("apple-roots.pem holds no pinned Apple root; found %d cert(s)" % len(seen))

        context = signin._ctx_for("gsa.apple.com")
        if context is signin.SSL_CTX:
            raise Fail("the Apple-only trust context was not used")
        raw = socket.create_connection(("gsa.apple.com", 443), timeout=20)
        try:
            tls = context.wrap_socket(raw, server_hostname="gsa.apple.com")
            print("TLS handshake OK (%s) using the Apple-root context" % tls.version())
            tls.close()
        finally:
            raw.close()
        print("SELFTEST 6/6: PASS")
    except Exception as exc:                                  # noqa: BLE001
        text = str(exc)
        print("SELFTEST 6/6: FAIL -> %s" % text[:200])
        traceback.print_exc()
        failures.append("Apple trust: %s" % text[:120])

    _hr("result")
    if failures:
        print("SELFTEST: FAIL (%d)" % len(failures))
        for item in failures:
            print("  - %s" % item)
        return 1
    print("SELFTEST: PASS — runtime, crypto toolkit, AES, anisette and Apple's TLS")
    print("all work. Safe to try --login next.")
    return 0


def _login(email, server):
    _hr("sign-in")
    print("This PoC only writes inside: %s" % POC_STATE)
    print("Apple ID : %s" % email)
    print("SRP-6a: the password becomes a proof; it is never sent and never saved.")
    print("Use a THROWAWAY account that can still sign in.")
    print()
    anisette, gs = connect_anisette(server or "", _state_path())
    account = AppleAccount(email, gs, anisette)

    password = getpass.getpass("Password (not shown, not stored): ")
    if not password:
        raise Fail("No password entered.")

    def ask_code(label):
        return re.sub(r"\D", "", ask(label))

    account.login(password, ask_code)
    password = None

    name = ""
    try:
        if account.spd:
            name = account.spd.get("fn", "")
    except Exception:                                         # noqa: BLE001
        pass

    _hr("result")
    print("LOGIN: PASS%s" % (" — signed in as %s" % name if name else ""))
    print("The on-device Apple sign-in path works. Nothing else happened:")
    print("no device registered, no certificate, no profile, no signing.")
    return 0


def _usage():
    print("usage: python3 poc_login.py --selftest [--server URL]")
    print("       python3 poc_login.py --login EMAIL [--server URL]")
    print()
    print("  --selftest   check the runtime, crypto, AES, anisette and Apple TLS")
    print("               (no Apple ID involved at all)")
    print("  --login      sign in with a throwaway Apple ID (stops after sign-in)")
    print("  --server     use this anisette server instead of the public list")


def _main(argv):
    email = None
    server = None
    selftest = False
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--selftest":
            selftest = True
        elif arg == "--login":
            email = rest.pop(0) if rest else None
        elif arg == "--server":
            server = rest.pop(0) if rest else None
        elif arg in ("-h", "--help"):
            _usage()
            return 0
        else:
            print("unknown argument: %s" % arg)
            _usage()
            return 2

    if selftest:
        return _selftest()
    if email:
        return _login(email, server)
    _usage()
    return 2


if __name__ == "__main__":
    try:
        sys.exit(_main(sys.argv[1:]))
    except Fail as exc:
        print()
        print("FAILED: %s" % exc)
        sys.exit(1)
    except PermissionError as exc:
        print()
        print("PERMISSION ERROR — a-Shell refused to write:")
        print("  path: %s" % (exc.filename or "(not reported)"))
        print("a-Shell only permits writes inside ~/Documents/barisign.")
        traceback.print_exc()
        sys.exit(3)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
