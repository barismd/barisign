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

**Licence (constrains this repo) — decided:** SideInstaller is under a **custom licence:
non-commercial, source-form-only redistribution, attribution required, changes must be noted**
(full text now at `third_party/sideinstaller/LICENSE.md`; the key conditions are quoted in
`THIRD_PARTY_NOTICES.md`). Decision: **keep the port but isolate it.** Every file derived from
SideInstaller lives in **`third_party/sideinstaller/`** with its licence, attribution and a list of
our changes; everything else stays MIT. Because that folder is part of this repository, **the
repository as a whole cannot be used commercially.** Where an MIT project (`isideload`, `idevice`)
covers the same logic, we will reimplement from it and shrink that folder.

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

Deliverable: **`poc/poc_login.py`** (MIT) plus the vendored module
**`third_party/sideinstaller/signin.py`** (non-MIT; see §12). Untested on a device; checks 1–6 were
verified on Linux as described in §12.1.

**Run these in a-Shell, one line at a time.**

1. Download both files into one folder (they must sit side by side):

```
mkdir -p ~/barisign && cd ~/barisign
```

```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/third_party/sideinstaller/signin.py -o signin.py
```

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

# 16. Decisions taken (user, 2026-10-14)

1. **Repo visibility:** make it public. *(Attempted via API — the token returns HTTP 403 "Resource
   not accessible by integration", so the user must flip it: repo → Settings → General → Danger
   Zone → Change repository visibility → Public. Raw URLs 404 until then.)*
2. **Licence:** keep the port, but **isolate** — all SideInstaller-derived files live in
   `third_party/sideinstaller/` with their licence, attribution and our change list; everything else
   stays MIT; prefer MIT sources (`isideload`, `idevice`) for reimplementation; state plainly in the
   README that the repo contains non-commercial-licensed parts. **Done** (`signin.py`,
   `LICENSE.md`, `README.md`, `THIRD_PARTY_NOTICES.md`, README section "Licences").
   The key licence conditions are quoted in `THIRD_PARTY_NOTICES.md`.
3. **Next build step:** **wait for the PoC-D results** before starting anything else.

## Next action

1. Flip the repository to public (step 1 above).
2. Run PoC-D in a-Shell (§14) and send the whole screen.
3. Report the §15 port-scan output (VPN off and on).

---

# 17. Round 2 — device results (iPad15,7, a-Shell) and fixes

iPad selftest: checks **1–4 PASS**, check **5 FAILED** with `PermissionError(1, 'Operation not
permitted')`, check **6 FAILED** with `CERTIFICATE_VERIFY_FAILED` on `gsa.apple.com`. All four
follow-up items were fixed without running anything on the device.

## 17.1 Writable paths (a-Shell only writes under `~/Documents`)

Confirmed on-device: `mkdir -p ~/barisign` is refused because the container home is read-only.
Fix: every file the PoC writes now lives under **`~/Documents/barisign`**
(`POC_HOME` = `~/Documents/barisign`, state at `~/Documents/barisign/state/anisette.json`).
`_ensure_dir()` prints the exact path and a full traceback if creation fails, and the top-level
handler catches `PermissionError` anywhere and prints the offending path plus a traceback.

## 17.2 Apple trust (fixes check 6)

