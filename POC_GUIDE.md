# PoC Guide — run this before Phase 1

Three small checks (A, B, C) on your **iPhone 13 (iOS 27)** and **iPad (A16)**.
They take about 15 minutes. **None of them asks for your Apple ID or password.**
Nothing here installs anything permanently except the two apps named in the steps.

Fill in the **Result form** at the end (or just send a photo/transcript of the a-Shell screen).

---

## Before you start

You will type or paste short commands into an app called **a-Shell**.
"Terminal" just means a black screen where you type text and press Enter/Return.

### How to install a-Shell

1. Open the **App Store**.
2. Search for **a-Shell**.
3. Install the one made by **Nicolas Holzschuch** — the app is called **a-Shell** (not "a-Shell mini"; mini also works but full is safer).
4. Open it. You will see a black screen with a blinking cursor and a keyboard.

### How to paste a command into a-Shell

1. In this guide, press and hold the command box → **Copy**.
2. Switch to a-Shell.
3. Tap the black area once, then **press and hold** on it until a menu appears → tap **Paste**.
4. Press **Return/Enter** on the keyboard to run it.

> If the Return key does not run the command, look for a small ▸ (play) or "return" key at the bottom-right of the a-Shell keyboard and tap it.

> Tip: after each command you can scroll up to see results. If a command says
> `command not found`, that is a normal, useful result — keep going.

---

# PoC-A — Does the on-device runtime work?

**Why:** we need to know the a-Shell runtime on *your* device has Python and the right pieces.

### A1 — Python version

Copy, paste into a-Shell, press Return:

```
python3 --version
```

- **PASS** if it prints something like `Python 3.13.1`.
- **FAIL** if it says `command not found`. Then try the same line with `python` instead of `python3` and note which one worked.

### A2 — Required pieces are present

**Copy this exactly as ONE line** and paste:

```
python3 -c "import sys,ssl,hashlib,zipfile,plistlib,zlib,socket,urllib.request; print('A2 = PASS'); print('python',sys.version.split()[0]); print('ssl',ssl.OPENSSL_VERSION)"
```

- **PASS** if you see `A2 = PASS` plus a Python version and an "OpenSSL"/"LibreSSL" line.
- **FAIL** if you see an error beginning with `Traceback` or `ModuleNotFoundError`. Copy the error text for the report.

### A3 — Optional extras (informational)

Run each line separately:

```
curl --version
```

```
openssl version
```

```
unzip -v
```

```
python3 -c "import importlib.util as u; print('cryptography available:', bool(u.find_spec('cryptography')))"
```

- We **expect** `curl` to work and `openssl` / `unzip` to say `command not found`. That is fine and matches the design (we use Python instead).
- Just record all four lines of output.

### PoC-A result

- **Pass** = A1 + A2 both PASS.

---

# PoC-B — Does iOS 27 install an IPA? (the install route)

**Why:** we plan to deliver the signed app by **AirDrop**. This checks what iOS 27 actually does.

There are three parts. **B1 and B2 can be done now. B3 is the real test but needs a signed IPA**
(we can produce one later; if you already have a working sideloaded app, you can use it for B3).

### B1 — Files routing (iPhone 13)

1. On the iPhone, open **Safari**.
2. Go to this address (type it or copy it):

```
https://github.com/SideStore/SideStore/releases/download/0.6.4/SideStore.ipa
```

3. When Safari asks, choose **Download** (it saves to the **Files** app → *Downloads* folder).
4. Open the **Files** app → **Downloads** → tap **SideStore.ipa**.

Record exactly what happens:

- Does an "Install" / "Open in..." prompt appear? Does it start installing? Does it show an error?
- **We expect** iOS to either refuse it (because this copy is **not yet signed for your device**) or show an install attempt that fails. Either result is useful — write down the exact wording.

### B2 — AirDrop iPhone 13 → iPad (A16)

1. On the two devices, turn on **Wi‑Fi** and **Bluetooth**, and set **AirDrop** to **Contacts Only** or **Everyone** (*Settings → General → AirDrop*).
2. On the **iPhone**: open **Files** → **Downloads** → **long-press** `SideStore.ipa` → **Share** → **AirDrop** → tap the **iPad**.
3. On the **iPad**: accept the file, then tap the received file.

Record:

- Did the iPad offer to **install** it?
- Did it install, or refuse? Exact wording of any error.

### B3 — The real test (needs a validly signed IPA)

