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

**Not taken:** the developer portal, certificate/App ID/profile code, the UDID capture, the local
CA/TLS server, the `zsign` invocation, and the original `main()`/`parse_args()`.

## Changes made by BariSign

1. **`aes_cbc_decrypt()` reimplemented** with `cryptography`. Upstream shells out to
   `openssl enc -d`, and this function is on the sign-in path (it decrypts Apple's SPD reply).
   a-Shell has no `openssl`.
2. The `openssl()` helper was **removed** (it is unused once the function above is replaced).
3. The file was **cut** to the parts listed above; our own command-line front end lives in
   `../../poc/poc_login.py` and is MIT.

## Do not copy from here into MIT files

Keep this code here. If the same logic can be reimplemented from an MIT-licensed project
(`nab138/isideload`, `jkcoxson/idevice`), do that instead and reduce this folder.
