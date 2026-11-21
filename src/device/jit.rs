//! JIT for apps signed with get-task-allow (every app BariStore installs),
//! the way StikDebug does it over the same pairing and LocalDevVPN:
//!
//! 1. The Developer Disk Image: on iOS 26.4+ a cryptex, installed through
//!    cryptexd (`com.apple.security.cryptexd.remote`) with a Cryptex1 ticket
//!    Apple's TSS signs for this iPhone. The files come from
//!    doronz88/DeveloperDiskImage, as StikDebug downloads them.
//! 2. The app is launched stopped through CoreDevice's app service.
//! 3. debugserver (`com.apple.internal.dt.remote.debugproxy`, there once the
//!    image is installed) attaches. Apps that ask for JIT pages the iOS 26
//!    way (`brk #0xf00d`, StikDebug's universal.js, or the older `brk #0x69`,
//!    legacy.js) are served until they detach; any other app is detached
//!    at once, which is all JIT needed before iOS 26.
//!
//! Protocols read from jkcoxson/idevice (MIT): cryptexd, tss, core_device
//! app_service, debug_proxy. Script behaviour from StikDebug (AGPL), not code.

use plist::{Dictionary, Value};
use std::time::Duration;

use super::session::Session;
use super::tcp::TcpConn;
use super::xpc::{RemoteXpc, Xpc};
use crate::{logf, state};

const DDI_BASE: &str =
    "https://raw.githubusercontent.com/doronz88/DeveloperDiskImage/main/PersonalizedImages/Xcode_iOS_DDI_Cryptex";
const TSS_URL: &str = "http://gs.apple.com/TSS/controller?action=2";
const CRYPTEXD: &str = "com.apple.security.cryptexd.remote";
const APP_SERVICE: &str = "com.apple.coredevice.appservice";
const DEBUG_PROXY: &str = "com.apple.internal.dt.remote.debugproxy";
const DDI_IDENTIFIER: &str = "com.apple.MobileAsset.DDI";
/// The four payloads a cryptex install pushes besides the ticket.
const PAYLOADS: [&str; 4] = ["Cryptex1,GenericDmg", "Cryptex1,GenericTrustCache", "Cryptex1,CryptexInfoPlist",
                             "Cryptex1,GenericVolume"];
/// Apps StikDebug serves with universal.js (they ask for JIT through brk).
const UNIVERSAL: [&str; 16] = ["Amethyst", "Andarium", "MeloNX", "XeniOS", "MeloCafe", "Manic EMU", "DukeX",
    "TachyonU", "touchHLE", "HyperHLE", "Applesauce", "RPCS3", "AetherPS4", "Husk", "Utsuro", "Henge"];
/// Apps StikDebug serves with legacy.js (one `brk #0x69` region request).
const LEGACY: [&str; 4] = ["UTM", "DolphiniOS", "Flycast", "ARMSX2 iOS"];

fn d(pairs: Vec<(&str, Xpc)>) -> Xpc {
    Xpc::Dict(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

fn get<'a>(v: &'a Dictionary, k: &str) -> Option<&'a Value> {
    v.get(k)
}

// ------------------------------------------------------------------ cryptexd

/// One cryptexd routine on its own connection (the daemon serves one each).
async fn cryptexd(session: &Session, routine: &str, argv: Xpc, files: &[(u64, &[u8])]) -> Result<Option<Value>, String> {
    let mut x = RemoteXpc::connect(session.connect(&[CRYPTEXD]).await?).await?;
    x.send(&d(vec![("routine", Xpc::String(routine.into())), ("argv", argv)]), true).await?;
    for (id, data) in files {
        x.send_file(*id, data).await?;
    }
    let reply = tokio::time::timeout(Duration::from_secs(300), x.recv()).await
        .map_err(|_| format!("cryptexd did not answer {routine}"))??.to_plist();
    let mut reply = reply.into_dictionary().ok_or("cryptexd answered without a dictionary")?;
    if let Some(err) = reply.get("cferr").and_then(Value::as_dictionary) {
        let text = err.get("cferr_userinfo").and_then(Value::as_dictionary)
            .and_then(|u| u.get("NSLocalizedDescription")).and_then(Value::as_string).unwrap_or("error");
        return Err(format!("cryptexd {routine}: {text}"));
    }
    match reply.get("error").and_then(Value::as_signed_integer) {
        Some(0) | None => Ok(reply.remove("argv")),
        Some(code) => Err(format!("cryptexd {routine} failed (errno {code})")),
    }
}

