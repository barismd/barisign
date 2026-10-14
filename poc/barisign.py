#!/usr/bin/env python3
# =============================================================================
#  BariSign — one command for every stage (MIT)
#  STATUS: stage 1 and 2 are verified on device; stage 3 (portal) is written but
#          untested against Apple; stage 4 (the signer) does not exist yet.
#
#  Every stage shares its result with the next through a small state file, so you
#  never retype a UDID or an Apple ID.
#
#      python3 -u poc/barisign.py            guided run, start to finish
#      python3 -u poc/barisign.py check      runtime + anisette + Apple TLS
#      python3 -u poc/barisign.py udid       stage 1 - read the UDID
#      python3 -u poc/barisign.py login      stage 2 - sign in to Apple
#      python3 -u poc/barisign.py portal     stage 3 - UDID + certificate + profile
#      python3 -u poc/barisign.py status     show what is already known
#      python3 -u poc/barisign.py forget     clear the saved state (not the CA)
# =============================================================================

import json
import os
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

import poc_login                                              # noqa: E402  (sets up TLS trust)
import signin                                                 # noqa: E402
import portal                                                 # noqa: E402
import udid_share                                             # noqa: E402
from signin import AppleAccount, Fail, connect_anisette        # noqa: E402
from portal import (DeveloperSession, ensure_app_id, ensure_device,               # noqa: E402
                    ensure_private_key, obtain_certificate, pick_team,
                    download_profile, profile_plist)

VERSION = "0.1.0"
STATE_DIR = poc_login.POC_STATE
STATE_FILE = os.path.join(STATE_DIR, "barisign.json")
VARIANTS_FILE = os.path.join(_HERE, "..", "variants.json")


# ---------------------------------------------------------------------------
# state: what the stages share with each other
# ---------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE) as handle:
            return json.load(handle)
    except Exception:                                         # noqa: BLE001
        return {}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as handle:
        json.dump(state, handle, indent=2)


def update_state(**values):
    state = load_state()
    state.update(values)
    save_state(state)
    return state


def load_variants():
    try:
        with open(VARIANTS_FILE) as handle:
            return json.load(handle)["variants"]
    except Exception:                                         # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _hr(label):
    print()
    print("---- %s %s" % (label, "-" * max(0, 56 - len(label))))


def _ask(prompt, default=None):
    entered, worked = poc_login._read_line(prompt, mask=False)
    if not worked:
        try:
            entered = input("")
        except EOFError:
            entered = ""
    entered = (entered or "").strip()
    return entered or (default or "")


def _yes(prompt, default=False):
    answer = _ask("%s [%s] " % (prompt, "Y/n" if default else "y/N")).lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _sign_in(email):
    """The one place that asks for the Apple ID and password."""
    _hr("Apple sign-in")
    anisette, gs = connect_anisette("", os.path.join(STATE_DIR, "anisette.json"))
    account = AppleAccount(email, gs, anisette)
    password = poc_login._read_password("Password (not shown, not stored): ")
    if not password:
        raise Fail("No password entered.")

    def ask_code(label):
        entered, worked = poc_login._read_line("%s: " % label, mask=False)
        if not worked:
            entered = signin.ask(label)
        return "".join(ch for ch in entered if ch.isdigit())

    account.login(password, ask_code)
    password = None
    print("  signed in.")
    return account


def _portal(account, email, udid, bundle_id, device_name, app_name):
    """Register the device, get a certificate and a provisioning profile."""
    _hr("developer portal")
    started = time.time()
    token, adsid = account.app_token("xcode.auth")
    session = DeveloperSession(account, token, adsid)

    team = pick_team(session)
    print("  team            : %s (%s)" % (team.get("name", "?"), team["teamId"]))

    ensure_device(session, team, udid, device_name)
    print("  device          : %s registered" % udid)

    full_bundle_id = "%s.%s" % (bundle_id, team["teamId"])
    app_id = ensure_app_id(session, team, full_bundle_id, app_name)
    print("  App ID          : %s" % full_bundle_id)

    key_path = ensure_private_key(email)
    cert_pem, reused = obtain_certificate(session, team, key_path, STATE_DIR)
    print("  certificate     : %s" % ("reused" if reused else "created"))
    print("  private key     : %s" % key_path)

    profile = download_profile(session, team, app_id)
    profile_path = os.path.join(STATE_DIR, "profile.mobileprovision")
    with open(profile_path, "wb") as handle:
        handle.write(profile)
    print("  profile         : %s" % profile_path)

    _hr("the profile Apple gave back")
    try:
        parsed = profile_plist(profile)
        devices = parsed.get("ProvisionedDevices") or []
        print("  name            : %s" % parsed.get("Name", "?"))
        print("  expires         : %s" % parsed.get("ExpirationDate", "?"))
        print("  devices in it   : %d  (ours included: %s)"
              % (len(devices), udid in devices))
        entitlements = parsed.get("Entitlements") or {}
        print("  application-id  : %s" % entitlements.get("application-identifier", "?"))
    except Exception as exc:                                  # noqa: BLE001
        print("  could not parse the profile: %r" % (exc,))
    print("  took %.0f s" % (time.time() - started))

    return {"team_id": team["teamId"], "bundle_id": full_bundle_id,
            "profile": profile_path, "key": key_path}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_check(_argv):
    """Everything that can be checked without an Apple ID."""
    passed = poc_login._selftest()
    _hr("result")
    print("CHECK: %s" % ("PASS - safe to continue" if passed == 0 else "see the failures above"))
    return passed


