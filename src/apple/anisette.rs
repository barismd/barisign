//! Anisette v3: device-identity headers minted by a remote server that runs
//! Apple's ADI. The server never sees the Apple ID or password: it gets a
//! random keychain identifier and relays Apple's provisioning blobs.
//!
//! What Apple accepts changed in October/November 2026 (SideInstaller's
//! `client_info_to_use`, siboot 0.20): servers on Apple's Android ADI library
//! lead to -80046 at the token request, so only macOS- and Windows-library
//! servers are used; a macOS-library server that names a Mac older than
//! 10.13 is answered with -23782/-22406, so sign-in reports a current Mac
//! instead. An identity is provisioned under the client info it signs in
//! with, so each (server, client info) pair keeps its own identity.

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use futures_util::{SinkExt, StreamExt};
use plist::{Dictionary, Value};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::time::Duration;
use tokio_tungstenite::tungstenite::Message;

use super::gsa::GrandSlam;
use crate::{logf, state};

/// macOS-library servers in siboot's (SideInstaller Auto's) order, then the
/// Windows-library ones.
pub const SERVERS: &[&str] = &[
    "https://new.ani.sidestore.io",
    "https://mac-ani.jkcoxson.com",
    "https://ani.iloader.app",
    "https://ani3.stikstore.app",
    "https://ani.jkcoxson.com",
    "https://ani2.stikstore.app",
    "https://win.ani.sidestore.io",
];

const WINDOWS_CLIENT_INFO: &str = "<PC> <Windows;6.2(0,0);9200> <com.apple.AuthKit/1 (com.apple.iTunes/12.12.4.1)>";
const AKD_USER_AGENT: &str = "akd/1.0 CFNetwork/808.1.4";
const CURRENT_MAC_CLIENT_INFO: &str = "<MacBookPro18,3> <macOS;26.6;25F84> <com.apple.AuthKit/1 (com.apple.akd/1.0)>";
const CURRENT_MAC_USER_AGENT: &str = "AuthKit/1 (Macintosh; OS X 26.6) (com.apple.akd/1.0)";
/// Stock client infos of anisette-v3-server/omnisette: every public server
/// that still hands them out runs Apple's Android library.
const STOCK_CLIENTS: [&str; 2] = ["<MacBookPro13,2> <macOS;13.1;22C65>", "<MacBookPro15,1> <Mac OS X;10.15.2;19C57>"];

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
pub struct ClientInfo {
    pub client_info: String,
    pub user_agent: String,
}

/// Whether a server advertising `info` runs Apple's Android ADI library.
fn android_adi(info: &ClientInfo) -> bool {
    let c = info.client_info.trim();
    STOCK_CLIENTS.iter().any(|s| c.starts_with(s)) || c.contains("<Android;")
}

/// The macOS version a client info names, as (major, minor).
fn mac_version(client: &str) -> Option<(u32, u32)> {
    let rest = ["<macOS;", "<Mac OS X;"].iter().find_map(|m| client.split_once(m).map(|(_, r)| r))?;
    let mut parts = rest.split([';', '>']).next()?.trim().split('.');
    let major = parts.next()?.parse().ok()?;
    let minor = parts.next().and_then(|p| p.parse().ok()).unwrap_or(0);
    Some((major, minor))
}

/// What to tell GrandSlam for a server advertising `advertised`.
pub fn client_info_to_use(advertised: &ClientInfo) -> ClientInfo {
    let client = advertised.client_info.trim();
    if client.is_empty() || android_adi(advertised) {
        return ClientInfo { client_info: WINDOWS_CLIENT_INFO.into(), user_agent: AKD_USER_AGENT.into() };
    }
    if mac_version(client).is_some_and(|v| v < (10, 13)) {
        return ClientInfo { client_info: CURRENT_MAC_CLIENT_INFO.into(), user_agent: CURRENT_MAC_USER_AGENT.into() };
    }
    let ua = advertised.user_agent.trim();
    ClientInfo { client_info: client.into(), user_agent: if ua.is_empty() { AKD_USER_AGENT } else { ua }.into() }
}

