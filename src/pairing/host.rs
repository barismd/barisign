//! The pairable host: the side Settings › Privacy & Security › Developer Mode
//! › "Pair with …" dials. The iPhone drives the conversation; we answer the
//! handshake and play the accessory/server role of pair-setup M1–M6.
//!
//! Wire: each frame is b"RPPairing" + u16 BE length + JSON envelope
//! {"message":{"plain":{"_0": <value>}}, "originatedBy": "device",
//!  "sequenceNumber": n}. Pair-setup data travels base64 in
//! event._0.pairingData._0.data as TLV8.

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use chacha20poly1305::aead::{Aead, KeyInit, Payload};
use chacha20poly1305::{ChaCha20Poly1305, Nonce};
use ed25519_dalek::Signer;
use hkdf::Hkdf;
use plist::{Dictionary, Value};
use serde_json::{json, Value as Json};
use sha2::Sha512;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

use super::record::{HostIdentity, Peer};
use super::{opack, tlv8};
use crate::srp;

pub const SERVICE_TYPE: &str = "_remotepairing-pairable-host._tcp";
pub const MODEL: &str = "Mac17,7";
pub const WIRE_VERSION: u64 = 26;

#[derive(Debug)]
pub enum PairError {
    WrongCode,
    Rejected(String),
    Protocol(String),
    Io(std::io::Error),
}

impl std::fmt::Display for PairError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PairError::WrongCode => write!(f, "the code entered on the iPhone did not match"),
            PairError::Rejected(why) => write!(f, "the iPhone cancelled the pairing ({why})"),
            PairError::Protocol(why) => write!(f, "unexpected pairing message: {why}"),
            PairError::Io(e) => write!(f, "the connection broke: {e}"),
        }
    }
}

impl From<std::io::Error> for PairError {
    fn from(e: std::io::Error) -> Self {
        PairError::Io(e)
    }
}

fn proto(why: impl Into<String>) -> PairError {
    PairError::Protocol(why.into())
}

/// TXT records for the advertisement (the keys idevice's host publishes).
pub fn txt_records(identity: &HostIdentity, name: &str) -> Vec<(String, String)> {
    vec![
        ("name".into(), name.into()),
        ("identifier".into(), identity.identifier.clone()),
        ("authTag".into(), B64.encode(identity.auth_tag())),
        ("model".into(), MODEL.into()),
        ("flags".into(), "1".into()),
        ("ver".into(), WIRE_VERSION.to_string()),
        ("minVer".into(), "17".into()),
    ]
}

pub struct PairableHost<'a, S> {
    stream: S,
    identity: &'a HostIdentity,
    name: String,
    sequence: u64,
    log: &'a (dyn Fn(String) + Send + Sync),
}

impl<'a, S: AsyncRead + AsyncWrite + Unpin> PairableHost<'a, S> {
    pub fn new(stream: S, identity: &'a HostIdentity, name: &str,
               log: &'a (dyn Fn(String) + Send + Sync)) -> Self {
        PairableHost { stream, identity, name: name.into(), sequence: 0, log }
    }

    /// Runs the whole conversation. `issue` makes the setup code once the
    /// iPhone starts pair-setup (M1), which is when Settings asks for it.
    pub async fn accept<F, Fut>(mut self, issue: F) -> Result<Peer, PairError>
    where F: FnOnce() -> Fut, Fut: std::future::Future<Output = String>
    {
        self.handshake().await?;
        self.recv_tlv(1).await?;
        let code = issue().await;
        self.pair_setup(&code).await
    }

    async fn send(&mut self, value: Json) -> Result<(), PairError> {
        let envelope = json!({
            "message": {"plain": {"_0": value}},
            "originatedBy": "device",
            "sequenceNumber": self.sequence,
        });
        self.sequence += 1;
        let body = serde_json::to_vec(&envelope).map_err(|e| proto(e.to_string()))?;
        if body.len() > u16::MAX as usize {
            return Err(proto("frame too large"));
        }
        let mut frame = b"RPPairing".to_vec();
        frame.extend_from_slice(&(body.len() as u16).to_be_bytes());
        frame.extend_from_slice(&body);
        self.stream.write_all(&frame).await?;
        self.stream.flush().await?;
        Ok(())
    }

