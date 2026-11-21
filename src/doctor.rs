//! Options › Log: one block of plain text to paste when something fails, with
//! the recent log; "Run checks" adds network, tunnel and sign-in checks.
//! Never contains the password, tokens, keys or the pairing record; the email
//! and UDID are shortened.

use std::fmt::Write;
use std::time::Duration;

use crate::{apple, device::session, install, minis, pairing::flow, state, store};

fn short(s: &str, keep: usize) -> String {
    if s.len() <= keep { s.to_string() } else { format!("{}…", &s[..keep]) }
}

async fn reach(url: &str) -> String {
    let http = store::client();
    let started = std::time::Instant::now();
    match tokio::time::timeout(Duration::from_secs(12), http.get(url).send()).await {
        Ok(Ok(r)) => format!("HTTP {} in {} ms", r.status().as_u16(), started.elapsed().as_millis()),
        Ok(Err(e)) => format!("failed: {e}"),
        Err(_) => "timed out".into(),
    }
}

pub async fn report(checks: bool) -> String {
    let mut out = String::new();
    let _ = writeln!(out, "BariStore {} {}", env!("CARGO_PKG_VERSION"), if checks { "checks" } else { "log" });
    let uname = std::process::Command::new("uname").arg("-a").output().map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_default();
    let _ = writeln!(out, "system      {uname}");
    let _ = writeln!(out, "alpine      {}", std::fs::read_to_string("/etc/alpine-release").unwrap_or_default().trim());
    let _ = writeln!(out, "home        {}", state::home().display());
    for tool in ["minis-bonjour", "apple-speak", "apple-player", "apple-notification", "minis-open"] {
        let _ = writeln!(out, "{tool:<19} {}", if minis::available(tool) { "yes" } else { "MISSING" });
    }
    if let Ok(entries) = std::fs::read_dir("/var/minis") {
        let names: Vec<String> = entries.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        let _ = writeln!(out, "/var/minis  {}", names.join(" "));
    }
    let _ = writeln!(out, "LocalDevVPN {}", if session::vpn_up().await { "answers" } else { "NO ANSWER" });
    if checks {
        let _ = writeln!(out, "pairing svc {}", match session::endpoint().await {
            Ok((host, port)) => format!("{host}:{port}"),
            Err(e) => e.to_string(),
        });
    }
    match flow::load_record() {
        Some(r) => {
            let _ = writeln!(out, "pairing     yes, {} ({}), udid {}", r.peer.name, r.peer.model, short(&r.peer.udid, 8));
            if checks && session::vpn_up().await {
                match tokio::time::timeout(Duration::from_secs(40), session::Session::open(&r)).await {
                    Ok(Ok(s)) => {
                        let dm = crate::device::services::developer_mode(&s).await;
                        let _ = writeln!(out, "tunnel      ok, {} services, Developer Mode {:?}", s.services.len(), dm);
                        let ios = s.properties.get("OSVersion").and_then(plist::Value::as_string).unwrap_or("?");
                        let _ = writeln!(out, "iOS         {ios}");
                    }
                    Ok(Err(e)) => { let _ = writeln!(out, "tunnel      FAILED: {e}"); }
                    Err(_) => { let _ = writeln!(out, "tunnel      timed out"); }
                }
            }
        }
        None => { let _ = writeln!(out, "pairing     none yet"); }
    }
    match apple::gsa::Account::load() {
        Some(a) => { let _ = writeln!(out, "Apple ID    {} (token fresh: {})", short(&a.email, 3), a.token_fresh()); }
        None => { let _ = writeln!(out, "Apple ID    not signed in"); }
    }
    let _ = writeln!(out, "team        {}", std::fs::read_to_string(state::path("team")).unwrap_or_default().lines().next().unwrap_or("-"));
    if checks {
        for server in apple::anisette::SERVERS {
            let _ = writeln!(out, "anisette    {server:<32} {}", reach(&format!("{server}/v3/client_info")).await);
        }
        // A made-up account through the first server: tells which client
        // Apple takes from this network today.
        for claim in apple::anisette::Claim::ALL {
            let verdict = apple::gsa::probe(apple::anisette::SERVERS[0], claim).await;
            let _ = writeln!(out, "sign-in     as {:<12} {verdict}", claim.name());
        }
        let _ = writeln!(out, "github      {}", reach("https://api.github.com/repos/SideStore/SideStore/releases?per_page=1").await);
    }
    let apps = install::installed();
    let _ = writeln!(out, "installed   {} app(s)", apps.len());
    for a in apps {
        let left = a.expires.saturating_sub(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs()) / 3600;
        let _ = writeln!(out, "  {} {} ({} h left)", a.name, a.version, left);
    }
    let _ = writeln!(out, "--- last log lines ---");
    let log = std::fs::read_to_string(state::path("baristore.log")).unwrap_or_default();
    let lines: Vec<&str> = log.lines().collect();
    for line in &lines[lines.len().saturating_sub(200)..] {
        let _ = writeln!(out, "{line}");
    }
    out
}