Root cause confirmed: a-Shell's Python carries its own CA list, which does not include Apple's root
(Apple's own devices do trust it, but Python here does not).

Fix: **`third_party/apple-roots/apple-roots.pem`** is shipped and used **only for `*.apple.com`**
hosts, via `signin._ctx_for(host)`; the default context is untouched for every other host.
**Verification is never disabled.**

| | |
|---|---|
| Source URL | `https://www.apple.com/appleca/AppleIncRootCertificate.cer` (from <https://www.apple.com/certificateauthority/>) |
| Subject | `C=US, O=Apple Inc., OU=Apple Certification Authority, CN=Apple Root CA` |
| SHA-256 | `B0:B1:73:0E:CB:C7:FF:45:05:14:2C:49:F1:29:5E:6E:DA:6B:CA:ED:7E:2C:68:C5:BE:91:B5:A1:10:01:F0:24` |
| Apple's published value | same, at <https://support.apple.com/en-us/121672>, row "Apple Root CA" |

The fingerprint is **pinned in `poc_login.py`** (`APPLE_ROOT_FINGERPRINTS`); check 6 hashes the file,
refuses to trust it unless it matches, then handshakes with the Apple context and asserts the Apple
context was really used. Only the root is needed — the server sends the intermediate
"Apple Server Authentication CA" itself.

## 17.3 Why the script mentioned `api.github.com/repos/FrizzleM/SideInstaller/releases`

It is upstream's `RELEASE_API` constant, part of SideInstaller's own "download my latest release"
flow. **The function that used it (`latest_release_ipa`) was never part of our slice**, so it was a
dead constant, not a call. It has been **removed**, along with upstream's unused `download()` helper
(both are now noted as removed in `third_party/sideinstaller/signin.py`). The only remaining
non-Apple network target in the login path is the anisette server.

## 17.4 Where the Apple ID password can go — audit

| Step | Where the password/time is | What leaves the device |
|---|---|---|
| `poc_login.py` | `getpass.getpass()` — no echo, one local variable | nothing |
| `AppleAccount.login(password, ask_code)` | passes it down as an argument | nothing |
| `srp_password_key(password, salt, iters, protocol)` | `hashlib.sha256(password.encode())`, then PBKDF2-HMAC-SHA256 | nothing (pure local compute) |
| `SRPClient.process(email, password_key, salt, B)` | computes the proof | nothing |
| `plist_request(gs.url("gsService"), …)` | sends `M1` (proof), `A2k` (public ephemeral), `u` (email) | the **proof**, never the password |
| 2FA (if Apple asks) | `ask_code()` reads the code you type | the code to `gsa.apple.com` only |
| after login | `password = None` | — |

Guarantee added: **`GrandSlam.plist_request()` now refuses any URL that is not an `apple.com` host**,
so even a tampered URL bag cannot redirect the proof or a 2FA code to a third party. Verified locally:
`plist_request("https://evil.example.com/steal", …)` → *"Refusing to send an authenticated Apple
request to 'evil.example.com'."*

Hosts actually contacted by the login PoC: the anisette server (identity headers only — no Apple ID,
no password, no token) and `gsa.apple.com` (SRP proof and, if needed, the 2FA reply).
`appleid.apple.com` appears only inside a help message, and `gsa.apple.com/auth/verify/phone/securitycode`
is used only if you choose SMS 2FA.

## 17.5 Verification of the fixes (run in the Codespace, Linux)

- `python3 poc/poc_login.py --selftest` → **PASS** on all six checks, with the shipped Apple root
  alone (no `SSL_CERT_FILE` workaround), reporting the pinned fingerprint.
- The flat on-device layout (three files in one folder) was tested: `apple-roots.pem` is found next
  to the script and the CLI runs.
- Host-guard test above passes.

## 17.6 Updated on-device commands

```
mkdir -p ~/Documents/barisign && cd ~/Documents/barisign
```
```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/third_party/sideinstaller/signin.py -o signin.py
```
```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/poc/poc_login.py -o poc_login.py
```
```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/third_party/apple-roots/apple-roots.pem -o apple-roots.pem
```
```
python3 poc_login.py --selftest
```
Then, only if all six PASS: `python3 poc_login.py --login YOUR-THROWAWAY-APPLE-ID`

---

# 18. Round 3 — second device run (iPad15,7) and fixes

Second run: checks **1–4 PASS**; check **5** failed with every anisette server; check **6** failed with
`apple-roots.pem holds no pinned Apple root; found 0 cert(s)`. Two separate bugs, both fixed.

## 18.1 Bug 1 (ours): `.gitignore` excluded the Apple root CA

Our own `.gitignore` contains `*.pem`, which silently excluded `third_party/apple-roots/apple-roots.pem`
from the commit while `AppleIncRootCertificate.cer` and the README went in. The user's `curl` for that
path therefore saved the literal text **`404: Not Found`** into `apple-roots.pem`, so the file existed
but held no certificate — exactly what check 6 reported.

Fix: explicit negations in `.gitignore` for the public trust anchors
(`!third_party/apple-roots/apple-roots.pem`, `!third_party/apple-roots/AppleIncRootCertificate.cer`,
`!third_party/ca-bundle/cacert.pem`), and the files are now tracked. Lesson recorded: `.gitignore`
rules for `*.pem` must be checked against the files we *do* want to ship.

## 18.2 Bug 2 (device constraint): a-Shell's Python has an EMPTY trust store

The anisette failures are not server problems. `ani.sidestore.io` returned
`[X509: NO_CERTIFICATE_OR_CRL_FOUND] no certificate or crl found`, which means the **client** has no
CA certificates loaded; the other servers returned `self signed certificate in certificate chain`.
So `ssl.create_default_context()` inside a-Shell yields a context with no roots, and **every**
`https://` request fails — not just Apple's.

Reproduced and fixed in the Codespace:

| Context | `ani.sidestore.io` | `gsa.apple.com` |
|---|---|---|
| empty trust store (mimics the iPad) | FAIL | FAIL |
| empty store + shipped `cacert.pem` | **OK** | FAIL (expected) |
| + `apple-roots.pem` for apple.com | — | **OK** |

Fix: **`third_party/ca-bundle/cacert.pem`** (curl project's build of Mozilla's CA list, **public
domain**, 121 certificates, SHA-256 `a41b5d356aea97a529fe27e0f7316d2f9d946d75927476cf9cf1b90637d00505`)
is loaded with `signin.set_ca_bundle()` for **every** host. `poc_login.py` prefers a bundle already on
the device (`certifi`) and falls back to the shipped file. **Verification is never disabled.** Apple's
root is still loaded *additionally* for `*.apple.com` only.

Self-test now prints a TLS diagnostic (default CA file/dir, which bundle was used, how many roots the
default context has) so the next report is conclusive either way.

## 18.3 Verification (Codespace, Linux)

- All six checks **PASS** with `certifi`.
- All six checks **PASS** with `certifi` forced unavailable, i.e. using the shipped `cacert.pem` +
  `apple-roots.pem` — the exact path the device will take.
- Empty-trust-store reproduction above.

## 18.4 Updated on-device commands (four files)

```
mkdir -p ~/Documents/barisign && cd ~/Documents/barisign
```
```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/third_party/sideinstaller/signin.py -o signin.py
```
```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/poc/poc_login.py -o poc_login.py
```
```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/third_party/apple-roots/apple-roots.pem -o apple-roots.pem
```
```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/third_party/ca-bundle/cacert.pem -o cacert.pem
```
```
python3 poc_login.py --selftest
```
Then, only if all six PASS: `python3 poc_login.py --login YOUR-THROWAWAY-APPLE-ID`

Sanity check before running (each should be non-zero, and the last should print a certificate):

```
wc -l signin.py poc_login.py apple-roots.pem cacert.pem
```
```
grep -c BEGIN apple-roots.pem cacert.pem
```

---

# 19. Round 4 — third device run: selftest PASS, `--login` appeared to hang

## 19.1 Result

Selftest: **all six checks PASS** on the iPad.
- TLS diagnostic confirmed the diagnosis: default CA file `…/a-Shell.app/cacert.pem`, bundle used
  `…/certifi/cacert.pem`, **127 roots** — so the trust-store fix works.
- Anisette succeeded via `ani.sidestore.zip` after two servers dropped (expected flakiness).
- Check 6 verified the pinned Apple root and handshook successfully.

`--login`: after the password was typed and Return pressed, **nothing happened**.

## 19.2 Root cause of the apparent hang (ours)

`getpass.getpass()` opens `/dev/tty` directly. Inside a-Shell that path **opens but is not the
terminal you are typing into**, so the call blocks forever right after printing the prompt — the
password is read into a local variable and no request is ever made. Two contributing factors:

1. `getpass` was the wrong primitive for a-Shell.
2. The sign-in path printed **nothing** between the prompt and Apple's reply, so a slow exchange and a
   hang looked identical.

## 19.3 Fixes

- `_read_password()` replaces `getpass`: it reads from **stdin**, disables echo with `termios` when
  stdin is a TTY, restores the terminal in a `finally`, and falls back to `input()` (with a warning
  that characters will be visible) if anything is unsupported. Verified by piping input: returns the
  line and never blocks.
- The PoC now prints how many characters were received, and a note that Apple may ask for a 2FA code.
- `signin.PROGRESS` hook added (5 call sites): "asking the anisette server for fresh headers" →
  "contacting Apple: SRP init (no password leaves this device)" → "computing the SRP proof locally" →
  "sending the proof to Apple (still no password)" → "Apple accepted the proof…". The CLI wires it to
  a printer, so the exchange can never look dead again.
- Run instructions now use `python3 -u` so output is unbuffered.

## 19.4 What happened to the password that was typed

- It was entered into `getpass` **locally**, was not echoed, and was **never written to any file**.
- Because `getpass` blocked, the process never reached the network: **no request of any kind was
  made**, and the SRP exchange had not started. The anisette server saw only the random identifier.
- Caveat stated plainly: a-Shell is a terminal, so a terminal app can in principle see keystrokes. If
  that is not acceptable, change the password.
- The account used was the user's **main** Apple ID, not a throwaway. If the PoC is re-run with it,
  Apple will require two-factor authentication, which the PoC supports (trusted-device code or SMS).
  A throwaway account is still recommended.

## 19.5 Next run

Only two files changed; re-download them and re-run:

```
cd ~/Documents/barisign
```
```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/third_party/sideinstaller/signin.py -o signin.py
```
```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/poc/poc_login.py -o poc_login.py
```
```
python3 -u poc_login.py --login YOUR-APPLE-ID
```

---

# 20. Round 5 — 2FA answer, URL-bag hardening, README rewrite

## 20.1 Is 2FA supported? YES

Verified by reading the vendored sign-in code. `AppleAccount.login()` loops over the sign-in states
and handles both Apple flows:

| Flow | What the code does |
|---|---|
| Trusted device | `GET gs.url("trustedDeviceSecondaryAuth")` to push a code (or Apple already pushed it, reported in `sm`), then prompts for the code and `GET gs.url("validateCode")` with a `security-code` header |
| SMS | `GET gs.url("secondaryAuth")`, then `POST https://gsa.apple.com/auth/verify/phone/securitycode` |

The sign-in is retried (up to 10 times) until Apple settles, so a wrong code can be re-entered.
**Status: implemented and reviewed, but not yet exercised on a device — untested.**

## 20.2 Hardening added: Apple's URL bag is now validated

All authenticated requests take their URL from Apple's own URL bag, which is a trust boundary. Every
one of the SRP proof, the 2FA code and the identity token is bound for an Apple host, so
`GrandSlam.url()` now **refuses any bag entry that is not an `apple.com` host**. This covers
`gsService`, `validateCode`, `trustedDeviceSecondaryAuth`, `secondaryAuth`, `midStartProvisioning`
and `midFinishProvisioning` in a single place. Verified: a bag entry pointing `gsService` at
`evil.example.com` is rejected with a clear message.

## 20.3 Free Apple ID limits — now verified, not remembered

Taken from SideStore's own FAQ (<https://docs.sidestore.io/docs/faq>):

- a free Apple Account can have **3 apps installed at a time, including SideStore itself**;
- there is a **10 App ID limit**;
- a paid ($99/year) account removes the 3-app limit and extends the expiry to a year.

## 20.4 README rewritten

`README.md` is now written for a general audience and covers:

- the **two-device flow** (Device A signs and AirDrops → Device B receives and installs) with a
  diagram and step-by-step instructions, plus the single-device option;
- **why using a main Apple ID is safe**: SRP-6a (the password is never transmitted, not even to
  Apple), the anisette server sees only a random identifier, nothing sensitive is written to disk, no
  third-party signing service, TLS verification never disabled, no telemetry, and the **complete list
  of hosts contacted**;
- 2FA support (§20.1);
- the verified free-account limits (§20.3);
- the honest **registration caveat**: a second device's UDID needs registering, and doing that without
  a computer is **unverified** on iOS 27;
- the licence reality (non-commercial parts).

## 20.5 Correction from device testing: an AirDropped IPA installs itself

Confirmed on-device: after accepting an AirDropped `.ipa`, iOS **installs it directly** — there is no
separate "Install" confirmation to tap. The README's two-device flow was corrected accordingly
(accepting the AirDrop is the only interaction). Opening the same file from the Files app may still
need a tap on the file.

## 20.6 Next action

Re-run the sign-in PoC with a 2FA account; the code should now prompt for the verification code:

```
cd ~/Documents/barisign
```
```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/third_party/sideinstaller/signin.py -o signin.py
```
```
python3 -u poc_login.py --login YOUR-APPLE-ID
```

---

# 21. Round 6 — anisette TLS failed in `--login` but passed in `--selftest`

Both `--login` attempts failed inside `connect_anisette` with, for **every** server including a
plain-HTTP one:

```
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
self signed certificate in certificate chain (_ssl.c:1028)
```

Two observations:

- The earlier empty-store signature (`X509: NO_CERTIFICATE_OR_CRL_FOUND`) is **gone**, so a trust store
  *is* being loaded now; the chain is rejected for a different reason.
- The same code path passed check 5/6 in the round-3 selftest on the same device.

## 21.1 Fix: load every CA bundle available, not just the first one

`_select_ca_bundle()` previously picked **one** source (`certifi`, else the shipped file). If that one
source lacks a root a server uses — or is unexpectedly unavailable — every TLS connection fails.
`_select_ca_bundles()` now collects **all** sources and loads all of them (`certifi` **and** the
shipped `third_party/ca-bundle/cacert.pem`), on top of whatever the default context provides. This
removes "the chosen bundle is missing a root" as a failure mode. Verification stays ON.

## 21.2 New `--diag` mode (and diagnostics before every sign-in)

`python3 -u poc_login.py --diag` prints the default CA file/dir, **every bundle loaded and where it
came from**, how many roots a default context has, then performs real handshakes (verification ON,
same contexts the real code uses) to `gsa.apple.com`, `github.com`, `ani.sidestore.io` and
`ani.sidestore.zip`, reporting which context each used.

`--login` now also prints the CA diagnostic before contacting anything, so the next attempt carries
the evidence.

What the output decides:

- **All hosts OK but anisette still fails** → the anisette servers are unreachable or changed on that
  network; try `--server`.
- **`gsa.apple.com` and `github.com` also FAIL** while they load fine in Safari → something on the
  network is intercepting TLS. We never disable verification; we report it and stop.

## 21.3 Not yet explained (honest status)

We do **not** yet know why the anisette hosts failed in `--login` while the earlier `--selftest`
passed. It is one of: the selected bundle lacked a root, transient TLS interception, or the servers'
certificates changed. §21.1 removes the first; §21.2 identifies the rest on the next run.

Cheap experiment: note the **VPN state** (LocalDevVPN/StosVPN on or off) for each attempt and try both
ways. The VPN was on for one of the earlier failing rounds.

## 21.4 `--diag` now also proves where a connection actually goes

Added: each host's **resolved IP addresses** and, on success, the **issuer of the certificate it
actually presented**. This makes a redirect or TLS interception impossible to miss — if a host
resolves to `127.0.0.1` / `10.7.x.x`, or the issuer is not the real CA, we will see it immediately.

Working example from the Codespace:

```
gsa.apple.com        OK  TLSv1.3 Apple ctx   issuer=Apple Server Authentication CA  [17.179.252.2]
github.com           OK  TLSv1.3 general ctx issuer=Sectigo Public Server Authentication CA DV E36  [140.82.121.4]
ani.sidestore.io     OK  TLSv1.3 general ctx issuer=YE1  [89.117.146.208]
```

**Hypothesis to test (unverified):** StosVPN/LocalDevVPN install DNS settings so the device can reach
its own services. If, while the VPN is on, those DNS settings catch ordinary hostnames too, then every
`https://` request would land on a local service with a self-signed certificate — which is exactly the
`self signed certificate in certificate chain` seen in §21, for every host at once, including a
plain-HTTP one that redirected. Running `--diag` with the VPN **off** and then **on** tests this
directly.

Design note that follows from this: **our sign-and-AirDrop flow may not need the VPN at all.** The VPN
exists so SideStore can reach the device's own services for installing/refreshing later. If it breaks
TLS during signing, the Shortcut should ask for it only where it is genuinely required.

## 21.5 Round 6 second run: everything PASS except the password prompt

`--diag` on the iPad (VPN **off**) was healthy and proved no interception:

```
gsa.apple.com      OK TLSv1.3 Apple ctx   issuer=Apple Server Authentication CA  [17.32.194.2, 17.32.194.34]
github.com         OK TLSv1.3 general ctx issuer=Sectigo Public Server Authentication CA DV E36  [140.82.121.3]
ani.sidestore.io   OK TLSv1.3 general ctx issuer=YE1  [89.117.146.208]
ani.sidestore.zip  OK TLSv1.3 general ctx issuer=YE1  [212.56.43.84]
```

`--selftest` then **PASSed all six**, including anisette provisioning and the pinned Apple root. So the
CA-bundle fix from §21.1 worked and the earlier anisette TLS failures do not reproduce.

`--login` still got stuck at the password prompt (the user had to restart a-Shell).

**Cause:** our own `_read_password()` used `termios` to turn ECHO off. On a-Shell that succeeds, but the
terminal then **stops delivering keystrokes to Python**, so the read blocked forever. (Earlier,
`getpass` had hung for a different reason: it opens `/dev/tty`, which is not the terminal being typed
into.) This replaced one hang with another — our bug both times.

**Fix:**
- `termios` is gone entirely. On a TTY the PoC now uses a plain `input()`, which is what a-Shell
  supports.
- A `select()` guard (120 s) means the prompt can **never wedge the terminal again** — it prints and
  exits instead of forcing an app restart.
- If stdin is **not** a TTY the password is read from the pipe. That is exactly how the Shortcut will
  deliver it, so the intended architecture needs no terminal input at all.
- Consequence, stated plainly: on a TTY the password is now **visible** as you type (a-Shell cannot
  hide it). `clear` afterwards is recommended; the Shortcut path avoids this.

**Verified in the Codespace:** the pipe path returns the line unchanged; the TTY path was exercised
under a real PTY and returned the typed line without hanging.

## 21.6 Round 7 — SRP SUCCEEDED on the device; failure was our AES padding

The user's run reached the very end of the handshake:

```
... asking the anisette server for fresh headers
... contacting Apple: SRP init (no password leaves this device)
... Apple replied; computing the SRP proof locally
... sending the proof to Apple (still no password)
... Apple accepted the proof; verifying Apple's own proof
FAILED: Could not decrypt the sign-in response
        (H(S): not well-formed (invalid token): line 1, column 2644; ...)
```

So, confirmed on a real device: the anisette headers, the SRP `init`, our computed `M1`, Apple's
acceptance of it, and the `M2` server-proof check **all work**. Apple even pushed a **2FA code** to the
user's devices at that point. The password prompt works too now (§21.5).

**The bug was ours, in the same replacement as before.** Upstream's `aes_cbc_decrypt()` shelled out to
`openssl enc -d -aes-256-cbc`, and `openssl enc -d` **strips PKCS#7 padding by default**. Our
`cryptography` version returned the raw plaintext, so Apple's SPD plist arrived with its 8 trailing
padding bytes and plistlib failed on them — reported as `not well-formed (invalid token)` at the end of
the document. Identical failure for both key candidates, because both decrypt to a good document plus
the same trailing padding.

**Fix:** `aes_cbc_decrypt()` now strips PKCS#7 padding (validating it) to match `openssl enc -d`.

**Reproduced and verified in the Codespace:** encrypting a small plist the way Apple does, then
`SRPClient.decrypt_spd(blob)`, now returns the parsed dict; the pre-fix behaviour reproduces the
device's exact `not well-formed (invalid token)` error. Self-test 3/6 was also strengthened to send
realistic padded data, so this class of bug cannot come back silently.

## 21.7 Masked password entry (restored, without the earlier hang)

The user asked for hidden input again *without* the "stuck on Enter" problem. There is now a sizeable
body of evidence about what a-Shell does and does not allow, so the implementation follows it:

| Approach | Result on a-Shell |
|---|---|
| `getpass.getpass()` | hangs — opens `/dev/tty`, which is not the terminal being typed into |
| `termios` ECHO-off + `readline()` | hangs — keystrokes stop reaching Python in that mode |
| `input()` | works, but everything typed is visible |
| **cbreak + `os.read(fd, 1)` + `*`, bounded by `select`** | **used now** |

`_read_password()` is a three-step ladder:

1. **stdin is a pipe** → read the line. This is how the Shortcut will deliver the password, so the
   eventual product needs no terminal input at all.
2. **masked read**: `ICANON`/`ECHO` off, one byte at a time from the raw fd, `*` per keystroke,
   backspace handled, `Ctrl-C` respected. Every read is bounded by `select`, and the terminal
   attributes are restored in a `finally`.
3. **fallback to plain `input()`** if masking is unsupported. If no keystroke arrives for 20 s the
   masked reader gives up, restores the terminal and says so, so the user can simply retype visibly.

The ladder is what makes this safe: a failure can only downgrade to visible input, never wedge the
terminal or require restarting a-Shell.

**Verified in the Codespace under a real PTY:** `Password: *******` on screen and the correct secret
returned; backspace redraws correctly; the pipe path still returns the line unchanged.

## 21.8 Round 8 — the padding fix was never on the device

The user re-ran and got the **byte-identical** failure (`column 2644` for both key candidates), after
reporting that masking now works (`Password: ********`). Cause: `aes_cbc_decrypt()` lives in
**`signin.py`**, where the PKCS#7 fix was made — but the instruction for that round only re-downloaded
`poc_login.py`. A stale `signin.py` therefore reproduced the old bug exactly, which is why the error did
not change by even one character. **That was an instruction error on our side.**

Two guards added so a stale file can never masquerade as a mystery failure again:

- **Version markers**: `signin.py` carries `VERSION` and `poc_login.py` carries `POC_VERSION`; both are
  printed by every mode (`--diag`, `--selftest`, `--login`).
- **Integrity self-check**: at start-up the PoC performs a padded AES-CBC round-trip through
  `signin.aes_cbc_decrypt()` using a pad byte that is **not** XML whitespace (6). If padding is not
  stripped it prints `*** STALE ***  got 16 bytes, expected 10  -> re-download signin.py`, which turns
  exactly this failure mode into a one-line diagnosis.

Verified: the check prints `OK (PKCS#7 padding stripping present)` with the current file, and prints the
`*** STALE ***` warning when the fix is removed.

## 21.9 Round 9 — PADDING FIX CONFIRMED; reached the 2FA prompt; one terminal bug left

With the current `signin.py` on the device (`0.3.0-unpad`, integrity check **OK**), the sign-in path got
all the way through:

```
... Apple accepted the proof; verifying Apple's own proof
A message with a verification code has been sent to your other devices ...
Two-factor code:
```

So the PKCS#7 fix is confirmed on a real device and **Apple's 2FA push is reached**. (A control run with
the literal account name `YOUR-APPLE-ID` returned Apple's `-20101 "account information was entered
incorrectly"`, which is the expected answer for a fake account and further confirms the request reaches
Apple correctly.)

**Remaining bug:** the `Two-factor code:` prompt did not accept typing — invisible, and Return blocked.

**Cause:** the masked password read leaves a-Shell's terminal in cbreak/no-echo mode and it does **not**
accept the settings being put back. Any later plain `input()` is then unusable — it is exactly why the
`
`/`
` and echo handling of our own reader worked while `input()` did not. This is a-Shell behaviour,
not something we can fix by asking nicer.

**Fix: never depend on `input()` again.**
- `_read_line(prompt, mask)` now serves **every** prompt: `mask=True` for the password, `mask=False`
  (echoed) for the 2FA code. It reads bytes in cbreak mode, handles `
`, backspace and `Ctrl-C`, and is
  bounded by `select`.
- `_restore_terminal()` re-enables `ICANON|ECHO|ISIG` from the *current* attributes, then **verifies**
  the result and only warns if the terminal genuinely refuses (with a note that closing and reopening
  a-Shell fixes it — nothing is broken).
- `ask_code()` (used by both the trusted-device and SMS 2FA flows) is routed through the same reader.

**Verified in the Codespace under a real PTY:** one process, password prompt prints `*******` and
returns the secret, the next prompt echoes `123456` and returns it.

## 21.10 Round 10 — **MILESTONE: full Apple sign-in achieved on-device**

```
Password (not shown, not stored): ********
... Apple accepted the proof; verifying Apple's own proof
A message with a verification code has been sent to your other devices ...
Two-factor code: 995908
...
---- result --------------------------------------------------
LOGIN: PASS — signed in as Barış Mert
```

Confirmed on a real device (iPad15,7 / iOS 27 / a-Shell), end to end: the anisette identity, SRP-6a,
Apple's acceptance of our proof, our verification of Apple's proof, the trusted-device 2FA push, the
code entry, and a real session. The anisette failover also demonstrated itself — `ani.sidestore.io`
rejected the stored identity, `.app` provisioned successfully, and the PoC continued.

Two smaller things settled in the same round:

- **The 2FA code is deliberately NOT masked** (`mask=False`). A verification code is not a secret, and
  showing it makes a mistyped code obvious instead of a mystery. Only the password is masked. A
  redundant duplicate branch in the backspace handling was removed, and the intent is now written in a
  comment, so the difference cannot later look like a bug.
- `poc_login.py` → `0.7.1` (its `signin.py` dependency is unchanged at `0.3.0-unpad`).

**Verified in the Codespace under a real PTY:** password prompt shows `*******`, the 2FA prompt echoes
`998877`, and both return the correct value in one process.

**What this de-risks:** §13's risk item 2 (Apple sign-in on-device) is now **done and verified**. The
remaining unknown from that list is the OpenSSL 1.1.1i / CA-store question, which §21.6–21.8 also
resolved in practice.

## 21.11 Next action

```
python3 -u poc_login.py --diag
```
Then, if the handshakes look healthy:

```
python3 -u poc_login.py --login YOUR-APPLE-ID
```

---

# 22. PHASE A — can SideStore be installed on a SINGLE device from a-Shell?

Research only. No code written, no developer-portal work. Everything below is from source or from
device measurements recorded by the projects themselves; each claim is marked **verified** (read in a
source or measured on hardware) or **unverified**. Nothing here is confirmed by the user's own device
unless it says so.

**Primary sources, with dates**

| Source | What it is | Date |
|---|---|---|
| `FrizzleM/SideInstaller` → `NOTES.md` | build notes, device-measured findings | entries dated **2026-08-31**, **2026-09-30** |
| `FrizzleM/SideInstaller` → `ashell/README.md` | the a-Shell port, measured on an iPhone in a-Shell | **2026-08-27**, **2026-08-31** |
| `FrizzleM/SideInstaller` → `ashell/BOOTSTRAP-PROMPT.md` | the route decisions and milestone list | current |
| `jkcoxson/idevice` @ `7bd551c` (**MIT**) | protocol client used by StikDebug and SideInstaller | current |
| `nab138/isideload` (**MIT**) | install sequence (`sideload/install.rs`) | current |
| CPython docs, `ssl` | PSK support | "Added in version 3.13" |
| `docs.sidestore.io/docs/advanced/jit` | iOS 26/27 JIT support status | current |

---

## 22.1 Q1 — How do they install on the *same* device on iOS 27? Which transport, which ports?

**The transport is not classic lockdownd/usbmux over the VPN.** For iOS 27 targets that path is
**verified closed on hardware** (`NOTES.md`, 2026-09-30):

> *"Answered for iOS 27 (2026-09-30): neither answers. The VPN peer resets the first request and
> loopback is refused by iOS, so on iOS 27 `connect` no longer tries this fallback."*

The live route is **RPPairing → TLS-PSK → CDTunnel → in-process software TCP stack → RSD → services**,
stated verbatim in `NOTES.md`:

> *"Transport used: RPPairing pair-verify over the LocalDevVPN Wi-Fi loopback → TLS-PSK tunnel →
> in-process software TCP stack (`tunnel_tcp_stack`) → RSD handshake → services over RSD. Pairing-record
> type for our own connection: RPPairing (`rp_pairing_file.plist`), not a classic lockdown record."*

**Ports** (port map measured on an iPhone, `ashell/README.md`, 2026-08-31):

| Port | Behaviour (measured) | What it is |
|---|---|---|
| **49152** | holds an idle connection; **resets at exactly 11 bytes received** unless the magic matches; correct 11-byte header → **held**; wrong magic → closed in 19 ms; full `attemptPairVerify` → **423 bytes of RPPairing JSON** (`wireProtocolVersion` 26, min 8) | **RPPairing listener** |
| **62078** | accepts, then resets the moment the first request arrives (e.g. `GetValue(DevicePublicKey)`, the opening of `lockdownd_pair`) | lockdownd — **unusable on-device** |
| **8443** | speaks first: answers plaintext with a TLS fatal alert | unidentified |
| **61779** | holds and tolerates 64 bytes, no cutoff | unidentified |
| 49151 | — | `tunneld` default port, a **host-side** daemon (`tunnel_tcp_stack`) |
| 27015 | — | usbmuxd default port (**desktop**) |

Addressing note: the 11-byte cutoff was originally misread as a protocol rejection; it is
**RPPairing's header** — `b"RPPairing"` (9 bytes) + `u16be` length = **11**
(`idevice` `remote_pairing/socket.rs:69`, framing; `recv_plain` reads those 11 bytes first). This is
why the user's own measurement ("1 byte gets no reply") is consistent: the service is waiting for 11.

**Services over RSD** (service names, `idevice` — the `.shim.remote` suffix is the RemoteXPC naming):

- `com.apple.mobile.installation_proxy.shim.remote` (`services/installation_proxy.rs:450`)
- `com.apple.mobile.house_arrest.shim.remote` (`services/house_arrest.rs:114`)
- `com.apple.mobile.mobile_image_mounter.shim.remote` (`services/mobile_image_mounter.rs:775`)
- `com.apple.amfi.lockdown.shim.remote` (`services/amfi.rs:143`)
- plus `services/installcoordination_proxy.rs` — the iOS 17+ IXPC install path

**SideStore (minimuxer + em_proxy)** uses the *classic* usbmuxd/lockdown model, which is why SideStore
needs a **pairing file**; **StikDebug/idevice** use `tunnel_create_rppairing` and also take a pairing
file. **Verified** that both depend on a pairing record; both are **native** apps.

## 22.2 Q2 — What exactly is needed

**Pairing record.** Two different things share the name:

- **Classic lockdown record** (`idevice` `pairing_file.rs`): `SystemBUID`, `HostID`, `HostCertificate`,
  `HostPrivateKey`, `RootCertificate`, `RootPrivateKey`, `DeviceCertificate`, `UDID` (+ EscrowBag on
  some paths) — certificates are RSA, exchanged during `lockdownd_pair`.
- **RPPairing file** (`rp_pairing_file.plist`) — what iOS 27 devices actually produce and consume
  (SideInstaller's file carries **both halves** so other apps can use it).

**Classic pipeline (Route A — closed on iOS 27):** pair over lockdownd on 62078 → `StartSession` →
`StartService` per service → AFC upload → installation_proxy install → house_arrest to seed the pairing
file. All messages are length-prefixed plists: `struct.pack(">I", len)` + `plistlib.dumps`.

**Upload target (verified, `isideload` `sideload/install.rs:34,88`):** `PublicStaging/<name>.ipa` over AFC.

**Install request (verified, `installation_proxy.rs:174` + `isideload` `install.rs:53-62`):**

```python
{"Command": "Install",
 "ClientOptions": {"PackageType": "Developer"},
 "PackagePath": "PublicStaging/<name>.ipa"}
```

with progress/completion plists streamed back (`watch_completion`). iOS 17+ also exposes
`installcoordination_proxy`.

**TLS to lockdownd (Route A):** a session where the host presents `HostCertificate`/`HostPrivateKey`
and the device presents `DeviceCertificate` — ordinary certificate-based TLS, so Python's `ssl` could
do it with `load_cert_chain`. **Irrelevant in practice: Route A never gets that far on iOS 27.**

## 22.3 Q3 — Can a pairing record be created ON the device on iOS 27, and can Python do the TLS?

**A classic lockdown record cannot be minted on-device on iOS 27 — verified, dated 2026-09-30.** Three
independent statements in `NOTES.md`:

1. `EnableWifiDebugging`'s own comment: *"lockdownd answers over USB only until `EnableWifiDebugging` is
   set, and setting it needs a session, which needs a record"* — a deadlock.
2. Measured: the VPN peer **resets the first request** and **loopback is refused by iOS**.
3. Side by Side measured the same thing against another device: *"lockdownd accepts the TCP connection
   and resets it (`ECONNRESET`, error 54) the moment the first request arrives — `GetValue(DevicePublicKey)`
   … so no Trust prompt ever appears."*

**An RPPairing record for a device CAN be created on iOS 27 — but only in a specific, native-only shape.**
The device-initiated flow, verified end-to-end by a reporter on **two iOS 27 iPhones**:

> *"(pairable host → RPPairing tunnel → RSD → install) end to end on two iOS 27 iPhones."*
>
> *"Pairing this iPhone means typing a code SideInstaller shows into a prompt in Settings, with
> SideInstaller behind it … the target taps a `_remotepairing-pairable-host._tcp` host under
> Settings › Privacy & Security › Developer Mode and types the PIN that host shows."*

**The five checkpoints** (the milestone list in `BOOTSTRAP-PROMPT.md`; `ashell/README.md` calls this
"checkpoint 1 of 5"):

| # | Checkpoint | Needed for |
|---|---|---|
| 1 | probes run, route chosen | prerequisite |
| 2 | **pair with this device from this device** and persist the record | **the pairing record** |
| 3 | reach one service and read something harmless (device info → **UDID**) | **UDID only** (and install) |
| 4 | install an **already signed** .ipa — *"this is the checkpoint that matters"* | install |
| 5 | sign with your own Apple ID, then install | signing |

**Can Python's `ssl` (OpenSSL 1.1.1i) do the TLS steps?**

- Route A (certificate-based): yes in principle — but the route is closed.
- Route B (RPPairing) uses **TLS 1.2 PSK** (`PSK-AES256-CBC-SHA384`; SideInstaller hand-rolls it in
  `tls_psk.rs`). CPython gained `ssl.SSLContext.set_psk_client_callback` **in 3.13** (CPython docs:
  *"Added in version 3.13"*), and a-Shell has **Python 3.13.1** — so this may be free rather than
  hand-rolled. **Unverified on the device**; one line settles it.
- **What is definitely missing, and is the real blocker:** the **software TCP stack** over the CDTunnel.
  Upstream that is the `jktcp` crate, called *"the largest single risk in this route and every byte of
  the .ipa crosses it twice"*, and it is **not vendored** in SideInstaller. Plus the **RemoteXPC/RSD**
  codec. SideInstaller's own a-Shell port has a RemoteXPC codec in `siboot.py` but notes it was
  *"written one layer too early"*.

**Why this is out of reach for a Shortcut-driven a-Shell script even if the protocol were written:**
pairing requires the app to **stay running in the background while the user leaves it** — SideInstaller
uses a **Live Activity** plus *"pairing's keep-alive audio keeps the app running"* while the user is in
Settings. A Python script launched by a Shortcut in a-Shell cannot hold a background keep-alive or post
a Live Activity, and iOS suspends it as soon as the user switches to Settings. **This is an
architectural blocker, not a coding-effort one.**

## 22.4 Q4 — Would lockdownd at `10.7.0.1:62078` answer `QueryType`/`GetValue` unpaired?

**No — measured, 2026-09-30.** `NOTES.md`: *"Answered for iOS 27: neither answers. The VPN peer resets
the first request and loopback is refused by iOS."* Side by Side adds: `ECONNRESET` the instant
`GetValue(DevicePublicKey)` arrives. The user's own "1 byte → no reply" on 49152 is the RPPairing header
waiting (11 bytes), a different port.

Their own earlier probe (`ashell/lockdown_probe.py`) was exactly this hypothesis; it was settled
negative. **Do not spend another round on it.**

## 22.5 Q5 — Every other currently working way to get a signed IPA installed with no computer

| Route | Second device? | Server? | Turkey | iOS 27 as of Oct 2026 | Risks / terms |
|---|---|---|---|---|---|
| **A. Single-device, pure Python in a-Shell** | No | No | n/a | **Not feasible** (§22.6) | — |
| **B. Sign on device A, AirDrop to device B** | **Yes** | No | Works | **Works** (user-measured: AirDrop installs; unsigned won't launch) | Receiver's UDID must be registered; acquiring a *second* device's UDID without a computer is the gap |
| **C. A native on-device installer (SideInstaller v1.0.0)** | No, per its README | No | Works | **Claims iOS 27+, and its two-device mode is verified on two iPhones** | **Native app, not Shortcut-drivable**; custom non-commercial licence; its own notes show the iPhone 13 path still falling back to an **imported pairing file** |
| **D. `itms-services` / OTA** | No | **Yes** (HTTPS + trusted cert) | Works | **Dead end, device-measured** | *"requires an Ad Hoc or Enterprise distribution profile — free-account development profiles are refused"* (the "couldn't verify integrity" error). Confirms our §5.1/§5.2 |
| **E. Prebuilt pairing file from a site/app** | No | Third party | Works | Works (community standard) | You hand a pairing record to a third party; revocation/ransom risk; against the spirit of "never leaves the device" |
| **F. StikDebug** | No | No | Works | **No longer on the App Store** (per its repo notice) | Still needs a pairing file for JIT |
| **G. `itms-services` from your own private HTTPS** | No | **Yes**, user-owned | Works | Works | The *signed* IPA carries your UDID + profile; a bearer URL leaks it |

Older-than-2026 sources were excluded. Nothing newer than Oct 2026 was found that changes this.

## 22.6 Q6 — Verdict

**Is single-device install feasible in pure Python in a-Shell?**

## **NO — high confidence.**

| Blocker | Status |
|---|---|
| Mint a pairing record on-device (Route A, classic lockdown) | **Impossible on iOS 27** (device-measured, dated) |
| RPPairing self-pairing (Route B) | **Exists on iOS 27, but is native-only**: requires a Bonjour pairable-host, a PIN typed into Settings, and the app staying alive in the background (Live Activity + keep-alive audio) |
| Software TCP stack over CDTunnel (`jktcp`) | **Not available** in pure Python; upstream calls it the largest single risk |
| RemoteXPC / RSD codec | Not available; SideInstaller's own attempt was "one layer too early" |
| Mach-O + CMS signer | Still to be written (§13 item 1) |
| TLS-PSK | **Probably free** via Python 3.13's `set_psk_client_callback` (unverified on-device) |

**Effort if it were attempted:** weeks of work and a very large token budget, to re-implement a Rust TCP
stack and RemoteXPC in Python, with the decisive step (self-pairing) unreachable from a Shortcut-launched
background-suspended script. **Not recommended.**

**Ranked recommendation**

1. **B — two-device AirDrop.** Already proven on the user's devices. The one open item is obtaining the
   **receiver's UDID** with no computer; iOS 27's device-initiated pairing
   (`_remotepairing-pairable-host._tcp` + PIN) is the verified mechanism, but it needs the same native
   tunnel stack for us to read the UDID.
2. **C — point users at a native on-device installer (SideInstaller) as a documented alternative**, and
   keep BariSign's Shortcut for what it does well: choosing the variant, signing with the user's Apple ID,
   and handing over a correctly signed IPA. (Its licence is non-commercial and its code stays in
   `third_party/` — we link to it, we do not ship it.)
3. **A — single-device pure-Python install: not recommended.** Documented above rather than attempted.

**Open question this phase did NOT resolve** (it needs the developer portal, which is out of scope for
Phase A): whether a free-account provisioning profile can cover **two devices at once**, which decides
how much of route B is left to build.

## 22.7 Cheap tests for later (no code written, one line each)

Run in a-Shell, in order, only when authorised:

1. TLS-PSK availability — decides whether Route B's `tls_psk.rs` would ever have to be hand-rolled:
   `python3 -c "import ssl; print('psk:', hasattr(ssl.SSLContext,'set_psk_client_callback'), ssl.OPENSSL_VERSION)"`
2. Unpaired lockdownd over the VPN peer (expected to fail per §22.4 — run once to close it on *this* device):
   `python3 -c "import socket,struct,plistlib; msg=plistlib.dumps({'Request':'QueryType','Label':'b','ClientVersion':'1'}); s=socket.create_connection(('10.7.0.1',62078),timeout=5); s.sendall(struct.pack('>I',len(msg))+msg); print(s.recv(4096)[:120])"`

**STOP — Phase A ends here.** Waiting for the user's go before any implementation.

---

# 23. UDID acquisition — source review, and the QR direction

Follow-up research requested after Phase A. Sources read: `SideStore/SideStore` (Swift),
`FrizzleM/SideInstaller` (`ish/sideinstaller.py`), `nab138/iloader` (Rust/Tauri), plus Apple docs.

## 23.1 How each project actually gets a UDID

| Project | Mechanism | Needs |
|---|---|---|
| **iloader** (desktop) | usbmuxd over USB: `get_pair_record(&device.udid)` (`src-tauri/src/pairing.rs:69`), rppairing file cached per UDID (`pairing.rs:326`) | a **computer** |
| **SideStore** (on-device) | reads *its own* UDID from the pairing-backed lockdown session (`fetchUDID()`, `Views/Settings/Auth/AccountVerificationRow.swift:177`) | a **pairing record** |
| **SideStore** (second device) | its Devices/Profiles screens let you **type or paste another device's UDID** and register it: `DevicesListView.swift:191` ("Device UDID"), `ProfilePortalDetailView.swift:206` ("Add Custom Device ID / UDID") | the UDID as text |
| **SideInstaller** (legacy, iSH) | Apple's **`Profile Service` configuration profile** with `DeviceAttributes: ["UDID", …]` posting back to a local HTTP server (`udid_mobileconfig()`, `Handler.do_POST`, regex on the CMS-signed body) | a server + a profile install on the target |
| **SideInstaller** (iOS 27, native) | the Settings-initiated **pairable host** flow → RPPairing → RSD → lockdown `GetValue` | the full native tunnel stack |

**Key consequence:** SideStore already implements "register a second device by UDID". Everything hinges
only on getting that UDID string off Device B.

## 23.2 Can a Shortcut on Device B produce its own UDID? **No.**

Apple's *Get Device Details* action returns OS, device type, system version, system build, device model,
device hostname and device name — **no UDID** (Apple Shortcuts User Guide; Matthew Cassinelli's action
reference). There is no Shortcuts action, and no JavaScript-for-Shortcuts API, that exposes it. So
**Device B cannot display a QR containing its own UDID** — that direction is impossible, and it is worth
knowing before building it.

## 23.3 The QR works in the other direction

Device B must be *asked* for its UDID. The only route that needs **no app on Device B** and is
implementable in a-Shell is Apple's documented **Profile Service** profile:

```
Device A (a-Shell)                     Device B (nothing installed)
 shows a QR  ────────────────────────►  Camera → Safari → download profile
 serves /udid.mobileconfig              install it (Settings → VPN & Device Management)
 ◄──────────────────────────────────── POST /udid  (CMS-signed plist containing the UDID)
```

Which is exactly the payload `udid_mobileconfig()` in SideInstaller's iSH script uses
(`PayloadType: "Profile Service"`, `DeviceAttributes: ["UDID", …]`). **We wrote our own implementation**
— the payload format is Apple's, documented in the archived *Over-the-Air Profile Delivery* guide — and
our code is MIT; no SideInstaller code was copied.

**Implemented:** `poc/udid_share.py` (a-Shell, stdlib only; QR via `segno`/`qrcode` if present, else it
prints the URL). It serves the profile, captures the POST, prints the UDID, and never writes it to a
file. **UNTESTED on a device.** *Verified here in simulation only:* the three routes respond, and a
simulated CMS-wrapped POST yields the UDID and gets a minimal `Configuration` profile back.

**The one decisive unknown:** whether iOS 27 still honours the Profile Service `DeviceAttributes`. The
web evidence is inconclusive — Apple's documentation of it is archived (2018) and public "get my UDID"
services still operate in 2026, which suggests it survives, but that is **unverified**. The script is
built to answer exactly this: if Device B installs the profile and no UDID arrives, the route is dead on
iOS 27 and §22.3's Settings-based pairing remains.

## 23.4 Script execution on Device B

Checked and rejected: Shortcuts' only scripting surface is *Run JavaScript on Web Page* (needs a Safari
page and cannot read the UDID), and Shortcuts has no API that exposes device identifiers. So "Shortcut
runs a script on Device B that produces the UDID" is not a route. Safari + one temporary profile is the
closest thing to "no app", and `poc/udid_share.py` implements it.

## 23.5 Answers about the profile (user questions)

- **Whose profile is it?** Ours, generated in memory by the script at that moment. `PayloadIdentifier`
  `io.barisign.udid`, display name "BariSign - read this device's UDID", shown under
  Settings → General → VPN & Device Management. It is **unsigned**, so iOS shows a "Not Signed" warning
  before installation. It is deleted by the user afterwards and carries no secret. Nothing is fetched
  from the internet — not even the profile itself.
- **Does it use the internet?** No. The callback URL is the signer's **LAN address**
  (`http://<ip>:8787/udid`), so the device information travels over the local Wi-Fi only. No Apple ID,
  no third-party server, no external host. Both devices must be on the same Wi-Fi.
- **Is it suitable for everyone?** Not yet, and honestly: it is a *probe*, not a polished flow.
  Friction: the profile warning text + an "Allow sending device information" prompt + deleting the
  profile afterwards; both devices on one Wi-Fi; locked-down (school/work) devices may refuse profile
  installation; and the decisive question — whether **iOS 27 still sends the UDID at all** — is
  **unverified**. If Apple removed it, this route is dead for everybody and §22.3's Settings pairing is
  the only no-app path left.
- **Is it easy? Does it need a restart?** No restart of anything — no device reboot, no a-Shell
  restart. It is roughly five taps on Device B (scan → tap link → Install → Install → Allow, then
  delete). The real constraint is on **Device A**: a-Shell must stay in the **foreground** (and the
  screen awake) while Device B scans, because iOS suspends a backgrounded app and the little server
  stops answering. A's screen auto-lock should be set to Never for the duration.
- One more unknown to expect: whether iOS 27 accepts a profile fetched over plain `http://`. The
  classic examples are `http://`, and if Apple now insists on HTTPS for profile delivery this needs a
  trusted certificate, which we already know is the hard part.

## 23.6 Next action (waiting for go)
cd ~/Documents/barisign
```
```
curl -sL https://raw.githubusercontent.com/barismd/barisign/main/poc/udid_share.py -o udid_share.py
```
```
python3 -m pip install segno
```
```
python3 -u udid_share.py
```
Then scan with Device B's Camera. Report: did the profile install, and did the UDID arrive?

---

# 24. udid_share.py hardened, and the four answers

## 24.1 What was changed in `poc/udid_share.py`

| Requirement | How |
|---|---|
| one-time secret path | every URL carries `secrets.token_urlsafe(12)`, regenerated each run; the path dies with the process |
| bind one interface only | binds **this device's Wi-Fi/hotspot address**, never `0.0.0.0`; refuses to start if only loopback is found |
| serve only what is needed | exactly two routes: the profile (GET) and the POST endpoint. Root and everything else are **404** (the QR now points straight at the profile, so there is no index page at all) |
| 5-minute timeout | `WINDOW_SECONDS = 300`, then it gives up and says so |
| close after first valid POST | the server is shut down as soon as the first valid POST is handled |
| never log the UDID | no access log, nothing written to disk, body capped at 256 KB, and the UDID is **not printed** until the confirmation step passes |
| confirmation | shows `PRODUCT` (model) and `VERSION` (iOS) and asks *"Is that the device in front of you? [y/N]"*; on anything but yes the UDID is discarded |
| reject a second POST | once claimed, further POSTs get **409** and cannot overwrite the first |
| show the whole profile | the full profile XML is printed before the server starts |

*Verified in simulation:* wrong path 404, root 404, token profile 200 (`Profile Service`), 1st POST 200,
2nd POST 409, and the captured fields are correct. **Still untested on hardware.**

## 24.2 The profile: what is in it, whether it stays, how it is removed

The XML the script prints is the **entire** profile: one payload of type `Profile Service`, with
`DeviceAttributes` = `["UDID", "PRODUCT", "VERSION"]`. No certificate, no VPN, no proxy, no Wi-Fi, no
payload of any other kind. It is **unsigned**, so iOS labels it "Not Signed".

**It stays on Device B.** The profile is installed like any configuration profile and nothing removes it
automatically — the POST does not uninstall it, and the script cannot remove it remotely. iOS then
fetches the *returned* profile as well (an empty `Configuration` named "BariSign - done (delete me)"),
so Device B should expect to see one or two "BariSign" entries. Removal on Device B:
**Settings → General → VPN & Device Management → tap the entry → Remove Profile → passcode.** Deleting it
removes nothing else; it configured nothing.

## 24.3 Plain HTTP: the real risk, and whether HTTPS is worth it

**The risk, stated plainly:** the POST (and the profile download) travels in cleartext over the Wi-Fi.
Anyone who can observe that network — an open/hotel Wi-Fi, or anyone who knows the Wi-Fi password on a
shared network — can read the UDID, the model and the iOS version, and could in principle answer *as* the
server on the target's next attempt. The mitigations here are: a one-time random path, a ≤5-minute
window, one-interface binding, and the model/version confirmation before the UDID is accepted. What the
risk is **not**: the UDID is not a credential. It is an identifier that developers paste into portals
every day; leaking it is a privacy issue, not an account compromise. No Apple ID, password, token or
pairing record is involved in this exchange.

**Is HTTPS with a locally generated certificate possible?** Technically yes: iOS accepts a chain rooted
in a CA that the user installs **and** switches on under Settings → General → About → Certificate Trust
Settings (a self-signed leaf alone is refused — that is a Phase 0 finding). **But for this payload it is
disproportionate:** it would mean asking the user to install *and trust a root CA* — two more scary
steps, and a much bigger security ask than the UDID itself — to protect a semi-public identifier. My
recommendation is to keep HTTP, keep the window short, and keep it on your own Wi-Fi.

One caveat either way, and it is **unverified**: whether iOS 27 still accepts a profile delivered over
`http://` at all. The classic implementations used `http://`, and ATS does not govern profile delivery,
but if iOS 27 refuses it we would be forced into the CA dance regardless of preference.

## 24.4 Exact test steps

**On Device A (iPad, a-Shell)**
1. Settings → Display & Brightness → Auto-Lock → **Never** (put it back afterwards).
2. In a-Shell:
   `cd ~/Documents/barisign`
   `curl -sL https://raw.githubusercontent.com/barismd/barisign/main/poc/udid_share.py -o udid_share.py`
   `python3 -m pip install segno`
   `python3 -u udid_share.py`
3. Leave a-Shell **in the foreground** and the screen awake. Read the printed profile XML once.

**On Device B (iPhone)** — about **7 taps plus the passcode**:
1. Camera → point at the QR → tap the banner (1)
2. Safari: "…trying to download a configuration profile" → **Allow** (2)
3. Settings → the "Profile Downloaded" banner (or General → VPN & Device Management → the profile) (3)
4. **Install** (4) → **Install** again on the confirm sheet (5)
5. Device passcode (6)
6. **Allow**, to send the device information (7)

**What success looks like:** A prints `Server closed after N s.`, then the model and iOS version, then
`Is that the device in front of you? [y/N]`. Answer `y` and it prints the UDID once. Device B then shows
the "BariSign" profile(s) under VPN & Device Management.

**What each failure looks like:**
- A: `RESULT: no UDID arrived within 300 seconds` — and you tell me which of the two it was: the profile
  never installed, or it installed and nothing was sent (the second means **iOS 27 dropped the Profile
  Service UDID and this route is dead**).
- B: Safari refuses or errors on the `http://` address → tell me the exact wording (that would mean the
  HTTPS requirement).
- A: no QR appears → `segno` did not install; the URL is printed instead, type it on Device B.
- A: `Server closed` far too early → the screen locked or a-Shell went to the background.
- A: you answer `n` → the UDID is discarded (proves the confirmation gate works).

**Report back:** the whole a-Shell screen, what Device B showed at each tap, whether the "Allow sending
device information" prompt appeared, and whether the profile landed in VPN & Device Management.

---

# 25. Profile lifetime, and the network checks in the Shortcut

## 25.1 Profile expiry — verified against Apple's own documentation

Two different keys exist, and only one of them deletes anything:

| Key | Apple's own words | What it actually does |
|---|---|---|
| `PayloadExpirationDate` | *"The date when a profile is no longer valid and the system **presents an update button to the user**"* (developer.apple.com/documentation/devicemanagement/toplevel); and the Configuration Profile Reference adds: *"This key is only used if the profile is delivered via **over-the-air** profile delivery"* | marks the profile expired and offers an *Update* button. **It does not remove anything**, and probably does nothing for a manually installed profile |
| `DurationUntilRemoval` | documented in Apple's archived **Over-the-Air Profile Delivery** guide (and referenced alongside `RemovalDate`) | the device **removes the profile itself** after this many seconds. This is the key that actually cleans up |

So: **we add both.** `DurationUntilRemoval = 600` (10 minutes) is the real cleanup; `PayloadExpirationDate`
is the same 10-minute mark, as a hint for the UI. Both are **unverified on iOS 27** — Apple ignores
unknown or inapplicable keys silently, so there is no downside to including them, but the manual
"delete the profile" instruction **still stands** and is what the test steps say. This is a belt-and-braces
addition, not a claim that the profile self-destructs.

Verified in the Codespace: the profile with both keys serialises to XML and parses back correctly.

## 25.2 Network checks belong in the Shortcut, not in a-Shell

**A-Shell cannot read the Wi-Fi name** — iOS does not expose the SSID to a generic process, and that is a
platform restriction, not something we can code around. Two Shortcuts actions cover the checks instead,
both confirmed to exist:

| Action | What it returns | Used for |
|---|---|---|
| **Get Network Details** (iOS 17+) | Network Name (SSID), BSSID, Wi-Fi Standard, channel, RSSI, noise, Rx/Tx rates | confirm the SSID is the user's **home/hotspot** network, and warn on anything else |
| **Get Current IP Address** | the local (LAN) address | confirm it is a private range and show the user exactly which network A is on |

The script already does the same private-range check on **its own** bound address and refuses to run on a
non-private address, but the Shortcut is where the *user-facing* question belongs:

1. *"Which network are you on?"* — read **SSID** and compare to the user's saved home network name;
   if it does not match, show a warning and let the user back out before anything is installed.
2. Show **A's local IP address** so the user can see it is a `192.168.` / `10.` / `172.16–31.` address.
3. Ask the readiness question: *"Is the other device on this same Wi-Fi?"* — and note that the real proof
   is simply whether Device B can open the address, so if the page does not load, the devices are not on
   the same network.
4. Remind the user to accept a-Shell's **Local Network** permission when iOS asks.

One caution worth writing into the guide: comparing **BSSIDs between the two devices is unreliable**
(mesh networks hand out different access points), so use the SSID plus reachability, not BSSID equality.

These four steps are recorded as requirements for `SHORTCUT_BUILD_GUIDE.md` in Phase 1.

---

# 26. `--https`: so only the signing device can read the UDID

## 26.1 What is and is not possible

The target runs **none of our code**, so we cannot give it an app-level key or an app-level cipher.
iOS will only encrypt this exchange with **TLS**, which leaves exactly two options:

| | Who can read the UDID in transit | Extra steps on the target |
|---|---|---|
| plain HTTP (default) | anyone who can observe the Wi-Fi | none |
| **HTTPS (`--https`)** | **only this device** | install a temporary CA **and** switch on full trust for it |

**Mutual authentication is not possible.** An iOS web request presents no client certificate, so the
target cannot prove which device it is. The `PRODUCT` + `VERSION` confirmation step is therefore not
redundant even under HTTPS — it is the only thing that ties the POST to the device in front of the user.

## 26.2 How `--https` works

1. A throwaway **CA** and a **leaf** for this LAN address are generated in memory
   (`cryptography`, RSA-2048). The leaf carries `SubjectAltName = IP:<lan-ip>`, and both carry
   `SubjectKeyIdentifier` / `AuthorityKeyIdentifier`, `KeyUsage` and `serverAuth` EKU — without those,
   OpenSSL refuses the handshake (`Missing Authority Key Identifier`, found while testing).
2. The **CA is served over plain HTTP** (it is public information, and it must be reachable *before*
   trust exists — a chicken-and-egg), then the **profile and the POST go over HTTPS**.
3. The target installs the CA and switches on **Settings → General → About → Certificate Trust
   Settings**. A self-signed leaf alone is refused by iOS, which is why this is a CA + leaf.
4. The private key is written only to a `0700` temporary directory under `~/Documents/barisign`, and
   that directory is **deleted the moment the run ends**. Nothing is committed and nothing is kept.

*Verified in the Codespace:* the CA profile is a `com.apple.security.root` payload; the profile serves
over HTTPS with the generated leaf; a POST over HTTPS is captured; and a client that does **not** trust
the CA is **refused** — so this is real verification, not verification switched off.

## 26.3 Will the profile really delete itself after 10 minutes?

**Unverified, and you should plan for it not to.** Two keys are in the profile now:

- `PayloadExpirationDate` — Apple documents this as marking the profile expired and *offering an update
  button*; the Configuration Profile Reference says it only applies to over-the-air delivery.
  **It does not delete anything.**
- `DurationUntilRemoval = 600` — from Apple's archived Over-the-Air Profile Delivery guide; this is the
  key that is supposed to make the device **remove the profile itself** after N seconds.

Whether iOS 27 honours `DurationUntilRemoval` for a manually installed profile is **unverified**. It is
harmless if ignored, but the manual deletion instruction is the one to rely on. The test settles it:
**after the run, wait ~11 minutes and look at Settings → General → VPN & Device Management.** If the
BariSign entry is gone, the key works; if it is still there, delete it by hand.

## 26.4 Yes — AirDrop the profile instead of using a QR

**The QR existed for exactly one reason: to hand Device B a URL.** A `.mobileconfig` can be AirDropped
like any other file, and the callback address is *inside* the profile, so Device B never needs a URL at
all. That deletes the QR, the Safari step, and — with `--https` — the entire plain-HTTP CA listener,
which also removes the unresolved "does iOS 27 accept `http://` for profile delivery?" question.

**What AirDrop does not remove:** the listener. The profile has to POST its attributes somewhere, so this
script must still be running and reachable from Device B when the profile is installed. It is
"no URL needed", not "no server needed".

| | QR / URL | **AirDrop (`--airdrop`)** |
|---|---|---|
| Taps on Device B | scan, Allow, Settings, Install, Install, passcode, Allow | accept, Settings, Install, Install, passcode, Allow |
| URL or typing needed | yes | no |
| Profile fetched over HTTP/HTTPS | yes | no (file transfer) |
| Plain-HTTP CA listener (`--https`) | required (chicken-and-egg) | **not needed** |
| Depends on `http://` profile delivery | yes (unverified) | **no** |
| Requires AirDrop to accept the file | no | yes: *Contacts Only* (same Apple ID/contacts) or *Everyone for 10 minutes* |

Implementation: `--airdrop` writes the profile next to the script and prints where it is; found in the
Files app at **On My iPhone/iPad → a-Shell → barisign**. With `--https --airdrop` it writes
`BariSign-CA.mobileconfig` **and** `BariSign-UDID.mobileconfig`, to be AirDropped in that order (CA first,
then trust it, then the UDID profile).

**Unverified:** that iOS 27 installs a `.mobileconfig` received by AirDrop directly (it may land in Files
first, where tapping it hands it to Settings). Either way it installs; the test will show the exact flow.
The profile files are gitignored — they contain only the LAN address and the one-time token.

## 26.5 Variant list extended with AltStore (verified)

Requested by the user: offer AltStore next to the SideStore variants. Checked against the GitHub API on
2026-10-14 rather than from memory:

| Variant | Repo | Channel | Asset | Verified |
|---|---|---|---|---|
| SideStore stable | `SideStore/SideStore` | newest clean release (`0.6.4`) | `SideStore.ipa` | yes |
| SideStore nightly | `SideStore/SideStore` | tag `nightly` (rolling) | `SideStore.ipa` | yes |
| LiveContainer + SideStore stable | `LiveContainer/LiveContainer` | newest clean release (`3.8.0`) | `LiveContainer+SideStore.ipa` | yes |
| LiveContainer + SideStore nightly | `LiveContainer/LiveContainer` | tag `nightly` | `LiveContainer+SideStore.ipa` | yes |
| **AltStore Classic stable** | `altstoreio/AltStore` | newest clean release (`v2.3`) | `AltStore_Classic_Notarized_2_3.ipa` | yes |

Findings that matter for the resolver:

1. **AltStore has exactly one release.** `v2.3`, published 2026-09-30, asset `AltStore_Classic_Notarized_2_3.ipa`
   (40,616,419 bytes, asset updated 2026-09-30). Older tags exist (`beta4`, `beta3.1`, `v1.6.3`, …) but
   **carry no release and no assets** — the API returns 404 — so there is **no installable AltStore
   nightly or beta IPA**. Hence five variants, not six.
2. **AltStore's asset name embeds the version.** The resolver must match `.ipa` by **pattern**, never by
   a fixed filename. This is the same class of trap as SideStore's `releases/latest` returning
   `0.7.0-alpha`, which is why `variants.json` says "resolve at runtime".
3. **AltStore v2.3 claims computer-free sideloading.** Its own release notes: *"Remote AltServer —
   Supports sideloading apps without a computer. Install, refresh, and (de-)activate apps from anywhere
   with a WiFi connection … Thanks @jkcoxson and the entire SideStore team for making this possible!"*
   It also raises the **minimum to iOS 17.4**. The exact mechanism is **unverified by us** — that is a
   SideInstaller-style claim we have not read the code for. It does mean AltStore is no longer
   automatically disqualified by the "needs a computer" rule the way it used to be.
4. **AltStore PAL is out of scope:** it is an EU-only marketplace delivered under the DMA, not an
   installable IPA — and irrelevant for a user in Turkey.
5. AltStore is **AGPL-3.0**, the same as SideStore and LiveContainer: we consume its released IPA, we do
   not copy its code.

`variants.json` at the repository root is now the machine-readable list (5 variants, the resolution
rules, the alpha-tag trap, the asset-pattern rule, and the warning strings). The README variant table
was updated to match.

## 26.6 Recommended test order

1. **First, plain**: `python3 -u udid_share.py` — this answers the only question that matters
   (does iOS 27 still send the UDID at all). Keep this run simple; do not mix it with the CA steps.
2. **Then, if it worked, with HTTPS**: `python3 -u udid_share.py --https` — same test, but the CA must
   be installed and trusted on the target first, so allow more taps.

---

# 27. Device report: profile refused — diagnosis, and the a-Shell share sheet

Device B answered **"Profil yüklenemedi, bilinmeyen bir hata oluştu"** (profile could not be installed,
an unknown error occurred) for the AirDropped `.mobileconfig`. Two things came out of it.

## 27.1 The share sheet does NOT require leaving a-Shell

The user asked why the Files app was needed at all. It is not: a-Shell ships an **`open` command that
presents the iOS share sheet for a file** (verified — `Resources/bin/open` exists in `holzschu/a-shell`,
and the community guide documents it as *"Use `open <file>` to share a file with other apps"*, alongside
`view`, `play` and `internalbrowser`). So:

```
open BariSign-UDID.mobileconfig
```

opens the share sheet in place, and AirDrop is two taps from there. `poc/udid_share.py --airdrop` now
invokes it itself through `os.system` (a-Shell runs commands in-process, and `os.system` reaches them),
printing the same command as a fallback. The Files-app detour is gone from the test steps.

Also from Apple's own documentation: on **iOS 26.2 and later**, AirDropping to a device whose owner is
not in your contacts requires an **AirDrop code** — relevant for the two-different-Apple-IDs case.

## 27.2 The profile was refused — isolating the cause

The likely culprit is ours: the first version added **`DurationUntilRemoval`** and
**`PayloadExpirationDate`**. Apple's reference says `PayloadExpirationDate` *"is only used if the profile
is delivered via over-the-air profile delivery"*, and `DurationUntilRemoval` comes from the archived OTA
enrollment flow — neither belongs in an initial, manually installed profile as confidently as we assumed.
A profile iOS rejects is worse than a leftover one, so **both keys are now OFF by default**
(`--lifetime` brings them back).

Because a refusal is a dead end for diagnosis, `--diag-profiles` now writes three throwaway profiles that
pin the layer:

| File | What it proves if it fails |
|---|---|
| `barsign-test1-empty.mobileconfig` (installs nothing) | unsigned profiles cannot be installed on the device at all, or the delivery method is the problem |
| `barsign-test2-profservice.mobileconfig` (only Apple's documented keys) | **iOS 27 rejects the `Profile Service` payload** → the whole no-app route is dead |
| `barsign-test3-profservice-lifetime.mobileconfig` (documented keys + the two lifetime keys) | the two lifetime keys are what iOS refuses — the most likely single answer |

They carry an unroutable placeholder callback (`192.0.2.1`), so they are installation tests only. The
user installs them in order and reports which fail; then we either fix the profile or retire the route and
move to the Settings-based pairing plan in §22.3.

**Not yet known:** which of the three actually fails. Nothing in this section is confirmed until the user
reports back.

---

# 28. Why `open` produced no share sheet (answered from source)

`open` is implemented in **`ios_system/shell_cmds_ios/open.m`** (read from source, 2026-10-14). Its own
comment says: *"The `openurl` command still available. The `open` command checks if the passed argument
is a file path, if it is, it opens it with an `UIActivityViewController` and if not, it calls the
`openurl` command."* So the share sheet was the right expectation — but the implementation fails
**silently** in two ways:

1. **Path not found → it degrades to `openurl`.** If `fileExistsAtPath:` is false, `open_main` calls
   `openurl_main` and the argument is treated as a **URL**. Nothing visible happens for a bare filename.
   Note it resolves the path against `NSFileManager.defaultManager.currentDirectoryPath`, which need not
   be the a-Shell shell's own current directory.
2. **`keyWindow` is nil.** The presentation code uses
   `[UIApplication sharedApplication].keyWindow` — deprecated since iOS 13, and a-Shell is scene-based
   (`SceneDelegate.swift`). When it is nil the code writes *"Cannot find a window. This command require
   an UI for opening files."* to **stderr** and then calls `presentViewController` on a nil controller,
   which does nothing.

It also sets `excludedActivityTypes` to exclude Notes, and uses a `popoverPresentationController`, which
on iPad needs a valid source view — another silent-no-op path.

**Conclusion: do not rely on `open` here.** The reliable route is the Files app (a-Shell's Documents is
visible there), which is also what the user switched to. `poc/udid_share.py` now always prints the
absolute path plus the Files location, tries `open` with an **absolute** path, and suggests
`view <file>` (Quick Look) to confirm the file is readable. `open` is documented as a convenience, not a
dependency.

Diagnosis for anyone who wants to pin down which of the two it is, in a-Shell:

```
ls -l barsign-test1-empty.mobileconfig      # does the shell see it?
open "$PWD/barsign-test1-empty.mobileconfig"  # absolute path
view barsign-test1-empty.mobileconfig        # Quick Look; proves it is readable
```

If the absolute path works and the relative one does not, it was cause 1. If neither works and stderr
says "Cannot find a window", it was cause 2.

---

# 29. Round: the empty profile was a bad control, and the working way to share from a-Shell

## 29.1 Device result: two of three profiles would not install

- `barsign-test1-empty` → iOS answered **"this profile is empty"** and never attempted an install. **Our
  test design was wrong**: an empty `Configuration` profile is rejected as empty, so it proved nothing.
- `barsign-test2` / `barsign-test3` (`Profile Service`) → would **not** install. The user notes the
  **Install button was still offered** even though iOS showed the profile as **"Not Signed"**, so the
  signature is probably not the cause.

The control is now a **Web Clip** (`com.apple.webClip.managed`) — a payload iOS certainly supports, which
adds one removable Home Screen icon. That makes the next round decisive: Web Clip installs ⇒ unsigned
profiles are fine and the failure is the `Profile Service` payload; Web Clip fails ⇒ the problem is
profile installation or AirDrop delivery itself.

## 29.2 Is `Profile Service` still supported? (research status)

Inconclusive, and honestly so. Apple documents it **only** in the archived *Over-the-Air Profile Delivery
and Configuration* guide (2018 and earlier); the current Device Management reference is MDM-oriented and
no "removed in iOS X" statement was found for this payload. Third-party UDID services still operate in
2026, which is weak evidence it survives — but the device just refused two of these profiles, which is
strong evidence it does not. The Web Clip control is what will settle it.

## 29.3 Sharing from a-Shell: what actually works

Checked a-Shell's own command registry (`a-Shell/ExtraCommands.swift`, `@_cdecl` names) and
`ios_system/shell_cmds_ios/`:

- there is **no `share` command** anywhere in a-Shell;
- the **only** in-app share sheet is `open <file>`, which we already know fails silently (§28);
- `view` delegates to `preview_main`, not a share-capable controller.

What does work, with no Files navigation by hand: a **URL scheme** that opens the Files app at a folder:

```
openurl shareddocuments://<absolute folder>
```

This is the `openurl` branch — a real URL, so it does not touch the broken file-path/keyWindow code — and
a-Shell's own issue #383 documents exactly this trick
(`open shareddocuments:///private/var/mobile/Containers/Data/Application/<uuid>/Documents`). The script now
prints the precise command, built from its own real container path, for both `--diag-profiles` and
`--airdrop`. From there the Files share sheet (which is native and reliable) does the AirDrop.

The long-term answer is Shortcuts: a-Shell ships **`Put File` / `Get File` intents**, so the Shortcut can
pull the file out of a-Shell and use its own share/AirDrop action — no reliance on a-Shell's broken
`open`. That is recorded as a requirement for `SHORTCUT_BUILD_GUIDE.md`.