#[derive(Clone)]
pub struct Anisette {
    pub server: String,
    /// What the server advertises (/v3/client_info).
    pub advertised: ClientInfo,
    /// What this identity signs in as.
    pub info: ClientInfo,
    keychain_id: [u8; 16],
    adi_pb: Option<Vec<u8>>,
}

#[derive(Clone, Debug)]
pub struct Headers {
    pub machine_id: String,
    pub otp: String,
    pub routing_info: String,
    pub device_id: String,
}

fn state_path(server: &str, info: &ClientInfo) -> std::path::PathBuf {
    let host = server.trim_start_matches("https://").replace(['/', ':'], "_");
    let kind = hex::encode(&Sha256::digest(info.client_info.as_bytes())[..4]);
    state::path(&format!("anisette-{host}-{kind}.plist"))
}

impl Anisette {
    /// Connects to `server`. With `as_advertised`, signs in as exactly what
    /// the server advertises (iloader 2.4.2's choice) instead of
    /// `client_info_to_use`.
    pub async fn connect(http: &reqwest::Client, server: &str, as_advertised: bool) -> Result<Anisette, String> {
        let advertised: ClientInfo = http.get(format!("{server}/v3/client_info"))
            .timeout(Duration::from_secs(30))
            .send().await.map_err(|e| format!("{server}: {e}"))?
            .error_for_status().map_err(|e| format!("{server}: {e}"))?
            .json().await.map_err(|e| format!("{server}: bad client_info: {e}"))?;
        if android_adi(&advertised) {
            return Err(format!("{server} runs Apple's Android ADI library, which Apple refuses (-80046)"));
        }
        let info = if as_advertised { advertised.clone() } else { client_info_to_use(&advertised) };
        logf!("anisette: {server} advertises {}; signing in as {}", advertised.client_info, info.client_info);
        let mut a = Anisette { server: server.into(), advertised, info, keychain_id: rand::random(), adi_pb: None };
        if let Ok(Value::Dictionary(d)) = plist::from_file::<_, Value>(a.state_path()) {
            if let (Some(id), Some(pb)) = (d.get("keychain_identifier").and_then(Value::as_data),
                                           d.get("adi_pb").and_then(Value::as_data)) {
                if let Ok(id) = id.try_into() {
                    a.keychain_id = id;
                    a.adi_pb = Some(pb.to_vec());
                }
            }
        }
        Ok(a)
    }

    pub fn device_id(&self) -> String {
        uuid::Uuid::from_bytes(self.keychain_id).to_string()
    }

    fn state_path(&self) -> std::path::PathBuf {
        state_path(&self.server, &self.info)
    }

    fn local_user(&self) -> String {
        hex::encode(Sha256::digest(self.keychain_id))
    }

    pub fn forget(&mut self) {
        self.keychain_id = rand::random();
        self.adi_pb = None;
        let _ = std::fs::remove_file(self.state_path());
    }

    fn save(&self) {
        let mut d = Dictionary::new();
        d.insert("keychain_identifier".into(), Value::Data(self.keychain_id.to_vec()));
        if let Some(pb) = &self.adi_pb {
            d.insert("adi_pb".into(), Value::Data(pb.clone()));
        }
        let _ = state::write_private(&self.state_path(), &crate::pairing::record::plist_xml(&d));
    }

