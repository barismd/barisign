//! The catalog: BariStore's own picks (AltStore, SideStore, LiveContainer +
//! SideStore, LiveContainer; stable and nightly, resolved live from each
//! project's GitHub releases) plus every app from the user's AltSources.
//! An app offered by several sources keeps one entry with one version per
//! source, so the user picks where it comes from.

use serde::{Deserialize, Serialize};
use serde_json::Value as Json;
use std::collections::BTreeMap;
use std::time::Duration;

use crate::{logf, state};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Source {
    pub url: String,
    pub name: String,
    #[serde(default)]
    pub error: String,
}

/// Open-source, widely used AltSources: preloaded on first run, and offered
/// again under Sources › Suggested once removed.
pub const DEFAULT_SOURCES: &[(&str, &str)] = &[
    ("https://community-apps.sidestore.io/sidecommunity.json", "SideStore Community"),
    ("https://stikdebug.xyz/index.json", "StikDebug"),
    ("https://alt.getutm.app", "UTM"),
    ("https://provenance-emu.com/apps.json", "Provenance"),
    ("https://raw.githubusercontent.com/Aidoku/Aidoku/altstore/apps.json", "Aidoku"),
    ("https://xitrix.github.io/iTorrent/AltStore.json", "iTorrent"),
    ("https://raw.githubusercontent.com/chachillie/Flycast-iOS/main/flycast-ios.json", "Flycast"),
];

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Version {
    pub source: String,       // source name ("BariStore" for the picks)
    pub source_url: String,
    pub channel: String,      // "stable" | "nightly" | ""
    pub version: String,
    pub date: String,
    pub url: String,
    pub size: u64,
    pub sha256: String,
    pub min_os: String,
    pub notes: String,
    pub warning: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct App {
    pub key: String,          // "bundle id" or "bundle id|ipa asset" for the picks
    pub bundle_id: String,
    pub name: String,
    pub developer: String,
    pub subtitle: String,
    pub description: String,
    pub icon: String,
    pub tint: String,
    pub category: String,
    pub featured: bool,
    pub versions: Vec<Version>,
}

pub fn sources() -> Vec<Source> {
    match std::fs::read(state::path("sources.json")).ok().and_then(|b| serde_json::from_slice(&b).ok()) {
        Some(list) => list,
        None => DEFAULT_SOURCES.iter().map(|(u, n)| Source { url: u.to_string(), name: n.to_string(), error: String::new() }).collect(),
    }
}

/// Default sources the user's list does not have.
pub fn suggested(list: &[Source]) -> Vec<Source> {
    DEFAULT_SOURCES.iter().filter(|(u, _)| !list.iter().any(|s| s.url == *u))
        .map(|(u, n)| Source { url: u.to_string(), name: n.to_string(), error: String::new() }).collect()
}

pub fn default_name(url: &str) -> Option<&'static str> {
    DEFAULT_SOURCES.iter().find(|(u, _)| *u == url).map(|(_, n)| *n)
}

pub fn save_sources(list: &[Source]) {
    if let Ok(b) = serde_json::to_vec_pretty(list) {
        let _ = std::fs::write(state::path("sources.json"), b);
    }
}

pub fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .user_agent(concat!("BariStore/", env!("CARGO_PKG_VERSION")))
        .timeout(Duration::from_secs(30))
        .build()
        .expect("HTTP client")
}

