//! The running store: state the page shows, the one task that runs at a time,
//! questions to the user, and the cached device and Apple sessions.

use serde::Serialize;
use serde_json::{json, Value as Json};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use tokio::sync::{oneshot, Mutex as AsyncMutex};

use crate::apple::{gsa, portal};
use crate::device::{services, session::{self, Session}};
use crate::install::{self, Installed, Options};
use crate::pairing::flow;
use crate::{logf, minis, state, store};

#[derive(Clone, Default, Serialize)]
pub struct Task {
    pub title: String,
    pub step: String,
    pub progress: Option<u32>,
    pub error: String,
    pub done: bool,
    /// What the user can do about an error ("retry" re-runs the same action).
    pub retry: Option<Json>,
}

#[derive(Clone, Serialize)]
pub struct Question {
    pub id: u64,
    pub kind: String,      // "code" | "text" | "choice"
    pub title: String,
    pub message: String,
    pub options: Vec<String>,
}

#[derive(Clone, Default, Serialize)]
pub struct Setup {
    pub vpn: Option<bool>,
    pub paired: bool,
    pub devmode: Option<bool>,
    pub signed_in: bool,
    pub email: String,
    pub team: String,
    pub team_name: String,
    pub udid: String,
    pub device_name: String,
}

#[derive(Default)]
struct Inner {
    setup: Setup,
    task: Option<Task>,
    question: Option<Question>,
    answers: HashMap<u64, oneshot::Sender<Option<String>>>,
    next_id: u64,
    pairing_code: String,
    /// A pairing run is advertising (the page shows where to tap).
    pairing_waiting: bool,
    catalog: Vec<store::App>,
    sources: Vec<store::Source>,
    refreshing: bool,
    device_apps: Vec<Json>,
}

#[derive(Clone)]
pub struct App {
    inner: Arc<Mutex<Inner>>,
    session: Arc<AsyncMutex<Option<Arc<Session>>>>,
    apple: Arc<AsyncMutex<Option<Arc<(gsa::GrandSlam, gsa::Account)>>>>,
    busy: Arc<AsyncMutex<()>>,
}

fn settings() -> Json {
    std::fs::read(state::path("settings.json")).ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_else(|| json!({"theme": "system", "increased_memory": false, "remove_extensions": false}))
}

impl App {
    pub fn new() -> App {
        let app = App {
            inner: Arc::new(Mutex::new(Inner::default())),
            session: Arc::new(AsyncMutex::new(None)),
            apple: Arc::new(AsyncMutex::new(None)),
            busy: Arc::new(AsyncMutex::new(())),
        };
        {
            let mut i = app.inner.lock().unwrap();
            i.catalog = store::cached_catalog();
            i.sources = store::sources();
            if let Some(r) = flow::load_record() {
                i.setup.paired = true;
                i.setup.udid = r.peer.udid.clone();
                i.setup.device_name = r.peer.name.clone();
            }
            if let Some(a) = gsa::Account::load() {
                i.setup.signed_in = true;
                i.setup.email = a.email;
            }
            if let Ok(t) = std::fs::read_to_string(state::path("team")) {
                let mut parts = t.splitn(2, '\n');
                i.setup.team = parts.next().unwrap_or("").trim().into();
                i.setup.team_name = parts.next().unwrap_or("").trim().into();
            }
        }
        app
    }

    pub fn snapshot(&self) -> Json {
        let i = self.inner.lock().unwrap();
        json!({
            "version": env!("CARGO_PKG_VERSION"),
            "setup": i.setup,
            "task": i.task,
            "question": i.question,
            "pairing_code": i.pairing_code,
            "pairing_waiting": i.pairing_waiting,
            "settings": settings(),
            "installed": install::installed(),
            "device_apps": i.device_apps,
            "log": crate::log::recent().iter().rev().take(80).rev().collect::<Vec<_>>(),
        })
    }

    pub fn catalog(&self) -> Json {
        let i = self.inner.lock().unwrap();
        json!({"apps": i.catalog, "sources": i.sources, "refreshing": i.refreshing})
    }