    /// Fresh headers, provisioning first when needed. With `sim`, the server
    /// first applies the resync data GrandSlam sent with an hsc 434.
    pub async fn headers(&mut self, http: &reqwest::Client, gs: &GrandSlam, sim: Option<&str>)
        -> Result<Headers, String>
    {
        for _ in 0..3 {
            if self.adi_pb.is_none() {
                self.provision(gs).await?;
            }
            let mut body = serde_json::json!({
                "identifier": B64.encode(self.keychain_id),
                "adi_pb": B64.encode(self.adi_pb.as_ref().unwrap()),
            });
            if let Some(sim) = sim {
                body["sim"] = sim.into();
                body["device_id"] = self.device_id().into();
                body["md_lu"] = self.local_user().into();
            }
            let reply = self.get_headers(http, &body).await?;
            let text = |k: &str| reply.get(k).and_then(|v| v.as_str()).map(str::to_string);
            match text("result").as_deref() {
                Some("Headers") => return Ok(Headers {
                    machine_id: text("X-Apple-I-MD-M").unwrap_or_default(),
                    otp: text("X-Apple-I-MD").unwrap_or_default(),
                    routing_info: text("X-Apple-I-MD-RINFO").unwrap_or_else(|| "17106176".into()),
                    device_id: self.device_id(),
                }),
                _ => {
                    let message = text("message").unwrap_or_else(|| reply.to_string());
                    logf!("anisette: {} get_headers: {message}", self.server);
                    if message.contains("-45061") {
                        self.forget();
                        continue;
                    }
                    return Err(format!("anisette server refused: {message}"));
                }
            }
        }
        Err("anisette provisioning kept failing".into())
    }

    /// POST /v3/get_headers; a 5xx is asked again twice (servers have
    /// answered one request in three with 502 at times).
    async fn get_headers(&self, http: &reqwest::Client, body: &serde_json::Value) -> Result<serde_json::Value, String> {
        let mut asked = 0;
        loop {
            asked += 1;
            let resp = http.post(format!("{}/v3/get_headers", self.server))
                .json(body).timeout(Duration::from_secs(20)).send().await
                .map_err(|e| format!("{}: {e}", self.server))?;
            if resp.status().is_server_error() && asked < 3 {
                logf!("anisette: {} answered {}; asking again", self.server, resp.status());
                tokio::time::sleep(Duration::from_secs(1)).await;
                continue;
            }
            return resp.error_for_status().map_err(|e| format!("{}: {e}", self.server))?
                .json().await.map_err(|e| format!("{}: {e}", self.server));
        }
    }

    async fn provision(&mut self, gs: &GrandSlam) -> Result<(), String> {
        let start = gs.url("midStartProvisioning")?;
        let finish = gs.url("midFinishProvisioning")?;
        let ws_url = format!("{}/v3/provisioning_session", self.server).replacen("https://", "wss://", 1);
        let (mut ws, _) = tokio::time::timeout(Duration::from_secs(30), tokio_tungstenite::connect_async(&ws_url)).await
            .map_err(|_| "anisette provisioning socket timed out".to_string())?
            .map_err(|e| format!("anisette provisioning socket: {e}"))?;
        let mut extra = reqwest::header::HeaderMap::new();
        extra.insert("X-Apple-I-MD-LU", self.local_user().parse().unwrap());
        extra.insert("X-Mme-Device-Id", self.device_id().parse().unwrap());
        loop {
            let msg = tokio::time::timeout(Duration::from_secs(30), ws.next()).await
                .map_err(|_| "anisette provisioning timed out".to_string())?
                .ok_or("anisette provisioning socket closed")?
                .map_err(|e| e.to_string())?;
            let Message::Text(text) = msg else { continue };
            let m: serde_json::Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
            let field = |k: &str| m.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
            let reply = match field("result").as_str() {
                "GiveIdentifier" => serde_json::json!({"identifier": B64.encode(self.keychain_id)}),
                "GiveStartProvisioningData" => {
                    let mut body = Dictionary::new();
                    body.insert("Header".into(), Value::Dictionary(Dictionary::new()));
                    body.insert("Request".into(), Value::Dictionary(Dictionary::new()));
                    let r = gs.plist_post(&start, &body, Some(extra.clone())).await?;
                    serde_json::json!({"spim": r.get("spim").and_then(Value::as_string).unwrap_or("")})
                }
                "GiveEndProvisioningData" => {
                    let mut request = Dictionary::new();
                    request.insert("cpim".into(), Value::String(field("cpim")));
                    let mut body = Dictionary::new();
                    body.insert("Header".into(), Value::Dictionary(Dictionary::new()));
                    body.insert("Request".into(), Value::Dictionary(request));
                    let r = gs.plist_post(&finish, &body, Some(extra.clone())).await?;
                    let s = |k: &str| r.get(k).and_then(Value::as_string).unwrap_or("").to_string();
                    serde_json::json!({"ptm": s("ptm"), "tk": s("tk")})
                }
                "ProvisioningSuccess" => {
                    self.adi_pb = Some(B64.decode(field("adi_pb")).map_err(|e| e.to_string())?);
                    let _ = ws.close(None).await;
                    self.save();
                    logf!("anisette: provisioned with {}", self.server);
                    return Ok(());
                }
                other => return Err(format!("anisette provisioning failed: {other} {}", field("message"))),
            };
            ws.send(Message::Text(reply.to_string().into())).await.map_err(|e| e.to_string())?;
        }
    }
}