async fn ddi_installed(session: &Session) -> Result<bool, String> {
    let argv = cryptexd(session, "copy-installed", d(vec![]), &[]).await?;
    Ok(argv.as_ref().and_then(Value::as_dictionary).and_then(|a| get(a, "remote-cryptex-array"))
        .and_then(Value::as_array).into_iter().flatten()
        .any(|c| c.as_dictionary().and_then(|c| get(c, "remote-cryptex-identifier")).and_then(Value::as_string)
             == Some(DDI_IDENTIFIER)))
}

/// The build identity whose variant is the developer disk image cryptex.
fn cryptex_identity(manifest: &Dictionary) -> Result<Dictionary, String> {
    get(manifest, "BuildIdentities").and_then(Value::as_array).into_iter().flatten()
        .filter_map(Value::as_dictionary)
        .find(|i| get(i, "Info").and_then(Value::as_dictionary).and_then(|i| get(i, "Variant"))
            .and_then(Value::as_string).is_some_and(|v| v.ends_with("Developer Disk Image Cryptex")))
        .cloned().ok_or_else(|| "the disk image's BuildManifest has no cryptex identity".into())
}

fn manifest_entry<'a>(identity: &'a Dictionary, key: &str) -> Option<&'a Dictionary> {
    get(identity, "Manifest").and_then(Value::as_dictionary).and_then(|m| get(m, key)).and_then(Value::as_dictionary)
}

fn number(v: Option<&Value>, key: &str) -> Result<u64, String> {
    match v {
        Some(Value::Integer(i)) => i.as_unsigned().ok_or(format!("{key} is negative")),
        Some(Value::String(s)) => {
            let t = s.trim_start_matches("0x");
            u64::from_str_radix(t, if t.len() == s.len() { 10 } else { 16 }).map_err(|_| format!("{key}: {s}"))
        }
        _ => Err(format!("the cryptex identity has no {key}")),
    }
}

/// Downloads (once, kept in ~/.baristore/ddi) the manifest and its payloads.
async fn ddi_files(progress: &(dyn Fn(String) + Send + Sync)) -> Result<(Dictionary, Vec<Vec<u8>>), String> {
    let dir = state::path("ddi");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let http = crate::store::client();
    let fetch = |name: String| {
        let http = http.clone();
        let path = dir.join(&name);
        async move {
            if let Ok(bytes) = std::fs::read(&path) {
                if !bytes.is_empty() {
                    return Ok::<Vec<u8>, String>(bytes);
                }
            }
            let bytes = http.get(format!("{DDI_BASE}/{name}")).timeout(Duration::from_secs(900)).send().await
                .and_then(|r| r.error_for_status()).map_err(|e| format!("disk image download failed: {e}"))?
                .bytes().await.map_err(|e| format!("disk image download failed: {e}"))?.to_vec();
            std::fs::write(&path, &bytes).map_err(|e| e.to_string())?;
            Ok(bytes)
        }
    };
    progress("Downloading the developer disk image".into());
    let manifest: Dictionary = plist::from_bytes(&fetch("BuildManifest.plist".into()).await?)
        .map_err(|e| format!("BuildManifest: {e}"))?;
    let identity = cryptex_identity(&manifest)?;
    let mut payloads = Vec::new();
    for key in PAYLOADS {
        let name = manifest_entry(&identity, key).and_then(|e| get(e, "Info")).and_then(Value::as_dictionary)
            .and_then(|i| get(i, "Path")).and_then(Value::as_string)
            .ok_or(format!("the manifest names no file for {key}"))?;
        payloads.push(fetch(name.to_string()).await?);
    }
    Ok((identity, payloads))
}

