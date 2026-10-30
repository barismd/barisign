# Pairing + `installation_proxy` on iOS 27 — what is possible

Status: **research, verified against source and device logs on 2026-10-30/31; the pure-Python
path in section 0 is the current candidate and is not yet tested on a device.** No code in this
document is written yet.

The question: can BariSign install its signed IPA on the iPhone with **no computer**, using
pairing plus `installation_proxy`, and generate pairing files that AltStore and SideStore can
use?

Short answer: **the only all-constraints path worth testing is a second iOS device (the iPad) running
a-Shell with `pymobiledevice3`.** Its iOS 27 `remote pair-host` accepts device-initiated pairing and
its **pure-Python userspace tunnel** needs no root, no TUN, no native app and no certificate. The
iPad stays in the foreground while the iPhone pairs from Settings, so a-Shell is not suspended.
The blockers to test are a-Shell's old `cryptography` (3.4.8), the extra pure-Python dependencies,
and raw mDNS vs. the Bonjour API. A native app (SideInstaller or a helper) is the fallback, not the
first choice.

## 0. The pure-Python route (test this first)

`pymobiledevice3` (GPL-3.0, separate tool — not vendored, no app, no certificate) implements exactly
the missing pieces:

- `pymobiledevice3 remote pair-host` — advertises `_remotepairing-pairable-host._tcp`, accepts the
  iOS 27 **device-initiated** pairing and prints the 6-digit code. On the iPhone:
  **Settings → Developer → Paired Macs** (Developer Mode on), pick the host, enter the code.
- The remote pairing record it writes is reused by `remote start-tunnel`.
- The tunnel has a **userspace backend** (`pmd-pytcp`, pure Python asyncio; no `utun`, no root) on
  top of either QUIC (`qh3`, pure Python) or TCP.
- `pymobiledevice3 install <ipa>` then installs through `installation_proxy` over that tunnel — the
  developer source, so the free-profile restriction that blocks AirDrop does not apply.

So: iPad runs a-Shell (foreground, advertisement + tunnel + install), iPhone only opens Settings and
pairs. The signed IPA goes iPhone → iPad by AirDrop **as a file** (file transfer is allowed); the
iPad installs it. No PC, no SideInstaller, no enterprise certificate.

Gates, in order:

1. **Bonjour from a-Shell on the iPad.** Run `python3 -u poc/pair_probe.py --seconds 600`, then on the
   iPhone open Settings → Developer → Paired Macs. If the host appears and the probe prints an
   `RPPairing` connection, the advertisement and Local Network permission are fine.
2. **`pymobiledevice3` imports on a-Shell.** The package metadata asks for `cryptography>=43` and
   many dependencies; a-Shell ships 3.4.8. Install with `--no-deps` and add only the pure-Python
   dependencies, then import `pymobiledevice3.remote.userspace_tunnel`. If the used APIs are older
   than the metadata floor, it works; if not, a small compatibility patch/vendored subset is needed.
   `pymobiledevice3`'s `MDNSResponder` uses raw multicast; if iOS refuses it, replace it with the
   `DNSServiceRegister` call the probe already uses.
3. **Pair, tunnel, install.** `remote pair-host` → record; `remote start-tunnel` → RSD;
   `pymobiledevice3 install`. The tunnel is pure Python and the iPad is foreground, so neither
   suspension nor a TUN device is involved.

If gate 2 is clean, this is the project's answer and no native helper is needed. If gate 2 fails on
3.4.8, the choice is a compatibility port or a native helper.

## 1. Why AirDrop and OTA are out

Device log from the iPhone (iOS 27, 0.6.10 IPA, AirDrop):

```
MIInstallerErrorDomain Code=111
"The bundle ... is authorized by a free provisioning profile, but apps validated by
those are not allowed to be installed from this source."
LegacyErrorString=ApplicationVerificationFailed
```

`MobileInstallation` sets `allowingFreeProfileValidation=false` for the AirDrop source. The
same IPA installs through `ideviceinstaller` (the developer/installation_proxy source), which
sets it true. OTA (`itms-services`) is enterprise/ad-hoc territory; a free profile is not
accepted from it either. So:

- AirDrop: **dead** for free-profile apps.
- OTA: **dead** without a paid/enterprise certificate.
- USB `installation_proxy` (ideviceinstaller / Xcode / Configurator): **works** (verified).
- `installation_proxy` over a **pairing tunnel**: the only remaining no-computer route.

## 2. How the on-device route works

The pieces, in order:

1. **RPPairing host.** An app on one device advertises
   `_remotepairing-pairable-host._tcp` over Bonjour and runs the pairing responder. The other
   device (or the same device, via Settings) sees it under
   *Settings → Privacy & Security → Developer Mode → Pair with …*, pairs, and the host gets an
   **RPPairing record** (`public_key`, `private_key` 32-byte Ed25519, `identifier`, `alt_irk`).
   The host shows a **PIN**; the pairing client types it.
2. **LocalDevVPN.** On the device that will install to itself, the RPPairing service is
   reachable at the VPN peer address (default `10.7.0.1:49152`). LocalDevVPN (App Store) creates
   that local tunnel; SideStore's docs make it mandatory for install/refresh, and SideInstaller
   uses the same address.
3. **Tunnel + RSD.** The pairing record opens a `tunnel_create_rppairing` connection; inside it
   runs RemoteServiceDiscovery/RemoteXPC, and then `installation_proxy` (and
   `installcoordination_proxy` on newer iOS).
4. **AFC / house_arrest.** The same tunnel copies files into an app container, e.g. the signing
   IPA into `/PublicStaging`, or a pairing file into SideStore/AltStore's Documents.

The host must stay alive while the pairing client is in Settings. SideInstaller does this with a
Live Activity (the PIN in the Dynamic Island/Lock Screen) plus a keep-alive helper. A-Shell has
none of that: it is suspended when Settings comes to the front.

## 3. What is already proven

### SideInstaller (open source, non-commercial)

- README: *"Install SideStore and LiveContainer directly on your iPhone. No PC required."*
  iOS 27+ works on-device; iOS 26.4–26.x needs a remote pairing file made on a PC.
- Requires **LocalDevVPN** and Wi-Fi.
- The app runs the RPPairing host itself, pairs this iPhone through Settings, opens the tunnel,
  signs with the user's own Apple ID and installs.
- **Side by Side** installs SideInstaller onto *another* iPhone on the same Wi-Fi: the target
  pairs from its Settings, the host advertises the pairing service; no VPN needed for that
  direct Wi-Fi path. That is a no-PC bootstrap for the second device.
- Its Rust core uses `jkcoxson/idevice` with `remote_pairing`, `tunnel_tcp_stack`, `rsd`,
  `installation_proxy`, `installcoordination_proxy`, `afc`, `house_arrest`, `misagent`.
- License: custom **non-commercial, source-only**. It may not be mixed into the AGPL forks.

### AltStore Classic 2.3

Source-verified in `AltStore/Managing Apps/OnDeviceClient.swift`:

- The pairing file must contain an **RPPairing** record: `record["private_key"] != nil`.
  Lockdown-style records are rejected.
- It opens `tunnel_create_rppairing` at port **49152** with the default PIN `000000`.
- It installs through `/PublicStaging/<bundle>.ipa` and `installation_proxy`.
- The file lives at `Documents/ALTPairingFile.plist` on the device (AltServer normally embeds
  it during install).

So once AltStore has an RPPairing file, it can install/refresh by itself. The missing piece is
getting that file onto the device without a PC.

### SideStore

- `AppConstants.swift`: supported names include `ALTPairingFile.mobiledevicepairing`
  (legacy), `PairingFile_Lockdown.plist`, `PairingFile_RemoteRP.plist`.
- Issue #1698 and the docs: from **iOS 26.4** a lockdown-style pairing file is rejected through
  LocalDevVPN; a **remote (RPPairing)** file is required. The settings page can import one and
  set it as preferred.
- Docs: initial install still needs a computer (iloader); the on-device VPN is required for
  every install/update/refresh afterwards.

### Licensing of the building blocks

- `jkcoxson/idevice` — **MIT**. Includes the RPPairing host, `tunnel_tcp_stack`, RSD,
  `installation_proxy`, `installcoordination_proxy`, AFC/house_arrest, pairing-file helpers.
