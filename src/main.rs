mod app;
mod apple;
mod der;
mod device;
mod doctor;
mod icons;
mod install;
mod log;
mod minis;
mod pairing;
mod sign;
mod siphash;
mod srp;
mod store;
mod web;
mod state;
mod update;

use std::process::ExitCode;

const USAGE: &str = "usage: baristore            open the store
       baristore update     install the newest BariStore
       baristore --version";

/// Set for the store's server process: "<port> <token>" to serve on.
const SERVE: &str = "BARISTORE_SERVE";
/// Set when the supervisor restarts itself after an update: the page stays
/// at the same address, so it is not opened again.
const KEEP: &str = "BARISTORE_KEEP";
/// The server's exit code after it installed a new build.
pub const UPDATED: i32 = 75;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let _ = std::fs::create_dir_all(state::home());
    if args.is_empty() && std::env::var_os(SERVE).is_none() {
        return supervise();
    }
    // Two workers: Minis's shell runs on an emulated CPU with little memory.
    let runtime = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build()
        .expect("tokio runtime");
    match runtime.block_on(run(args)) {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("baristore: {message}");
            ExitCode::FAILURE
        }
    }
}

async fn run(args: Vec<String>) -> Result<(), String> {
    match args.first().map(String::as_str) {
        None => serve().await,
        Some("update") => match update::download_and_replace(&|step| println!("{step}")).await? {
            update::Outcome::UpToDate => {
                println!("BariStore {} is the newest build.", env!("CARGO_PKG_VERSION"));
                Ok(())
            }
            update::Outcome::Updated(version) => {
                println!("Updated to {version}. Opening the store…");
                Err(update::restart())
            }
        },
        Some("--version") | Some("-V") => {
            println!("baristore {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        #[cfg(debug_assertions)]
        Some("dev") => dev(&args[1..]).await,
        _ => {
            println!("{USAGE}");
            Ok(())
        }
    }
}

/// `baristore`: a small supervisor that runs the store's server as a child
/// process and starts it again when it dies. iOS can take memory from Minis
/// in the background, and the server then aborts ("memory allocation …
/// failed"); the page stays at the same address and reconnects. The
/// supervisor itself allocates almost nothing after start.
fn supervise() -> ExitCode {
    let (port, token, reopen) = match std::env::var(KEEP).ok().and_then(|v| {
        let (p, t) = v.split_once(' ')?;
        Some((p.parse::<u16>().ok()?, t.to_string()))
    }) {
        Some((port, token)) => (port, token, false),
        None => {
            let port = std::net::TcpListener::bind(("127.0.0.1", 0)).and_then(|l| l.local_addr())
                .map(|a| a.port()).unwrap_or(47801);
            (port, hex::encode(rand::random::<[u8; 16]>()), true)
        }
    };
    let url = format!("http://127.0.0.1:{port}/{token}/");
    let Ok(exe) = std::env::current_exe() else {
        eprintln!("baristore: cannot find its own file");
        return ExitCode::FAILURE;
    };
    if reopen {
        println!("BariStore {}", env!("CARGO_PKG_VERSION"));
        if !app::minis_ok() {
            println!("Note: this is not Open Minis 1.16+ (no minis-bonjour): pairing will not work here.");
        }
    }
    let (quit_tx, quit_rx) = std::sync::mpsc::channel::<()>();
    std::thread::spawn(move || {
        // Return quits; with no terminal input (stdin at its end) nothing does.
        let mut line = String::new();
        if std::io::stdin().read_line(&mut line).unwrap_or(0) > 0 {
            let _ = quit_tx.send(());
        }
    });
    let mut crashes: Vec<std::time::Instant> = Vec::new();
    let mut opened = !reopen;
    loop {
        let child = std::process::Command::new(&exe).env(SERVE, format!("{port} {token}"))
            .env_remove(KEEP).stdin(std::process::Stdio::null()).spawn();
        let mut child = match child {
            Ok(c) => c,
            Err(e) => {
                eprintln!("baristore: cannot start the store: {e}");
                return ExitCode::FAILURE;
            }
        };
        if !opened {
            opened = true;
            // The page polls until the server answers, so it can open now.
            if !minis::open(&url) {
                println!("Open this address in Minis's browser: {url}");
            }
            println!("The store is open. Keep Minis open while installing.");
            println!("To quit: press Return here (or Ctrl-C), or Options › Quit in the store.");
        }
        let status = loop {
            if let Ok(Some(status)) = child.try_wait() {
                break status;
            }
            if quit_rx.recv_timeout(std::time::Duration::from_millis(250)).is_ok() {
                let _ = child.kill();
                let _ = child.wait();
                println!("BariStore closed. Type baristore to open it again.");
                return ExitCode::SUCCESS;
            }
        };
        match status.code() {
            Some(0) => {
                println!("BariStore closed. Type baristore to open it again.");
                return ExitCode::SUCCESS;
            }
            Some(UPDATED) => {
                use std::os::unix::process::CommandExt;
                println!("BariStore updated; starting the new build.");
                let err = std::process::Command::new(&exe).env(KEEP, format!("{port} {token}")).exec();
                eprintln!("baristore: cannot start the new build: {err}");
                return ExitCode::FAILURE;
            }
            _ => {
                let now = std::time::Instant::now();
                crashes.retain(|t| now.duration_since(*t).as_secs() < 60);
                crashes.push(now);
                println!("BariStore stopped ({status}); starting it again.");
                // In the background iOS may refuse memory for a while: wait
                // longer when it keeps failing.
                let pause = if crashes.len() > 3 { 10 } else { 1 };
                std::thread::sleep(std::time::Duration::from_secs(pause));
            }
        }
    }
}

/// The store's server (the supervisor's child): serves the page at the
/// address it was given until Options › Quit or an update ends it.
async fn serve() -> Result<(), String> {
    let spec = std::env::var(SERVE).map_err(|_| "not started by baristore".to_string())?;
    let (port, token) = spec.split_once(' ').ok_or("bad BARISTORE_SERVE")?;
    let port: u16 = port.parse().map_err(|_| "bad port")?;
    logf!("baristore {} starting (pid {})", env!("CARGO_PKG_VERSION"), std::process::id());
    let store = app::App::new();
    web::serve(store, port, token).await?;
    std::future::pending::<()>().await;
    Ok(())
}

/// Developer commands for interop tests (debug builds only).
#[cfg(debug_assertions)]
async fn dev(args: &[String]) -> Result<(), String> {
    match args.first().map(String::as_str) {
        Some("pairhost") => {
            let port: u16 = args.get(1).and_then(|p| p.parse().ok()).ok_or("dev pairhost <port> <code>")?;
            let code = args.get(2).ok_or("dev pairhost <port> <code>")?;
            let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await.map_err(|e| e.to_string())?;
            let (stream, _) = listener.accept().await.map_err(|e| e.to_string())?;
            let identity = pairing::record::HostIdentity::generate();
            let log = |line: String| println!("host: {line}");
            let peer = pairing::host::PairableHost::new(stream, &identity, "BariStore", &log)
                .accept(|| async { code.clone() }).await.map_err(|e| e.to_string())?;
            println!("paired: udid={} model={} name={}", peer.udid, peer.model, peer.name);
            Ok(())
        }
        Some("tunnel") => {
            // Through CDTunnel against BARISTORE_DEVICE, then one raw packet.
            let record = pairing::record::PairingRecord {
                host: pairing::record::HostIdentity::generate(),
                peer: Default::default(),
            };
            let tunnel = device::session::Tunnel::open(&record).await.map_err(|e| e.to_string())?;
            println!("tunnel: rsd {}", tunnel.rsd_port);
            match tokio::time::timeout(std::time::Duration::from_secs(5), tunnel.stack.connect(tunnel.rsd_port)).await {
                Ok(Err(e)) => println!("tcp over the echo link: {e}"),
                Ok(Ok(_)) => println!("tcp over the echo link: connected"),
                Err(_) => println!("tcp over the echo link: no answer (expected from an echo)"),
            }
            Ok(())
        }
        Some("sign") => {
            // dev sign <in.ipa> <out.ipa> <cert.der> <key.pk8.der> <team>: every
            // app/appex gets a dummy profile and minimal entitlements.
            use plist::Value;
            let a = |i: usize| args.get(i).cloned().ok_or("dev sign <in> <out> <cert.der> <key.der> <team>");
            let bytes = std::fs::read(a(1)?).map_err(|e| e.to_string())?;
            let leaf = std::fs::read(a(3)?).map_err(|e| e.to_string())?;
            let key = {
                use rsa::pkcs8::DecodePrivateKey;
                rsa::RsaPrivateKey::from_pkcs8_der(&std::fs::read(a(4)?).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?
            };
            let team = a(5)?;
            let mut ipa = sign::bundle::Ipa::read(bytes)?;
            let mut plan = sign::bundle::Plan {
                identity: sign::codesign::Identity { leaf, chain: vec![], key, team: team.clone(), common_name: "Test".into() },
                bundles: Default::default(), info_edits: Default::default(), add_files: vec![],
            };
            for b in ipa.bundles()? {
                if b.kind == sign::bundle::Kind::Framework { continue; }
                let new_id = format!("{}.{team}", b.id);
                let mut ent = plist::Dictionary::new();
                ent.insert("application-identifier".into(), Value::String(format!("{team}.{new_id}")));
                ent.insert("com.apple.developer.team-identifier".into(), Value::String(team.clone()));
                ent.insert("get-task-allow".into(), Value::Boolean(true));
                println!("bundle {} {} -> {}", b.dir, b.id, new_id);
                plan.bundles.insert(b.dir.clone(), sign::bundle::Signing { new_id, profile: b"profile".to_vec(), entitlements: ent });
            }
            let started = std::time::Instant::now();
            sign::bundle::sign(&mut ipa, &plan, &mut |n, t| println!("signed {n}/{t}"))?;
            std::fs::write(a(2)?, ipa.write()?).map_err(|e| e.to_string())?;
            println!("ok in {:?}", started.elapsed());
            Ok(())
        }
        _ => Err("dev: unknown command".into()),
    }
}
