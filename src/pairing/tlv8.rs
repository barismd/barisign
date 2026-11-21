//! TLV8 as used by Apple pair-setup / pair-verify: one byte type, one byte
//! length, values longer than 255 bytes split into consecutive fragments of
//! the same type (concatenated again when decoding).

use std::collections::BTreeMap;

pub const IDENTIFIER: u8 = 1;
pub const SALT: u8 = 2;
pub const PUBLIC_KEY: u8 = 3;
pub const PROOF: u8 = 4;
pub const ENCRYPTED_DATA: u8 = 5;
pub const STATE: u8 = 6;
pub const ERROR: u8 = 7;
pub const SIGNATURE: u8 = 10;
pub const INFO: u8 = 17;

pub fn encode(entries: &[(u8, &[u8])]) -> Vec<u8> {
    let mut out = Vec::new();
    for (kind, value) in entries {
        if value.is_empty() {
            out.extend_from_slice(&[*kind, 0]);
            continue;
        }
        for chunk in value.chunks(255) {
            out.push(*kind);
            out.push(chunk.len() as u8);
            out.extend_from_slice(chunk);
        }
    }
    out
}

pub fn decode(data: &[u8]) -> Result<BTreeMap<u8, Vec<u8>>, String> {
    let mut map: BTreeMap<u8, Vec<u8>> = BTreeMap::new();
    let mut pos = 0;
    while pos + 2 <= data.len() {
        let kind = data[pos];
        let len = data[pos + 1] as usize;
        let end = pos + 2 + len;
        if end > data.len() {
            return Err("truncated TLV8".into());
        }
        map.entry(kind).or_default().extend_from_slice(&data[pos + 2..end]);
        pos = end;
    }
    if pos != data.len() {
        return Err("trailing byte in TLV8".into());
    }
    Ok(map)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn long_values_split_and_join() {
        let long = vec![7u8; 600];
        let bytes = encode(&[(STATE, &[1]), (PUBLIC_KEY, &long), (ERROR, &[])]);
        assert_eq!(bytes.len(), 3 + (2 + 255) * 2 + (2 + 90) + 2);
        let map = decode(&bytes).unwrap();
        assert_eq!(map[&STATE], vec![1]);
        assert_eq!(map[&PUBLIC_KEY], long);
        assert!(map[&ERROR].is_empty());
    }

    #[test]
    fn truncated_is_an_error() {
        assert!(decode(&[6, 3, 1]).is_err());
    }
}
