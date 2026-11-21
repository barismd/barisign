//! Just enough DER for CSRs, certificates and CMS: an encoder and a reader
//! that walks TLVs.

pub fn len(n: usize) -> Vec<u8> {
    if n < 0x80 {
        return vec![n as u8];
    }
    let bytes: Vec<u8> = n.to_be_bytes().iter().copied().skip_while(|b| *b == 0).collect();
    let mut out = vec![0x80 | bytes.len() as u8];
    out.extend(bytes);
    out
}

pub fn tlv(tag: u8, content: &[u8]) -> Vec<u8> {
    let mut out = vec![tag];
    out.extend(len(content.len()));
    out.extend_from_slice(content);
    out
}

pub fn seq(parts: &[Vec<u8>]) -> Vec<u8> {
    tlv(0x30, &parts.concat())
}

pub fn set(parts: &[Vec<u8>]) -> Vec<u8> {
    tlv(0x31, &parts.concat())
}

pub fn int_bytes(big_endian: &[u8]) -> Vec<u8> {
    let mut v: Vec<u8> = big_endian.iter().copied().skip_while(|b| *b == 0).collect();
    if v.is_empty() || v[0] & 0x80 != 0 {
        v.insert(0, 0);
    }
    tlv(0x02, &v)
}

pub fn int(n: u64) -> Vec<u8> {
    int_bytes(&n.to_be_bytes())
}

pub fn oid(dotted: &str) -> Vec<u8> {
    let parts: Vec<u64> = dotted.split('.').map(|p| p.parse().unwrap()).collect();
    let mut body = vec![(parts[0] * 40 + parts[1]) as u8];
    for &p in &parts[2..] {
        let mut chunk = vec![(p & 0x7F) as u8];
        let mut v = p >> 7;
        while v > 0 {
            chunk.push(0x80 | (v & 0x7F) as u8);
            v >>= 7;
        }
        chunk.reverse();
        body.extend(chunk);
    }
    tlv(0x06, &body)
}

pub fn null() -> Vec<u8> {
    vec![0x05, 0x00]
}

pub fn utf8(s: &str) -> Vec<u8> {
    tlv(0x0C, s.as_bytes())
}

pub fn printable(s: &str) -> Vec<u8> {
    tlv(0x13, s.as_bytes())
}

pub fn octets(b: &[u8]) -> Vec<u8> {
    tlv(0x04, b)
}

pub fn bits(b: &[u8]) -> Vec<u8> {
    let mut v = vec![0];
    v.extend_from_slice(b);
    tlv(0x03, &v)
}

/// [n] EXPLICIT / constructed context tag.
pub fn ctx(n: u8, content: &[u8]) -> Vec<u8> {
    tlv(0xA0 | n, content)
}

pub fn utc_time(unix: u64) -> Vec<u8> {
    let days = unix / 86400;
    let secs = unix % 86400;
    let (y, m, d) = civil_from_days(days as i64);
    let text = format!("{:02}{:02}{:02}{:02}{:02}{:02}Z", y % 100, m, d, secs / 3600, (secs / 60) % 60, secs % 60);
    tlv(0x17, text.as_bytes())
}

/// Days since 1970-01-01 to (year, month, day), proleptic Gregorian.
pub fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719468;
    let era = z.div_euclid(146097);
    let doe = z.rem_euclid(146097);
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// A DER element: tag, the full encoding, and the content.
#[derive(Clone, Copy, Debug)]
pub struct Tlv<'a> {
    pub tag: u8,
    pub raw: &'a [u8],
    pub content: &'a [u8],
}

pub fn read(data: &[u8]) -> Result<(Tlv<'_>, &[u8]), String> {
    if data.len() < 2 {
        return Err("truncated DER".into());
    }
    let tag = data[0];
    let (length, header) = if data[1] & 0x80 == 0 {
        (data[1] as usize, 2)
    } else {
        let n = (data[1] & 0x7F) as usize;
        if n == 0 || n > 4 || data.len() < 2 + n {
            return Err("bad DER length".into());
        }
        (data[2..2 + n].iter().fold(0usize, |a, b| (a << 8) | *b as usize), 2 + n)
    };
    let end = header.checked_add(length).filter(|e| *e <= data.len()).ok_or("truncated DER")?;
    Ok((Tlv { tag, raw: &data[..end], content: &data[header..end] }, &data[end..]))
}

pub fn children(content: &[u8]) -> Result<Vec<Tlv<'_>>, String> {
    let mut out = Vec::new();
    let mut rest = content;
    while !rest.is_empty() {
        let (t, r) = read(rest)?;
        out.push(t);
        rest = r;
    }
    Ok(out)
}

/// The parts of an X.509 certificate signing needs.
pub struct CertInfo<'a> {
    pub serial: &'a [u8],      // full INTEGER encoding
    pub issuer: &'a [u8],      // full Name encoding
    pub subject: &'a [u8],
    pub spki: &'a [u8],        // full SubjectPublicKeyInfo
}

pub fn cert_info(der: &[u8]) -> Result<CertInfo<'_>, String> {
    let (cert, _) = read(der)?;
    let parts = children(cert.content)?;
    if parts.len() < 3 {
        return Err("not a certificate".into());
    }
    let tbs = children(parts[0].content)?;
    let offset = usize::from(tbs.first().map(|t| t.tag) == Some(0xA0));
    let get = |i: usize| tbs.get(offset + i).map(|t| t.raw).ok_or("short certificate");
    Ok(CertInfo {
        serial: get(0)?,
        issuer: get(2)?,
        subject: get(4)?,
        spki: get(5)?,
    })
}

/// The first value of `oid` inside a Name (e.g. 2.5.4.3 = CN), as text.
pub fn name_value(name: &[u8], dotted: &str) -> Option<String> {
    let want = oid(dotted);
    let (n, _) = read(name).ok()?;
    for rdn in children(n.content).ok()? {
        for atv in children(rdn.content).ok()? {
            let kv = children(atv.content).ok()?;
            if kv.len() == 2 && kv[0].raw == want.as_slice() {
                return Some(String::from_utf8_lossy(kv[1].content).into_owned());
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn oid_encoding() {
        assert_eq!(oid("1.2.840.113549.1.1.11"), vec![0x06, 0x09, 0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x0B]);
    }

    #[test]
    fn long_length() {
        assert_eq!(len(300), vec![0x82, 0x01, 0x2C]);
        let blob = tlv(0x04, &vec![1u8; 300]);
        let (t, rest) = read(&blob).unwrap();
        assert_eq!(t.content.len(), 300);
        assert!(rest.is_empty());
    }

    #[test]
    fn dates() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(20_413), (2025, 11, 21));
    }

    #[test]
    fn reads_apple_root() {
        let der = include_bytes!("../assets/certs/AppleRootCA.cer");
        let info = cert_info(der).unwrap();
        assert_eq!(name_value(info.subject, "2.5.4.3").as_deref(), Some("Apple Root CA"));
    }
}
