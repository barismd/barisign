//! Developer services (developerservices2.apple.com, protocol QH65B2): teams,
//! devices, development certificates, App IDs, app groups, profiles, and the
//! v1 bundleIds PATCH that adds Increased Memory Limit.

use plist::{Dictionary, Value};
use rsa::pkcs1::EncodeRsaPublicKey;
use rsa::pkcs8::{DecodePrivateKey, EncodePrivateKey};
use rsa::{Pkcs1v15Sign, RsaPrivateKey};
use sha2::{Digest, Sha256};

use super::gsa::{Account, GrandSlam};
use crate::{der, logf, state};

pub const MACHINE_NAME: &str = "BariStore";

#[derive(Debug)]
/// A development certificate as the portal lists it.
pub struct DevCert {
    pub der: Vec<u8>,
    pub key: RsaPrivateKey,
    /// What AltStore-family apps match `ALTCertificateID` against.
    pub serial: String,
    /// The machineId sent with the CSR: the password of ALTCertificate.p12.
    pub machine_id: String,
}

impl DevCert {
    fn listed(c: &Dictionary, content: &[u8], key: RsaPrivateKey) -> DevCert {
        let serial = Some(s(c, "serialNumber")).filter(|v| !v.is_empty()).unwrap_or_else(|| s(c, "serialNum"));
        DevCert { der: content.to_vec(), key, serial, machine_id: s(c, "machineId") }
    }
}

pub struct PortalError {
    pub code: i64,
    pub message: String,
}

impl std::fmt::Display for PortalError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.code == 0 { write!(f, "{}", self.message) } else { write!(f, "{} (Apple code {})", self.message, self.code) }
    }
}

fn err(message: impl Into<String>) -> PortalError {
    PortalError { code: 0, message: message.into() }
}

#[derive(Clone, Debug)]
pub struct Team {
    pub id: String,
    pub name: String,
    pub kind: String,
}

#[derive(Clone, Debug)]
pub struct AppId {
    pub id: String,
    pub identifier: String,
    pub name: String,
    pub features: Dictionary,
}

pub struct Portal<'a> {
    pub gs: &'a GrandSlam,
    pub account: &'a Account,
}

fn s(d: &Dictionary, k: &str) -> String {
    d.get(k).and_then(Value::as_string).unwrap_or("").to_string()
}

fn list<'d>(d: &'d Dictionary, k: &str) -> Vec<&'d Dictionary> {
    d.get(k).and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_dictionary).collect()).unwrap_or_default()
}

