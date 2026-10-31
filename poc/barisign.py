#!/usr/bin/env python3
# =============================================================================
#  BariSign — on-device installer (MIT)
#
#  Runs on the SIGNER device (a-Shell). The target needs Safari + Settings only.
#
#      python3 -u poc/barisign.py            guided run
#      python3 -u poc/barisign.py udid       serve the target's UDID page
#      python3 -u poc/barisign.py login      Apple sign-in
#      python3 -u poc/barisign.py portal     UDID + certificate + profile
#      python3 -u poc/barisign.py sign       download, sign and zip
#      python3 -u poc/barisign.py status     what is saved
#      python3 -u poc/barisign.py check      diagnostics only
#      python3 -u poc/barisign.py version    which copy is running
#      python3 -u poc/barisign.py forget     clear the saved state
# =============================================================================

import glob
import hashlib
import html
import json
import os
import shlex
import shutil
import socket
import threading
import re
import subprocess
import sys
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

VERSION = "0.8.4"
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
        original_revoke = module.revoke_to_make_room

        def portal_revoke(session, team):
            text = _SPINNER.text
            revoked = original_revoke(session, team)
            if revoked:
                # The certificate list and the prompt leave the screen full;
                # redraw the stage instead of continuing underneath them.
                _refresh_stage()
                if text:
                    _SPINNER.set(text)
            return revoked

        module.revoke_to_make_room = portal_revoke


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

# a-Shell runs SwiftTerm. 2J clears the visible page, 1;1H puts the cursor at the
# top: the same pair the app itself sends for Ctrl-L. 3J then drops the
# scrollback, because a-Shell restores the buffer on relaunch and the previous
# stage (the UDID instructions, for example) otherwise comes back. After a 3J the
# view's scroll offset can be stale, so a full page of newlines is printed first:
# that scrolls the buffer, lets the view follow to the bottom, and only then is
# the page cleared.
CLEAR = "\033[2J\033[1;1H"


def _clear_screen():
    """Clear the page and every saved line, and leave the view at the new page.

    Order matters. The previous stage is on the visible page; a page of newlines
    scrolls it into the saved lines, and only then does 3J drop them. 3J used to
    run first, so the stage that was just scrolled away was saved *after* the
    clear and stayed in the scrollback (sometimes, depending on what the buffer
    already held). A second page of newlines lets the view settle after 3J's
    buffer trim, then the page is cleared and the cursor homed.
    """
    try:
        import shutil
        rows = max(1, shutil.get_terminal_size((40, 24)).lines)
    except Exception:                                         # noqa: BLE001
        rows = 24
    # Two pages before 3J: the cursor can be anywhere on the page, so one page of
    # newlines does not always scroll a full screen (that is why the old stage
    # only sometimes ended up in the scrollback).
    sys.stdout.write("\033[0m" + "\n" * (2 * rows) + "\033[3J" + "\n" * rows + CLEAR)
    sys.stdout.flush()


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


_STAGE = {"title": "", "note": ""}


def _stage(title, note=""):
    """Clean screen, then the stage header. The note restores what the clear
    would otherwise wipe (the Apple ID, for example)."""
    _STAGE["title"] = title
    _STAGE["note"] = note
    _SPINNER.stop()
    _clear_screen()
    print("BariSign %s" % VERSION)
    print()
    print("== %s ==" % title.upper())
    if note:
        print("  %s" % note)
    print()


def _refresh_stage():
    """Redraw the current stage after something scrolls a lot (a revoke)."""
    if _STAGE["title"]:
        _stage(_STAGE["title"], _STAGE["note"])
    else:
        _clear_screen()


def _step(index, total, label):
    _SPINNER.set("(%d/%d) %s" % (index, total, label))


def _activity(message):
    """Vendored narration would fight the spinner, so it is not shown."""
    return


def _portal_line(message, prefix="  "):
    """A portal line that would otherwise be written over the spinner.

    The spinner is left stopped: restarting it after every line of a burst (the
    certificate list) put a spinner frame between the lines and made the block
    look twice as long. The next _step, or the end of a prompt, resumes it.
    """
    _SPINNER.stop()
    print("%s%s" % (prefix, message))


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


