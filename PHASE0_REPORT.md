# Phase 0 — Research & Feasibility

Status: **Phase 0 complete.** User decisions recorded in §0. Phase 1 not started; only the PoCs
in `POC_GUIDE.md` are authorised for now. Everything below was checked against real sources
(GitHub API/source, Apple docs, community reports) on 2026-10-14. Items we could not confirm are
marked **unverified**.

## 0. Confirmed decisions (user, 2026-10-14)

1. **Go for PoCs only**, not full Phase 1 yet. PoC results come back first.
2. **Target = iOS 27+ only.** Stated as a hard requirement in `README.md`.
3. **AirDrop is the primary install route.**
4. **English only for now.** Translations (English + Turkish) only after it works.
5. Test devices: **iPhone 13 (iOS 27)** = main target; **iPad (A16)** = AirDrop receiver.
6. PoC-A/B/C written for a non-programmer in `POC_GUIDE.md` (exact taps, exact commands,
   pass/fail meaning).

---

## 1. Environment check (GitHub Codespace, Linux)

| Tool | Present | Notes |
|---|---|---|
| git | 2.55.0 | |
| curl | 8.5.0 | |
| python3 | 3.14.2 | repo-side tooling only; **not** the on-device runtime |
| pip | 26.2.1 | |
| node / npm | 24.21.0 / 11.19.0 | |
| cargo / rustc | **missing** | not needed (we do not build Rust) |
| gcc / clang | 13.3.0 / 18.1.3 | |
| unzip / zip | present | |
| openssl | 3.5.8 | |
| jq, gh (authed) | present | `gh` token has 50k API budget |
| ripgrep | **missing** | use `grep` |

Repo state: `github.com/barismd/barisign`, single empty `Initial commit`.

---

## 2. Verified release sources for the four variants

Resolved **at runtime** from the GitHub Releases API — never hardcoded.

| # | Variant | Source | Tag/latest rule | Asset |
|---|---|---|---|---|
| 1 | SideStore stable | `SideStore/SideStore` | newest release that is **not prerelease AND whose tag has no `alpha`/`beta`/`rc`** | `SideStore.ipa` |
| 2 | SideStore nightly | `SideStore/SideStore` | rolling tag **`nightly`** | `SideStore.ipa` |
| 3 | LiveContainer + SideStore stable | `LiveContainer/LiveContainer` | newest non-prerelease, tag-clean rule as above | `LiveContainer+SideStore.ipa` |
| 4 | LiveContainer + SideStore nightly | `LiveContainer/LiveContainer` | rolling tag **`nightly`** | `LiveContainer+SideStore.ipa` |

Verified facts (GitHub API, 2026-10-14):

- `SideStore/SideStore` stable = `0.6.4` (2026-09-09). **Trap (empirically confirmed 2026-10-14):**
  `0.7.0-alpha` has `prerelease=false`, and GitHub's `releases/latest` **returns `0.7.0-alpha`** —
  so both `releases/latest` and a naive "newest non-prerelease" hand the user an alpha. The
  resolver must list releases and reject `alpha`/`beta`/`rc` in the tag name.
- `SideStore/SideStore` tag `nightly` is a rolling release; `SideStore.ipa` (31,137,651 B)
  was rebuilt **2026-10-13T20:51Z** while the release's `published_at` still reads 2023 — so
  version display must use the **asset `updated_at`**, not the release publish date.
- `LiveContainer/LiveContainer` stable = `3.8.0` (2026-07-17); tag `nightly` asset
  `LiveContainer+SideStore.ipa` (35,926,202 B) updated 2026-09-18. Asset name is URL-encoded
  (`LiveContainer%2BSideStore.ipa`).
