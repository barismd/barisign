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
| **AirDrop** (our current route) | Normal install path; needs the UDID in the profile and Developer Mode on | Not a way to enable Developer Mode |

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

## What this means for BariSign

- **The phone we sign on (iPhone 13) needs nothing new.** Developer Mode is already on, the UDID is in
  the profile, and AirDrop installs the app. LocalDevVPN plays no part.
- **A second iPhone** needs *its* UDID registered (`portal --udid`) **and** Developer Mode on. If it
  has never paired, the switch may not exist; the computer-free trigger is the iOS 27 pairing above,
  which needs a pairable-host app on that phone — and installing that app needs either a non-free
  certificate or a computer once. A VPN cannot stand in for it.
- **The long-term win** is the fork plan: a pairing module inside SideStore/AltStore/LiveContainer
  (RPPairing host + PIN + pairing record) plus LocalDevVPN and `installation_proxy` would install and
  refresh **on the phone itself**, with no AirDrop and no second device. That belongs in the Swift
  apps, not in a-Shell Python: Bonjour advertising, the Live Activity for the PIN, and the tunnel are
  app-level features. a-Shell's Python cannot advertise the Bonjour service reliably, and the tunnel
  needs TLS-PSK plus a software TCP stack.
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
