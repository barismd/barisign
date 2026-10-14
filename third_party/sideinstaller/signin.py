# =============================================================================
#  VENDORED CODE — SideInstaller by FrizzleM   (NOT MIT)
# =============================================================================
#  Source   : https://github.com/FrizzleM/SideInstaller  ->  ish/sideinstaller.py
#  Copyright: (c) 2026 FrizzleM
#  Licence  : see LICENSE.md in this folder. Non-commercial; source-form
#             redistribution only; attribution required; changes must be noted.
#             This file is NOT covered by BariSign's MIT licence.
#
#  Taken: the pure-Python sign-in stack (HTTP, AES/AES-GCM, SRP-6a, WebSocket,
#         Anisette v3 + provisioning, GrandSlam, AppleAccount).
#  Left out: developer portal, certificates/App IDs/profiles, UDID capture,
#         local CA/TLS server, the zsign call, and upstream's own CLI.
#
#  CHANGES BY BARISIGN (required by the licence to be stated):
#    * the openssl()-based aes_cbc_decrypt() was removed and reimplemented below
#      on top of `cryptography`, because a-Shell has no openssl and this function
#      is on the sign-in path;
#    * the now-unused openssl() helper was deleted;
#    * the file was cut down to the list above.
# =============================================================================


import base64
import binascii
import getpass
import gzip
import hashlib
import hmac
import http.client
import json
import os
import plistlib
import re
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
import zipfile

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

HOME = os.path.expanduser("~")
STORAGE = os.environ.get("SIDEINSTALLER_HOME", os.path.join(HOME, ".sideinstaller"))

# The App Store build of iSH is a normal sandboxed app, so it cannot ask the
# device for its own UDID. Every route to one is listed in ask_udid().
UDID_RE = re.compile(r"^(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16})$")

GSA_URL_BAG = "https://gsa.apple.com/grandslam/GsService2/lookup"
DEV_HOST = "developerservices2.apple.com"
# clientId/protocolVersion identify us as Xcode to the portal. Not decorative:
# the .action endpoints 404 without the matching path segment.
DEV_CLIENT_ID = "XABBG36SBA"
DEV_PROTOCOL = "QH65B2"

DEFAULT_ANISETTE = os.environ.get("ANISETTE_URL", "https://ani.sidestore.io")
# Same live list the app reads, with the same bundled fallback, so a server
# going down here fails over exactly the way it does in SideInstaller.
ANISETTE_LIST_URL = "https://servers.sidestore.io/servers.json"
BUNDLED_ANISETTE = [
    "https://ani.sidestore.io",
    "https://ani.sidestore.app",
    "https://ani.sidestore.zip",
    "https://ani.846969.xyz",
    "https://ani.npeg.us",
    "https://anisette.wedotstud.io",
    "https://ani.neoarz.com",
    "https://ani.jaydenha.uk",
]
# Anisette servers are volunteer-run and half of any list is usually down;
# stop after this many so a bad day doesn't turn into a long wait.
MAX_ANISETTE_TRIES = 6

RELEASE_API = "https://api.github.com/repos/FrizzleM/SideInstaller/releases"
# Matches the machineName SideInstaller itself sends, so a certificate minted
# here is the same one the app would look for later.
MACHINE_NAME = os.environ.get("MACHINE_NAME", "SideInstaller")

# RFC 5054 2048-bit group, the one GrandSlam negotiates.
SRP_N = int(
    "ac6bdb41324a9a9bf166de5e1389582faf72b6651987ee07fc3192943db56050"
    "a37329cbb4a099ed8193e0757767a13dd52312ab4b03310dcd7f48a9da04fd50"
    "e8083969edb767b0cf6095179a163ab3661a05fbd5faaae82918a9962f0b93b8"
    "55f97993ec975eeaa80d740adbf4ff747359d041d5c33ea71d281e446b14773b"
    "ca97b43a23fb801676bd207a436c6481f1d2b9078717461a5b9d32e688f87748"
    "544523b524b0d57d5ea77a2775d2ecfa032cfbdbf52fb3786160279004e57ae6"
    "af874e7303ce53299ccc041c7bc308d82a5698f3a8d0c38271ae35f8e9dbfbb6"
    "94b5c803d89f7ae435de236d525f54759b65e372fcd68ef20fa7111f9e4aff73",
    16,
)
SRP_g = 2


# ----------------------------------------------------------------------------
# Terminal output
# ----------------------------------------------------------------------------

_TTY = sys.stdout.isatty()


def _c(code, text):
    return "\033[%sm%s\033[0m" % (code, text) if _TTY else text


def step(msg):
    print(_c("1;36", "==> ") + _c("1", msg))


def info(msg):
    print("    " + msg)


def warn(msg):
    print(_c("1;33", "  ! ") + msg)


def die(msg):
    print(_c("1;31", "  x ") + msg, file=sys.stderr)
    sys.exit(1)


