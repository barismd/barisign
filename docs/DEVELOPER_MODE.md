# Developer Mode, pairing and LocalDevVPN — the computer-free paths

Checked 2026-10-29 by reading Apple's documentation and the source of the tools that already do this
on-device (SideInstaller, StikPair, Husk, Madeira). Nothing here has been tried on our iPhone yet;
the parts we could not confirm are marked **unverified**.

## Short version

- **LocalDevVPN cannot enable Developer Mode.** It is a loopback VPN: a packet tunnel that gives an
  app on the phone a path to the phone's *own* developer services (device IP `10.7.0.1`). It carries
  install traffic; it does not change an OS security setting.
- iOS 16+ requires **Developer Mode on** to install and run an app signed with a development
  certificate (a free Apple ID is a development certificate). No VPN, profile or app can bypass that.
- The computer-free way to make Developer Mode *available* is **pairing**. iOS 27 lets the phone pair
  with an app on the same phone: the app advertises a pairable host, and in
  **Settings → Privacy & Security → Developer Mode → Pair with <app>** the user enters the phone
  passcode and the six-digit code the app shows. Apple documents that the Developer Mode setting may
  not appear until pairing begins. After pairing, the switch is there; the user turns it on and the
  phone restarts.
- **AMFI can also reveal or turn on Developer Mode** (`DeveloperModeStatus`,
  `amfi_reveal_developer_mode_option_in_ui`, `amfi_enable_developer_mode`), but only over a developer
  link that already works. On iOS 27 that link itself needs Developer Mode, so with the setting off
  there is nothing to send the request over — SideInstaller's own code says so: *"on iOS 27, where the
  link itself needs Developer Mode, they can't bring a missing option back."*
- **The first app on a brand-new phone is the remaining hole.** Our free account can only produce
  development-signed IPAs, and installing one needs Developer Mode. The tools that bootstrap a fresh
  phone (SideInstaller, signing services) deliver their first app with a **non-development**
  certificate (Ad Hoc/enterprise), which a free account cannot create. On a phone that has never
  paired, a pure free + no-computer bootstrap is therefore not available today. **unverified** whether
  iOS 27 shows the Developer Mode switch before the first pairing on every device.

## What each piece does

| Piece | Role | Gate it does **not** remove |
|---|---|---|
| **Developer Mode** | iOS gate for installing/running development-signed apps | — |
| **Pairing** (RPPairing, iOS 27 on-device) | Creates the pairing record the phone's developer services accept; begins the process that makes Developer Mode available | The user still flips the switch and restarts |
| **LocalDevVPN** (App Store, free, jkcoxson) | Loopback packet tunnel so an app reaches the phone's own tunnel/RSD services at `10.7.0.1` | Developer Mode; needs Wi-Fi (its loopback does not work over cellular) |
| **AMFI** (over a working link) | Can reveal the Developer Mode option or turn it on; turning it on refuses while a passcode is set | Cannot run without a link, i.e. not a cold bootstrap on iOS 27 |
| **Ad Hoc / enterprise certificate** | Install without Developer Mode | Needs a paid account (a free Apple ID cannot issue one) |
| **AirDrop** | **Refused for free profiles** on iOS 27 (`MIInstallerErrorDomain` 111); it is not a route any more | Not a way to enable Developer Mode |

## How SideInstaller does it (read from its source)

1. **RPPairing host in the app.** The app advertises
   `_remotepairing-pairable-host._tcp` over Bonjour (`PairingController.swift`), so the *phone* dials
   the *app*. The user goes to Settings → Privacy & Security → Developer Mode → "Pair with
   SideInstaller", enters the passcode, and types the six-digit PIN the app shows (a Live Activity or
   a notification draws it over Settings). The app runs the accessory side of SRP-6a and receives an
   RPPairing record.
2. **LocalDevVPN loopback.** The app connects to `10.7.0.1:49152` through LocalDevVPN, does
   pair-verify with that record, opens the developer tunnel (RSD) and talks to `installation_proxy`
   and AFC — the same services SideStore uses.
3. **Install and refresh** follow SideStore's model.
4. **Developer Mode buttons.** AMFI can show the option or turn it on, but only over a link that
   already works; their own comment concedes it cannot help on iOS 27 with the setting off.
5. **The bootstrap app itself** is installed from sideinstaller.net "using any of the certificates" —
   a signing service, i.e. not a free development certificate. That is how a fresh phone gets the
   first app before Developer Mode exists.

