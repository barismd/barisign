//! GrandSlam (gsa.apple.com): URL bag, SRP-6a sign-in (s2k / s2k_fo),
//! trusted-device and SMS two-factor codes, and the Xcode app token used by
//! the developer services. The password never leaves this device: it only
//! feeds PBKDF2 and the SRP proof. Request shapes follow nab138/isideload (MIT).

use aes::cipher::{block_padding::Pkcs7, BlockDecryptMut, KeyIvInit};
use aes_gcm::aead::{generic_array::typenum::U16, Aead, KeyInit, Payload};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use hmac::{Hmac, Mac};
use plist::{Dictionary, Value};
use sha2::{Digest, Sha256};
use std::future::Future;
use std::pin::Pin;

use super::anisette::{Anisette, Headers, SERVERS};
use crate::{logf, srp, state};

const URL_BAG: &str = "https://gsa.apple.com/grandslam/GsService2/lookup";
const APPLE_ROOT: &[u8] = include_bytes!("../../assets/certs/AppleRootCA.cer");

pub fn http_client() -> reqwest::Client {
    reqwest::Client::builder()
        .add_root_certificate(reqwest::Certificate::from_der(APPLE_ROOT).expect("Apple root"))
        .http1_title_case_headers()
        .timeout(std::time::Duration::from_secs(60))
        .build()
        .expect("HTTP client")
}

#[derive(Debug)]
pub enum LoginError {
    /// -20101 / -20970 / -22406, with Apple's own words. -22406 can also mean
    /// Apple is throttling, or did not believe the anisette identity.
    WrongPassword(String),
    /// Apple refused this anisette identity (-80045/-80046/-23782): try another server.
    IdentityRefused(String),
    /// Retrying cannot help now (429, 503 from gsa, locked account).
    Stop(String),
    Cancelled,
    Other(String),
}

impl std::fmt::Display for LoginError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LoginError::WrongPassword(why) => write!(f,
                "Apple rejected the email or password ({why}). If you are sure they are right, Apple may be \
                 limiting sign-ins: wait a while before trying again"),
            LoginError::Stop(why) => write!(f, "{why}"),
            LoginError::IdentityRefused(why) => write!(f, "Apple refused the sign-in identity ({why})"),
            LoginError::Cancelled => write!(f, "sign-in cancelled"),
            LoginError::Other(why) => write!(f, "{why}"),
        }
    }
}