Do this **only** if you have an IPA that is already signed for your device (for example, an app
you previously sideloaded, or a build you already have). If you do not, skip B3 for now and say so.

1. AirDrop that **signed** IPA from the iPhone to the iPad (same steps as B2).
2. On the iPad, tap the received file and let it install.
3. Open the installed app.

- **PASS** = the app installs **and opens**.
- Note: an app that installs but will not open usually means the signature is not valid for that device.

### PoC-B result

- **Routing Pass** = iOS clearly reacts to `.ipa` files (install UI or a clear, specific error) in both Files and AirDrop.
- **Full Pass** = a *signed* IPA installs and opens via AirDrop on iOS 27.

---

# PoC-C — The device's own "door" for installing (LocalDevVPN peer)

**Why:** this is the mechanism that lets software *on the phone* talk to the phone's own install
service. Everything about "no computer" depends on it.

### C0 — Install and connect the VPN

1. App Store → search **LocalDevVPN** → install (free, by jkcoxson). *(If unavailable, install **StosVPN** instead.)*
2. Open it and turn the VPN **on**. Approve the iOS "VPN configuration" prompt.
3. Wait until the VPN shows **Connected**. Keep it connected for the rest of PoC-C.

> We only need the VPN as a "tunnel". It does not send your data anywhere unusual.

### C1 — See the addresses

In a-Shell:

```
ifconfig
```

Look for a **`utun`** or **`tap`** entry with an address starting with **`10.7.`**.
Copy that whole `ifconfig` output for the report. (The expected peer is **`10.7.0.1`**.)

### C2 — Idle test (does the door stay open?)

**Copy as ONE line:**

```
python3 -c "import socket,time; s=socket.create_connection(('10.7.0.1',49152),timeout=5); print('C2-1 TCP OPEN'); time.sleep(5); print('C2-2 STILL OPEN AFTER 5s IDLE -> PASS')"
```

- **PASS** = you see `C2-1 TCP OPEN` and then `C2-2 STILL OPEN AFTER 5s IDLE -> PASS`.
- **FAIL** = an error (e.g. `ConnectionRefusedError`, `timed out`, or it closes). Copy the exact error.
- If it fails with `10.7.0.1` but C1 showed a different `10.7.x.x` address, repeat the command replacing `10.7.0.1` with that address.

### C3 — Control test (this one SHOULD fail)

**One line:**

```
python3 -c "import socket; s=socket.socket(); s.settimeout(5); print('C3 connect result (non-zero = refused = EXPECTED):', s.connect_ex(('127.0.0.1',49152)))"
```

- **Expected (good)** = a **non-zero** number (e.g. `61`), meaning nothing is listening on plain loopback.
- If it returns `0`, tell me — that changes the design.

### C4 — Send test (a reset here is EXPECTED and useful)

**One line:**

```
python3 -c "import socket,time; s=socket.create_connection(('10.7.0.1',49152),timeout=5); print('C4-1 TCP OPEN'); s.settimeout(3); s.sendall(bytes([0])); print('C4-2 SENT 1 BYTE'); time.sleep(1); d=s.recv(64); print('C4-3 GOT', len(d), 'BYTES BACK')"
```

- If it **resets/closes** after sending (you may see `ConnectionResetError` or nothing after C4-2), that is **expected** — it confirms the door rejects unknown data, matching the research.
- If it returns bytes, also expected-ish — just record them.

### PoC-C result

- **Pass** = C2 stays open AND C3 is refused (non-zero).

---

# Result form (fill in and send back)

```
Device: iPhone 13 (iOS version: ______)   iPad model: ____________ (iOS version: ______)

PoC-A  A1 python3 --version output: ..............................
       (if needed) `python` worked instead? yes / no
       A2 output: ..............................................
       A3 curl / openssl / unzip / cryptography lines: ........
       => PoC-A PASS?  yes / no

PoC-B  B1 Files tap: what happened? ............................
       B2 AirDrop to iPad: offer to install?  yes / no
                           exact message: ......................
       B3 (only if you had a signed IPA) installed+opened?  yes / no / skipped

PoC-C  C1 10.7.x.x address seen: ...............................
       C2 result: PASS / error text ............................
       C3 connect_ex number: ...................................
       C4 result: reset / bytes back / error text .............
       => PoC-C PASS?  yes / no
```

That is all. Send the form (or screenshots) and I will decide the Phase 1 build from your results.
