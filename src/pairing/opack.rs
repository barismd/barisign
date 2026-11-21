//! OPACK, Apple's compact binary encoding, for the dictionaries exchanged in
//! pair-setup M5/M6. Values map onto `plist::Value`.
//!
//! Decoding keeps a back-reference table: every out-of-line scalar (sized
//! integer, real, string, data) is interned the first time it appears, and tags
//! 0xA0..=0xC4 point back into it. Inline integers (0x08..=0x2F) and
//! collections are never interned. (Behaviour as documented by the MIT
//! jkcoxson/idevice implementation; a device sends a back-reference when, for
//! instance, its `name` equals its `model`.)

use plist::{Dictionary, Value};

pub fn encode(value: &Value) -> Vec<u8> {
    let mut out = Vec::new();
    encode_into(value, &mut out);
    out
}

fn encode_len(out: &mut Vec<u8>, len: usize, inline: u8, ladder: [u8; 4]) {
    if len <= 0x20 {
        out.push(inline + len as u8);
    } else if len <= 0xFF {
        out.push(ladder[0]);
        out.push(len as u8);
    } else if len <= 0xFFFF {
        out.push(ladder[1]);
        out.extend_from_slice(&(len as u16).to_le_bytes());
    } else if len <= 0xFFFF_FFFF {
        out.push(ladder[2]);
        out.extend_from_slice(&(len as u32).to_le_bytes());
    } else {
        out.push(ladder[3]);
        out.extend_from_slice(&(len as u64).to_le_bytes());
    }
}

fn encode_into(value: &Value, out: &mut Vec<u8>) {
    match value {
        Value::Boolean(b) => out.push(if *b { 0x01 } else { 0x02 }),
        Value::Integer(i) => {
            let v = i.as_unsigned().unwrap_or(0);
            if v <= 0x27 {
                out.push(v as u8 + 8);
            } else if v <= 0xFF {
                out.push(0x30);
                out.push(v as u8);
            } else if v <= 0xFFFF {
                out.push(0x31);
                out.extend_from_slice(&(v as u16).to_le_bytes());
            } else if v <= 0xFFFF_FFFF {
                out.push(0x32);
                out.extend_from_slice(&(v as u32).to_le_bytes());
            } else {
                out.push(0x33);
                out.extend_from_slice(&v.to_le_bytes());
            }
        }
        Value::Real(r) => {
            out.push(0x36);
            out.extend_from_slice(&r.to_le_bytes());
        }
        Value::String(s) => {
            encode_len(out, s.len(), 0x40, [0x61, 0x62, 0x63, 0x64]);
            out.extend_from_slice(s.as_bytes());
        }
        Value::Data(d) => {
            encode_len(out, d.len(), 0x70, [0x91, 0x92, 0x93, 0x94]);
            out.extend_from_slice(d);
        }
        Value::Array(items) => {
            if items.len() < 15 {
                out.push(0xD0 + items.len() as u8);
            } else {
                out.push(0xDF);
            }
            for item in items {
                encode_into(item, out);
            }
            if items.len() >= 15 {
                out.push(0x03);
            }
        }
        Value::Dictionary(dict) => {
            if dict.len() < 15 {
                out.push(0xE0 + dict.len() as u8);
            } else {
                out.push(0xEF);
            }
            for (key, item) in dict {
                encode_into(&Value::String(key.clone()), out);
                encode_into(item, out);
            }
            if dict.len() >= 15 {
                out.push(0x03);
            }
        }
        // Dates and UIDs never occur in pairing payloads.
        _ => out.push(0x04),
    }
}

pub fn decode(data: &[u8]) -> Result<Value, String> {
    let mut reader = Reader { data, pos: 0, objects: Vec::new() };
    let value = reader.value()?;
    if reader.pos != data.len() {
        return Err("trailing bytes after OPACK value".into());
    }
    Ok(value)
}

struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
    objects: Vec<Value>,
}

