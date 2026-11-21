//! IPA in memory: find the bundles, rewrite identifiers, embed profiles, sign
//! deepest-first, seal CodeResources, check the result, write the IPA.

use plist::{Dictionary, Value};
use sha1::Sha1;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::io::{Cursor, Read, Write};

use super::codesign::{self, Identity, Target};

#[derive(Clone)]
pub struct Entry {
    pub data: Vec<u8>,
    pub mode: u32,
    pub symlink: bool,
}

pub struct Ipa {
    pub files: BTreeMap<String, Entry>,
    order: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Kind {
    App,
    Extension,
    Framework,
}

#[derive(Clone, Debug)]
pub struct Bundle {
    pub dir: String,          // "Payload/X.app/PlugIns/W.appex"
    pub kind: Kind,
    pub id: String,
    pub name: String,
    pub version: String,
    pub executable: String,
    pub info: Dictionary,
}

impl Bundle {
    pub fn depth(&self) -> usize {
        self.dir.matches('/').count()
    }
}

/// What the portal produced for one app or extension bundle.
pub struct Signing {
    pub new_id: String,
    pub profile: Vec<u8>,
    pub entitlements: Dictionary,
}

pub struct Plan {
    pub identity: Identity,
    /// keyed by bundle dir; frameworks need no entry
    pub bundles: HashMap<String, Signing>,
    /// extra Info.plist keys per bundle dir (ALTAppGroups, ALTCertificateID…)
    pub info_edits: HashMap<String, Dictionary>,
    /// files to add or replace (path -> bytes)
    pub add_files: Vec<(String, Vec<u8>)>,
}

fn is_bundle_dir(name: &str) -> Option<Kind> {
    if name.ends_with(".app") {
        Some(Kind::App)
    } else if name.ends_with(".appex") {
        Some(Kind::Extension)
    } else if name.ends_with(".framework") {
        Some(Kind::Framework)
    } else {
        None
    }
}

impl Ipa {
    pub fn read(bytes: &[u8]) -> Result<Ipa, String> {
        let mut zip = zip::ZipArchive::new(Cursor::new(bytes)).map_err(|e| format!("not an IPA (zip): {e}"))?;
        let mut files = BTreeMap::new();
        let mut order = Vec::new();
        for i in 0..zip.len() {
            let mut f = zip.by_index(i).map_err(|e| e.to_string())?;
            if f.is_dir() {
                continue;
            }
            let name = f.name().to_string();
            if name.split('/').any(|p| p == "..") || name.starts_with('/') {
                return Err(format!("unsafe path in IPA: {name}"));
            }
            let mode = f.unix_mode().unwrap_or(0o644);
            let symlink = mode & 0o170000 == 0o120000;
            let mut data = Vec::with_capacity(f.size() as usize);
            f.read_to_end(&mut data).map_err(|e| e.to_string())?;
            order.push(name.clone());
            files.insert(name, Entry { data, mode, symlink });
        }
        Ok(Ipa { files, order })
    }