def _read_line(prompt):
    """One line, read with our own byte reader on a TTY.

    input() stops working in a-Shell once _masked_line has touched the terminal's
    line discipline, and the UDID prompt runs right after the "Return to open
    Safari" prompt (which uses that reader). So every visible prompt reads bytes
    itself; non-TTY runs still use input() so piped input keeps working.
    """
    if sys.stdin.isatty():
        return poc_login._masked_line(prompt, mask=False)
    try:
        return input(prompt)
    except EOFError:
        return ""


def _ask(prompt, default=None):
    entered = (_read_line(prompt) or "").strip()
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


def _sign_in(email, preferred=None, use_clipboard=False, staged=False):
    """Anisette, then Apple, then the password — never the other way round.

    Apple's October 2026 rules (verified against release notes, issue trackers and
    live servers): the sign-in must claim the platform the anisette data was made
    on, and only Mac anisette still signs in. So the Mac-emitting servers are tried
    first and their own client_info is what we send. A 503 or -80046 means the edge
    refused the identity: forget it and try the next server. The password is asked
    once, after Apple has answered, and never stored.

    `staged` says the caller already put up the Apple sign-in screen and asked for
    the address there; without it the address would be asked before the screen
    exists, which is what the UDID stage used to do.
    """
    if not staged:
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
    # Also gives the revoke path a stage to redraw, with the Apple ID restored.
    _stage("Portal", "Apple ID  %s" % email)
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
    print("  profile   expires %s, target: %s"
          % (expires, "yes" if ours else "NO"))
    if len(registered) > len(devices):
        print("  ! the profile is older than the device list: run portal again")
    if devices and not ours:
        print("  ! the profile does not list the target: an app signed with it")
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


def cmd_reset(_argv):
    """Remove everything this tool created; leave the installed program alone."""
    _stage("Reset")
    folder = os.path.dirname(STATE_DIR)
    folders = []
    files = []
    if os.path.isdir(STATE_DIR) and os.listdir(STATE_DIR):
        folders.append(STATE_DIR)
    folders.extend(sorted(glob.glob(os.path.join(folder, ".barisign-sign-*"))))
    for variant in load_variants():
        identifier = variant.get("id")
        if identifier:
            path = os.path.join(folder, identifier + ".ipa")
            if os.path.isfile(path):
                files.append(path)
    for pattern in ("*-signed.ipa", "*-signed.zip", "*.ipa.part", "*.zip.part"):
        files.extend(sorted(glob.glob(os.path.join(folder, pattern))))
    files = sorted(set(files))

    if not folders and not files:
        _result("RESET", "nothing to remove")
        return 0

    _line_clear()
    if os.path.isdir(STATE_DIR):
        print("  state     Apple ID, UDID, key, certificate, profile")
    if files:
        print("  files     %d downloaded or signed file%s"
              % (len(files), "" if len(files) == 1 else "s"))
    if len(folders) > 1:
        print("  temp      %d working folder%s"
              % (len(folders) - 1, "" if len(folders) == 2 else "s"))
    print()
    if not _yes("  delete these?", False):
        print("  nothing removed")
        return 0

    removed = 0
    for path in folders:
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    for path in files:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    _result("RESET", "ok  %d removed" % removed)
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