impl Headers {
    /// The cpd ("client provided data") dictionary GrandSlam requests carry.
    pub fn cpd(&self) -> Dictionary {
        let mut d = Dictionary::new();
        for (k, v) in [("bootstrap", "true"), ("icscrec", "true"), ("loc", "en_US"),
                       ("pbe", "false"), ("prkgen", "true"), ("svct", "iCloud")] {
            d.insert(k.into(), Value::String(v.into()));
        }
        for (k, v) in self.pairs() {
            d.insert(k.into(), Value::String(v));
        }
        d
    }

    pub fn pairs(&self) -> Vec<(&'static str, String)> {
        vec![
            ("X-Mme-Device-Id", self.device_id.clone()),
            ("X-Apple-I-MD", self.otp.clone()),
            ("X-Apple-I-MD-M", self.machine_id.clone()),
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn info(c: &str, ua: &str) -> ClientInfo {
        ClientInfo { client_info: c.into(), user_agent: ua.into() }
    }

    #[test]
    fn old_mac_becomes_current_mac() {
        let got = client_info_to_use(&info("<MacBookPro13,3> <Mac OS X;10.12.6;16G29> <com.apple.AuthKit/1 (com.apple.akd/1.0)>", "akd/1.0"));
        assert_eq!(got.client_info, CURRENT_MAC_CLIENT_INFO);
        assert_eq!(got.user_agent, CURRENT_MAC_USER_AGENT);
    }

    #[test]
    fn windows_and_new_macs_are_kept() {
        let win = info(WINDOWS_CLIENT_INFO, "akd/1.0 CFNetwork/808.1.4");
        assert_eq!(client_info_to_use(&win), win);
        let mac = info("<Mac15,7> <macOS;27.0;26A5378j> <com.apple.AuthKit/1 (com.apple.akd/1.0)>", "");
        assert_eq!(client_info_to_use(&mac).client_info, mac.client_info);
        assert_eq!(client_info_to_use(&mac).user_agent, AKD_USER_AGENT);
    }

    #[test]
    fn android_library_is_recognised() {
        assert!(android_adi(&info("<MacBookPro13,2> <macOS;13.1;22C65> <com.apple.AuthKit/1 (com.apple.dt.Xcode/3594.4.19)>", "")));
        assert!(android_adi(&info("<Pixel 6> <Android;12;0> <com.apple.AuthKit/1 (com.apple.akd/1.0)>", "")));
        assert!(!android_adi(&info(WINDOWS_CLIENT_INFO, "")));
        assert_eq!(client_info_to_use(&info("", "")).client_info, WINDOWS_CLIENT_INFO);
    }
}
