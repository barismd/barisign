//! Mach-O code signatures, written the way the device-verified BariStore
//! Python signer did (and Apple's codesign does): an embedded SuperBlob with a
//! CodeDirectory v0x20400 (SHA-256, 16 KiB pages), the designated requirement,
//! XML + DER entitlements, and a detached CMS SignedData carrying Apple's
//! 1.2.840.113635.100.9.1/.2 cdhash attributes.

use plist::{Dictionary, Value};
use rsa::{Pkcs1v15Sign, RsaPrivateKey};
use sha2::{Digest, Sha256};

use crate::der;

pub const PAGE_SHIFT: u8 = 14;
pub const PAGE: usize = 1 << PAGE_SHIFT;

const LC_SEGMENT_64: u32 = 0x19;
const LC_CODE_SIGNATURE: u32 = 0x1D;
const LC_UUID: u32 = 0x1B;
const MH_EXECUTE: u32 = 2;

const CSMAGIC_REQUIREMENT: u32 = 0xFADE0C00;
const CSMAGIC_REQUIREMENTS: u32 = 0xFADE0C01;
const CSMAGIC_CODEDIRECTORY: u32 = 0xFADE0C02;
const CSMAGIC_EMBEDDED_SIGNATURE: u32 = 0xFADE0CC0;
const CSMAGIC_ENTITLEMENTS: u32 = 0xFADE7171;
const CSMAGIC_DER_ENTITLEMENTS: u32 = 0xFADE7172;
const CSMAGIC_BLOBWRAPPER: u32 = 0xFADE0B01;

const SLOT_CODEDIRECTORY: u32 = 0;
const SLOT_INFO: u32 = 1;
const SLOT_REQUIREMENTS: u32 = 2;
const SLOT_RESOURCES: u32 = 3;
const SLOT_ENTITLEMENTS: u32 = 5;
const SLOT_DER_ENTITLEMENTS: u32 = 7;
const SLOT_SIGNATURE: u32 = 0x10000;

/// What signs: the leaf certificate, its chain, the key, and the team.
pub struct Identity {
    pub leaf: Vec<u8>,
    pub chain: Vec<Vec<u8>>,
    pub key: RsaPrivateKey,
    pub team: String,
    pub common_name: String,
}

/// Per-binary inputs.
pub struct Target<'a> {
    pub identifier: &'a str,
    pub entitlements: Option<&'a Dictionary>,
    pub info_plist: Option<&'a [u8]>,
    pub code_resources: Option<&'a [u8]>,
}

pub fn is_macho(data: &[u8]) -> bool {
    matches!(data.get(..4), Some([0xCF, 0xFA, 0xED, 0xFE]) | Some([0xFE, 0xED, 0xFA, 0xCF])
        | Some([0xCE, 0xFA, 0xED, 0xFE]) | Some([0xFE, 0xED, 0xFA, 0xCE])
        | Some([0xCA, 0xFE, 0xBA, 0xBE]) | Some([0xCA, 0xFE, 0xBA, 0xBF]))
}

#[derive(Clone, Copy)]
struct Segment {
    vmsize: u64,
    fileoff: u64,
    filesize: u64,
    cmd_offset: usize,
}

struct Thin<'a> {
    data: &'a [u8],
    filetype: u32,
    ncmds: u32,
    sizeofcmds: u32,
    text: Segment,
    linkedit: Segment,
    last_fileoff: u64,
    first_section: Option<u32>,
    signature: Option<(usize, u32, u32)>, // (cmd offset, dataoff, datasize)
    uuid: Option<String>,
}

fn le32(d: &[u8], o: usize) -> u32 {
    u32::from_le_bytes(d[o..o + 4].try_into().unwrap())
}
fn le64(d: &[u8], o: usize) -> u64 {
    u64::from_le_bytes(d[o..o + 8].try_into().unwrap())
}

