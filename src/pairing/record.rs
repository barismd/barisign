//! Our long-term host identity and the RemotePairing record.
//!
//! The record is an XML plist with `public_key`, `private_key` (32-byte
//! Ed25519 seed), `identifier` and `alt_irk`: the layout jkcoxson/idevice reads
//! (so StikDebug and friends can import it). We add `peer_*` keys about the
//! paired iPhone; readers ignore keys they do not know.

use ed25519_dalek::SigningKey;
use plist::{Dictionary, Value};
use std::path::Path;

use crate::siphash::siphash24;

#[derive(Clone)]
pub struct HostIdentity {
    seed: [u8; 32],
    pub identifier: String,
    pub alt_irk: [u8; 16],
}

#[derive(Clone, Debug, Default)]
pub struct Peer {
    pub udid: String,
    pub identifier: String,
    pub name: String,
    pub model: String,
    pub alt_irk: Vec<u8>,
    pub public_key: Vec<u8>,
}

impl HostIdentity {
    pub fn generate() -> Self {
        HostIdentity {
            seed: rand::random(),
            identifier: uuid::Uuid::new_v4().to_string().to_uppercase(),
            alt_irk: rand::random(),
        }
    }

    pub fn signing_key(&self) -> SigningKey {
        SigningKey::from_bytes(&self.seed)
    }

    pub fn public_key(&self) -> [u8; 32] {
        self.signing_key().verifying_key().to_bytes()
    }

    /// authTag for the TXT record: SipHash-2-4(altIRK, identifier), first six
    /// bytes of the little-endian result in reverse order.
    pub fn auth_tag(&self) -> [u8; 6] {
        let tag = siphash24(&self.alt_irk, self.identifier.as_bytes()).to_le_bytes();
        let mut out = [0u8; 6];
        for (i, byte) in out.iter_mut().enumerate() {
            *byte = tag[5 - i];
        }
        out
    }

    fn base_dict(&self) -> Dictionary {
        let mut d = Dictionary::new();
        d.insert("public_key".into(), Value::Data(self.public_key().to_vec()));
        d.insert("private_key".into(), Value::Data(self.seed.to_vec()));
        d.insert("identifier".into(), Value::String(self.identifier.clone()));
        d.insert("alt_irk".into(), Value::Data(self.alt_irk.to_vec()));
        d
    }

    pub fn from_dict(d: &Dictionary) -> Option<Self> {
        let seed: [u8; 32] = d.get("private_key")?.as_data()?.try_into().ok()?;
        let identifier = d.get("identifier")?.as_string()?.to_string();
        let alt_irk: [u8; 16] = d
            .get("alt_irk")
            .and_then(Value::as_data)
            .and_then(|v| v.try_into().ok())
            .unwrap_or_else(rand::random);
        let identity = HostIdentity { seed, identifier, alt_irk };
        if let Some(public) = d.get("public_key").and_then(Value::as_data) {
            if public != identity.public_key() {
                return None;
            }
        }
        Some(identity)
    }

    pub fn load_or_create(path: &Path) -> std::io::Result<Self> {
        if let Ok(Value::Dictionary(d)) = plist::from_file::<_, Value>(path) {
            if let Some(identity) = Self::from_dict(&d) {
                return Ok(identity);
            }
        }
        let identity = Self::generate();
        crate::state::write_private(path, &plist_xml(&identity.base_dict()))?;
        Ok(identity)
    }
}

pub struct PairingRecord {
    pub host: HostIdentity,
    pub peer: Peer,
}

impl PairingRecord {
    pub fn to_xml(&self) -> Vec<u8> {
        let mut d = self.host.base_dict();
        d.insert("peer_udid".into(), Value::String(self.peer.udid.clone()));
        d.insert("peer_identifier".into(), Value::String(self.peer.identifier.clone()));
        d.insert("peer_name".into(), Value::String(self.peer.name.clone()));
        d.insert("peer_model".into(), Value::String(self.peer.model.clone()));
        d.insert("peer_alt_irk".into(), Value::Data(self.peer.alt_irk.clone()));
        d.insert("peer_public_key".into(), Value::Data(self.peer.public_key.clone()));
        plist_xml(&d)
    }

    /// Only the RemotePairing keys: what apps import (SideStore rejects a file
    /// that looks like it holds a second, lockdown record).
    pub fn to_remote_only_xml(&self) -> Vec<u8> {
        plist_xml(&self.host.base_dict())
    }

    pub fn from_bytes(bytes: &[u8]) -> Option<Self> {
        let d = plist::from_bytes::<Value>(bytes).ok()?.into_dictionary()?;
        let host = HostIdentity::from_dict(&d)?;
        let text = |k: &str| d.get(k).and_then(Value::as_string).unwrap_or("").to_string();
        let data = |k: &str| d.get(k).and_then(Value::as_data).map(<[u8]>::to_vec).unwrap_or_default();
        Some(PairingRecord {
            host,
            peer: Peer {
                udid: text("peer_udid"),
                identifier: text("peer_identifier"),
                name: text("peer_name"),
                model: text("peer_model"),
                alt_irk: data("peer_alt_irk"),
                public_key: data("peer_public_key"),
            },
        })
    }
}

pub fn plist_xml(d: &Dictionary) -> Vec<u8> {
    let mut out = Vec::new();
    plist::to_writer_xml(&mut out, &Value::Dictionary(d.clone())).expect("plist to memory");
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn record_roundtrip() {
        let host = HostIdentity::generate();
        let record = PairingRecord {
            host: host.clone(),
            peer: Peer { udid: "00008110-001A2C3E0E91801E".into(), ..Default::default() },
        };
        let back = PairingRecord::from_bytes(&record.to_xml()).unwrap();
        assert_eq!(back.host.identifier, host.identifier);
        assert_eq!(back.host.public_key(), host.public_key());
        assert_eq!(back.peer.udid, "00008110-001A2C3E0E91801E");
    }
}