def _lan_ip():
    """The signer's address on the Wi-Fi network, without sending a packet."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(1.0)
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        probe.close()
        return address
    except Exception:                                         # noqa: BLE001
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:                                     # noqa: BLE001
            return "127.0.0.1"


UDID_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BariSign — target device</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 17px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0 auto; max-width: 34rem; padding: 2.2rem 1.3rem 3rem; }
  h1 { font-size: 1.7rem; margin: 0 0 .2rem; }
  .role { color: #666; margin: 0 0 1.6rem; }
  ol { padding-left: 1.3rem; }
  li { margin: .55rem 0; }
  a { color: #0a6cff; }
  form { margin-top: 1.5rem; }
  input[type=text] { box-sizing: border-box; width: 100%; font: inherit;
         padding: .75rem; border: 1px solid #999; border-radius: .55rem; }
  button { font: inherit; font-weight: 600; margin-top: .8rem; width: 100%;
         padding: .8rem; border: 0; border-radius: .55rem; background: #0a6cff;
         color: #fff; }
  .error { color: #b00020; font-weight: 600; }
  .small { color: #666; font-size: .9rem; margin-top: 1.8rem; }
  code { overflow-wrap: anywhere; }
</style>
</head>
<body>
<h1>BariSign</h1>
<p class="role">This is the <strong>target device</strong> — the one that will get the app.</p>
<ol>
  <li>Install the profile from <a href="__UDID_URL__">median.co/udid</a>.</li>
  <li>Copy the UDID shown at the end.</li>
  <li>Paste it below and submit.</li>
</ol>
__ERROR__
<form method="post" action="">
  <input type="text" name="udid" inputmode="text" autocomplete="off"
         autocapitalize="off" autocorrect="off" spellcheck="false"
         placeholder="00008110-001a2c3d4e5f6071" aria-label="UDID">
  <button type="submit">Submit UDID</button>
</form>
<p class="small">Both devices must be on the same Wi-Fi. The signer device is
waiting; after submitting you can close this tab.</p>
</body>
</html>
"""

