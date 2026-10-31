# Third-party notices

BariSign reuses open-source code. This file records what, where, and under which licence.

## Code copied into this repository

### SideInstaller by FrizzleM — `third_party/sideinstaller/`

- Upstream: https://github.com/FrizzleM/SideInstaller — file `ish/sideinstaller.py`
- Copyright (c) 2026 FrizzleM
- **Licence: custom, NOT MIT.** Full text: `third_party/sideinstaller/LICENSE.md`.
- Required credit (§4): *SideInstaller by FrizzleM*, linked above.
- Files: `third_party/sideinstaller/signin.py` (the ported sign-in stack),
  `third_party/sideinstaller/portal.py` (the ported developer portal) and
  `third_party/sideinstaller/README.md` (what was taken and what we changed).

**Consequence for this repository:** it contains non-commercial-licensed parts, so **the repository
as a whole may not be used commercially**, and those files cannot be relicensed MIT.

### Key conditions we are relying on (quoted from that licence)

> **§2 Grant of Rights** … permission is hereby granted … to: Use, run, and study the Software for
> personal, educational, and non-commercial purposes; Copy and modify the Software; Incorporate the
> Software, in whole or in part, into their own projects; Distribute the Software or derivative
> works **in source code form**, provided this license and the copyright notice above are included
> and the changes made are clearly indicated.

> **§3 Restrictions** … prohibited without prior written authorization … **1. Commercial Use /
> Monetization.** Any Commercial Use of the Software, of derivative works, or of Official Releases.
> **2. Redistribution of Official Releases.** Rehosting, mirroring, repackaging, re-signing, or
> otherwise redistributing Official Releases (including IPA files) …

> **§4 Attribution** Projects that incorporate the Software must credit "SideInstaller by FrizzleM"
> and link to the official repository in their documentation or about section.

How BariSign complies: the code sits only in `third_party/sideinstaller/`, the licence text and the
copyright notice travel with it, the changes we made are listed both in that folder's README and in
the file header, we credit "SideInstaller by FrizzleM" with a link, and **we redistribute no
SideInstaller binaries or IPAs** (we don't host any IPAs at all).

## Certificates

### Mozilla CA bundle — `third_party/ca-bundle/cacert.pem`

- Source: `https://curl.se/ca/cacert.pem` (curl project's build of Mozilla's CA list)
- Licence: public domain (stated in the file header)
- SHA-256: `a41b5d356aea97a529fe27e0f7316d2f9d946d75927476cf9cf1b90637d00505`
- Used for: all hosts, because a-Shell's Python ships an empty trust store.

### Apple Root CA — `third_party/apple-roots/`

- Source: `https://www.apple.com/appleca/AppleIncRootCertificate.cer` (Apple PKI page)
- SHA-256: `B0:B1:73:0E:CB:C7:FF:45:05:14:2C:49:F1:29:5E:6E:DA:6B:CA:ED:7E:2C:68:C5:BE:91:B5:A1:10:01:F0:24`
- Apple publishes this fingerprint at <https://support.apple.com/en-us/121672>; it was verified to
  match. Public trust anchor, redistributable. Used only for `*.apple.com` hosts.

## Referenced projects (not copied)

Used for protocol research, verification, or as runtime dependencies. Their releases are consumed
as-is; no code is copied from them here.

| Project | Licence | Use |
|---|---|---|
| SideStore (`SideStore/SideStore`) | AGPL-3.0 | released IPAs for variants 1–2 |
| LiveContainer (`LiveContainer/LiveContainer`) | AGPL-3.0 | released IPA for variants 3–4 |
| isideload (`nab138/isideload`) | MIT | protocol reference; **preferred source** for reimplementation |
| idevice (`jkcoxson/idevice`) | MIT | protocol reference; **preferred source** for reimplementation |
| zsign (`zhlynn/zsign`) | MIT | reference for the Apple CMS layout |
| pymobiledevice3 (`doronz88/pymobiledevice3`) | GPL-3.0 | runtime dependency for `bin/barisign pair`; installed **separately** on the device into `state/pymd3` and imported there. No code is copied into this repository and it is not redistributed by it. |
| a-Shell (`holzschu/a-Shell`) | BSD-3-Clause | the on-device runtime (user installs it) |
| LocalDevVPN / StosVPN | see upstream | runtime dependency (user installs it) |
| anisette servers | see upstream | network service at runtime |

## Licence policy for future work

Where an **MIT** project (`isideload`, `idevice`) provides the same logic as the copied SideInstaller
code, reimplement from the MIT source and delete the copied version, so
`third_party/sideinstaller/` shrinks over time.
