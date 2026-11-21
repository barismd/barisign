//! One pairing run inside Open Minis: advertise through minis-bonjour, keep
//! Minis alive (silent speech, or the player) while the user is in Settings,
//! and make the code
//! only when the iPhone asks for it (after "Pair with BariStore" and the
//! passcode): then it goes out as a notification, which the user sees over
//! Settings. Up to three tries, each with a new code.

use std::time::{Duration, Instant};
use tokio::net::TcpListener;
use tokio::sync::mpsc;

use super::host::{self, PairError, PairableHost};
use super::record::{HostIdentity, PairingRecord};
use crate::{logf, minis, state};

pub const DISPLAY_NAME: &str = "BariStore";
const TRIES: u32 = 3;
const WAIT_FOR_DEVICE: Duration = Duration::from_secs(10 * 60);
const WAIT_FOR_CODE: Duration = Duration::from_secs(5 * 60);

#[derive(Clone, Debug)]
pub enum Event {
    /// Advertising; the user can go to Settings now. `keepalive` names what
    /// keeps Minis running (None: nothing could).
    Waiting { keepalive: Option<&'static str> },
    /// Settings asks for the code now.
    Code { code: String, attempt: u32, notification: bool },
    WrongCode { attempts_left: u32 },
    Paired { name: String },
}

pub fn record_path() -> std::path::PathBuf {
    state::path("pairing.plist")
}

pub fn load_record() -> Option<PairingRecord> {
    PairingRecord::from_bytes(&std::fs::read(record_path()).ok()?)
}

pub fn new_code() -> String {
    format!("{:06}", rand::random::<u32>() % 1_000_000)
}

/// `player`: keep Minis running with its player instead of silent speech.
pub async fn run(events: mpsc::UnboundedSender<Event>, player: bool) -> Result<PairingRecord, String> {
    let identity = HostIdentity::load_or_create(&state::path("host.plist"))
        .map_err(|e| format!("cannot save the host identity: {e}"))?;
    minis::withdraw_stale(host::SERVICE_TYPE).await;

    let listener = TcpListener::bind(("127.0.0.1", 0)).await.map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();

    let keepalive = match minis::KeepAlive::start(player, &state::home()).await {
        Ok(k) => Some(k),
        Err(e) => {
            logf!("pair: keep-alive unavailable: {e}");
            None
        }
    };
    let advert = minis::advertise(host::SERVICE_TYPE, &identity.identifier, port,
                                  &host::txt_records(&identity, DISPLAY_NAME))
        .await
        .map_err(|e| format!("Minis could not advertise the pairing host: {e}"))?;
    logf!("pair: advertising {} on guest port {port} ({})", advert.name, advert.state);
    logf!("pair: keeping Minis running with {}", keepalive.as_ref().map(|k| k.name()).unwrap_or("nothing"));
    let _ = events.send(Event::Waiting { keepalive: keepalive.as_ref().map(|k| k.name()) });

    let started = Instant::now();
    let mut attempt = 0;
    let mut last_check = Instant::now();
    loop {
        let left = WAIT_FOR_DEVICE.checked_sub(started.elapsed())
            .ok_or("Settings did not connect within 10 minutes")?;
        let accepted = tokio::select! {
            accepted = listener.accept() => Some(accepted),
            _ = tokio::time::sleep(Duration::from_secs(3).min(left)) => None,
        };
        if last_check.elapsed() > Duration::from_secs(3) {
            if let Some(k) = &keepalive {
                k.keep_going().await;
            }
            last_check = Instant::now();
        }
        let Some(accepted) = accepted else { continue };
        let (stream, peer) = accepted.map_err(|e| e.to_string())?;
        attempt += 1;
        logf!("pair: connection {attempt} from {peer}");
        let log = |line: String| logf!("pair: {line}");
        let events_for_code = events.clone();
        let issue = move || async move {
            let code = new_code();
            let spaced = format!("{} {}", &code[..3], &code[3..]);
            let notification = minis::notify(&format!("Pairing code {spaced}"),
                                             &format!("Type {spaced} in Settings to pair with {DISPLAY_NAME}.")).await;
            if let Err(e) = &notification {
                logf!("pair: notification failed: {e}");
            }
            let _ = events_for_code.send(Event::Code { code: code.clone(), attempt, notification: notification.is_ok() });
            code
        };
        let conversation = PairableHost::new(stream, &identity, DISPLAY_NAME, &log).accept(issue);
        match tokio::time::timeout(WAIT_FOR_CODE, conversation).await {
            Ok(Ok(peer)) => {
                let record = PairingRecord { host: identity.clone(), peer };
                state::write_private(&record_path(), &record.to_xml())
                    .map_err(|e| format!("paired, but the record could not be saved: {e}"))?;
                logf!("pair: paired with {} ({})", record.peer.name, record.peer.model);
                let _ = events.send(Event::Paired { name: record.peer.name.clone() });
                let _ = minis::notify("Paired", "Go back to Open Minis: BariStore continues.").await;
                drop(advert);
                drop(keepalive);
                return Ok(record);
            }
            Ok(Err(PairError::WrongCode)) => {
                logf!("pair: wrong code on try {attempt}");
                let _ = events.send(Event::WrongCode { attempts_left: TRIES.saturating_sub(attempt) });
            }
            Ok(Err(e)) => logf!("pair: try {attempt} ended: {e}"),
            Err(_) => logf!("pair: try {attempt}: no code typed within 5 minutes"),
        }
        if attempt >= TRIES {
            return Err("Pairing failed after three tries".into());
        }
    }
}
