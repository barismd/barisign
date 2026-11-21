//! RemoteXPC: XPC objects inside a minimal HTTP/2 client (no HPACK: every
//! HEADERS frame is empty). Used for RemoteServiceDiscovery, which hands out
//! the port of every service inside the tunnel.
//!
//! Handshake as jkcoxson/idevice performs it with iOS 17-27: SETTINGS
//! (max streams 100, window 1 MiB), WINDOW_UPDATE 983041, open stream 1,
//! root message {} (flags 0x1), open stream 3, reply init (0x400001),
//! root 0x201, then the "Handshake" dictionary with one fixed host UUID
//! (iOS 27.2+ tears down a tunnel's services when a second connection
//! presents a different one). Message id is always 1.

use plist::{Dictionary, Value};
use super::tcp::TcpConn;

const PREFACE: &[u8] = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n";
const DATA: u8 = 0;
const HEADERS: u8 = 1;
const RST_STREAM: u8 = 3;
const SETTINGS: u8 = 4;
const PING: u8 = 6;
const GOAWAY: u8 = 7;
const WINDOW_UPDATE: u8 = 8;
const ACK: u8 = 1;
const END_HEADERS: u8 = 4;
const ROOT: u32 = 1;
const REPLY: u32 = 3;
const MAX_FRAME: usize = 16384;

const WRAPPER_MAGIC: u32 = 0x29B0_0B92;
const PAYLOAD_MAGIC: u32 = 0x4213_3742;

#[derive(Clone, Debug, PartialEq)]
pub enum Xpc {
    Null,
    Bool(bool),
    Int(i64),
    Uint(u64),
    Double(f64),
    Date(u64),
    Data(Vec<u8>),
    String(String),
    Uuid([u8; 16]),
    Array(Vec<Xpc>),
    Dict(Vec<(String, Xpc)>),
}

fn pad4(out: &mut Vec<u8>) {
    while out.len() % 4 != 0 {
        out.push(0);
    }
}

impl Xpc {
    fn encode_into(&self, out: &mut Vec<u8>) {
        let tag = |out: &mut Vec<u8>, t: u32| out.extend_from_slice(&t.to_le_bytes());
        match self {
            Xpc::Null => tag(out, 0x1000),
            Xpc::Bool(b) => { tag(out, 0x2000); out.extend_from_slice(&[*b as u8, 0, 0, 0]); }
            Xpc::Int(v) => { tag(out, 0x3000); out.extend_from_slice(&v.to_le_bytes()); }
            Xpc::Uint(v) => { tag(out, 0x4000); out.extend_from_slice(&v.to_le_bytes()); }
            Xpc::Double(v) => { tag(out, 0x5000); out.extend_from_slice(&v.to_le_bytes()); }
            Xpc::Date(v) => { tag(out, 0x7000); out.extend_from_slice(&v.to_le_bytes()); }
            Xpc::Data(d) => {
                tag(out, 0x8000);
                out.extend_from_slice(&(d.len() as u32).to_le_bytes());
                out.extend_from_slice(d);
                pad4(out);
            }
            Xpc::String(s) => {
                tag(out, 0x9000);
                out.extend_from_slice(&(s.len() as u32 + 1).to_le_bytes());
                out.extend_from_slice(s.as_bytes());
                out.push(0);
                pad4(out);
            }
            Xpc::Uuid(u) => { tag(out, 0xA000); out.extend_from_slice(u); }
            Xpc::Array(items) => {
                tag(out, 0xE000);
                let mut body = (items.len() as u32).to_le_bytes().to_vec();
                for item in items {
                    item.encode_into(&mut body);
                }
                out.extend_from_slice(&(body.len() as u32).to_le_bytes());
                out.extend_from_slice(&body);
            }
            Xpc::Dict(entries) => {
                tag(out, 0xF000);
                let mut body = (entries.len() as u32).to_le_bytes().to_vec();
                for (k, v) in entries {
                    body.extend_from_slice(k.as_bytes());
                    body.push(0);
                    pad4(&mut body);
                    v.encode_into(&mut body);
                }
                out.extend_from_slice(&(body.len() as u32).to_le_bytes());
                out.extend_from_slice(&body);
            }
        }
    }