Other projects confirm the same pairing UI: StikPair (iOS 27+, "Pair with StikPair", PIN in a Live
Activity), Husk ("Pair with Husk", code in a notification), Madeira ("Pair with Madeira").

> **Update 2026-10-31:** the AirDrop experiment below is history. AirDrop refuses free-profile IPAs
> (`MIInstallerErrorDomain` 111). The current install route is `sh bin/pair`: the **signer** runs a-Shell
> and the pairing host in the foreground, while the **target** only opens Settings and pairs. See
> `docs/PAIRING_INSTALL.md` and `AGENTS.md`.

## Two experiments before any pairing code

1. **Install first, then look for the switch** (no new code). Apple's WWDC says Developer Mode is
   needed to *install* a development-signed app; several tools report the opposite in practice: the
   app installs, refuses to open, and tapping it makes the Developer Mode setting appear. Our earlier
   test on the other iPhone proved nothing because that phone was not in the profile. Register its
   UDID (`sh bin/barisign portal --udid <that UDID>`), sign again, and AirDrop the **.ipa** (not the
   zip) from the signing phone straight to it. Watch whether the icon appears, and whether tapping it
   reveals Settings → Privacy & Security → Developer Mode. If it does, no pairing is needed at all.
2. **Does Bonjour work from a-Shell?** `poc/pair_probe.py` (untested) registers
   `_remotepairing-pairable-host._tcp` through `DNSServiceRegister` via ctypes, advertises the same TXT
   keys SideInstaller uses, and listens on 49152. Run it on a **second device** with a-Shell (the iPad)
   on the same Wi-Fi; on the target phone open Settings → Privacy & Security → Developer Mode and tap
   "Pair with BariSign". A connection in the probe window means the phone found and dialled us. The
   probe performs no pairing and writes nothing.

If (1) works, stop. If (2) connects, the remaining work is the RPPairing responder (SRP-6a accessory,
TLV/Opaque, Ed25519, pairing record) — a large clean-room port from MIT `idevice`, and the phone still
has to stay in Settings while the host keeps running, which is why a real app (with a Live Activity and
a continued-processing task) is the right host, not a-Shell on the same phone.

**Why not a-Shell on the same phone:** switching to Settings suspends it, the PIN has to be read before
leaving, and there is no Live Activity or background task to keep the listener alive. SideInstaller,
StikPair, Husk and Madeira all use an app extension for exactly this.

## What this means for BariSign

- **Signer and target are roles, not models.** The signer runs a-Shell; the target runs only Safari
  and Settings. Either can be an iPhone or an iPad, and they share the Wi-Fi.
- **The target** needs *its* UDID registered (`bin/barisign udid` serves the page; the target submits it)
  **and** Developer Mode on. The pairing host runs on the **signer**, in a-Shell, while the user is on
  the target — so the "a-Shell is suspended by Settings" problem does not arise.
- **The install** is `sh bin/pair`: the signer advertises `BariSign`, the target pairs from Settings,
  and the signer installs with `installation_proxy` through the pure-Python userspace tunnel.
- **The long-term win** is the fork plan: a pairing module inside SideStore/AltStore/LiveContainer
  (RPPairing host + PIN + pairing record) plus LocalDevVPN and `installation_proxy` would let the apps
  refresh themselves. That belongs in the Swift apps; `bin/pair` is the first, a-Shell-side version of
  the same protocol.
- **Licence note.** SideInstaller and StikPair are non-commercial; LocalDevVPN is an App Store app.
  Read them for behaviour only. The clean-room reimplementation route in `docs/FORK_PLAN.md` stands,
  based on the MIT `jkcoxson/idevice` remote-pairing code.

## Sources

- Apple, *Enabling Developer Mode on a device*: the switch only appears after pairing or a previous
  Mac pairing; turn it on, restart, confirm.
- Apple, *Managing devices in Device Hub* (iOS 27 wireless pairing): the Developer Mode setting may
  not appear until pairing begins.
- `FrizzleM/SideInstaller`: `ios-app/PairingController.swift`, `Engine.swift` (pairing guide and the
  AMFI comment), `DeviceConnection.swift`, `NOTES.md`, `ashell/README.md`.
- `StikDebug/StikPair` and `willfaust/Madeira` release notes, `Leviidev/Husk` JIT guide: the iOS 27
  "Pair with <app>" steps and the PIN display.
- `jkcoxson/LocalDevVPN` (App Store id 6755608044): loopback tunnel for SideStore/StikJIT.
