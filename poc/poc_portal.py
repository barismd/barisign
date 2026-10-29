#!/usr/bin/env python3
# =============================================================================
#  BariSign — developer portal (MIT)
#  STATUS: UNTESTED against Apple. Written from SideInstaller's verified code,
#          which is vendored (under its own licence) in third_party/sideinstaller/.
#
#  What it does, in order, with the free Apple ID you signed in with:
#    1. lists your teams and picks one
#    2. registers a UDID with that team (so the device may run your builds)
#    3. creates an App ID for the bundle identifier you name
#    4. creates (or reuses) a development certificate for a key generated HERE
#    5. downloads the matching provisioning profile
#
#  It never revokes anything without asking. Nothing is installed.
#
#  USAGE (in a-Shell, from this repository):
#      python3 -u poc/poc_portal.py --email YOU@example.com --udid <UDID>
#      python3 -u poc/poc_portal.py --email YOU@example.com --udid <UDID> \
#              --bundle-id com.SideStore.SideStore --device-name "My iPhone"
#
#  Get the UDID with `sh bin/barisign udid`. The bundle identifier must match the app
#  you later want to install (see variants.json).
# =============================================================================

import os
import re
import sys
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.join(_HERE, "..", "third_party", "sideinstaller"),
                   os.path.join(_HERE, "third_party", "sideinstaller")):
    if os.path.isfile(os.path.join(_candidate, "portal.py")):
        sys.path.insert(0, _candidate)
        break
sys.path.insert(0, _HERE)

# Importing poc_login also configures the TLS trust (Apple roots + CA bundle),
# which this script needs for every developer-portal call.
import poc_login                                              # noqa: E402
import signin                                                 # noqa: E402
try:
    import portal                                             # noqa: E402
except ImportError as exc:                                    # noqa: BLE001
    print("Could not import the vendored developer-portal code: %s" % exc)
    print("third_party/sideinstaller/portal.py is missing or incomplete.")
    print("Fix it by re-running the installer:  sh install.sh")
    sys.exit(2)
from signin import AppleAccount, Fail, connect_anisette         # noqa: E402
from portal import (DeveloperSession, ensure_app_id, ensure_device,               # noqa: E402
                    ensure_private_key, obtain_certificate, pick_team,
                    download_profile, profile_plist)

POC_VERSION = "0.1.0"


def _hr(label):
    print()
    print("---- %s %s" % (label, "-" * max(0, 56 - len(label))))


def _usage():
    print("usage: python3 -u poc/poc_portal.py --email EMAIL --udid UDID \\")
    print("          [--bundle-id ID] [--device-name NAME] [--app-name NAME]")
    print()
    print("  --email        the free Apple ID to sign in with (same one as poc_login)")
    print("  --udid         this device's UDID (from sh bin/barisign udid)")
    print("  --bundle-id    defaults to com.barisign.smoke for a first test")
    print("  --device-name  name shown in the Apple developer portal")
    print("  --app-name     name for the App ID")


UDID_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{24})$")


def _normalise_udid(text):
    """Same rule as `barisign.py udid`: 40 hex, or 8+16 with an optional dash."""
    cleaned = re.sub(r"[\s\-]", "", (text or "")).lower()
    if not UDID_PATTERN.match(cleaned):
        return ""
    if len(cleaned) == 40:
        return cleaned
    return "%s-%s" % (cleaned[:8], cleaned[8:])


