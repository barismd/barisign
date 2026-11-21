//! The page: a tiny HTTP/1.1 server on 127.0.0.1 under a 128-bit random path
//! (other apps and websites on the iPhone can reach a loopback port, but not
//! guess the path), Host checked against the loopback (no DNS rebinding).
//! Shown in Minis's browser sheet with minis-open.

use serde_json::{json, Value as Json};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

use crate::app::App;
use crate::install::Options;
use crate::logf;

const PAGE: &str = include_str!("../ui/index.html");
const LOGO: &[u8] = include_bytes!("../assets/baristore.svg");

pub async fn serve(app: App) -> Result<String, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).await.map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    let token = hex::encode(rand::random::<[u8; 16]>());
    let url = format!("http://127.0.0.1:{port}/{token}/");
    let prefix = format!("/{token}");
    tokio::spawn(async move {
        loop {
            let Ok((stream, _)) = listener.accept().await else { continue };
            let app = app.clone();
            let prefix = prefix.clone();
            tokio::spawn(async move {
                let _ = handle(stream, app, &prefix, port).await;
            });
        }
    });
    Ok(url)
}

async fn respond(stream: &mut TcpStream, status: &str, kind: &str, body: &[u8]) -> std::io::Result<()> {
    respond_cached(stream, status, kind, body, "no-store").await
}

async fn respond_cached(stream: &mut TcpStream, status: &str, kind: &str, body: &[u8], cache: &str)
    -> std::io::Result<()>
{
    let head = format!(
        "HTTP/1.1 {status}\r\nContent-Type: {kind}\r\nContent-Length: {}\r\nCache-Control: {cache}\r\n\
         X-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n", body.len());
    stream.write_all(head.as_bytes()).await?;
    stream.write_all(body).await?;
    stream.flush().await
}

async fn handle(mut stream: TcpStream, app: App, prefix: &str, port: u16) -> std::io::Result<()> {
    let mut buf = Vec::new();
    let mut chunk = [0u8; 8192];
    let header_end = loop {
        let n = stream.read(&mut chunk).await?;
        if n == 0 {
            return Ok(());
        }
        buf.extend_from_slice(&chunk[..n]);
        if let Some(p) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            break p + 4;
        }
        if buf.len() > 64 * 1024 {
            return respond(&mut stream, "431 Too Large", "text/plain", b"").await;
        }
    };
    let head = String::from_utf8_lossy(&buf[..header_end]).to_string();
    let mut lines = head.split("\r\n");
    let request = lines.next().unwrap_or("");
    let mut parts = request.split(' ');
    let (method, path) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""));
    let mut host = "";
    let mut length = 0usize;
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            match k.trim().to_ascii_lowercase().as_str() {
                "host" => host = v.trim(),
                "content-length" => length = v.trim().parse().unwrap_or(0),
                _ => {}
            }
        }
    }
    if host != format!("127.0.0.1:{port}") && host != format!("localhost:{port}") {
        return respond(&mut stream, "403 Forbidden", "text/plain", b"").await;
    }
    let Some(route) = path.strip_prefix(prefix) else {
        return respond(&mut stream, "404 Not Found", "text/plain", b"").await;
    };
    if length > 1 << 20 {
        return respond(&mut stream, "413 Too Large", "text/plain", b"").await;
    }
    let mut body = buf[header_end..].to_vec();
    while body.len() < length {
        let n = stream.read(&mut chunk).await?;
        if n == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..n]);
    }
    let (route, query) = route.split_once('?').unwrap_or((route, ""));
    match (method, route) {
        ("GET", "/icon") => {
            let url = query.split('&').find_map(|p| p.strip_prefix("u=")).map(percent_decode).unwrap_or_default();
            match crate::icons::get(&crate::store::client(), &url).await {
                Ok(bytes) => respond_cached(&mut stream, "200 OK", crate::icons::kind(&bytes), &bytes,
                                            "private, max-age=604800").await,
                Err(_) => respond(&mut stream, "404 Not Found", "text/plain", b"").await,
            }
        }
        ("GET", "/") | ("GET", "") => respond(&mut stream, "200 OK", "text/html; charset=utf-8", PAGE.as_bytes()).await,
        ("GET", "/logo.svg") => respond(&mut stream, "200 OK", "image/svg+xml", LOGO).await,
        ("GET", "/api/state") => {
            let s = app.snapshot().to_string();
            respond(&mut stream, "200 OK", "application/json", s.as_bytes()).await
        }
        ("GET", "/api/catalog") => {
            let s = app.catalog().to_string();
            respond(&mut stream, "200 OK", "application/json", s.as_bytes()).await
        }
        ("POST", "/api/action") => {
            let request: Json = serde_json::from_slice(&body).unwrap_or(Json::Null);
            let reply = action(&app, &request).await;
            respond(&mut stream, "200 OK", "application/json", reply.to_string().as_bytes()).await
        }
        _ => respond(&mut stream, "404 Not Found", "text/plain", b"").await,
    }
}

