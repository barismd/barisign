#!/usr/bin/env python3
# =============================================================================
#  BariSign — pair + install from the signer device (MIT wrapper)
#
#  Run on the SIGNER device's a-Shell, with the TARGET device on the same Wi-Fi:
#
#      sh bin/pair
#
#  The signer advertises itself as a pairable host; on the target open
#  Settings > Privacy & Security > Developer Mode > Paired Macs (or
#  Settings > Developer > Paired Macs), pick "BariSign", and enter the code
#  printed here. The signer then opens the userspace tunnel and installs the
#  signed IPA with installation_proxy. No PC, no SideInstaller, no certificate.
#  Either device may be an iPhone or an iPad.
#
#  pymobiledevice3 is GPL-3.0-or-later and is NOT part of this repository: the
#  first run installs it into state/pymd3 on the device. This wrapper contains
#  no pymobiledevice3 code and is MIT, like the rest of BariSign.
#
#  STATUS: the pymobiledevice3 path is not yet verified on a-Shell. The script
#  stops with a precise message at the first gate that fails instead of
#  pretending. See docs/PAIRING_INSTALL.md.
# =============================================================================

import asyncio
import glob
import os
import shutil
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE_DIR = os.environ.get("BARISIGN_STATE_DIR") or os.path.join(ROOT, "state")
PIP_TARGET = os.path.join(STATE_DIR, "pymd3")

PURE_DEPS = [
    "qh3<2",
    "pmd-pytcp",
    "pmd-net-addr",
    "pmd-net-proto",
    "bytestruct",
    "opack2",
    "ifaddr",
    "hyperframe",
    "typing_extensions",
    "packaging",
    "pygnuutils",
    "questionary",
    "prompt_toolkit",
    "rich",
    "requests",
]

PYTUN_STUB = '''\
class TunTapDevice:
    """Stub: the userspace tunnel never creates a kernel utun."""
    def __init__(self, *args, **kwargs):
        raise RuntimeError("kernel tun is unavailable; use the userspace tunnel")
'''

PSUTIL_STUB = '''\
class Process:
    pass


class ZombieProcess(Exception):
    pass


class NoSuchProcess(Exception):
    pass


class AccessDenied(Exception):
    pass


def process_iter(*args, **kwargs):
    return []


def virtual_memory():
    class Memory:
        total = 0
        available = 0
        percent = 0
    return Memory()
'''


def _say(text):
    print(text, flush=True)


def _add_path():
    for path in (PIP_TARGET, HERE):
        if path not in sys.path:
            sys.path.insert(0, path)


def _write_stubs():
    os.makedirs(PIP_TARGET, exist_ok=True)
    for name, body in (("pytun_pmd3.py", PYTUN_STUB), ("psutil.py", PSUTIL_STUB)):
        path = os.path.join(PIP_TARGET, name)
        if not os.path.isfile(path):
            with open(path, "w") as handle:
                handle.write(body)


def _pip(args):
    command = [sys.executable, "-m", "pip", "install", "--upgrade",
               "--target", PIP_TARGET] + args
    _say("  pip %s" % " ".join(args))
    result = subprocess.run(command)
    return result.returncode == 0


def setup():
    """One-time: install pymobiledevice3 and its pure-Python dependencies."""
    os.makedirs(PIP_TARGET, exist_ok=True)
    _say("  first run: installing the pairing engine into state/pymd3")
    ok = _pip(["--no-deps", "pymobiledevice3"])
    ok = _pip(PURE_DEPS) and ok
    _write_stubs()
    _add_path()
    return ok


def _import_engine(quiet=False):
    _add_path()
    _write_stubs()
    try:
        from pymobiledevice3.remote.tunnel_service import PairableHostInfo, serve_pairable_host
        from pymobiledevice3.remote.userspace_tunnel import establish_userspace_rsd
        from pymobiledevice3.services.installation_proxy import InstallationProxyService
        return PairableHostInfo, serve_pairable_host, establish_userspace_rsd, InstallationProxyService
    except Exception as exc:                                    # noqa: BLE001
        if not quiet:
            _say("  ! pairing engine not usable: %s: %s" % (type(exc).__name__, exc))
        return None


def check():
    """Report the gates without touching the device."""
    import platform
    _say("  python      %s" % platform.python_version())
    try:
        import cryptography
        _say("  cryptography %s" % getattr(cryptography, "__version__", "?"))
    except Exception as exc:                                    # noqa: BLE001
        _say("  ! cryptography: %s" % exc)
    engine = _import_engine()
    if engine is None:
        _say("  ! run 'sh bin/pair setup' first (or run without --check to install)")
        return 1
    _say("  engine      imported")
    try:
        from ashell_bonjour import is_ashell
    except Exception:                                           # noqa: BLE001
        is_ashell = lambda: False                              # noqa: E731
    _say("  a-Shell     %s" % ("yes (Bonjour shim active)" if is_ashell() else "no (raw mDNS)"))
    return 0


