//! Lockdown-style services reached through RSD ("*.shim.remote"): u32 BE
//! length + XML plist, opened with the RSDCheckin exchange. On top: AFC,
//! installation_proxy, house_arrest, lockdown GetValue and AMFI actions.

use plist::{Dictionary, Value};
use super::session::Session;
use super::tcp::TcpConn;

pub const LOCKDOWN: &[&str] = &["com.apple.mobile.lockdown.remote.trusted", "com.apple.mobile.lockdown.remote.untrusted"];
pub const AFC: &[&str] = &["com.apple.afc.shim.remote"];
pub const INSTALL: &[&str] = &["com.apple.mobile.installation_proxy.shim.remote"];
pub const HOUSE_ARREST: &[&str] = &["com.apple.mobile.house_arrest.shim.remote"];
pub const AMFI: &[&str] = &["com.apple.amfi.lockdown.shim.remote"];

fn io(e: std::io::Error) -> String {
    e.to_string()
}

pub struct ServiceConn {
    conn: TcpConn,
}

impl ServiceConn {
    pub async fn open(session: &Session, names: &[&str]) -> Result<ServiceConn, String> {
        let conn = session.connect(names).await?;
        let mut s = ServiceConn { conn };
        let mut checkin = Dictionary::new();
        checkin.insert("Label".into(), Value::String("BariStore".into()));
        checkin.insert("ProtocolVersion".into(), Value::String("2".into()));
        checkin.insert("Request".into(), Value::String("RSDCheckin".into()));
        s.send(&checkin).await?;
        let first = s.recv().await?;
        if first.get("Request").and_then(Value::as_string) != Some("RSDCheckin") {
            return Err(format!("unexpected RSDCheckin answer: {first:?}"));
        }
        let second = s.recv().await?;
        if let Some(e) = second.get("Error") {
            return Err(format!("the iPhone refused {}: {e:?}", names[0]));
        }
        if second.get("Request").and_then(Value::as_string) != Some("StartService") {
            return Err(format!("unexpected StartService answer: {second:?}"));
        }
        Ok(s)
    }

    pub async fn send(&mut self, value: &Dictionary) -> Result<(), String> {
        let mut body = Vec::new();
        plist::to_writer_xml(&mut body, &Value::Dictionary(value.clone())).map_err(|e| e.to_string())?;
        let mut frame = (body.len() as u32).to_be_bytes().to_vec();
        frame.extend_from_slice(&body);
        self.conn.write_all(&frame).await.map_err(io)
    }

    pub async fn recv(&mut self) -> Result<Dictionary, String> {
        let head = self.conn.read_exact(4).await.map_err(io)?;
        let len = u32::from_be_bytes(head.try_into().unwrap()) as usize;
        if len == 0 || len > 64 << 20 {
            return Err(format!("bad plist length {len}"));
        }
        let body = self.conn.read_exact(len).await.map_err(io)?;
        plist::from_bytes::<Value>(&body)
            .map_err(|e| e.to_string())?
            .into_dictionary()
            .ok_or_else(|| "service answered with a non-dictionary".into())
    }

    pub async fn request(&mut self, value: &Dictionary) -> Result<Dictionary, String> {
        self.send(value).await?;
        self.recv().await
    }
}

fn dict(pairs: &[(&str, Value)]) -> Dictionary {
    let mut d = Dictionary::new();
    for (k, v) in pairs {
        d.insert((*k).into(), v.clone());
    }
    d
}

// --- lockdown / AMFI -------------------------------------------------------

/// Some(true/false) for Developer Mode, None when the iPhone does not say.
pub async fn developer_mode(session: &Session) -> Result<Option<bool>, String> {
    let mut s = ServiceConn::open(session, LOCKDOWN).await?;
    let reply = s.request(&dict(&[
        ("Label", Value::String("BariStore".into())),
        ("Request", Value::String("GetValue".into())),
        ("Domain", Value::String("com.apple.security.mac.amfi".into())),
        ("Key", Value::String("DeveloperModeStatus".into())),
    ])).await?;
    Ok(reply.get("Value").and_then(Value::as_boolean))
}

/// AMFI action 0 reveals the Developer Mode switch in Settings (no restart);
/// action 1 turns it on and restarts (refused while a passcode is set).
/// iOS closes the service after one request, so each call opens a new one.
pub async fn amfi_action(session: &Session, action: u64) -> Result<(), String> {
    let mut s = ServiceConn::open(session, AMFI).await?;
    match s.request(&dict(&[("action", Value::Integer(action.into()))])).await {
        Ok(reply) if reply.get("success").and_then(Value::as_boolean) == Some(true) => Ok(()),
        Ok(reply) => Err(reply.get("Error").or(reply.get("error"))
            .map(|e| format!("{e:?}")).unwrap_or_else(|| "refused".into())),
        // turn-on restarts before answering
        Err(_) if action == 1 => Ok(()),
        Err(e) => Err(e),
    }
}