    fn set_task(&self, f: impl FnOnce(&mut Task)) {
        let mut i = self.inner.lock().unwrap();
        let t = i.task.get_or_insert_with(Task::default);
        f(t);
    }

    pub fn step(&self, text: impl Into<String>) {
        let text = text.into();
        logf!("task: {text}");
        self.set_task(|t| { t.step = text; t.progress = None; });
    }

    pub fn progress(&self, text: impl Into<String>, pct: u32) {
        let text = text.into();
        self.set_task(|t| { t.step = text; t.progress = Some(pct.min(100)); });
    }

    /// Shows a question on the page and waits for the answer (None: cancelled).
    pub async fn ask(&self, kind: &str, title: &str, message: &str, options: &[&str]) -> Option<String> {
        let (tx, rx) = oneshot::channel();
        {
            let mut i = self.inner.lock().unwrap();
            i.next_id += 1;
            let id = i.next_id;
            i.answers.insert(id, tx);
            i.question = Some(Question { id, kind: kind.into(), title: title.into(), message: message.into(),
                                         options: options.iter().map(|s| s.to_string()).collect() });
        }
        let answer = rx.await.ok().flatten();
        self.inner.lock().unwrap().question = None;
        answer
    }

    pub fn answer(&self, id: u64, value: Option<String>) {
        let tx = self.inner.lock().unwrap().answers.remove(&id);
        if let Some(tx) = tx {
            let _ = tx.send(value);
        }
    }

    /// Runs `job` as the one task; its error ends the task with a message.
    pub fn spawn<F, Fut>(&self, title: &str, retry: Option<Json>, job: F)
    where F: FnOnce(App) -> Fut + Send + 'static, Fut: std::future::Future<Output = Result<String, String>> + Send
    {
        let app = self.clone();
        let title = title.to_string();
        tokio::spawn(async move {
            let Ok(_guard) = app.busy.try_lock() else {
                logf!("task: {title} refused: another task is running");
                return;
            };
            {
                let mut i = app.inner.lock().unwrap();
                i.task = Some(Task { title: title.clone(), ..Default::default() });
            }
            logf!("task: start {title}");
            let result = job(app.clone()).await;
            let mut i = app.inner.lock().unwrap();
            let t = i.task.get_or_insert_with(Task::default);
            t.done = true;
            t.progress = None;
            match result {
                Ok(msg) => { logf!("task: done {title}: {msg}"); t.step = msg; }
                Err(e) => { logf!("task: failed {title}: {e}"); t.error = e; t.retry = retry; }
            }
        });
    }

    pub fn dismiss_task(&self) {
        let mut i = self.inner.lock().unwrap();
        if i.task.as_ref().is_some_and(|t| t.done) {
            i.task = None;
        }
    }

    // --- device ---------------------------------------------------------

    pub async fn check_vpn(&self) -> bool {
        let up = session::vpn_up().await;
        self.inner.lock().unwrap().setup.vpn = Some(up);
        up
    }

    /// A live session (reused while it works), or a clear reason why not.
    pub async fn device(&self) -> Result<Arc<Session>, String> {
        let mut guard = self.session.lock().await;
        if let Some(s) = guard.as_ref() {
            if services::developer_mode(s).await.is_ok() {
                return Ok(s.clone());
            }
            *guard = None;
        }
        if !self.check_vpn().await {
            return Err("LocalDevVPN is not connected. Open LocalDevVPN, connect it, then try again.".into());
        }
        let record = flow::load_record().ok_or("This iPhone is not paired with BariStore yet.")?;
        let s = Arc::new(Session::open(&record).await.map_err(|e| e.to_string())?);
        let dm = services::developer_mode(&s).await.ok().flatten();
        {
            let mut i = self.inner.lock().unwrap();
            i.setup.devmode = dm;
            if !s.udid.is_empty() {
                i.setup.udid = s.udid.clone();
            }
            if let Some(n) = s.properties.get("DeviceName").and_then(plist::Value::as_string) {
                i.setup.device_name = n.to_string();
            }
        }
        *guard = Some(s.clone());
        Ok(s)
    }