fn text(v: &Json, k: &str) -> String {
    match v.get(k) {
        Some(Json::String(s)) => s.clone(),
        Some(Json::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

/// Parses an AltSource document (versions[] or the legacy flat fields).
pub fn parse_source(doc: &Json, source: &Source) -> (String, Vec<App>) {
    let name = { let n = text(doc, "name"); if n.is_empty() { source.name.clone() } else { n } };
    let mut apps = Vec::new();
    for a in doc.get("apps").and_then(Json::as_array).into_iter().flatten() {
        let bundle_id = text(a, "bundleIdentifier");
        if bundle_id.is_empty() {
            continue;
        }
        let mut versions: Vec<Version> = a.get("versions").and_then(Json::as_array).into_iter().flatten()
            .take(1)
            .map(|v| Version {
                source: name.clone(), source_url: source.url.clone(), channel: String::new(),
                version: text(v, "version"), date: text(v, "date"), url: text(v, "downloadURL"),
                size: v.get("size").and_then(Json::as_u64).unwrap_or(0), sha256: text(v, "sha256"),
                min_os: text(v, "minOSVersion"), notes: text(v, "localizedDescription"), warning: String::new(),
            })
            .collect();
        if versions.is_empty() && !text(a, "downloadURL").is_empty() {
            versions.push(Version {
                source: name.clone(), source_url: source.url.clone(), channel: String::new(),
                version: text(a, "version"), date: text(a, "versionDate"), url: text(a, "downloadURL"),
                size: a.get("size").and_then(Json::as_u64).unwrap_or(0), sha256: String::new(),
                min_os: text(a, "minOSVersion"), notes: text(a, "versionDescription"), warning: String::new(),
            });
        }
        if versions.iter().all(|v| v.url.is_empty()) {
            continue;
        }
        apps.push(App {
            key: bundle_id.clone(), bundle_id, name: text(a, "name"), developer: text(a, "developerName"),
            subtitle: text(a, "subtitle"), description: text(a, "localizedDescription"),
            icon: text(a, "iconURL"), tint: text(a, "tintColor"), category: text(a, "category"),
            featured: false, versions,
        });
    }
    (name, apps)
}

struct Pick {
    name: &'static str,
    bundle_id: &'static str,
    repo: &'static str,
    asset: &'static str,
    developer: &'static str,
    subtitle: &'static str,
    icon: &'static str,
    nightly: bool,
}

const PICKS: &[Pick] = &[
    Pick { name: "SideStore", bundle_id: "com.SideStore.SideStore", repo: "SideStore/SideStore", asset: "SideStore.ipa",
           developer: "SideStore Team", subtitle: "Sideload apps and refresh them on your iPhone", nightly: true,
           icon: "https://raw.githubusercontent.com/SideStore/SideStore/develop/AltStore/Resources/Icons.xcassets/AppIcon.appiconset/180.png" },
    Pick { name: "LiveContainer + SideStore", bundle_id: "com.kdt.livecontainer", repo: "LiveContainer/LiveContainer",
           asset: "LiveContainer+SideStore.ipa", developer: "LiveContainer", nightly: true,
           subtitle: "Run apps inside one container, with SideStore built in",
           icon: "https://raw.githubusercontent.com/LiveContainer/LiveContainer/main/screenshots/AppIcon1024.png" },
    Pick { name: "LiveContainer", bundle_id: "com.kdt.livecontainer", repo: "LiveContainer/LiveContainer",
           asset: "LiveContainer.ipa", developer: "LiveContainer", nightly: true,
           subtitle: "Run apps without installing them, beyond the 3-app limit",
           icon: "https://raw.githubusercontent.com/LiveContainer/LiveContainer/main/screenshots/AppIcon1024.png" },
    Pick { name: "AltStore Classic", bundle_id: "com.rileytestut.AltStore", repo: "altstoreio/AltStore", asset: ".ipa",
           developer: "Riley Testut", subtitle: "The original alternative app store", nightly: false,
           // AltStore 2.0's icon, at the commit that added it (the branch moved).
           icon: "https://raw.githubusercontent.com/altstoreio/AltStore/252bea18795f8beef2e1ea5aa8ea4d36427f675c/AltStore/Resources/Assets.xcassets/AppIcon.appiconset/AltStore-180.png" },
];

fn release_version(rel: &Json, asset: &Json, channel: &str, pick: &Pick) -> Version {
    let body = text(rel, "body");
    let title = text(rel, "name");
    let upper = format!("{title}\n{body}").to_uppercase();
    let mut warning = String::new();
    if channel == "nightly" {
        warning = "Nightly builds are pre-release and may be unstable.".into();
    }
    if upper.contains("OUTDATED") {
        warning = "The developers mark this build as outdated and recommend the nightly.".into();
    }
    let digest = text(asset, "digest");
    Version {
        source: "BariStore".into(),
        source_url: format!("https://github.com/{}/releases", pick.repo),
        channel: channel.into(),
        version: if channel == "nightly" { format!("nightly {}", &text(asset, "updated_at").get(..10).unwrap_or("")) } else { text(rel, "tag_name") },
        date: text(asset, "updated_at").get(..10).unwrap_or("").to_string(),
        url: text(asset, "browser_download_url"),
        size: asset.get("size").and_then(Json::as_u64).unwrap_or(0),
        sha256: digest.strip_prefix("sha256:").unwrap_or("").to_string(),
        min_os: String::new(),
        notes: body.chars().take(1500).collect(),
        warning,
    }
}

fn is_unusable(rel: &Json) -> bool {
    let all = format!("{}\n{}", text(rel, "name"), text(rel, "body")).to_uppercase();
    all.contains("DO NOT USE") || all.contains("SIGN-IN IS BROKEN")
}

fn stable_tag(tag: &str) -> bool {
    let t = tag.to_lowercase();
    !(t.contains("alpha") || t.contains("beta") || t.contains("rc") || t == "nightly")
}

/// Stable: the newest non-pre-release whose tag is not alpha/beta/rc; if the
/// developers mark it unusable, no stable is offered (we do not fall back to an
/// older one). Nightly: the release tagged "nightly" (rolling; its asset date
/// is the version shown).
async fn picks(http: &reqwest::Client) -> Vec<App> {
    let mut out = Vec::new();
    let mut cache: BTreeMap<&str, Vec<Json>> = BTreeMap::new();
    for pick in PICKS {
        if !cache.contains_key(pick.repo) {
            let url = format!("https://api.github.com/repos/{}/releases?per_page=30", pick.repo);
            let list = match http.get(&url).header("Accept", "application/vnd.github+json").send().await {
                Ok(r) if r.status().is_success() => r.json::<Vec<Json>>().await.unwrap_or_default(),
                Ok(r) => { logf!("store: {url}: HTTP {}", r.status()); Vec::new() }
                Err(e) => { logf!("store: {url}: {e}"); Vec::new() }
            };
            cache.insert(pick.repo, list);
        }
        let releases = &cache[pick.repo];
        let asset_of = |rel: &Json| -> Option<Json> {
            rel.get("assets")?.as_array()?.iter().find(|a| {
                let n = text(a, "name");
                if pick.asset.starts_with('.') { n.ends_with(pick.asset) } else { n == pick.asset }
            }).cloned()
        };
        let mut versions = Vec::new();
        if let Some(rel) = releases.iter().find(|r| r.get("draft") != Some(&Json::Bool(true))
            && r.get("prerelease") != Some(&Json::Bool(true)) && stable_tag(&text(r, "tag_name")))
        {
            if !is_unusable(rel) {
                if let Some(a) = asset_of(rel) {
                    versions.push(release_version(rel, &a, "stable", pick));
                }
            }
        }
        if pick.nightly {
            if let Some(rel) = releases.iter().find(|r| text(r, "tag_name") == "nightly") {
                if let Some(a) = asset_of(rel) {
                    versions.push(release_version(rel, &a, "nightly", pick));
                }
            }
        }
        if versions.is_empty() {
            continue;
        }
        out.push(App {
            key: format!("{}|{}", pick.bundle_id, pick.asset),
            bundle_id: pick.bundle_id.into(), name: pick.name.into(), developer: pick.developer.into(),
            subtitle: pick.subtitle.into(), description: String::new(), icon: pick.icon.into(),
            tint: String::new(), category: "BariStore picks".into(), featured: true, versions,
        });
    }
    out
}

/// Fetches everything (concurrently); falls back to the cached catalog for
/// sources that do not answer.
pub async fn refresh() -> (Vec<App>, Vec<Source>) {
    let http = client();
    let mut list = sources();
    let fetches = list.iter().map(|s| {
        let http = http.clone();
        let s = s.clone();
        async move {
            let r = http.get(&s.url).send().await;
            let doc = match r {
                Ok(r) if r.status().is_success() => r.json::<Json>().await.map_err(|e| format!("not an AltSource: {e}")),
                Ok(r) => Err(format!("the source answered HTTP {}", r.status().as_u16())),
                Err(e) if e.is_timeout() => Err("the source did not answer in time".into()),
                Err(e) => { logf!("store: {}: {e}", s.url); Err("could not reach the source".into()) }
            };
            (s, doc)
        }
    });
    let (picked, results) = tokio::join!(picks(&http), futures_util::future::join_all(fetches));
    let cached = cached_catalog();
    let mut merged: BTreeMap<String, App> = BTreeMap::new();
    for app in picked {
        merged.insert(app.key.clone(), app);
    }
    if !merged.values().any(|a| a.featured) {
        for app in cached.iter().filter(|a| a.featured) {
            merged.insert(app.key.clone(), app.clone());
        }
    }
    for (i, (source, doc)) in results.into_iter().enumerate() {
        let apps = match doc {
            Ok(doc) => {
                let (name, apps) = parse_source(&doc, &source);
                list[i].name = name;
                list[i].error.clear();
                apps
            }
            Err(e) => {
                list[i].error = e;
                cached.iter().filter(|a| a.versions.iter().any(|v| v.source_url == source.url)).cloned().collect()
            }
        };
        for app in apps {
            match merged.get_mut(&app.key) {
                Some(existing) if !existing.featured => {
                    for v in app.versions {
                        if !existing.versions.iter().any(|x| x.source_url == v.source_url) {
                            existing.versions.push(v);
                        }
                    }
                    if existing.icon.is_empty() {
                        existing.icon = app.icon;
                    }
                }
                Some(_) => {}
                None => {
                    merged.insert(app.key.clone(), app);
                }
            }
        }
    }
    let catalog: Vec<App> = merged.into_values().collect();
    if let Ok(b) = serde_json::to_vec(&catalog) {
        let _ = std::fs::write(state::path("catalog.json"), b);
    }
    save_sources(&list);
    (catalog, list)
}

pub fn cached_catalog() -> Vec<App> {
    std::fs::read(state::path("catalog.json")).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default()
}

/// Downloads `v` into ~/.baristore/ipas/, reusing a complete earlier copy.
pub async fn download(v: &Version, progress: &(dyn Fn(u64, u64) + Send + Sync)) -> Result<std::path::PathBuf, String> {
    use futures_util::StreamExt;
    use sha2::{Digest, Sha256};
    let dir = state::path("ipas");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let name = hex::encode(&Sha256::digest(format!("{}|{}|{}", v.url, v.version, v.date))[..12]);
    let path = dir.join(format!("{name}.ipa"));
    if let Ok(data) = std::fs::read(&path) {
        let size_ok = v.size == 0 || data.len() as u64 == v.size;
        let sha_ok = v.sha256.is_empty() || hex::encode(Sha256::digest(&data)) == v.sha256.to_lowercase();
        if size_ok && sha_ok {
            return Ok(path);
        }
    }
    let resp = client().get(&v.url).timeout(Duration::from_secs(1800)).send().await
        .map_err(|e| format!("download failed: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!("download failed: HTTP {}", resp.status()));
    }
    let total = resp.content_length().unwrap_or(v.size);
    let mut data = Vec::with_capacity(total as usize);
    let mut stream = resp.bytes_stream();
    while let Some(chunk) = stream.next().await {
        data.extend_from_slice(&chunk.map_err(|e| format!("download broke: {e}"))?);
        progress(data.len() as u64, total);
    }
    if v.size > 0 && data.len() as u64 != v.size {
        return Err(format!("download incomplete ({} of {} bytes)", data.len(), v.size));
    }
    if !v.sha256.is_empty() && hex::encode(Sha256::digest(&data)) != v.sha256.to_lowercase() {
        return Err("the download does not match its published SHA-256".into());
    }
    if data.get(..2) != Some(b"PK") {
        return Err("the download is not an IPA".into());
    }
    std::fs::write(&path, &data).map_err(|e| e.to_string())?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_versions_and_legacy() {
        let doc: Json = serde_json::json!({"name": "Demo", "apps": [
            {"name": "A", "bundleIdentifier": "a.b", "versions": [{"version": "2", "downloadURL": "https://x/a.ipa", "size": 5}]},
            {"name": "B", "bundleIdentifier": "c.d", "version": "1", "downloadURL": "https://x/b.ipa"},
            {"name": "C", "bundleIdentifier": ""}
        ]});
        let src = Source { url: "https://s".into(), name: "S".into(), error: String::new() };
        let (name, apps) = parse_source(&doc, &src);
        assert_eq!(name, "Demo");
        assert_eq!(apps.len(), 2);
        assert_eq!(apps[0].versions[0].version, "2");
        assert_eq!(apps[1].versions[0].url, "https://x/b.ipa");
    }

    #[test]
    fn unusable_and_tags() {
        assert!(is_unusable(&serde_json::json!({"name": "0.6.4 (DO NOT USE)", "body": ""})));
        assert!(!stable_tag("0.7.0-alpha"));
        assert!(stable_tag("3.8.0"));
    }
}