// --- AFC -------------------------------------------------------------------

const AFC_MAGIC: &[u8; 8] = b"CFA6LPAA";
const OP_STATUS: u64 = 0x01;
const OP_DATA: u64 = 0x02;
const OP_REMOVE: u64 = 0x08;
const OP_MKDIR: u64 = 0x09;
const OP_OPEN: u64 = 0x0D;
const OP_OPEN_RESULT: u64 = 0x0E;
const OP_READ: u64 = 0x0F;
const OP_WRITE: u64 = 0x10;
const OP_CLOSE: u64 = 0x14;
pub const READ: u64 = 1;
pub const WRITE_TRUNCATE: u64 = 3;

pub struct Afc {
    s: ServiceConn,
    packet: u64,
}

impl Afc {
    pub async fn open(session: &Session) -> Result<Afc, String> {
        Ok(Afc { s: ServiceConn::open(session, AFC).await?, packet: 0 })
    }

    pub fn from_service(s: ServiceConn) -> Afc {
        Afc { s, packet: 0 }
    }

    async fn op(&mut self, op: u64, header_payload: &[u8], data: &[u8]) -> Result<Vec<u8>, String> {
        let this = 40 + header_payload.len() as u64;
        let total = this + data.len() as u64;
        let mut out = AFC_MAGIC.to_vec();
        for v in [total, this, self.packet, op] {
            out.extend_from_slice(&v.to_le_bytes());
        }
        out.extend_from_slice(header_payload);
        out.extend_from_slice(data);
        self.packet += 1;
        self.s.conn.write_all(&out).await.map_err(io)?;
        let head = self.s.conn.read_exact(40).await.map_err(io)?;
        if &head[..8] != AFC_MAGIC {
            return Err("bad AFC reply".into());
        }
        let entire = u64::from_le_bytes(head[8..16].try_into().unwrap());
        let rop = u64::from_le_bytes(head[32..40].try_into().unwrap());
        if !(40..=(64 << 20)).contains(&entire) {
            return Err(format!("bad AFC length {entire}"));
        }
        let body = if entire > 40 { self.s.conn.read_exact((entire - 40) as usize).await.map_err(io)? } else { Vec::new() };
        match rop {
            OP_STATUS => {
                let code = body.get(..8).map(|b| u64::from_le_bytes(b.try_into().unwrap())).unwrap_or(0);
                if code != 0 {
                    return Err(format!("AFC error {code} (operation 0x{op:X})"));
                }
                Ok(Vec::new())
            }
            OP_DATA | OP_OPEN_RESULT => Ok(body),
            other => Err(format!("unexpected AFC reply 0x{other:X}")),
        }
    }

    fn path(p: &str) -> Vec<u8> {
        let mut v = p.as_bytes().to_vec();
        v.push(0);
        v
    }

    pub async fn mkdir(&mut self, path: &str) -> Result<(), String> {
        self.op(OP_MKDIR, &Self::path(path), &[]).await.map(|_| ())
    }

    pub async fn remove(&mut self, path: &str) -> Result<(), String> {
        self.op(OP_REMOVE, &Self::path(path), &[]).await.map(|_| ())
    }

    async fn fopen(&mut self, path: &str, mode: u64) -> Result<u64, String> {
        let mut payload = mode.to_le_bytes().to_vec();
        payload.extend_from_slice(&Self::path(path));
        let body = self.op(OP_OPEN, &payload, &[]).await?;
        body.get(..8).map(|b| u64::from_le_bytes(b.try_into().unwrap())).ok_or_else(|| "AFC open without handle".into())
    }

    async fn fclose(&mut self, handle: u64) -> Result<(), String> {
        self.op(OP_CLOSE, &handle.to_le_bytes(), &[]).await.map(|_| ())
    }

    pub async fn write_file(&mut self, path: &str, data: &[u8], progress: &mut (dyn FnMut(usize, usize) + Send))
        -> Result<(), String>
    {
        let handle = self.fopen(path, WRITE_TRUNCATE).await?;
        let mut sent = 0;
        // 1 MiB per write, as libimobiledevice's afcclient and idevice use.
        for chunk in data.chunks(1 << 20) {
            if let Err(e) = self.op(OP_WRITE, &handle.to_le_bytes(), chunk).await {
                let _ = self.fclose(handle).await;
                return Err(e);
            }
            sent += chunk.len();
            progress(sent, data.len());
        }
        self.fclose(handle).await
    }