def ok(msg):
    print(_c("1;32", "  ✓ ") + msg)


def ask(prompt, default=None):
    suffix = " [%s]: " % default if default else ": "
    while True:
        try:
            value = input(prompt + suffix).strip()
        except EOFError:
            die("No input available; run this script interactively.")
        if value:
            return value
        if default is not None:
            return default


def confirm(prompt, default=False):
    hint = "[Y/n]" if default else "[y/N]"
    try:
        value = input("%s %s " % (prompt, hint)).strip().lower()
    except EOFError:
        return default
    if not value:
        return default
    return value in ("y", "yes")


class Fail(Exception):
    """Anything the user can act on. Raised instead of a traceback."""


# ----------------------------------------------------------------------------
# HTTP
#
# http.client rather than urllib: urllib title-cases header names through
# str.capitalize(), which turns X-Apple-I-MD into X-apple-i-md. isideload goes
# out of its way to keep reqwest's title casing for these endpoints, so the
# safest thing is to send the header names exactly as written.
# ----------------------------------------------------------------------------

SSL_CTX = ssl.create_default_context()


def request(method, url, headers=None, body=None, timeout=60, redirects=5):
    """Return (status, header_dict, body_bytes). Follows redirects."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query

    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=SSL_CTX)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)

    if isinstance(body, str):
        body = body.encode("utf-8")

    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", parts.netloc)
        sent = set()
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
            sent.add(key.lower())
        if body is not None and "content-length" not in sent:
            conn.putheader("Content-Length", str(len(body)))
        if "accept-encoding" not in sent:
            conn.putheader("Accept-Encoding", "identity")
        conn.endheaders(body)
        response = conn.getresponse()
        data = response.read()
        status = response.status
        got = {k.lower(): v for k, v in response.getheaders()}
        # The developer portal gzips its replies whatever Accept-Encoding says.
        if got.get("content-encoding", "").lower() == "gzip":
            data = gzip.decompress(data)
    finally:
        conn.close()

    if status in (301, 302, 303, 307, 308) and redirects > 0 and got.get("location"):
        target = urllib.parse.urljoin(url, got["location"])
        # 303, and 301/302 on POST, degrade to GET the way every client does.
        if status == 303 or (status in (301, 302) and method == "POST"):
            method, body = "GET", None
        return request(method, target, headers, body, timeout, redirects - 1)

    return status, got, data


def download(url, dest, headers=None):
    """Stream a URL to a file, printing progress. Returns the byte count."""
    parts = urllib.parse.urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(parts.hostname, port, timeout=120, context=SSL_CTX)
    else:
        conn = http.client.HTTPConnection(parts.hostname, port, timeout=120)
    path = parts.path + ("?" + parts.query if parts.query else "")
    try:
        conn.request("GET", path, headers=dict(headers or {}, **{"Host": parts.netloc}))
        response = conn.getresponse()
        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader("Location")
            response.read()
            conn.close()
            return download(urllib.parse.urljoin(url, location), dest, headers)
        if response.status != 200:
            raise Fail("Download failed (HTTP %d): %s" % (response.status, url))
        total = int(response.getheader("Content-Length") or 0)
        written = 0
        with open(dest, "wb") as handle:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if total and _TTY:
                    sys.stdout.write("\r    %d%% (%.1f MB)" % (written * 100 // total, written / 1e6))
                    sys.stdout.flush()
        if total and _TTY:
            sys.stdout.write("\r" + " " * 32 + "\r")
            sys.stdout.flush()
        return written
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# openssl
#
# Everything asymmetric shells out. Alpine's python3 has no AES and no RSA, and
# the wheels that would supply them need a Rust toolchain, which under iSH's
# i386 emulation is not a realistic ask.
# ----------------------------------------------------------------------------



# ----------------------------------------------------------------------------
# AES-GCM
#
# GrandSlam returns app tokens under AES-256-GCM with a 16-byte nonce, which
# `openssl enc` cannot do at all (no GCM) and which even the 12-byte fast path
# wouldn't cover. GCM only ever runs AES forwards, so this is the encrypt-side
# cipher plus GHASH — a few hundred bytes of plaintext, so speed is irrelevant.
# ----------------------------------------------------------------------------

_SBOX = None
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D]


def _build_sbox():
    global _SBOX
    if _SBOX is not None:
        return _SBOX
    p = q = 1
    sbox = [0] * 256
    while True:
        # p *= 3 in GF(2^8)
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        # q /= 3
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        value = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (value ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    _SBOX = sbox
    return sbox


def _xtime(a):
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


class AES:
    """AES-256 forward direction only (all GCM needs)."""

    def __init__(self, key):
        sbox = _build_sbox()
        nk = len(key) // 4
        if nk != 8:
            raise Fail("AES: expected a 256-bit key, got %d bits" % (len(key) * 8))
        self.rounds = nk + 6
        words = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
        for i in range(nk, 4 * (self.rounds + 1)):
            temp = list(words[i - 1])
            if i % nk == 0:
                temp = temp[1:] + temp[:1]
                temp = [sbox[b] for b in temp]
                temp[0] ^= _RCON[i // nk - 1]
            elif i % nk == 4:
                temp = [sbox[b] for b in temp]
            words.append([a ^ b for a, b in zip(words[i - nk], temp)])
        self.round_keys = [sum(words[4 * r:4 * r + 4], []) for r in range(self.rounds + 1)]
        self.sbox = sbox

    def encrypt_block(self, block):
        sbox = self.sbox
        state = [b ^ k for b, k in zip(block, self.round_keys[0])]
        for rnd in range(1, self.rounds + 1):
            state = [sbox[b] for b in state]
            # ShiftRows, on a column-major state
            state = [
                state[0], state[5], state[10], state[15],
                state[4], state[9], state[14], state[3],
                state[8], state[13], state[2], state[7],
                state[12], state[1], state[6], state[11],
            ]
            if rnd != self.rounds:
                mixed = []
                for c in range(4):
                    a = state[4 * c:4 * c + 4]
                    t = a[0] ^ a[1] ^ a[2] ^ a[3]
                    mixed += [
                        a[0] ^ t ^ _xtime(a[0] ^ a[1]),
                        a[1] ^ t ^ _xtime(a[1] ^ a[2]),
                        a[2] ^ t ^ _xtime(a[2] ^ a[3]),
                        a[3] ^ t ^ _xtime(a[3] ^ a[0]),
                    ]
                state = mixed
            state = [b ^ k for b, k in zip(state, self.round_keys[rnd])]
        return bytes(state)


def _ghash(h_key, data):
    """GF(2^128) multiply-accumulate, the GCM authentication primitive."""
    h = int.from_bytes(h_key, "big")
    y = 0
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16].ljust(16, b"\0")
        y ^= int.from_bytes(block, "big")
        z = 0
        v = h
        for bit in range(127, -1, -1):
            if (y >> bit) & 1:
                z ^= v
            if v & 1:
                v = (v >> 1) ^ 0xE1000000000000000000000000000000
            else:
                v >>= 1
        y = z
    return y.to_bytes(16, "big")


def aes_gcm_decrypt(key, nonce, ciphertext, aad, tag):
    cipher = AES(key)
    h = cipher.encrypt_block(b"\0" * 16)

    if len(nonce) == 12:
        j0 = nonce + b"\0\0\0\1"
    else:
        padded = nonce + b"\0" * ((-len(nonce)) % 16) + b"\0" * 8 + struct.pack(">Q", len(nonce) * 8)
        j0 = _ghash(h, padded)

    counter = int.from_bytes(j0, "big")
    out = bytearray()
    for offset in range(0, len(ciphertext), 16):
        counter = (counter & ~0xFFFFFFFF) | ((counter + 1) & 0xFFFFFFFF)
        stream = cipher.encrypt_block(counter.to_bytes(16, "big"))
        block = ciphertext[offset:offset + 16]
        out += bytes(a ^ b for a, b in zip(block, stream))

    auth = (
        aad + b"\0" * ((-len(aad)) % 16)
        + ciphertext + b"\0" * ((-len(ciphertext)) % 16)
        + struct.pack(">QQ", len(aad) * 8, len(ciphertext) * 8)
    )
    expected = bytes(a ^ b for a, b in zip(_ghash(h, auth), cipher.encrypt_block(j0)))
    if not hmac.compare_digest(expected, tag):
        raise Fail("App token failed its authentication check.")
    return bytes(out)


# ----------------------------------------------------------------------------
# SRP-6a, GrandSlam flavour
#
# Ported byte-for-byte from what isideload gets out of srp 0.7.0-rc.3 with
# `new_with_options(false)`. Two deviations from textbook RFC 5054 matter:
#   * the username is left OUT of x, so x = H(salt | H(":" | password))
#   * `password` is not the password but PBKDF2 over its SHA-256 digest
# Everything is serialised as big-endian with leading zero bytes stripped,
# matching `to_be_bytes_trimmed_vartime`, except inside PAD() where values are
# left-padded to the width of N.
# ----------------------------------------------------------------------------

def _trim(value):
    length = max(1, (value.bit_length() + 7) // 8)
    return value.to_bytes(length, "big")


def _pad(value_bytes, width):
    return value_bytes.rjust(width, b"\0")


def _sha256(*chunks):
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
    return digest.digest()


def loads_bare_plist(data):
    """plistlib.loads for what GrandSlam encrypts (the sign-in response, app
    tokens): a bare <dict>, which plistlib only reads inside a <plist>."""
    data = data.strip()
    if data.startswith(b"<dict"):
        data = b'<plist version="1.0">' + data + b"</plist>"
    return plistlib.loads(data)


class SRPClient:
    def __init__(self):
        self.n_bytes = _trim(SRP_N)
        self.width = len(self.n_bytes)
        self.a = int.from_bytes(secrets.token_bytes(32), "big")
        self.a_pub = _trim(pow(SRP_g, self.a, SRP_N))

    def process(self, username, password_key, salt, b_pub_bytes):
        b_pub = int.from_bytes(b_pub_bytes, "big")
        if b_pub % SRP_N == 0:
            raise Fail("Apple returned an invalid SRP parameter (B ≡ 0).")

        g_bytes = _trim(SRP_g)
        u = int.from_bytes(
            _sha256(_pad(self.a_pub, self.width), _pad(b_pub_bytes, self.width)), "big"
        )
        k = int.from_bytes(_sha256(self.n_bytes, _pad(g_bytes, self.width)), "big")

        identity_hash = _sha256(b"", b":", password_key)
        x = int.from_bytes(_sha256(salt, identity_hash), "big")

        premaster = pow(b_pub - k * pow(SRP_g, x, SRP_N), self.a + u * x, SRP_N)
        self.premaster = _trim(premaster)
        self.session_key = _sha256(self.premaster)

        n_xor_g = bytes(
            a ^ b for a, b in zip(_sha256(self.n_bytes), _sha256(_pad(g_bytes, self.width)))
        )
        self.m1 = _sha256(
            n_xor_g,
            _sha256(username.encode("utf-8")),
            salt,
            self.a_pub,
            b_pub_bytes,
            self.session_key,
        )
        self.m2 = _sha256(self.a_pub, self.m1, self.session_key)
        return self.m1

    def verify(self, server_m2):
        return hmac.compare_digest(self.m2, server_m2)

    def decrypt_spd(self, blob):
        """Unwrap the AES-CBC 'extra data' the server returns with M2.

        The HMAC key here is the one place two working implementations differ:
        isideload keys it on the raw premaster secret S, while everything built
        on pysrp keys it on H(S). Rather than bet on one, try both and keep the
        one that yields a parseable plist.
        """
        errors = []
        for label, base in (("H(S)", self.session_key), ("S", self.premaster)):
            key = hmac.new(base, b"extra data key:", hashlib.sha256).digest()
            iv = hmac.new(base, b"extra data iv:", hashlib.sha256).digest()[:16]
            try:
                return loads_bare_plist(aes_cbc_decrypt(key, iv, blob))
            except Exception as exc:  # noqa: BLE001 - any failure means "wrong key"
                errors.append("%s: %s" % (label, exc))
        raise Fail("Could not decrypt the sign-in response (%s)." % "; ".join(errors))


def srp_password_key(password, salt, iterations, protocol):
    """Apple's s2k / s2k_fo password pre-hash, fed to PBKDF2-HMAC-SHA256."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    if protocol == "s2k_fo":
        digest = binascii.hexlify(digest)
    elif protocol != "s2k":
        raise Fail("Apple asked for an unsupported SRP protocol: %s" % protocol)
    return hashlib.pbkdf2_hmac("sha256", digest, salt, iterations, 32)