def _parse(argv):
    values = {"email": None, "udid": None, "bundle_id": "com.barisign.smoke",
              "device_name": "BariSign test device", "app_name": "BariSign test"}
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        key = {"--email": "email", "--udid": "udid", "--bundle-id": "bundle_id",
               "--device-name": "device_name", "--app-name": "app_name"}.get(arg)
        if key is None:
            print("unknown argument: %s" % arg)
            return None
        if not rest:
            print("%s needs a value" % arg)
            return None
        values[key] = rest.pop(0)
    if not values["email"] or not values["udid"]:
        return None
    values["udid"] = _normalise_udid(values["udid"])
    if not values["udid"]:
        print("--udid is not a UDID: 40 hex characters, or 24 with a dash after the first 8.")
        return None
    return values


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        _usage()
        return 0
    options = _parse(argv)
    if options is None:
        _usage()
        return 2

    # a-Shell can only write inside ~/Documents, so point every writer there
    state_dir = poc_login.POC_STATE
    os.makedirs(state_dir, exist_ok=True)
    portal.STORAGE = state_dir
    signin.STORAGE = state_dir

    _hr("runtime")
    print("poc_portal.py ver : %s" % POC_VERSION)
    print("signin.py ver     : %s" % getattr(signin, "VERSION", "(none)"))
    print("writable state    : %s" % state_dir)

    _hr("Apple sign-in")
    anisette, gs = connect_anisette("", os.path.join(state_dir, "anisette.json"))
    account = AppleAccount(options["email"], gs, anisette)

    password = poc_login._read_password("Password (not shown, not stored): ")
    if not password:
        raise Fail("No password entered.")

    def ask_code(label):
        try:
            entered = input("%s: " % label)
        except EOFError:
            entered = ""
        return "".join(ch for ch in entered if ch.isdigit())

    account.login(password, ask_code)
    password = None
    print("  signed in.")

    _hr("developer portal")
    started = time.time()
    token, adsid = account.app_token("xcode.auth")
    session = DeveloperSession(account, token, adsid)

    team = pick_team(session)
    print("  team            : %s (%s)" % (team.get("name", "?"), team["teamId"]))

    ensure_device(session, team, options["udid"], options["device_name"])
    print("  device          : %s registered" % options["udid"])

    bundle_id = "%s.%s" % (options["bundle_id"], team["teamId"])
    app_id = ensure_app_id(session, team, bundle_id, options["app_name"])
    print("  App ID          : %s" % bundle_id)

    key_path = ensure_private_key(options["email"])
    cert_path, reused = obtain_certificate(session, team, key_path, state_dir)
    print("  certificate     : %s (%s)" % (cert_path, "reused" if reused else "created"))
    print("  private key     : %s" % key_path)

    profile = download_profile(session, team, app_id)
    profile_path = os.path.join(state_dir, "profile.mobileprovision")
    with open(profile_path, "wb") as handle:
        handle.write(profile)
    print("  profile         : %s" % profile_path)

    _hr("the profile Apple gave back")
    try:
        parsed = profile_plist(profile)
        print("  name            : %s" % parsed.get("Name", "?"))
        print("  uuid            : %s" % parsed.get("UUID", "?"))
        print("  expires         : %s" % parsed.get("ExpirationDate", "?"))
        devices = parsed.get("ProvisionedDevices") or []
        print("  devices in it   : %d%s" % (len(devices),
              " (ours included: %s)" % (options["udid"] in devices if devices else "n/a")))
        entitlements = parsed.get("Entitlements") or {}
        print("  application-id  : %s" % entitlements.get("application-identifier", "?"))
        print("  get-task-allow  : %s" % entitlements.get("get-task-allow", "?"))
    except Exception as exc:                                  # noqa: BLE001
        print("  could not parse the profile: %r" % (exc,))

    _hr("portal")
    print("  team      %s (%s)" % (team.get("name", "?"), team["teamId"]))
    print("  app id    %s" % bundle_id)
    print("  cert      %s" % ("reused" if reused else "created"))
    print("  files     key.pem, cert.pem, profile.mobileprovision")
    print("  took      %.0f s" % (time.time() - started))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Fail as exc:
        print()
        print("FAILED: %s" % exc)
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    except Exception:                                         # noqa: BLE001
        print()
        print("UNEXPECTED ERROR:")
        traceback.print_exc()
        sys.exit(4)
