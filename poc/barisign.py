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
#      python3 -u poc/barisign.py sign       download, sign and zip
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
import shlex
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
                    download_profile, profile_plist, list_devices)

# portal.py imported MACHINE_NAME by name, so both modules need it. It is what
# Apple shows next to a development certificate, and what lets a later run reuse
# the certificate this tool created instead of burning another slot.
signin.MACHINE_NAME = "BariSign"
portal.MACHINE_NAME = "BariSign"
try:
    import signer                                              # noqa: E402
    import download                                            # noqa: E402
except ImportError as exc:                                    # noqa: BLE001
    print("Could not import the signer or downloader module: %s" % exc)
    print("poc/signer.py or poc/download.py is missing or incomplete.")
    print("Fix it by re-running the installer in the BariSign folder:  sh install.sh")
    sys.exit(2)

VERSION = "0.6.3"
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
    """The vendored stack narrates every step; it all goes through one line.

    portal.py imported info/step/warn/ask by name, so rebinding them in signin
    is not enough: its own references still printed straight to stdout and
    landed on top of the moving line.
    """
    for name in ("info", "ok", "step", "warn"):
        if hasattr(signin, name):
            setattr(signin, name, lambda message="", *a, **k: _activity(message))
    signin.PROGRESS = lambda message="": _activity(message)
    if "portal" in sys.modules:
        module = sys.modules["portal"]
        module.info = lambda message="", *a, **k: _portal_line(message)
        module.ok = lambda message="", *a, **k: _portal_line(message)
        module.warn = lambda message="", *a, **k: _portal_line(message, "  ! ")
        module.step = lambda message="", *a, **k: _portal_step(message)
        # Replace portal's ask/confirm with our own reader: input() inside the
        # vendored code kept losing the typed number to the moving line.
        module.ask = lambda prompt, default=None: _portal_ask(prompt, default)
        module.confirm = lambda prompt, default=False: _portal_confirm(prompt, default)


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

# H homes first, then 2J clears the screen. 3J (clear the scrollback too) is not
# used: in a-Shell it leaves the cursor where the previous command ended, so on a
# screen with output every line printed below it scrolled the stage header out of
# view and the user had to scroll back up. Clearing the scrollback is not worth
# that.
CLEAR = "\033[H\033[2J"


def _columns():
    try:
        import shutil
        return max(20, shutil.get_terminal_size((40, 20)).columns)
    except Exception:                                         # noqa: BLE001
        return 40



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
                width = _columns() - 1
                sys.stdout.write("\r\033[2K" + ("  " + text + " " + "." * self.dots)[:width])
                sys.stdout.flush()
                with self._lock:
                    self.dots = (self.dots + 1) % 4
            # Wake every 50 ms so stop() can join without waiting a full tick.
            for _ in range(8):
                if self._stop.wait(0.05):
                    return

    def stop(self):
        self._stop.set()
        thread = self._thread
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            # Join, not just signal: a thread woken mid-write could otherwise
            # erase the first characters of a prompt that follows.
            thread.join(0.7)
        with self._lock:
            self.text = ""
        sys.stdout.write("\r\033[2K")
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


def _portal_line(message, prefix="  "):
    """A portal line that would otherwise be written over the spinner."""
    text = _SPINNER.text
    _SPINNER.stop()
    print("%s%s" % (prefix, message))
    if text:
        _SPINNER.set(text)


def _portal_step(message):
    """Keep the stage number, replace the label: '(4/5) generating a key'."""
    text = _SPINNER.text
    prefix = ""
    if text.startswith("(") and ") " in text:
        prefix = text.split(") ", 1)[0] + ") "
    _SPINNER.set(prefix + message)


def _portal_read(prompt, default=None):
    """One line, read with the same byte reader as the 2FA code.

    input() here went through the vendored ask() while the spinner was running;
    the typed digits were wiped and the choice came back empty. This reads on a
    TTY byte by byte (visible, modes restored), and falls back to input() only
    when stdin is not a terminal.
    """
    if sys.stdin.isatty():
        answer = poc_login._masked_line(prompt, mask=False)
    else:
        try:
            answer = input(prompt)
        except EOFError:
            answer = ""
    answer = (answer or "").strip()
    if not answer and default is not None:
        return default
    return answer


def _portal_question(reader):
    """Stop the spinner, read with `reader`, then put the spinner back."""
    text = _SPINNER.text
    _SPINNER.stop()
    try:
        return reader()
    finally:
        if text:
            _SPINNER.set(text)


def _portal_ask(prompt, default=None):
    suffix = " [%s]: " % default if default else ": "
    return _portal_question(lambda: _portal_read(prompt + suffix, default))