# ----------------------------------------------------------------------------
# Minimal RFC 6455 client
#
# Only used to talk to the anisette server's provisioning socket. Alpine's
# py3-websockets is an extra package and an extra thing to trust; the subset
# needed here (text frames, one message in flight) is small enough to inline.
# ----------------------------------------------------------------------------

class WebSocket:
    def __init__(self, url, timeout=45):
        parts = urllib.parse.urlsplit(url)
        secure = parts.scheme == "wss"
        port = parts.port or (443 if secure else 80)
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")

        raw = socket.create_connection((parts.hostname, port), timeout=timeout)
        self.sock = SSL_CTX.wrap_socket(raw, server_hostname=parts.hostname) if secure else raw

        nonce = base64.b64encode(secrets.token_bytes(16)).decode()
        handshake = (
            "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
            % (path, parts.netloc, nonce)
        )
        self.sock.sendall(handshake.encode())

        self.buffer = b""
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise Fail("Anisette server closed the provisioning socket during the handshake.")
            self.buffer += chunk
        head, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise Fail("Anisette server refused the provisioning socket: %s"
                       % head.split(b"\r\n")[0].decode("utf-8", "replace"))

    def _recv_exactly(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise Fail("Anisette provisioning socket closed unexpectedly.")
            self.buffer += chunk
        head, self.buffer = self.buffer[:count], self.buffer[count:]
        return head

    def recv(self):
        """Return the next text message, or None once the peer closes."""
        while True:
            header = self._recv_exactly(2)
            opcode = header[0] & 0x0F
            masked = header[1] & 0x80
            length = header[1] & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exactly(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exactly(8))[0]
            mask = self._recv_exactly(4) if masked else None
            payload = self._recv_exactly(length) if length else b""
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode in (0x1, 0x2):
                return payload.decode("utf-8", "replace")

    def _send_frame(self, opcode, payload):
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = secrets.token_bytes(4)
        header += mask
        header += bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header))

    def send_json(self, payload):
        self._send_frame(0x1, json.dumps(payload).encode())

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ----------------------------------------------------------------------------
# Anisette
#
# Apple will not authenticate a client that cannot produce a machine
# identifier issued by their own ADI library. Nobody can run that library here,
# so an anisette server runs it instead: we hand it an opaque identifier, it
# hands back one-time headers. It never sees the Apple ID or the password.
#
# Two protocols are in the wild. v3 provisions once over a websocket and then
# mints headers from the stored blob; v1 just returns a header set per request.
# ----------------------------------------------------------------------------