    pub fn bundles(&self) -> Result<Vec<Bundle>, String> {
        let mut dirs: Vec<String> = Vec::new();
        for path in self.files.keys() {
            let parts: Vec<&str> = path.split('/').collect();
            let mut prefix = String::new();
            for p in &parts[..parts.len().saturating_sub(1)] {
                if !prefix.is_empty() {
                    prefix.push('/');
                }
                prefix.push_str(p);
                if is_bundle_dir(p).is_some() && !dirs.contains(&prefix) {
                    dirs.push(prefix.clone());
                }
            }
        }
        let mut out = Vec::new();
        for dir in dirs {
            let Some(info_entry) = self.files.get(&format!("{dir}/Info.plist")) else { continue };
            let Ok(Value::Dictionary(info)) = plist::from_bytes::<Value>(&info_entry.data) else { continue };
            let text = |k: &str| info.get(k).and_then(Value::as_string).unwrap_or("").to_string();
            let executable = text("CFBundleExecutable");
            if executable.is_empty() || !self.files.contains_key(&format!("{dir}/{executable}")) {
                continue;
            }
            let leaf = dir.rsplit('/').next().unwrap_or("");
            out.push(Bundle {
                kind: is_bundle_dir(leaf).unwrap(),
                id: text("CFBundleIdentifier"),
                name: [text("CFBundleDisplayName"), text("CFBundleName"), executable.clone()]
                    .into_iter().find(|s| !s.is_empty()).unwrap_or_default(),
                version: text("CFBundleShortVersionString"),
                executable,
                dir,
                info,
            });
        }
        if !out.iter().any(|b| b.kind == Kind::App && b.dir.starts_with("Payload/") && b.depth() == 1) {
            return Err("no app found in Payload/".into());
        }
        Ok(out)
    }

    pub fn main_app(&self) -> Result<Bundle, String> {
        self.bundles()?.into_iter().find(|b| b.kind == Kind::App && b.depth() == 1)
            .ok_or_else(|| "no app found in Payload/".into())
    }

    /// Removes app extensions (PlugIns/ and Extensions/) of the main app.
    pub fn drop_extensions(&mut self) -> Result<usize, String> {
        let app = self.main_app()?;
        let before = self.files.len();
        let plugins = format!("{}/PlugIns/", app.dir);
        let extensions = format!("{}/Extensions/", app.dir);
        self.files.retain(|p, _| !p.starts_with(&plugins) && !p.starts_with(&extensions));
        self.order.retain(|p| !p.starts_with(&plugins) && !p.starts_with(&extensions));
        Ok(before - self.files.len())
    }

    fn put(&mut self, path: &str, data: Vec<u8>, mode: u32) {
        if !self.files.contains_key(path) {
            self.order.push(path.to_string());
        }
        self.files.insert(path.to_string(), Entry { data, mode, symlink: false });
    }

    fn write_info(&mut self, dir: &str, info: &Dictionary) -> Result<(), String> {
        let path = format!("{dir}/Info.plist");
        let binary = self.files.get(&path).map(|e| e.data.starts_with(b"bplist00")).unwrap_or(false);
        let mut out = Vec::new();
        let value = Value::Dictionary(info.clone());
        if binary {
            plist::to_writer_binary(&mut out, &value).map_err(|e| e.to_string())?;
        } else {
            plist::to_writer_xml(&mut out, &value).map_err(|e| e.to_string())?;
        }
        let mode = self.files.get(&path).map(|e| e.mode).unwrap_or(0o644);
        self.put(&path, out, mode);
        Ok(())
    }

