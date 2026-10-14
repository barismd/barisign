#!/usr/bin/env python3
# =============================================================================
#  BariSign — PoC-D : on-device Apple ID sign-in (no computer)
#  MIT licence (this file). It imports the vendored sign-in stack from
#  third_party/sideinstaller/signin.py, which is under a different licence.
# =============================================================================
#
#  RUN IT (on the iPhone, in a-Shell):
#
#      mkdir -p ~/barisign && cd ~/barisign
#      curl -sL <REPO>/third_party/sideinstaller/signin.py -o signin.py
#      curl -sL <REPO>/poc/poc_login.py -o poc_login.py
#      python3 poc_login.py --selftest
#      python3 poc_login.py --login YOUR-THROWAWAY-APPLE-ID
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

_HERE = os.path.dirname(os.path.abspath(__file__))
# Works both when the two files sit side by side (the on-device layout) and when
# this is run from a checkout of the repository.
for _candidate in (os.path.join(_HERE, "third_party", "sideinstaller"),
                   os.path.join(_HERE, "..", "third_party", "sideinstaller")):
    if os.path.isfile(os.path.join(_candidate, "signin.py")):
        sys.path.insert(0, _candidate)
        break
sys.path.insert(0, _HERE)

try:
    from signin import (Fail, SRP_N, SRP_g, AppleAccount, aes_cbc_decrypt,
                        ask, connect_anisette, request)
except ImportError as exc:                                    # noqa: PERF203
    print("Could not import signin.py: %s" % exc)
    print("Download it next to this file:")
    print("  curl -sL <REPO>/third_party/sideinstaller/signin.py -o signin.py")
    sys.exit(2)

POC_STATE = os.path.join(os.path.expanduser("~"), ".barisign-poc")


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
    try:
        os.makedirs(POC_STATE, exist_ok=True)
        anisette, _gs = connect_anisette("", os.path.join(POC_STATE, "anisette.json"))
        headers = anisette.headers()
        print("anisette server : %s (v%s)" % (anisette.url, anisette.version))
        print("headers obtained: %s" % ", ".join(sorted(headers)))
        print("SELFTEST 5/6: PASS")
    except Exception as exc:                                  # noqa: BLE001
        failures.append("anisette: %r" % (exc,))
        print("SELFTEST 5/6: FAIL -> %r" % (exc,))

    _hr("6/6 Apple GrandSlam TLS trust (gsa.apple.com)")
    print("Sign-in starts with a TLS connection to gsa.apple.com. Apple's chain is")
    print("rooted in Apple's own CA, which Linux often does not trust but Apple")
    print("devices do. This is a plain handshake, so it isolates that one question.")
    try:
        context = ssl.create_default_context()
        raw = socket.create_connection(("gsa.apple.com", 443), timeout=20)
        try:
            tls = context.wrap_socket(raw, server_hostname="gsa.apple.com")
            print("TLS handshake OK (%s)" % tls.version())
            tls.close()
        finally:
            raw.close()
        print("SELFTEST 6/6: PASS")
    except Exception as exc:                                  # noqa: BLE001
        text = str(exc)
        print("SELFTEST 6/6: FAIL -> %s" % text[:200])
        if "certificate verify failed" in text:
            print()
            print("DIAGNOSIS: this Python does not trust the chain gsa.apple.com sends.")
            print("This is EXPECTED on Linux/desktop and is NOT a bug there.")
            print("If it happens ON THE IPHONE it is a real blocker, and the fix is to")
            print("ship Apple's public root CA in a trust file next to the script.")
            print("Please report this line verbatim.")
        failures.append("Apple GrandSlam TLS trust: %s" % text[:120])

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
    os.makedirs(POC_STATE, exist_ok=True)
    print("Apple ID : %s" % email)
    print("SRP-6a: the password becomes a proof; it is never sent and never saved.")
    print("Use a THROWAWAY account that can still sign in.")
    print()
    anisette, gs = connect_anisette(server or "", os.path.join(POC_STATE, "anisette.json"))
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
    except KeyboardInterrupt:
        print()
        sys.exit(130)