# What we tell GrandSlam we are, whatever the anisette server suggests. Public
# servers make anisette data with Apple's Android ADI library and hand out a
# macOS client info, and since 2026-10-12 GrandSlam refuses a sign-in whose
# client OS isn't the one the machine ID was made on (-80045, "This action
# cannot be completed at this time"). So the device and OS are an Android
# phone's. Most servers also still name com.apple.dt.Xcode, and since September
# 2026 Apple's GSA edge answers any request carrying that with HTTP 503 before
# reading the rest; isideload, AltStore and SideSign all switched to akd.
GSA_CLIENT_INFO = "<Pixel 7> <Android;14;UQ1A.240205.002> <com.apple.AuthKit/1 (com.apple.akd/1.0)>"
GSA_USER_AGENT = "akd/1.0 CFNetwork/808.1.4"
# Two kinds of request are made as a Mac, because Apple won't take them from an
# Android phone. The URL bag: asked as an Android phone, the lookup answers with
# ten URLs on gsd.apple.com; as a Mac, with the full bag on gsa.apple.com, where
# the other URLs in this script live and where sign-in as an Android phone was
# tested. And the second-factor pages under gsa.apple.com/auth (sending the code
# to the trusted devices again, texting it, checking a texted code): asked as an
# Android phone they answer a bare 403, before they read who is asking.
GSA_MAC_CLIENT_INFO = "<Mac15,7> <macOS;27.0;26A5378j> <com.apple.AuthKit/1 (com.apple.akd/1.0)>"


