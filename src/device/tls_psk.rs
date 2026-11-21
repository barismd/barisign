//! TLS 1.2 with a pre-shared key (RFC 4279), the two suites iOS offers on the
//! RemotePairing tunnel: TLS_PSK_WITH_AES_256_CBC_SHA384 (0x00AF, preferred)
//! and TLS_PSK_WITH_AES_128_CBC_SHA (0x008C). Empty PSK identity; the PSK is the
//! X25519 secret from pair-verify. Client side only.

use aes::cipher::{block_padding::NoPadding, BlockDecryptMut, BlockEncryptMut, KeyIvInit};
use hmac::{Hmac, Mac};
use sha1::Sha1;
use sha2::{Digest, Sha256, Sha384};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

const VERSION: [u8; 2] = [3, 3];
const CT_CCS: u8 = 20;
const CT_ALERT: u8 = 21;
const CT_HANDSHAKE: u8 = 22;
const CT_DATA: u8 = 23;
pub const MAX_PLAINTEXT: usize = 16384;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Suite {
    Aes256Sha384,
    Aes128Sha,
}

impl Suite {
    fn from_id(id: u16) -> Option<Self> {
        match id {
            0x00AF => Some(Suite::Aes256Sha384),
            0x008C => Some(Suite::Aes128Sha),
            _ => None,
        }
    }
    fn key_len(self) -> usize {
        match self { Suite::Aes256Sha384 => 32, Suite::Aes128Sha => 16 }
    }
    fn mac_len(self) -> usize {
        match self { Suite::Aes256Sha384 => 48, Suite::Aes128Sha => 20 }
    }
    fn mac(self, key: &[u8], parts: &[&[u8]]) -> Vec<u8> {
        match self {
            Suite::Aes256Sha384 => hmac_parts::<Hmac<Sha384>>(key, parts),
            Suite::Aes128Sha => hmac_parts::<Hmac<Sha1>>(key, parts),
        }
    }
    fn prf(self, secret: &[u8], label: &[u8], seed: &[u8], len: usize) -> Vec<u8> {
        let mut full = label.to_vec();
        full.extend_from_slice(seed);
        match self {
            Suite::Aes256Sha384 => p_hash::<Hmac<Sha384>>(secret, &full, len),
            Suite::Aes128Sha => p_hash::<Hmac<Sha256>>(secret, &full, len),
        }
    }
    fn transcript_hash(self, data: &[u8]) -> Vec<u8> {
        match self {
            Suite::Aes256Sha384 => Sha384::digest(data).to_vec(),
            Suite::Aes128Sha => Sha256::digest(data).to_vec(),
        }
    }
}

fn hmac_parts<M: Mac + hmac::digest::KeyInit>(key: &[u8], parts: &[&[u8]]) -> Vec<u8> {
    let mut mac = <M as hmac::digest::KeyInit>::new_from_slice(key).expect("HMAC takes any key");
    for p in parts {
        mac.update(p);
    }
    mac.finalize().into_bytes().to_vec()
}

fn p_hash<M: Mac + hmac::digest::KeyInit + Clone>(secret: &[u8], seed: &[u8], len: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(len + 64);
    let mut a = hmac_parts::<M>(secret, &[seed]);
    while out.len() < len {
        out.extend_from_slice(&hmac_parts::<M>(secret, &[&a, seed]));
        a = hmac_parts::<M>(secret, &[&a]);
    }
    out.truncate(len);
    out
}

fn psk_premaster(psk: &[u8]) -> Vec<u8> {
    let n = (psk.len() as u16).to_be_bytes();
    let mut out = n.to_vec();
    out.extend(std::iter::repeat(0u8).take(psk.len()));
    out.extend_from_slice(&n);
    out.extend_from_slice(psk);
    out
}

fn record(kind: u8, body: &[u8]) -> Vec<u8> {
    let mut out = vec![kind, VERSION[0], VERSION[1]];
    out.extend_from_slice(&(body.len() as u16).to_be_bytes());
    out.extend_from_slice(body);
    out
}