    pub fn encode_payload(&self) -> Vec<u8> {
        let mut out = PAYLOAD_MAGIC.to_le_bytes().to_vec();
        out.extend_from_slice(&5u32.to_le_bytes());
        self.encode_into(&mut out);
        out
    }

    pub fn decode_payload(data: &[u8]) -> Result<Xpc, String> {
        if data.len() < 8 || data[..4] != PAYLOAD_MAGIC.to_le_bytes() {
            return Err("bad XPC payload header".into());
        }
        let mut at = 8;
        decode(data, &mut at, 0)
    }

    pub fn to_plist(&self) -> Value {
        match self {
            Xpc::Null => Value::String(String::new()),
            Xpc::Bool(b) => Value::Boolean(*b),
            Xpc::Int(v) => Value::Integer((*v).into()),
            Xpc::Uint(v) | Xpc::Date(v) => Value::Integer((*v).into()),
            Xpc::Double(v) => Value::Real(*v),
            Xpc::Data(d) => Value::Data(d.clone()),
            Xpc::String(s) => Value::String(s.clone()),
            Xpc::Uuid(u) => Value::String(uuid::Uuid::from_bytes(*u).to_string()),
            Xpc::Array(items) => Value::Array(items.iter().map(Xpc::to_plist).collect()),
            Xpc::Dict(entries) => {
                let mut d = Dictionary::new();
                for (k, v) in entries {
                    d.insert(k.clone(), v.to_plist());
                }
                Value::Dictionary(d)
            }
        }
    }
}

fn take<'a>(data: &'a [u8], at: &mut usize, n: usize) -> Result<&'a [u8], String> {
    let end = at.checked_add(n).filter(|e| *e <= data.len()).ok_or("truncated XPC object")?;
    let s = &data[*at..end];
    *at = end;
    Ok(s)
}

fn u32le(data: &[u8], at: &mut usize) -> Result<u32, String> {
    Ok(u32::from_le_bytes(take(data, at, 4)?.try_into().unwrap()))
}

fn u64le(data: &[u8], at: &mut usize) -> Result<u64, String> {
    Ok(u64::from_le_bytes(take(data, at, 8)?.try_into().unwrap()))
}

fn skip_pad(at: &mut usize) {
    *at = (*at + 3) & !3;
}

fn decode(data: &[u8], at: &mut usize, depth: u32) -> Result<Xpc, String> {
    if depth > 32 {
        return Err("XPC nested too deeply".into());
    }
    let kind = u32le(data, at)?;
    Ok(match kind {
        0x1000 => Xpc::Null,
        0x2000 => { let v = take(data, at, 4)?[0] != 0; Xpc::Bool(v) }
        0x3000 => Xpc::Int(u64le(data, at)? as i64),
        0x4000 => Xpc::Uint(u64le(data, at)?),
        0x5000 => Xpc::Double(f64::from_bits(u64le(data, at)?)),
        0x7000 => Xpc::Date(u64le(data, at)?),
        0x8000 | 0x9000 => {
            let len = u32le(data, at)? as usize;
            let raw = take(data, at, len)?.to_vec();
            skip_pad(at);
            if kind == 0x8000 {
                Xpc::Data(raw)
            } else {
                let end = raw.iter().position(|b| *b == 0).unwrap_or(raw.len());
                Xpc::String(String::from_utf8_lossy(&raw[..end]).into_owned())
            }
        }
        0xA000 => Xpc::Uuid(take(data, at, 16)?.try_into().unwrap()),
        0xE000 => {
            let _len = u32le(data, at)?;
            let count = u32le(data, at)?;
            let mut items = Vec::new();
            for _ in 0..count {
                items.push(decode(data, at, depth + 1)?);
            }
            Xpc::Array(items)
        }
        0xF000 => {
            let _len = u32le(data, at)?;
            let count = u32le(data, at)?;
            let mut entries = Vec::new();
            for _ in 0..count {
                let rest = &data[*at..];
                let end = rest.iter().position(|b| *b == 0).ok_or("unterminated XPC key")?;
                let key = String::from_utf8_lossy(&rest[..end]).into_owned();
                *at += end + 1;
                skip_pad(at);
                entries.push((key, decode(data, at, depth + 1)?));
            }
            Xpc::Dict(entries)
        }
        other => return Err(format!("unknown XPC type 0x{other:X}")),
    })
}