impl Portal<'_> {
    async fn headers(&self) -> Result<reqwest::header::HeaderMap, PortalError> {
        let a = self.gs.headers().await.map_err(err)?;
        let mut h = self.gs.base_headers(false);
        for (k, v) in a.pairs() {
            h.insert(k, v.parse().map_err(|_| err("bad anisette header"))?);
        }
        h.insert("X-Apple-GS-Token", self.account.xcode_token.parse().map_err(|_| err("bad token"))?);
        h.insert("X-Apple-I-Identity-Id", self.account.adsid.parse().map_err(|_| err("bad adsid"))?);
        Ok(h)
    }

    pub async fn call(&self, endpoint: &str, team: Option<&str>, mut body: Dictionary) -> Result<Dictionary, PortalError> {
        let platform = if endpoint == "listTeams" { "" } else { "ios/" };
        let url = format!("https://developerservices2.apple.com/services/QH65B2/{platform}{endpoint}.action?clientId=XABBG36SBA");
        body.insert("clientId".into(), Value::String("XABBG36SBA".into()));
        body.insert("protocolVersion".into(), Value::String("QH65B2".into()));
        body.insert("requestId".into(), Value::String(uuid::Uuid::new_v4().to_string().to_uppercase()));
        body.insert("userLocale".into(), Value::Array(vec![Value::String("en_US".into())]));
        if let Some(team) = team {
            body.insert("teamId".into(), Value::String(team.into()));
        }
        let resp = self.gs.http.post(&url).headers(self.headers().await?)
            .body(crate::pairing::record::plist_xml(&body)).send().await
            .map_err(|e| err(format!("developer services: {e}")))?;
        let status = resp.status();
        let text = resp.text().await.map_err(|e| err(e.to_string()))?;
        let reply: Dictionary = plist::from_bytes(text.as_bytes())
            .map_err(|_| err(format!("developer services answered HTTP {status}: {}", text.chars().take(160).collect::<String>())))?;
        let code = reply.get("resultCode").and_then(Value::as_signed_integer).unwrap_or(0);
        if code != 0 {
            let message = reply.get("userString").or(reply.get("resultString"))
                .and_then(Value::as_string).unwrap_or("refused").to_string();
            logf!("portal: {endpoint} -> {code} {message}");
            return Err(PortalError { code, message });
        }
        Ok(reply)
    }

    pub async fn teams(&self) -> Result<Vec<Team>, PortalError> {
        let r = self.call("listTeams", None, Dictionary::new()).await?;
        Ok(list(&r, "teams").into_iter().map(|t| Team { id: s(t, "teamId"), name: s(t, "name"), kind: s(t, "type") }).collect())
    }

    /// Registers the device unless the team already has it.
    pub async fn ensure_device(&self, team: &str, udid: &str, name: &str) -> Result<(), PortalError> {
        let r = self.call("listDevices", Some(team), Dictionary::new()).await?;
        let norm = |u: &str| u.replace('-', "").to_lowercase();
        if list(&r, "devices").iter().any(|d| norm(&s(d, "deviceNumber")) == norm(udid)) {
            return Ok(());
        }
        let mut body = Dictionary::new();
        body.insert("deviceNumber".into(), Value::String(udid.into()));
        body.insert("name".into(), Value::String(name.into()));
        self.call("addDevice", Some(team), body).await.map(|_| ())
    }

    pub async fn certificates(&self, team: &str) -> Result<Vec<Dictionary>, PortalError> {
        let r = self.call("listAllDevelopmentCerts", Some(team), Dictionary::new()).await?;
        Ok(list(&r, "certificates").into_iter().cloned().collect())
    }

    pub async fn revoke(&self, team: &str, serial: &str) -> Result<(), PortalError> {
        let mut body = Dictionary::new();
        body.insert("serialNumber".into(), Value::String(serial.into()));
        self.call("revokeDevelopmentCert", Some(team), body).await.map(|_| ())
    }

    /// Our development certificate and its key, reused while Apple still
    /// lists it; a new one otherwise. When the team is full (7460), only
    /// certificates BariStore itself made are revoked: other tools' certificates
    /// keep their apps running, so the error names them instead.
    pub async fn certificate(&self, team: &str) -> Result<DevCert, PortalError> {
        let key = load_or_create_key().map_err(err)?;
        let modulus = key.to_public_key().to_pkcs1_der().map_err(|e| err(e.to_string()))?.as_bytes().to_vec();
        let certs = self.certificates(team).await?;
        for c in &certs {
            if let Some(content) = c.get("certContent").and_then(Value::as_data) {
                if let Ok(info) = der::cert_info(content) {
                    if spki_matches(info.spki, &modulus) {
                        return Ok(DevCert::listed(c, content, key));
                    }
                }
            }
        }
        let csr = build_csr(&key).map_err(err)?;
        let mut body = Dictionary::new();
        body.insert("csrContent".into(), Value::String(csr));
        body.insert("machineName".into(), Value::String(MACHINE_NAME.into()));
        let machine_id = uuid::Uuid::new_v4().to_string().to_uppercase();
        body.insert("machineId".into(), Value::String(machine_id.clone()));
        let submitted = match self.call("submitDevelopmentCSR", Some(team), body.clone()).await {
            Err(e) if e.code == 7460 => {
                let ours: Vec<String> = certs.iter()
                    .filter(|c| s(c, "machineName") == MACHINE_NAME).map(|c| s(c, "serialNumber")).collect();
                if ours.is_empty() {
                    let names: Vec<String> = certs.iter().map(|c| s(c, "machineName")).collect();
                    return Err(err(format!(
                        "your Apple ID already has the maximum number of development certificates ({}). \
                         Revoke one in Options › Apple ID (apps signed with it stop opening).", names.join(", "))));
                }
                for serial in ours {
                    self.revoke(team, &serial).await?;
                }
                self.call("submitDevelopmentCSR", Some(team), body).await?
            }
            other => other?,
        };
        let request_id = submitted.get("certRequest").and_then(Value::as_dictionary)
            .map(|d| s(d, "certRequestId")).unwrap_or_default();
        for c in self.certificates(team).await? {
            let content = c.get("certContent").and_then(Value::as_data).unwrap_or(&[]);
            let matches_id = !request_id.is_empty() && s(&c, "certificateId") == request_id;
            let matches_key = der::cert_info(content).map(|i| spki_matches(i.spki, &modulus)).unwrap_or(false);
            if matches_id || matches_key {
                let mut cert = DevCert::listed(&c, content, key);
                if cert.machine_id.is_empty() {
                    cert.machine_id = machine_id;
                }
                return Ok(cert);
            }
        }
        Err(err("Apple issued a certificate but does not list it yet; try again in a minute"))
    }

    pub async fn app_ids(&self, team: &str) -> Result<Vec<AppId>, PortalError> {
        let r = self.call("listAppIds", Some(team), Dictionary::new()).await?;
        Ok(list(&r, "appIds").into_iter().map(to_app_id).collect())
    }

    pub async fn ensure_app_id(&self, team: &str, identifier: &str, name: &str) -> Result<AppId, PortalError> {
        if let Some(existing) = self.app_ids(team).await?.into_iter().find(|a| a.identifier == identifier) {
            return Ok(existing);
        }
        let mut body = Dictionary::new();
        body.insert("identifier".into(), Value::String(identifier.into()));
        body.insert("name".into(), Value::String(clean_name(name)));
        let r = self.call("addAppId", Some(team), body).await?;
        r.get("appId").and_then(Value::as_dictionary).map(to_app_id).ok_or_else(|| err("addAppId answered without an App ID"))
    }

    pub async fn update_features(&self, team: &str, app: &AppId, features: Dictionary) -> Result<AppId, PortalError> {
        let mut body = features;
        body.insert("appIdId".into(), Value::String(app.id.clone()));
        let r = self.call("updateAppId", Some(team), body).await?;
        Ok(r.get("appId").and_then(Value::as_dictionary).map(to_app_id).unwrap_or_else(|| app.clone()))
    }

    /// Creates the group when missing, enables App Groups on the App ID and
    /// assigns the group. Returns false when the team refuses groups.
    pub async fn assign_group(&self, team: &str, app: &AppId, group: &str) -> Result<bool, PortalError> {
        if app.features.get("APG3427HIY").and_then(Value::as_boolean) != Some(true) {
            let mut f = Dictionary::new();
            f.insert("APG3427HIY".into(), Value::Boolean(true));
            if self.update_features(team, app, f).await.is_err() {
                return Ok(false);
            }
        }
        let r = self.call("listApplicationGroups", Some(team), Dictionary::new()).await?;
        let mut group_id = list(&r, "applicationGroupList").into_iter()
            .find(|g| s(g, "identifier") == group).map(|g| s(g, "applicationGroup"));
        if group_id.is_none() {
            let mut body = Dictionary::new();
            body.insert("identifier".into(), Value::String(group.into()));
            body.insert("name".into(), Value::String(clean_name(&format!("BariStore {group}"))));
            match self.call("addApplicationGroup", Some(team), body).await {
                Ok(r) => group_id = r.get("applicationGroup").and_then(Value::as_dictionary).map(|g| s(g, "applicationGroup")),
                Err(e) => {
                    logf!("portal: cannot create group {group}: {e}");
                    return Ok(false);
                }
            }
        }
        let Some(group_id) = group_id else { return Ok(false) };
        let mut body = Dictionary::new();
        body.insert("appIdId".into(), Value::String(app.id.clone()));
        body.insert("applicationGroups".into(), Value::String(group_id));
        Ok(self.call("assignApplicationGroupToAppId", Some(team), body).await.is_ok())
    }

    pub async fn profile(&self, team: &str, app: &AppId) -> Result<Vec<u8>, PortalError> {
        let mut body = Dictionary::new();
        body.insert("appIdId".into(), Value::String(app.id.clone()));
        let r = self.call("downloadTeamProvisioningProfile", Some(team), body).await?;
        r.get("provisioningProfile").and_then(Value::as_dictionary)
            .and_then(|p| p.get("encodedProfile")).and_then(Value::as_data).map(<[u8]>::to_vec)
            .ok_or_else(|| err("Apple sent no provisioning profile"))
    }

    /// Increased Memory Limit on an App ID (works on free teams, as GetMoreRam
    /// and isideload show). Needs a new profile and a reinstall to apply.
    pub async fn add_increased_memory(&self, team: &str, app: &AppId) -> Result<(), PortalError> {
        let body = serde_json::json!({"data": {
            "type": "bundleIds", "id": app.id,
            "attributes": {"hasExclusiveManagedCapabilities": false, "teamId": team, "bundleType": "bundle",
                           "identifier": app.identifier, "seedId": team, "name": app.name},
            "relationships": {"bundleIdCapabilities": {"data": [{
                "type": "bundleIdCapabilities",
                "attributes": {"settings": [], "enabled": true},
                "relationships": {"capability": {"data": {"id": "INCREASED_MEMORY_LIMIT", "type": "capabilities"}}}
            }]}}
        }});
        let mut h = self.headers().await?;
        h.insert("Content-Type", "application/vnd.api+json".parse().unwrap());
        h.insert("Accept", "application/vnd.api+json".parse().unwrap());
        let resp = self.gs.http.patch(format!("https://developerservices2.apple.com/services/v1/bundleIds/{}", app.id))
            .headers(h).body(body.to_string()).send().await.map_err(|e| err(e.to_string()))?;
        if resp.status().is_success() {
            Ok(())
        } else {
            let status = resp.status();
            Err(err(format!("Apple refused Increased Memory Limit (HTTP {status}): {}",
                resp.text().await.unwrap_or_default().chars().take(200).collect::<String>())))
        }
    }
}

