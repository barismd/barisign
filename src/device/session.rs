//! One live connection to this iPhone: control channel + TLS-PSK CDTunnel +
//! our TCP stack + RSD service map. LocalDevVPN loops 10.7.0.1 back to the
//! device's own RemotePairing service (port 49152, or another dynamic port).

use std::collections::BTreeMap;
use std::net::Ipv6Addr;
use std::time::Duration;
use serde_json::{json, Value as Json};
use tokio::net::TcpStream;

use super::rppairing::{Control, VerifyError};
use super::tcp::{Reassembler, Stack, TcpConn};
use super::{tls_psk, xpc};
use crate::pairing::record::PairingRecord;
use crate::{logf, state};

pub const LOOPBACK: &str = "10.7.0.1";
pub const PAIRING_PORT: u16 = 49152;

pub struct Session {
    _control: Control,
    stack: Stack,
    pub services: BTreeMap<String, u16>,
    pub properties: plist::Dictionary,
    pub udid: String,
}

#[derive(Debug)]
pub enum OpenError {
    NoVpn(String),
    NotPaired,
    Failed(String),
}

impl std::fmt::Display for OpenError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OpenError::NoVpn(why) => write!(f, "LocalDevVPN is not connected ({why})"),
            OpenError::NotPaired => write!(f, "this iPhone no longer accepts BariStore's pairing; pair again"),
            OpenError::Failed(why) => write!(f, "{why}"),
        }
    }
}

/// The host UUID every RemoteXPC handshake presents (fixed per install).
pub fn host_uuid() -> [u8; 16] {
    let path = state::path("host-uuid");
    if let Ok(text) = std::fs::read_to_string(&path) {
        if let Ok(u) = uuid::Uuid::parse_str(text.trim()) {
            return *u.as_bytes();
        }
    }
    let u = uuid::Uuid::new_v4();
    let _ = state::write_private(&path, u.to_string().as_bytes());
    *u.as_bytes()
}

/// Is LocalDevVPN answering? Through it 10.7.0.1 either accepts or refuses
/// at once; without it the connection goes nowhere.
pub async fn vpn_up() -> bool {
    let (host, port) = override_endpoint().unwrap_or((LOOPBACK.to_string(), PAIRING_PORT));
    match tokio::time::timeout(Duration::from_secs(3), TcpStream::connect((host.as_str(), port))).await {
        Ok(Ok(_)) => true,
        Ok(Err(e)) => e.kind() == std::io::ErrorKind::ConnectionRefused,
        Err(_) => false,
    }
}

/// BARISTORE_DEVICE=host:port points at a mock device in tests.
fn override_endpoint() -> Option<(String, u16)> {
    let v = std::env::var("BARISTORE_DEVICE").ok()?;
    let (h, p) = v.rsplit_once(':')?;
    Some((h.to_string(), p.parse().ok()?))
}

/// Does `port` answer an RPPairing handshake (the RemotePairing service)?
async fn answers_pairing(host: &str, port: u16) -> bool {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let Ok(Ok(mut s)) = tokio::time::timeout(Duration::from_secs(2), TcpStream::connect((host, port))).await else {
        return false;
    };
    let body = serde_json::to_vec(&json!({"message": {"plain": {"_0": {"request": {"_0": {"handshake": {"_0": {
        "hostOptions": {"attemptPairVerify": true},
        "wireProtocolVersion": super::rppairing::WIRE_VERSION}}}}}}},
        "originatedBy": "host", "sequenceNumber": 0})).unwrap();
    let mut frame = b"RPPairing".to_vec();
    frame.extend_from_slice(&(body.len() as u16).to_be_bytes());
    frame.extend_from_slice(&body);
    let mut magic = [0u8; 9];
    s.write_all(&frame).await.is_ok()
        && matches!(tokio::time::timeout(Duration::from_secs(3), s.read_exact(&mut magic)).await, Ok(Ok(_)))
        && &magic == b"RPPairing"
}

/// Where the RemotePairing service answers. Usually 49152, but iOS takes
/// the first free dynamic port at boot, so when that one is not it, the
/// other dynamic ports are knocked 32 at a time (as siboot 0.19.1 does) and
/// the port found is remembered.
pub async fn endpoint() -> Result<(String, u16), OpenError> {
    if let Some(e) = override_endpoint() {
        return Ok(e);
    }
    let saved = std::fs::read_to_string(state::path("device-port")).ok()
        .and_then(|s| s.trim().parse::<u16>().ok());
    for port in saved.into_iter().chain([PAIRING_PORT]) {
        if answers_pairing(LOOPBACK, port).await {
            return Ok((LOOPBACK.to_string(), port));
        }
    }
    if !vpn_up().await {
        return Err(OpenError::NoVpn(format!("{LOOPBACK} does not answer")));
    }
    logf!("device: {PAIRING_PORT} is not the pairing service; knocking on the other dynamic ports");
    let ports: Vec<u16> = (PAIRING_PORT + 1..=u16::MAX).filter(|p| *p != 62078).collect();
    for chunk in ports.chunks(32) {
        let probes = chunk.iter().map(|&p| async move { (p, answers_pairing(LOOPBACK, p).await) });
        if let Some((port, _)) = futures_util::future::join_all(probes).await.into_iter().find(|(_, ok)| *ok) {
            logf!("device: the pairing service answers on {port}");
            let _ = std::fs::write(state::path("device-port"), port.to_string());
            return Ok((LOOPBACK.to_string(), port));
        }
    }
    Err(OpenError::Failed("LocalDevVPN is connected, but no port answers as the iPhone's pairing service. \
        Restart the iPhone with Developer Mode on, then try again".into()))
}

