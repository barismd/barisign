#!/usr/bin/env python3
# =============================================================================
#  BariSign — one command for every stage (MIT)
#  STATUS: stage 1 and 2 are verified on device; stage 3 (portal) is written but
#          untested against Apple; stage 4 (the signer) does not exist yet.
#
#  Everything runs on the device the app will be installed on: no second phone
#  and no computer. The UDID comes from a one-tap profile (median.co), so there is
#  no local server and no certificate trust to set up.
#
#  Every stage shares its result with the next through a small state file, so you
#  never retype a UDID or an Apple ID.
#
#      python3 -u poc/barisign.py            guided run, start to finish
#      python3 -u poc/barisign.py check      runtime + anisette + Apple TLS
#      python3 -u poc/barisign.py udid       stage 1 - read this device's UDID
#      python3 -u poc/barisign.py login      stage 2 - sign in to Apple
#      python3 -u poc/barisign.py portal     stage 3 - UDID + certificate + profile
#      python3 -u poc/barisign.py status     show what is already known
#      python3 -u poc/barisign.py forget     clear the saved state
#
#  Other ways to read a UDID (other sites, or your own HTTPS server) are described
#  in docs/UDID_OPTIONS.md; this script only takes the value and remembers it.
# =============================================================================

import hashlib
import json
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

import poc_login                                              # noqa: E402  (sets up TLS trust)
import signin                                                 # noqa: E402
try:
    import portal                                             # noqa: E402
except ImportError as exc:                                    # noqa: BLE001
    print("Could not import the vendored developer-portal code: %s" % exc)
    print("third_party/sideinstaller/portal.py is missing or incomplete.")
    print("Fix it by re-running the installer in the BariSign folder:  sh install.sh")
    sys.exit(2)
from signin import AppleAccount, Fail, connect_anisette        # noqa: E402
from portal import (DeveloperSession, ensure_app_id, ensure_device,               # noqa: E402
                    ensure_private_key, obtain_certificate, pick_team,
                    download_profile, profile_plist)

VERSION = "0.4.2"
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
    # Plain input(): a-Shell echoes it normally, and nothing touches termios, so a
    # later prompt cannot end up with echo switched off (that bit us once).
    try:
        entered = input(prompt)
    except EOFError:
        entered = ""
    entered = (entered or "").strip()
    return entered or (default or "")


def _yes(prompt, default=False):
    answer = _ask("%s [%s] " % (prompt, "Y/n" if default else "y/N")).lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _bag_probe(gs, client_info, user_agent):
    """GET Apple's URL bag with the real anisette headers for this identity.

    No account data is involved. The probe carries the same headers the real bag
    request will, so a 200 here means the sign-in request will not be refused for
    this reason.
    """
    try:
        headers = gs.base_headers(plist=False)
    except Exception as exc:                                  # noqa: BLE001
        return 0, "no anisette headers: %r" % (exc,)
    headers["X-Mme-Client-Info"] = client_info
    headers["User-Agent"] = user_agent
    try:
        status, _, body = signin.request("GET", signin.GSA_URL_BAG,
                                         headers=headers, timeout=30)
    except Exception as exc:                                  # noqa: BLE001
        return 0, repr(exc)
    snippet = " ".join((body or b"").decode("utf-8", "replace").split())[:160]
    return status, snippet


def _preflight_apple(gs):
    """The exact first request the sign-in makes: Apple's URL bag, as a Mac.

    The identity is not a free choice, and that is why this probe tries exactly one:

      * the **URL bag** and the **2FA pages** only answer a Mac — asked as an Android
        phone the bag returns ten gsd.apple.com URLs and 2FA answers a bare 403;
      * the **sign-in POST** must claim Android, because public anisette servers mint
        their machine ID with Apple's Android ADI library, and since 2026-10-12
        GrandSlam refuses a sign-in whose client OS differs from it (-80045);
      * the **developer portal** is a third endpoint (Xcode's clientId), and it is
        still untested.

    So a 200 here means the bag — which every other call depends on — is reachable.
    Probing more identities would only add requests, not information.
    """
    status, note = _bag_probe(gs, signin.GSA_MAC_CLIENT_INFO, signin.GSA_USER_AGENT)
    if status == 200:
        print("  Apple reachable      : yes (URL bag, as a Mac)")
        return True
    print("  Apple's URL bag      : HTTP %s%s" % (status, (" - " + note) if note else ""))
    return False