    pub async fn pair(&self) -> Result<String, String> {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let app = self.clone();
        let watcher = tokio::spawn(async move {
            while let Some(ev) = rx.recv().await {
                match ev {
                    flow::Event::Waiting { keepalive } => {
                        app.inner.lock().unwrap().pairing_waiting = true;
                        app.step(if keepalive {
                            "Waiting for Settings. Swipe Minis's player down: it keeps playing, and keeps BariStore running."
                        } else {
                            "Waiting for Settings. Minis's player did not start: be quick in Settings."
                        });
                    }
                    flow::Event::Code { code, attempt, notification } => {
                        app.inner.lock().unwrap().pairing_code = code;
                        app.step(format!("Settings asks for the code (try {attempt}).{}",
                            if notification { " It also came as a notification." } else { "" }));
                    }
                    flow::Event::WrongCode { attempts_left } => app.step(format!("Wrong code. {attempts_left} tries left")),
                    flow::Event::Paired { udid, name } => app.step(format!("Paired with {name} ({})", udid.get(..8).unwrap_or(""))),
                }
            }
        });
        let result = flow::run(tx).await;
        watcher.abort();
        {
            let mut i = self.inner.lock().unwrap();
            i.pairing_code.clear();
            i.pairing_waiting = false;
        }
        let record = result?;
        *self.session.lock().await = None;
        {
            let mut i = self.inner.lock().unwrap();
            i.setup.paired = true;
            i.setup.udid = record.peer.udid.clone();
            i.setup.device_name = record.peer.name.clone();
        }
        // Prove the new record works and learn Developer Mode's state now.
        self.step("Connecting through LocalDevVPN");
        match self.device().await {
            Ok(_) => Ok(format!("Paired with {}. The connection works.", record.peer.name)),
            Err(e) => {
                logf!("pair: paired, but the first connection failed: {e}");
                Ok(format!("Paired with {}, but connecting failed: {e}", record.peer.name))
            }
        }
    }

    pub async fn developer_mode(&self, action: Option<u64>) -> Result<String, String> {
        let s = self.device().await?;
        if let Some(a) = action {
            services::amfi_action(&s, a).await?;
            if a == 1 {
                *self.session.lock().await = None;
                return Ok("Developer Mode is being turned on: the iPhone restarts. Open Minis and BariStore again afterwards.".into());
            }
            return Ok("The Developer Mode switch is now in Settings › Privacy & Security.".into());
        }
        let dm = services::developer_mode(&s).await?;
        self.inner.lock().unwrap().setup.devmode = dm;
        Ok(match dm { Some(true) => "Developer Mode is on".into(), Some(false) => "Developer Mode is off".into(), None => "unknown".into() })
    }

    // --- Apple ----------------------------------------------------------

    pub async fn apple(&self) -> Result<Arc<(gsa::GrandSlam, gsa::Account)>, String> {
        let mut guard = self.apple.lock().await;
        if let Some(a) = guard.as_ref() {
            if a.1.token_fresh() {
                return Ok(a.clone());
            }
        }
        match gsa::resume().await {
            Ok(pair) => {
                let pair = Arc::new(pair);
                *guard = Some(pair.clone());
                Ok(pair)
            }
            Err(e) => {
                logf!("apple: saved session unusable: {e}");
                drop(guard);
                self.inner.lock().unwrap().setup.signed_in = false;
                Err("Apple needs you to sign in again (Options › Apple ID).".into())
            }
        }
    }

