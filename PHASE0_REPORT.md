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

## 20.5 Next action

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