def _sign_in(email, preferred=None):
    """Anisette, then Apple, then the password — never the other way round.

    Apple's October 2026 rules (verified against release notes, issue trackers and
    live servers): the sign-in must claim the platform the anisette data was made
    on, and only Mac anisette still signs in. So the Mac-emitting servers are tried
    first and their own client_info is what we send. A 503 or -80046 means the edge
    refused the identity: forget it and try the next server. The password is asked
    once, after Apple has answered, and never stored.
    """
    _hr("Apple sign-in")
    state_path = os.path.join(STATE_DIR, "anisette.json")
    candidates = _anisette_order([preferred] if preferred else [])
    last_error = None
    saw_503 = False
    password = None

    def ask_code(label):
        try:
            entered = input("%s: " % label)
        except EOFError:
            entered = ""
        return "".join(ch for ch in entered if ch.isdigit())

    for index, url in enumerate(candidates, 1):
        print("  anisette %d/%d        : %s" % (index, len(candidates), url))
        try:
            anisette, gs = connect_anisette(url, state_path)
        except Fail as exc:
            print("    unusable: %s" % exc)
            last_error = exc
            continue
        print("    headers minted (v%s)" % getattr(anisette, "version", "?"))

        if not _preflight_apple(gs):
            print("    Apple's edge refuses every identity we know here. Nothing was")
            print("    sent about your account; the next server gets a new identity.")
            saw_503 = True
            last_error = Fail("HTTP 503 from Apple's edge")
            _forget_anisette(anisette, state_path)
            continue

        if password is None:
            password = poc_login._read_password("Password (not shown, not stored): ")
            if not password:
                raise Fail("No password entered.")

        account = AppleAccount(email, gs, anisette)
        try:
            account.login(password, ask_code)
        except Fail as exc:
            last_error = exc
            print("    %s" % exc)
            if not _identity_refused(str(exc)):
                raise
            print("    Apple refused this identity (-80045/-80046/503). Forgetting")
            print("    it and trying the next anisette server…")
            saw_503 = True
            _forget_anisette(anisette, state_path)
            continue
        password = None
        print("  signed in via %s" % url)
        return account

    if not saw_503:
        raise Fail("No anisette server could be used. Last error: %s\n"
                   "    Without one Apple cannot be asked to sign you in; the anisette\n"
                   "    servers are community-run, so trying again later usually works."
                   % (last_error,))
    raise Fail("Apple answered HTTP 503 for every server and identity tried (%d).\n"
               "    That is Apple's edge refusing the request before it reads the\n"
               "    account, so the password was never the problem. Wait 10-15 minutes\n"
               "    without retrying, then run login again. Repeated attempts of the\n"
               "    same minute keep the block alive." % len(candidates))


def _identity_refused(message):
    """True when Apple refused the *identity*, not the credentials.

    2026-10-12: a sign-in whose claimed platform differs from the anisette data is
    answered -80045. 2026-10-24: the Android claim itself is answered -80046, and
    some edges answer a bare HTTP 503 before reading the account. In all three
    cases the cure is another server and a fresh identity, never another password.
    """
    text = message.lower()
    return ("503" in text or "service unavailable" in text
            or "-80045" in text or "-80046" in text or "cannot be completed at this time" in text)


def _forget_anisette(anisette, state_path):
    try:
        anisette.forget_provisioning()
    except Exception:                                         # noqa: BLE001
        pass
    try:
        os.remove(state_path)
    except OSError:
        pass


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

    if not bundle_id:
        raise Fail("No bundle identifier, so there is nothing to register.")
    # state keeps the *full* identifier, so never append the team twice
    suffix = "." + team["teamId"]
    full_bundle_id = (bundle_id[:-len(suffix)] if bundle_id.endswith(suffix) else bundle_id) + suffix
    app_id = ensure_app_id(session, team, full_bundle_id, app_name)
    print("  App ID          : %s" % full_bundle_id)

    key_path = ensure_private_key(email)
    cert_path, reused = obtain_certificate(session, team, key_path, STATE_DIR)
    print("  certificate     : %s (%s)" % (cert_path, "reused" if reused else "created"))
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
            "profile": profile_path, "key": key_path, "cert": cert_path}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_check(_argv):
    """Everything that can be checked without an Apple ID."""
    passed = poc_login._selftest()
    _hr("result")
    print("CHECK: %s" % ("PASS - safe to continue" if passed == 0 else "see the failures above"))
    return passed


