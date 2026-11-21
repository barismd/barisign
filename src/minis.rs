//! Open Minis's host commands. Each prints one JSON envelope with --compact:
//! {"ok":true,"data":{...}} or {"ok":false,"error":{"code","message"}}.
//! (Interfaces as documented in OpenMinis/OpenMinis src/shared/cli/bonjour.md
//! and as siboot drives them.)
//!
//! * minis-bonjour advertise <type> <name> <port> [k=v...]: Minis publishes the
//!   service with the iOS Bonjour API and forwards each connection to <port>
//!   inside the shell. `stop <id>` withdraws it.
//! * apple-player play <file>: plays through Minis's player with the playback
//!   audio session, which keeps Minis running while the user is in Settings.
//! * apple-notification schedule --title --body --after 1: a local notification.
//! * minis-open <url>: shows a page in a sheet over the terminal.

use serde_json::Value;
use std::path::Path;
use std::process::Stdio;
use std::time::Duration;
use tokio::process::Command;

#[derive(Debug)]
pub enum MinisError {
    Missing(String),
    Refused { code: String, message: String },
    Failed(String),
}

impl std::fmt::Display for MinisError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MinisError::Missing(tool) => write!(
                f, "`{tool}` is missing: BariStore needs Open Minis 1.16 or newer (App Store)"),
            MinisError::Refused { code, message } => write!(f, "{message} ({code})"),
            MinisError::Failed(why) => write!(f, "{why}"),
        }
    }
}

pub async fn call(tool: &str, args: &[&str]) -> Result<Value, MinisError> {
    let child = Command::new(tool)
        .args(args)
        .arg("--compact")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| match e.kind() {
            std::io::ErrorKind::NotFound => MinisError::Missing(tool.into()),
            _ => MinisError::Failed(format!("cannot run {tool}: {e}")),
        })?;
    let out = tokio::time::timeout(Duration::from_secs(80), child.wait_with_output())
        .await
        .map_err(|_| MinisError::Failed(format!("{tool} did not answer in 80 s")))?
        .map_err(|e| MinisError::Failed(format!("{tool}: {e}")))?;
    let text = String::from_utf8_lossy(&out.stdout);
    let envelope: Value = text
        .lines()
        .rev()
        .find_map(|line| serde_json::from_str(line.trim()).ok())
        .ok_or_else(|| MinisError::Failed(format!(
            "{tool} printed no JSON (stderr: {})", String::from_utf8_lossy(&out.stderr).trim())))?;
    if envelope.get("ok").and_then(Value::as_bool) == Some(true) {
        Ok(envelope.get("data").cloned().unwrap_or(Value::Null))
    } else {
        let error = envelope.get("error").cloned().unwrap_or(Value::Null);
        Err(MinisError::Refused {
            code: error.get("code").and_then(Value::as_str).unwrap_or("error").into(),
            message: error.get("message").and_then(Value::as_str).unwrap_or("refused").into(),
        })
    }
}

/// Fire-and-forget for cleanup paths (drop, Ctrl-C).
pub fn call_now(tool: &str, args: &[&str]) {
    let _ = std::process::Command::new(tool)
        .args(args)
        .arg("--compact")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

pub fn available(tool: &str) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::env::var_os("PATH").is_some_and(|path| {
        std::env::split_paths(&path).any(|dir| {
            std::fs::metadata(dir.join(tool)).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        })
    })
}

/// A published Bonjour service; withdrawn on drop.
pub struct Advert {
    pub id: String,
    pub name: String,
    pub state: String,
}

impl Drop for Advert {
    fn drop(&mut self) {
        call_now("minis-bonjour", &["stop", &self.id]);
    }
}

pub async fn advertise(service: &str, name: &str, port: u16, txt: &[(String, String)])
    -> Result<Advert, MinisError>
{
    let port = port.to_string();
    let records: Vec<String> = txt.iter().map(|(k, v)| format!("{k}={v}")).collect();
    let mut args = vec!["advertise", service, name, port.as_str()];
    args.extend(records.iter().map(String::as_str));
    let data = call("minis-bonjour", &args).await?;
    let text = |k: &str| data.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let advert = Advert { id: text("id"), name: text("name"), state: text("state") };
    if advert.id.is_empty() {
        return Err(MinisError::Failed(format!("minis-bonjour answered without an id: {data}")));
    }
    if advert.state == "failed" {
        let why = text("failed_reason");
        return Err(MinisError::Refused { code: "failed".into(), message: why });
    }
    Ok(advert)
}

/// Withdraws pairable-host adverts a killed run left behind.
pub async fn withdraw_stale(service: &str) {
    if let Ok(data) = call("minis-bonjour", &["list"]).await {
        for advert in data.get("advertisements").and_then(Value::as_array).into_iter().flatten() {
            let kind = advert.get("type").and_then(Value::as_str).unwrap_or("");
            if kind.trim_end_matches('.') == service {
                if let Some(id) = advert.get("id").and_then(Value::as_str) {
                    let _ = call("minis-bonjour", &["stop", id]).await;
                }
            }
        }
    }
}

/// Silence playing through Minis's player; stopped on drop.
pub struct Player {
    pub session: String,
}

impl Drop for Player {
    fn drop(&mut self) {
        call_now("apple-player", &["stop", &self.session]);
    }
}

impl Player {
    pub async fn start(dir: &Path) -> Result<Player, MinisError> {
        let file = dir.join("silence.wav");
        if !file.is_file() {
            std::fs::write(&file, silent_wav(30 * 60))
                .map_err(|e| MinisError::Failed(format!("cannot write {}: {e}", file.display())))?;
        }
        let data = call("apple-player", &["play", &file.to_string_lossy()]).await?;
        let session = data.get("session_id").and_then(Value::as_str).unwrap_or("").to_string();
        if session.is_empty() {
            return Err(MinisError::Failed(format!("apple-player answered without a session: {data}")));
        }
        Ok(Player { session })
    }

    pub async fn status(&self) -> String {
        match call("apple-player", &["status", &self.session]).await {
            Ok(data) => data.get("status").and_then(Value::as_str).unwrap_or("unknown").into(),
            Err(e) => format!("error: {e}"),
        }
    }

    pub async fn resume(&self) {
        let _ = call("apple-player", &["resume", &self.session]).await;
    }
}

pub async fn notify(title: &str, body: &str) -> Result<(), MinisError> {
    call("apple-notification", &["schedule", "--title", title, "--body", body, "--after", "1"])
        .await
        .map(|_| ())
}

/// Shows `url` in Minis's browser sheet. minis-open prints the escape the
/// terminal turns into the sheet, so its stdout must reach the terminal.
pub fn open(url: &str) -> bool {
    std::process::Command::new("minis-open")
        .arg(url)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success())
}

/// 8 kHz, 8-bit mono PCM silence (0x80), about 470 KB a minute.
pub fn silent_wav(seconds: u32) -> Vec<u8> {
    let rate = 8000u32;
    let len = rate * seconds;
    let mut wav = Vec::with_capacity(44 + len as usize);
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36 + len).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16u32.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes());
    wav.extend_from_slice(&rate.to_le_bytes());
    wav.extend_from_slice(&rate.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes());
    wav.extend_from_slice(&8u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&len.to_le_bytes());
    wav.resize(44 + len as usize, 0x80);
    wav
}