    fn code_resources(&self, dir: &str, executable: &str) -> Vec<u8> {
        let prefix = format!("{dir}/");
        let mut files = Dictionary::new();
        let mut files2 = Dictionary::new();
        for (path, entry) in self.files.range(prefix.clone()..) {
            let Some(rel) = path.strip_prefix(&prefix) else { break };
            if rel.starts_with("_CodeSignature/") || rel == executable {
                continue;
            }
            let name = rel.rsplit('/').next().unwrap_or(rel);
            let omit_both = name == ".DS_Store" || rel.ends_with(".lproj/locversion.plist");
            let omit_v2 = rel == "Info.plist" || rel == "PkgInfo";
            if entry.symlink {
                if !omit_v2 && !omit_both {
                    let mut d = Dictionary::new();
                    d.insert("symlink".into(), Value::String(String::from_utf8_lossy(&entry.data).into_owned()));
                    files2.insert(rel.to_string(), Value::Dictionary(d));
                }
                continue;
            }
            if !omit_both {
                files.insert(rel.to_string(), Value::Data(Sha1::digest(&entry.data).to_vec()));
            }
            if !omit_v2 && !omit_both {
                let mut d = Dictionary::new();
                d.insert("hash2".into(), Value::Data(Sha256::digest(&entry.data).to_vec()));
                files2.insert(rel.to_string(), Value::Dictionary(d));
            }
        }
        let rule = |pairs: &[(&str, Value)]| {
            let mut d = Dictionary::new();
            for (k, v) in pairs {
                d.insert((*k).into(), v.clone());
            }
            Value::Dictionary(d)
        };
        let t = Value::Boolean(true);
        let w = |n: f64| ("weight", Value::Real(n));
        let mut rules = Dictionary::new();
        rules.insert("^.*".into(), t.clone());
        rules.insert("^.*\\.lproj/".into(), rule(&[("optional", t.clone()), w(1000.0)]));
        rules.insert("^.*\\.lproj/locversion.plist$".into(), rule(&[("omit", t.clone()), w(1100.0)]));
        rules.insert("^Base\\.lproj/".into(), rule(&[w(1010.0)]));
        rules.insert("^version.plist$".into(), t.clone());
        let mut rules2 = Dictionary::new();
        rules2.insert(".*\\.dSYM($|/)".into(), rule(&[w(11.0)]));
        rules2.insert("^(.*/)?\\.DS_Store$".into(), rule(&[("omit", t.clone()), w(2000.0)]));
        rules2.insert("^.*".into(), t.clone());
        rules2.insert("^.*\\.lproj/".into(), rule(&[("optional", t.clone()), w(1000.0)]));
        rules2.insert("^.*\\.lproj/locversion.plist$".into(), rule(&[("omit", t.clone()), w(1100.0)]));
        rules2.insert("^Base\\.lproj/".into(), rule(&[w(1010.0)]));
        rules2.insert("^Info\\.plist$".into(), rule(&[("omit", t.clone()), w(20.0)]));
        rules2.insert("^PkgInfo$".into(), rule(&[("omit", t.clone()), w(20.0)]));
        rules2.insert("^embedded\\.provisionprofile$".into(), rule(&[w(20.0)]));
        rules2.insert("^version\\.plist$".into(), rule(&[w(20.0)]));
        let mut top = Dictionary::new();
        top.insert("files".into(), Value::Dictionary(files));
        top.insert("files2".into(), Value::Dictionary(files2));
        top.insert("rules".into(), Value::Dictionary(rules));
        top.insert("rules2".into(), Value::Dictionary(rules2));
        codesign::plist_xml(&Value::Dictionary(codesign::sorted(&top)))
    }

    pub fn write(&self) -> Result<Vec<u8>, String> {
        let mut out = zip::ZipWriter::new(Cursor::new(Vec::new()));
        for path in &self.order {
            let Some(entry) = self.files.get(path) else { continue };
            if entry.symlink {
                let target = String::from_utf8_lossy(&entry.data).into_owned();
                out.add_symlink(path, target, zip::write::SimpleFileOptions::default())
                    .map_err(|e| e.to_string())?;
                continue;
            }
            let options = zip::write::SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Deflated)
                .compression_level(Some(1))
                .unix_permissions(entry.mode & 0o7777);
            out.start_file(path, options).map_err(|e| e.to_string())?;
            out.write_all(&entry.data).map_err(|e| e.to_string())?;
        }
        Ok(out.finish().map_err(|e| e.to_string())?.into_inner())
    }
}

/// The plist inside a .mobileprovision (CMS-wrapped).
pub fn profile_plist(profile: &[u8]) -> Result<Dictionary, String> {
    let start = profile.windows(5).position(|w| w == b"<?xml").ok_or("profile has no plist")?;
    let end = profile.windows(8).rposition(|w| w == b"</plist>").ok_or("profile has no plist end")? + 8;
    plist::from_bytes::<Value>(&profile[start..end]).map_err(|e| e.to_string())?
        .into_dictionary().ok_or_else(|| "profile plist is not a dictionary".into())
}

