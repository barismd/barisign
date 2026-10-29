#!/usr/bin/env python3
# =============================================================================
#  BariSign — on-device installer (MIT)
#
#  Runs on the device that gets the app. No computer, no second device.
#
#      python3 -u poc/barisign.py            guided run
#      python3 -u poc/barisign.py udid       this device's UDID
#      python3 -u poc/barisign.py login      Apple sign-in
#      python3 -u poc/barisign.py portal     UDID + certificate + profile
#      python3 -u poc/barisign.py status     what is saved
#      python3 -u poc/barisign.py check      diagnostics only
#      python3 -u poc/barisign.py version    which copy is running
#      python3 -u poc/barisign.py forget     clear the saved state
# =============================================================================

import hashlib
import json
import os
import threading
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

VERSION = "0.5.3"
STATE_DIR = poc_login.POC_STATE
STATE_FILE = os.path.join(STATE_DIR, "barisign.json")
VARIANTS_FILE = os.path.join(_HERE, "..", "variants.json")

# Done here, not in main(): the key, certificate and profile paths are derived
# from it, and an early return in main() used to leave it pointing at the app
# container root, which is not writable.
os.makedirs(STATE_DIR, exist_ok=True)
portal.STORAGE = STATE_DIR
signin.STORAGE = STATE_DIR


def _wire_progress():
    """The vendored stack narrates every step; it all goes through one line."""
    for name in ("info", "ok", "step", "warn"):
        if hasattr(signin, name):
            setattr(signin, name, lambda message="", *a, **k: _activity(message))
    signin.PROGRESS = lambda message="": _activity(message)


_wire_progress()


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

CLEAR = "\033[2J\033[H"
LINE = 38


class _Spinner:
    """One line, rewritten in place: '(2/5) device ....' with the dots growing."""

    def __init__(self):
        self.text = ""
        self.dots = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        # stop() retires the thread; a new stage needs a fresh one.
        if self._thread is None or not self._thread.is_alive():
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def set(self, text):
        with self._lock:
            self.text = text
            self.dots = 0
        self.start()

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                text = self.text
            if text:
                sys.stdout.write("\r" + ("  " + text + " " + "." * self.dots)[:LINE].ljust(LINE))
                sys.stdout.flush()
                with self._lock:
                    self.dots = (self.dots + 1) % 4
            time.sleep(0.4)

    def stop(self):
        self._stop.set()
        with self._lock:
            self.text = ""
        sys.stdout.write("\r" + " " * LINE + "\r")
        sys.stdout.flush()


_SPINNER = _Spinner()


def _stage(title, note=""):
    """Clean screen, then the stage header. The note restores what the clear
    would otherwise wipe (the Apple ID, for example)."""
    _SPINNER.stop()
    sys.stdout.write(CLEAR)
    sys.stdout.flush()
    print("BariSign %s" % VERSION)
    print()
    print("== %s ==" % title.upper())
    if note:
        print("  %s" % note)
    print()


def _step(index, total, label):
    _SPINNER.set("(%d/%d) %s" % (index, total, label))


def _activity(message):
    """Vendored narration would fight the spinner, so it is not shown."""
    return


def _pause_for_input():
    _SPINNER.stop()


def _line_clear():
    _SPINNER.stop()


def _result(label, text):
    _line_clear()
    print()
    print("%s  %s" % (label, text))


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
    return status == 200


