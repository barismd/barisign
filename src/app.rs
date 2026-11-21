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
    /// What keeps Minis running during pairing ("" when nothing does).
    pairing_keepalive: String,
    catalog: Vec<store::App>,
    sources: Vec<store::Source>,
    refreshing: bool,
    device_apps: Vec<Json>,
    /// The team's App IDs, as My Apps shows them (null until asked).
    app_ids: Json,
}

#[derive(Clone)]
pub struct App {
    inner: Arc<Mutex<Inner>>,
    session: Arc<AsyncMutex<Option<Arc<Session>>>>,
    apple: Arc<AsyncMutex<Option<Arc<(gsa::GrandSlam, gsa::Account)>>>>,
    busy: Arc<AsyncMutex<()>>,
}

const SETTINGS: [&str; 4] = ["theme", "increased_memory", "remove_extensions", "setup_done"];

fn settings() -> Json {
    let mut s = json!({"theme": "system", "increased_memory": false, "remove_extensions": false, "setup_done": false});
    let saved: Json = std::fs::read(state::path("settings.json")).ok()
        .and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(Json::Null);
    if let (Some(obj), Some(saved)) = (s.as_object_mut(), saved.as_object()) {
        for (k, v) in saved {
            if SETTINGS.contains(&k.as_str()) {
                obj.insert(k.clone(), v.clone());
            }
        }
    }
    s
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
            "boot": boot_id(),
            "setup": i.setup,
            "task": i.task,
            "question": i.question,
            "pairing_code": i.pairing_code,
            "pairing_waiting": i.pairing_waiting,
            "pairing_keepalive": i.pairing_keepalive,
            "settings": settings(),
            "installed": install::installed(),
            "device_apps": i.device_apps,
            "app_ids": i.app_ids,
            "log": crate::log::recent().iter().rev().take(80).rev().collect::<Vec<_>>(),
        })
    }

    pub fn catalog(&self) -> Json {
        let i = self.inner.lock().unwrap();
        json!({"apps": i.catalog, "sources": i.sources, "suggested": store::suggested(&i.sources),
               "refreshing": i.refreshing})
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
            return Err(session::NO_ROUTE.into());
        }
        let record = flow::load_record().ok_or("This iPhone is not paired yet.")?;
        // Bounded: on mobile data LocalDevVPN can take a connection and then
        // never answer.
        let s = match tokio::time::timeout(std::time::Duration::from_secs(45), Session::open(&record)).await {
            Ok(Ok(s)) => Arc::new(s),
            Ok(Err(e)) => return Err(e.to_string()),
            Err(_) => {
                logf!("device: no tunnel within 45 s");
                return Err(session::NO_ROUTE.into());
            }
        };
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

    /// `player`: keep Minis running with its player instead of silent speech.
    pub async fn pair(&self, player: bool) -> Result<String, String> {
        // Pairing itself would work on mobile data, but nothing after it would.
        self.step("Checking Wi-Fi and LocalDevVPN");
        if !self.check_vpn().await {
            return Err(session::NO_ROUTE.into());
        }
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let app = self.clone();
        let watcher = tokio::spawn(async move {
            while let Some(ev) = rx.recv().await {
                match ev {
                    flow::Event::Waiting { keepalive } => {
                        {
                            let mut i = app.inner.lock().unwrap();
                            i.pairing_waiting = true;
                            i.pairing_keepalive = keepalive.unwrap_or("").to_string();
                        }
                        app.step(match keepalive {
                            Some(_) => "Waiting for Settings",
                            None => "Waiting for Settings. Minis may stop in the background: be quick.",
                        });
                    }
                    flow::Event::Code { code, attempt, notification } => {
                        app.inner.lock().unwrap().pairing_code = code;
                        let new = if attempt == 1 { "the code" } else { "the new code" };
                        app.step(if notification { format!("Type {new} from the notification in Settings") }
                                 else { format!("Type {new} below in Settings (no notification could be sent)") });
                    }
                    flow::Event::WrongCode { attempts_left } => {
                        app.inner.lock().unwrap().pairing_code.clear();
                        app.step(format!("Wrong code. Tap Pair with BariStore again ({attempts_left} tries left)."));
                    }
                    flow::Event::Paired { name } => app.step(format!("Paired with {name}")),
                }
            }
        });
        let result = flow::run(tx, player).await;
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
        self.step("Checking the connection");
        match self.device().await {
            Ok(_) => Ok(format!("Paired with {}.", record.peer.name)),
            Err(e) => {
                logf!("pair: paired, but the first connection failed: {e}");
                Ok(format!("Paired with {}, but the connection check failed: {e}", record.peer.name))
            }
        }
    }

    pub async fn developer_mode(&self, action: Option<u64>) -> Result<String, String> {
        let s = self.device().await?;
        if let Some(a) = action {
            services::amfi_action(&s, a).await?;
            if a == 1 {
                *self.session.lock().await = None;
                return Ok("The iPhone restarts to turn Developer Mode on. Open BariStore again afterwards.".into());
            }
            return Ok("Developer Mode is now in Settings › Privacy & Security.".into());
        }
        let dm = services::developer_mode(&s).await?;
        self.inner.lock().unwrap().setup.devmode = dm;
        Ok(match dm { Some(true) => "Developer Mode is on.", Some(false) => "Developer Mode is off.", None => "Developer Mode: unknown." }.into())
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
            // A network failure must not sign anyone out: the page keeps the
            // account and says what failed.
            Err(e) => {
                logf!("apple: saved session unusable: {e}");
                Err(format!("Apple did not accept the saved sign-in ({e}). Try again; if it keeps failing, \
                             sign out and in again in Options › Apple ID."))
            }
        }
    }

    pub async fn login(&self, email: String, password: String) -> Result<String, String> {
        let app = self.clone();
        let ask: Box<gsa::CodeAsker> = Box::new(move |kind, hint| {
            let app = app.clone();
            Box::pin(async move {
                let where_ = match kind {
                    gsa::CodeKind::TrustedDevice => "Enter the code Apple sent to your devices.",
                    gsa::CodeKind::Sms => "Enter the code Apple sent by text message.",
                };
                let msg = match hint { Some(h) => format!("{h} {where_}"), None => where_.to_string() };
                let options: &[&str] = if kind == gsa::CodeKind::TrustedDevice { &["sms"] } else { &[] };
                app.ask("code", "Two-factor code", &msg, options).await
            })
        });
        self.step("Signing in to Apple");
        let app = self.clone();
        let status = move |text: String| app.step(text);
        let (gs, account) = gsa::login(email.trim(), &password, ask.as_ref(), &status).await.map_err(|e| e.to_string())?;
        account.save().map_err(|e| format!("signed in, but the session could not be saved: {e}"))?;
        self.step("Loading your team");
        let teams = portal::Portal { gs: &gs, account: &account }.teams().await.map_err(|e| e.to_string())?;
        let team = match teams.len() {
            0 => return Err("This Apple ID has no developer team".into()),
            1 => teams[0].clone(),
            _ => {
                let labels: Vec<String> = teams.iter().map(|t| format!("{} ({})", t.name, t.kind)).collect();
                let refs: Vec<&str> = labels.iter().map(String::as_str).collect();
                let pick = self.ask("choice", "Team", "Which team signs your apps?", &refs).await
                    .ok_or("Cancelled")?;
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
        Ok("Signed in.".into())
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
            let icons: Vec<String> = catalog.iter().map(|a| a.icon.clone()).collect();
            {
                let mut i = app.inner.lock().unwrap();
                i.catalog = catalog;
                i.sources = sources;
                i.refreshing = false;
            }
            crate::icons::prefetch(icons).await;
        });
    }

    pub fn add_source(&self, url: &str) -> Result<(), String> {
        let url = url.trim();
        if !url.starts_with("https://") {
            return Err("The address must start with https://".into());
        }
        let mut list = store::sources();
        if !list.iter().any(|s| s.url == url) {
            let name = store::default_name(url).unwrap_or(url).to_string();
            list.push(store::Source { url: url.into(), name, error: String::new() });
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
        self.step("Connecting");
        let s = self.device().await?;
        if services::developer_mode(&s).await? == Some(false) {
            return Err("Developer Mode is off. Turn it on in Settings › Privacy & Security, then try again.".into());
        }
        self.step("Checking your Apple ID");
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

    pub async fn install_version(&self, app_key: &str, version: usize, opts: Options, reuse: install::Reuse)
        -> Result<String, String>
    {
        let (entry, v) = {
            let i = self.inner.lock().unwrap();
            let entry = i.catalog.iter().find(|a| a.key == app_key).cloned().ok_or("This app is no longer in the store.")?;
            let v = entry.versions.get(version).cloned().ok_or("This version is no longer offered.")?;
            (entry, v)
        };
        let (session, apple) = self.ready_to_install().await?;
        let app = self.clone();
        let started = std::time::Instant::now();
        let ipa_path = store::download(&v, &move |done, total| {
            let pct = if total > 0 { (done * 100 / total) as u32 } else { 0 };
            app.progress(format!("Downloading {} of {} MB", done / 1_000_000, total.max(done) / 1_000_000), pct);
        }).await?;
        logf!("install: downloaded in {:.1} s", started.elapsed().as_secs_f64());
        let bytes = std::fs::read(&ipa_path).map_err(|e| e.to_string())?;
        self.sign_and_install(&session, &apple, bytes, opts, reuse, Installed {
            bundle_id: String::new(), original_id: entry.bundle_id.clone(), name: entry.name.clone(),
            version: v.version.clone(), source_url: v.url.clone(), ipa_path: ipa_path.to_string_lossy().into(),
            team: String::new(), expires: 0, installed_at: 0, options: Options::default(), icon_url: entry.icon.clone(),
            ids: Default::default(), increased_memory: false,
        }).await
    }

    /// Installs an IPA the user picked (uploaded by the page into ipas/).
    pub async fn install_local(&self, path: &str, opts: Options, reuse: install::Reuse) -> Result<String, String> {
        let path = std::path::Path::new(path);
        if path.parent() != Some(state::path("ipas").as_path()) {
            return Err("That file is not one BariStore received.".into());
        }
        let info = crate::sign::bundle::peek(path)?;
        let (session, apple) = self.ready_to_install().await?;
        let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
        self.sign_and_install(&session, &apple, bytes, opts, reuse, Installed {
            bundle_id: String::new(), original_id: info.id, name: info.name, version: info.version,
            source_url: String::new(), ipa_path: path.to_string_lossy().into(), team: String::new(), expires: 0,
            installed_at: 0, options: Options::default(), icon_url: String::new(), ids: Default::default(),
            increased_memory: false,
        }).await
    }

    async fn sign_and_install(&self, session: &Session, apple: &(gsa::GrandSlam, gsa::Account), bytes: Vec<u8>,
                              opts: Options, reuse: install::Reuse, mut entry: Installed) -> Result<String, String> {
        let team = self.team()?;
        let (udid, name) = {
            let i = self.inner.lock().unwrap();
            (if session.udid.is_empty() { i.setup.udid.clone() } else { session.udid.clone() }, i.setup.device_name.clone())
        };
        if udid.is_empty() {
            return Err("This iPhone's UDID is unknown. Pair again (Options › Pairing).".into());
        }
        let app = self.clone();
        let ask: Box<install::Asker> = Box::new(move |title, message, options| {
            let app = app.clone();
            Box::pin(async move {
                let refs: Vec<&str> = options.iter().map(String::as_str).collect();
                app.ask("choice", &title, &message, &refs).await
            })
        });
        let previous = (!entry.bundle_id.is_empty()).then(|| (entry.bundle_id.clone(), entry.ids.clone()));
        let ctx = install::Context {
            gs: &apple.0, account: &apple.1, team: &team, udid: &udid,
            device_name: if name.is_empty() { "iPhone" } else { &name },
            session, ask: ask.as_ref(),
            reuse,
            previous: previous.as_ref().map(|(id, ids)| (id.as_str(), ids)),
        };
        let app = self.clone();
        let status = move |s: String| app.step(s);
        let prepared = install::prepare_and_sign(&ctx, bytes, &opts, &status).await?;
        if entry.version.is_empty() {
            entry.version = prepared.version.clone();
        }
        let app = self.clone();
        let status = move |stage: &str, pct: u32| {
            let what = if stage == "Uploading" { "Uploading" } else { "Installing" };
            app.progress(format!("{what} {pct}%"), pct);
        };
        entry.bundle_id = prepared.main_id.clone();
        entry.ids = prepared.ids.clone();
        entry.increased_memory = prepared.increased_memory;
        entry.team = team.id.clone();
        entry.expires = prepared.expires;
        let opts_memory = opts.increased_memory;
        entry.options = opts;
        let started = std::time::Instant::now();
        install::install_signed(session, &prepared.ipa, entry.clone(), &status).await?;
        logf!("install: uploaded and installed {} MB in {:.1} s", prepared.ipa.len() / 1_000_000,
              started.elapsed().as_secs_f64());
        if let Some(record) = flow::load_record() {
            self.step("Giving the app the pairing file");
            if let Err(e) = install::place_pairing_file(session, &prepared.main_id, &entry.original_id, &record).await {
                logf!("install: pairing file not placed: {e}");
            }
        }
        self.inner.lock().unwrap().app_ids = Json::Null;
        let first = install::installed().len() <= 1;
        let memory = if opts_memory && !prepared.increased_memory {
            " Apple did not add Increased Memory Limit to its profile."
        } else { "" };
        Ok(if first {
            format!("{} is installed.{memory} Before opening it the first time, trust your Apple ID in Settings › \
                     General › VPN & Device Management.", entry.name)
        } else {
            format!("{} is installed.{memory}", entry.name)
        })
    }

    pub async fn refresh_app(&self, bundle_id: &str) -> Result<String, String> {
        let mut entry = install::installed().into_iter().find(|i| i.bundle_id == bundle_id)
            .ok_or("BariStore did not install this app.")?;
        let (session, apple) = self.ready_to_install().await?;
        if !std::path::Path::new(&entry.ipa_path).is_file() {
            // Cleared from the cache: download it again from where it came from.
            if entry.source_url.is_empty() {
                return Err("The IPA this app was installed from is gone. Add it again with Add IPA.".into());
            }
            let v = store::Version {
                source: String::new(), source_url: String::new(), channel: String::new(), version: entry.version.clone(),
                date: String::new(), url: entry.source_url.clone(), size: 0, sha256: String::new(), min_os: String::new(),
                notes: String::new(), warning: String::new(),
            };
            let app = self.clone();
            let path = store::download(&v, &move |done, total| {
                let pct = if total > 0 { (done * 100 / total) as u32 } else { 0 };
                app.progress(format!("Downloading {} of {} MB", done / 1_000_000, total.max(done) / 1_000_000), pct);
            }).await?;
            entry.ipa_path = path.to_string_lossy().into();
        }
        let bytes = std::fs::read(&entry.ipa_path).map_err(|e| e.to_string())?;
        let opts = entry.options.clone();
        let name = entry.name.clone();
        self.sign_and_install(&session, &apple, bytes, opts, install::Reuse::default(), entry).await?;
        Ok(format!("{name} is refreshed for 7 more days."))
    }

    pub async fn refresh_all(&self) -> Result<String, String> {
        let list = install::installed();
        if list.is_empty() {
            return Ok("No apps to refresh.".into());
        }
        let mut failed = Vec::new();
        for (n, e) in list.iter().enumerate() {
            self.inner.lock().unwrap().task.as_mut().map(|t| t.title = format!("Refreshing {} of {}", n + 1, list.len()));
            if let Err(err) = self.refresh_app(&e.bundle_id).await {
                logf!("refresh: {}: {err}", e.name);
                failed.push(format!("{}: {err}", e.name));
            }
        }
        if failed.is_empty() {
            Ok(format!("{} app{} refreshed for 7 more days.", list.len(), if list.len() == 1 { "" } else { "s" }))
        } else {
            Err(failed.join("\n"))
        }
    }

    /// The team's App IDs for My Apps: each one's app, expiry, and how many
    /// a free team has left.
    pub async fn load_app_ids(&self) -> Result<(), String> {
        let apple = self.apple().await?;
        let team = self.team()?;
        let ids = portal::Portal { gs: &apple.0, account: &apple.1 }.app_ids(&team.id).await.map_err(|e| e.to_string())?;
        // Which app holds each App ID: the apps on the iPhone (any tool's),
        // else BariStore's own list when the iPhone cannot be reached.
        let mut apps: Vec<(String, String)> = install::installed().into_iter().map(|i| (i.bundle_id, i.name)).collect();
        if let Ok(s) = self.device().await {
            if let Ok(list) = services::installed_apps(&s).await {
                apps = list.iter().filter_map(|d| {
                    let t = |k: &str| d.get(k).and_then(plist::Value::as_string).unwrap_or("").to_string();
                    let name = [t("CFBundleDisplayName"), t("CFBundleName")].into_iter().find(|n| !n.is_empty())?;
                    Some((t("CFBundleIdentifier"), name))
                }).collect();
            }
        }
        let owner = |id: &str| apps.iter().find(|(bundle, _)| id == bundle || id.starts_with(&format!("{bundle}.")))
            .map(|(_, name)| name.clone()).unwrap_or_default();
        let list: Vec<Json> = ids.iter().map(|a| json!({
            "identifier": a.identifier, "name": a.name, "expires": a.expires, "app": owner(&a.identifier),
        })).collect();
        self.inner.lock().unwrap().app_ids = json!({
            "list": list, "left": portal::FREE_APP_IDS.saturating_sub(ids.len()), "max": portal::FREE_APP_IDS,
        });
        Ok(())
    }

    pub async fn delete_app(&self, bundle_id: &str) -> Result<String, String> {
        let s = self.device().await?;
        self.step("Deleting");
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
        self.inner.lock().unwrap().app_ids = Json::Null;
        Ok("Deleted. Its App IDs stay reserved until they expire and can be reused.".into())
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
        let record = flow::load_record().ok_or("This iPhone is not paired yet.")?;
        let original = install::installed().into_iter().find(|i| i.bundle_id == bundle_id)
            .map(|i| i.original_id).unwrap_or_else(|| bundle_id.to_string());
        if install::place_pairing_file(&s, bundle_id, &original, &record).await? {
            Ok("The app has the pairing file now.".into())
        } else {
            Err("This app does not read a pairing file.".into())
        }
    }

    pub fn export_pairing(&self) -> Result<String, String> {
        let record = flow::load_record().ok_or("This iPhone is not paired yet.")?;
        let dir = export_dir();
        std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        let path = dir.join("BariStore-Pairing.plist");
        std::fs::write(&path, record.to_remote_only_xml()).map_err(|e| e.to_string())?;
        Ok(format!("Saved to {}. Find it in the Files app under Minis, and keep it private.", path.display()))
    }

    pub fn save_settings(&self, patch: &Json) {
        let mut s = settings();
        if let (Some(obj), Some(p)) = (s.as_object_mut(), patch.as_object()) {
            for (k, v) in p {
                if SETTINGS.contains(&k.as_str()) {
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
                     "sources.json", "settings.json", "signing-key.der", "anisette-server", "host-uuid",
                     "device-port", "silence.wav"] {
            let _ = std::fs::remove_file(state::path(name));
        }
        let _ = std::fs::remove_dir_all(state::path("ipas"));
        let _ = std::fs::remove_dir_all(state::path("icons"));
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
        i.catalog = store::cached_catalog();
    }

    /// Options › Clear cache: icons, the catalog copy and downloaded IPAs.
    /// IPAs of installed apps that came from a source are downloaded again
    /// when they are refreshed; IPAs added by hand are kept.
    pub fn clear_cache(&self) -> String {
        let keep: Vec<String> = install::installed().into_iter().filter(|i| i.source_url.is_empty())
            .map(|i| i.ipa_path).collect();
        let mut freed = dir_size(&state::path("icons"));
        let _ = std::fs::remove_dir_all(state::path("icons"));
        if let Ok(meta) = std::fs::metadata(state::path("catalog.json")) {
            freed += meta.len();
        }
        let _ = std::fs::remove_file(state::path("catalog.json"));
        if let Ok(dir) = std::fs::read_dir(state::path("ipas")) {
            for e in dir.flatten() {
                let path = e.path().to_string_lossy().into_owned();
                if !keep.contains(&path) {
                    freed += e.metadata().map(|m| m.len()).unwrap_or(0);
                    let _ = std::fs::remove_file(e.path());
                }
            }
        }
        logf!("cache: cleared {} MB", freed / 1_000_000);
        format!("Freed {} MB.", (freed + 500_000) / 1_000_000)
    }

    pub fn cache_size(&self) -> u64 {
        dir_size(&state::path("icons")) + dir_size(&state::path("ipas"))
            + std::fs::metadata(state::path("catalog.json")).map(|m| m.len()).unwrap_or(0)
    }

    /// Options › Update BariStore: the newest build, then a restart into it.
    pub async fn update(&self) -> Result<String, String> {
        let app = self.clone();
        match crate::update::download_and_replace(&move |s| app.step(s)).await? {
            crate::update::Outcome::UpToDate => Ok(format!("BariStore {} is the newest build.", env!("CARGO_PKG_VERSION"))),
            crate::update::Outcome::Updated(version) => {
                // The supervisor starts the new build at the same address.
                tokio::spawn(async {
                    tokio::time::sleep(std::time::Duration::from_millis(1500)).await;
                    std::process::exit(crate::UPDATED);
                });
                Ok(format!("Updated to {version}. BariStore restarts; this page reloads by itself."))
            }
        }
    }
}

/// Changes when the server restarts: the page reloads itself then.
fn boot_id() -> &'static str {
    static BOOT: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    BOOT.get_or_init(|| format!("{}-{}", std::process::id(), std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis()))
}

fn dir_size(dir: &std::path::Path) -> u64 {
    std::fs::read_dir(dir).map(|d| d.flatten().filter_map(|e| e.metadata().ok()).map(|m| m.len()).sum()).unwrap_or(0)
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
