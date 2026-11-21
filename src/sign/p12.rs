//! PKCS#12 (RFC 7292) for the certificate AltStore-family apps sign with:
//! SideStore and AltStore read `ALTCertificate.p12` from their bundle (with
//! `ALTCertificateID` in Info.plist naming the certificate) and open it with
//! the certificate's portal machine identifier as the password; without it
//! they ask Apple for a new certificate or to revoke one. The key bag uses
//! PBES2 (PBKDF2-HMAC-SHA256, AES-256-CBC), the MAC HMAC-SHA256, as current
//! OpenSSL and Apple's Security framework write them.

use aes::cipher::{block_padding::Pkcs7, BlockEncryptMut, KeyIvInit};
use hmac::{Hmac, Mac};
use rsa::pkcs8::EncodePrivateKey;
use rsa::RsaPrivateKey;
use sha1::Sha1;
use sha2::{Digest, Sha256};

use crate::der;

const ITERATIONS: u64 = 2048;
const DATA: &str = "1.2.840.113549.1.7.1";

fn bmp(s: &str) -> Vec<u8> {
    s.encode_utf16().flat_map(u16::to_be_bytes).collect()
}

/// RFC 7292 appendix B.2 key derivation with SHA-256 (u = 32, v = 64).
fn pkcs12_kdf(password: &str, salt: &[u8], id: u8, iterations: u64, n: usize) -> Vec<u8> {
    const U: usize = 32;
    const V: usize = 64;
    let fill = |src: &[u8]| -> Vec<u8> {
        if src.is_empty() {
            return Vec::new();
        }
        let len = V * src.len().div_ceil(V);
        src.iter().copied().cycle().take(len).collect()
    };
    let mut pw = bmp(password);
    pw.extend_from_slice(&[0, 0]);
    let mut i: Vec<u8> = [fill(salt), fill(&pw)].concat();
    let mut out = Vec::new();
    while out.len() < n {
        let mut h = Sha256::new();
        h.update([id; V]);
        h.update(&i);
        let mut a = h.finalize().to_vec();
        for _ in 1..iterations {
            a = Sha256::digest(&a).to_vec();
        }
        let b: Vec<u8> = a.iter().copied().cycle().take(V).collect();
        for block in i.chunks_mut(V) {
            let mut carry = 1u16;
            for k in (0..V).rev() {
                let sum = block[k] as u16 + b[k] as u16 + carry;
                block[k] = sum as u8;
                carry = sum >> 8;
            }
        }
        out.extend_from_slice(&a[..U.min(n - out.len())]);
    }
    out
}

fn content_info_data(content: &[u8]) -> Vec<u8> {
    der::seq(&[der::oid(DATA), der::ctx(0, &der::octets(content))])
}

fn attributes(local_key_id: &[u8], name: &str) -> Vec<u8> {
    der::set(&[
        der::seq(&[der::oid("1.2.840.113549.1.9.20"), der::set(&[der::tlv(0x1E, &bmp(name))])]),
        der::seq(&[der::oid("1.2.840.113549.1.9.21"), der::set(&[der::octets(local_key_id)])]),
    ])
}

/// The certificate and its key as a .p12 protected by `password`.
pub fn pkcs12(cert: &[u8], key: &RsaPrivateKey, password: &str, name: &str) -> Result<Vec<u8>, String> {
    let pkcs8 = key.to_pkcs8_der().map_err(|e| e.to_string())?;
    let local_key_id = Sha1::digest(cert).to_vec();
    let attrs = attributes(&local_key_id, name);

    let cert_bag = der::seq(&[
        der::oid("1.2.840.113549.1.12.10.1.3"),
        der::ctx(0, &der::seq(&[der::oid("1.2.840.113549.1.9.22.1"), der::ctx(0, &der::octets(cert))])),
        attrs.clone(),
    ]);

    let salt: [u8; 16] = rand::random();
    let iv: [u8; 16] = rand::random();
    let mut aes_key = [0u8; 32];
    pbkdf2::pbkdf2::<Hmac<Sha256>>(password.as_bytes(), &salt, ITERATIONS as u32, &mut aes_key)
        .map_err(|e| e.to_string())?;
    let sealed = cbc::Encryptor::<aes::Aes256>::new(&aes_key.into(), &iv.into())
        .encrypt_padded_vec_mut::<Pkcs7>(pkcs8.as_bytes());
    let pbes2 = der::seq(&[
        der::oid("1.2.840.113549.1.5.13"),
        der::seq(&[
            der::seq(&[
                der::oid("1.2.840.113549.1.5.12"),
                der::seq(&[der::octets(&salt), der::int(ITERATIONS),
                           der::seq(&[der::oid("1.2.840.113549.2.9"), der::null()])]),
            ]),
            der::seq(&[der::oid("2.16.840.1.101.3.4.1.42"), der::octets(&iv)]),
        ]),
    ]);
    let key_bag = der::seq(&[
        der::oid("1.2.840.113549.1.12.10.1.2"),
        der::ctx(0, &der::seq(&[pbes2, der::octets(&sealed)])),
        attrs,
    ]);

    let auth_safe = der::seq(&[
        content_info_data(&der::seq(&[cert_bag])),
        content_info_data(&der::seq(&[key_bag])),
    ]);
    let mac_salt: [u8; 16] = rand::random();
    let mac_key = pkcs12_kdf(password, &mac_salt, 3, ITERATIONS, 32);
    let mut mac = <Hmac<Sha256> as Mac>::new_from_slice(&mac_key).map_err(|e| e.to_string())?;
    mac.update(&auth_safe);
    let digest = mac.finalize().into_bytes();
    let mac_data = der::seq(&[
        der::seq(&[der::seq(&[der::oid("2.16.840.1.101.3.4.2.1"), der::null()]), der::octets(&digest)]),
        der::octets(&mac_salt),
        der::int(ITERATIONS),
    ]);
    Ok(der::seq(&[der::int(3), content_info_data(&auth_safe), mac_data]))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn openssl_opens_it() {
        let dir = std::env::temp_dir().join(format!("baristore-p12-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let key_path = dir.join("k.pem");
        let cert_path = dir.join("c.der");
        let ok = std::process::Command::new("openssl")
            .args(["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=Test", "-days", "1",
                   "-outform", "DER", "-keyout", key_path.to_str().unwrap(), "-out", cert_path.to_str().unwrap()])
            .output().unwrap().status.success();
        assert!(ok);
        use rsa::pkcs8::DecodePrivateKey;
        let key = RsaPrivateKey::from_pkcs8_pem(&std::fs::read_to_string(&key_path).unwrap()).unwrap();
        let cert = std::fs::read(&cert_path).unwrap();
        let p12 = pkcs12(&cert, &key, "A1B2C3D4-MACHINE", "BariStore").unwrap();
        let p12_path = dir.join("t.p12");
        std::fs::write(&p12_path, p12).unwrap();
        let out = std::process::Command::new("openssl")
            .args(["pkcs12", "-in", p12_path.to_str().unwrap(), "-passin", "pass:A1B2C3D4-MACHINE", "-nodes"])
            .output().unwrap();
        let text = String::from_utf8_lossy(&out.stdout);
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        assert!(text.contains("BEGIN CERTIFICATE") && text.contains("BEGIN PRIVATE KEY"));
        assert!(text.contains("friendlyName: BariStore"));
        let wrong = std::process::Command::new("openssl")
            .args(["pkcs12", "-in", p12_path.to_str().unwrap(), "-passin", "pass:wrong", "-nodes"])
            .output().unwrap();
        assert!(!wrong.status.success());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