class Anisette:
    def __init__(self, url, state_path):
        self.url = url.rstrip("/")
        self.state_path = state_path
        self.version = None
        self.client_info = GSA_CLIENT_INFO
        self.user_agent = GSA_USER_AGENT
        self.routing_info = "0"
        self.identifier = None
        self.adi_pb = None
        self._load()

    # -- persisted identity ---------------------------------------------------

    def _load(self):
        try:
            with open(self.state_path) as handle:
                saved = json.load(handle)
            self.identifier = base64.b64decode(saved["identifier"])
            if saved.get("adi_pb"):
                self.adi_pb = base64.b64decode(saved["adi_pb"])
        except (OSError, ValueError, KeyError):
            self.identifier = secrets.token_bytes(16)

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        payload = {"identifier": base64.b64encode(self.identifier).decode()}
        if self.adi_pb:
            payload["adi_pb"] = base64.b64encode(self.adi_pb).decode()
        with open(self.state_path, "w") as handle:
            json.dump(payload, handle)
        os.chmod(self.state_path, 0o600)

    @property
    def device_id(self):
        return str(uuid.UUID(bytes=self.identifier))

    @property
    def md_lu(self):
        return hashlib.sha256(self.identifier).hexdigest()

    # -- protocol detection ---------------------------------------------------

    @staticmethod
    def _json(body, what):
        try:
            return json.loads(body)
        except ValueError:
            snippet = body[:120].decode("utf-8", "replace").strip()
            raise Fail("Anisette server answered %s with something that isn't JSON: %s"
                       % (what, snippet or "an empty body"))

    def detect(self):
        status, _, body = request("GET", self.url + "/v3/client_info")
        if status == 200:
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {}
            # Only the shape identifies a v3 server; its values are not sent.
            if isinstance(payload, dict) and "client_info" in payload and "user_agent" in payload:
                self.version = 3
                return 3

        status, _, body = request("GET", self.url + "/")
        if status == 200:
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {}
            if "X-Apple-I-MD" in payload:
                self.version = 1
                return 1

        raise Fail("%s does not look like an anisette server." % self.url)

    # -- v3 provisioning ------------------------------------------------------

    def needs_provisioning(self):
        return self.version == 3 and not self.adi_pb

    def forget_provisioning(self):
        self.adi_pb = None
        self._save()

    def provision(self, gs):
        ws_url = self.url.replace("https://", "wss://").replace("http://", "ws://")
        socket_ = WebSocket(ws_url + "/v3/provisioning_session")
        headers = {"X-Apple-I-MD-LU": self.md_lu, "X-Mme-Device-Id": self.device_id}
        try:
            while True:
                message = socket_.recv()
                if message is None:
                    raise Fail("Anisette provisioning socket closed before it finished.")
                payload = json.loads(message)
                result = payload.get("result")

                if result == "GiveIdentifier":
                    socket_.send_json({"identifier": base64.b64encode(self.identifier).decode()})

                elif result == "GiveStartProvisioningData":
                    response = gs.plist_request(
                        gs.url("midStartProvisioning"),
                        {"Header": {}, "Request": {}},
                        extra_headers=headers,
                    )
                    socket_.send_json({"spim": response["spim"]})

                elif result == "GiveEndProvisioningData":
                    response = gs.plist_request(
                        gs.url("midFinishProvisioning"),
                        {"Header": {}, "Request": {"cpim": payload["cpim"]}},
                        extra_headers=headers,
                    )
                    socket_.send_json({"ptm": response["ptm"], "tk": response["tk"]})

                elif result == "ProvisioningSuccess":
                    self.adi_pb = base64.b64decode(payload["adi_pb"])
                    self._save()
                    return

                elif result in ("Timeout", "InvalidIdentifier"):
                    raise Fail("Anisette provisioning failed: %s" % result)

                else:
                    raise Fail("Anisette provisioning failed: %s"
                               % payload.get("message", result or message))
        finally:
            socket_.close()

    # -- headers --------------------------------------------------------------

    def headers(self):
        """The three headers Apple checks, refreshed per request (X-Apple-I-MD
        is a one-time password and does not survive being reused)."""
        if self.version == 1:
            status, _, body = request("GET", self.url + "/")
            if status != 200:
                raise Fail("Anisette server returned HTTP %d." % status)
            payload = self._json(body, "a header request")
            self.routing_info = payload.get("X-Apple-I-MD-RINFO", "0")
            return {
                "X-Mme-Device-Id": payload.get("X-Mme-Device-Id", self.device_id),
                "X-Apple-I-MD": payload["X-Apple-I-MD"],
                "X-Apple-I-MD-M": payload["X-Apple-I-MD-M"],
            }

        status, _, body = request(
            "POST", self.url + "/v3/get_headers",
            headers={"Content-Type": "application/json"},
            body=json.dumps({
                "identifier": base64.b64encode(self.identifier).decode(),
                "adi_pb": base64.b64encode(self.adi_pb).decode(),
            }),
        )
        payload = self._json(body, "a header request") if body else {}
        if status != 200 or payload.get("result") != "Headers":
            raise Fail("Anisette server refused to mint headers: %s"
                       % payload.get("message", "HTTP %d" % status))
        self.routing_info = payload.get("X-Apple-I-MD-RINFO", "0")
        return {
            "X-Mme-Device-Id": self.device_id,
            "X-Apple-I-MD": payload["X-Apple-I-MD"],
            "X-Apple-I-MD-M": payload["X-Apple-I-MD-M"],
        }

    def client_provided_data(self):
        # These really are the strings "true"/"false", not plist booleans —
        # isideload sends them that way and Apple accepts it.
        cpd = {
            "bootstrap": "true",
            "icscrec": "true",
            "loc": "en_US",
            "pbe": "false",
            "prkgen": "true",
            "svct": "iCloud",
        }
        cpd.update(self.headers())
        return cpd


