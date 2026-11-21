//! RemotePairing client: pair-verify with our saved record, then ask the
//! iPhone for a TCP tunnel listener. Message shapes and key derivation follow
//! jkcoxson/idevice (wire version 19, one sequence counter for plain and
//! encrypted messages, the X25519 secret as TLS-PSK).

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use chacha20poly1305::aead::{Aead, KeyInit, Payload};
use chacha20poly1305::{ChaCha20Poly1305, Nonce};
use ed25519_dalek::Signer;
use hkdf::Hkdf;
use serde_json::{json, Value as Json};
use sha2::Sha512;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use x25519_dalek::{EphemeralSecret, PublicKey};

use crate::pairing::{record::PairingRecord, tlv8};

pub const WIRE_VERSION: u64 = 19;

pub struct Control {
    stream: TcpStream,
    sequence: u64,
    encrypted_sequence: u64,
    client: Option<ChaCha20Poly1305>,
    server: Option<ChaCha20Poly1305>,
    pub psk: Vec<u8>,
    pub peer_info: Json,
}

#[derive(Debug)]
pub enum VerifyError {
    /// The iPhone no longer knows this host: pair again.
    NotPaired,
    Other(String),
}

impl std::fmt::Display for VerifyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            VerifyError::NotPaired => write!(f, "this iPhone no longer accepts BariStore's pairing; pair again"),
            VerifyError::Other(s) => write!(f, "{s}"),
        }
    }
}

fn other(e: impl std::fmt::Display) -> VerifyError {
    VerifyError::Other(e.to_string())
}

impl Control {
    async fn send_raw(&mut self, message: Json) -> Result<(), VerifyError> {
        let envelope = json!({"message": message, "originatedBy": "host", "sequenceNumber": self.sequence});
        self.sequence += 1;
        let body = serde_json::to_vec(&envelope).map_err(other)?;
        let mut frame = b"RPPairing".to_vec();
        frame.extend_from_slice(&(body.len() as u16).to_be_bytes());
        frame.extend_from_slice(&body);
        self.stream.write_all(&frame).await.map_err(other)
    }

    async fn send_plain(&mut self, value: Json) -> Result<(), VerifyError> {
        self.send_raw(json!({"plain": {"_0": value}})).await
    }

    async fn recv(&mut self) -> Result<Json, VerifyError> {
        let mut head = [0u8; 11];
        self.stream.read_exact(&mut head).await.map_err(other)?;
        if &head[..9] != b"RPPairing" {
            return Err(other("the iPhone did not answer with RPPairing framing"));
        }
        let mut body = vec![0u8; u16::from_be_bytes([head[9], head[10]]) as usize];
        self.stream.read_exact(&mut body).await.map_err(other)?;
        serde_json::from_slice(&body).map_err(other)
    }

    async fn pairing_data(&mut self, data: &[u8], new_session: bool) -> Result<(), VerifyError> {
        self.send_plain(json!({"event": {"_0": {"pairingData": {"_0": {
            "data": B64.encode(data), "kind": "verifyManualPairing", "startNewSession": new_session}}}}})).await
    }

    async fn recv_pairing_data(&mut self) -> Result<std::collections::BTreeMap<u8, Vec<u8>>, VerifyError> {
        let reply = self.recv().await?;
        let data = reply
            .pointer("/message/plain/_0/event/_0/pairingData/_0/data")
            .and_then(Json::as_str)
            .ok_or_else(|| other(format!("no pair-verify data in {reply}")))?;
        tlv8::decode(&B64.decode(data).map_err(other)?).map_err(other)
    }

    pub async fn open(host: &str, port: u16, record: &PairingRecord) -> Result<Control, VerifyError> {
        let stream = tokio::time::timeout(std::time::Duration::from_secs(8), TcpStream::connect((host, port)))
            .await
            .map_err(|_| other(format!("{host}:{port} did not answer (is LocalDevVPN connected?)")))?
            .map_err(|e| other(format!("{host}:{port}: {e} (is LocalDevVPN connected?)")))?;
        let _ = stream.set_nodelay(true);
        let mut c = Control {
            stream, sequence: 0, encrypted_sequence: 0, client: None, server: None,
            psk: Vec::new(), peer_info: Json::Null,
        };
        c.send_plain(json!({"request": {"_0": {"handshake": {"_0": {
            "hostOptions": {"attemptPairVerify": true}, "wireProtocolVersion": WIRE_VERSION}}}}})).await?;
        let reply = c.recv().await?;
        c.peer_info = reply.pointer("/message/plain/_0/response/_1/handshake/_0/peerDeviceInfo")
            .cloned().unwrap_or(Json::Null);
        c.verify(record).await?;
        Ok(c)
    }