impl<'a> Thin<'a> {
    fn parse(data: &'a [u8]) -> Result<Thin<'a>, String> {
        if data.len() < 32 {
            return Err("truncated Mach-O".into());
        }
        match &data[..4] {
            [0xCF, 0xFA, 0xED, 0xFE] => {}
            [0xCE, 0xFA, 0xED, 0xFE] | [0xFE, 0xED, 0xFA, 0xCE] => return Err("this IPA contains a 32-bit binary; iOS 27 cannot run it".into()),
            _ => return Err("not a little-endian 64-bit Mach-O".into()),
        }
        let (filetype, ncmds, sizeofcmds) = (le32(data, 12), le32(data, 16), le32(data, 20));
        let end = 32 + sizeofcmds as usize;
        if end > data.len() {
            return Err("Mach-O load commands run past the file".into());
        }
        let (mut text, mut linkedit, mut last, mut first_section, mut signature, mut uuid) = (None, None, 0u64, None, None, None);
        let mut off = 32;
        for _ in 0..ncmds {
            if off + 8 > end {
                return Err("truncated load command".into());
            }
            let (cmd, size) = (le32(data, off), le32(data, off + 4) as usize);
            if size < 8 || off + size > end {
                return Err("bad load command size".into());
            }
            match cmd {
                LC_SEGMENT_64 => {
                    let name = &data[off + 8..off + 24];
                    let seg = Segment { vmsize: le64(data, off + 32), fileoff: le64(data, off + 40),
                                        filesize: le64(data, off + 48), cmd_offset: off };
                    if seg.filesize > 0 {
                        last = last.max(seg.fileoff);
                    }
                    let nsects = le32(data, off + 64) as usize;
                    for i in 0..nsects {
                        let s = off + 72 + i * 80;
                        if s + 80 > off + size {
                            break;
                        }
                        let o = le32(data, s + 48);
                        if o != 0 {
                            first_section = Some(first_section.map_or(o, |f: u32| f.min(o)));
                        }
                    }
                    if name.starts_with(b"__TEXT\0") {
                        text = Some(seg);
                    } else if name.starts_with(b"__LINKEDIT\0") {
                        linkedit = Some(seg);
                    }
                }
                LC_CODE_SIGNATURE if size >= 16 => signature = Some((off, le32(data, off + 8), le32(data, off + 12))),
                LC_UUID if size >= 24 => uuid = Some(hex::encode_upper(&data[off + 8..off + 24])),
                _ => {}
            }
            off += size;
        }
        Ok(Thin {
            data, filetype, ncmds, sizeofcmds,
            text: text.ok_or("no __TEXT segment")?,
            linkedit: linkedit.ok_or("no __LINKEDIT segment")?,
            last_fileoff: last, first_section, signature, uuid,
        })
    }

    fn code_end(&self) -> Result<usize, String> {
        if self.linkedit.fileoff != self.last_fileoff {
            return Err("__LINKEDIT is not the last segment; this binary cannot be re-signed safely".into());
        }
        let linkedit_end = (self.linkedit.fileoff + self.linkedit.filesize) as usize;
        if let Some((_, dataoff, datasize)) = self.signature {
            if (dataoff + datasize) as usize != self.data.len() || linkedit_end != self.data.len() {
                return Err("the existing signature is not at the end of the binary".into());
            }
            return Ok(dataoff as usize);
        }
        if linkedit_end != self.data.len() {
            return Err("the binary has data after __LINKEDIT".into());
        }
        Ok(self.data.len())
    }

    fn patched(&self, dataoff: usize, datasize: usize) -> Result<Vec<u8>, String> {
        let mut d = self.data.to_vec();
        match self.signature {
            Some((off, _, _)) => {
                d[off + 8..off + 12].copy_from_slice(&(dataoff as u32).to_le_bytes());
                d[off + 12..off + 16].copy_from_slice(&(datasize as u32).to_le_bytes());
            }
            None => {
                let at = 32 + self.sizeofcmds as usize;
                if self.first_section.map_or(true, |f| (f as usize) < at + 16) {
                    return Err("no room for a signature load command".into());
                }
                d[16..20].copy_from_slice(&(self.ncmds + 1).to_le_bytes());
                d[20..24].copy_from_slice(&(self.sizeofcmds + 16).to_le_bytes());
                for (i, v) in [LC_CODE_SIGNATURE, 16, dataoff as u32, datasize as u32].iter().enumerate() {
                    d[at + i * 4..at + i * 4 + 4].copy_from_slice(&v.to_le_bytes());
                }
            }
        }
        let size = (dataoff as u64 - self.linkedit.fileoff) + datasize as u64;
        let vmsize = self.linkedit.vmsize.max((size + 16383) & !16383);
        let c = self.linkedit.cmd_offset;
        d[c + 32..c + 40].copy_from_slice(&vmsize.to_le_bytes());
        d[c + 48..c + 56].copy_from_slice(&size.to_le_bytes());
        Ok(d)
    }
}

pub fn macho_uuid(data: &[u8]) -> Option<String> {
    Thin::parse(data).ok().and_then(|t| t.uuid)
}

fn blob(magic: u32, payload: &[u8]) -> Vec<u8> {
    let mut out = magic.to_be_bytes().to_vec();
    out.extend_from_slice(&(payload.len() as u32 + 8).to_be_bytes());
    out.extend_from_slice(payload);
    out
}

fn padded(raw: &[u8]) -> Vec<u8> {
    let mut out = (raw.len() as u32).to_be_bytes().to_vec();
    out.extend_from_slice(raw);
    while out.len() % 4 != 0 {
        out.push(0);
    }
    out
}

/// identifier "X" and anchor apple generic and certificate leaf[subject.CN] = "CN"
/// and certificate 1[field.1.2.840.113635.100.6.2.1] exists
pub fn requirements(identifier: &str, common_name: &str) -> Vec<u8> {
    let wwdr = [0x2A, 0x86, 0x48, 0x86, 0xF7, 0x63, 0x64, 0x06, 0x02, 0x01];
    let mut e = Vec::new();
    let w = |e: &mut Vec<u8>, v: u32| e.extend_from_slice(&v.to_be_bytes());
    w(&mut e, 1);
    w(&mut e, 6); w(&mut e, 2); e.extend(padded(identifier.as_bytes()));
    w(&mut e, 6); w(&mut e, 15);
    w(&mut e, 6); w(&mut e, 11); w(&mut e, 0);
    e.extend(padded(b"subject.CN")); w(&mut e, 1); e.extend(padded(common_name.as_bytes()));
    w(&mut e, 14); w(&mut e, 1); e.extend(padded(&wwdr)); w(&mut e, 0);
    let inner = blob(CSMAGIC_REQUIREMENT, &e);
    let mut out = Vec::new();
    for v in [CSMAGIC_REQUIREMENTS, 20 + inner.len() as u32, 1, 3, 20] {
        out.extend_from_slice(&v.to_be_bytes());
    }
    out.extend(inner);
    out
}

/// Dictionary with keys sorted (recursively), as codesign writes plists.
pub fn sorted(d: &Dictionary) -> Dictionary {
    let mut keys: Vec<&String> = d.keys().collect();
    keys.sort();
    let mut out = Dictionary::new();
    for k in keys {
        let v = match &d[k] {
            Value::Dictionary(inner) => Value::Dictionary(sorted(inner)),
            other => other.clone(),
        };
        out.insert(k.clone(), v);
    }
    out
}

pub fn plist_xml(value: &Value) -> Vec<u8> {
    let mut out = Vec::new();
    plist::to_writer_xml(&mut out, value).expect("plist to memory");
    out
}

fn der_value(v: &Value) -> Result<Vec<u8>, String> {
    Ok(match v {
        Value::Boolean(b) => der::tlv(0x01, &[if *b { 0xFF } else { 0 }]),
        Value::String(s) => der::utf8(s),
        Value::Integer(i) => der::int(i.as_unsigned().ok_or("negative entitlement integer")?),
        Value::Data(d) => der::octets(d),
        Value::Array(items) => der::tlv(0x30, &items.iter().map(der_value).collect::<Result<Vec<_>, _>>()?.concat()),
        Value::Dictionary(d) => der::tlv(0xB0, &der_entries(d)?),
        other => return Err(format!("entitlement value {other:?} cannot be encoded")),
    })
}

fn der_entries(d: &Dictionary) -> Result<Vec<u8>, String> {
    let mut keys: Vec<&String> = d.keys().collect();
    keys.sort();
    let mut out = Vec::new();
    for k in keys {
        out.extend(der::seq(&[der::utf8(k), der_value(&d[k])?]));
    }
    Ok(out)
}

pub fn der_entitlements(d: &Dictionary) -> Result<Vec<u8>, String> {
    Ok(der::tlv(0x70, &[der::int(1), der::tlv(0xB0, &der_entries(d)?)].concat()))
}

#[allow(clippy::too_many_arguments)]
fn code_directory(identifier: &str, team: &str, code_limit: usize, specials: &[(u32, [u8; 32])],
                  pages: &[[u8; 32]], exec_base: u64, exec_limit: u64, exec_flags: u64) -> Vec<u8> {
    let mut ident = identifier.as_bytes().to_vec();
    ident.push(0);
    let mut team_bytes = Vec::new();
    if !team.is_empty() {
        team_bytes.extend_from_slice(team.as_bytes());
        team_bytes.push(0);
    }
    let highest = specials.iter().map(|(s, _)| *s).max().unwrap_or(0);
    let header = 0x58usize;
    let team_offset = if team.is_empty() { 0 } else { header + ident.len() };
    let hash_offset = header + ident.len() + team_bytes.len() + highest as usize * 32;
    let total = hash_offset + pages.len() * 32;
    let mut d = Vec::with_capacity(total);
    for v in [CSMAGIC_CODEDIRECTORY, total as u32, 0x20400, 0, hash_offset as u32, header as u32,
              highest, pages.len() as u32, code_limit as u32] {
        d.extend_from_slice(&v.to_be_bytes());
    }
    d.extend_from_slice(&[32, 2, 0, PAGE_SHIFT]);
    for v in [0u32, 0, team_offset as u32, 0] {
        d.extend_from_slice(&v.to_be_bytes());
    }
    for v in [0u64, exec_base, exec_limit, exec_flags] {
        d.extend_from_slice(&v.to_be_bytes());
    }
    debug_assert_eq!(d.len(), header);
    d.extend(ident);
    d.extend(team_bytes);
    for slot in (1..=highest).rev() {
        let hash = specials.iter().find(|(s, _)| *s == slot).map(|(_, h)| *h).unwrap_or([0; 32]);
        d.extend_from_slice(&hash);
    }
    for p in pages {
        d.extend_from_slice(p);
    }
    d
}

fn superblob(mut blobs: Vec<(u32, Vec<u8>)>) -> Vec<u8> {
    blobs.sort_by_key(|(slot, _)| *slot);
    let index_size = 12 + 8 * blobs.len();
    let mut index = Vec::new();
    let mut body = Vec::new();
    for (slot, payload) in &blobs {
        index.extend_from_slice(&slot.to_be_bytes());
        index.extend_from_slice(&((index_size + body.len()) as u32).to_be_bytes());
        body.extend_from_slice(payload);
    }
    let mut out = Vec::new();
    for v in [CSMAGIC_EMBEDDED_SIGNATURE, (index_size + body.len()) as u32, blobs.len() as u32] {
        out.extend_from_slice(&v.to_be_bytes());
    }
    out.extend(index);
    out.extend(body);
    out
}

fn attribute(oid: &str, values: &[Vec<u8>]) -> Vec<u8> {
    der::seq(&[der::oid(oid), der::set(values)])
}

fn algorithm(oid: &str) -> Vec<u8> {
    der::seq(&[der::oid(oid), der::null()])
}

/// Detached CMS SignedData over the CodeDirectory.
pub fn cms(code_directory: &[u8], id: &Identity) -> Result<Vec<u8>, String> {
    let digest = Sha256::digest(code_directory);
    let info = der::cert_info(&id.leaf)?;
    let issuer_and_serial = der::seq(&[info.issuer.to_vec(), info.serial.to_vec()]);
    let mut cdhashes = Dictionary::new();
    cdhashes.insert("cdhashes".into(), Value::Array(vec![Value::Data(digest[..20].to_vec())]));
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs();
    let attributes = vec![
        attribute("1.2.840.113549.1.9.3", &[der::oid("1.2.840.113549.1.7.1")]),
        attribute("1.2.840.113549.1.9.5", &[der::utc_time(now)]),
        attribute("1.2.840.113549.1.9.4", &[der::octets(&digest)]),
        attribute("1.2.840.113635.100.9.2", &[der::seq(&[der::oid("2.16.840.1.101.3.4.2.1"), der::octets(&digest)])]),
        attribute("1.2.840.113635.100.9.1", &[der::octets(&plist_xml(&Value::Dictionary(cdhashes)))]),
    ];
    let mut sorted_attrs = attributes;
    sorted_attrs.sort();
    let signed = der::set(&sorted_attrs);
    let signature = id.key.sign(Pkcs1v15Sign::new::<Sha256>(), &Sha256::digest(&signed))
        .map_err(|e| format!("RSA signing failed: {e}"))?;
    let signer = der::seq(&[
        der::int(1),
        issuer_and_serial,
        algorithm("2.16.840.1.101.3.4.2.1"),
        der::ctx(0, &sorted_attrs.concat()),
        algorithm("1.2.840.113549.1.1.1"),
        der::octets(&signature),
    ]);
    let mut certs: Vec<Vec<u8>> = std::iter::once(id.leaf.clone()).chain(id.chain.iter().cloned()).collect();
    certs.sort();
    let signed_data = der::seq(&[
        der::int(1),
        der::set(&[algorithm("2.16.840.1.101.3.4.2.1")]),
        der::seq(&[der::oid("1.2.840.113549.1.7.1")]),
        der::ctx(0, &certs.concat()),
        der::set(&[signer]),
    ]);
    Ok(der::seq(&[der::oid("1.2.840.113549.1.7.2"), der::ctx(0, &signed_data)]))
}

fn sign_thin(data: &[u8], t: &Target, id: &Identity) -> Result<Vec<u8>, String> {
    let mach = Thin::parse(data)?;
    let code_end = mach.code_end()?;
    let dataoff = (code_end + 15) & !15;
    let sha = |b: &[u8]| -> [u8; 32] { Sha256::digest(b).into() };
    let req = requirements(t.identifier, &id.common_name);
    let mut specials = vec![(SLOT_REQUIREMENTS, sha(&req))];
    if let Some(info) = t.info_plist {
        specials.push((SLOT_INFO, sha(info)));
    }
    if let Some(res) = t.code_resources {
        specials.push((SLOT_RESOURCES, sha(res)));
    }
    let mut ent_blobs = None;
    if let Some(ent) = t.entitlements {
        let xml = blob(CSMAGIC_ENTITLEMENTS, &plist_xml(&Value::Dictionary(sorted(ent))));
        let der_ent = blob(CSMAGIC_DER_ENTITLEMENTS, &der_entitlements(ent)?);
        specials.push((SLOT_ENTITLEMENTS, sha(&xml)));
        specials.push((SLOT_DER_ENTITLEMENTS, sha(&der_ent)));
        ent_blobs = Some((xml, der_ent));
    }
    let mut flags = 0u64;
    if mach.filetype == MH_EXECUTE {
        flags = 1;
        if let Some(ent) = t.entitlements {
            let on = |k: &str| ent.get(k).and_then(Value::as_boolean) == Some(true);
            if on("com.apple.private.amfi.can-load-cdhash") { flags |= 0x100; }
            if on("com.apple.private.amfi.can-execute-cdhash") { flags |= 0x200; }
        }
    }
    let exec_base = mach.text.fileoff;
    let exec_limit = mach.text.fileoff + mach.text.filesize;
    let mut predicted = 0usize;
    for _ in 0..4 {
        let patched = mach.patched(dataoff, predicted)?;
        let mut body = patched[..code_end].to_vec();
        body.resize(dataoff, 0);
        let pages: Vec<[u8; 32]> = body.chunks(PAGE).map(sha).collect();
        let cd = code_directory(t.identifier, &id.team, dataoff, &specials, &pages, exec_base, exec_limit, flags);
        let mut blobs = vec![(SLOT_CODEDIRECTORY, cd.clone()), (SLOT_REQUIREMENTS, req.clone())];
        if let Some((xml, der_ent)) = &ent_blobs {
            blobs.push((SLOT_ENTITLEMENTS, xml.clone()));
            blobs.push((SLOT_DER_ENTITLEMENTS, der_ent.clone()));
        }
        blobs.push((SLOT_SIGNATURE, blob(CSMAGIC_BLOBWRAPPER, &cms(&cd, id)?)));
        let mut sb = superblob(blobs);
        sb.resize((sb.len() + 15) & !15, 0);
        if sb.len() == predicted {
            let mut out = mach.patched(dataoff, sb.len())?[..code_end].to_vec();
            out.resize(dataoff, 0);
            out.extend(sb);
            return Ok(out);
        }
        predicted = sb.len();
    }
    Err(format!("the signature size did not settle for {}", t.identifier))
}

/// Signs a thin or fat (32-bit header) Mach-O.
pub fn sign_macho(data: &[u8], t: &Target, id: &Identity) -> Result<Vec<u8>, String> {
    let magic = u32::from_be_bytes(data.get(..4).ok_or("empty binary")?.try_into().unwrap());
    if magic == 0xCAFEBABF {
        return Err("64-bit fat binaries are not supported".into());
    }
    if magic != 0xCAFEBABE {
        return sign_thin(data, t, id);
    }
    let be = |o: usize| u32::from_be_bytes(data[o..o + 4].try_into().unwrap());
    let count = be(4) as usize;
    if count > 16 || data.len() < 8 + count * 20 {
        return Err("bad fat header".into());
    }
    let mut slices = Vec::new();
    for i in 0..count {
        let b = 8 + i * 20;
        let (cpu, sub, off, size, align) = (be(b), be(b + 4), be(b + 8) as usize, be(b + 12) as usize, be(b + 16).max(4));
        let slice = data.get(off..off + size).ok_or("fat slice runs past the file")?;
        slices.push((cpu, sub, align, sign_thin(slice, t, id)?));
    }
    let mut out = (0xCAFEBABEu32).to_be_bytes().to_vec();
    out.extend_from_slice(&(count as u32).to_be_bytes());
    let mut pos = 8 + count * 20;
    let mut placed = Vec::new();
    for (_, _, align, d) in &slices {
        let boundary = 1usize << align;
        pos = (pos + boundary - 1) & !(boundary - 1);
        placed.push(pos);
        pos += d.len();
    }
    for ((cpu, sub, align, d), at) in slices.iter().zip(&placed) {
        for v in [*cpu, *sub, *at as u32, d.len() as u32, *align] {
            out.extend_from_slice(&v.to_be_bytes());
        }
    }
    for ((_, _, _, d), at) in slices.iter().zip(&placed) {
        out.resize(*at, 0);
        out.extend_from_slice(d);
    }
    Ok(out)
}