UDID_DONE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BariSign — UDID received</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 17px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0 auto; max-width: 34rem; padding: 2.2rem 1.3rem; }
  code { overflow-wrap: anywhere; }
  .ok { color: #137333; font-weight: 600; }
</style></head>
<body>
<h1>UDID received</h1>
<p class="ok">The signer device has it and is continuing.</p>
<p><code>__UDID__</code></p>
<p>You can close this tab and return to the signer device.</p>
</body></html>
"""


class _UDIDServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.result = ""
        self.done = threading.Event()
        self.timeout = 1.0


class _UDIDHandler(BaseHTTPRequestHandler):
    server_version = "BariSignUDID/1.0"

    def log_message(self, fmt, *args):                        # noqa: A003
        return

    def _send(self, status, body):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):                                         # noqa: N802
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        self._send(200, UDID_PAGE.replace("__UDID_URL__", UDID_URL)
                              .replace("__ERROR__", ""))

    def do_POST(self):                                        # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 1 or length > 4096:
            self._send(413, UDID_PAGE.replace("__UDID_URL__", UDID_URL)
                                  .replace("__ERROR__", '<p class="error">Empty submission.</p>'))
            return
        raw = self.rfile.read(length).decode("utf-8", "replace")
        fields = urllib.parse.parse_qs(raw, keep_blank_values=True)
        typed = (fields.get("udid") or [""])[0]
        udid = extract_udid(typed)
        if not udid:
            self._send(200, UDID_PAGE.replace("__UDID_URL__", UDID_URL)
                                  .replace("__ERROR__",
                                           '<p class="error">That is not a UDID. '
                                           'Copy the 24-character value (or 40 hex characters) '
                                           'from the profile page.</p>'))
            return
        self.server.result = udid
        self._send(200, UDID_DONE.replace("__UDID__", html.escape(udid)))
        self.server.done.set()

    def log_request(self, code="-", size="-"):                # noqa: A003
        return


def _serve_udid_page():
    """Serve the target-device UDID page and return the submitted value."""
    server = _UDIDServer(("0.0.0.0", 0), _UDIDHandler)
    port = server.server_address[1]
    address = _lan_ip()
    print("  On the TARGET device, open this address in Safari:")
    print()
    print("    http://%s:%d/" % (address, port))
    print()
    if address.startswith("127."):
        print("  ! no Wi-Fi address: connect the signer to the Wi-Fi network")
        print()
    print("  The page has the median.co/udid link and the UDID box.")
    print("  Allow Local Network if iOS asks. Waiting...")
    print()
    try:
        while not server.done.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        print()
        server.result = ""
    finally:
        server.server_close()
    return server.result


def cmd_udid(argv):
    """Get the target device's UDID: serve a page, or paste it manually."""
    _stage("UDID")
    typed = [item for item in argv if not item.startswith("-")]
    udid = extract_udid(typed[0]) if typed else ""
    manual = "--manual" in argv or "--no-open" in argv
    if not udid and "--pair" in argv:
        print("  First pair the TARGET; the pairing gives its UDID.")
        print("  If Paired Macs is not available, install the original IPA on")
        print("  the target first (it makes Developer Mode appear), then retry.")
        print()
        pair = os.path.join(_HERE, "pair_install.py")
        code = subprocess.call([sys.executable, "-u", pair, "--pair-only",
                                "--timeout", "120"])
        udid = extract_udid(load_state().get("udid", ""))
        if code != 0 or not udid:
            print("  pairing did not return a UDID; falling back to the page")
            udid = ""
    if not udid and manual:
        print("  On this signer device, install the profile from:")
        print("    %s" % UDID_URL)
        print("  then paste the UDID below.")
        print()
        for _attempt in range(3):
            udid = extract_udid(_ask("  UDID: "))
            if udid:
                break
            print("  not a UDID; try again")
    elif not udid:
        print("  This is the SIGNER device.")
        print("  The TARGET device — the one that gets the app — opens a page here.")
        print("  Both devices must be on the same Wi-Fi.")
        print()
        udid = _serve_udid_page()
        if not udid:
            print("  stopped")
            return 1

    if not udid:
        print("  stopped")
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
    given = bool(positional)
    email = positional[0] if given else ""
    if not email:
        # The address belongs on the sign-in screen, not on whatever screen was
        # showing when `login` started.
        _stage("Apple sign-in")
        email = _ask("  Apple ID: ").strip()
        if not email:
            print("no Apple ID")
            return 2
    account = _sign_in(email, preferred, use_clipboard, staged=not given)
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
    result = _portal(account, email, udid, bundle_id, "BariSign device", "BariSign")
    update_state(email=email, udid=udid, variant=variant or state.get("variant", ""),
                 bundle_id=result["bundle_id"], team_id=result["team_id"],
                 profile=result["profile"], key=result["key"], cert=result["cert"])
    _result("PORTAL", "ok")
    return 0


def _share_file(path):
    """Open a-Shell's preview so the share sheet can AirDrop the file."""
    print("  sharing   %s" % _display_path(path))
    marker = os.environ.get("TERM_PROGRAM", "").lower()
    a_shell = (sys.platform.startswith(("ios", "darwin"))
               or "a-shell" in marker or "ashell" in marker)
    if a_shell and sys.stdin.isatty():
        os.system("view %s" % shlex.quote(path))
    else:
        print("  path      %s" % path)


def _display_path(path):
    """The path as the Files app shows it, not the long container path."""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    full = os.path.abspath(path)
    if full == docs or full.startswith(docs + os.sep):
        return "Device/a-Shell/" + os.path.relpath(full, docs).replace(os.sep, "/")
    return path




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
                                 make_zip="--no-zip" not in argv,
                                 drop_extensions="--no-extensions" in argv)
    except signer.SigningError as exc:
        _line_clear()
        print()
        print("! %s" % exc)
        return None
    update_state(ipa=ipa, signed_ipa=result["ipa"], zip=result["zip"])
    _result("SIGN", "ok  %s" % os.path.basename(result["ipa"]))
    if result["zip"]:
        print("  zip   %s" % _display_path(result["zip"]))
    print("  next  sh bin/barisign pair  (install on the target over Wi-Fi)")
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

    _stage("Download")
    ipa, _info = _obtain_ipa(state, argv)
    if not ipa:
        return 1
    print("  original  %s" % _display_path(ipa))
    print("  if the target has no Developer Mode, share this IPA to it and")
    print("  tap it once; the Developer Mode switch appears after the attempt.")
    if _yes("  share the original IPA to the target now?", False):
        _share_file(ipa)

    if state.get("udid") and _yes("  keep UDID %s?" % state["udid"], True):
        pass
    elif cmd_udid(["--pair"]) != 0:
        return 1
    state = load_state()

    email = state.get("email") or ""
    if not email:
        # Same rule as `login`: the stage comes first, the address is asked on it.
        _stage("Apple sign-in")
        email = _ask("  Apple ID: ").strip()
        if not email:
            print("no Apple ID")
            return 2
    account = _sign_in(email, staged=not state.get("email"))
    update_state(email=email)

    result = _portal(account, email, state.get("udid", ""), state.get("bundle_id", ""),
                     "BariSign device", "BariSign")
    update_state(bundle_id=result["bundle_id"], team_id=result["team_id"],
                 profile=result["profile"], key=result["key"], cert=result["cert"])
    _result("PORTAL", "ok")

    state = load_state()
    _stage("Sign")
    saved = signer.find_saved_paths(STATE_DIR)
    result = _sign_now(ipa, saved, argv)
    if result is None:
        return 1
    if "--no-install" not in argv and _yes("  install on the target now?", True):
        pair = os.path.join(_HERE, "pair_install.py")
        command = [sys.executable, "-u", pair, "--ipa", result["ipa"]]
        paired = load_state()
        if paired.get("paired") and paired.get("udid"):
            command += ["--udid", paired["udid"]]
        return subprocess.call(command)
    return 0