/// Apple's Cryptex1 ticket for this iPhone and nonce (TSS).
async fn cryptex_ticket(identity: &Dictionary, chip: &Dictionary, nonce: &[u8]) -> Result<Vec<u8>, String> {
    let mut req = Dictionary::new();
    let mut put = |k: &str, v: Value| { req.insert(k.into(), v); };
    put("@HostPlatformInfo", Value::String("mac".into()));
    put("@VersionInfo", Value::String("libauthinstall-1033.0.2".into()));
    put("@UUID", Value::String(uuid::Uuid::new_v4().to_string().to_uppercase()));
    put("@Cryptex1,Ticket", Value::Boolean(true));
    put("Cryptex1,ChipID", Value::Integer(number(get(identity, "Cryptex1,ChipID"), "Cryptex1,ChipID")?.into()));
    put("Cryptex1,ProductClass", Value::Integer(number(get(identity, "Cryptex1,ProductClass"), "Cryptex1,ProductClass")?.into()));
    for key in ["Cryptex1,Type", "Cryptex1,SubType", "Cryptex1,UseProductClass", "Cryptex1,NonceDomain",
                "Cryptex1,Version", "Cryptex1,PreauthorizationVersion"] {
        put(key, get(identity, key).cloned().ok_or(format!("the cryptex identity has no {key}"))?);
    }
    put("Cryptex1,Nonce", Value::Data(nonce.to_vec()));
    let production = match get(chip, "img4_chip_cpro") {
        Some(Value::Boolean(b)) => *b,
        Some(Value::Integer(i)) => i.as_unsigned().unwrap_or(0) != 0,
        _ => return Err("the iPhone did not say whether it is a production device".into()),
    };
    put("Cryptex1,ProductionMode", Value::Boolean(production));
    let field = |k: &str| get(chip, k).and_then(Value::as_unsigned_integer).ok_or(format!("the iPhone sent no {k}"));
    let mut udid = field("img4_chip_chip")?.to_be_bytes().to_vec();
    udid.extend_from_slice(&field("img4_chip_ecid")?.to_be_bytes());
    put("Cryptex1,UDID", Value::Data(udid));
    put("Cryptex1,UniqueTagList", Value::Data(Vec::new()));
    if let Some(manifest) = get(identity, "Manifest").and_then(Value::as_dictionary) {
        for (key, entry) in manifest.iter().filter(|(k, _)| k.starts_with("Cryptex1,")) {
            let Some(entry) = entry.as_dictionary() else { continue };
            let personalize = get(entry, "Info").and_then(Value::as_dictionary).and_then(|i| get(i, "Personalize"))
                .and_then(Value::as_boolean).unwrap_or(false);
            if let (true, Some(digest)) = (personalize, get(entry, "Digest")) {
                let mut e = Dictionary::new();
                e.insert("Digest".into(), digest.clone());
                req.insert(key.clone(), Value::Dictionary(e));
            }
        }
    }
    let body = crate::pairing::record::plist_xml(&req);
    let text = crate::store::client().post(TSS_URL)
        .header("Cache-Control", "no-cache").header("Content-type", "text/xml; charset=\"utf-8\"")
        .header("User-Agent", "InetURL/1.0").body(body).timeout(Duration::from_secs(60)).send().await
        .map_err(|e| format!("Apple's signing server: {e}"))?.text().await.map_err(|e| e.to_string())?;
    let Some((_, plist_text)) = text.split_once("REQUEST_STRING=") else {
        let why = text.split("&REQUEST_STRING=").next().unwrap_or(&text).chars().take(200).collect::<String>();
        return Err(format!("Apple's signing server refused the disk image ({why})"));
    };
    let reply: Dictionary = plist::from_bytes(plist_text.as_bytes()).map_err(|e| format!("TSS answer: {e}"))?;
    reply.get("Cryptex1,Ticket").or_else(|| reply.get("ApImg4Ticket")).and_then(Value::as_data).map(<[u8]>::to_vec)
        .ok_or_else(|| "Apple's answer has no Cryptex1 ticket".into())
}