    pub async fn login(&self, email: String, password: String) -> Result<String, String> {
        let app = self.clone();
        let ask: Box<gsa::CodeAsker> = Box::new(move |kind, hint| {
            let app = app.clone();
            Box::pin(async move {
                let where_ = match kind {
                    gsa::CodeKind::TrustedDevice => "Apple sent a 6-digit code to your devices.",
                    gsa::CodeKind::Sms => "Apple sent a 6-digit code by text message.",
                };
                let msg = match hint { Some(h) => format!("{h} {where_}"), None => where_.to_string() };
                let options: &[&str] = if kind == gsa::CodeKind::TrustedDevice { &["sms"] } else { &[] };
                app.ask("code", "Verification code", &msg, options).await
            })
        });
        self.step("Signing in to Apple");
        let app = self.clone();
        let status = move |text: String| app.step(text);
        let (gs, account) = gsa::login(email.trim(), &password, ask.as_ref(), &status).await.map_err(|e| e.to_string())?;
        account.save().map_err(|e| format!("signed in, but the session could not be saved: {e}"))?;
        self.step("Choosing the developer team");
        let teams = portal::Portal { gs: &gs, account: &account }.teams().await.map_err(|e| e.to_string())?;
        let team = match teams.len() {
            0 => return Err("This Apple ID has no developer team".into()),
            1 => teams[0].clone(),
            _ => {
                let labels: Vec<String> = teams.iter().map(|t| format!("{} ({})", t.name, t.kind)).collect();
                let refs: Vec<&str> = labels.iter().map(String::as_str).collect();
                let pick = self.ask("choice", "Developer team", "Which team should sign your apps?", &refs).await
                    .ok_or("sign-in cancelled")?;
                teams.into_iter().zip(labels).find(|(_, l)| *l == pick).map(|(t, _)| t).ok_or("no team chosen")?
            }
        };
        let _ = std::fs::write(state::path("team"), format!("{}\n{}", team.id, team.name));
        {
            let mut i = self.inner.lock().unwrap();
            i.setup.signed_in = true;
            i.setup.email = account.email.clone();
            i.setup.team = team.id.clone();
            i.setup.team_name = team.name.clone();
        }
        *self.apple.lock().await = Some(Arc::new((gs, account)));
        Ok("Signed in".into())
    }

    // --- store ----------------------------------------------------------

    pub fn refresh_catalog(&self) {
        let app = self.clone();
        {
            let mut i = app.inner.lock().unwrap();
            if i.refreshing { return; }
            i.refreshing = true;
        }
        tokio::spawn(async move {
            let (catalog, sources) = store::refresh().await;
            let mut i = app.inner.lock().unwrap();
            i.catalog = catalog;
            i.sources = sources;
            i.refreshing = false;
        });
    }

    pub fn add_source(&self, url: &str) -> Result<(), String> {
        let url = url.trim();
        if !url.starts_with("https://") {
            return Err("A source address starts with https://".into());
        }
        let mut list = store::sources();
        if !list.iter().any(|s| s.url == url) {
            list.push(store::Source { url: url.into(), name: url.into(), error: String::new() });
            store::save_sources(&list);
        }
        self.refresh_catalog();
        Ok(())
    }

    pub fn remove_source(&self, url: &str) {
        let mut list = store::sources();
        list.retain(|s| s.url != url);
        store::save_sources(&list);
        self.refresh_catalog();
    }

    async fn ready_to_install(&self) -> Result<(Arc<Session>, Arc<(gsa::GrandSlam, gsa::Account)>), String> {
        self.step("Checking LocalDevVPN and the pairing");
        let s = self.device().await?;
        if services::developer_mode(&s).await? == Some(false) {
            return Err("Developer Mode is off. Turn it on in Settings › Privacy & Security › Developer Mode, \
                        let the iPhone restart, then try again.".into());
        }
        self.step("Checking the Apple ID");
        let apple = self.apple().await?;
        Ok((s, apple))
    }

    fn team(&self) -> Result<portal::Team, String> {
        let i = self.inner.lock().unwrap();
        if i.setup.team.is_empty() {
            return Err("Sign in to Apple first (Options › Apple ID).".into());
        }
        Ok(portal::Team { id: i.setup.team.clone(), name: i.setup.team_name.clone(), kind: String::new() })
    }

