# Third-party notices

BariSign reuses open-source code. This file records what, where, and under which licence.

## Ported source code

### SideInstaller — `poc/poc_login.py`, `third_party/SideInstaller-LICENSE.md`

- Upstream: https://github.com/FrizzleM/SideInstaller — file `ish/sideinstaller.py`
- Copyright (c) 2026 FrizzleM
- Licence: custom. **Source-only redistribution, non-commercial, attribution required, changes
  must be clearly indicated.** Full text: `third_party/SideInstaller-LICENSE.md`.
- Used for: the Apple sign-in stack (anisette v3 + provisioning, SRP-6a, GrandSlam,
  `AppleAccount`, `DeveloperSession`).
- Changes made by BariSign are listed in the header of `poc/poc_login.py`.

**Consequence:** these files cannot be relicensed MIT, and this repository as a whole **cannot be
used commercially**. BariSign's own files may be MIT; ported files keep the terms above.

## Referenced projects (not copied)

Used for protocol research, verification, or as runtime dependencies. Their releases are consumed
as-is; no code is copied here.

| Project | Licence | Use |
|---|---|---|
| SideStore (`SideStore/SideStore`) | AGPL-3.0 | released IPAs for variants 1–2 |
| LiveContainer (`LiveContainer/LiveContainer`) | AGPL-3.0 | released IPA for variants 3–4 |
| isideload (`nab138/isideload`) | MIT | protocol reference |
| idevice (`jkcoxson/idevice`) | MIT | protocol reference |
| zsign (`zhlynn/zsign`) | MIT | reference for the Apple CMS layout |
| pymobiledevice3 (`doronz88/pymobiledevice3`) | GPL-3.0 | protocol reference only |
| a-Shell (`holzschu/a-Shell`) | BSD-3-Clause | the on-device runtime (user installs it) |
| LocalDevVPN / StosVPN | see upstream | runtime dependency (user installs it) |
| anisette servers | see upstream | network service at runtime |