/// Decodes %XX escapes (as encodeURIComponent writes them).
fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let hex = |b: u8| (b as char).to_digit(16).map(|d| d as u8);
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match (bytes[i], bytes.get(i + 1).copied().and_then(hex), bytes.get(i + 2).copied().and_then(hex)) {
            (b'%', Some(hi), Some(lo)) => {
                out.push(hi << 4 | lo);
                i += 3;
            }
            (b, _, _) => {
                out.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

#[cfg(test)]
mod tests {
    #[test]
    fn percent_decoding() {
        assert_eq!(super::percent_decode("https%3A%2F%2Fa.b%2Fx%20y.png"), "https://a.b/x y.png");
        assert_eq!(super::percent_decode("100%"), "100%");
        assert_eq!(super::percent_decode("%zz%4"), "%zz%4");
    }
}

fn s(v: &Json, k: &str) -> String {
    v.get(k).and_then(Json::as_str).unwrap_or("").to_string()
}

async fn action(app: &App, r: &Json) -> Json {
    let name = s(r, "action");
    let ok = |msg: &str| json!({"ok": true, "message": msg});
    let fail = |msg: String| json!({"ok": false, "message": msg});
    if name != "answer" {
        logf!("page: {name}");
    }
    match name.as_str() {
        "check" => {
            let up = app.check_vpn().await;
            if up && app.device().await.is_ok() {
                return ok("connected");
            }
            ok(if up { "vpn" } else { "novpn" })
        }
        "pair" => {
            // A failed run with silent speech is retried with Minis's player.
            let player = r.get("player").and_then(Json::as_bool).unwrap_or(false);
            app.spawn("Pairing", Some(json!({"action": "pair", "player": true})), move |a| async move { a.pair(player).await });
            ok("started")
        }
        "devmode_status" => { app.spawn("Developer Mode", Some(r.clone()), |a| async move { a.developer_mode(None).await }); ok("started") }
        "devmode_show" => { app.spawn("Developer Mode", Some(r.clone()), |a| async move { a.developer_mode(Some(0)).await }); ok("started") }
        "devmode_on" => { app.spawn("Developer Mode", None, |a| async move { a.developer_mode(Some(1)).await }); ok("started") }
        "login" => {
            let (email, password) = (s(r, "email"), s(r, "password"));
            if email.is_empty() || password.is_empty() {
                return fail("Enter your Apple ID email and password".into());
            }
            app.spawn("Signing in", None, move |a| async move { a.login(email, password).await });
            ok("started")
        }
        "sign_out" => { app.sign_out().await; ok("signed out") }
        "answer" => {
            let id = r.get("id").and_then(Json::as_u64).unwrap_or(0);
            let value = r.get("value").and_then(Json::as_str).map(str::to_string);
            app.answer(id, value);
            ok("")
        }
        "dismiss" => { app.dismiss_task(); ok("") }
        "catalog_refresh" => { app.refresh_catalog(); ok("") }
        "source_add" => match app.add_source(&s(r, "url")) { Ok(()) => ok("added"), Err(e) => fail(e) },
        "source_remove" => { app.remove_source(&s(r, "url")); ok("removed") }
        "install" => {
            let key = s(r, "key");
            let version = r.get("version").and_then(Json::as_u64).unwrap_or(0) as usize;
            let opts = Options {
                remove_extensions: r.get("remove_extensions").and_then(Json::as_bool).unwrap_or(false),
                increased_memory: r.get("increased_memory").and_then(Json::as_bool).unwrap_or(false),
            };
            let title = s(r, "name");
            app.spawn(&format!("Installing {title}"), Some(r.clone()),
                      move |a| async move { a.install_version(&key, version, opts).await });
            ok("started")
        }
        "refresh" => {
            let id = s(r, "bundle_id");
            app.spawn(&format!("Refreshing {}", s(r, "name")), Some(r.clone()), move |a| async move { a.refresh_app(&id).await });
            ok("started")
        }
        "refresh_all" => { app.spawn("Refreshing all apps", Some(r.clone()), |a| async move { a.refresh_all().await }); ok("started") }
        "delete" => {
            let id = s(r, "bundle_id");
            app.spawn(&format!("Deleting {}", s(r, "name")), Some(r.clone()), move |a| async move { a.delete_app(&id).await });
            ok("started")
        }
        "app_ids" => match app.load_app_ids().await { Ok(()) => ok(""), Err(e) => fail(e) },
        "update" => { app.spawn("Updating BariStore", Some(r.clone()), |a| async move { a.update().await }); ok("started") }
        "device_apps" => match app.load_device_apps().await { Ok(()) => ok(""), Err(e) => fail(e) },
        "place_pairing" => {
            let id = s(r, "bundle_id");
            app.spawn("Pairing file", Some(r.clone()), move |a| async move { a.place_pairing(&id).await });
            ok("started")
        }
        "export_pairing" => match app.export_pairing() { Ok(m) => ok(&m), Err(e) => fail(e) },
        "settings" => { app.save_settings(r.get("settings").unwrap_or(&Json::Null)); ok("saved") }
        "doctor" => ok(&crate::doctor::report().await),
        "reset" => { app.reset().await; ok("reset") }
        "quit" => {
            tokio::spawn(async {
                tokio::time::sleep(std::time::Duration::from_millis(300)).await;
                println!("BariStore closed. Type baristore to open it again.");
                std::process::exit(0)
            });
            ok("bye")
        }
        _ => fail(format!("unknown action {name}")),
    }
}