fn other(e: impl std::fmt::Display) -> LoginError {
    LoginError::Other(e.to_string())
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CodeKind {
    TrustedDevice,
    Sms,
}

/// Asks the user for a 2FA code; None cancels.
pub type CodeAsker = dyn Fn(CodeKind, Option<String>) -> Pin<Box<dyn Future<Output = Option<String>> + Send>>
    + Send + Sync;

pub struct GrandSlam {
    pub http: reqwest::Client,
    pub anisette: tokio::sync::Mutex<Anisette>,
    info: super::anisette::ClientInfo,
    bag: Dictionary,
}

fn check(response: &Dictionary) -> Result<(), LoginError> {
    let status = response.get("Status").and_then(Value::as_dictionary);
    let code = status.and_then(|s| s.get("ec")).and_then(Value::as_signed_integer).unwrap_or(0);
    if code == 0 {
        return Ok(());
    }
    let message = status.and_then(|s| s.get("em")).and_then(Value::as_string).unwrap_or("").to_string();
    let what = format!("{code} {message}");
    logf!("gsa: Apple answered {what}");
    Err(match code {
        -20101 | -20970 | -22406 => LoginError::WrongPassword(what),
        -80045 | -80046 | -23782 => LoginError::IdentityRefused(what),
        -20209 => LoginError::Stop(format!("Apple locked this Apple Account for security reasons ({what}); \
                                             unlock it at iforgot.apple.com")),
        _ => LoginError::Other(format!("Apple error {what}")),
    })
}

impl GrandSlam {
    pub fn base_headers(&self, json: bool) -> reqwest::header::HeaderMap {
        let mut h = reqwest::header::HeaderMap::new();
        let kind = if json { "application/json" } else { "text/x-xml-plist" };
        h.insert("Content-Type", kind.parse().unwrap());
        h.insert("Accept", kind.parse().unwrap());
        if let Ok(v) = self.info.client_info.parse() {
            h.insert("X-Mme-Client-Info", v);
        }
        if let Ok(v) = self.info.user_agent.parse() {
            h.insert("User-Agent", v);
        }
        h.insert("X-Xcode-Version", "14.2 (14C18)".parse().unwrap());
        h.insert("X-Apple-App-Info", "com.apple.gs.xcode.auth".parse().unwrap());
        h
    }

    pub async fn connect(server: &str, as_advertised: bool) -> Result<GrandSlam, String> {
        let http = http_client();
        let anisette = Anisette::connect(&http, server, as_advertised).await?;
        let info = anisette.info.clone();
        let mut gs = GrandSlam { http, anisette: tokio::sync::Mutex::new(anisette), info, bag: Dictionary::new() };
        let text = gs.http.get(URL_BAG).headers(gs.base_headers(false)).send().await
            .map_err(|e| format!("gsa.apple.com: {e}"))?
            .text().await.map_err(|e| e.to_string())?;
        let bag: Dictionary = plist::from_bytes(text.as_bytes()).map_err(|e| format!("URL bag: {e}"))?;
        gs.bag = bag.get("urls").and_then(Value::as_dictionary).cloned().ok_or("URL bag without urls")?;
        Ok(gs)
    }

    pub fn url(&self, key: &str) -> Result<String, String> {
        self.bag.get(key).and_then(Value::as_string).map(str::to_string)
            .ok_or_else(|| format!("Apple's URL bag has no {key}"))
    }

    pub async fn plist_post(&self, url: &str, body: &Dictionary, extra: Option<reqwest::header::HeaderMap>)
        -> Result<Dictionary, String>
    {
        let mut req = self.http.post(url).headers(self.base_headers(false));
        if let Some(extra) = extra {
            req = req.headers(extra);
        }
        let body = crate::pairing::record::plist_xml(body);
        // A 429 is often momentary (iloader 2.3.4): ask again after 2 s and 5 s.
        let mut delays = [2u64, 5].into_iter();
        let resp = loop {
            let resp = req.try_clone().ok_or("request cannot be repeated")?
                .body(body.clone()).send().await.map_err(|e| e.to_string())?;
            match delays.next() {
                Some(d) if resp.status() == reqwest::StatusCode::TOO_MANY_REQUESTS => {
                    logf!("gsa: 429 from Apple; asking again in {d} s");
                    tokio::time::sleep(std::time::Duration::from_secs(d)).await;
                }
                _ => break resp,
            }
        };
        let status = resp.status();
        let text = resp.text().await.map_err(|e| e.to_string())?;
        if !status.is_success() {
            return Err(format!("HTTP {status} from Apple: {}", text.chars().take(200).collect::<String>()));
        }
        let d: Dictionary = plist::from_bytes(text.as_bytes())
            .map_err(|_| format!("Apple answered with something that is not a plist: {}",
                                 text.chars().take(200).collect::<String>()))?;
        Ok(d.get("Response").and_then(Value::as_dictionary).cloned().unwrap_or(d))
    }

    pub async fn headers(&self) -> Result<Headers, String> {
        let mut a = self.anisette.lock().await;
        a.headers(&self.http, self, None).await
    }

    async fn resync(&self, sim: &str) -> Result<Headers, String> {
        let mut a = self.anisette.lock().await;
        a.headers(&self.http, self, Some(sim)).await
    }
}

/// What a signed-in account keeps (no password).
#[derive(Clone, Debug)]
pub struct Account {
    pub email: String,
    pub adsid: String,
    pub idms_token: String,
    pub session_key: Vec<u8>,
    pub c: Vec<u8>,
    pub first_name: String,
    pub last_name: String,
    pub xcode_token: String,
    pub xcode_expiry_ms: u64,
}

impl Account {
    pub fn save(&self) -> std::io::Result<()> {
        let mut d = Dictionary::new();
        let s = |v: &str| Value::String(v.into());
        d.insert("email".into(), s(&self.email));
        d.insert("adsid".into(), s(&self.adsid));
        d.insert("GsIdmsToken".into(), s(&self.idms_token));
        d.insert("sk".into(), Value::Data(self.session_key.clone()));
        d.insert("c".into(), Value::Data(self.c.clone()));
        d.insert("firstName".into(), s(&self.first_name));
        d.insert("lastName".into(), s(&self.last_name));
        d.insert("xcodeToken".into(), s(&self.xcode_token));
        d.insert("xcodeExpiry".into(), Value::Integer(self.xcode_expiry_ms.into()));
        state::write_private(&state::path("account.plist"), &crate::pairing::record::plist_xml(&d))
    }

    pub fn load() -> Option<Account> {
        let d = plist::from_file::<_, Value>(state::path("account.plist")).ok()?.into_dictionary()?;
        let s = |k: &str| d.get(k).and_then(Value::as_string).unwrap_or("").to_string();
        let b = |k: &str| d.get(k).and_then(Value::as_data).map(<[u8]>::to_vec).unwrap_or_default();
        Some(Account {
            email: s("email"), adsid: s("adsid"), idms_token: s("GsIdmsToken"), session_key: b("sk"),
            c: b("c"), first_name: s("firstName"), last_name: s("lastName"), xcode_token: s("xcodeToken"),
            xcode_expiry_ms: d.get("xcodeExpiry").and_then(Value::as_unsigned_integer).unwrap_or(0),
        }).filter(|a| !a.email.is_empty())
    }

    pub fn token_fresh(&self) -> bool {
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
        self.xcode_expiry_ms > now.as_millis() as u64 + 10 * 60 * 1000
    }
}

fn session_hmac(key: &[u8], label: &str) -> Vec<u8> {
    let mut mac = <Hmac<Sha256> as Mac>::new_from_slice(key).unwrap();
    mac.update(label.as_bytes());
    mac.finalize().into_bytes().to_vec()
}

enum Stage {
    LoggedIn(Dictionary),
    TrustedDevice(Dictionary),
    Sms(Dictionary),
}

async fn srp_login(gs: &GrandSlam, email: &str, password: &str) -> Result<Stage, LoginError> {
    let url = gs.url("gsService").map_err(other)?;
    let headers = gs.headers().await.map_err(other)?;
    let group = srp::Group::rfc5054_2048_sha256();
    let client = srp::Client::new(&group);

    let init = |headers: &Headers| {
        let mut init = Dictionary::new();
        init.insert("A2k".into(), Value::Data(client.public()));
        init.insert("cpd".into(), Value::Dictionary(headers.cpd()));
        init.insert("o".into(), Value::String("init".into()));
        init.insert("ps".into(), Value::Array(vec![Value::String("s2k".into()), Value::String("s2k_fo".into())]));
        init.insert("u".into(), Value::String(email.into()));
        request_body(init)
    };
    let mut r1 = gs.plist_post(&url, &init(&headers), None).await.map_err(gsa_failure)?;
    // Windows-library servers get hsc 434 (-22421) on a machine's first
    // sign-in, with resync data for ADI; once applied, the same request works.
    let mut headers = headers;
    if let Some(sim) = resync_sim(&r1) {
        logf!("gsa: Apple asked for an anisette resync (hsc 434)");
        headers = gs.resync(&sim).await.map_err(other)?;
        r1 = gs.plist_post(&url, &init(&headers), None).await.map_err(gsa_failure)?;
    }
    check(&r1)?;
    let data = |d: &Dictionary, k: &str| d.get(k).and_then(Value::as_data).map(<[u8]>::to_vec)
        .ok_or_else(|| other(format!("Apple's answer lacks {k}")));
    let salt = data(&r1, "s")?;
    let b_pub = data(&r1, "B")?;
    let iterations = r1.get("i").and_then(Value::as_signed_integer).ok_or_else(|| other("no iteration count"))?;
    let c = r1.get("c").and_then(Value::as_string).ok_or_else(|| other("no c"))?.to_string();
    let protocol = r1.get("sp").and_then(Value::as_string).unwrap_or("s2k").to_string();
    if protocol != "s2k" && protocol != "s2k_fo" {
        return Err(other(format!("Apple asked for an unsupported protocol: {protocol}")));
    }
    let digest = Sha256::digest(password.as_bytes());
    let input: Vec<u8> = if protocol == "s2k_fo" { hex::encode(digest).into_bytes() } else { digest.to_vec() };
    let mut derived = [0u8; 32];
    pbkdf2::pbkdf2::<Hmac<Sha256>>(&input, &salt, iterations as u32, &mut derived).map_err(other)?;
    let x = srp::x_without_user(&group, &salt, &derived);
    let session = client.process(email.as_bytes(), &salt, &b_pub, &x).map_err(other)?;

    let mut complete = Dictionary::new();
    complete.insert("M1".into(), Value::Data(session.proof.clone()));
    complete.insert("c".into(), Value::String(c));
    complete.insert("cpd".into(), Value::Dictionary(headers.cpd()));
    complete.insert("o".into(), Value::String("complete".into()));
    complete.insert("u".into(), Value::String(email.into()));
    let r2 = gs.plist_post(&url, &request_body(complete), None).await.map_err(gsa_failure)?;
    check(&r2)?;
    if !session.verify_server(&data(&r2, "M2")?) {
        return Err(other("Apple's server proof did not verify"));
    }
    let spd_sealed = data(&r2, "spd")?;
    let key = session_hmac(&session.key, "extra data key:");
    let iv = session_hmac(&session.key, "extra data iv:");
    let spd_raw = cbc::Decryptor::<aes::Aes256>::new_from_slices(&key, &iv[..16]).map_err(other)?
        .decrypt_padded_vec_mut::<Pkcs7>(&spd_sealed).map_err(|_| other("cannot decrypt Apple's session data"))?;
    let spd: Dictionary = plist::from_bytes(&spd_raw).map_err(other)?;
    let status = r2.get("Status").and_then(Value::as_dictionary).cloned().unwrap_or_default();
    match status.get("au").and_then(Value::as_string) {
        Some("trustedDeviceSecondaryAuth") => return Ok(Stage::TrustedDevice(spd)),
        Some("secondaryAuth") => return Ok(Stage::Sms(spd)),
        Some("repair") | None => {}
        Some(other_step) => logf!("gsa: unknown extra step {other_step}; continuing"),
    }
    if status.get("hsc").and_then(Value::as_signed_integer) == Some(409) {
        return Ok(Stage::TrustedDevice(spd));
    }
    Ok(Stage::LoggedIn(spd))
}

/// HTTP failures from gsa.apple.com: a 503 is Apple refusing the request
/// before it reads the anisette data, and a 429 left after the retries is
/// throttling, so no other server would do better.
fn gsa_failure(e: String) -> LoginError {
    if e.contains("HTTP 503") {
        LoginError::Stop(format!("Apple's sign-in server refused the request; try again later ({e})"))
    } else if e.contains("HTTP 429") {
        LoginError::Stop("Apple is limiting sign-ins for this account or network. Wait a while: every attempt \
                          can extend the wait".into())
    } else {
        LoginError::Other(e)
    }
}

/// The resync data (base64) of a response whose Status asks for an anisette
/// resync (hsc 434).
fn resync_sim(response: &Dictionary) -> Option<String> {
    let status = response.get("Status")?.as_dictionary()?;
    if status.get("hsc").and_then(Value::as_signed_integer) != Some(434) {
        return None;
    }
    match status.get("X-Apple-I-MD-DATA")? {
        Value::String(s) => Some(s.clone()),
        Value::Data(d) => Some(B64.encode(d)),
        _ => None,
    }
}

fn request_body(request: Dictionary) -> Dictionary {
    let mut header = Dictionary::new();
    header.insert("Version".into(), Value::String("1.0.1".into()));
    let mut body = Dictionary::new();
    body.insert("Header".into(), Value::Dictionary(header));
    body.insert("Request".into(), Value::Dictionary(request));
    body
}

async fn second_factor_headers(gs: &GrandSlam, spd: &Dictionary, json: bool) -> Result<reqwest::header::HeaderMap, LoginError> {
    let a = gs.headers().await.map_err(other)?;
    let mut h = gs.base_headers(json);
    for (k, v) in a.pairs() {
        h.insert(k, v.parse().map_err(other)?);
    }
    let s = |k: &str| spd.get(k).and_then(Value::as_string).unwrap_or("").to_string();
    let identity = B64.encode(format!("{}:{}", s("adsid"), s("GsIdmsToken")));
    h.insert("X-Apple-Identity-Token", identity.parse().map_err(other)?);
    h.insert("X-Apple-I-MD-RINFO", a.routing_info.parse().map_err(other)?);
    Ok(h)
}

async fn trusted_device(gs: &GrandSlam, spd: &Dictionary, ask: &CodeAsker) -> Result<(), LoginError> {
    let send = gs.url("trustedDeviceSecondaryAuth").map_err(other)?;
    let validate = gs.url("validateCode").map_err(other)?;
    gs.http.get(&send).headers(second_factor_headers(gs, spd, false).await?).send().await.map_err(other)?;
    let mut hint = None;
    for _ in 0..5 {
        let code = ask(CodeKind::TrustedDevice, hint.take()).await.ok_or(LoginError::Cancelled)?;
        let text = gs.http.get(&validate).headers(second_factor_headers(gs, spd, false).await?)
            .header("security-code", code.trim()).send().await.map_err(other)?
            .text().await.map_err(other)?;
        let reply: Dictionary = plist::from_bytes(text.as_bytes()).unwrap_or_default();
        match check(&reply) {
            Ok(()) => return Ok(()),
            Err(LoginError::Other(e)) if e.contains("-21669") => hint = Some("That code was wrong. Try again.".into()),
            Err(e) => return Err(e),
        }
    }
    Err(other("too many wrong codes"))
}

async fn sms(gs: &GrandSlam, spd: &Dictionary, ask: &CodeAsker) -> Result<(), LoginError> {
    let numbers: serde_json::Value = gs.http.get("https://gsa.apple.com/auth")
        .headers(second_factor_headers(gs, spd, true).await?).send().await.map_err(other)?
        .json().await.unwrap_or(serde_json::Value::Null);
    let id = numbers.pointer("/trustedPhoneNumbers/0/id").and_then(|v| v.as_u64()).unwrap_or(1);
    let body = serde_json::json!({"phoneNumber": {"id": id}, "mode": "sms"});
    gs.http.put("https://gsa.apple.com/auth/verify/phone")
        .headers(second_factor_headers(gs, spd, true).await?).json(&body).send().await.map_err(other)?;
    let mut hint = None;
    for _ in 0..5 {
        let code = ask(CodeKind::Sms, hint.take()).await.ok_or(LoginError::Cancelled)?;
        let body = serde_json::json!({"securityCode": {"code": code.trim()}, "phoneNumber": {"id": id}, "mode": "sms"});
        let resp = gs.http.post("https://gsa.apple.com/auth/verify/phone/securitycode")
            .headers(second_factor_headers(gs, spd, true).await?).json(&body).send().await.map_err(other)?;
        if resp.status().is_success() {
            return Ok(());
        }
        hint = Some("That code was wrong. Try again.".into());
    }
    Err(other("too many wrong codes"))
}

pub async fn app_token(gs: &GrandSlam, account: &mut Account) -> Result<(), LoginError> {
    let app = "com.apple.gs.xcode.auth";
    let headers = gs.headers().await.map_err(other)?;
    let mut mac = <Hmac<Sha256> as Mac>::new_from_slice(&account.session_key).map_err(other)?;
    mac.update(b"apptokens");
    mac.update(account.adsid.as_bytes());
    mac.update(app.as_bytes());
    let mut request = Dictionary::new();
    request.insert("app".into(), Value::Array(vec![Value::String(app.into())]));
    request.insert("c".into(), Value::Data(account.c.clone()));
    request.insert("checksum".into(), Value::Data(mac.finalize().into_bytes().to_vec()));
    request.insert("cpd".into(), Value::Dictionary(headers.cpd()));
    request.insert("o".into(), Value::String("apptokens".into()));
    request.insert("u".into(), Value::String(account.adsid.clone()));
    request.insert("t".into(), Value::String(account.idms_token.clone()));
    let url = gs.url("gsService").map_err(other)?;
    let reply = gs.plist_post(&url, &request_body(request), None).await.map_err(gsa_failure)?;
    check(&reply)?;
    let sealed = reply.get("et").and_then(Value::as_data).ok_or_else(|| other("no encrypted token"))?;
    if sealed.len() < 35 || &sealed[..3] != b"XYZ" {
        return Err(other("unknown app token format"));
    }
    type Gcm16 = aes_gcm::AesGcm<aes::Aes256, U16>;
    let cipher = Gcm16::new_from_slice(&account.session_key).map_err(other)?;
    let opened = cipher.decrypt(aes_gcm::aead::generic_array::GenericArray::from_slice(&sealed[3..19]),
                                Payload { msg: &sealed[19..], aad: &sealed[..3] })
        .map_err(|_| other("cannot decrypt the app token"))?;
    let token: Dictionary = plist::from_bytes(&opened).map_err(other)?;
    if token.get("status-code").and_then(Value::as_signed_integer) != Some(200) {
        return Err(other(format!("app token refused: {token:?}")));
    }
    let entry = token.get("t").and_then(Value::as_dictionary).and_then(|t| t.get(app))
        .and_then(Value::as_dictionary).ok_or_else(|| other("no xcode token"))?;
    account.xcode_token = entry.get("token").and_then(Value::as_string).unwrap_or("").to_string();
    account.xcode_expiry_ms = entry.get("expiry").and_then(Value::as_unsigned_integer).unwrap_or(0);
    Ok(())
}

/// Full sign-in on one anisette server.
async fn login_on(gs: &GrandSlam, email: &str, password: &str, ask: &CodeAsker) -> Result<Account, LoginError> {
    let mut stage = srp_login(gs, email, password).await?;
    for _ in 0..2 {
        match stage {
            Stage::LoggedIn(_) => break,
            Stage::TrustedDevice(ref spd) => trusted_device(gs, spd, ask).await?,
            Stage::Sms(ref spd) => sms(gs, spd, ask).await?,
        }
        stage = srp_login(gs, email, password).await?;
    }
    let Stage::LoggedIn(spd) = stage else {
        return Err(other("Apple kept asking for a second factor"));
    };
    let s = |k: &str| spd.get(k).and_then(Value::as_string).unwrap_or("").to_string();
    let b = |k: &str| spd.get(k).and_then(Value::as_data).map(<[u8]>::to_vec).unwrap_or_default();
    let mut account = Account {
        email: email.to_string(), adsid: s("adsid"), idms_token: s("GsIdmsToken"), session_key: b("sk"),
        c: b("c"), first_name: s("fn"), last_name: s("ln"), xcode_token: String::new(), xcode_expiry_ms: 0,
    };
    app_token(gs, &mut account).await?;
    Ok(account)
}

/// Signs in, failing over to the next anisette server when Apple refuses the
/// identity. On a server whose own client info we replaced (an old Mac), a
/// credential refusal is tried once more as the server advertises it
/// (iloader 2.4.2 signs in that way), then sign-in stops: more attempts with
/// the same password risk Apple locking the account. Returns the GrandSlam
/// connection that worked.
pub async fn login(email: &str, password: &str, ask: &CodeAsker) -> Result<(GrandSlam, Account), LoginError> {
    let email = email.trim().to_lowercase();
    let mut last = LoginError::Other("no anisette server answered".into());
    let mut tried_advertised = false;
    for server in preferred_servers() {
        for as_advertised in [false, true] {
            if as_advertised && tried_advertised {
                break;
            }
            let gs = match GrandSlam::connect(&server, as_advertised).await {
                Ok(gs) => gs,
                Err(e) => {
                    logf!("gsa: {server} unusable: {e}");
                    last = LoginError::Other(e);
                    break;
                }
            };
            let replaced = { let a = gs.anisette.lock().await; a.info != a.advertised };
            if as_advertised && !replaced {
                break;
            }
            match login_on(&gs, &email, password, ask).await {
                Ok(account) => {
                    let _ = std::fs::write(state::path("anisette-server"),
                                           format!("{server}\n{}", if as_advertised { "advertised" } else { "" }));
                    return Ok((gs, account));
                }
                Err(LoginError::IdentityRefused(why)) => {
                    logf!("gsa: {server}: identity refused ({why}); next server");
                    gs.anisette.lock().await.forget();
                    last = LoginError::IdentityRefused(why);
                    break;
                }
                Err(LoginError::WrongPassword(why)) if replaced && !as_advertised => {
                    logf!("gsa: {server}: {why} as a current Mac; once more as the server advertises");
                    tried_advertised = true;
                    last = LoginError::WrongPassword(why);
                }
                Err(e) => return Err(e),
            }
        }
    }
    Err(last)
}

/// Reuses the saved session: refreshes the Xcode token without a password.
pub async fn resume() -> Result<(GrandSlam, Account), String> {
    let mut account = Account::load().ok_or("not signed in")?;
    let saved = std::fs::read_to_string(state::path("anisette-server")).unwrap_or_default();
    let mut lines = saved.lines();
    let server = lines.next().filter(|s| !s.trim().is_empty()).unwrap_or(SERVERS[0]).trim().to_string();
    let gs = GrandSlam::connect(&server, lines.next() == Some("advertised")).await?;
    if !account.token_fresh() {
        app_token(&gs, &mut account).await.map_err(|e| e.to_string())?;
        let _ = account.save();
    }
    Ok((gs, account))
}

fn preferred_servers() -> Vec<String> {
    let mut list: Vec<String> = SERVERS.iter().map(|s| s.to_string()).collect();
    if let Some(saved) = std::fs::read_to_string(state::path("anisette-server")).ok()
        .and_then(|s| s.lines().next().map(|l| l.trim().to_string())).filter(|s| !s.is_empty())
    {
        list.retain(|s| *s != saved);
        list.insert(0, saved);
    }
    list
}