/// Installs the developer disk image unless it already is. True when it was
/// installed now (the RSD service list then needs a fresh session).
pub async fn ensure_ddi(session: &Session, progress: &(dyn Fn(String) + Send + Sync)) -> Result<bool, String> {
    if ddi_installed(session).await? {
        return Ok(false);
    }
    let (identity, payloads) = ddi_files(progress).await?;
    progress("Asking Apple to sign the disk image for this iPhone".into());
    let chip = cryptexd(session, "read-personalization-id", d(vec![]), &[]).await?
        .and_then(Value::into_dictionary).ok_or("cryptexd sent no personalization identifiers")?;
    let domain = number(get(&identity, "Cryptex1,NonceDomain"), "Cryptex1,NonceDomain")?;
    let blob = cryptexd(session, "get-nonce", d(vec![("nonce-domain-handle", Xpc::Uint(domain))]), &[]).await?
        .and_then(Value::into_dictionary).and_then(|a| a.get("nonce").and_then(Value::as_data).map(<[u8]>::to_vec))
        .ok_or("cryptexd sent no nonce")?;
    // A 2-byte lead, the nonce, then its length as little-endian u32.
    let len = blob.len().checked_sub(4).map(|at| u32::from_le_bytes(blob[at..].try_into().unwrap()) as usize)
        .filter(|len| blob.len() >= 6 && 2 + len <= blob.len()).ok_or("cryptexd sent a malformed nonce")?;
    let ticket = cryptex_ticket(&identity, &chip, &blob[2..2 + len]).await?;
    progress("Installing the developer disk image".into());
    let uint = |k: &str| get(&identity, k).and_then(Value::as_unsigned_integer).map(Xpc::Uint)
        .ok_or(format!("the cryptex identity has no {k}"));
    let pass = |k: &str| get(&identity, k).map(Xpc::from_plist).ok_or(format!("the cryptex identity has no {k}"));
    let properties = d(vec![
        ("Cryptex1,UseProductClass", pass("Cryptex1,UseProductClass")?),
        ("MountedCryptex", Xpc::Bool(false)),
        ("Cryptex1,SubType", uint("Cryptex1,SubType")?),
        ("Cryptex1,NonceDomain", uint("Cryptex1,NonceDomain")?),
        ("Cryptex1,Version", pass("Cryptex1,Version")?),
        ("Cryptex1,PreauthVersion", pass("Cryptex1,PreauthorizationVersion")?),
    ]);
    // image, trustcache, ticket, info, volume hash: transfer ids 1-5.
    let files: [&[u8]; 5] = [&payloads[0], &payloads[1], &ticket, &payloads[2], &payloads[3]];
    let mut argv = vec![
        ("auth", Xpc::Uint(0)), ("client-version", Xpc::Uint(3)), ("cryptex1-properties", properties),
        ("image-type-index", Xpc::Int(10)), ("nonce-persistence", Xpc::Uint(1)), ("persistence", Xpc::Uint(2)),
    ];
    for (i, key) in ["image", "trustcache", "im4m", "info", "volumehash"].into_iter().enumerate() {
        argv.push((key, Xpc::FileTransfer { id: i as u64 + 1, size: files[i].len() as u64 }));
    }
    let transfers: Vec<(u64, &[u8])> = files.iter().enumerate().map(|(i, f)| (i as u64 + 1, *f)).collect();
    cryptexd(session, "install", d(argv), &transfers).await?;
    logf!("jit: developer disk image installed");
    Ok(true)
}

