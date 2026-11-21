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
       baristore doctor     print a diagnostic report to paste
       baristore update     install the newest BariStore
       baristore --version";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let _ = std::fs::create_dir_all(state::home());
    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().expect("tokio runtime");
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
        None => open_store().await,
        Some("doctor") => {
            println!("{}", doctor::report().await);
            Ok(())
        }
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

async fn open_store() -> Result<(), String> {
    logf!("baristore {} starting", env!("CARGO_PKG_VERSION"));
    if !app::minis_ok() {
        println!("Note: this is not Open Minis 1.16+ (no minis-bonjour): pairing will not work here.");
    }
    let store = app::App::new();
    let url = web::serve(store.clone()).await?;
    println!("BariStore {}", env!("CARGO_PKG_VERSION"));
    if !minis::open(&url) {
        println!("Open this address in Minis's browser: {url}");
    }
    println!("The store is open. Keep Minis open while installing.");
    println!("To quit: press Return here (or Ctrl-C), or Options › Quit in the store.");
    tokio::select! {
        _ = tokio::signal::ctrl_c() => {}
        _ = return_pressed() => {}
    }
    println!("BariStore closed. Type baristore to open it again.");
    Ok(())
}

/// Resolves when a line is typed in the terminal. When there is no terminal
/// input (stdin at its end, as after `| sh`), it never resolves.
async fn return_pressed() {
    let line = tokio::task::spawn_blocking(|| {
        let mut line = String::new();
        std::io::stdin().read_line(&mut line).unwrap_or(0)
    }).await.unwrap_or(0);
    if line == 0 {
        std::future::pending::<()>().await;
    }
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
            let mut ipa = sign::bundle::Ipa::read(&bytes)?;
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
