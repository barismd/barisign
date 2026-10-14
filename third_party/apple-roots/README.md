# Apple public root CA (trust file)

`apple-roots.pem` is Apple's public **Apple Root CA** certificate. It is needed because the Python
inside a-Shell ships its own CA list, which does **not** include Apple's root, so the TLS handshake to
`gsa.apple.com` fails with `CERTIFICATE_VERIFY_FAILED`.

It is used **only for `*.apple.com` hosts** (`signin._ctx_for`). Certificate verification is never
disabled anywhere in this project.

## Provenance

| | |
|---|---|
| Downloaded from | `https://www.apple.com/appleca/AppleIncRootCertificate.cer` (linked from <https://www.apple.com/certificateauthority/>) |
| Format kept | `AppleIncRootCertificate.cer` — the original DER bytes |
| Also provided | `apple-roots.pem` — the same certificate, PEM-encoded |
| Subject | `C=US, O=Apple Inc., OU=Apple Certification Authority, CN=Apple Root CA` |
| **SHA-256 fingerprint** | `B0:B1:73:0E:CB:C7:FF:45:05:14:2C:49:F1:29:5E:6E:DA:6B:CA:ED:7E:2C:68:C5:BE:91:B5:A1:10:01:F0:24` |
| Fingerprint published by Apple at | <https://support.apple.com/en-us/121672> ("List of available root certificates in iOS 18, iPadOS 18 …"), row **Apple Root CA** |

The fingerprint above is the value Apple publishes; it was checked to match the downloaded
certificate byte-for-byte (see the command below).

## Verify it yourself

```sh
openssl x509 -inform DER -in AppleIncRootCertificate.cer -noout -fingerprint -sha256
# expect: SHA256 Fingerprint=B0:B1:73:0E:CB:C7:FF:45:05:14:2C:49:F1:29:5E:6E:DA:6B:CA:ED:7E:2C:68:C5:BE:91:B5:A1:10:01:F0:24
```

`poc/poc_login.py` also checks this automatically: check 6/6 refuses to trust the file unless a
certificate in it hashes to the pinned fingerprint, and the pinned value is a constant in the script
(`APPLE_ROOT_FINGERPRINTS`), so replacing the file with a different CA makes the check fail loudly.

## Note on the chain

`gsa.apple.com` sends its leaf plus the intermediate **Apple Server Authentication CA**; only the root
is missing from a-Shell's trust list, which is why shipping this one certificate is enough.