impl Reader<'_> {
    fn take(&mut self, n: usize) -> Result<&[u8], String> {
        let end = self.pos.checked_add(n).ok_or("OPACK length overflow")?;
        if end > self.data.len() {
            return Err("truncated OPACK".into());
        }
        let slice = &self.data[self.pos..end];
        self.pos = end;
        Ok(slice)
    }

    fn uint(&mut self, size: usize) -> Result<u64, String> {
        let bytes = self.take(size)?;
        let mut buf = [0u8; 8];
        buf[..size].copy_from_slice(bytes);
        Ok(u64::from_le_bytes(buf))
    }

    fn remember(&mut self, value: Value) -> Value {
        if !self.objects.contains(&value) {
            self.objects.push(value.clone());
        }
        value
    }

    fn sized_len(&mut self, tag: u8, inline: u8) -> Result<usize, String> {
        let u8_tag = inline + 0x21;
        if tag < u8_tag {
            return Ok((tag - inline) as usize);
        }
        let size = match tag - u8_tag {
            0 => 1,
            1 => 2,
            2 => 4,
            3 => 8,
            _ => return Err(format!("bad OPACK length tag 0x{tag:02x}")),
        };
        usize::try_from(self.uint(size)?).map_err(|_| "OPACK length too large".into())
    }

    fn value(&mut self) -> Result<Value, String> {
        let tag = self.take(1)?[0];
        match tag {
            0x01 => Ok(Value::Boolean(true)),
            0x02 => Ok(Value::Boolean(false)),
            0x08..=0x2F => Ok(Value::Integer(((tag - 8) as u64).into())),
            0x30..=0x33 => {
                let v = self.uint(1 << (tag - 0x30))?;
                Ok(self.remember(Value::Integer(v.into())))
            }
            0x35 => {
                let bits = self.uint(4)? as u32;
                Ok(self.remember(Value::Real(f32::from_bits(bits) as f64)))
            }
            0x36 => {
                let bits = self.uint(8)?;
                Ok(self.remember(Value::Real(f64::from_bits(bits))))
            }
            0x40..=0x64 => {
                let len = self.sized_len(tag, 0x40)?;
                let text = String::from_utf8(self.take(len)?.to_vec())
                    .map_err(|_| "OPACK string is not UTF-8")?;
                Ok(self.remember(Value::String(text)))
            }
            0x70..=0x94 => {
                let len = self.sized_len(tag, 0x70)?;
                let data = self.take(len)?.to_vec();
                Ok(self.remember(Value::Data(data)))
            }
            0xA0..=0xC0 => self.lookup((tag - 0xA0) as usize),
            0xC1..=0xC4 => {
                let index = self.uint(1 << (tag - 0xC1))? as usize;
                self.lookup(index)
            }
            0xD0..=0xDF => {
                let mut items = Vec::new();
                if tag == 0xDF {
                    while self.peek()? != 0x03 {
                        items.push(self.value()?);
                    }
                    self.pos += 1;
                } else {
                    for _ in 0..(tag - 0xD0) {
                        items.push(self.value()?);
                    }
                }
                Ok(Value::Array(items))
            }
            0xE0..=0xEF => {
                let mut dict = Dictionary::new();
                let mut entry = |reader: &mut Self| -> Result<(), String> {
                    let key = match reader.value()? {
                        Value::String(s) => s,
                        other => format!("{other:?}"),
                    };
                    let item = reader.value()?;
                    dict.insert(key, item);
                    Ok(())
                };
                if tag == 0xEF {
                    while self.peek()? != 0x03 {
                        entry(self)?;
                    }
                    self.pos += 1;
                } else {
                    for _ in 0..(tag - 0xE0) {
                        entry(self)?;
                    }
                }
                Ok(Value::Dictionary(dict))
            }
            _ => Err(format!("unsupported OPACK tag 0x{tag:02x}")),
        }
    }

    fn peek(&self) -> Result<u8, String> {
        self.data.get(self.pos).copied().ok_or_else(|| "unterminated OPACK collection".into())
    }

    fn lookup(&self, index: usize) -> Result<Value, String> {
        self.objects.get(index).cloned().ok_or_else(|| {
            format!("OPACK back-reference {index} out of range ({} seen)", self.objects.len())
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dict(pairs: &[(&str, Value)]) -> Value {
        let mut d = Dictionary::new();
        for (k, v) in pairs {
            d.insert((*k).into(), v.clone());
        }
        Value::Dictionary(d)
    }

    #[test]
    fn roundtrip() {
        let value = dict(&[
            ("altIRK", Value::Data(vec![1; 16])),
            ("name", Value::String("BariStore".into())),
            ("count", Value::Integer(300u64.into())),
            ("small", Value::Integer(3u64.into())),
            ("flag", Value::Boolean(true)),
            ("long", Value::String("x".repeat(40))),
        ]);
        assert_eq!(decode(&encode(&value)).unwrap(), value);
    }

    #[test]
    fn back_reference_resolves() {
        // {"name": "iPhone", "model": <ref 1>}: keys and values are interned in
        // order: "name"(0) "iPhone"(1) "model"(2).
        let mut bytes = vec![0xE2];
        bytes.push(0x44);
        bytes.extend_from_slice(b"name");
        bytes.push(0x46);
        bytes.extend_from_slice(b"iPhone");
        bytes.push(0x45);
        bytes.extend_from_slice(b"model");
        bytes.push(0xA1);
        let value = decode(&bytes).unwrap();
        let d = value.as_dictionary().unwrap();
        assert_eq!(d.get("model").unwrap().as_string(), Some("iPhone"));
    }
}