    async fn recv(&mut self) -> Result<Json, PairError> {
        let mut magic = [0u8; 9];
        self.stream.read_exact(&mut magic).await?;
        if &magic != b"RPPairing" {
            return Err(proto("not an RPPairing frame"));
        }
        let mut len = [0u8; 2];
        self.stream.read_exact(&mut len).await?;
        let mut body = vec![0u8; u16::from_be_bytes(len) as usize];
        self.stream.read_exact(&mut body).await?;
        let envelope: Json = serde_json::from_slice(&body).map_err(|_| proto("frame is not JSON"))?;
        envelope
            .pointer("/message/plain/_0")
            .cloned()
            .ok_or_else(|| proto("frame without message.plain._0"))
    }

    async fn handshake(&mut self) -> Result<(), PairError> {
        let request = self.recv().await?;
        let handshake = request
            .pointer("/request/_0/handshake/_0")
            .ok_or_else(|| proto("expected a handshake request"))?;
        (self.log)(format!("handshake: wire {} options {}",
            handshake.get("wireProtocolVersion").unwrap_or(&Json::Null),
            handshake.get("hostOptions").unwrap_or(&Json::Null)));
        if handshake.pointer("/hostOptions/attemptPairVerify") == Some(&Json::Bool(true)) {
            return Err(proto("the iPhone asked to pair-verify an existing pairing"));
        }
        let response = json!({
            "response": {
                "forRequestIdentifier": 0,
                "_1": {"handshake": {"_0": {
                    "wireProtocolVersion": WIRE_VERSION,
                    "minimumSupportedWireProtocolVersion": 8,
                    "deviceOptions": {
                        "allowsPairSetup": true,
                        "allowsPinlessPairing": false,
                        "allowsIncomingTunnelConnections": false,
                        "allowsUpgradeOfLockdownPairings": false,
                        "allowsSharingSensitiveInfo": false,
                    },
                    "peerDeviceInfo": {
                        "udid": "",
                        "deviceKVSIncludesSensitiveInfo": false,
                        "identifier": self.identity.identifier,
                        "name": self.name,
                        "model": MODEL,
                    },
                }}},
            }
        });
        self.send(response).await
    }

    async fn send_tlv(&mut self, entries: &[(u8, &[u8])]) -> Result<(), PairError> {
        let data = B64.encode(tlv8::encode(entries));
        self.send(json!({"event": {"_0": {"pairingData": {"_0": {
            "data": data, "startNewSession": false, "kind": "setupManualPairing"}}}}})).await
    }

    async fn recv_tlv(&mut self, state: u8) -> Result<std::collections::BTreeMap<u8, Vec<u8>>, PairError> {
        let message = self.recv().await?;
        if let Some(event) = message.pointer("/event/_0") {
            if let Some(data) = event.pointer("/pairingData/_0/data").and_then(Json::as_str) {
                let raw = B64.decode(data).map_err(|_| proto("pairing data is not base64"))?;
                let tlv = tlv8::decode(&raw).map_err(proto)?;
                if let Some(code) = tlv.get(&tlv8::ERROR) {
                    return Err(PairError::Rejected(format!("TLV error {:?}", code)));
                }
                if tlv.get(&tlv8::STATE).map(|s| s.as_slice()) != Some(&[state][..]) {
                    return Err(proto(format!("expected pair-setup M{state}")));
                }
                (self.log)(format!("M{state} received"));
                return Ok(tlv);
            }
            if event.get("pairingRejectedWithError").is_some() {
                return Err(PairError::Rejected(event.to_string()));
            }
        }
        Err(proto(format!("expected pair-setup M{state}, got {message}")))
    }