    pub async fn install_version(&self, app_key: &str, version: usize, opts: Options) -> Result<String, String> {
        let (entry, v) = {
            let i = self.inner.lock().unwrap();
            let entry = i.catalog.iter().find(|a| a.key == app_key).cloned().ok_or("that app is no longer in the store")?;
            let v = entry.versions.get(version).cloned().ok_or("that version is gone")?;
            (entry, v)
        };
        let (session, apple) = self.ready_to_install().await?;
        let app = self.clone();
        let ipa_path = store::download(&v, &move |done, total| {
            let pct = if total > 0 { (done * 100 / total) as u32 } else { 0 };
            app.progress(format!("Downloading {} MB", done / 1_000_000), pct);
        }).await?;
        let bytes = std::fs::read(&ipa_path).map_err(|e| e.to_string())?;
        self.sign_and_install(&session, &apple, &bytes, opts, Installed {
            bundle_id: String::new(), original_id: entry.bundle_id.clone(), name: entry.name.clone(),
            version: v.version.clone(), source_url: v.url.clone(), ipa_path: ipa_path.to_string_lossy().into(),
            team: String::new(), expires: 0, installed_at: 0, options: Options::default(), icon_url: entry.icon.clone(),
        }).await
    }

    async fn sign_and_install(&self, session: &Session, apple: &(gsa::GrandSlam, gsa::Account), bytes: &[u8],
                              opts: Options, mut entry: Installed) -> Result<String, String> {
        let team = self.team()?;
        let (udid, name) = {
            let i = self.inner.lock().unwrap();
            (if session.udid.is_empty() { i.setup.udid.clone() } else { session.udid.clone() }, i.setup.device_name.clone())
        };
        if udid.is_empty() {
            return Err("this iPhone's UDID is unknown; pair again".into());
        }
        let ctx = install::Context { gs: &apple.0, account: &apple.1, team: &team, udid: &udid,
                                     device_name: if name.is_empty() { "iPhone" } else { &name } };
        let app = self.clone();
        let status = move |s: String| app.step(s);
        let (signed, new_id, expires, version) = install::prepare_and_sign(&ctx, bytes, &opts, &status).await?;
        if entry.version.is_empty() {
            entry.version = version;
        }
        let app = self.clone();
        let status = move |s: String| {
            let pct = s.rsplit(' ').next().and_then(|p| p.trim_end_matches('%').parse().ok());
            match pct { Some(p) => app.progress(s.clone(), p), None => app.step(s.clone()) }
        };
        entry.bundle_id = new_id.clone();
        entry.team = team.id.clone();
        entry.expires = expires;
        entry.options = opts;
        install::install_signed(session, &signed, entry.clone(), &status).await?;
        if let Some(record) = flow::load_record() {
            match install::place_pairing_file(session, &new_id, &entry.original_id, &record).await {
                Ok(true) => self.step("Pairing file placed"),
                Ok(false) => {}
                Err(e) => logf!("install: pairing file not placed: {e}"),
            }
        }
        Ok(format!("{} is installed. First time: Settings › General › VPN & Device Management › trust your Apple ID.", entry.name))
    }

    pub async fn refresh_app(&self, bundle_id: &str) -> Result<String, String> {
        let entry = install::installed().into_iter().find(|i| i.bundle_id == bundle_id).ok_or("not installed by BariStore")?;
        let (session, apple) = self.ready_to_install().await?;
        let bytes = std::fs::read(&entry.ipa_path).map_err(|_| "the original IPA is gone; install the app again from the store".to_string())?;
        let opts = entry.options.clone();
        self.sign_and_install(&session, &apple, &bytes, opts, entry).await
    }

    pub async fn refresh_all(&self) -> Result<String, String> {
        let list = install::installed();
        let soon = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs() + 3 * 86400;
        let due: Vec<Installed> = list.into_iter().filter(|i| i.expires < soon).collect();
        if due.is_empty() {
            return Ok("Every app is good for at least three more days".into());
        }
        for (n, e) in due.iter().enumerate() {
            self.step(format!("Refreshing {} ({}/{})", e.name, n + 1, due.len()));
            self.refresh_app(&e.bundle_id).await.map_err(|err| format!("{}: {err}", e.name))?;
        }
        Ok(format!("Refreshed {} app(s)", due.len()))
    }

