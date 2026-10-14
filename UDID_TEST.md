# UDID profile test — v2 (after "profile could not be installed")

Your Device B answered: **"Profil yüklenemedi, bilinmeyen bir hata oluştu"** — the profile was refused.
So we first find **which layer** iOS is refusing, with three throwaway profiles, and only then run the
real thing.

**Good news on the other question:** a-Shell has an `open` command that presents the **iOS share sheet**
for a file, so you no longer leave a-Shell and go through Files. `open barsign-test1-empty.mobileconfig`
→ share sheet → AirDrop. (Verified: `open` exists in a-Shell's command set and is documented as the
"share a file with other apps" command.)

---

## Step 0 — find out what iOS is refusing (2 minutes)

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

That writes three files and stops. Share and install them on Device B **in this order**, and tell me
which ones fail:

| Share it with | What it is | If it fails |
|---|---|---|
| `open barsign-test1-empty.mobileconfig` | a profile that installs **nothing** | unsigned profiles cannot be installed on Device B at all (or AirDrop delivery is the problem) → we try sending it another way |
| `open barsign-test2-profservice.mobileconfig` | `Profile Service`, **only** the keys Apple documents | **iOS 27 no longer accepts the `Profile Service` payload** → this whole no-app route is dead, and we fall back to the Settings pairing route |
| `open barsign-test3-profservice-lifetime.mobileconfig` | the same **plus** `DurationUntilRemoval` + `PayloadExpirationDate` | those two extra keys are what iOS refuses — which is very likely, since the first version of the profile carried them |

The files contain **no usable callback address** (they point at `192.0.2.1`, which is unroutable). They
exist only to test installation. Install each one, note the result, then **remove it** (Settings →
General → VPN & Device Management → Remove Profile).

**Likely outcome and what I already changed:** the two lifetime keys are now **OFF by default**. An
initial profile that iOS rejects is worse than a leftover one; pass `--lifetime` if you want them back.
If test 3 is the only failure, the real run below should now work.

---

## Step 1 — the real run

Still on Device A:

```
python3 -u udid_share.py --airdrop
```

It prints the whole profile (one `Profile Service` payload, asking only for UDID, PRODUCT, VERSION),
writes `BariSign-UDID.mobileconfig`, **and opens the share sheet by itself**. If the sheet does not
appear, type:

```
open BariSign-UDID.mobileconfig
```

Then AirDrop to Device B. Keep a-Shell in the foreground; it waits **5 minutes**.

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