def _sign_in(email, preferred=None, use_clipboard=False):
    """Anisette, then Apple, then the password — never the other way round.

    Apple's October 2026 rules (verified against release notes, issue trackers and
    live servers): the sign-in must claim the platform the anisette data was made
    on, and only Mac anisette still signs in. So the Mac-emitting servers are tried
    first and their own client_info is what we send. A 503 or -80046 means the edge
    refused the identity: forget it and try the next server. The password is asked
    once, after Apple has answered, and never stored.
    """
    _stage("Apple sign-in", "Apple ID  %s" % email)
    state_path = os.path.join(STATE_DIR, "anisette.json")
    candidates = _anisette_order([preferred] if preferred else [])
    last_error = None
    saw_503 = False
    password = None

    def ask_code(label):
        """The 2FA code is not a secret: read it visibly, with our own echo, so it
        works even when the terminal's echo was left off by the password prompt."""
        prompt = "  %s: " % label
        if sys.stdin.isatty():
            entered = poc_login._masked_line(prompt, mask=False)
        else:
            try:
                entered = input(prompt)
            except EOFError:
                entered = ""
        return "".join(ch for ch in entered if ch.isdigit())

    for index, url in enumerate(candidates, 1):
        _step(1, 3, "anisette")
        try:
            anisette, gs = connect_anisette(url, state_path)
        except Fail as exc:
            print("    unusable: %s" % exc)
            last_error = exc
            continue


        if _preflight_apple(gs):
            _step(2, 3, "apple reachable")
        else:
            _step(2, 3, "apple: retry")
            saw_503 = True
            last_error = Fail("HTTP 503 from Apple's edge")
            _forget_anisette(anisette, state_path)
            continue

        account = AppleAccount(email, gs, anisette)
        refused_identity = False
        for attempt in range(3):
            if password is None and use_clipboard:
                password = poc_login._clipboard_text().strip()
                if not password:
                    raise Fail("the clipboard is empty: copy the password first")
                _step(3, 3, "sign in")
                _pause_for_input()
            if password is None:
                _step(3, 3, "sign in")
                _pause_for_input()
                password = poc_login._read_password("  Password: ")
                if not password:
                    raise Fail("no password")
            try:
                account.login(password, ask_code)
                break
            except Fail as exc:
                last_error = exc
                message = str(exc)
                print("  ! %s" % message)
                if _identity_refused(message):
                    print("  ! identity refused; next server")
                    saw_503 = True
                    _forget_anisette(anisette, state_path)
                    refused_identity = True
                    break          # keep the password: it was never the problem
                if "-22406" in message or "correct password" in message.lower():
                    print("  ! wrong password (-22406)")
                    print("    apple ID sent: %r" % email)
                    print("    password: %s" % poc_login._password_diagnostics(password))
                    password = None
                    if attempt < 2:
                        print("  retry %d/3" % (attempt + 2))
                        continue
                raise
        if refused_identity:
            continue
        password = None
        _line_clear()
        return account

    if not saw_503:
        raise Fail("no anisette server worked (last: %s)" % (last_error,))
    raise Fail("apple refused every identity (503); wait 10-15 minutes, \n"
               "    then run login again - the password was never the problem")


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
    started = time.time()
    token, adsid = account.app_token("xcode.auth")
    session = DeveloperSession(account, token, adsid)

    _step(1, 5, "team")
    team = pick_team(session)
    _step(2, 5, "device")
    ensure_device(session, team, udid, device_name)

    if not bundle_id:
        raise Fail("No bundle identifier, so there is nothing to register.")
    # state keeps the *full* identifier, so never append the team twice
    suffix = "." + team["teamId"]
    full_bundle_id = (bundle_id[:-len(suffix)] if bundle_id.endswith(suffix) else bundle_id) + suffix
    _step(3, 5, "app id")
    app_id = ensure_app_id(session, team, full_bundle_id, app_name)

    key_path = ensure_private_key(email)
    _step(4, 5, "certificate")
    cert_path, reused = obtain_certificate(session, team, key_path, STATE_DIR)

    _step(5, 5, "profile")
    profile = download_profile(session, team, app_id)
    profile_path = os.path.join(STATE_DIR, "profile.mobileprovision")
    with open(profile_path, "wb") as handle:
        handle.write(profile)

    devices, expires = [], "?"
    try:
        parsed = profile_plist(profile)
        devices = parsed.get("ProvisionedDevices") or []
        expires = parsed.get("ExpirationDate", "?")
    except Exception:                                         # noqa: BLE001
        pass
    def _hex(value):
        return "".join(ch for ch in (value or "").lower() if ch in "0123456789abcdef")
    ours = any(_hex(device) == _hex(udid) for device in devices)
    _line_clear()
    print("  team      %s (%s)" % (team.get("name", "?"), team["teamId"]))
    print("  app id    %s" % full_bundle_id)
    print("  cert      %s" % ("reused" if reused else "created"))
    print("  profile   %d device(s), expires %s, this device: %s"
          % (len(devices), expires, "yes" if ours else "NO"))
    if devices and not ours:
        print("  ! the profile does not list this device: an app signed with it")
        print("    will not launch. Re-run portal, or remove the profile at")
        print("    developer.apple.com and run portal again.")
    print("  took      %.0f s" % (time.time() - started))

    return {"team_id": team["teamId"], "bundle_id": full_bundle_id,
            "profile": profile_path, "key": key_path, "cert": cert_path}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_check(_argv):
    """Everything that can be checked without an Apple ID."""
    passed = poc_login._selftest()
    print()
    print("CHECK  %s" % ("pass" if passed == 0 else "fail"))
    return passed