# ----------------------------------------------------------------------------
# GrandSlam
# ----------------------------------------------------------------------------

class GrandSlam:
    def __init__(self, anisette):
        self.anisette = anisette
        status, _, body = request("GET", GSA_URL_BAG, headers=self.mac_headers())
        if status != 200:
            raise Fail("Could not reach Apple's GrandSlam service (HTTP %d)." % status)
        self.url_bag = plistlib.loads(body)["urls"]

    def base_headers(self, plist=True):
        headers = {}
        if plist:
            headers["Content-Type"] = "text/x-xml-plist"
            headers["Accept"] = "text/x-xml-plist"
        headers["X-Mme-Client-Info"] = self.anisette.client_info
        headers["User-Agent"] = self.anisette.user_agent
        headers["X-Xcode-Version"] = "14.2 (14C18)"
        headers["X-Apple-App-Info"] = "com.apple.gs.xcode.auth"
        return headers

    def mac_headers(self, plist=True):
        """base_headers, saying the client is a Mac (see GSA_MAC_CLIENT_INFO)."""
        headers = self.base_headers(plist)
        headers["X-Mme-Client-Info"] = GSA_MAC_CLIENT_INFO
        return headers

    def url(self, key):
        if key not in self.url_bag:
            raise Fail("Apple's URL bag has no '%s' entry." % key)
        return self.url_bag[key]

    def plist_request(self, url, body, extra_headers=None):
        headers = self.base_headers()
        headers.update(extra_headers or {})
        payload = plistlib.dumps(body, fmt=plistlib.FMT_XML)
        status, _, response = request("POST", url, headers=headers, body=payload)
        if status != 200:
            raise Fail("Apple returned HTTP %d for %s." % (status, url))
        parsed = plistlib.loads(response)
        if "Response" not in parsed:
            raise Fail("Unexpected reply from Apple (no Response section).")
        return check_gs_error(parsed["Response"])