// ------------------------------------------------------------- app service

/// Launches `bundle_id` stopped; returns its pid.
async fn launch_stopped(session: &Session, bundle_id: &str) -> Result<u64, String> {
    let mut x = RemoteXpc::connect(session.connect(&[APP_SERVICE]).await?).await?;
    let version = d(vec![
        ("originalComponentsCount", Xpc::Int(2)),
        ("components", Xpc::Array(vec![Xpc::Uint(629), Xpc::Uint(3)])),
        ("stringValue", Xpc::String("629.3".into())),
    ]);
    let mut empty_plist = Vec::new();
    plist::to_writer_xml(&mut empty_plist, &Value::Dictionary(Dictionary::new())).map_err(|e| e.to_string())?;
    let input = d(vec![
        ("applicationSpecifier", d(vec![("bundleIdentifier", d(vec![("_0", Xpc::String(bundle_id.into()))]))])),
        ("options", d(vec![
            ("arguments", Xpc::Array(vec![])),
            ("environmentVariables", d(vec![])),
            ("standardIOUsesPseudoterminals", Xpc::Bool(true)),
            ("startStopped", Xpc::Bool(true)),
            ("terminateExisting", Xpc::Bool(true)),
            ("user", d(vec![("active", Xpc::Bool(true))])),
            ("platformSpecificOptions", Xpc::Data(empty_plist)),
        ])),
        ("standardIOIdentifiers", d(vec![])),
    ]);
    let request = d(vec![
        ("CoreDevice.CoreDeviceDDIProtocolVersion", Xpc::Int(0)),
        ("CoreDevice.action", d(vec![])),
        ("CoreDevice.coreDeviceVersion", version),
        ("CoreDevice.deviceIdentifier", Xpc::String(uuid::Uuid::new_v4().to_string())),
        ("CoreDevice.featureIdentifier", Xpc::String("com.apple.coredevice.feature.launchapplication".into())),
        ("CoreDevice.input", input),
        ("CoreDevice.invocationIdentifier", Xpc::String(uuid::Uuid::new_v4().to_string())),
    ]);
    x.send(&request, true).await?;
    let reply = tokio::time::timeout(Duration::from_secs(60), x.recv()).await
        .map_err(|_| "the iPhone did not answer the launch".to_string())??.to_plist();
    let reply = reply.into_dictionary().ok_or("the launch answer is not a dictionary")?;
    let Some(output) = reply.get("CoreDevice.output").and_then(Value::as_dictionary) else {
        return Err(format!("the iPhone did not launch the app: {:?}", reply.get("CoreDevice.error")));
    };
    output.get("processToken").and_then(Value::as_dictionary).and_then(|t| t.get("processIdentifier"))
        .and_then(Value::as_unsigned_integer).ok_or_else(|| "the launch answer has no process id".into())
}

// ------------------------------------------------------------- debugserver

/// GDB remote serial protocol to debugserver, acknowledgements off.
struct Gdb {
    conn: TcpConn,
    ack: bool,
}

impl Gdb {
    async fn byte(&mut self) -> Result<u8, String> {
        Ok(self.conn.read_exact(1).await.map_err(|e| format!("debugserver: {e}"))?[0])
    }

    async fn send_raw(&mut self, data: &[u8]) -> Result<(), String> {
        self.conn.write_all(data).await.map_err(|e| format!("debugserver: {e}"))
    }

    async fn command(&mut self, packet: &str) -> Result<String, String> {
        let sum = packet.bytes().fold(0u8, |a, b| a.wrapping_add(b));
        self.send_raw(format!("${packet}#{sum:02x}").as_bytes()).await?;
        self.response().await
    }

