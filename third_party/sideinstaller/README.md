# Vendored code: SideInstaller's sign-in stack

This folder contains source code copied from **SideInstaller by FrizzleM** and is **not** covered by
BariSign's MIT licence. Everything in this folder is governed by `LICENSE.md` here.

- **Upstream:** https://github.com/FrizzleM/SideInstaller — file `ish/sideinstaller.py`
- **Upstream licence:** `LICENSE.md` in this folder (custom: non-commercial, source-only
  redistribution, attribution required, changes must be clearly indicated).
- **Credit required by §4 of that licence:** *SideInstaller by FrizzleM* — see the link above.

## What was taken

`signin.py` contains the following, unchanged unless listed under "Changes":

- terminal helpers, HTTP client (`request`), download helper;
- pure-Python AES (S-box, encryption block), AES-GCM and GHASH;
- `SRPClient` (RFC 5054 2048-bit group + SHA-256), `srp_password_key`, plist helpers;
- a raw `WebSocket` client;
- `Anisette` (v3, including the provisioning session), `GrandSlam`, `check_gs_error`;
- `AppleAccount` (sign-in, trusted-device and SMS 2FA), `app_token`;
- the anisette-server selection helpers (`anisette_candidates`, `connect_anisette`).

`portal.py` contains the **developer portal**: team selection, device registration, App ID creation,
CSR + certificate issuing (including the 7460 certificate-limit path and reuse-by-key-modulus), and
provisioning-profile download/parsing.

**Not taken:** the UDID capture, the local CA/TLS server, the `zsign` invocation, and the original
`main()`/`parse_args()`.

## Changes made by BariSign

1. **`aes_cbc_decrypt()` reimplemented** with `cryptography`. Upstream shells out to
   `openssl enc -d`, and this function is on the sign-in path (it decrypts Apple's SPD reply).
   a-Shell has no `openssl`.
2. The `openssl()` helper was **removed** (it is unused once the function above is replaced).
3. **`Anisette.detect()` keeps the server's `client_info`/`user_agent`** instead of discarding them.
   Since 2026-10-12 Apple refuses a sign-in whose claimed platform (-80045) differs from the platform
   the anisette data was minted on, and since ~10-24 it refuses the Android claim outright
   (-80046 / 503). The server's own strings are the only safe values.
4. **`GrandSlam.mac_headers()`** keeps the server's string when it already describes a Mac.
5. **HTTP errors now quote Apple's reply**: `plist_request()` appends the first ~240
   characters of the response body, because a 503 carries the only clue there is.
6. **`portal.py`: every `openssl` call replaced with `cryptography`** — CSR building, PEM/DER
   conversion and the key-modulus comparison (a-Shell has no `openssl` binary).
7. The files were **cut** to the parts listed above; our command-line front ends live in
   `../../poc/poc_login.py`, `../../poc/poc_portal.py` and `../../poc/barisign.py`, all MIT.
8. `STORAGE` is overridden by the caller to `state/` inside the installed folder, because a-Shell can
   only write under `~/Documents`.

## Do not copy from here into MIT files

Keep this code here. If the same logic can be reimplemented from an MIT-licensed project
(`nab138/isideload`, `jkcoxson/idevice`), do that instead and reduce this folder.