def _portal_confirm(prompt, default=False):
    suffix = " [%s] " % ("Y/n" if default else "y/N")

    def read():
        answer = _portal_read(prompt + suffix, "").lower()
        if not answer:
            return default
        return answer in ("y", "yes")

    return _portal_question(read)


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
    registered = list_devices(session, team)

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
    print("  devices   %d registered on this team, %d in the profile"
          % (len(registered), len(devices)))
    print("  profile   expires %s, this device: %s"
          % (expires, "yes" if ours else "NO"))
    if len(registered) > len(devices):
        print("  ! the profile is older than the device list: run portal again")
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
            ("variant", state.get("variant")), ("release", state.get("asset_version")),
            ("bundle", state.get("bundle_id")), ("team", state.get("team_id")),
            ("cert", state.get("cert")), ("profile", state.get("profile")),
            ("ipa", _display_path(state["ipa"]) if state.get("ipa") else None),
            ("signed", _display_path(state["signed_ipa"]) if state.get("signed_ipa") else None),
            ("zip", _display_path(state["zip"]) if state.get("zip") else None))
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

    udid = ""
    if "--udid" in argv:
        position = argv.index("--udid") + 1
        if position >= len(argv):
            print("--udid needs a value")
            return 2
        udid = normalise_udid(argv[position])
        if not udid:
            print("! that is not a UDID")
            return 2
    if not udid:
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


def _display_path(path):
    """The path as the Files app shows it, not the long container path."""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    full = os.path.abspath(path)
    if full == docs or full.startswith(docs + os.sep):
        return "Device/a-Shell/" + os.path.relpath(full, docs).replace(os.sep, "/")
    return path


def _open_zip_preview(path):
    """Enter opens a-Shell's preview, which has the share sheet for AirDrop.

    `view` is one of a-Shell's own commands, not a file in PATH, so searching
    the PATH for it finds nothing. On iOS it is simply run; anywhere else the
    path is printed (on a Mac `view` is vim).
    """
    if not sys.stdin.isatty():
        return
    _press_return("  return  open the zip preview: ")
    marker = os.environ.get("TERM_PROGRAM", "").lower()
    a_shell = (sys.platform.startswith(("ios", "darwin"))
               or "a-shell" in marker or "ashell" in marker)
    if a_shell and not os.environ.get("BARISIGN_NO_VIEW"):
        if os.system("view %s" % shlex.quote(path)) == 0:
            return
    print("  view  %s" % _display_path(path))


_SIGN_STEPS = {"read": (1, 5, "ipa"), "open": (2, 5, "unpack"),
               "prepare": (2, 5, "prepare"), "sign": (3, 5, "sign"),
               "check": (4, 5, "verify"), "write": (4, 5, "package"),
               "zip": (5, 5, "zip"), "done": (5, 5, "done")}


def _sign_progress(label):
    index, total, text = _SIGN_STEPS.get(label, (3, 5, label))
    _step(index, total, text)


def _choose_variant(state, variants):
    """The one-line menu; remembers the choice in state."""
    _stage("Variant")
    for index, item in enumerate(variants, 1):
        print("  %d  %s" % (index, item["name"]))
    choice = _ask("  choose 1-%d: " % len(variants), "1")
    if not (choice.isdigit() and 1 <= int(choice) <= len(variants)):
        choice = "1"
    chosen = variants[int(choice) - 1]
    update_state(variant=chosen["id"], bundle_id=chosen["bundle_id"])
    print("  %s" % chosen["name"])
    return chosen


def _obtain_ipa(state, argv):
    """Return (path, info): --ipa, else the chosen variant's current release."""
    typed = argv_get(argv, "--ipa")
    if typed:
        path = os.path.abspath(typed)
        if not os.path.isfile(path):
            print("  ! not found: %s" % path)
            return "", {}
        return path, {"version": os.path.basename(path)}

    variants = load_variants()
    variant = download.variant_by_id(variants, state.get("variant") or "")
    if variant is None:
        if not variants:
            print("  ! variants.json is missing or empty")
            return "", {}
        variant = _choose_variant(state, variants)
        state = load_state()

    folder = os.path.dirname(STATE_DIR)
    try:
        _step(1, 2, "resolve")
        info = download.resolve(variant)
    except download.DownloadError as exc:
        saved = os.path.join(folder, (variant.get("id") or "barisign") + ".ipa")
        if os.path.isfile(saved) and os.path.getsize(saved) > 0:
            _result("IPA", "%s (no GitHub; using the saved file)"
                    % os.path.basename(saved))
            return saved, {"version": "saved file"}
        _line_clear()
        print()
        print("! %s" % exc)
        return "", {}

    _line_clear()
    print("  ipa   %s  (%.1f MB)" % (info["version"], (info["size"] or 0) / 1048576.0))
    if info["warning"]:
        print("  ! nightly builds may be unstable")
    update_state(variant=variant["id"], asset_version=info["version"])

    last = [-5]

    def moved(received, total):
        percent = int(received * 100 / total) if total else 0
        if percent >= last[0] + 5 or percent == 100:
            last[0] = percent
            _step(2, 2, "download %d%%" % percent)

    try:
        info = download.ensure(variant, folder, moved)
    except download.DownloadError as exc:
        _line_clear()
        print()
        print("! %s" % exc)
        return "", {}
    update_state(ipa=info["path"])
    return info["path"], info