pub struct Tunnel {
    pub control: Control,
    pub stack: Stack,
    pub rsd_port: u16,
}

impl Tunnel {
    /// Pair-verify, createListener, TLS-PSK, CDTunnel, and our TCP stack on it.
    pub async fn open(record: &PairingRecord) -> Result<Tunnel, OpenError> {
        let (host, pairing_port) = endpoint().await?;
        let host = host.as_str();
        let mut control = match Control::open(host, pairing_port, record).await {
            Ok(c) => c,
            Err(VerifyError::NotPaired) => return Err(OpenError::NotPaired),
            Err(VerifyError::Other(why)) if why.contains("LocalDevVPN") => return Err(OpenError::NoVpn(why)),
            Err(VerifyError::Other(why)) => return Err(OpenError::Failed(format!("pair-verify: {why}"))),
        };
        logf!("device: pair-verify ok");
        let port = control.create_tcp_listener().await.map_err(|e| OpenError::Failed(format!("tunnel listener: {e}")))?;
        let tunnel = TcpStream::connect((host, port)).await
            .map_err(|e| OpenError::Failed(format!("tunnel port {port}: {e}")))?;
        let _ = tunnel.set_nodelay(true);
        let (r, w) = tunnel.into_split();
        let (mut reader, mut writer) = tls_psk::connect(r, w, &control.psk).await
            .map_err(|e| OpenError::Failed(format!("TLS-PSK: {e}")))?;
        logf!("device: TLS-PSK tunnel up on port {port}");

        let request = serde_json::to_vec(&json!({"type": "clientHandshakeRequest", "mtu": 16000})).unwrap();
        let mut hello = b"CDTunnel".to_vec();
        hello.extend_from_slice(&(request.len() as u16).to_be_bytes());
        hello.extend_from_slice(&request);
        writer.write(&hello).await.map_err(|e| OpenError::Failed(format!("CDTunnel: {e}")))?;
        let mut got = Vec::new();
        let (info, rest) = loop {
            got.extend(reader.read().await.map_err(|e| OpenError::Failed(format!("CDTunnel: {e}")))?);
            if got.len() >= 10 {
                if &got[..8] != b"CDTunnel" {
                    return Err(OpenError::Failed("CDTunnel answer without its magic".into()));
                }
                let len = u16::from_be_bytes([got[8], got[9]]) as usize;
                if got.len() >= 10 + len {
                    let info: Json = serde_json::from_slice(&got[10..10 + len])
                        .map_err(|e| OpenError::Failed(format!("CDTunnel JSON: {e}")))?;
                    break (info, got[10 + len..].to_vec());
                }
            }
        };
        let addr = |v: Option<&Json>| v.and_then(Json::as_str).and_then(|s| s.split('/').next())
            .and_then(|s| s.parse::<Ipv6Addr>().ok());
        let client = addr(info.pointer("/clientParameters/address"));
        let server = addr(info.get("serverAddress"));
        let rsd_port = info.get("serverRSDPort")
            .and_then(|p| p.as_u64().or_else(|| p.as_str().and_then(|s| s.parse().ok())))
            .and_then(|p| u16::try_from(p).ok());
        let (Some(client), Some(server), Some(rsd_port)) = (client, server, rsd_port) else {
            return Err(OpenError::Failed(format!("incomplete CDTunnel answer: {info}")));
        };
        logf!("device: tunnel {client} -> {server}, RSD port {rsd_port}");

        let (stack, mut outgoing) = Stack::start(client, server);
        let inbound = stack.clone();
        tokio::spawn(async move {
            let mut assembler = Reassembler::default();
            for p in assembler.push(&rest) {
                inbound.deliver(p);
            }
            loop {
                match reader.read().await {
                    Ok(data) => {
                        for p in assembler.push(&data) {
                            inbound.deliver(p);
                        }
                    }
                    Err(e) => {
                        logf!("device: tunnel closed: {e}");
                        inbound.link_down(format!("the tunnel closed: {e}"));
                        return;
                    }
                }
            }
        });
        tokio::spawn(async move {
            while let Some(first) = outgoing.recv().await {
                let mut batch = first;
                while batch.len() < 256 * 1024 {
                    match outgoing.try_recv() {
                        Ok(p) => batch.extend_from_slice(&p),
                        Err(_) => break,
                    }
                }
                if writer.write(&batch).await.is_err() {
                    return;
                }
            }
        });

        Ok(Tunnel { control, stack, rsd_port })
    }
}

impl Session {
    pub async fn open(record: &PairingRecord) -> Result<Session, OpenError> {
        let Tunnel { control, stack, rsd_port, .. } = Tunnel::open(record).await?;
        let rsd = stack.connect(rsd_port).await.map_err(|e| OpenError::Failed(format!("RSD: {e}")))?;
        let (services, properties) = xpc::rsd_handshake(rsd, host_uuid()).await.map_err(OpenError::Failed)?;
        let udid = properties.get("UniqueDeviceID").and_then(plist::Value::as_string)
            .unwrap_or(&record.peer.udid).to_string();
        logf!("device: RSD lists {} services", services.len());
        Ok(Session { _control: control, stack, services, properties, udid })
    }

    pub fn port(&self, names: &[&str]) -> Result<u16, String> {
        for name in names {
            if let Some(p) = self.services.get(*name) {
                return Ok(*p);
            }
        }
        Err(format!("the iPhone does not offer {} (Developer Mode off?)", names[0]))
    }

    pub async fn connect(&self, names: &[&str]) -> Result<TcpConn, String> {
        let port = self.port(names)?;
        self.stack.connect(port).await
    }
}
