# AGENTS.md: BariStore

## Goal

An app store for iPhone/iPad that runs in **Open Minis** (1.16+), one device,
no computer. One command: `baristore` opens the store page (minis-open sheet).
iOS/iPadOS 27+ only. Free Apple ID accounts only.

## Decisions (owner)

- Everything in **Rust**, one static aarch64-musl binary. Our own code: read
  behaviour and protocols from the reference projects, write original code; MIT
  sources (idevice, isideload) may be read as code; AGPL/GPL/non-commercial
  projects only for behaviour and docs. Record sources in THIRD_PARTY_NOTICES.md.
- Platform is Open Minis, not a-Shell: a-Shell cannot publish the
  `_remotepairing-pairable-host._tcp` Bonjour service (device-proven: no
  NSBonjourServices entry, no multicast entitlement, raw announcements never
  showed an entry on iOS 27.2.1), Minis does (`minis-bonjour advertise`).
- Store spec: Featured (search, add source, AltSources, BariStore picks:
  AltStore, SideStore, LiveContainer+SideStore, LiveContainer, stable + nightly),
  My Apps (installed, refresh, delete, per-app options), Options (Apple ID,
  pairing export, info, theme, reset, install defaults: Increased Memory Limit,
  remove extensions). JIT: wanted if possible (owner, 2026-11-21); needs a
  mounted personalized DDI (TSS) + debugserver, as StikDebug does; iOS 26+ JIT
  works only in apps built for it. Not built yet.
- Versions: experimental builds go to `main` as 0.10.0-alpha.N (CI publishes
  `v<version>` and the rolling `nightly` that install.sh downloads). Once stable
  and complete, history is squashed and released as 1.0.0.
- Never sign the user out on our own. Never store the password.
- Code, comments, commits, README in English. The owner talks Turkish.
- Device tests come in **batches** the owner runs; keep their effort low.

## Facts we rely on (and where they come from)

- Minis keeps running in Settings only while `apple-player` plays (siboot;
  same mechanism proven for a-Shell's `play` on device). Its audio sheet can be
  swiped down: playback goes on in a floating bubble (Open Minis
  `MinisAudioPreviewView.onDisappear`). No player-free keep-alive exists for
  terminal commands.
- The "Pair with …" entry asks for the passcode, then sends pair-setup M1; the
  code is made and notified at M1 (owner: an earlier notification is gone by then).
- The pairing service is usually on 49152 but can be another dynamic port
  (siboot 0.19.1): knock 49153–65535 except 62078.
- iOS 27.2+: every RemoteXPC handshake on a tunnel must present the same host
  UUID (idevice); we keep one per install (`~/.baristore/host-uuid`).
- SideStore nightlies read `Documents/PairingFile_RemoteRP.plist` only with
  prefs `isPairingReset=false`, `activePairingProtocol=rppairing`; LiveContainer's
  SideStore reads `SideStore/Documents/ALTPairingFile.mobiledevicepairing`;
  AltStore 2.3.1+ reads `Documents/ALTPairingFile.plist` (SideInstaller docs).
- Apple sign-in (SideInstaller `client_info_to_use`, siboot 0.20): no
  Android-library servers (-80046 at the token); a server naming a Mac older
  than 10.13 signs in as `<MacBookPro18,3> <macOS;26.6;25F84>`; Windows-library
  servers need the hsc 434 resync. One anisette identity per (server, client
  info). Credential codes -20101/-20970/-22406 stop sign-in.
- SideStore/AltStore refresh with the certificate in their bundle:
  `ALTCertificate.p12` (password = portal machineId) + `ALTCertificateID` =
  serial (SideStore CertificateProvisioningFlow; isideload). LC+SS: in the
  `com.SideStore.SideStore` framework. AltStore also gets `ALTDeviceID`.
- Each app extension needs its own App ID/profile, identifier under the app's.

## Verified vs not

Linux-verified: SRP against idevice-srp (both roles), pairing host against a
mock iPhone, TLS-PSK against openssl s_server, TCP stack against smoltcp, tunnel
against a mock device, CSR and PKCS#12 with openssl (3.x and 1.1.1), signatures
with rcodesign. **Device-verified (owner, 0.1.0):** LocalDevVPN check, pairing.
Everything else: say "untested on device" until the owner confirms.

## Layout

`src/pairing` pairable host · `src/device` tunnel, TCP, XPC, services ·
`src/apple` anisette, GrandSlam, portal · `src/sign` code signing ·
`src/install.rs` App IDs → profiles → sign → install → pairing file ·
`src/store.rs` catalog · `src/app.rs` state/tasks · `src/web.rs` + `ui/index.html`
the page · `src/doctor.rs` report. CI: `.github/workflows/release.yml` publishes
the `nightly` release that `install.sh` downloads.

## Rules

- No dead code, no unused options, no extra files; `cargo build --release` must
  have zero warnings; `cargo test` must pass.
- Never commit secrets, pairing records, keys, profiles or IPAs.
- Bump `version` in Cargo.toml on every functional change.