fn to_app_id(d: &Dictionary) -> AppId {
    AppId {
        id: s(d, "appIdId"),
        identifier: s(d, "identifier"),
        name: s(d, "name"),
        features: d.get("features").and_then(Value::as_dictionary).cloned().unwrap_or_default(),
    }
}

/// Apple accepts letters, digits and spaces in App ID names.
fn clean_name(name: &str) -> String {
    let cleaned: String = name.chars().map(|c| if c.is_ascii_alphanumeric() || c == ' ' { c } else { ' ' }).collect();
    let trimmed = cleaned.split_whitespace().collect::<Vec<_>>().join(" ");
    if trimmed.is_empty() { "BariStore App".into() } else { trimmed.chars().take(60).collect() }
}

fn spki_matches(spki: &[u8], pkcs1_public: &[u8]) -> bool {
    spki.windows(pkcs1_public.len()).any(|w| w == pkcs1_public)
}

fn load_or_create_key() -> Result<RsaPrivateKey, String> {
    let path = state::path("signing-key.der");
    if let Ok(bytes) = std::fs::read(&path) {
        if let Ok(key) = RsaPrivateKey::from_pkcs8_der(&bytes) {
            return Ok(key);
        }
    }
    logf!("portal: generating a 2048-bit signing key");
    let key = RsaPrivateKey::new(&mut rand::rngs::OsRng, 2048).map_err(|e| e.to_string())?;
    let der = key.to_pkcs8_der().map_err(|e| e.to_string())?;
    state::write_private(&path, der.as_bytes()).map_err(|e| e.to_string())?;
    Ok(key)
}