fn handshake_msg(kind: u8, body: &[u8]) -> Vec<u8> {
    let mut out = vec![kind];
    out.extend_from_slice(&(body.len() as u32).to_be_bytes()[1..]);
    out.extend_from_slice(body);
    out
}

fn alert_text(payload: &[u8]) -> String {
    let what = match payload.get(1) {
        Some(0) => "close_notify",
        Some(10) => "unexpected_message",
        Some(20) => "bad_record_mac",
        Some(40) => "handshake_failure",
        Some(47) => "illegal_parameter",
        Some(51) => "decrypt_error",
        Some(70) => "protocol_version",
        Some(80) => "internal_error",
        Some(115) => "unknown_psk_identity",
        _ => "alert",
    };
    format!("TLS {what} ({:?})", payload)
}

async fn read_record<R: AsyncRead + Unpin>(r: &mut R) -> std::io::Result<(u8, Vec<u8>)> {
    let mut header = [0u8; 5];
    r.read_exact(&mut header).await?;
    let len = u16::from_be_bytes([header[3], header[4]]) as usize;
    let mut body = vec![0u8; len];
    r.read_exact(&mut body).await?;
    Ok((header[0], body))
}

fn io_err(msg: impl Into<String>) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidData, msg.into())
}

pub struct Keys {
    suite: Suite,
    mac: Vec<u8>,
    key: Vec<u8>,
    seq: u64,
}

impl Keys {
    fn seal(&mut self, kind: u8, plain: &[u8]) -> Vec<u8> {
        let mut payload = plain.to_vec();
        let mac = self.suite.mac(&self.mac, &[
            &self.seq.to_be_bytes(), &[kind], &VERSION, &(plain.len() as u16).to_be_bytes(), plain]);
        payload.extend_from_slice(&mac);
        let pad = 16 - (payload.len() % 16);
        payload.extend(std::iter::repeat((pad - 1) as u8).take(pad));
        let iv: [u8; 16] = rand::random();
        let len = payload.len();
        match self.suite {
            Suite::Aes256Sha384 => cbc::Encryptor::<aes::Aes256>::new_from_slices(&self.key, &iv)
                .unwrap().encrypt_padded_mut::<NoPadding>(&mut payload, len).unwrap(),
            Suite::Aes128Sha => cbc::Encryptor::<aes::Aes128>::new_from_slices(&self.key, &iv)
                .unwrap().encrypt_padded_mut::<NoPadding>(&mut payload, len).unwrap(),
        };
        self.seq += 1;
        let mut body = iv.to_vec();
        body.extend_from_slice(&payload);
        record(kind, &body)
    }

    fn open(&mut self, kind: u8, body: &[u8]) -> std::io::Result<Vec<u8>> {
        if body.len() < 32 || body.len() % 16 != 0 {
            return Err(io_err("malformed TLS record"));
        }
        let (iv, cipher) = body.split_at(16);
        let mut data = cipher.to_vec();
        match self.suite {
            Suite::Aes256Sha384 => cbc::Decryptor::<aes::Aes256>::new_from_slices(&self.key, iv)
                .unwrap().decrypt_padded_mut::<NoPadding>(&mut data).map_err(|_| io_err("TLS decrypt"))?,
            Suite::Aes128Sha => cbc::Decryptor::<aes::Aes128>::new_from_slices(&self.key, iv)
                .unwrap().decrypt_padded_mut::<NoPadding>(&mut data).map_err(|_| io_err("TLS decrypt"))?,
        };
        let pad = *data.last().unwrap() as usize + 1;
        let mac_len = self.suite.mac_len();
        if pad + mac_len > data.len() || data[data.len() - pad..].iter().any(|b| *b as usize != pad - 1) {
            return Err(io_err("bad TLS padding"));
        }
        let plain_len = data.len() - pad - mac_len;
        let (plain, rest) = data.split_at(plain_len);
        let expected = self.suite.mac(&self.mac, &[
            &self.seq.to_be_bytes(), &[kind], &VERSION, &(plain_len as u16).to_be_bytes(), plain]);
        if !crate::srp::same(&rest[..mac_len], &expected) {
            return Err(io_err("TLS record MAC mismatch"));
        }
        self.seq += 1;
        Ok(plain.to_vec())
    }
}