def cmd_version(_argv):
    """Prove which copy is running: version, file, size and fingerprint."""
    path = os.path.abspath(__file__)
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()[:12]
        size = os.path.getsize(path)
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
    except OSError as exc:                                    # noqa: BLE001
        print("could not read %s: %r" % (path, exc))
        return 1
    print("BariSign %s" % VERSION)
    print("  file        : %s" % path)
    print("  size        : %d bytes" % size)
    print("  last change : %s" % stamp)
    print("  sha256[:12] : %s" % digest)
    print("  clipboard   : %s" % ("pbpaste found" if _have_command("pbpaste") else "no pbpaste"))
    print("  open        : %s" % ("found" if _have_command("open") else "missing"))
    print("  stdin is tty: %s" % sys.stdin.isatty())
    return 0


def _have_command(name):
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return True
    return False


def cmd_status(_argv):
    state = load_state()
    _hr("what BariSign already knows")
    print("  Apple ID        : %s" % (state.get("email") or "(not set - run: login)"))
    print("  UDID            : %s" % (state.get("udid") or "(not set - run: udid)"))
    print("  variant         : %s" % (state.get("variant") or "(not chosen)"))
    print("  bundle id       : %s" % (state.get("bundle_id") or "(not set)"))
    print("  team            : %s" % (state.get("team_id") or "(not set)"))
    print("  certificate     : %s" % (state.get("cert") or "(not issued)"))
    print("  profile         : %s" % (state.get("profile") or "(not downloaded)"))
    print()
    print("  state file      : %s" % STATE_FILE)
    return 0


def cmd_forget(_argv):
    if os.path.isfile(STATE_FILE):
        os.remove(STATE_FILE)
        print("Forgot the saved Apple ID, UDID, variant and paths.")
    else:
        print("Nothing to forget.")
    return 0


UDID_URL = "https://median.co/udid"

UDID_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{24})$")


ANSI_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]")


def extract_udid(text):
    """Find a UDID inside whatever arrived.

    a-Shell may hand us a bracketed paste ("\x1b[200~…\x1b[201~"), a labelled line
    ("UDID: …"), quotes, or plain text. Rather than rejecting all of that, strip the
    terminal noise and pick the UDID out of it. Returns the canonical form or "".
    """
    cleaned = ANSI_ESCAPES.sub("", text or "")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch in "-_ ")
    # "UDID: 00008110 001a …" — drop the label, then glue groups of hex together
    unlabelled = re.sub(r"^\s*udid\s*[:=]?\s*", "", cleaned, flags=re.IGNORECASE)
    squashed = unlabelled
    while True:
        tighter = re.sub(r"(?<=[0-9a-fA-F])[ \-_](?=[0-9a-fA-F])", "", squashed)
        if tighter == squashed:
            break
        squashed = tighter
    for candidate in (cleaned, unlabelled, squashed):
        for pattern in (r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])",
                        r"(?<![0-9a-fA-F])[0-9a-fA-F]{8}-[0-9a-fA-F]{16}(?![0-9a-fA-F])",
                        r"(?<![0-9a-fA-F])[0-9a-fA-F]{24}(?![0-9a-fA-F])"):
            found = re.search(pattern, candidate)
            if found:
                return normalise_udid(found.group(0))
    return ""


def _clipboard_text():
    """What is on the iOS clipboard, if a-Shell exposes it (pbpaste)."""
    try:
        with os.popen("pbpaste 2>/dev/null") as handle:
            return handle.read()
    except Exception:                                         # noqa: BLE001
        return ""


def normalise_udid(text):
    """Accept what a UDID page copies: 40 hex characters, or the 8+16 form that
    A12-and-later devices use, with or without the dash."""
    cleaned = re.sub(r"[\s\-]", "", (text or "")).lower()
    if not UDID_PATTERN.match(cleaned):
        return ""
    if len(cleaned) == 40:
        return cleaned
    return "%s-%s" % (cleaned[:8], cleaned[8:])


def _press_return(prompt):
    """Wait for Return with plain input(): no terminal modes are changed."""
    if not sys.stdin.isatty():
        return ""            # piped input: do not swallow the next line
    try:
        return input(prompt)
    except EOFError:
        return ""


def _open_in_safari(url):
    """`open <url>` is how a-Shell hands a link to Safari (it is documented for
    shortcuts://). Returns True when the command reported success."""
    if os.environ.get("BARISIGN_NO_OPEN"):
        return None                    # suppressed on purpose, not a failure
    try:
        return os.system("open '%s' >/dev/null 2>&1" % url) == 0
    except Exception:                                         # noqa: BLE001
        return False