def cmd_status(_argv):
    state = load_state()
    _hr("what BariSign already knows")
    print("  Apple ID        : %s" % (state.get("email") or "(not set - run: login)"))
    print("  UDID            : %s" % (state.get("udid") or "(not set - run: udid)"))
    print("  variant         : %s" % (state.get("variant") or "(not chosen)"))
    print("  bundle id       : %s" % (state.get("bundle_id") or "(not set)"))
    print("  team            : %s" % (state.get("team_id") or "(not set)"))
    print("  profile         : %s" % (state.get("profile") or "(not downloaded)"))
    ca_cert, _ca_key = (None, None)
    try:
        ca_cert, _ca_key = udid_share.load_or_create_ca()
        print("  local CA        : \"BariSign CA %s\" stored next to poc/udid_share.py"
              % udid_share.ca_fingerprint(ca_cert)[:11])
        print("                    (install and trust it ONCE on the other device)")
    except Exception:                                         # noqa: BLE001
        print("  local CA        : (not created yet)")
    print()
    print("  state file      : %s" % STATE_FILE)
    return 0


def cmd_forget(_argv):
    if os.path.isfile(STATE_FILE):
        os.remove(STATE_FILE)
        print("Forgot the saved Apple ID, UDID, variant and paths.")
    else:
        print("Nothing to forget.")
    print("The local CA is kept on purpose (so devices stay trusted).")
    return 0


def cmd_udid(argv):
    """Stage 1. `--udid-only` skips the CA once it is trusted."""
    udid_only = "--udid-only" in argv
    _hr("stage 1 - read the UDID")
    udid_share.sys.argv = ["udid_share.py"] + (["--udid-only"] if udid_only else [])
    code = udid_share.main()
    if code == 0 and getattr(udid_share, "LAST_UDID", None):
        update_state(udid=udid_share.LAST_UDID)
        _hr("saved")
        print("  UDID %s is now remembered; the portal will use it." % udid_share.LAST_UDID)
        return 0
    return code or 1


def cmd_login(argv):
    """Stage 2: sign in and remember the account."""
    email = argv[0] if argv and not argv[0].startswith("-") else _ask("Apple ID (email): ")
    if not email:
        print("No Apple ID given.")
        return 2
    account = _sign_in(email)
    _hr("result")
    print("LOGIN: ok. Nothing was registered and nothing was installed.")
    update_state(email=email)
    print("  Apple ID remembered; the portal will reuse it.")
    if getattr(account, "spd", None):
        try:
            name = account.spd.get("fn", "")
            if name:
                print("  signed in as %s" % name)
        except Exception:                                     # noqa: BLE001
            pass
    return 0


def cmd_portal(argv):
    """Stage 3: device registration, certificate, provisioning profile."""
    state = load_state()

    email = state.get("email") or _ask("Apple ID (email): ")
    if not email:
        print("No Apple ID. Run 'barisign.py login' first.")
        return 2

    variant = state.get("variant") or ""
    bundle_id = state.get("bundle_id") or ""
    if "--bundle-id" in argv:
        bundle_id = argv[argv.index("--bundle-id") + 1]
    if not bundle_id:
        variants = load_variants()
        if variants:
            _hr("which variant should the profile be for?")
            for index, item in enumerate(variants, 1):
                print("  %d) %-38s %s" % (index, item["name"], item.get("bundle_id", "")))
            choice = _ask("Choose [1-%d]: " % len(variants), "1")
            if choice.isdigit() and 1 <= int(choice) <= len(variants):
                variant = variants[int(choice) - 1]["id"]
                bundle_id = variants[int(choice) - 1]["bundle_id"]
    if not bundle_id:
        bundle_id = _ask("Bundle identifier (e.g. com.SideStore.SideStore): ")
    if not bundle_id:
        print("No bundle identifier; cannot continue.")
        return 2

    udid = state.get("udid") or ""
    if not udid:
        print("No UDID saved. Running stage 1 first.")
        code = cmd_udid([])
        if code != 0:
            return code
        udid = load_state().get("udid", "")
    else:
        print("Using the saved UDID %s (run 'barisign.py udid' to read another)." % udid)

    account = _sign_in(email)
    result = _portal(account, email, udid,
                     bundle_id,
                     _ask("Device name for the portal [BariSign device]: ", "BariSign device"),
                     _ask("App name for the portal [BariSign]: ", "BariSign"))

    update_state(email=email, udid=udid, variant=variant or state.get("variant", ""),
                 bundle_id=result["bundle_id"], team_id=result["team_id"],
                 profile=result["profile"], key=result["key"])

    _hr("result")
    print("PORTAL: ok - the device is registered and Apple returned a profile.")
    print("NEXT: stage 4, the signer. It does not exist yet, so nothing installs")
    print("      anything today. When it does, this becomes: pick a variant,")
    print("      sign, AirDrop.")
    return 0


