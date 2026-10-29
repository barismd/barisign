# BariSign

Runs in **a-Shell** on the device that gets the app. No computer, no second device.

**Status:** UDID and Apple sign-in work. The signer and the downloader are written and were checked on
Linux against real IPAs, but no install from this flow has happened on a device yet.

---

## Install

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

## UDID

```
sh bin/barisign udid
```

1. Safari opens `median.co/udid`
2. install the profile, then copy the UDID
3. come back and press Return

`--no-open` skips Safari. Other ways to get a UDID: `docs/UDID_OPTIONS.md`.

---

## LOGIN

```
sh bin/barisign login
```

It first proves an anisette server works and Apple answers, then asks for the password (typed as `*`).
`--use-clipboard` takes the password from the clipboard with no typing. `--server URL` pins an anisette
server. A free Apple ID is enough.

---

## PORTAL

```
sh bin/barisign portal
```

Registers the UDID, issues a certificate, downloads a provisioning profile. Untested against Apple.

---

## SIGN and INSTALL

The guided run does this for you: it resolves the chosen variant's current `.ipa` from the project's own
releases, checks the SHA-256 GitHub publishes, signs it with the key, certificate and profile from the
portal, verifies the result, and writes `<name>-signed.zip`.

```
sh bin/barisign sign              # download (unless --ipa), sign, zip
sh bin/barisign sign --ipa X.ipa  # sign a file you already have
```

After the last line, press **Return**: a-Shell opens the zip's preview, which has the share button, so
the AirDrop is one tap. Send the `.zip` to any second device, unzip it there, and AirDrop the `.ipa` back —
iOS installs an AirDropped IPA by itself.

The app installs **only on the device whose UDID is in the profile** (the phone you ran this on): a
development-signed app is refused elsewhere, with a different Apple ID or not. The second device is only
a courier. To use the app on another device, register that UDID first (`sh bin/barisign portal --udid UDID`)
and sign again.

Untested on a device: this is the next thing to try.

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
| terminal stops taking input | quit a-Shell from the app switcher and reopen it |