    pub async fn read_file(&mut self, path: &str, limit: usize) -> Result<Vec<u8>, String> {
        let handle = self.fopen(path, READ).await?;
        let mut out = Vec::new();
        while out.len() < limit {
            let mut req = handle.to_le_bytes().to_vec();
            req.extend_from_slice(&(256u64 * 1024).to_le_bytes());
            let block = self.op(OP_READ, &req, &[]).await?;
            if block.is_empty() {
                break;
            }
            out.extend_from_slice(&block);
        }
        let _ = self.fclose(handle).await;
        Ok(out)
    }
}

// --- installation_proxy ----------------------------------------------------

/// Uploads `ipa` to /PublicStaging and installs it as a developer package.
pub async fn install(session: &Session, ipa: &[u8], progress: &mut (dyn FnMut(&str, u32) + Send)) -> Result<(), String> {
    let remote = format!("/PublicStaging/baristore-{}.ipa", &uuid::Uuid::new_v4().simple().to_string()[..12]);
    {
        let mut afc = Afc::open(session).await?;
        let _ = afc.mkdir("/PublicStaging").await;
        afc.write_file(&remote, ipa, &mut |done, total| {
            progress("Uploading", (done * 100 / total.max(1)) as u32);
        }).await?;
    }
    let result = async {
        let mut s = ServiceConn::open(session, INSTALL).await?;
        s.send(&dict(&[
            ("Command", Value::String("Install".into())),
            ("ClientOptions", Value::Dictionary(dict(&[("PackageType", Value::String("Developer".into()))]))),
            ("PackagePath", Value::String(remote.clone())),
        ])).await?;
        loop {
            let reply = s.recv().await?;
            if let Some(e) = reply.get("Error") {
                let detail = reply.get("ErrorDescription").and_then(Value::as_string).unwrap_or("");
                return Err(format!("install failed: {} {detail}", e.as_string().unwrap_or("error")));
            }
            let status = reply.get("Status").and_then(Value::as_string).unwrap_or("");
            if status == "Complete" {
                return Ok(());
            }
            let pct = reply.get("PercentComplete").and_then(Value::as_unsigned_integer).unwrap_or(0);
            progress(status, pct as u32);
        }
    }.await;
    if let Ok(mut afc) = Afc::open(session).await {
        let _ = afc.remove(&remote).await;
    }
    result
}

pub async fn uninstall(session: &Session, bundle_id: &str) -> Result<(), String> {
    let mut s = ServiceConn::open(session, INSTALL).await?;
    s.send(&dict(&[
        ("Command", Value::String("Uninstall".into())),
        ("ApplicationIdentifier", Value::String(bundle_id.into())),
    ])).await?;
    loop {
        let reply = s.recv().await?;
        if let Some(e) = reply.get("Error") {
            return Err(format!("uninstall failed: {e:?}"));
        }
        if reply.get("Status").and_then(Value::as_string) == Some("Complete") {
            return Ok(());
        }
    }
}

/// User apps: bundle id -> Info.plist subset (name, version, signer).
pub async fn installed_apps(session: &Session) -> Result<Vec<Dictionary>, String> {
    let mut s = ServiceConn::open(session, INSTALL).await?;
    let attrs: Vec<Value> = ["CFBundleIdentifier", "CFBundleDisplayName", "CFBundleName",
        "CFBundleShortVersionString", "CFBundleVersion", "SignerIdentity", "ProfileValidated", "Path"]
        .iter().map(|s| Value::String((*s).into())).collect();
    s.send(&dict(&[
        ("Command", Value::String("Browse".into())),
        ("ClientOptions", Value::Dictionary(dict(&[
            ("ApplicationType", Value::String("User".into())),
            ("ReturnAttributes", Value::Array(attrs)),
        ]))),
    ])).await?;
    let mut apps = Vec::new();
    loop {
        let reply = s.recv().await?;
        if let Some(Value::Array(list)) = reply.get("CurrentList") {
            apps.extend(list.iter().filter_map(|v| v.as_dictionary().cloned()));
        }
        if let Some(e) = reply.get("Error") {
            return Err(format!("listing apps failed: {e:?}"));
        }
        if reply.get("Status").and_then(Value::as_string) == Some("Complete") {
            return Ok(apps);
        }
    }
}

// --- house_arrest ----------------------------------------------------------

/// AFC rooted in an app's container (VendContainer) or Documents (VendDocuments).
pub async fn app_container(session: &Session, bundle_id: &str, documents_only: bool) -> Result<Afc, String> {
    let mut s = ServiceConn::open(session, HOUSE_ARREST).await?;
    let reply = s.request(&dict(&[
        ("Command", Value::String(if documents_only { "VendDocuments" } else { "VendContainer" }.into())),
        ("Identifier", Value::String(bundle_id.into())),
    ])).await?;
    if let Some(e) = reply.get("Error") {
        return Err(format!("cannot open the container of {bundle_id}: {e:?}"));
    }
    Ok(Afc::from_service(s))
}