def cmd_version(_argv):
    """Which copy is running."""
    path = os.path.abspath(__file__)
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()[:12]
    _stage("Version")
    print("  file      %s" % path)
    print("  sha256    %s" % digest)
    return 0


def _have_command(name):
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return True
    return False


def cmd_status(_argv):
    state = load_state()
    _stage("Status")
    rows = (("Apple ID", state.get("email")), ("UDID", state.get("udid")),
            ("variant", state.get("variant")), ("bundle", state.get("bundle_id")),
            ("team", state.get("team_id")), ("cert", state.get("cert")),
            ("profile", state.get("profile")))
    for label, value in rows:
        print("  %-9s %s" % (label, value or "-"))
    return 0


def cmd_forget(_argv):
    if os.path.isfile(STATE_FILE):
        os.remove(STATE_FILE)
        print("forgot: Apple ID, UDID, variant, certificate, profile")
    else:
        print("nothing saved")
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
    return poc_login._clipboard_text()


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
    """This device's UDID: Safari opens, the value is pasted back."""
    _stage("UDID")
    typed = [item for item in argv if not item.startswith("-")]
    udid = extract_udid(typed[0]) if typed else ""
    if not udid:
        print("  Safari opens median.co/udid")
        print("  1  install the profile")
        print("  2  copy the UDID")
        print("  3  come back here")
        print()
        if "--no-open" not in argv:
            _step(1, 3, "safari")
            _pause_for_input()
            _press_return("  Return to open Safari: ")
            if _open_in_safari(UDID_URL) is not True:
                print("  open it yourself: %s" % UDID_URL)
                print()
        for attempt in range(3):
            found = extract_udid(_clipboard_text())
            if found and attempt == 0:
                print("  clipboard: %s" % found)
                if _yes("  use it?", True):
                    udid = found
                    break
            _step(2, 3, "read the UDID")
            _pause_for_input()
            typed = _ask("  UDID: ")
            udid = extract_udid(typed)
            if udid:
                break
            if not typed:
                print("  stopped")
                return 1
            print("  not a UDID: %r" % (typed[:60],))
    if not udid:
        return 1
    update_state(udid=udid)
    _result("UDID", "%s saved" % udid)
    return 0


def argv_get(argv, flag):
    if flag in argv:
        position = argv.index(flag) + 1
        if position < len(argv):
            return argv[position]
    return ""


# Apple only accepts a sign-in that claims the platform the anisette data was made
# on, and Mac anisette is the kind that still signs in. These two report a Mac and
# come first; the rest of SideStore's curated list follows, HTTPS only.
TRUSTED_ANISETTE = (
    "https://mac-ani.jkcoxson.com",
    "https://ani.jkcoxson.com",
)


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


def cmd_login(argv):
    """Apple sign-in."""
    preferred = argv_get(argv, "--server")
    use_clipboard = "--use-clipboard" in argv
    positional = [item for item in argv if not item.startswith("-") and item != preferred]
    email = positional[0] if positional else _ask("  Apple ID: ")
    if not email:
        print("no Apple ID")
        return 2
    account = _sign_in(email, preferred, use_clipboard)
    update_state(email=email)
    name = ""
    try:
        name = (account.spd or {}).get("fn", "") if getattr(account, "spd", None) else ""
    except Exception:                                         # noqa: BLE001
        name = ""
    _result("LOGIN", "ok%s" % (("  " + name) if name else ""))
    return 0