/// PKCS#10 request, PEM, subject CN/O/C as Xcode's.
fn build_csr(key: &RsaPrivateKey) -> Result<String, String> {
    let rdn = |o: &str, v: Vec<u8>| der::set(&[der::seq(&[der::oid(o), v])]);
    let subject = der::seq(&[
        rdn("2.5.4.6", der::printable("US")),
        rdn("2.5.4.10", der::utf8("BariStore")),
        rdn("2.5.4.3", der::utf8("BariStore")),
    ]);
    let public = key.to_public_key().to_pkcs1_der().map_err(|e| e.to_string())?;
    let spki = der::seq(&[der::seq(&[der::oid("1.2.840.113549.1.1.1"), der::null()]), der::bits(public.as_bytes())]);
    let info = der::seq(&[der::int(0), subject, spki, der::ctx(0, &[])]);
    let digest = Sha256::digest(&info);
    let signature = key.sign(Pkcs1v15Sign::new::<Sha256>(), &digest).map_err(|e| e.to_string())?;
    let csr = der::seq(&[info, der::seq(&[der::oid("1.2.840.113549.1.1.11"), der::null()]), der::bits(&signature)]);
    use base64::Engine;
    let b64 = base64::engine::general_purpose::STANDARD.encode(csr);
    let lines: Vec<&str> = b64.as_bytes().chunks(64).map(|c| std::str::from_utf8(c).unwrap()).collect();
    Ok(format!("-----BEGIN CERTIFICATE REQUEST-----\n{}\n-----END CERTIFICATE REQUEST-----\n", lines.join("\n")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn csr_verifies_with_openssl() {
        let key = RsaPrivateKey::new(&mut rand::rngs::OsRng, 2048).unwrap();
        let pem = build_csr(&key).unwrap();
        let path = std::env::temp_dir().join(format!("baristore-csr-{}.pem", std::process::id()));
        std::fs::write(&path, pem).unwrap();
        let out = std::process::Command::new("openssl")
            .args(["req", "-in", path.to_str().unwrap(), "-noout", "-verify", "-subject"])
            .output().unwrap();
        let _ = std::fs::remove_file(&path);
        let text = String::from_utf8_lossy(&out.stdout).to_string() + &String::from_utf8_lossy(&out.stderr);
        assert!(text.contains("verify OK") || text.contains("Certificate request self-signature verify OK"), "{text}");
        assert!(text.contains("BariStore"), "{text}");
    }

    #[test]
    fn names_are_cleaned() {
        assert_eq!(clean_name("LiveContainer+SideStore"), "LiveContainer SideStore");
        assert_eq!(clean_name("!!!"), "BariStore App");
    }
}
