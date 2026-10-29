#!/usr/bin/env python3
# =============================================================================
#  BariSign — pairing discovery probe (UNTESTED ON DEVICE)
#
#  Answers one question before any pairing code is written: can a-Shell
#  advertise the Bonjour service an iPhone looks for under
#      Settings > Privacy & Security > Developer Mode > "Pair with ..."?
#
#  It registers `_remotepairing-pairable-host._tcp` with the same TXT keys
#  SideInstaller/StikPair use, listens on port 49152, and prints any connection
#  the phone makes. A connection means the phone found us and dialled; that is
#  the green light for implementing the pairing responder (SRP-6a accessory).
#  No pairing is performed and nothing is written to disk.
#
#  Run it on the device that stays in front of you (an iPad or a second iPhone
#  with a-Shell), on the same Wi-Fi as the phone whose Developer Mode is off.
#  Then, on that phone:
#      Settings > Privacy & Security > Developer Mode > "Pair with <name>"
#  and watch this window. On the same phone, a-Shell is suspended when Settings
#  comes to the front, so the host is best run on another device.
#
#  Usage:
#      python3 -u poc/pair_probe.py                 # 180 seconds
#      python3 -u poc/pair_probe.py --seconds 600
#      python3 -u poc/pair_probe.py --name "BariSign"
#
#  STATUS: UNTESTED ON DEVICE. The ctypes dhcp/mDNS calls and the Local Network
#  permission are exactly what this probe is meant to find out. On a system
#  without the Bonjour library it reports what it could not load and stops.
# =============================================================================

import base64
import ctypes
import os
import select
import socket
import sys
import threading
import time

SERVICE_TYPE = b"_remotepairing-pairable-host._tcp"
SERVICE_PORT = 49152

# The TXT keys Apple's pairable-host record carries (see idevice's responder.rs).
WIRE_VERSION = "19"
MIN_VERSION = "17"
MODEL = "Mac17,7"

_kDNSServiceErr = {
    0: "ok",
    -65537: "unknown error",
    -65539: "out of memory",
    -65540: "bad parameter",
    -65548: "name conflict",
    -65549: "invalid",
    -65557: "policy denied (Local Network permission?)",
    -65562: "not authorised",
    -65563: "unsupported",
}

# void (*DNSServiceRegisterReply)(DNSServiceRef, DNSServiceFlags,
#     DNSServiceErrorType, const char *name, const char *regtype,
#     const char *domain, void *context);
REPLY = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32,
                         ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                         ctypes.c_void_p)


def _load_dnssd():
    """Find the Bonjour C API. iOS keeps it in libsystem_dnssd."""
    candidates = (
        "libsystem_dnssd.dylib",
        "/usr/lib/system/libsystem_dnssd.dylib",
        "libdns_sd.dylib",
        "/usr/lib/libdns_sd.dylib",
        "dns_sd.dll",
    )
    errors = []
    for name in candidates:
        try:
            library = ctypes.CDLL(name)
            if hasattr(library, "DNSServiceRegister"):
                return library, name
        except OSError as exc:
            errors.append("%s (%s)" % (name, str(exc)[:40]))
    # Last resort: symbols already in the process (libSystem is always there).
    try:
        library = ctypes.CDLL(None)
        if hasattr(library, "DNSServiceRegister"):
            return library, "the running process"
    except OSError as exc:
        errors.append("process (%s)" % (str(exc)[:40],))
    raise RuntimeError("Bonjour (DNSServiceRegister) not reachable: %s"
                       % "; ".join(errors or ["no library"]))


def _txt_record(items):
    payload = b""
    for key, value in items:
        key = key.encode("utf-8")
        value = value.encode("utf-8")
        if len(key) > 255 or len(value) > 255:
            continue
        payload += bytes([len(key)]) + key + bytes([len(value)]) + value
    return payload