def cmd_udid(argv):
    """Stage 1: the UDID of this device, pasted in.

    The median.co profile is installed with one tap and shows the UDID, so this stage
    needs no server, no certificate and no second device. The steps are printed first,
    then Return opens Safari, then the pasted value is checked and remembered.
    docs/UDID_OPTIONS.md lists the alternatives (other sites, your own HTTPS server).
    """
    _hr("stage 1 - read this device's UDID")
    typed = [item for item in argv if not item.startswith("-")]
    udid = extract_udid(typed[0]) if typed else ""
    if not udid:
        print(" 1. Safari on THIS device:   https://median.co/udid")
        print(" 2. Tap the install button, allow the profile, then finish installing it:")
        print("      Settings > General > VPN & Device Management > Downloaded Profile")
        print(" 3. Safari then shows the UDID. Tap 'Copy UDID to Clipboard'.")
        print(" 4. Come back here and paste it (or:  sh bin/barisign udid <UDID>).")
        print()
        print(" Only the hardware ID is read, nothing is signed in, and the profile")
        print(" can be removed straight afterwards. Other options: docs/UDID_OPTIONS.md")
        print()
        if "--no-open" not in argv:
            _press_return(" Press Return to open Safari (Ctrl-C to stop) ")
            opened = _open_in_safari(UDID_URL)
            if opened is True:
                print(" Safari opened. Come back here and paste the UDID.")
            elif opened is False:
                print(" Could not open Safari. Open this yourself:")
                print("   %s" % UDID_URL)
            else:
                print(" Safari not opened (BARISIGN_NO_OPEN). The address is:")
                print("   %s" % UDID_URL)
            print()
        for attempt in range(3):
            if attempt == 0:
                clip = _clipboard_text()
                from_clipboard = extract_udid(clip)
                if from_clipboard:
                    print(" The clipboard already holds a UDID: %s" % from_clipboard)
                    if _yes(" Use this one?", True):
                        udid = from_clipboard
                        break
                    print(" Okay, type or paste the right one.")
                elif not _have_command("pbpaste"):
                    print(" a-Shell has no 'pbpaste' here, so the clipboard cannot be read:")
                    print(" copy the UDID and paste it below.")
                else:
                    print(" The clipboard holds no UDID (%r): paste it below." % (clip[:40],))
            try:
                typed = input(" Paste the UDID (blank = stop): ")
            except EOFError:
                typed = ""
            udid = extract_udid(typed)
            if udid:
                print(" Read: %s" % udid)
                break
            if not typed.strip():
                print("Stopped. Nothing was saved.")
                return 1
            print(" Could not find a UDID in: %r" % (typed[:120],))
            print(" A UDID is 40 hex characters, or 24 like 00008110-001a2c3e0e91801e.")
    if not udid:
        return 1
    update_state(udid=udid)
    _hr("saved")
    print("  UDID %s is remembered; the portal will register it." % udid)
    print("  Remove the profile now if you like: Settings > General > VPN & Device Management.")
    return 0


# Apple's October 2026 rule: the sign-in must claim the platform the anisette
# data was made on, and only Mac anisette still signs in.
#
# These two are what we use by default. Both are jkcoxson's, both are entries in
# SideStore's own curated list (github.com/SideStore/anisette-servers, AGPL-3.0,
# `kind: 2` = macOS), and both report a Mac client_info (checked 2026-10-29).
# Everything else comes from the same curated HTTPS list and is used in the order
# SideStore publishes it; the first two are simply the ones known to sign in today.
TRUSTED_ANISETTE = (
    "https://mac-ani.jkcoxson.com",
    "https://ani.jkcoxson.com",
    "https://ani.sidestore.io",
)


def argv_get(argv, flag):
    if flag in argv:
        position = argv.index(flag) + 1
        if position < len(argv):
            return argv[position]
    return ""


def _anisette_order(preferred):
    """The curated list, HTTPS only, with the Mac servers that work today first.

    All of these are official entries in SideStore's list and the software behind
    them is open source (omnisette-server, anisette-v3-server), so any https://
    entry is acceptable. Plain http:// entries are dropped: anisette headers have
    no business travelling in clear text.
    """
    order = []
    for url in list(preferred) + list(TRUSTED_ANISETTE) + signin.anisette_candidates(""):
        url = (url or "").rstrip("/")
        if not url.startswith("https://") or url in order:
            continue
        order.append(url)
    return order[:6]


