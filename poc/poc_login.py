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


def _select_ca_bundles():
    """Every CA source we can find — we load ALL of them.

    a-Shell's own trust store is empty, and a single bundle can be missing a root
    that a particular server uses. Loading every available bundle removes that
    failure mode. Certificate verification stays ON.
    """
    found = []
    try:
        import certifi                                     # noqa: PLC0415
        where = certifi.where()
        if where and os.path.isfile(where):
            found.append((os.path.abspath(where), "certifi (on the device)"))
    except Exception:                                      # noqa: BLE001
        pass
    shipped = _find_file("cacert.pem")
    if shipped and all(os.path.abspath(shipped) != path for path, _ in found):
        found.append((shipped, "shipped third_party/ca-bundle/cacert.pem"))
    return found


CA_BUNDLES = _select_ca_bundles()
for _path, _label in CA_BUNDLES:
    signin.set_ca_bundle(_path)
if not CA_BUNDLES:
    print("WARNING: no CA bundle found anywhere; https will very likely fail.")

APPLE_ROOTS = _find_file("apple-roots.pem")
if APPLE_ROOTS:
    signin.APPLE_CA_FILE = APPLE_ROOTS

# Never let the sign-in exchange look like a hang.
signin.PROGRESS = lambda message: print("    ... %s" % message, flush=True)


POC_VERSION = "0.7.1"


def _signin_integrity():
    """Catch a stale signin.py on the device instead of failing later at Apple.

    Reproduces the device bug: Apple's reply is AES-CBC with PKCS#7 padding. The
    padding byte used here is 6, which is NOT XML whitespace, so an implementation
    that forgets to strip it cannot parse the result.
    """
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        plain = b"barisign-x"                       # 10 bytes -> 6 bytes of padding
        padded = plain + bytes([6]) * 6
        key, iv = bytes(16), bytes(16)
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        blob = encryptor.update(padded) + encryptor.finalize()
        got = signin.aes_cbc_decrypt(key, iv, blob)
    except Exception as exc:                                  # noqa: BLE001
        print("signin.py check  : UNKNOWN (%r)" % (exc,))
        return
    if got == plain:
        print("signin.py check  : OK (PKCS#7 padding stripping present)")
    else:
        print("signin.py check  : *** STALE *** got %d bytes, expected %d"
              % (len(got), len(plain)))
        print("                   -> re-download signin.py: this copy predates the fix")


def _tls_diagnostics():
    print("poc_login.py ver : %s" % POC_VERSION)
    print("signin.py ver    : %s" % getattr(signin, "VERSION", "(none - stale file?)"))
    _signin_integrity()
    paths = ssl.get_default_verify_paths()
    print("default CA file : %s" % (paths.cafile or "(none)"))
    print("default CA dir  : %s" % (paths.capath or "(none)"))
    print("bundles loaded  : %d" % len(CA_BUNDLES))
    for path, label in CA_BUNDLES:
        print("   - %s  [%s]" % (path, label))
    if not CA_BUNDLES:
        print("   (none found — https will fail)")
    try:
        roots = ssl.create_default_context().get_ca_certs()
        print("roots in a default context: %d" % len(roots))
    except Exception as exc:                               # noqa: BLE001
        print("roots in a default context: unknown (%r)" % (exc,))


def _diag():
    """One-shot: is TLS healthy, and which context does each host use?"""
    _hr("TLS diagnostic")
    _tls_diagnostics()
    print()
    print("Hosts (verification ON, exactly as the real code does it):")
    for host in ("gsa.apple.com", "github.com", "ani.sidestore.io", "ani.sidestore.zip"):
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            ips = ", ".join(sorted({info[4][0] for info in infos}))
        except Exception as exc:                           # noqa: BLE001
            ips = "<DNS failed: %s>" % exc
        context = signin._ctx_for(host)
        which = "Apple ctx" if context is getattr(signin, "_APPLE_CTX", None) else "general ctx"
        try:
            raw = socket.create_connection((host, 443), timeout=20)
            try:
                tls = context.wrap_socket(raw, server_hostname=host)
                issuer = ""
                try:
                    cert = tls.getpeercert()
                    for rdn in cert.get("issuer", ()):
                        for key, value in rdn:
                            if key == "commonName":
                                issuer = value
                except Exception:                          # noqa: BLE001
                    pass
                print("  %-20s OK   %-7s %-11s issuer=%s  [resolved to %s]"
                      % (host, tls.version(), which, issuer or "?", ips))
                tls.close()
            finally:
                raw.close()
        except Exception as exc:                           # noqa: BLE001
            print("  %-20s FAIL %-11s -> %s  [resolved to %s]"
                  % (host, which, str(exc)[:80], ips))
    print()
    print("Reading this: if a host resolves to 127.0.0.1 / 10.7.x.x, or the successful")
    print("issuer is not the real CA (Let's Encrypt / Apple / DigiCert …), then")
    print("something on the device or network is redirecting TLS. Never disable")
    print("verification — send this whole screen instead.")


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
        plain = b"barisign-selftest"          # 17 bytes -> needs 15 bytes of padding
        pad = 16 - (len(plain) % 16)
        padded = plain + bytes([pad]) * pad   # what Apple sends us
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        got = aes_cbc_decrypt(key, iv, ciphertext)
        if got == plain:
            print("AES-CBC round-trip: padding stripped correctly")
            print("SELFTEST 3/6: PASS")
        else:
            failures.append("AES-CBC mismatch")
            print("SELFTEST 3/6: FAIL -> got %d bytes, expected %d"
                  % (len(got), len(plain)))
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