    /// M2–M6, after M1 arrived.
    async fn pair_setup(&mut self, code: &str) -> Result<Peer, PairError> {
        let group = srp::Group::rfc5054_3072_sha512();
        let salt: [u8; 16] = rand::random();
        let x = srp::x_with_user(&group, &salt, b"Pair-Setup", code.as_bytes());
        let server = srp::Server::new(&group, b"Pair-Setup", &salt, &x);
        self.send_tlv(&[(tlv8::STATE, &[2]), (tlv8::SALT, &salt), (tlv8::PUBLIC_KEY, &server.public())]).await?;

        let m3 = self.recv_tlv(3).await?;
        let a = m3.get(&tlv8::PUBLIC_KEY).ok_or_else(|| proto("M3 without a public key"))?;
        let proof = m3.get(&tlv8::PROOF).ok_or_else(|| proto("M3 without a proof"))?;
        let session = server.process(a).map_err(proto)?;
        if !srp::same(proof, &session.client_proof) {
            self.send_tlv(&[(tlv8::STATE, &[4]), (tlv8::ERROR, &[2])]).await?;
            return Err(PairError::WrongCode);
        }
        self.send_tlv(&[(tlv8::STATE, &[4]), (tlv8::PROOF, &session.server_proof)]).await?;

        let key = hkdf_sha512(&session.key, b"Pair-Setup-Encrypt-Salt", b"Pair-Setup-Encrypt-Info");
        let cipher = ChaCha20Poly1305::new_from_slice(&key).map_err(|_| proto("bad key"))?;
        let m5 = self.recv_tlv(5).await?;
        let sealed = m5.get(&tlv8::ENCRYPTED_DATA).ok_or_else(|| proto("M5 without encrypted data"))?;
        let plain = cipher
            .decrypt(Nonce::from_slice(b"\0\0\0\0PS-Msg05"), Payload { msg: sealed, aad: b"" })
            .map_err(|_| proto("cannot decrypt M5"))?;
        let device = tlv8::decode(&plain).map_err(proto)?;
        let info = device
            .get(&tlv8::INFO)
            .map(|raw| opack::decode(raw))
            .transpose()
            .map_err(proto)?
            .and_then(|v| v.into_dictionary())
            .unwrap_or_default();
        let text = |k: &str| info.get(k).and_then(Value::as_string).unwrap_or("").to_string();
        let peer = Peer {
            udid: text("remotepairing_udid"),
            identifier: text("accountID"),
            name: text("name"),
            model: text("model"),
            alt_irk: info.get("altIRK").and_then(Value::as_data).map(<[u8]>::to_vec).unwrap_or_default(),
            public_key: device.get(&tlv8::PUBLIC_KEY).cloned().unwrap_or_default(),
        };

        let accessory_x = hkdf_sha512(&session.key, b"Pair-Setup-Accessory-Sign-Salt",
                                      b"Pair-Setup-Accessory-Sign-Info");
        let public = self.identity.public_key();
        let mut signed = accessory_x.to_vec();
        signed.extend_from_slice(self.identity.identifier.as_bytes());
        signed.extend_from_slice(&public);
        let signature = self.identity.signing_key().sign(&signed).to_bytes();
        let mut ours = Dictionary::new();
        ours.insert("altIRK".into(), Value::Data(self.identity.alt_irk.to_vec()));
        ours.insert("btAddr".into(), Value::String("11:22:33:44:55:66".into()));
        ours.insert("mac".into(), Value::Data(vec![0x11, 0x22, 0x33, 0x44, 0x55, 0x66]));
        ours.insert("remotepairing_serial_number".into(), Value::String("AAAAAAAAAAAA".into()));
        ours.insert("accountID".into(), Value::String(self.identity.identifier.clone()));
        ours.insert("remotepairing_udid".into(), Value::String(String::new()));
        ours.insert("model".into(), Value::String(MODEL.into()));
        ours.insert("name".into(), Value::String(self.name.clone()));
        let ours = opack::encode(&Value::Dictionary(ours));
        let m6_plain = tlv8::encode(&[
            (tlv8::IDENTIFIER, self.identity.identifier.as_bytes()),
            (tlv8::PUBLIC_KEY, &public),
            (tlv8::SIGNATURE, &signature),
            (tlv8::INFO, &ours),
        ]);
        let sealed = cipher
            .encrypt(Nonce::from_slice(b"\0\0\0\0PS-Msg06"), Payload { msg: &m6_plain, aad: b"" })
            .map_err(|_| proto("cannot encrypt M6"))?;
        self.send_tlv(&[(tlv8::ENCRYPTED_DATA, &sealed), (tlv8::STATE, &[6])]).await?;
        (self.log)("M6 sent: paired".into());
        Ok(peer)
    }
}

pub fn hkdf_sha512(ikm: &[u8], salt: &[u8], info: &[u8]) -> [u8; 32] {
    let mut out = [0u8; 32];
    Hkdf::<Sha512>::new(Some(salt), ikm).expand(info, &mut out).expect("32 bytes is a valid HKDF length");
    out
}