/// The write half: application data out.
pub struct TlsWriter<W> {
    inner: W,
    keys: Keys,
}

impl<W: AsyncWrite + Unpin> TlsWriter<W> {
    pub async fn write(&mut self, data: &[u8]) -> std::io::Result<()> {
        let mut out = Vec::with_capacity(data.len() + 128);
        for chunk in data.chunks(MAX_PLAINTEXT) {
            out.extend_from_slice(&self.keys.seal(CT_DATA, chunk));
        }
        self.inner.write_all(&out).await?;
        self.inner.flush().await
    }
}

/// The read half: application data in, as it arrives.
pub struct TlsReader<R> {
    inner: R,
    keys: Keys,
}

impl<R: AsyncRead + Unpin> TlsReader<R> {
    /// Next non-empty application-data payload.
    pub async fn read(&mut self) -> std::io::Result<Vec<u8>> {
        loop {
            let (kind, body) = read_record(&mut self.inner).await?;
            match kind {
                CT_ALERT => return Err(io_err(alert_text(&body))),
                CT_CCS => continue,
                CT_DATA | CT_HANDSHAKE => {
                    let plain = self.keys.open(kind, &body)?;
                    if kind == CT_DATA && !plain.is_empty() {
                        return Ok(plain);
                    }
                }
                _ => continue,
            }
        }
    }
}

/// Runs the handshake on `reader`/`writer` (two halves of one connection).
pub async fn connect<R, W>(mut reader: R, mut writer: W, psk: &[u8])
    -> std::io::Result<(TlsReader<R>, TlsWriter<W>)>