/// Signs every bundle in `ipa` according to `plan`, then self-checks.
pub fn sign(ipa: &mut Ipa, plan: &Plan, progress: &mut dyn FnMut(usize, usize)) -> Result<(), String> {
    for (path, data) in &plan.add_files {
        ipa.put(path, data.clone(), 0o644);
    }
    let mut bundles = ipa.bundles()?;
    // Identifier rewrites and Info.plist edits first: parents seal children.
    for b in bundles.iter_mut() {
        let mut info = b.info.clone();
        let mut changed = false;
        if let Some(s) = plan.bundles.get(&b.dir) {
            if b.id != s.new_id {
                info.insert("CFBundleIdentifier".into(), Value::String(s.new_id.clone()));
                if let Some(Value::Array(tasks)) = info.get("BGTaskSchedulerPermittedIdentifiers").cloned() {
                    let old = b.id.clone();
                    let new = s.new_id.clone();
                    let tasks = tasks.into_iter().map(|t| match t {
                        Value::String(x) => Value::String(x.replacen(&old, &new, 1)),
                        other => other,
                    }).collect();
                    info.insert("BGTaskSchedulerPermittedIdentifiers".into(), Value::Array(tasks));
                }
                b.id = s.new_id.clone();
                changed = true;
            }
        }
        if let Some(edits) = plan.info_edits.get(&b.dir) {
            for (k, v) in edits {
                info.insert(k.clone(), v.clone());
            }
            changed = true;
        }
        if changed {
            ipa.write_info(&b.dir, &info)?;
            b.info = info;
        }
    }
    bundles.sort_by_key(|b| std::cmp::Reverse(b.depth()));
    let dirs: Vec<String> = bundles.iter().map(|b| b.dir.clone()).collect();
    let total = bundles.len();
    for (n, b) in bundles.iter().enumerate() {
        let signing = plan.bundles.get(&b.dir);
        if b.kind != Kind::Framework && signing.is_none() {
            return Err(format!("no profile was prepared for {} ({})", b.name, b.id));
        }
        if let Some(s) = signing {
            ipa.put(&format!("{}/embedded.mobileprovision", b.dir), s.profile.clone(), 0o644);
        }
        // Loose Mach-O files (dylibs, helpers) that belong to this bundle.
        let prefix = format!("{}/", b.dir);
        let nested: Vec<&String> = dirs.iter().filter(|d| d.starts_with(&prefix)).collect();
        let main = format!("{}/{}", b.dir, b.executable);
        let loose: Vec<String> = ipa.files.iter()
            .filter(|(p, e)| p.starts_with(&prefix) && **p != main && !e.symlink
                    && !nested.iter().any(|d| p.starts_with(&format!("{d}/")))
                    && codesign::is_macho(&e.data))
            .map(|(p, _)| p.clone()).collect();
        for path in loose {
            let data = ipa.files[&path].data.clone();
            let name = path.rsplit('/').next().unwrap_or("");
            let identifier = match codesign::macho_uuid(&data) {
                Some(u) => format!("{name}-{u}"),
                None => name.to_string(),
            };
            let target = Target { identifier: &identifier, entitlements: None, info_plist: None, code_resources: None };
            let signed = codesign::sign_macho(&data, &target, &plan.identity).map_err(|e| format!("{path}: {e}"))?;
            ipa.files.get_mut(&path).unwrap().data = signed;
        }
        let resources = ipa.code_resources(&b.dir, &b.executable);
        let mode = ipa.files.get(&format!("{}/_CodeSignature/CodeResources", b.dir)).map(|e| e.mode).unwrap_or(0o644);
        ipa.put(&format!("{}/_CodeSignature/CodeResources", b.dir), resources.clone(), mode);
        let info_raw = ipa.files.get(&format!("{}/Info.plist", b.dir)).map(|e| e.data.clone());
        let entitlements = signing.map(|s| &s.entitlements);
        let target = Target { identifier: &b.id, entitlements, info_plist: info_raw.as_deref(), code_resources: Some(&resources) };
        let data = ipa.files[&main].data.clone();
        let signed = codesign::sign_macho(&data, &target, &plan.identity).map_err(|e| format!("{main}: {e}"))?;
        ipa.files.get_mut(&main).unwrap().data = signed;
        progress(n + 1, total);
    }
    verify(ipa)
}