def _probe_apple(preferred=None):
    """Anisette + Apple's URL bag. No account, no password, no SRP request.

    Use it to see whether a 503 block has lifted without spending sign-in attempts:
    it makes one unauthenticated GET per anisette server.
    """
    _hr("probe - anisette and Apple, nothing else")
    state_path = os.path.join(STATE_DIR, "anisette.json")
    candidates = _anisette_order([preferred] if preferred else [])[:4]
    for index, url in enumerate(candidates, 1):
        print("  anisette %d/%d        : %s" % (index, len(candidates), url))
        try:
            anisette, gs = connect_anisette(url, state_path)
        except Fail as exc:
            print("    unusable: %s" % exc)
            continue
        print("    headers minted (v%s)" % getattr(anisette, "version", "?"))
        if _preflight_apple(gs):
            _hr("result")
            print("PROBE: PASS - Apple is answering. Run 'sh bin/barisign login' now.")
            return 0
    _hr("result")
    print("PROBE: FAIL - Apple's edge is still refusing (the URL bag answered 503).")
    print("  Nothing was sent about your account and no password was asked.")
    print("  Wait longer (15 minutes is a minimum, not a promise), switch network")
    print("  if you can, and turn off LocalDevVPN / StosVPN / any VPN first.")
    return 1


def cmd_login(argv):
    """Stage 2: sign in and remember the account.

    `--probe` stops after the anisette + Apple check; `--server URL` pins an
    anisette server instead of the built-in Mac-first order.
    """
    preferred = argv_get(argv, "--server")
    if "--probe" in argv:
        return _probe_apple(preferred)
    positional = [item for item in argv if not item.startswith("-")
                  and item != preferred]
    email = positional[0] if positional else _ask("Apple ID (email): ")
    if not email:
        print("No Apple ID given.")
        return 2
    account = _sign_in(email, preferred)
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
        position = argv.index("--bundle-id") + 1
        if position >= len(argv):
            print("--bundle-id needs a value, e.g. --bundle-id com.SideStore.SideStore")
            return 2
        bundle_id = argv[position]
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
    # Apple's portal wants a name for the registered device and for the App ID.
    # They only appear at developer.apple.com, so they are filled in rather than
    # asked: one less prompt for the user.
    result = _portal(account, email, udid, bundle_id, "BariSign device", "BariSign")

    update_state(email=email, udid=udid, variant=variant or state.get("variant", ""),
                 bundle_id=result["bundle_id"], team_id=result["team_id"],
                 profile=result["profile"], key=result["key"], cert=result["cert"])

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
        state = load_state()

    email = state.get("email") or _ask("Apple ID (email): ")
    if not email:
        print("No Apple ID given.")
        return 2
    account = _sign_in(email)
    state = load_state()
    result = _portal(account, email, state.get("udid", ""), state.get("bundle_id", ""),
                     "BariSign device", "BariSign")
    update_state(email=email, bundle_id=result["bundle_id"], team_id=result["team_id"],
                 profile=result["profile"], key=result["key"], cert=result["cert"])

    _hr("done")
    print("Registered, certified, and the profile is saved.")
    print("Stage 4 (the signer) is still to be built - that is what turns this into")
    print("an installable app. Run 'barisign.py status' any time to see the state.")
    return 0


COMMANDS = {
    "version": cmd_version,
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
    print("  barisign.py udid       stage 1: this device's UDID (median.co profile)")
    print("  barisign.py login      stage 2: sign in to Apple with a free Apple ID")
    print("  barisign.py login --probe   anisette + Apple only (no password, no account)")
    print("  login --server URL          pin an anisette server (default: Mac-first order)")
    print("  barisign.py portal     stage 3: register the UDID + certificate + profile")
    print("  barisign.py status     what is already saved")
    print("  barisign.py version    which copy is running (version + fingerprint)")
    print("  barisign.py forget     clear the saved state")
    print()
    print("Common:  barisign.py check | udid | login | portal | status | forget")
    print("UDID:    'udid' opens Safari, then takes the pasted value; 'udid <UDID>' stores")
    print("         one directly; '--no-open' skips Safari. docs/UDID_OPTIONS.md has more.")
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
    except BrokenPipeError:
        # `barisign.py ... | head` closes the pipe early; that is not an error.
        os._exit(0)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    except Exception:                                         # noqa: BLE001
        print()
        print("UNEXPECTED ERROR:")
        traceback.print_exc()
        sys.exit(4)