    async fn verify(&mut self, record: &PairingRecord) -> Result<(), VerifyError> {
        let secret = EphemeralSecret::random_from_rng(rand::rngs::OsRng);
        let public = PublicKey::from(&secret);
        self.pairing_data(&tlv8::encode(&[(tlv8::STATE, &[1]), (tlv8::PUBLIC_KEY, public.as_bytes())]), true).await?;
        let m2 = self.recv_pairing_data().await?;
        if m2.contains_key(&tlv8::ERROR) {
            return Err(VerifyError::NotPaired);
        }
        let device: [u8; 32] = m2.get(&tlv8::PUBLIC_KEY)
            .and_then(|k| k.as_slice().try_into().ok())
            .ok_or_else(|| other("pair-verify M2 without a 32-byte key"))?;
        let shared = secret.diffie_hellman(&PublicKey::from(device));
        self.psk = shared.as_bytes().to_vec();

        let mut key = [0u8; 32];
        Hkdf::<Sha512>::new(Some(b"Pair-Verify-Encrypt-Salt"), &self.psk)
            .expand(b"Pair-Verify-Encrypt-Info", &mut key).unwrap();
        let cipher = ChaCha20Poly1305::new_from_slice(&key).unwrap();
        let identifier = record.host.identifier.as_bytes();
        let mut signed = public.as_bytes().to_vec();
        signed.extend_from_slice(identifier);
        signed.extend_from_slice(&device);
        let signature = record.host.signing_key().sign(&signed).to_bytes();
        let inner = tlv8::encode(&[(tlv8::IDENTIFIER, identifier), (tlv8::SIGNATURE, &signature)]);
        let sealed = cipher
            .encrypt(Nonce::from_slice(b"\0\0\0\0PV-Msg03"), Payload { msg: &inner, aad: b"" })
            .map_err(|_| other("pair-verify encryption"))?;
        self.pairing_data(&tlv8::encode(&[(tlv8::STATE, &[3]), (tlv8::ENCRYPTED_DATA, &sealed)]), false).await?;
        let m4 = self.recv_pairing_data().await?;
        if m4.contains_key(&tlv8::ERROR) {
            let _ = self.send_plain(json!({"event": {"_0": {"pairVerifyFailed": {}}}})).await;
            return Err(VerifyError::NotPaired);
        }
        let derive = |info: &[u8]| {
            let mut k = [0u8; 32];
            Hkdf::<Sha512>::new(None, &self.psk).expand(info, &mut k).unwrap();
            ChaCha20Poly1305::new_from_slice(&k).unwrap()
        };
        self.client = Some(derive(b"ClientEncrypt-main"));
        self.server = Some(derive(b"ServerEncrypt-main"));
        Ok(())
    }

    async fn encrypted(&mut self, request: Json) -> Result<Json, VerifyError> {
        let mut nonce = [0u8; 12];
        nonce[..8].copy_from_slice(&self.encrypted_sequence.to_le_bytes());
        let plain = serde_json::to_vec(&request).map_err(other)?;
        let sealed = self.client.as_ref().ok_or_else(|| other("not verified"))?
            .encrypt(Nonce::from_slice(&nonce), Payload { msg: &plain, aad: b"" })
            .map_err(|_| other("encryption failed"))?;
        self.send_raw(json!({"streamEncrypted": {"_0": B64.encode(sealed)}})).await?;
        let reply = self.recv().await?;
        let data = reply.pointer("/message/streamEncrypted/_0").and_then(Json::as_str)
            .ok_or_else(|| other(format!("no encrypted reply: {reply}")))?;
        let opened = self.server.as_ref().unwrap()
            .decrypt(Nonce::from_slice(&nonce), Payload { msg: &B64.decode(data).map_err(other)?, aad: b"" })
            .map_err(|_| other("cannot decrypt the iPhone's reply"))?;
        self.encrypted_sequence += 1;
        let value: Json = serde_json::from_slice(&opened).map_err(other)?;
        value.pointer("/response/_1").cloned()
            .ok_or_else(|| other(format!("the iPhone refused the request: {value}")))
    }

    pub async fn create_tcp_listener(&mut self) -> Result<u16, VerifyError> {
        let reply = self.encrypted(json!({"request": {"_0": {"createListener": {
            "key": B64.encode(&self.psk), "transportProtocolType": "tcp"}}}})).await?;
        reply.pointer("/createListener/port")
            .and_then(|p| p.as_u64().or_else(|| p.as_str().and_then(|s| s.parse().ok())))
            .and_then(|p| u16::try_from(p).ok())
            .ok_or_else(|| other(format!("no tunnel port in {reply}")))
    }
}