def _read_line(prompt, mask=False, idle_limit=20.0):
    """Read one line byte-by-byte in cbreak mode. Returns (text, worked).

    Why not `input()`: a-Shell's terminal does not reliably return to its original
    mode after a masked read, so a later `input()` can end up invisible or blocked
    on a bare carriage return. Reading every prompt through the same mechanism
    means we never depend on the terminal being in a "normal" state.

    `mask=True` prints `*`; `mask=False` echoes what you type. Every read is
    bounded by `select`, so this can never wedge, and the terminal attributes are
    restored (and verified) in `finally`.
    """
    fd = sys.stdin.fileno()
    try:
        import select
        import termios
    except ImportError:
        return "", False

    try:
        saved = termios.tcgetattr(fd)
    except Exception:                                          # noqa: BLE001
        return "", False

    buf = bytearray()
    try:
        new = list(saved)
        if mask:
            new[3] = new[3] & ~termios.ECHO
        new[3] = new[3] & ~termios.ICANON          # deliver bytes without waiting
        new[6] = list(new[6])
        new[6][termios.VMIN] = 0
        new[6][termios.VTIME] = 0
        try:
            termios.tcsetattr(fd, termios.TCSANOW, new)
        except Exception:                                      # noqa: BLE001
            return "", False

        sys.stdout.write(prompt)
        sys.stdout.flush()
        last = time.time()
        while True:
            try:
                ready, _, _ = select.select([fd], [], [], 1.0)
            except Exception:                                  # noqa: BLE001
                break
            if not ready:
                if time.time() - last > idle_limit:
                    return "", False            # nothing arrived -> let caller decide
                continue
            try:
                chunk = os.read(fd, 1)
            except Exception:                                  # noqa: BLE001
                break
            if not chunk:
                break
            last = time.time()
            byte = chunk[0]
            if byte in (0x0A, 0x0D):                            # Return
                break
            if byte in (0x7F, 0x08):                            # Backspace
                if buf:
                    buf.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if byte == 0x03:                                    # Ctrl-C
                raise KeyboardInterrupt
            buf.append(byte)
            if mask:
                sys.stdout.write("*")
            else:
                sys.stdout.write(bytes([byte]).decode("utf-8", "replace"))
            sys.stdout.flush()
        sys.stdout.write("\n")
        sys.stdout.flush()
        return bytes(buf).decode("utf-8", "replace"), True
    finally:
        _restore_terminal(fd)


def _restore_terminal(fd):
    """Put the terminal back, and say so if we could not."""
    try:
        import termios
    except ImportError:
        return
    for _attempt in range(2):
        try:
            current = termios.tcgetattr(fd)
            current[3] = current[3] | termios.ICANON | termios.ECHO | termios.ISIG
            current[6] = list(current[6])
            current[6][termios.VMIN] = 1
            current[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, current)
        except Exception:                                      # noqa: BLE001
            return
        try:
            if termios.tcgetattr(fd)[3] & termios.ECHO:
                return
        except Exception:                                      # noqa: BLE001
            return
    print()
    print("NOTE: this terminal did not accept its settings back. If the shell looks")
    print("odd afterwards (no echo), close and reopen a-Shell — nothing is broken.")


def _read_password(prompt):
    """Hidden when possible, visible when not, and never wedged.

    (1) a pipe means the Shortcut is driving -> read it; (2) masked cbreak read;
    (3) plain `input()` as a last resort.
    """
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\r\n")

    secret, worked = _read_line(prompt, mask=True)
    if worked:
        return secret

    print("(masking is not supported here — type it again, it will be visible)")
    try:
        return input("")
    except EOFError:
        return ""


def _login(email, server):
    _hr("sign-in")
    print("This PoC only writes inside: %s" % POC_STATE)
    print("Apple ID : %s" % email)
    print("SRP-6a: the password becomes a proof; it is never sent and never saved.")
    print("Your typing is masked with '*' where a-Shell allows it. If masking is")
    print("not supported you will be told, and it will be visible instead.")
    print()
    print("If this account uses two-factor authentication, Apple will ask for a")
    print("code and a prompt will appear here.")
    print()
    _tls_diagnostics()
    anisette, gs = connect_anisette(server or "", _state_path())
    account = AppleAccount(email, gs, anisette)

    password = _read_password("Password (not shown, not stored): ")
    print("Password received (%d characters). It is used locally only." % len(password))
    if not password:
        raise Fail("No password entered.")

    def ask_code(label):
        # Deliberately NOT masked (mask=False): a verification code is not a
        # secret, and seeing it on screen makes a wrong code obvious instead of
        # turning it into a mystery failure. Only the password is masked.
        entered, worked = _read_line("%s: " % label, mask=False)
        if not worked:
            entered = ask(label)
        return re.sub(r"\D", "", entered)

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
    print("       python3 poc_login.py --diag")
    print("       python3 poc_login.py --login EMAIL [--server URL]")
    print()
    print("  --selftest   check the runtime, crypto, AES, anisette and Apple TLS")
    print("               (no Apple ID involved at all)")
    print("  --diag       print the CA bundles and test TLS to a few hosts")
    print("  --login      sign in with an Apple ID (stops after sign-in)")
    print("  --server     use this anisette server instead of the public list")


def _main(argv):
    email = None
    server = None
    selftest = False
    diag = False
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--selftest":
            selftest = True
        elif arg == "--diag":
            diag = True
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

    if diag:
        _diag()
        return 0
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