fn wrapper(flags: u32, body: Option<&Xpc>) -> Vec<u8> {
    let payload = body.map(Xpc::encode_payload).unwrap_or_default();
    let mut out = WRAPPER_MAGIC.to_le_bytes().to_vec();
    out.extend_from_slice(&flags.to_le_bytes());
    out.extend_from_slice(&(payload.len() as u64).to_le_bytes());
    out.extend_from_slice(&1u64.to_le_bytes());
    out.extend_from_slice(&payload);
    out
}

pub struct RemoteXpc {
    conn: TcpConn,
    root: Vec<u8>,
    reply: Vec<u8>,
}

impl RemoteXpc {
    async fn frame(&mut self, kind: u8, flags: u8, stream: u32, payload: &[u8]) -> std::io::Result<()> {
        let mut out = (payload.len() as u32).to_be_bytes()[1..].to_vec();
        out.push(kind);
        out.push(flags);
        out.extend_from_slice(&stream.to_be_bytes());
        out.extend_from_slice(payload);
        self.conn.write_all(&out).await
    }

    async fn data(&mut self, stream: u32, payload: &[u8]) -> std::io::Result<()> {
        for chunk in payload.chunks(MAX_FRAME) {
            self.frame(DATA, 0, stream, chunk).await?;
        }
        Ok(())
    }

    pub async fn connect(conn: TcpConn) -> Result<RemoteXpc, String> {
        let mut x = RemoteXpc { conn, root: Vec::new(), reply: Vec::new() };
        let io = |e: std::io::Error| format!("RemoteXPC: {e}");
        x.conn.write_all(PREFACE).await.map_err(io)?;
        let mut settings = Vec::new();
        settings.extend_from_slice(&3u16.to_be_bytes());
        settings.extend_from_slice(&100u32.to_be_bytes());
        settings.extend_from_slice(&4u16.to_be_bytes());
        settings.extend_from_slice(&1_048_576u32.to_be_bytes());
        x.frame(SETTINGS, 0, 0, &settings).await.map_err(io)?;
        x.frame(WINDOW_UPDATE, 0, 0, &983_041u32.to_be_bytes()).await.map_err(io)?;
        x.frame(HEADERS, END_HEADERS, ROOT, &[]).await.map_err(io)?;
        x.data(ROOT, &wrapper(0x1, Some(&Xpc::Dict(vec![])))).await.map_err(io)?;
        x.frame(HEADERS, END_HEADERS, REPLY, &[]).await.map_err(io)?;
        x.data(REPLY, &wrapper(0x0040_0001, None)).await.map_err(io)?;
        x.data(ROOT, &wrapper(0x201, None)).await.map_err(io)?;
        Ok(x)
    }

    pub async fn send(&mut self, value: &Xpc, wanting_reply: bool) -> Result<(), String> {
        let mut flags = 0x101;
        if wanting_reply {
            flags |= 0x1_0000;
        }
        self.data(ROOT, &wrapper(flags, Some(value))).await.map_err(|e| format!("RemoteXPC: {e}"))
    }

    pub async fn send_device_handshake(&mut self, host_uuid: [u8; 16]) -> Result<(), String> {
        let message = Xpc::Dict(vec![
            ("MessageType".into(), Xpc::String("Handshake".into())),
            ("MessagingProtocolVersion".into(), Xpc::Uint(7)),
            ("UUID".into(), Xpc::Uuid(host_uuid)),
            ("Properties".into(), Xpc::Dict(vec![
                ("RemoteXPCVersionFlags".into(), Xpc::Uint(0x0100_0000_0000_0006)),
                ("SensitivePropertiesVisible".into(), Xpc::Bool(true)),
            ])),
            ("Services".into(), Xpc::Dict(vec![])),
        ]);
        self.send(&message, false).await
    }