/// Recomputes every page hash and the Info/CodeResources slots of each main
/// executable from the final bytes. Catches signer bugs before an install.
pub fn verify(ipa: &Ipa) -> Result<(), String> {
    for b in ipa.bundles()? {
        let main = format!("{}/{}", b.dir, b.executable);
        let data = &ipa.files[&main].data;
        let slice = thin_arm64(data).ok_or_else(|| format!("{main}: no arm64 slice"))?;
        let cd = code_directory_of(slice).ok_or_else(|| format!("{main}: no CodeDirectory"))?;
        let be = |o: usize| u32::from_be_bytes(cd[o..o + 4].try_into().unwrap()) as usize;
        let (hash_off, special, pages, limit) = (be(16), be(24), be(28), be(32));
        for p in 0..pages {
            let start = p * codesign::PAGE;
            let end = (start + codesign::PAGE).min(limit);
            let want = &cd[hash_off + p * 32..hash_off + p * 32 + 32];
            if Sha256::digest(&slice[start..end]).as_slice() != want {
                return Err(format!("{main}: page {p} hash mismatch"));
            }
        }
        let slot = |n: usize| (n <= special).then(|| &cd[hash_off - n * 32..hash_off - n * 32 + 32]);
        if let (Some(want), Some(info)) = (slot(1), ipa.files.get(&format!("{}/Info.plist", b.dir))) {
            if Sha256::digest(&info.data).as_slice() != want {
                return Err(format!("{main}: Info.plist slot mismatch"));
            }
        }
        if let (Some(want), Some(res)) = (slot(3), ipa.files.get(&format!("{}/_CodeSignature/CodeResources", b.dir))) {
            if Sha256::digest(&res.data).as_slice() != want {
                return Err(format!("{main}: CodeResources slot mismatch"));
            }
        }
    }
    Ok(())
}

fn thin_arm64(data: &[u8]) -> Option<&[u8]> {
    if data.get(..4)? == [0xCF, 0xFA, 0xED, 0xFE] {
        return Some(data);
    }
    if data.get(..4)? != [0xCA, 0xFE, 0xBA, 0xBE] {
        return None;
    }
    let be = |o: usize| u32::from_be_bytes(data[o..o + 4].try_into().unwrap());
    for i in 0..be(4) as usize {
        let b = 8 + i * 20;
        if be(b) == 0x0100000C {
            return data.get(be(b + 8) as usize..(be(b + 8) + be(b + 12)) as usize);
        }
    }
    None
}

fn code_directory_of(m: &[u8]) -> Option<&[u8]> {
    let le = |o: usize| u32::from_le_bytes(m[o..o + 4].try_into().unwrap());
    let ncmds = le(16) as usize;
    let mut off = 32;
    for _ in 0..ncmds {
        if le(off) == 0x1D {
            let (dataoff, size) = (le(off + 8) as usize, le(off + 12) as usize);
            let sb = m.get(dataoff..dataoff + size)?;
            let be = |o: usize| u32::from_be_bytes(sb[o..o + 4].try_into().unwrap());
            for i in 0..be(8) as usize {
                if be(12 + i * 8) == 0 {
                    let at = be(16 + i * 8) as usize;
                    let len = u32::from_be_bytes(sb[at + 4..at + 8].try_into().unwrap()) as usize;
                    return sb.get(at..at + len);
                }
            }
        }
        off += le(off + 4) as usize;
    }
    None
}