def cmd_portal(argv):
    """Register the UDID, issue a certificate, download the profile."""
    state = load_state()
    email = state.get("email") or ""
    if not email:
        print("! no Apple ID: run login first")
        return 2

    variant = state.get("variant") or ""
    bundle_id = state.get("bundle_id") or ""
    if "--bundle-id" in argv:
        position = argv.index("--bundle-id") + 1
        if position >= len(argv):
            print("--bundle-id needs a value")
            return 2
        bundle_id = argv[position]
    if not bundle_id:
        variants = load_variants()
        if variants:
            _stage("Variant")
            for index, item in enumerate(variants, 1):
                print("  %d  %s" % (index, item["name"]))
            choice = _ask("  choose 1-%d: " % len(variants), "1")
            if choice.isdigit() and 1 <= int(choice) <= len(variants):
                variant = variants[int(choice) - 1]["id"]
                bundle_id = variants[int(choice) - 1]["bundle_id"]
    if not bundle_id:
        print("no bundle id")
        return 2

    udid = state.get("udid") or ""
    if not udid:
        print("no UDID: running udid first")
        if cmd_udid([]) != 0:
            return 1
        udid = load_state().get("udid", "")

    account = _sign_in(email)
    _stage("Portal")
    result = _portal(account, email, udid, bundle_id, "BariSign device", "BariSign")
    update_state(email=email, udid=udid, variant=variant or state.get("variant", ""),
                 bundle_id=result["bundle_id"], team_id=result["team_id"],
                 profile=result["profile"], key=result["key"], cert=result["cert"])
    _result("PORTAL", "ok")
    return 0


def cmd_guided(_argv):
    """check -> variant -> udid -> login -> portal."""
    state = load_state()
    print("BariSign %s" % VERSION)
    if poc_login._selftest(quiet=True) != 0:
        print("check  fail: run 'sh bin/barisign check' for the details")
        return 1
    print("check  ok")

    variants = load_variants()
    if variants:
        _stage("Variant")
        for index, item in enumerate(variants, 1):
            print("  %d  %s" % (index, item["name"]))
        choice = _ask("  choose 1-%d: " % len(variants), "1")
        if not (choice.isdigit() and 1 <= int(choice) <= len(variants)):
            choice = "1"
        chosen = variants[int(choice) - 1]
        state = update_state(variant=chosen["id"], bundle_id=chosen["bundle_id"])
        print("  %s" % chosen["name"])

    if state.get("udid") and _yes("  keep UDID %s?" % state["udid"], True):
        pass
    elif cmd_udid([]) != 0:
        return 1
    state = load_state()

    email = state.get("email") or _ask("  Apple ID: ")
    if not email:
        print("no Apple ID")
        return 2
    account = _sign_in(email)
    update_state(email=email)

    result = _portal(account, email, state.get("udid", ""), state.get("bundle_id", ""),
                     "BariSign device", "BariSign")
    update_state(bundle_id=result["bundle_id"], team_id=result["team_id"],
                 profile=result["profile"], key=result["key"], cert=result["cert"])
    _result("PORTAL", "ok")
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
    print("BariSign %s" % VERSION)
    print()
    print("  sh bin/barisign           guided run")
    print("  sh bin/barisign udid      this device's UDID")
    print("  sh bin/barisign login     Apple sign-in")
    print("  sh bin/barisign portal    UDID + certificate + profile")
    print("  sh bin/barisign status    what is saved")
    print("  sh bin/barisign check     diagnostics only")
    print("  sh bin/barisign version   which copy is running")
    print("  sh bin/barisign forget    clear the saved state")
    print()
    print("  login:  --use-clipboard (no typing)   --server URL (pin anisette)")
    print("  udid:   --no-open (do not open Safari)")


def main(argv):
    if not argv:
        return cmd_guided([])
    if argv[0] in ("-h", "--help", "help"):
        _usage()
        return 0

    command = argv[0]
    handler = COMMANDS.get(command)
    if handler is None:
        print("unknown command: %s" % command)
        _usage()
        return 2


    return handler(argv[1:])


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Fail as exc:
        print()
        print("! %s" % exc)
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
