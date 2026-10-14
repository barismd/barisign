# UDID profile test — v2 (after "profile could not be installed")

Your Device B answered: **"Profil yüklenemedi, bilinmeyen bir hata oluştu"** — the profile was refused.
So we first find **which layer** iOS is refusing, with three throwaway profiles, and only then run the
real thing.

**Sharing out of a-Shell:** a-Shell's own commands contain **no `share`**, and the only in-app share
sheet comes from `open <file>` — which can fail silently (`ios_system/shell_cmds_ios/open.m`: if the path
is not found it degrades to `openurl`; and it presents on the deprecated `keyWindow`, which is nil in a
scene-based app — see `PHASE0_REPORT.md` §28). The reliable, still-in-terminal route is a **URL scheme**
that opens the Files app straight at the folder:

```
openurl shareddocuments://<absolute folder path printed by the script>
```

Then long-press the file → Share → AirDrop. `python3 -u poc_login.py --diag-profiles` and
`--airdrop` print that exact command for you.

---

## Step 0 — find out what iOS is refusing (2 minutes)

Your Device B answered **"Profil yüklenemedi, bilinmeyen bir hata oluştu"**. Two things were wrong with
the last test round, and both are fixed here:

- The **empty profile was a bad control**: iOS said *"this profile is empty"* and never tried to install
  it, so it told us nothing. The control is now a **Web Clip** — a payload iOS definitely supports, which
  adds one removable icon to the Home Screen.
- The `open` command is **not** a reliable way to share, so the folder is opened in Files instead.

On Device A, in a-Shell:

```
cd ~/Documents/barisign
```

```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/poc/udid_share.py -o udid_share.py
```

```
python3 -u udid_share.py --diag-profiles
```

Then, with the single command it prints (this is the working one — it opens **Files** at the folder,
using a URL scheme instead of the broken file-path branch):

```
openurl shareddocuments:///private/var/mobile/Containers/Data/Application/<your-uuid>/Documents/barisign
```

Long-press each file → **Share → AirDrop** → Device B, **in this order**:

| File | What it is | What the result tells us |
|---|---|---|
| `barsign-test1-webclip.mobileconfig` | a **Web Clip** (one removable Home Screen icon) | **the control.** If this installs, unsigned profiles work and any later failure is about the payload type. If it does **not** install, the problem is profile installation or the AirDrop delivery, not our payload |
| `barsign-test2-profservice.mobileconfig` | `Profile Service`, **only** Apple's documented keys | **iOS 27 rejects the `Profile Service` payload** → this whole no-app route is dead |
| `barsign-test4-profservice-https.mobileconfig` | the **same** payload with an ordinary `https://` callback — **no certificate, no trust step** | **the decisive one.** Installs ⇒ the payload type is fine and `http://` was the problem; still refused ⇒ the payload is retired on iOS 27 |
| `barsign-test3-profservice-lifetime.mobileconfig` | the same plus the two lifetime keys | informational (they are off by default now) |

Install each one, note the exact result, then remove it: for the Web Clip, long-press the icon →
Remove; for the others, Settings → General → VPN & Device Management → Remove Profile.

**If the Web Clip installs but `Profile Service` does not**, that is the answer we are looking for and it
means Apple has retired the payload: the receiver will need an app after all, and we move to the plan in
`PHASE0_REPORT.md` §22.3. If **even the Web Clip** fails, tell me — then the problem is the delivery
method (AirDrop) and we retry the same file through the QR/HTTP route.

---

## Step 1 — the real run

Still on Device A:

```
python3 -u udid_share.py --airdrop
```

It prints the whole profile (one `Profile Service` payload, asking only for UDID, PRODUCT, VERSION),
writes `BariSign-UDID.mobileconfig`, tries the share sheet, and prints the absolute path.

Then send it to Device B the reliable way: **Files → On My iPhone/iPad → a-Shell → barisign →
long-press `BariSign-UDID.mobileconfig` → Share → AirDrop**. Keep a-Shell in the foreground; it waits **5 minutes**.

Both devices must be on the same Wi-Fi. On iOS 26.2 and later, if Device B is not in your contacts,
AirDrop asks for an **AirDrop code** — share it and enter it on Device A.

---

## Step 2 — Device B installs it (~6 taps + passcode)

| # | Tap |
|---|---|
| 1 | Accept the AirDrop |
| 2 | If the install sheet did not open: Files → the file |
| 3 | **Install** (top right) |
| 4 | **Install** again on the confirmation sheet |
| 5 | Your passcode |
| 6 | **Allow** — to send the device information |

Step 6 is the one that matters: iOS is asking permission to send UDID / model / version.

---

## Step 3 — Device A confirms and prints the UDID

```
    model (PRODUCT): iPad15,7
    iOS  (VERSION) : 27.0
    UDID           : hidden until you confirm

Is that the device in front of you? [y/N]
```

Type `y` if that is Device B → the UDID is printed once. Type `n` if not → the UDID is discarded
(the guard against another device on the Wi-Fi posting instead).

---

## What the outcomes mean

| What you see | Meaning |
|---|---|
| `Server closed after N s` → model + iOS → `y` → **UDID printed** | ✅ **PASS** — iOS 27 still returns the UDID, and the no-app route works |
| `no UDID arrived within 300 seconds`, but on Device B the profile **was** installed and you tapped **Allow** | ❌ the route is dead on iOS 27 → we switch to the Settings-based pairing plan |
| Device B never even shows the **Allow** prompt | same as above; report exactly what it showed |
| a-Shell closed the wait too early | Device A locked, or a-Shell went to the background |
| `the UDID was discarded` | you answered `n` — the confirmation gate working as intended |

---

## Cleanup on Device B

Settings → General → **VPN & Device Management** → tap the **BariSign** entry → **Remove Profile** →
passcode. Delete every *BariSign* entry, including the test profiles.

Bonus measurement: **wait ~11 minutes** after installing and look again. The real profile no longer
carries `DurationUntilRemoval` by default, so unless you ran with `--lifetime` it will still be there —
that is expected, not a bug.

---

## What to send back

1. For each of the three test profiles: **installed or failed** (and the error text if it failed).
2. The **whole a-Shell screen** from the real run.
3. Whether the **Allow** prompt appeared on Device B, and whether the profile showed up under
   **VPN & Device Management**.

No Apple ID, password or pairing record is involved anywhere in this test.