    /// One reply packet, with run-length encoding and escapes undone.
    async fn response(&mut self) -> Result<String, String> {
        let mut c = self.byte().await?;
        while c != b'$' {
            c = self.byte().await?;
        }
        let mut out: Vec<u8> = Vec::new();
        loop {
            match self.byte().await? {
                b'#' => break,
                b'}' => { let e = self.byte().await?; out.push(e ^ 0x20); }
                b'*' => {
                    let n = self.byte().await?.saturating_sub(29) as usize;
                    if let Some(&last) = out.last() {
                        out.extend(std::iter::repeat_n(last, n));
                    }
                }
                b => out.push(b),
            }
        }
        self.byte().await?;
        self.byte().await?;
        if self.ack {
            self.send_raw(b"+").await?;
        }
        Ok(String::from_utf8_lossy(&out).into_owned())
    }
}

/// A register from a stop reply ("…;10:0011223344556677;…"), little-endian hex.
fn register(stop: &str, number: &str) -> Option<u64> {
    let at = stop.find(&format!(";{number}:")).map(|i| i + number.len() + 2)
        .or_else(|| stop.find(&format!("{number}:")).filter(|i| *i == 0).map(|_| number.len() + 1))?;
    let hex = stop.get(at..at + 16)?;
    let mut bytes = [0u8; 8];
    for (i, b) in bytes.iter_mut().enumerate() {
        *b = u8::from_str_radix(hex.get(i * 2..i * 2 + 2)?, 16).ok()?;
    }
    Some(u64::from_le_bytes(bytes))
}

fn thread_of(stop: &str) -> Option<&str> {
    let at = stop.find("thread:")? + 7;
    stop[at..].split(';').next()
}

fn le_hex(v: u64) -> String {
    hex::encode(v.to_le_bytes())
}