def cmd_guided(_argv):
    """check -> variant -> UDID -> sign in -> portal."""
    state = load_state()
    _hr("welcome")
    print("BariSign %s - one guided run. Ctrl-C stops at any point." % VERSION)
    print("Nothing is installed; this registers a device and gets a certificate.")

    if cmd_check([]) != 0:
        print("Fix the checks above before continuing.")
        return 1

    variants = load_variants()
    if variants:
        _hr("which variant?")
        for index, item in enumerate(variants, 1):
            print("  %d) %-38s %s" % (index, item["name"], item.get("bundle_id", "")))
        choice = _ask("Choose [1-%d]: " % len(variants), "1")
        if not (choice.isdigit() and 1 <= int(choice) <= len(variants)):
            choice = "1"
        chosen = variants[int(choice) - 1]
        state = update_state(variant=chosen["id"], bundle_id=chosen["bundle_id"])
        print("  %s" % chosen["name"])

    if state.get("udid") and _yes("Reuse the saved UDID %s?" % state["udid"], True):
        print("  reusing the UDID")
    else:
        code = cmd_udid([])
        if code != 0:
            return code

    email = state.get("email") or _ask("Apple ID (email): ")
    if not email:
        print("No Apple ID given.")
        return 2
    account = _sign_in(email)
    state = load_state()
    result = _portal(account, email, state.get("udid", ""), state.get("bundle_id", ""),
                     "BariSign device", "BariSign")
    update_state(email=email, bundle_id=result["bundle_id"], team_id=result["team_id"],
                 profile=result["profile"], key=result["key"])

    _hr("done")
    print("Registered, certified, and the profile is saved.")
    print("Stage 4 (the signer) is still to be built - that is what turns this into")
    print("an installable app. Run 'barisign.py status' any time to see the state.")
    return 0


COMMANDS = {
    "check": cmd_check,
    "status": cmd_status,
    "forget": cmd_forget,
    "udid": cmd_udid,
    "login": cmd_login,
    "portal": cmd_portal,
    "all": cmd_guided,
}


def _usage():
    print("BariSign - one command for every stage (v%s)" % VERSION)
    print()
    print("  barisign.py            guided run: check -> variant -> UDID -> Apple -> portal")
    print("  barisign.py check      runtime + crypto + anisette + Apple TLS, nothing else")
    print("  barisign.py udid       stage 1: read another device's UDID")
    print("  barisign.py login      stage 2: sign in to Apple with a free Apple ID")
    print("  barisign.py portal     stage 3: register the UDID + certificate + profile")
    print("  barisign.py status     what is already saved")
    print("  barisign.py forget     clear the saved state (keeps the local CA)")
    print()
    print("Common:  barisign.py check | udid | login | portal | status | forget")
    print("Stage 1 also takes --udid-only (after the CA is trusted once).")
    print("The portal takes --bundle-id to skip the variant menu.")


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        _usage()
        return 0 if argv else cmd_guided([])

    command = argv[0]
    handler = COMMANDS.get(command)
    if handler is None:
        print("unknown command: %s" % command)
        _usage()
        return 2

    # the portal needs the writable state directory for keys, certs and profiles
    os.makedirs(STATE_DIR, exist_ok=True)
    portal.STORAGE = STATE_DIR
    signin.STORAGE = STATE_DIR
    return handler(argv[1:])


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Fail as exc:
        print()
        print("FAILED: %s" % exc)
        print("(nothing was installed; 'barisign.py status' shows what is saved)")
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    except Exception:                                         # noqa: BLE001
        print()
        print("UNEXPECTED ERROR:")
        traceback.print_exc()
        sys.exit(4)
