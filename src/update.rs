//! `baristore update` and Options › Update BariStore: the newest build from
//! the rolling `nightly` release, checked against its SHA-256 and swapped in
//! for the running binary.

use sha2::{Digest, Sha256};
use std::path::PathBuf;

const BASE: &str = "https://github.com/barismd/BariStore/releases/download/nightly";

pub enum Outcome {
    UpToDate,
    /// The version the new binary reports.
    Updated(String),
}

fn executable() -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| format!("cannot find BariStore's own file: {e}"))?;
    Ok(std::fs::canonicalize(&exe).unwrap_or(exe))
}

pub async fn download_and_replace(progress: &(dyn Fn(String) + Send + Sync)) -> Result<Outcome, String> {
    let http = reqwest::Client::builder().timeout(std::time::Duration::from_secs(600)).build()
        .map_err(|e| e.to_string())?;
    let get = |url: String| {
        let http = http.clone();
        async move {
            http.get(&url).send().await.and_then(|r| r.error_for_status())
                .map_err(|e| format!("download failed: {e}"))
        }
    };
    progress("Checking for a newer build".into());
    let sums = get(format!("{BASE}/baristore-aarch64.sha256")).await?.text().await.map_err(|e| e.to_string())?;
    let want = sums.split_whitespace().next().unwrap_or("").to_ascii_lowercase();
    if want.len() != 64 {
        return Err("the release has no valid checksum".into());
    }
    let exe = executable()?;
    if std::fs::read(&exe).map(|b| hex::encode(Sha256::digest(&b)) == want).unwrap_or(false) {
        return Ok(Outcome::UpToDate);
    }
    let mut resp = get(format!("{BASE}/baristore-aarch64")).await?;
    let total = resp.content_length().unwrap_or(0);
    let mut bytes = Vec::with_capacity(total as usize);
    let mut shown = None;
    while let Some(chunk) = resp.chunk().await.map_err(|e| format!("download failed: {e}"))? {
        bytes.extend_from_slice(&chunk);
        let mb = bytes.len() / 1_000_000;
        if total > 0 && shown != Some(mb) {
            shown = Some(mb);
            progress(format!("Downloading {mb} of {} MB", total / 1_000_000));
        }
    }
    if hex::encode(Sha256::digest(&bytes)) != want {
        return Err("the download does not match its checksum; nothing was changed".into());
    }
    let staged = exe.with_extension("new");
    std::fs::write(&staged, &bytes).map_err(|e| format!("cannot write {}: {e}", staged.display()))?;
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&staged, std::fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
    }
    let version = std::process::Command::new(&staged).arg("--version").output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().trim_start_matches("baristore ").to_string())
        .unwrap_or_default();
    if version.is_empty() {
        let _ = std::fs::remove_file(&staged);
        return Err("the new build does not start on this device; nothing was changed".into());
    }
    std::fs::rename(&staged, &exe).map_err(|e| format!("cannot replace {}: {e}", exe.display()))?;
    Ok(Outcome::Updated(version))
}