/// Writes one byte to every 16 KiB page of the region, which makes the pages
/// usable for JIT (StikDebug's prepare_memory_region).
async fn prepare_region(gdb: &mut Gdb, start: u64, size: u64) -> Result<(), String> {
    const PAGE: u64 = 16_384;
    let pages = size.div_ceil(PAGE);
    for batch in (0..pages).collect::<Vec<_>>().chunks(128) {
        let mut out = String::new();
        for p in batch {
            let packet = format!("M{:x},1:69", start + p * PAGE);
            let sum = packet.bytes().fold(0u8, |a, b| a.wrapping_add(b));
            out.push_str(&format!("${packet}#{sum:02x}"));
        }
        gdb.send_raw(out.as_bytes()).await?;
        for _ in batch {
            gdb.response().await?;
        }
    }
    Ok(())
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Script {
    /// Attach and detach: JIT before iOS 26.
    Detach,
    /// universal.js: brk #0xf00d commands until the app detaches.
    Universal,
    /// legacy.js: one brk #0x69 region request, then detach.
    Legacy,
}

fn script_for(name: &str) -> Script {
    if UNIVERSAL.contains(&name) {
        Script::Universal
    } else if LEGACY.contains(&name) {
        Script::Legacy
    } else {
        Script::Detach
    }
}

/// Serves the app's JIT requests until it detaches (or for the one legacy one).
async fn serve(gdb: &mut Gdb, script: Script) -> Result<(), String> {
    let mut served = 0;
    loop {
        let stop = gdb.command("c").await?;
        let (Some(tid), Some(pc)) = (thread_of(&stop).map(str::to_string), register(&stop, "20")) else {
            if stop.starts_with('W') || stop.starts_with('X') {
                return Err("the app quit".into());
            }
            continue;
        };
        let word = gdb.command(&format!("m{pc:x},4")).await?;
        let insn = u32::from_le_bytes(hex::decode(word.get(..8).unwrap_or("")).ok()
            .and_then(|b| b.try_into().ok()).unwrap_or([0; 4]));
        if insn & 0xFFE0_001F != 0xD420_0000 {
            // Not a brk: hand the signal back to the app.
            if let Some(sig) = stop.get(1..3) {
                let _ = gdb.command(&format!("vCont;S{sig}:{tid}")).await;
            }
            continue;
        }
        let immediate = (insn >> 5) & 0xFFFF;
        let (x0, x1, x16) = (register(&stop, "00").unwrap_or(0), register(&stop, "01").unwrap_or(0),
                             register(&stop, "10").unwrap_or(0));
        // Moves the thread past the brk.
        let skip = format!("P20={};thread:{tid};", le_hex(pc + 4));
        match (script, immediate) {
            (Script::Legacy, 0x69) => {
                prepare_region(gdb, x0, x1).await?;
                gdb.command(&skip).await?;
                return Ok(());
            }
            (Script::Universal, 0xF00D) => {
                gdb.command(&skip).await?;
                match x16 {
                    0 => return Ok(()),
                    1 if x0 != 0 || x1 != 0 => {
                        let mut page = x0;
                        if page == 0 {
                            let r = gdb.command(&format!("_M{x1:x},rx")).await?;
                            page = u64::from_str_radix(r.trim(), 16).map_err(|_| format!("no RX memory: {r}"))?;
                        }
                        prepare_region(gdb, page, x1).await?;
                        gdb.command(&format!("P0={};thread:{tid};", le_hex(page))).await?;
                        served += 1;
                        logf!("jit: region {served} ready at {page:#x} ({x1} bytes)");
                    }
                    other => logf!("jit: command {other} is not supported (needs StikDebug's script engine)"),
                }
            }
            (Script::Universal, 0x69) => {
                gdb.command(&skip).await?;
                gdb.command(&format!("P0={};thread:{tid};", le_hex(0xE000_0069))).await?;
            }
            _ => {}
        }
    }
}

/// Enables JIT for `bundle_id` (`name` is its display name) and starts it.
pub async fn enable(session: &Session, bundle_id: &str, name: &str, progress: &(dyn Fn(String) + Send + Sync))
    -> Result<String, String>
{
    progress(format!("Starting {name}"));
    let pid = launch_stopped(session, bundle_id).await?;
    logf!("jit: {bundle_id} launched stopped as pid {pid}");
    progress("Attaching the debugger".into());
    let mut gdb = Gdb { conn: session.connect(&[DEBUG_PROXY]).await?, ack: true };
    gdb.send_raw(b"++").await?;
    if gdb.command("QStartNoAckMode").await? == "OK" {
        gdb.ack = false;
    }
    let attach = gdb.command(&format!("vAttach;{pid:x}")).await?;
    if attach.starts_with('E') {
        return Err(format!("debugserver could not attach ({attach}). The app must be signed by BariStore (get-task-allow)."));
    }
    let script = script_for(name);
    if script != Script::Detach {
        progress(format!("{name} is starting; serving its JIT requests"));
        serve(&mut gdb, script).await?;
    }
    let _ = gdb.command("D").await;
    Ok(format!("JIT is on for {name}."))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stop_reply_registers() {
        let stop = "T05thread:1a2b;name:main;00:0100000000000000;01:0040000000000000;10:0100000000000000;20:f0debc9a78563412;";
        assert_eq!(register(stop, "00"), Some(1));
        assert_eq!(register(stop, "01"), Some(0x4000));
        assert_eq!(register(stop, "10"), Some(1));
        assert_eq!(register(stop, "20"), Some(0x1234_5678_9abc_def0));
        assert_eq!(thread_of(stop), Some("1a2b"));
        assert_eq!(le_hex(0x1234), "3412000000000000");
    }

    #[test]
    fn scripts_follow_stikdebug_assignments() {
        assert!(script_for("MeloNX") == Script::Universal);
        assert!(script_for("UTM") == Script::Legacy);
        assert!(script_for("Delta") == Script::Detach);
    }
}