- **No checksum asset is published for any of the four IPAs.** Integrity checks are therefore
  limited to: HTTP `Content-Length`, zip structural validity, post-signing `codesign`-equivalent
  verification, and displaying the SHA-256 we compute. ("Verify when checksums exist" → they
  don't, so we say so plainly in the README.)
- LiveContainer ships an official **combined** `LiveContainer+SideStore.ipa`; there is no need
  to splice two IPAs ourselves.

Nightly warning: both nightly tags are rolling pre-release builds; README/Shortcut must warn.

---

## 3. Research table (project, language, license, role, on-device?, cheapest reuse)

| Project | Lang | License | Role in our flow | Runs on-device? | Cheapest reuse |
|---|---|---|---|---|---|
| `SideStore/SideStore` | Swift | **AGPL-3.0** | variant source #1/#2; reference for sign/refresh | No (native, Xcode) | Take its released IPAs only |
| `LiveContainer/LiveContainer` | Swift | **AGPL-3.0** | variant source #3/#4 | No | Take its released IPA only |
| `nab138/isideload` | Rust | **MIT** | auth + sign + install core (used by SideInstaller) | No (native) | Best **MIT** logic source to port to Python |
| `jkcoxson/idevice` | Rust | **MIT** | on-device RSD/tunnel/AFC/installation_proxy | No | Protocol logic reference (MIT) |
| `jkcoxson/minimuxer` | Rust | **AGPL-3.0** | on-device usbmuxd | No | Reference only (AGPL is viral) |
| `jkcoxson/em_proxy` | Rust | **AGPL-3.0** | loopback install trick | No | Reference only |
| `jkcoxson/LocalDevVPN`, `SideStore/StosVPN` | Swift | **NOASSERTION** | provides the VPN peer `10.7.0.1` | Yes (App Store app) | Runtime **dependency** the user installs, not code |
| `FrizzleM/SideInstaller` | Swift | **custom** (non-commercial; source redistribution w/ attribution; **no** binary redistribution) | only known working PC-free on-device installer; ships `ashell/` pure-Python bootstrap (incomplete) | No (native) | Port `ashell/*.py` + protocol notes, with attribution |
| `StikDebug/StikDebug` | Swift | **AGPL-3.0** | on-device RSD tunnel / JIT | No | Reference only |
| `doronz88/pymobiledevice3` | Python | **GPL-3.0** | pure-Python idevice + developer auth + anisette client | Partly — needs `cryptography` (C ext) ⇒ **not** in a-Shell | Protocol reference (GPL: viral if copied) |
| `zhlynn/zsign` | C++ | **MIT** | IPA signing | No | Signing logic reference (MIT) |
| `claration/Feather` | Swift | **GPL-3.0** | on-device signer (developer certs) | No | Reference |
| `Dadoum/Provision` | D | NOASSERTION | Apple server auth/cert/profile | No | Reference |
| `rileytestut/AltSign` | Obj-C | **no license file** | auth/sign primitives | No | Reference only — **unsafe to copy** |
| `SideStore/apple-private-apis` | Rust | **MPL-2.0** | Apple private-API clients + anisette | No | Reference (file-level copyleft) |
| `nab138/iloader` | TS + Rust | **MIT** | desktop sideloader (needs PC+usbmuxd) | No | Reference for release URLs / error UX |
| `nodzen/sidex` | Shell | MIT | Xcode-only install, **no anisette** | No (needs Xcode) | Reference for anisette-free auth |
| public anisette servers (e.g. Dadoum anisette-v3 family, `hroy123/Anisette-server-26`) | D/misc | mixed | supply anisette headers to the on-device login | Yes (network) | Call at runtime, with a fallback list |
| `holzschu/a-Shell` | Obj-C/Perl/CPython | **BSD-3-Clause** | the on-device runtime | **Yes** (App Store) | The compute layer |

**License bottom line for a public repo:** our own code can be MIT/permissive as long as we do
**not** copy AGPL/GPL code (SideStore, LiveContainer, minimuxer, StikDebug, Feather,
pymobiledevice3, AltSign) or NOASSERTION code without terms (LocalDevVPN/StosVPN, Provision).
Usable for porting: **isideload (MIT)**, **idevice (MIT)**, **zsign (MIT, reference)**,
**SideInstaller (custom: non-commercial, source-only, attribution, changes noted)**.

---

## 4. On-device runtime decision

**Constraint measured on real hardware** (SideInstaller `ashell/README.md`, iPhone 17,3 / iOS 27,
2026-08-27 — credible, device-measured, and the only such evidence found):

- a-Shell runs Python **3.13.1 native arm64**, full stdlib, ~385 commands, `os.system` available.
- **No `fork`/`exec`** ⇒ no native binary can be shipped. a-Shell's clang targets **WASM**, and
  WASM there has "no sockets, no forks" ⇒ WASM cannot carry networking either.
- No `openssl`, no `unzip` (stdlib `zipfile`/`hashlib`/`ssl`/`plistlib` cover the gaps).
- BSD sockets really work; X25519 ≈1 ms, SHA-256 ≈2.9 GB/s, RSA-2048 keygen ≈2 s.

| Candidate | Verdict |
|---|---|
| **a-Shell (full)** | **Chosen.** Free, App Store, Python + real sockets. a-Shell **mini** works too (386 MB vs 2 GB; has Python, curl, network utils). Runtime proven adequate on-device. |
| iSH | Rejected: x86 emulation, no iOS Local Network permission (connections to the device's own services are dropped), slow. SideInstaller's Rust build failed here. |
| Terminus (a-Shell repackage) | Same runtime; optional alternate host. |
| Pythonista / Pyto | Paid and/or no first-class Shortcuts→script stdin contract as clean as a-Shell's; reject. |
| Scriptable / JavaScriptCore | No raw TCP sockets; reject for the device protocol. |
| Pure Shortcuts actions | Cannot sign, cannot speak the device protocol; used **only** for UI/orchestration. |
| Native apps (SideInstaller/Feather/SideStore) | Not scriptable from a Shortcut; used as **references**, and LocalDevVPN is a runtime dependency. |

**∴ Compute layer = a-Shell (full), one stdlib-only Python file.** No pip step.

---

## 5. Feasibility verdicts

### 5.1 Loopback `itms-services` on the device itself — **REJECT (recommend not building it)**

Findings:
- `itms-services://?action=download-manifest&url=…` requires the **manifest over HTTPS from a
  cert that validates against a trusted CA**. A **self-signed leaf is insufficient since iOS 12,
  even if installed** (Apple deployment docs + Studio Drydock). It must be a **CA** whose root is
  installed **and** manually trusted in *Settings → General → About → Certificate Trust Settings*.
- Shipping a private key in a public repo is unacceptable, so the CA+leaf must be generated
  **on-device** — but a-Shell has **no `openssl`** and Python's stdlib cannot emit X.509, so we'd
  have to hand-roll ASN.1/DER + RSA signing. Extra project, extra risk.
- **Reachability:** the installer runs in a separate system process (`itunesstored`/`installd`).
  Whether that process can reach a socket bound on `127.0.0.1` inside an app sandbox is
  **unverified** and there is no evidence for it.
- **Lifetime:** the Shortcut ends after the script; iOS suspends the a-Shell process. A loopback
  HTTP server must stay alive *until the installer finishes*, which a suspended app cannot do.
  No supported background mode covers this.
- Security: any app/attacker able to reach loopback (or the phone's LAN) could pull the signed
  IPA and the private key material.

**Verdict:** cannot be shown to work, and the private-key/server-lifetime problems are
architectural. **Recommended: do not use.** Minimal PoC (only if you want the negative confirmed
on your device — see §6.1) is cheap but we do not plan to build a product on it.

### 5.2 `itms-services` from a per-user private HTTPS location — **WORKABLE, but not primary**

- Technically sound: a real host with a real (DV) cert satisfies the trust rule; the manifest and
  IPA just need to be HTTPS and publicly fetchable by the device.
- **Privacy:** the **signed** IPA contains your UDID and the provisioning profile, and the link is
  a bearer URL — anyone with it can download the artifact. Uploading it to third-party storage
  leaks that metadata to that provider and to anyone who learns the URL.
- Requires the user to have a private HTTPS location and to upload a fresh file per install/refresh.

**Verdict:** viable fallback, documented with the privacy trade-off. Not the default.

### 5.3 AirDrop of the signed IPA — **PRIMARY (recommended)**

- iOS **17.1+ installs a signed, AirDropped `.ipa`** by prompting to install (community-verified
  via multiple reports; not an Apple-documented feature of the sideloading flow).
- No server, no certificate, no private key, no background-execution problem: the **user** taps
  *Install* in the share sheet, so nothing needs to survive after the Shortcut ends.
- Only requirement: the IPA must be **validly signed for that device** (UDID in the profile) —
  which is exactly what our signer produces.

**Residual unknowns (must be device-tested):** AirDrop-to-self behaviour; whether the same flow
works from *Files* → tap IPA on your iOS version; and the whole signing path (§5.4).

### 5.4 The real blocker is **not** install — it is **auth/sign/pairing on-device in pure Python**

- To sign for the device you need (a) an Apple-ID session (SRP + 2FA) via anisette, (b) a
  development certificate + App ID + provisioning profile for the device's **UDID**, (c) a
  CMS/PKCS#7 signature over the Mach-O files.
- **Anisette**: only anisette *headers* are requested from an anisette server; your Apple ID and
  password go to **Apple** (`gsa.apple.com`), never to the anisette server. This satisfies the
  "never send credentials to a server I don't control" rule, and we will document exactly which
  fields leave the device. Fallback list of servers with retry, as requested.
- **Getting the UDID on-device** is the crux:
  - iOS **27+**: SideInstaller states no pairing file is needed ⇒ a computer-free path exists.
  - iOS **17.4 – 26.x**: a **pairing file is required**, and the established statement is that it
    "requires a PC". On-device pairing-file generation is possible in *native* apps via
    RPPairing + the LocalDevVPN peer, but **no working pure-Python implementation exists**.
- Nobody has demonstrated the full pure-Python on-device path: SideInstaller's own a-Shell
  bootstrap is at **"checkpoint 1 of 5"** and its author states the device reset every protocol
  attempt on port 49152 until they found the real framing (RPPairing: `"RPPairing"` + `u16be len`
  + JSON, over the LocalDevVPN peer `10.7.0.1:49152`), after which the tunnel stack (pair-setup →
  TLS-PSK → CDTunnel → software TCP stack → RSD/RemoteXPC → AFC/`installation_proxy`) is still
  unimplemented in Python.

**Verdict (honest):** signing four fixed IPAs on-device with a free Apple ID in stdlib-only
Python is **feasible in principle and a large, high-risk build**; the on-device *install* is
solved by AirDrop; the genuinely hard, undemonstrated parts are the **iOS 17.4–26.x pairing/UDID**
and the optional `itms-services` route. This is the risk you must accept before Phase 1.

---

## 6. Recommended architecture (decided after the research above)

```
iOS Shortcut (pure Shortcuts actions; all text from i18n files)
  1. readiness checks: LocalDevVPN installed? connected? Developer Mode on? Apple ID ready?
  2. variant picker (4 options) -> shows resolved version after the script resolves it
  3. "Run a-Shell command"/Script action -> runs barisign.py <variant> [lang]
        barisign.py (single file, stdlib only, in a-Shell):
          a. resolve IPA URL + version at runtime (GitHub Releases API)
          b. download IPA (Content-Length check, SHA-256 shown)
          c. Apple ID sign-in via anisette (SRP + 2FA), fallback anisette servers on failure
          d. obtain cert + App ID + provisioning profile for this device
          e. sign the IPA (pure-Python CMS + entitlements)
          f. hand the signed IPA back to the Shortcut
  4. install: Share sheet -> AirDrop (primary); Files-open route as documented alternative
```

**Why:** only a-Shell gives a free, on-device, scriptable runtime with real sockets and Python;
Shortcuts alone cannot sign; AirDrop removes the cert/server/backgrounding problems entirely.

### Feasibility PoCs for you to run (all on-device, no Apple ID needed for the first ones)

- **PoC-A (runtime):** in a-Shell, `python3 -c "import sys,ssl,socket,hashlib,zipfile,plistlib; print(sys.version, ssl.OPENSSL_VERSION)"` and confirm `python3 --version` + that `curl` exists. Confirms runtime claims on *your* device.
- **PoC-B (install route, no signing):** AirDrop any **already-signed** IPA to your own iPhone, and separately: save a signed IPA to *Files* and tap it. Report whether the install prompt appears. (Needs an IPA signed for your device; if you have none, a SideStore-signed app from an existing setup works.)
- **PoC-C (VPN peer):** with LocalDevVPN connected, in a-Shell try to open a TCP connection to `10.7.0.1:49152` and report whether it stays open (SideInstaller measured: it does, and resets on the first wrong bytes). Uses the stub we ship.
- **PoC-D (loopback itms-services, optional/negative):** serve a manifest+IPA on `https://127.0.0.1:<port>` with a user-trusted CA and open the `itms-services` link from Safari; report the error. Only run if you want §5.1 confirmed empirically.
- **PoC-E (auth, credentials):** only after you approve Phase 1 — sign in with a throwaway Apple ID and report the step reached.

---

## 7. Facts vs. unknowns

**Verified:** release sources, tags, asset names/sizes, the `prerelease=false` alpha trap, absence
of checksums, all licenses above, a-Shell runtime capabilities, the itms-services cert rule, the
LocalDevVPN peer/RPPairing framing, that SideInstaller's a-Shell path is incomplete.

**Unverified (needs your device):** AirDrop-to-self and Files-install on your iOS version; whether
the installer can reach loopback; whether a-Shell ships `cryptography` or can `pip install`
pure-Python packages (plan assumes **stdlib only**, so this is not required); end-to-end auth and
signing; iOS 17.4–26.x pairing without a computer.

---

## 8. Phase 0 close-out — decisions taken

1. **Authorised now:** PoC-A/B/C only (see `POC_GUIDE.md`). **Not** authorised yet: full Phase 1.
2. **Target = iOS 27+ only**; on iOS 17.4–26.x a computer-free first install may be impossible
   (pairing file) and is explicitly out of scope. Stated in `README.md`.
3. **Primary install route = AirDrop** (confirmed). Private-HTTPS `itms-services` kept as a
   documented fallback only.
4. **Languages:** English now; English + Turkish in the final phase, after it works.
5. Next action is on the user: run PoC-A/B/C on iPhone 13 (iOS 27) + iPad (A16) and report results.

---

# 9. PoC results (user-run on real devices, 2026-10-14)

Devices: **iPhone 13 (iOS 27)**, a-Shell full; **iPad (A16)** as the AirDrop target.
VPN state was not recorded for PoC-A/B; PoC-C was run with LocalDevVPN **on** and **off**.

## 9.1 PoC-A — runtime — **PASS**

| Check | Result |
|---|---|
| A1 | `Python 3.13.1+` |
| A2 | PASS (`ssl` reports `OpenSSL 1.1.1i 8 Dec 2020`) |
| A3 | `curl 8.1.2` (OpenSSL/1.1.1w, https). `openssl` → **command not found**. `unzip` → **command not found** |
| A4 | `pip install pyaes` → **Successfully installed pyaes-1.6.1** (pure-Python wheel built on device) |
| A5/A6 | `cryptography` **3.4.8 present**; `AESGCM` and `pkcs7` import OK |

Two assumptions changed (see §10): third-party **pure-Python packages can be installed**, and
`cryptography` **is already there**.

## 9.2 PoC-C — the device's own install door — **PASS**, with two surprises

| Check | Result |
|---|---|
| C1 | `utun4 inet 10.7.1.1 --> 10.7.1.1 netmask 0xffffffff`; **no `10.7.0.1` entry shown** |
| C2 | TCP to `10.7.0.1:49152` → **OPEN**, still open after 5 s idle → **PASS** |
| C3 | `connect_ex(127.0.0.1:49152)` → **`0` (connected)** — with the VPN **on and off** |
| C3 control | `connect_ex(127.0.0.1:50321)` → `61` (refused, as expected) |
| C4 | send 1 zero byte → `recv` **timed out after 3 s**; no reset, no bytes returned |

Surprises (both **unverified**, one port-scan command is in §15):

1. `10.7.0.1` is reachable although `ifconfig` only lists the local `/32` `10.7.1.1` — the peer is
   routed, not listed.
2. Something listens on **loopback `49152` regardless of the VPN**. This contradicts the Phase 0
   assumption that 49152 is only the VPN peer; the port is not proof that the tunnel is up.
3. C4 timed out instead of resetting. SideInstaller reported a reset on wrong bytes, so this
   behaviour is version/protocol dependent (**unverified**).

**Conclusion:** the door exists and holds an idle connection (the property the design needs), but
"port 49152 answers" is **not** a reliable VPN/readiness signal, and the readiness check must not
use it.

## 9.3 PoC-B — install route — **routing PASS**

- iOS 27 **accepts and installs an unsigned `SideStore.ipa` 0.6.4** via **both** a Files tap and
  **AirDrop iPhone → iPad**. The app then **does not open** (expected — unsigned).
- Not recorded: whether the Files tap *on the iPhone itself* installs to its own home screen.
  To re-check.
- B3 skipped (no signed IPA available yet).

**Conclusion (important):** iOS enforces signature validity at **launch**, not at install. The
AirDrop plan therefore needs *only* a valid signature — no install trick. This removes the entire
cert/loopback/private-key problem from the critical path.

---

# 10. Corrections to Phase 0 assumptions

| Phase 0 assumption | Correction (PoC-A) |
|---|---|
| No third-party/C-extension packages | **Wrong.** Pure-Python `pip install` works on-device (`pyaes` installed). |
| We'd need a pure-Python AES fallback | **Resolved.** `cryptography` (AES-GCM/CBC) is present; a pure-Python AES also exists in the reusable code. |
| Signing needs `openssl` | Still true (`openssl` is absent) — but every `openssl` call in the reusable code is replaceable by `cryptography`. |
| Runtime is modern | a-Shell's Python ships **OpenSSL 1.1.1i (2020)** — old. Watch it for Apple endpoints (PoC-D check 6 tests exactly this). |
| 49152 open ⇒ VPN up | **Wrong.** Loopback 49152 answers with the VPN off. |

---

# 11. What `cryptography` 3.4.8 can and cannot do — VERIFIED (read from its 3.4.8 source)

**Can do:** RSA key generation, CSR builder, DER/PEM X.509 load, PKCS#1 v1.5 sign/verify,
AES-GCM, AES-CBC, SHA-256 digests, PKCS#12 load.

**Cannot do:** produce an **Apple code-signing CMS**. `PKCS7SignatureBuilder` in 3.4.8 offers only
the options `Binary`, `DetachedSignature`, `NoCapabilities`, `NoAttributes`, plus
`add_signer(cert, key, hash_algorithm)` — there is **no API for custom signed attributes**.

**Why that matters (verified in zsign, MIT):** Apple's signature CMS is built as
`CMS_PARTIAL | CMS_DETACHED | CMS_NOSMIMECAP | CMS_BINARY` with SHA-256 and then two Apple
attributes are added explicitly:

- OID `1.2.840.113635.100.9.1` — the CDHashes plist;
- OID `1.2.840.113635.100.9.2` — the alternate CodeDirectory SHA-256, uppercase hex, wrapped in an
  ASN.1 SEQUENCE.

`cryptography` cannot emit those, so **the signer must assemble CMS itself.**
Planned route: **`asn1crypto`** (pure Python, pip-installable on-device by the same mechanism that
installed `pyaes`) for ASN.1/CMS, plus `cryptography` for RSA and hashing. Fallback: a hand-rolled
DER writer with no dependency. **Unverified until it runs on-device.**

---

# 12. Breakthrough: an existing pure-Python pipeline we can port

`FrizzleM/SideInstaller`, file **`ish/sideinstaller.py`** (2003 lines, pure Python, written to run
inside iSH) already implements, with **no native dependency**:

pure-Python AES (own S-box), AES-GCM, AES-CBC · `SRPClient` (RFC 5054 2048-bit + SHA-256) and the
PBKDF2 password key · a raw WebSocket client · **Anisette v3 including provisioning** · GrandSlam ·
`AppleAccount` sign-in including trusted-device and SMS 2FA · `app_token` · `DeveloperSession` ·
teams · device registration · certificate issue/reuse · App ID · provisioning profile · GitHub
release resolution (with correct alpha/prerelease filtering).

Only two things need native tools, and **both are outside the sign-in path**:

1. `openssl` — key generation, CSR, DER→PEM, modulus comparison, and the local CA/TLS server.
   Replaceable with `cryptography`.
2. `zsign` — the actual Mach-O/CMS signature. **Must be written in pure Python.**

**Licence (constrains this repo):** SideInstaller is under a **custom licence — non-commercial,
source-only redistribution, attribution required, changes must be noted**. The licence text is
copied to `third_party/SideInstaller-LICENSE.md`. Consequence: **files ported from it cannot be
relicensed MIT, and this repository cannot be used commercially.** Our own files can stay MIT;
the ported ones must carry their notice. (Decision requested in §16.)

## 12.1 What we verified ourselves in the Codespace (Linux — not a device)

`python3 poc/poc_login.py --selftest`:

- **PASS** runtime; `cryptography` (RSA-2048 keygen 0.1 s, CSR 611 B, PKCS#1 v1.5 sign+verify);
  pure-Python AES-CBC round-trip; SRP group confirmed **RFC 5054 2048-bit, g = 2**.
- **PASS** after trusting Apple's public root (which Linux lacks and iOS has):
  **anisette v3 provisioning over the real WebSocket succeeded against `ani.sidestore.io`**, and
  headers `X-Apple-I-MD`, `X-Apple-I-MD-M`, `X-Mme-Device-Id` were minted;
  and the TLS handshake to `gsa.apple.com` verifies.
- **Finding:** `gsa.apple.com` chains to the legacy **"Apple Root CA"**, which desktop/Linux trust
  stores lack (`openssl verify`: *error 19, self-signed certificate in certificate chain*). iOS
  trusts it. If a-Shell's Python does not, we ship the public root CA — PoC-D check 6 detects this
  on-device and names the fix.
- Two bugs in **our glue** found and fixed while testing: the AES self-test used a 17-byte string;
  and upstream's `anisette_candidates()` needs a string hint (`""`), not `None`.

---

# 13. Revised Phase 1 plan and risk order

Architecture is unchanged from §6. New: **build by porting `ish/sideinstaller.py`**, not from scratch.

Riskiest first:

1. **Pure-Python Mach-O + CMS signer — highest risk, make-or-break.** Needs the CodeDirectory
   (SHA-256, entitlements hash, DER entitlements), CodeResources, a CMS SignedData carrying the two
   Apple OIDs, `LC_CODE_SIGNATURE` insertion, and zip repacking. No pure-Python implementation is
   known to exist (**unverified**). If this fails, the project fails.
2. **Apple sign-in on-device** — medium, now largely de-risked (the code exists). Unknown: whether
   a-Shell's **OpenSSL 1.1.1i** and its CA store can talk to `gsa.apple.com`.
3. **Anisette provisioning on-device** — medium-low (proven over WebSocket here; must run under
   a-Shell's Python).
4. **Developer portal** (certificate, App ID, profile) — low (same code; needs a real Apple ID).
5. **UDID acquisition on iOS 27** — low-medium. SideInstaller states no pairing file is needed on
   iOS 27; *how* it obtains the UDID without the mobileconfig trick is **unverified**.
6. **AirDrop install** — low; PoC-B already shows iOS routes it.

---

# 14. Step 1 (authorised now): PoC-D — on-device Apple sign-in

Deliverable: **`poc/poc_login.py`** (untested on a device; checks 1–4 and, with a trusted root,
5–6 were verified on Linux as described in §12.1). It is the vendored login stack plus our own CLI,
with exactly two documented changes (header of the file).

**Run these in a-Shell, one line at a time.**

1. Download it:

```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/poc/poc_login.py -o poc_login.py
```

2. Credential-free self-test (safe; no Apple ID anywhere):

```
python3 poc_login.py --selftest
```

3. Only if all six checks PASS — sign in with a **throwaway** Apple ID:

```
python3 poc_login.py --login YOUR-THROWAWAY-APPLE-ID
```

What the checks mean:

| Check | PASS means |
|---|---|
| 1/6 runtime | a-Shell's Python/SSL respond |
| 2/6 crypto toolkit | `cryptography` can make keys, CSRs and signatures |
| 3/6 AES-CBC | the sign-in reply can be decrypted without `openssl` |
| 4/6 SRP group | the RFC 5054 group constants are intact |
| 5/6 anisette | an anisette server provisioned and minted headers (**no Apple ID involved**) |
| 6/6 Apple TLS | a-Shell's Python trusts `gsa.apple.com` |

- Steps 2 and 3 stop after sign-in: **no device registration, no certificate, no profile, no
  signing, nothing installed.**
- Report the whole screen. A `6/6 FAIL` is the single most important line — it decides whether we
  ship Apple's public root CA.

---

# 15. Loopback port scan (one line, for a-Shell)

```
python3 -c "import socket; f=lambda p:(lambda s:(s.settimeout(0.4),s.connect_ex(('127.0.0.1',p)))[1])(socket.socket()); print([(p,f(p)) for p in range(49150,49161)])"
```

`connect_ex` returns `0` when something is listening and a non-zero error code (usually `61`) when
nothing is. Run it **with LocalDevVPN off and again with it on**, and send both lines.

---

# 16. Decisions needed now

1. **Licence:** accept that ported SideInstaller files keep their **non-commercial** licence and
   this repo therefore cannot be MIT throughout (our own files stay MIT)? Or rewrite the ported
   parts from the protocol to stay fully MIT?
2. **Proceed to the signer** (§13 item 1) as Phase 1, or do more login PoCs first?
3. Anything you want changed about `poc/poc_login.py` before you run it?
