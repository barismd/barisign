# BariSign

Two iOS/iPadOS devices on the same Wi-Fi are enough. Two roles, no computer:

- **Signer** — runs **a-Shell** and BariSign; signs and installs.
- **Target** — gets the app; uses only Safari and Settings.

Either device can be an **iPhone or an iPad** (iOS/iPadOS 27+). They must be on the **same Wi-Fi**.
a-Shell and BariSign are installed on the **signer only**.

**Status:** Apple sign-in, the developer portal, signing and launching the signed app are verified on
a real device. The pair-first **UDID from pairing**, the fallback **UDID page** and the **signer →
target pairing install** are written but not yet verified on a device.

---

## Install (on the signer)

```
mkdir -p ~/Documents/barisign && cd ~/Documents/barisign
curl -sSL "https://github.com/barismd/barisign/blob/main/install.sh?raw=true" -o install.sh
sh install.sh
```

Then one command drives everything:

```
sh bin/barisign            # guided
sh bin/barisign status     # what is saved
```

---

## UDID (pairing first)

The normal flow takes the target's UDID **from the pairing**, so no median profile is needed:

```
sh bin/barisign udid --pair
```

1. The signer advertises as **BariSign** and prints a code.
2. On the **target**: **Settings → Privacy & Security → Developer Mode → Paired Macs** (or
   **Settings → Developer → Paired Macs**) → **BariSign** → code.
3. The signer reads the UDID from the pairing/tunnel and saves it.

If the target has never had Developer Mode, download the original IPA on the signer and share it to
the target first: install it once; the Developer Mode switch appears after the failed launch attempt.
That IPA is only the trigger; the app BariSign signs is a different, signed build.

If pairing cannot provide a UDID, fall back to the page:

```
sh bin/barisign udid
```

The signer prints `http://<signer-ip>:<port>/`; on the target open it in Safari, install the
`median.co/udid` profile, copy the UDID, paste it into the box and submit. `--manual` keeps a
paste-on-the-signer fallback. Other UDID sources: `docs/UDID_OPTIONS.md`.

---

## LOGIN (signer)

```
sh bin/barisign login
```

It first proves an anisette server works and Apple answers, then asks for the password (typed as `*`).
`--use-clipboard` takes the password from the clipboard with no typing. `--server URL` pins an anisette
server. A free Apple ID is enough.

---

## PORTAL (signer)

```
sh bin/barisign portal
```

Registers the target's UDID, issues a certificate and downloads a provisioning profile. Verified on a
device: team → device → App ID → certificate → profile, with the target's UDID in the profile.

---

## SIGN (signer)

The guided run does this for you: it resolves the chosen variant's current `.ipa` from the project's own
releases, checks the SHA-256 GitHub publishes, signs it with the key, certificate and profile from the
portal, verifies the result, and writes `<name>-signed.ipa` (plus a `.zip`).

```
sh bin/barisign sign              # download (unless --ipa), sign, zip
sh bin/barisign sign --ipa X.ipa  # sign a file you already have
```

---

## INSTALL on the target (signer + target)

```
sh bin/barisign pair
```

1. The signer advertises as **BariSign** and prints a 6-digit code.
2. On the **target**, open **Settings → Privacy & Security → Developer Mode → Paired Macs**
   (or **Settings → Developer → Paired Macs**), pick **BariSign**, and enter the code.
3. The signer installs the newest `*-signed.ipa` over Wi-Fi with `installation_proxy`.
4. The signer also writes the pairing file into the installed app:
   AltStore `Documents/ALTPairingFile.plist`, SideStore
   `Documents/ALTPairingFile.mobiledevicepairing`. A copy stays under `state/` for manual import.
5. On the target, tap **Trust** for the developer and open the app.

The first `sh bin/barisign pair` installs its networking engine once; later runs are local.

The app installs **only on the target whose UDID is in the profile**: a development-signed app is
refused on any other device or Apple ID. To use another target, register its UDID and sign again.

---

## Free Apple ID limits

- 3 apps installed at a time (SideStore counts as one)
- 10 App IDs
- 7-day signing

---

## Problems

| What you see | What it means |
|---|---|
| `check  fail` | `sh bin/barisign check` prints what broke |
| login `-22406` | Apple says the password is wrong. The Apple ID is case-sensitive here; watch the keyboard's autocorrect. BariSign retries twice and shows the ID it sent |
| login `-80045` / `-80046` / `503` | Apple refused the identity, not the password. It moves to the next anisette server; if all fail, wait 10–15 minutes without retrying |
| UDID not recognised | `40` hex characters, or `24` like `00008110-001a2c3e0e91801e` |
| target cannot open the page | same Wi-Fi? Allow **Local Network** for a-Shell; the address has the signer's Wi-Fi IP |
| `bin/barisign pair` finds no IPA | sign on the signer first, or pass `--ipa FILE` |
| BariSign does not appear in Paired Macs | both devices on the same Wi-Fi; press `sh bin/barisign pair` again; allow Local Network for a-Shell |
| terminal stops taking input | quit a-Shell from the app switcher and reopen it |
