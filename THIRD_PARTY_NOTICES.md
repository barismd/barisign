# Third-party notices

BariStore's code is MIT and written for this project. No third-party source
code is copied into it.

## Libraries in the binary

All are permissive. Generated with
`cargo tree -e normal --target aarch64-unknown-linux-musl --format '{p} {l}'`
(183 crates on 2026-11-21):

| Licence | Crates |
|---|---|
| MIT and/or Apache-2.0 (incl. 0BSD/Zlib/BSL-1.0/Unlicense alternatives) | 155 |
| Unicode-3.0 (ICU data via `url`/`idna`) | 18 |
| BSD-3-Clause | 4 |
| ISC / Apache-2.0 AND ISC (`ring`) | 3 |
| CDLA-Permissive-2.0 (`webpki-roots` certificate data) | 2 |

Main ones: `tokio`, `reqwest`, `rustls`, `ring`, `webpki-roots`,
`tokio-tungstenite`, `plist`, `serde`, `zip`, RustCrypto (`sha1`, `sha2`, `hmac`,
`hkdf`, `pbkdf2`, `aes`, `cbc`, `aes-gcm`, `chacha20poly1305`, `rsa`),
dalek (`ed25519-dalek`, `x25519-dalek`), `num-bigint`, `uuid`. Each crate's
licence text is in its source package on crates.io.

Test-only (not in the binary): `idevice-srp` (MIT OR Apache-2.0, an SRP oracle)
and `smoltcp` (0BSD, a TCP peer).

## Apple certificates (`assets/certs/`)

Apple Root CA, Apple Root CA - G3 and the Apple Worldwide Developer Relations
intermediates G2-G6: Apple's public CA certificates. Each intermediate verifies
against one of the two roots; Apple Root CA matches the fingerprint Apple
publishes. Used to trust gsa.apple.com and to complete code-signing chains.

## Protocol references (read, not copied)

| Project | Licence | Used for |
|---|---|---|
| [jkcoxson/idevice](https://github.com/jkcoxson/idevice) | MIT | RemotePairing, TLS-PSK tunnel, RemoteXPC, services |
| [nab138/isideload](https://github.com/nab138/isideload) | MIT | anisette v3, GrandSlam, developer services, app groups, Increased Memory Limit, ALTCertificate.p12 placement |
| [nab138/iloader](https://github.com/nab138/iloader) | MIT | the advertised client info fallback (2.4.2) |
| [SideStore/SideStore](https://github.com/SideStore/SideStore) | AGPL-3.0 | behaviour only: how it finds its signing certificate (ALTCertificateID, ALTCertificate.p12) |
| [FrizzleM/siboot](https://github.com/FrizzleM/siboot) | MIT (stated in Cargo.toml; no LICENSE file) | how Open Minis keeps a pairing alive (minis-bonjour, apple-player) |
| [OpenMinis/OpenMinis](https://github.com/OpenMinis/OpenMinis) | GPL-3.0 | documented interface of the `minis-*` / `apple-*` commands; behaviour of the player sheet |
| FrizzleM/SideInstaller | non-commercial | documented behaviour only: where SideStore / LiveContainer / AltStore read a pairing file; which client info Apple accepts (Oct–Nov 2026) |
| StikDebug/StikDebug | AGPL-3.0 | documented behaviour only: pairing-file destinations |

The apps BariStore offers (AltStore, SideStore, LiveContainer and every app in a
user's AltSources) are downloaded from their publishers at install time; none
is redistributed here.

## Logo

`assets/baristore.svg`: BariStore's logo, provided by the project owner.
