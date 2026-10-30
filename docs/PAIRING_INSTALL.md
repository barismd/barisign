# Pairing + `installation_proxy` on iOS 27 — what is possible

Status: **research, verified against source and device logs on 2026-10-30/31.** No code in
this document is written yet.

The question: can BariSign install its signed IPA on the iPhone with **no computer**, using
pairing plus `installation_proxy`, and generate pairing files that AltStore and SideStore can
use?

Short answer: **yes, but not from a-Shell alone.** The install engine has to be a native app.
SideInstaller already proves the whole pipeline on iOS 27; the clean path for BariSign is a
native helper built from the MIT `jkcoxson/idevice` stack. Pure Python on the iPhone cannot run
the RPPairing tunnel.

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

A native helper is required. The options, easiest first:

| Option | No PC? | Own Apple ID? | Work | Notes |
|---|---|---|---|---|
| **A. SideInstaller as bootstrap** | yes (app from its official page) | yes for installed apps; SideInstaller itself is a shared-cert build | none | Proven on iOS 27. LocalDevVPN. Then SideStore/AltStore run; SideInstaller can install a custom IPA. |
| **B. Fork SideInstaller into a BariSign installer** | yes, if distributed the same way | yes | medium | Non-commercial code, isolated from the AGPL forks. Bootstrap of the fork has the same problem as A. |
| **C. Clean native helper from MIT `idevice`/`isideload`** | yes, once installed | yes | large | AGPL-compatible. RPPairing host + tunnel + installation_proxy + AFC pairing-file writer. Still needs a bootstrap (PC once, or a shared-cert web install). |
| **D. Pure a-Shell/Python** | no | yes | — | Bonjour host is possible; the RPPairing tunnel needs a userspace TCP stack and the host is suspended by Settings. Not a viable installer. |
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

1. **Unblock the user now:** install SideInstaller from its official page on the iPhone (or on
   the iPad and use Side by Side), connect LocalDevVPN, sign in, install SideStore. This proves
   the no-PC pipeline on the actual device. Keep BariSign for signing and refreshes.
2. **Decide the product direction:**
   - A/B: depend on SideInstaller (fast, proven, non-commercial bootstrap).
   - C: build a BariSign native helper from MIT `idevice`/`isideload` (own, larger).
3. **Pairing-file writer:** once a native tunnel exists, add the RPPairing record writer for
   AltStore/SideStore. Without the native tunnel, only the import path (AirDrop the file and let
   the app import it) is realistic.
4. **Do not** spend more time on AirDrop/OTA for free profiles.

## 7. Unverified / risks

- SideInstaller's iOS 27 no-PC claim is the author's and is consistent with the source, but this
  project has not independently run it on the iPhone yet.
- A pure-Python RPPairing **host** might work on the iPad, where a-Shell stays in the foreground;
  the **tunnel/installation_proxy** side still needs the native TCP stack, so it does not remove
  the native helper.
- AltStore 2.3's Remote AltServer is a separate feature; it is not the same as on-device
  RPPairing and still points at a server.
- Pairing-file compatibility across AltStore/SideStore versions changes quickly; test each
  release.
