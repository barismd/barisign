//! One log for everything: timestamped lines in ~/.baristore/baristore.log,
//! and the last lines in memory for the page (Options › Log, `baristore log`).

use std::io::Write;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

static RECENT: Mutex<Vec<String>> = Mutex::new(Vec::new());
const KEEP: usize = 400;

pub fn stamp() -> String {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    let secs = now.as_secs();
    let (h, m, s) = ((secs / 3600) % 24, (secs / 60) % 60, secs % 60);
    format!("{h:02}:{m:02}:{s:02}.{:03}Z", now.subsec_millis())
}

pub fn line(text: impl AsRef<str>) {
    let entry = format!("{} {}", stamp(), text.as_ref());
    if std::env::var_os("BARISTORE_ECHO").is_some() {
        eprintln!("{entry}");
    }
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true).append(true).open(crate::state::path("baristore.log"))
    {
        let _ = writeln!(file, "{entry}");
    }
    let mut recent = RECENT.lock().unwrap_or_else(|e| e.into_inner());
    recent.push(entry);
    if recent.len() > KEEP {
        let extra = recent.len() - KEEP;
        recent.drain(..extra);
    }
}

pub fn recent() -> Vec<String> {
    RECENT.lock().map(|r| r.clone()).unwrap_or_default()
}

#[macro_export]
macro_rules! logf {
    ($($arg:tt)*) => { $crate::log::line(format!($($arg)*)) };
}