where R: AsyncRead + Unpin, W: AsyncWrite + Unpin
{
    let client_random: [u8; 32] = rand::random();
    let mut hello = VERSION.to_vec();
    hello.extend_from_slice(&client_random);
    hello.push(0);
    hello.extend_from_slice(&[0, 4, 0x00, 0xAF, 0x00, 0x8C]);
    hello.extend_from_slice(&[1, 0]);
    let hello = handshake_msg(1, &hello);
    let mut transcript = hello.clone();
    writer.write_all(&record(CT_HANDSHAKE, &hello)).await?;
    writer.flush().await?;

    let mut server_random = None;
    let mut suite = None;
    let mut done = false;
    let mut pending: Vec<u8> = Vec::new();
    while !done {
        let (kind, body) = read_record(&mut reader).await?;
        match kind {
            CT_ALERT => return Err(io_err(alert_text(&body))),
            CT_HANDSHAKE => {}
            _ => return Err(io_err(format!("unexpected TLS record {kind} during handshake"))),
        }
        transcript.extend_from_slice(&body);
        pending.extend_from_slice(&body);
        while pending.len() >= 4 {
            let len = u32::from_be_bytes([0, pending[1], pending[2], pending[3]]) as usize;
            if pending.len() < 4 + len {
                break;
            }
            let msg: Vec<u8> = pending.drain(..4 + len).collect();
            match msg[0] {
                2 => {
                    if msg.len() < 4 + 2 + 32 + 1 {
                        return Err(io_err("short ServerHello"));
                    }
                    server_random = Some(msg[6..38].to_vec());
                    let at = 39 + msg[38] as usize;
                    let id = u16::from_be_bytes([*msg.get(at).ok_or_else(|| io_err("short ServerHello"))?,
                                                 *msg.get(at + 1).ok_or_else(|| io_err("short ServerHello"))?]);
                    suite = Some(Suite::from_id(id).ok_or_else(|| io_err(format!("server chose suite 0x{id:04X}")))?);
                }
                14 => done = true,
                _ => {}
            }
        }
    }
    let server_random = server_random.ok_or_else(|| io_err("no ServerHello"))?;
    let suite = suite.ok_or_else(|| io_err("no cipher suite"))?;

    let mut randoms = client_random.to_vec();
    randoms.extend_from_slice(&server_random);
    let master = suite.prf(&psk_premaster(psk), b"master secret", &randoms, 48);
    let mut expansion_seed = server_random.clone();
    expansion_seed.extend_from_slice(&client_random);
    let (m, k) = (suite.mac_len(), suite.key_len());
    let block = suite.prf(&master, b"key expansion", &expansion_seed, 2 * m + 2 * k);
    let mut client = Keys { suite, mac: block[..m].to_vec(), key: block[2 * m..2 * m + k].to_vec(), seq: 0 };
    let mut server = Keys { suite, mac: block[m..2 * m].to_vec(), key: block[2 * m + k..2 * m + 2 * k].to_vec(), seq: 0 };

    let key_exchange = handshake_msg(16, &[0, 0]);
    transcript.extend_from_slice(&key_exchange);
    let mut out = record(CT_HANDSHAKE, &key_exchange);
    out.extend_from_slice(&record(CT_CCS, &[1]));
    let verify = suite.prf(&master, b"client finished", &suite.transcript_hash(&transcript), 12);
    let finished = handshake_msg(20, &verify);
    out.extend_from_slice(&client.seal(CT_HANDSHAKE, &finished));
    transcript.extend_from_slice(&finished);
    writer.write_all(&out).await?;
    writer.flush().await?;

    loop {
        let (kind, body) = read_record(&mut reader).await?;
        match kind {
            CT_ALERT => return Err(io_err(alert_text(&body))),
            CT_CCS => continue,
            CT_HANDSHAKE => {
                let plain = server.open(kind, &body)?;
                if plain.first() != Some(&20) {
                    continue;
                }
                let expected = suite.prf(&master, b"server finished", &suite.transcript_hash(&transcript), 12);
                if plain.len() < 16 || !crate::srp::same(&plain[4..16], &expected) {
                    return Err(io_err("server Finished does not verify (wrong key?)"));
                }
                break;
            }
            _ => return Err(io_err(format!("unexpected TLS record {kind} before Finished"))),
        }
    }
    Ok((TlsReader { inner: reader, keys: server }, TlsWriter { inner: writer, keys: client }))
}

#[cfg(test)]
mod tests {
    //! Interop with OpenSSL's s_server (an independent TLS 1.2 PSK server).
    use super::*;

    async fn against_openssl(cipher: &str) {
        let psk = [0x5Au8; 32];
        let port = 20000 + (rand::random::<u16>() % 20000);
        let mut server = std::process::Command::new("openssl")
            .args(["s_server", "-quiet", "-rev", "-nocert", "-tls1_2", "-accept", &port.to_string(),
                   "-psk", &hex::encode(psk), "-cipher", cipher])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .expect("openssl s_server");
        let mut stream = None;
        for _ in 0..50 {
            if let Ok(s) = tokio::net::TcpStream::connect(("127.0.0.1", port)).await {
                stream = Some(s);
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        }
        let (r, w) = stream.expect("server up").into_split();
        let (mut reader, mut writer) = connect(r, w, &psk).await.expect("handshake");
        writer.write(b"hello baristore\n").await.unwrap();
        let mut got = Vec::new();
        while !got.ends_with(b"\n") {
            got.extend(reader.read().await.unwrap());
        }
        assert_eq!(got, b"erotsirab olleh\n");
        let _ = server.kill();
    }

    #[tokio::test]
    async fn aes256_sha384_with_openssl() {
        against_openssl("PSK-AES256-CBC-SHA384").await;
    }

    #[tokio::test]
    async fn aes128_sha_with_openssl() {
        against_openssl("PSK-AES128-CBC-SHA:@SECLEVEL=0").await;
    }
}
