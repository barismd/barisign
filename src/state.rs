//! Where BariStore keeps its files, and how secrets are written.

use std::io::Write;
use std::path::{Path, PathBuf};

/// `$BARISTORE_HOME`, else `~/.baristore`.
pub fn home() -> PathBuf {
    if let Some(dir) = std::env::var_os("BARISTORE_HOME") {
        return PathBuf::from(dir);
    }
    let base = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
    base.join(".baristore")
}

pub fn path(name: &str) -> PathBuf {
    home().join(name)
}

/// Writes `data` atomically with mode 0600 (pairing records, sessions, keys).
pub fn write_private(path: &Path, data: &[u8]) -> std::io::Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = path.with_extension("tmp");
    {
        let mut file = std::fs::OpenOptions::new()
            .write(true).create(true).truncate(true).mode(0o600).open(&tmp)?;
        file.write_all(data)?;
        file.sync_all()?;
    }
    std::fs::rename(&tmp, path)
}