def find_ipa(explicit=None):
    if explicit:
        return explicit if os.path.isfile(explicit) else ""
    candidates = []
    for pattern in (os.path.join(STATE_DIR, "*-signed.ipa"),
                    os.path.join(ROOT, "*-signed.ipa")):
        candidates.extend(glob.glob(pattern))
    candidates = [path for path in candidates if os.path.isfile(path)]
    if not candidates:
        return ""
    return max(candidates, key=os.path.getmtime)


def _patch_bonjour():
    try:
        from ashell_bonjour import patch
    except Exception:                                           # noqa: BLE001
        return False
    try:
        return patch()
    except Exception as exc:                                    # noqa: BLE001
        _say("  ! Bonjour shim failed: %s" % exc)
        return False


async def _run(ipa, pair_only=False, udid=""):
    engine = _import_engine()
    if engine is None:
        return 2
    PairableHostInfo, serve_pairable_host, establish_userspace_rsd, InstallationProxyService = engine
    _patch_bonjour()

    if not pair_only and not udid and not ipa:
        _say("  ! no signed IPA found")
        _say("    sign on the signer first (sh bin/barisign sign),")
        _say("    or pass --ipa FILE.")
        return 2

    found_udid = udid
    if udid and not pair_only:
        _say("  using        already paired %s" % udid)
    else:
        host = PairableHostInfo(name="BariSign", model="Mac17,7")

        def pin(code):
            _say("")
            _say("  target pairing code:  %s" % code)
            _say("")

        def waiting(seconds):
            _say("  waiting for the target to pair (%ds)" % int(seconds))

        _say("")
        _say("  signer: advertising as BariSign")
        _say("  target: Settings > Privacy & Security > Developer Mode > Paired Macs")
        _say("          (or Settings > Developer > Paired Macs) -> BariSign -> Pair")
        _say("")
        try:
            result = await serve_pairable_host(host, pin_callback=pin,
                                               waiting_callback=waiting)
        except Exception as exc:                                # noqa: BLE001
            _say("  ! pairing failed: %s" % exc)
            return 2

        device = getattr(result, "peer_device", None)
        name = getattr(device, "name", "device")
        found_udid = getattr(device, "udid", "") or udid
        _say("  paired       %s  %s" % (name, found_udid))
        record = getattr(result, "record_path", "")
        _say("  record       %s" % (record or "?"))
        if record and os.path.isfile(record):
            # Same record under the names AltStore and SideStore read. The user
            # can import the SideStore one, or copy the AltStore one with AFC.
            for target in ("ALTPairingFile.plist",
                           "ALTPairingFile.mobiledevicepairing",
                           "PairingFile_RemoteRP.plist"):
                try:
                    shutil.copyfile(record, os.path.join(STATE_DIR, target))
                except OSError:
                    pass
            _say("  pairing file also saved in state/ (AltStore + SideStore names)")
    if pair_only:
        return 0
    if not ipa:
        _say("  ! no signed IPA found")
        return 2

    _say("  installing   %s" % ipa)
    try:
        rsd = await establish_userspace_rsd(serial=found_udid or None)
    except Exception as exc:                                    # noqa: BLE001
        _say("  ! tunnel failed: %s" % exc)
        return 3

    def progress(percent, *args):
        sys.stdout.write("\r  install      %3d%%" % int(percent))
        sys.stdout.flush()

    try:
        async with InstallationProxyService(lockdown=rsd) as service:
            await service.install_from_local(ipa, developer=True, handler=progress)
        _say("")
        _say("  installed")
        return 0
    except Exception as exc:                                    # noqa: BLE001
        _say("")
        _say("  ! install failed: %s" % exc)
        return 3


def main(argv):
    explicit_ipa = ""
    pair_only = False
    udid = ""
    if "--help" in argv or "-h" in argv:
        _say("sh bin/pair                 pair the target and install the newest -signed.ipa")
        _say("sh bin/pair --ipa FILE      use a specific signed IPA")
        _say("sh bin/pair --pair-only     pair only; print the pairing record")
        _say("sh bin/pair --udid UDID     install to an already paired device")
        _say("sh bin/pair --check         check python, cryptography and the engine")
        _say("sh bin/pair --setup         install the engine only")
        return 0
    if "--ipa" in argv:
        position = argv.index("--ipa") + 1
        if position < len(argv):
            explicit_ipa = argv[position]
    if "--udid" in argv:
        position = argv.index("--udid") + 1
        if position < len(argv):
            udid = argv[position]
    pair_only = "--pair-only" in argv
    debug = "--debug" in argv

    if "--check" in argv:
        return check()
    if "--setup" in argv:
        return 0 if setup() else 1

    if _import_engine(quiet=True) is None:
        if not setup():
            _say("  ! setup failed; see the message above")
            return 1
        if _import_engine() is None:
            return 1

    ipa = find_ipa(explicit_ipa)
    try:
        return asyncio.run(_run(ipa, pair_only=pair_only, udid=udid))
    except KeyboardInterrupt:
        _say("")
        return 130
    except Exception as exc:                                    # noqa: BLE001
        _say("  ! %s" % exc)
        if debug:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