def check_gs_error(response):
    status = response.get("Status", response)
    code = status.get("ec", 0)
    if code:
        raise Fail("Apple rejected the request (%s): %s"
                   % (code, status.get("em", "no message")))
    return response


# ----------------------------------------------------------------------------
# Apple ID sign-in
# ----------------------------------------------------------------------------

class AppleAccount:
    def __init__(self, email, gs, anisette):
        self.email = email
        self.gs = gs
        self.anisette = anisette
        self.spd = None

    def login(self, password, ask_code):
        state = self._login_once(password)
        for _ in range(10):
            if state == "ok":
                return
            if state == "device-2fa":
                self._device_2fa(ask_code)
            elif state == "device-2fa-sent":
                self._device_2fa(ask_code, send=False)
            elif state == "sms-2fa":
                self._sms_2fa(ask_code)
            else:
                # "repair" and friends: the session is usable if a PET came back.
                if self._pet():
                    return
                raise Fail("Apple wants an extra sign-in step this script cannot do: %s.\n"
                           "    Sign in once at https://appleid.apple.com in Safari, then retry." % state)
            state = self._login_once(password)
        raise Fail("Sign-in did not settle after 10 attempts.")

    def _pet(self):
        try:
            return self.spd["t"]["com.apple.gs.idms.pet"]["token"]
        except (KeyError, TypeError):
            return None

    def _login_once(self, password):
        # One anisette fetch for both legs of the handshake. X-Apple-I-MD is a
        # one-time password, but the pair belongs to a single SRP exchange —
        # isideload reuses it here and refreshing mid-handshake risks Apple
        # rejecting the second leg.
        cpd = self.anisette.client_provided_data()
        srp = SRPClient()

        first = self.gs.plist_request(self.gs.url("gsService"), {
            "Header": {"Version": "1.0.1"},
            "Request": {
                "A2k": srp.a_pub,
                "cpd": cpd,
                "o": "init",
                "ps": ["s2k", "s2k_fo"],
                "u": self.email,
            },
        })

        password_key = srp_password_key(
            password, bytes(first["s"]), int(first["i"]), first["sp"]
        )
        proof = srp.process(self.email, password_key, bytes(first["s"]), bytes(first["B"]))

        second = self.gs.plist_request(
            self.gs.url("gsService"),
            {
                "Header": {"Version": "1.0.1"},
                "Request": {
                    "M1": proof,
                    "c": first["c"],
                    "cpd": cpd,
                    "o": "complete",
                    "u": self.email,
                },
            },
            extra_headers={"Connection": "close"},
        )

        if not srp.verify(bytes(second["M2"])):
            raise Fail("Apple's server proof did not verify. Refusing to continue.")

        self.spd = srp.decrypt_spd(bytes(second["spd"]))

        status = second.get("Status", {})
        au = status.get("au")
        if au == "trustedDeviceSecondaryAuth":
            return "device-2fa"
        if au == "secondaryAuth":
            return "sms-2fa"
        if au is None and status.get("hsc") == 409:
            # What an Android client gets: 409 (a second step is due) with no
            # step named, and a code already pushed to the trusted devices.
            return "device-2fa-sent"
        if au == "repair" or au is None:
            return "ok"
        return au

    def _identity_headers(self):
        headers = self.anisette.headers()
        identity = "%s:%s" % (self.spd["adsid"], self.spd["GsIdmsToken"])
        headers["X-Apple-Identity-Token"] = base64.b64encode(identity.encode()).decode()
        headers["X-Apple-I-MD-RINFO"] = self.anisette.routing_info
        return headers

    def _device_2fa(self, ask_code, send=True):
        """Ask for the trusted-device code and hand it to Apple. With `send`
        false Apple has pushed the code already, and says so in `sm`."""
        if send:
            headers = self.gs.mac_headers()
            headers.update(self._identity_headers())
            status, _, _ = request("GET", self.gs.url("trustedDeviceSecondaryAuth"),
                                   headers=headers)
            if status >= 400:
                raise Fail("Apple would not send a verification code (HTTP %d)." % status)
            info("A verification code has been sent to your trusted devices.")
        else:
            info(self.spd.get("sm") or "A verification code has been sent to your trusted devices.")

        code = ask_code("Two-factor code")
        headers = self.gs.base_headers()
        headers.update(self._identity_headers())
        headers["security-code"] = code
        status, _, body = request("GET", self.gs.url("validateCode"), headers=headers)
        if status >= 400:
            raise Fail("Apple rejected the verification code (HTTP %d)." % status)
        check_gs_error(plistlib.loads(body))

    def _sms_2fa(self, ask_code):
        headers = self.gs.mac_headers(plist=False)
        headers.update(self._identity_headers())
        status, _, _ = request("GET", self.gs.url("secondaryAuth"), headers=headers)
        if status >= 400:
            raise Fail("Apple would not send an SMS code (HTTP %d)." % status)
        info("A verification code has been sent by SMS.")

        code = ask_code("SMS code")
        headers = self.gs.mac_headers(plist=False)
        headers.update(self._identity_headers())
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json, text/javascript, */*; q=0.01"
        status, _, body = request(
            "POST", "https://gsa.apple.com/auth/verify/phone/securitycode",
            headers=headers,
            body=json.dumps({
                "securityCode": {"code": code},
                "phoneNumber": {"id": 1},
                "mode": "sms",
            }),
        )
        if status >= 400:
            detail = body.decode("utf-8", "replace")
            try:
                first = json.loads(detail)["serviceErrors"][0]
                detail = "%s — %s" % (first.get("title", ""), first.get("message", ""))
            except (ValueError, KeyError, IndexError):
                pass
            raise Fail("Apple rejected the SMS code: %s" % detail.strip())

    def app_token(self, app):
        """Exchange the sign-in for a service token — 'xcode.auth' is the one
        the developer portal accepts."""
        if not app.startswith("com.apple.gs."):
            app = "com.apple.gs." + app

        adsid = self.spd["adsid"]
        session_key = bytes(self.spd["sk"])
        checksum = hmac.new(session_key, digestmod=hashlib.sha256)
        checksum.update(b"apptokens")
        checksum.update(adsid.encode())
        checksum.update(app.encode())

        response = self.gs.plist_request(self.gs.url("gsService"), {
            "Header": {"Version": "1.0.1"},
            "Request": {
                "app": [app],
                "c": self.spd["c"],
                "checksum": checksum.digest(),
                "cpd": self.anisette.client_provided_data(),
                "o": "apptokens",
                "u": adsid,
                "t": self.spd["GsIdmsToken"],
            },
        })

        blob = bytes(response["et"])
        if len(blob) < 35 or blob[:3] != b"XYZ":
            raise Fail("App token came back in an unrecognised format.")
        decrypted = aes_gcm_decrypt(
            session_key, blob[3:19], blob[19:-16], blob[:3], blob[-16:]
        )
        token = loads_bare_plist(decrypted)
        if token.get("status-code") != 200:
            raise Fail("Apple refused the developer token (status %s)." % token.get("status-code"))
        return token["t"][app]["token"], adsid


# ----------------------------------------------------------------------------
# The developer portal (developerservices2 — the API Xcode itself uses)
# ----------------------------------------------------------------------------

def anisette_candidates(preferred):
    """The user's choice first, then the live community list, then the copy
    baked in here."""
    urls = [preferred]
    try:
        status, _, body = request("GET", ANISETTE_LIST_URL,
                                  headers={"User-Agent": "SideInstaller"}, timeout=15)
        if status == 200:
            for server in json.loads(body).get("servers", []):
                address = (server.get("address") or "").rstrip("/")
                if address.startswith("http"):
                    urls.append(address)
    except (OSError, ValueError, ssl.SSLError):
        pass
    urls.extend(BUNDLED_ANISETTE)

    seen = set()
    unique = []
    for url in urls:
        key = url.rstrip("/")
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def connect_anisette(url_hint, state_path):
    """Return (anisette, grandslam), both proven to work.

    Provisioning is inside the loop on purpose: a server that answers
    /v3/client_info happily will still drop the provisioning socket when it is
    busy, and that has to fail over to the next one like any other outage.
    """
    tried = []
    for url in anisette_candidates(url_hint)[:MAX_ANISETTE_TRIES]:
        try:
            anisette = Anisette(url, state_path)
            version = anisette.detect()
            gs = GrandSlam(anisette)

            if anisette.needs_provisioning():
                info("Provisioning with %s (first run only)…" % url)
                anisette.provision(gs)
            try:
                anisette.headers()
            except Fail:
                # A stored blob this server won't accept: throw it away and
                # provision again before giving up on the server.
                if anisette.version != 3 or not anisette.adi_pb:
                    raise
                warn("Stored anisette identity was rejected; provisioning again.")
                anisette.forget_provisioning()
                anisette.provision(gs)
                anisette.headers()

            ok("Anisette: %s (v%d)" % (url, version))
            return anisette, gs
        except (Fail, OSError, ssl.SSLError) as exc:
            tried.append("%s — %s" % (url, exc))
            warn("Anisette server unavailable: %s" % url)
    raise Fail("No anisette server answered. Tried:\n      " + "\n      ".join(tried))




# =============================================================================
#  BariSign replacement for upstream's openssl-based aes_cbc_decrypt()
#  CHANGED BY BARISIGN — see the header of this file.
# =============================================================================

def aes_cbc_decrypt(key, iv, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(data) + decryptor.finalize()