def _sign_now(ipa, saved, argv):
    """Run the signer, then print the two closing lines."""
    try:
        result = signer.sign_ipa(ipa, saved["profile"], saved["key"], saved["cert"],
                                 output_ipa=argv_get(argv, "--out") or None,
                                 udid=saved.get("udid") or None,
                                 progress=_sign_progress,
                                 make_zip="--no-zip" not in argv)
    except signer.SigningError as exc:
        _line_clear()
        print()
        print("! %s" % exc)
        return None
    update_state(ipa=ipa, signed_ipa=result["ipa"], zip=result["zip"])
    _result("SIGN", "ok  %s" % os.path.basename(result["ipa"]))
    if result["zip"]:
        print("  zip   %s" % _display_path(result["zip"]))
    print("  next  airDrop the zip, unzip it, airDrop the .ipa back to this iPhone")
    if result["zip"]:
        _open_zip_preview(result["zip"])
    return result


def cmd_sign(argv):
    """Download the chosen variant (unless --ipa) and sign it."""
    _stage("Sign")
    saved = signer.find_saved_paths(STATE_DIR)
    missing = [label for label, path in (("key", saved["key"]),
                                         ("cert", saved["cert"]),
                                         ("profile", saved["profile"])) if not path]
    if missing:
        print("  ! no %s: run 'sh bin/barisign portal' first" % ", ".join(missing))
        return 2

    ipa, _info = _obtain_ipa(load_state(), argv)
    if not ipa:
        return 1
    if _sign_now(ipa, saved, argv) is None:
        return 1
    return 0


def cmd_guided(argv):
    """check -> variant -> udid -> login -> portal -> download -> sign -> zip."""
    state = load_state()
    print("BariSign %s" % VERSION)
    if poc_login._selftest(quiet=True) != 0:
        print("check  fail: run 'sh bin/barisign check' for the details")
        return 1
    print("check  ok")

    variants = load_variants()
    if variants:
        _choose_variant(state, variants)
        state = load_state()

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

    state = load_state()
    ipa, _info = _obtain_ipa(state, argv)
    if not ipa:
        return 1
    saved = signer.find_saved_paths(STATE_DIR)
    if _sign_now(ipa, saved, argv) is None:
        return 1
    return 0


COMMANDS = {
    "version": cmd_version,
    "check": cmd_check,
    "status": cmd_status,
    "forget": cmd_forget,
    "udid": cmd_udid,
    "login": cmd_login,
    "portal": cmd_portal,
    "sign": cmd_sign,
    "all": cmd_guided,
}


_OPTIONS = {
    "login": (
        ("--use-clipboard", "take the password from the clipboard, no typing"),
        ("--server URL", "pin an anisette server"),
    ),
    "portal": (
        ("--udid UDID", "register another device too, e.g. an iPad"),
    ),
    "sign": (
        ("--ipa FILE", "sign this file instead of downloading"),
        ("--out FILE", "write the signed IPA here"),
        ("--no-zip", "do not package the .zip"),
    ),
    "udid": (
        ("--no-open", "do not open Safari"),
    ),
}


def _usage():
    print("BariSign %s" % VERSION)
    print()
    print("  sh bin/barisign           guided run")
    print("  sh bin/barisign udid      this device's UDID")
    print("  sh bin/barisign login     Apple sign-in")
    print("  sh bin/barisign portal    UDID + certificate + profile")
    print("  sh bin/barisign sign      download, sign and zip the chosen variant")
    print("  sh bin/barisign status    what is saved")
    print("  sh bin/barisign check     diagnostics only")
    print("  sh bin/barisign version   which copy is running")
    print("  sh bin/barisign forget    clear the saved state")
    print()
    print("  options: sh bin/barisign <command> help")


def _options(command):
    print("BariSign %s" % VERSION)
    print()
    print("  sh bin/barisign %s" % command)
    for flag, text in _OPTIONS[command]:
        print()
        print("    %s" % flag)
        print("        %s" % text)


def main(argv):
    if not argv:
        return cmd_guided([])
    if argv[0] in ("-h", "--help", "help"):
        _usage()
        return 0
    if len(argv) >= 2 and argv[1] == "help" and argv[0] in _OPTIONS:
        _options(argv[0])
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
