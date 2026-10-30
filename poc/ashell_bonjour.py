#!/usr/bin/env python3
# =============================================================================
#  BariSign — Bonjour advertisement for pymobiledevice3 on a-Shell (MIT)
#
#  pymobiledevice3's own MDNSResponder opens raw multicast sockets. iOS only
#  lets an app advertise through the Bonjour daemon (DNSServiceRegister);
#  raw multicast needs an entitlement a-Shell does not have. This module is
#  the drop-in responder: same async-context-manager surface as
#  pymobiledevice3.bonjour.MDNSResponder, but registration goes through
#  libsystem_dnssd, exactly like poc/pair_probe.py already does.
#
#  It is used by poc/pair_install.py on the iPad. On a normal Linux/macOS
#  machine the original responder is left alone.
# =============================================================================

import ctypes
import os
import socket

SERVICE_TYPE = "_remotepairing-pairable-host._tcp"

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

REPLY = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32,
                         ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                         ctypes.c_void_p)


def is_ashell():
    """True on the a-Shell app (the only place the Bonjour shim is needed)."""
    return (os.environ.get("TERM_PROGRAM", "") == "a-Shell"
            or os.environ.get("BARISIGN_ASHELL") == "1")


def _load_dnssd():
    """Find the Bonjour C API, as pair_probe.py does."""
    candidates = (
        "libsystem_dnssd.dylib",
        "/usr/lib/system/libsystem_dnssd.dylib",
        "libdns_sd.dylib",
        "/usr/lib/libdns_sd.dylib",
    )
    errors = []
    for name in candidates:
        try:
            library = ctypes.CDLL(name)
            if hasattr(library, "DNSServiceRegister"):
                return library
        except OSError as exc:
            errors.append("%s (%s)" % (name, str(exc)[:40]))
    try:
        library = ctypes.CDLL(None)
        if hasattr(library, "DNSServiceRegister"):
            return library
    except OSError as exc:
        errors.append("process (%s)" % (str(exc)[:40],))
    raise RuntimeError("Bonjour (DNSServiceRegister) not reachable: %s"
                       % "; ".join(errors or ["no library"]))


def _txt_record(properties):
    payload = b""
    for key, value in properties.items():
        key = str(key).encode("utf-8")
        value = str(value).encode("utf-8")
        if len(key) > 255 or len(value) > 255:
            continue
        payload += bytes([len(key)]) + key + bytes([len(value)]) + value
    return payload


class BonjourResponder:
    """Minimal ``MDNSResponder`` replacement using the system Bonjour daemon.

    ``pymobiledevice3`` uses the object only as an async context manager:
    entering publishes the service, leaving withdraws it. The daemon answers
    PTR/SRV/TXT/A queries, so no packet loop runs in Python.
    """

    def __init__(self, service_type, instance_name, port, properties,
                 hostname=None, addresses=None):
        self.service_type = service_type.rstrip(".") or SERVICE_TYPE
        self.instance_name = properties.get("name") or instance_name
        self.port = int(port)
        self.properties = dict(properties or {})
        self._library = None
        self._ref = ctypes.c_void_p()
        self._callback = None
        self._error = 0

    def _registered(self, _ref, _flags, error, _name, _type, _domain, _context):
        if error:
            self._error = error

    def _register(self):
        library = _load_dnssd()
        library.DNSServiceRegister.argtypes = [
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_uint16, ctypes.c_uint16, ctypes.c_char_p, REPLY,
            ctypes.c_void_p]
        library.DNSServiceRegister.restype = ctypes.c_int32
        library.DNSServiceRefDeallocate.argtypes = [ctypes.c_void_p]

        txt = _txt_record(self.properties)
        self._callback = REPLY(self._registered)
        error = library.DNSServiceRegister(
            ctypes.byref(self._ref), 0, 0,
            self.instance_name.encode("utf-8"),
            self.service_type.encode("utf-8"), None, None,
            socket.htons(self.port), len(txt), txt, self._callback, None)
        if error:
            raise RuntimeError("DNSServiceRegister: %d (%s)"
                               % (error, _kDNSServiceErr.get(error, "see dns_sd.h")))
        if self._error:
            raise RuntimeError("DNSServiceRegister callback: %d (%s)"
                               % (self._error, _kDNSServiceErr.get(self._error, "see dns_sd.h")))
        self._library = library

    async def __aenter__(self):
        self._register()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._library is not None and self._ref:
            try:
                self._library.DNSServiceRefDeallocate(self._ref)
            except Exception:                                   # noqa: BLE001
                pass
        self._ref = ctypes.c_void_p()
        self._callback = None
        self._library = None


def patch():
    """Make pymobiledevice3's pairing host advertise via Bonjour on a-Shell."""
    if not is_ashell():
        return False
    try:
        from pymobiledevice3.remote import tunnel_service
    except Exception:                                           # noqa: BLE001
        return False
    tunnel_service.MDNSResponder = BonjourResponder
    return True