def cmd_pair(argv):
    """Pair the target and install the signed IPA (the engine in pair_install.py)."""
    pair = os.path.join(_HERE, "pair_install.py")
    return subprocess.call([sys.executable, "-u", pair] + list(argv))


COMMANDS = {
    "pair": cmd_pair,
    "version": cmd_version,
    "check": cmd_check,
    "status": cmd_status,
    "forget": cmd_forget,
    "reset": cmd_reset,
    "udid": cmd_udid,
    "login": cmd_login,
    "portal": cmd_portal,
    "sign": cmd_sign,
    "all": cmd_guided,
}


_OPTIONS = {
    "all": (
        ("--no-install", "do not start the target install after signing"),
    ),
    "pair": (
        ("--pair-only", "pair, print the UDID and write the pairing file"),
        ("--ipa FILE", "install this signed IPA"),
        ("--udid UDID", "reuse an existing pairing for this UDID"),
        ("--check", "python, cryptography and engine import only"),
        ("--setup", "install the engine only"),
        ("--timeout N", "give up if the target does not pair"),
    ),
    "login": (
        ("--use-clipboard", "take the password from the clipboard, no typing"),
        ("--server URL", "pin an anisette server"),
    ),
    "portal": (
        ("--udid UDID", "register another target device too"),
    ),
    "sign": (
        ("--ipa FILE", "sign this file instead of downloading"),
        ("--out FILE", "write the signed IPA here"),
        ("--no-zip", "do not package the .zip"),
        ("--no-extensions", "drop PlugIns (an app extension needs its own App ID)"),
    ),
    "udid": (
        ("--pair", "pair the target first and take its UDID from the pairing"),
        ("--manual", "paste the UDID on the signer instead of serving the page"),
    ),
}


def _usage():
    print("BariSign %s" % VERSION)
    print()
    print("  sh bin/barisign           guided run")
    print("  sh bin/barisign udid      serve the target-device UDID page")
    print("  sh bin/barisign login     Apple sign-in")
    print("  sh bin/barisign portal    UDID + certificate + profile")
    print("  sh bin/barisign sign      download, sign and zip the chosen variant")
    print("  sh bin/barisign pair      pair the target and install over Wi-Fi")
    print("  sh bin/barisign status    what is saved")
    print("  sh bin/barisign check     diagnostics only")
    print("  sh bin/barisign version   which copy is running")
    print("  sh bin/barisign forget    clear the saved state")
    print("  sh bin/barisign reset      remove saved state, IPAs and zips")
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