    pub async fn delete_app(&self, bundle_id: &str) -> Result<String, String> {
        let s = self.device().await?;
        self.step("Removing the app");
        services::uninstall(&s, bundle_id).await?;
        let mut list = install::installed();
        if let Some(e) = list.iter().find(|i| i.bundle_id == bundle_id) {
            let still_used = list.iter().filter(|o| o.ipa_path == e.ipa_path).count() > 1;
            if !still_used {
                let _ = std::fs::remove_file(&e.ipa_path);
            }
        }
        list.retain(|i| i.bundle_id != bundle_id);
        install::save_installed(&list);
        self.load_device_apps().await.ok();
        Ok("Removed".into())
    }

    pub async fn load_device_apps(&self) -> Result<(), String> {
        let s = self.device().await?;
        let apps = services::installed_apps(&s).await?;
        let list: Vec<Json> = apps.iter().map(|d| {
            let t = |k: &str| d.get(k).and_then(plist::Value::as_string).unwrap_or("").to_string();
            json!({"bundle_id": t("CFBundleIdentifier"), "name": if t("CFBundleDisplayName").is_empty() { t("CFBundleName") } else { t("CFBundleDisplayName") },
                   "version": t("CFBundleShortVersionString"), "signer": t("SignerIdentity")})
        }).collect();
        self.inner.lock().unwrap().device_apps = list;
        Ok(())
    }

    pub async fn place_pairing(&self, bundle_id: &str) -> Result<String, String> {
        let s = self.device().await?;
        let record = flow::load_record().ok_or("not paired")?;
        let original = install::installed().into_iter().find(|i| i.bundle_id == bundle_id)
            .map(|i| i.original_id).unwrap_or_else(|| bundle_id.to_string());
        if install::place_pairing_file(&s, bundle_id, &original, &record).await? {
            Ok("Pairing file placed in the app".into())
        } else {
            Err("this app has no known place for a pairing file".into())
        }
    }

    pub fn export_pairing(&self) -> Result<String, String> {
        let record = flow::load_record().ok_or("not paired yet")?;
        let dir = export_dir();
        std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        let path = dir.join("BariStore-Pairing.plist");
        std::fs::write(&path, record.to_remote_only_xml()).map_err(|e| e.to_string())?;
        Ok(format!("Saved to {} (Files app › Minis). Keep it private.", path.display()))
    }

    pub fn save_settings(&self, patch: &Json) {
        let mut s = settings();
        if let (Some(obj), Some(p)) = (s.as_object_mut(), patch.as_object()) {
            for (k, v) in p {
                if ["theme", "increased_memory", "remove_extensions"].contains(&k.as_str()) {
                    obj.insert(k.clone(), v.clone());
                }
            }
        }
        let _ = std::fs::write(state::path("settings.json"), s.to_string());
    }

    pub async fn sign_out(&self) {
        let _ = std::fs::remove_file(state::path("account.plist"));
        *self.apple.lock().await = None;
        let mut i = self.inner.lock().unwrap();
        i.setup.signed_in = false;
        i.setup.email.clear();
    }

    /// Forgets everything except installed apps on the iPhone itself.
    pub async fn reset(&self) {
        *self.apple.lock().await = None;
        *self.session.lock().await = None;
        for name in ["account.plist", "team", "pairing.plist", "host.plist", "installed.json", "catalog.json",
                     "sources.json", "settings.json", "signing-key.der", "anisette-server", "host-uuid"] {
            let _ = std::fs::remove_file(state::path(name));
        }
        let _ = std::fs::remove_dir_all(state::path("ipas"));
        if let Ok(dir) = std::fs::read_dir(state::home()) {
            for e in dir.flatten() {
                if e.file_name().to_string_lossy().starts_with("anisette-") {
                    let _ = std::fs::remove_file(e.path());
                }
            }
        }
        let mut i = self.inner.lock().unwrap();
        *i = Inner::default();
        i.sources = store::sources();
    }
}

/// Where exported files go: Minis's shared folder when it exists (visible in
/// the Files app), else BariStore's own folder.
pub fn export_dir() -> std::path::PathBuf {
    for dir in ["/var/minis/shared", "/var/minis/workspace"] {
        if std::path::Path::new(dir).is_dir() {
            return std::path::PathBuf::from(dir);
        }
    }
    state::home()
}

pub fn minis_ok() -> bool {
    minis::available("minis-bonjour")
}