class _Listener:
    """Accept the phone's connection on 49152 and show what it sends."""

    def __init__(self, port):
        self.port = port
        self.seen = threading.Event()
        self._stop = threading.Event()
        self._sockets = []
        for family, address in ((socket.AF_INET, ("0.0.0.0", port)),
                                (socket.AF_INET6, ("::", port, 0, 0))):
            try:
                sock = socket.socket(family, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if family == socket.AF_INET6:
                    try:
                        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    except OSError:
                        pass
                sock.bind(address)
                sock.listen(4)
                sock.settimeout(1.0)
                self._sockets.append(sock)
            except OSError:
                try:
                    sock.close()
                except Exception:                               # noqa: BLE001
                    pass
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        for sock in self._sockets:
            try:
                sock.close()
            except OSError:
                pass

    def _run(self):
        while not self._stop.is_set():
            ready, _, _ = select.select(self._sockets, [], [], 0.5)
            if not ready:
                continue
            for sock in ready:
                try:
                    connection, peer = sock.accept()
                except OSError:
                    continue
                self.seen.set()
                print("  CONNECTION from %s:%s" % (peer[0], peer[1]))
                connection.settimeout(3.0)
                try:
                    first = connection.recv(64)
                except OSError:
                    first = b""
                if first:
                    print("  first bytes    %s" % " ".join("%02x" % b for b in first[:24]))
                    if first.startswith(b"RPPairing"):
                        print("  this is the pairing client: the phone found us")
                else:
                    print("  the peer connected and said nothing yet")
                try:
                    connection.close()
                except OSError:
                    pass


def main(argv):
    seconds = 180
    name = "BariSign"
    identifier = base64.b16encode(os.urandom(8)).decode("ascii").lower()
    if "--seconds" in argv:
        try:
            seconds = max(10, int(argv[argv.index("--seconds") + 1]))
        except (IndexError, ValueError):
            pass
    if "--name" in argv:
        try:
            name = argv[argv.index("--name") + 1]
        except IndexError:
            pass

    print("pair probe")
    try:
        library, source = _load_dnssd()
    except RuntimeError as exc:
        print("  ! %s" % exc)
        print("  this device cannot advertise over Bonjour from Python; stop here.")
        return 1

    auth_tag = base64.b64encode(os.urandom(16)).decode("ascii")
    txt = _txt_record([
        ("name", name), ("identifier", identifier), ("authTag", auth_tag),
        ("model", MODEL), ("flags", "1"), ("ver", WIRE_VERSION),
        ("minVer", MIN_VERSION),
    ])

    library.DNSServiceRegister.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_uint16, ctypes.c_uint16, ctypes.c_char_p, REPLY, ctypes.c_void_p]
    library.DNSServiceRegister.restype = ctypes.c_int32
    library.DNSServiceRefSockFD.argtypes = [ctypes.c_void_p]
    library.DNSServiceRefSockFD.restype = ctypes.c_int
    library.DNSServiceProcessResult.argtypes = [ctypes.c_void_p]
    library.DNSServiceProcessResult.restype = ctypes.c_int32
    library.DNSServiceRefDeallocate.argtypes = [ctypes.c_void_p]

    holder = {"ref": ctypes.c_void_p()}

    def registered(ref, flags, error, service_name, regtype, domain, context):
        if error:
            print("  ! registration failed: %d (%s)"
                  % (error, _kDNSServiceErr.get(error, "see dns_sd.h")))
            return
        print("  registered     %s.%s%s"
              % (service_name.decode("utf-8", "replace"),
                 regtype.decode("utf-8", "replace"),
                 domain.decode("utf-8", "replace")))

    callback = REPLY(registered)

    listener = _Listener(SERVICE_PORT)
    listener.start()

    error = library.DNSServiceRegister(
        ctypes.byref(holder["ref"]), 0, 0, name.encode("utf-8"), SERVICE_TYPE,
        None, None, socket.htons(SERVICE_PORT), len(txt), txt, callback, None)
    if error:
        print("  ! DNSServiceRegister: %d (%s)"
              % (error, _kDNSServiceErr.get(error, "see dns_sd.h")))
        print("  if this says 0 but nothing is listed, the sandbox refused quietly.")
        listener.stop()
        return 1

    print("  source         %s" % source)
    print("  advertising    %s as \"%s\"" % (SERVICE_TYPE.decode(), name))
    print("  port           %d" % SERVICE_PORT)
    print("  waiting        %ds for the phone to connect" % seconds)
    print()
    print("  on the iPhone: Settings > Privacy & Security > Developer Mode")
    print("                 then \"Pair with %s\"" % name)
    print()
    print("  Ctrl-C stops.")

    socket_fd = library.DNSServiceRefSockFD(holder["ref"])
    deadline = time.time() + seconds
    last_note = time.time()
    try:
        while time.time() < deadline and not listener.seen.is_set():
            try:
                ready, _, _ = select.select([socket_fd], [], [], 1.0)
            except (OSError, ValueError):
                break
            if ready:
                library.DNSServiceProcessResult(holder["ref"])
            if time.time() - last_note >= 30:
                last_note = time.time()
                print("  still waiting  %ds left" % int(deadline - time.time()))
    except KeyboardInterrupt:
        print()
    finally:
        listener.stop()
        try:
            library.DNSServiceRefDeallocate(holder["ref"])
        except Exception:                                       # noqa: BLE001
            pass

    print()
    if listener.seen.is_set():
        print("RESULT  the phone connected: Bonjour works from a-Shell here")
        return 0
    print("RESULT  no connection. Either it was not listed in Settings, or the")
    print("        service was refused. Share this output before writing more.")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print()
        sys.exit(130)
