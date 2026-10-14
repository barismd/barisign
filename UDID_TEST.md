# UDID profile test — run this now

Runs on **Device A (the iPad with a-Shell)** and asks **Device B (the iPhone)** for its UDID using a
temporary configuration profile. Device B needs **no app** — only AirDrop, Settings and Safari.

**This test answers one question: does iOS 27 still send the UDID from a "Profile Service" profile?**
Everything else is already known to work.

Both devices must be on **the same Wi-Fi**, and Device A must stay **awake and in the foreground**.

---

## 0. Before you start (Device A)

1. Settings → **Display & Brightness → Auto-Lock → Never** (put it back afterwards).
2. Settings → General → AirDrop → **Contacts Only** — or **Everyone for 10 minutes** if Device B uses a
   different Apple ID.
3. Keep the two devices close (Bluetooth + Wi-Fi on for AirDrop).

---

## 1. Device A — get the script and start it

Type these into a-Shell, one line at a time:

```
cd ~/Documents/barisign
```

```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/poc/udid_share.py -o udid_share.py
```

```
python3 -u udid_share.py --airdrop
```

It prints the whole profile (one `Profile Service` payload, asking only for UDID, PRODUCT and VERSION),
then the path of a file it just wrote, and then it waits **5 minutes**.

Leave a-Shell in the foreground. Do not switch apps on Device A.

---

## 2. Device A → Device B: send the file by AirDrop

1. Open the **Files** app on Device A.
2. Go to **On My iPad → a-Shell → barisign**.
3. Long-press **`BariSign-UDID.mobileconfig`** → **Share** → **AirDrop** → tap Device B.
4. On Device B: **Accept**.

Device B now has the file. Tapping an AirDropped profile usually opens it straight away; if it lands in
**Files → Downloads** instead, tap it there.

---

## 3. Device B — install it (about 6 taps + passcode)

| # | Tap |
|---|---|
| 1 | Accept the AirDrop |
| 2 | If the install sheet does not open by itself: Files → the file |
| 3 | **Install** (top right) |
| 4 | **Install** again on the confirmation sheet |
| 5 | Your passcode |
| 6 | **Allow** — to send the device information |

Step 6 is the one that matters: that prompt is iOS asking permission to send UDID / model / version.

---

## 4. Device A — confirm and read the result

a-Shell prints something like:

```
A device sent its details. Before the UDID is shown, confirm it is
the device in front of you ...

    model (PRODUCT): iPad15,7
    iOS  (VERSION) : 27.0
    UDID           : hidden until you confirm

Is that the device in front of you? [y/N]
```

- If that is **Device B**, type `y` and press Return → the UDID is printed once.
- If it is **not** Device B, type `n` → the UDID is discarded and nothing is kept. (This is the guard
  against some other device on the Wi-Fi posting instead.)

---

## 5. What the outcomes mean

| What you see | What it means |
|---|---|
| `Server closed after N s` → model + iOS version → `y` → **UDID printed** | ✅ **PASS.** iOS 27 still sends the UDID, and the whole no-app route works. |
| `RESULT: no UDID arrived within 300 seconds`, and on Device B you never got past the AirDrop | The profile never reached Settings — retry, or use the QR route (§7) |
| `RESULT: no UDID arrived…`, and on Device B the profile **was** installed (it shows under VPN & Device Management) and you tapped Allow, but nothing arrived | ❌ **The route is dead on iOS 27.** Apple no longer returns the UDID this way, and we switch to the Settings-based pairing route. |
| Device B shows no **Allow** prompt at all | Same as above — worth reporting exactly what it showed |
| No QR/URL problem, but a-Shell closed too early | Device A locked or a-Shell went to the background |
| Device A printed `the UDID was discarded` | You answered `n` — the confirmation gate working as designed |

---

## 6. Clean up (both devices)

**Device B:** Settings → General → **VPN & Device Management** → tap the **BariSign** entry →
**Remove Profile** → passcode. Delete anything else named *BariSign* too.

Also worth doing as a bonus measurement: **wait about 11 minutes after installing** and look again. The
profile carries `DurationUntilRemoval = 600`, which is *supposed* to make iOS delete it by itself — that
is unverified, and this is the cheap way to find out. Tell me whether it was gone or still there.

---

## 7. If AirDrop will not work — the QR route

Same script, no AirDrop:

```
python3 -u udid_share.py
```

It prints a QR and an address. On Device B: **Camera → scan → Allow** the profile download, then follow
step 3 above. Notes: both devices must be on the same Wi-Fi, Device A must stay in the foreground, and
if iOS refuses the `http://` profile download, tell me the exact wording.

---

## 8. What to send back

1. The **whole a-Shell screen** from Device A (from the script starting to the final result).
2. On Device B: whether the profile appeared under **VPN & Device Management**, and whether the
   **Allow** prompt appeared.
3. After ~11 minutes: was the **BariSign** profile still there on Device B?

That is everything. Nothing is written to any file, the UDID is printed once and goes nowhere else, and
no Apple ID, password or pairing record is involved in this test at all.