- `nab138/isideload` — MIT (auth, portal, signing).
- `pymobiledevice3` — GPL-3.0; useful as a protocol reference, but not a drop-in iOS tunnel.
- SideInstaller — non-commercial; **cannot** go into the AGPL forks. Its architecture is the
  reference, not the code.

## 4. The hard boundary

The pure-Python route may remove the need for a native helper; if it fails, the fallback is one. The
options, easiest first:

| Option | No PC? | Own Apple ID? | Work | Notes |
|---|---|---|---|---|
| **A. SideInstaller as bootstrap** | yes (app from its official page) | yes for installed apps; SideInstaller itself is a shared-cert build | none | Proven on iOS 27. LocalDevVPN. Then SideStore/AltStore run; SideInstaller can install a custom IPA. |
| **B. Fork SideInstaller into a BariSign installer** | yes, if distributed the same way | yes | medium | Non-commercial code, isolated from the AGPL forks. Bootstrap of the fork has the same problem as A. |
| **C. Clean native helper from MIT `idevice`/`isideload`** | yes, once installed | yes | large | AGPL-compatible. RPPairing host + tunnel + installation_proxy + AFC pairing-file writer. Still needs a bootstrap (PC once, or a shared-cert web install). |
| **D. a-Shell/Python on the iPad, `pymobiledevice3`** | **maybe** | yes | test first | `remote pair-host` + pure-Python userspace tunnel; no native app, no cert. Open risks: cryptography 3.4.8, deps, raw mDNS. This is the only all-constraints path; test gates above. |
| **E. USB `ideviceinstaller`** | no | yes | none | Works today (0.6.12 verified). |

There is **no** pure own-cert + no-PC + no-third-party-app path on iOS 27: a free-profile app
cannot be AirDropped or OTA-installed, and a free-signed helper can only be installed through a
developer source. A bootstrap app must therefore come from somewhere: an App Store app that can
do the native work (none today), a shared-cert build (SideInstaller), or a one-time PC install.

## 5. Pairing files BariSign would write

The host gets an RPPairing record. For compatibility:

| App | Path | Needs |
|---|---|---|
| AltStore Classic 2.3 | `Documents/ALTPairingFile.plist` | RPPairing (`private_key`), enough for `OnDeviceClient` |
| SideStore | `Documents/ALTPairingFile.mobiledevicepairing` | remote record on iOS 26.4+; lockdown half for older |
| LiveContainer + SideStore | `SideStore/Documents/ALTPairingFile.mobiledevicepairing` | same |
| Feather/StikDebug etc. | see SideInstaller's `PairingTargets` table | mixed |

A merged plist containing both the RPPairing keys and the lockdown keys is the safest for old and
new apps; AltStore ignores the lockdown half, SideStore uses the remote half on 26.4+. The file
is written over AFC/house_arrest, exactly as AltServer and SideInstaller do.

## 6. Recommended order

1. **Test the pure-Python route on the iPad** (section 0). `pair_probe.py` is in the repo and needs no
   new dependency. If the iPhone's Paired Macs screen shows the host, the Bonjour half is proven.
2. **Test `pymobiledevice3` on a-Shell** with `--no-deps` and the pure dependencies. If it imports
   and `remote pair-host` runs, pair the iPhone and try `remote start-tunnel` + `install`.
3. If that works: BariSign keeps signing on the iPhone, the iPad installs, and the same tool writes
   the RPPairing record into AltStore/SideStore over AFC (section 5). No PC, no SideInstaller, no
   certificate.
4. If a-Shell's 3.4.8 blocks it: either port the needed subset or fall back to a native helper.
5. **Do not** spend more time on AirDrop/OTA for free profiles.

## 7. Unverified / risks

- SideInstaller's iOS 27 no-PC claim is the author's and is consistent with the source, but this
  project has not independently run it on the iPhone yet.
- The pure-Python tunnel is the open question: `pymobiledevice3` advertises a userspace backend, but
  it has not been run under a-Shell's `cryptography` 3.4.8 and Python build. If it imports and pairs,
  the native helper is unnecessary; if not, the fallback is a compatibility port or a native helper.
- AltStore 2.3's Remote AltServer is a separate feature; it is not the same as on-device
  RPPairing and still points at a server.
- Pairing-file compatibility across AltStore/SideStore versions changes quickly; test each
  release.
