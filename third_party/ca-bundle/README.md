# CA bundle for the on-device Python

`cacert.pem` is a bundle of public CA root certificates, derived from Mozilla's CA list and
maintained by the curl project. It is **not** secret material.

## Why it is needed

The Python inside a-Shell builds its TLS context with an **empty trust store**. On the iPad, every
`https://` request failed with either

```
[X509: NO_CERTIFICATE_OR_CRL_FOUND] no certificate or crl found
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self signed certificate in certificate chain
```

Loading a real bundle fixes it. We reproduce the failure and the fix in the repository notes for
this round. **Certificate verification is never disabled.**

## Provenance

| | |
|---|---|
| Source URL | `https://curl.se/ca/cacert.pem` |
| Derived from | Mozilla's CA root list (the file states the date of the list it was built from) |
| Licence | The curl CA bundle is distributed as **public domain** (see the header inside the file) |
| Certificates | 121 |
| SHA-256 of this copy | `a41b5d356aea97a529fe27e0f7316d2f9d946d75927476cf9cf1b90637d00505` |

Verify with:

```sh
sha256sum cacert.pem
grep -c 'BEGIN CERTIFICATE' cacert.pem
```

## How it is used

`poc/poc_login.py` prefers a bundle already present on the device (`certifi`) and falls back to this
shipped file:

```python
try:
    import certifi
    bundle = certifi.where()
except ImportError:
    bundle = "<this file>"
signin.set_ca_bundle(bundle)     # load_verify_locations, verification stays ON
```

Apple's own root is **not** in this bundle, so `third_party/apple-roots/apple-roots.pem` is loaded
*in addition* for `*.apple.com` only.
