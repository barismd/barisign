# BariSign

Runs in **a-Shell** on the device that gets the app. No computer, no second device.

**Status:** UDID and Apple sign-in work. The signer — the part that makes an IPA installable — is not
built yet, so nothing installs today.

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

Not built. When it is: the signed app is written as a `.zip`, AirDropped to any second device, unzipped
there, and AirDropped back — iOS installs an AirDropped IPA by itself.

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
