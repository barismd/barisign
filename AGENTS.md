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
  remove extensions). JIT (owner, 2026-11-21): built in `src/device/jit.rs`,
  untested on device.
- Versions: experimental builds go to `main` as 0.10.0-alpha.N (CI publishes
  `v<version>` and the rolling `nightly` that install.sh downloads). Once stable
  and complete, history is squashed and released as 1.0.0.
- Never sign the user out on our own. Never store the password.
- No `baristore doctor` command: Options › Log (quick) and Run checks (network,
  tunnel, sign-in probe) in the page; `baristore log` prints the quick log.
- Code, comments, commits, README in English. The owner talks Turkish.
- Device tests come in **batches** the owner runs; keep their effort low.

## Facts we rely on (and where they come from)

- Minis keeps running in Settings while it plays audio. `apple-player` (siboot;
  proven the same way for a-Shell's `play`) covers the screen with a sheet the
  owner does not want; `apple-speak speak --volume 0` uses the same audio
  session as Minis's own keep-alive (playback, mix with others) with no screen,
  so pairing uses it, repeated (each call returns after the speech or 60 s).
  Untested on device; a failed pairing is retried with the player.
- The "Pair with …" entry asks for the passcode, then sends pair-setup M1; the
  code is made and notified at M1 (owner: an earlier notification is gone by then).
- Developer Mode may be missing from Settings on a phone that never had it:
  offering a pairing (the pairable-host advert) makes iOS show it (Apple's
  docs, and the owner's a-Shell build revealed it this way). After pairing,
  AMFI action 0/1 shows or turns it on.
- Tunnel TCP (as jktcp, idevice's device-proven stack): MSS = min(MTU from the
  CDTunnel handshake, 8192) - 60, window scale option sent (shift 0) and the
  peer's honoured, at most 1 MiB in flight; AFC writes 1 MiB per request.
- `baristore` is a supervisor: it runs the server as a child
  (BARISTORE_SERVE="port token") and restarts it when it dies; iOS takes
  memory from Minis in the background and Rust aborts on a failed allocation
  (seen on device). Exit 0 = quit, 75 = updated (the file is already
  the new build: the supervisor starts the next child, same address). Never
  exec from a process that ran tokio: in Minis's shell the exec'd process
  kept the old worker threads, which panicked on closed descriptors (seen on
  device, alpha.7); `baristore update` shuts its runtime down and runs the
  new build as a child. The page reloads when the
  server's boot id changes. Memory: IPAs are streamed to disk and only
  Mach-O/plist entries are kept in memory while signing; icon decoding is
  bounded; two tokio workers.
- Wi-Fi cannot be read in Minis. A connect to 10.7.0.1 that times out means no
  route (mobile data or no VPN): say so, never knock 16 000 ports then
  (siboot's preflight); a refused port does mean "look on other ports".
- The page's origin is stable per run, but nothing it must remember lives in
  localStorage: settings.json holds theme and setup_done.
  WKWebView in Minis shows no confirm()/alert(): the page uses its own sheets.
- The pairing service is usually on 49152 but can be another dynamic port
  (siboot 0.19.1): knock 49153–65535 except 62078. Device log 2026-11-21
  (iOS 27.2.1, alpha.8): 49152 took the TCP connection and then never answered
  our handshake (not refused), and the scan found nothing within the 45 s cap
  that alpha.5 put on the whole check (alpha.3/4 scanned without a limit and
  alpha.4's Reset began forgetting the saved port: the likely regression; Reset keeps `device-port` again; the
  pairing code and wire 19 are byte-identical to alpha.3, which worked).
  Since alpha.9 the scan has 240 s, 64 ports at a time (Minis's descriptor
  limit), the handshake is also tried with wire 26 (what iOS 27 sends as a
  host) in case 19 is now ignored, and the log says why a port did not
  answer. Untested on device.
- iOS 27.2+: every RemoteXPC handshake on a tunnel must present the same host
  UUID (idevice); we keep one per install (`~/.baristore/host-uuid`).
- SideStore nightlies read `Documents/PairingFile_RemoteRP.plist` only with
  prefs `isPairingReset=false`, `activePairingProtocol=rppairing`; LiveContainer's
  SideStore reads `SideStore/Documents/ALTPairingFile.mobiledevicepairing`;
  AltStore 2.3.1+ reads `Documents/ALTPairingFile.plist` (SideInstaller docs).
- Apple sign-in, checked live 2026-11-21 with a made-up account (Options › Log ›
  Run checks repeats it): through the macOS-library servers Apple takes
  the client they advertise (`<MacBookPro13,3> <Mac OS X;10.12.6;16G29>`, UA
  `akd/1.0 CFNetwork/811.5.4 Darwin/16.7.0`, iloader 2.4.2) and answers a
  current Mac with HTTP 503; a Windows claim fails provisioning there. Sign-in
  rotates servers × claims (advertised, then current Mac for -23782). No
  Android-library servers (-80046). Fresh HTTP/1.1 connection per GSA
  request (isideload: reused ones get 5xx). Windows-library servers need the
  hsc 434 resync. One anisette identity per (server, client info). Email is
  lowercased. Credential codes -20101/-20970/-22406 stop sign-in.
- GrandSlam SRP: M1 hashes H(N) xor H(PAD(g)) (srp 0.7.0-rc.3
  `g_no_pad = false`); unpadded g gives -22406 for a right password (seen on
  device 2026-11-21). The session data (spd) is keyed on S, falling back to
  H(S). Pair-setup keeps unpadded g (device-proven). Known-answer test from
  the old Python sign-in in `src/srp.rs`.
- Free teams: 10 App IDs at a time (SideStore counts the same); listAppIds
  gives each one's expiry. The install sheet picks "new" or an App ID whose app
  is not on the iPhone (installation_proxy Browse); when the IDs run out
  BariStore asks which one to reuse. Extensions only take ones under the
  app's identifier. The profile is checked for Increased Memory Limit.
  Installed.ids keeps the identifiers so refreshes keep them.
- StikDebug (com.stik.*) reads Documents/pairingFile.plist.
- JIT as StikDebug does it: iOS 26.4+ installs the DDI as a cryptex through
  cryptexd (read-personalization-id, get-nonce with the identity's
  Cryptex1,NonceDomain, a Cryptex1 ticket from TSS gs.apple.com, install with
  five XPC file transfers), files from doronz88/DeveloperDiskImage
  Xcode_iOS_DDI_Cryptex (manifest checked 2026-11-21). Then CoreDevice
  appservice launches the app stopped, debugserver attaches; apps StikDebug
  lists for universal.js / legacy.js get brk #0xf00d / #0x69 served (16 KiB
  pages each touched with M addr,1:69), others detach at once. universal.js's
  "new breakpoints" command (app-supplied JavaScript) is not supported.
- SideStore/AltStore refresh with the certificate in their bundle:
  `ALTCertificate.p12` (password = portal machineId) + `ALTCertificateID` =
  serial (SideStore CertificateProvisioningFlow; isideload). LC+SS: in the
  `com.SideStore.SideStore` framework. AltStore also gets `ALTDeviceID`.
- Each app extension needs its own App ID/profile, identifier under the app's.

## Verified vs not

Linux-verified: SRP pair-setup against idevice-srp, GrandSlam SRP against the
device-proven Python sign-in (known answer), pairing host against a
mock iPhone, TLS-PSK against openssl s_server, TCP stack against smoltcp, tunnel
against a mock device, CSR and PKCS#12 with openssl (3.x and 1.1.1), signatures
with rcodesign. **Device-verified (owner):** LocalDevVPN check, pairing (0.1.0); Apple sign-in
with 2FA, signing and installing an app (0.10.0-alpha.3).
Everything else: say "untested on device" until the owner confirms.

## Layout

`src/pairing` pairable host · `src/device` tunnel, TCP, XPC, services ·
`src/apple` anisette, GrandSlam, portal · `src/sign` code signing ·
`src/install.rs` App IDs → profiles → sign → install → pairing file ·
`src/store.rs` catalog · `src/icons.rs` icon cache · `src/app.rs` state/tasks ·
`src/web.rs` + `ui/index.html` the page · `src/doctor.rs` report ·
`src/update.rs` self-update. CI: `.github/workflows/release.yml` publishes
the `nightly` release that `install.sh` downloads.

## Rules

- No dead code, no unused options, no extra files; `cargo build --release` must
  have zero warnings; `cargo test` must pass.
- Never commit secrets, pairing records, keys, profiles or IPAs.
- Bump `version` in Cargo.toml on every functional change.