    /// Next non-empty message from either channel.
    pub async fn recv(&mut self) -> Result<Xpc, String> {
        loop {
            for channel in [ROOT, REPLY] {
                let buf = if channel == ROOT { &mut self.root } else { &mut self.reply };
                if buf.len() >= 24 {
                    if buf[..4] != WRAPPER_MAGIC.to_le_bytes() {
                        return Err("bad XPC wrapper magic".into());
                    }
                    let size = u64::from_le_bytes(buf[8..16].try_into().unwrap()) as usize;
                    if buf.len() >= 24 + size {
                        let body: Vec<u8> = buf.drain(..24 + size).skip(24).collect();
                        if size > 0 {
                            let value = Xpc::decode_payload(&body)?;
                            if !matches!(&value, Xpc::Dict(e) if e.is_empty()) {
                                return Ok(value);
                            }
                        }
                        continue;
                    }
                }
            }
            self.read_frame().await?;
        }
    }

    async fn read_frame(&mut self) -> Result<(), String> {
        let io = |e: std::io::Error| format!("RemoteXPC: {e}");
        let header = self.conn.read_exact(9).await.map_err(io)?;
        let len = u32::from_be_bytes([0, header[0], header[1], header[2]]) as usize;
        let (kind, flags) = (header[3], header[4]);
        let stream = u32::from_be_bytes(header[5..9].try_into().unwrap()) & 0x7FFF_FFFF;
        let payload = if len > 0 { self.conn.read_exact(len).await.map_err(io)? } else { Vec::new() };
        match kind {
            SETTINGS if flags & ACK == 0 => self.frame(SETTINGS, ACK, 0, &[]).await.map_err(io)?,
            PING if flags & ACK == 0 => self.frame(PING, ACK, 0, &payload).await.map_err(io)?,
            DATA => {
                if !payload.is_empty() {
                    let n = (payload.len() as u32).to_be_bytes();
                    self.frame(WINDOW_UPDATE, 0, 0, &n).await.map_err(io)?;
                    self.frame(WINDOW_UPDATE, 0, stream, &n).await.map_err(io)?;
                }
                match stream {
                    ROOT => self.root.extend_from_slice(&payload),
                    REPLY => self.reply.extend_from_slice(&payload),
                    _ => {}
                }
            }
            RST_STREAM => return Err(format!("the iPhone reset XPC stream {stream}")),
            GOAWAY => return Err(format!(
                "the iPhone closed RemoteXPC: {}", String::from_utf8_lossy(payload.get(8..).unwrap_or(&[])))),
            _ => {}
        }
        Ok(())
    }
}

/// RSD: returns (services name -> port, device properties).
pub async fn rsd_handshake(conn: TcpConn, host_uuid: [u8; 16])
    -> Result<(std::collections::BTreeMap<String, u16>, Dictionary), String>
{
    let mut x = RemoteXpc::connect(conn).await?;
    x.send_device_handshake(host_uuid).await?;
    let peer = tokio::time::timeout(std::time::Duration::from_secs(20), x.recv())
        .await
        .map_err(|_| "RSD did not answer within 20 s".to_string())??
        .to_plist();
    let peer = peer.into_dictionary().ok_or("RSD answer is not a dictionary")?;
    let mut services = std::collections::BTreeMap::new();
    if let Some(Value::Dictionary(map)) = peer.get("Services") {
        for (name, entry) in map {
            let port = entry.as_dictionary().and_then(|d| d.get("Port")).and_then(|p| match p {
                Value::String(s) => s.parse::<u16>().ok(),
                Value::Integer(i) => i.as_unsigned().and_then(|v| u16::try_from(v).ok()),
                _ => None,
            });
            if let Some(port) = port {
                services.insert(name.clone(), port);
            }
        }
    } else {
        return Err("RSD answer has no Services".into());
    }
    let properties = peer.get("Properties").and_then(Value::as_dictionary).cloned().unwrap_or_default();
    Ok((services, properties))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip() {
        let v = Xpc::Dict(vec![
            ("MessageType".into(), Xpc::String("Handshake".into())),
            ("n".into(), Xpc::Uint(7)),
            ("u".into(), Xpc::Uuid([1; 16])),
            ("ok".into(), Xpc::Bool(true)),
            ("list".into(), Xpc::Array(vec![Xpc::Int(-3), Xpc::Data(vec![1, 2, 3])])),
            ("empty".into(), Xpc::Dict(vec![])),
        ]);
        assert_eq!(Xpc::decode_payload(&v.encode_payload()).unwrap(), v);
    }
}
