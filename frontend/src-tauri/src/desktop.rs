use std::{
    fs::{self, File, OpenOptions},
    io,
    net::TcpListener,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    time::Duration,
};

use anyhow::{anyhow, Context};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use tauri::{path::BaseDirectory, AppHandle, Manager, State};
use zip::ZipArchive;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum BootstrapState {
    NotInstalled,
    Installing,
    Ready,
    Broken,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendHealth {
    healthy: bool,
    detail: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopStatus {
    is_desktop_shell: bool,
    bootstrap_state: BootstrapState,
    runtime_version: String,
    backend_url: String,
    logs_path: String,
    backend_health: BackendHealth,
    status_message: String,
    dev_mode: bool,
    last_error: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeManifest {
    runtime_version: String,
    windows: WindowsRuntime,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WindowsRuntime {
    archive: String,
    python_path: String,
}

#[derive(Debug)]
struct DesktopState {
    status: DesktopStatus,
    startup_inflight: bool,
}

#[derive(Clone)]
pub struct DesktopManager {
    state: Arc<Mutex<DesktopState>>,
    backend_child: Arc<Mutex<Option<Child>>>,
}

impl Default for DesktopManager {
    fn default() -> Self {
        Self {
            state: Arc::new(Mutex::new(DesktopState {
                status: DesktopStatus {
                    is_desktop_shell: true,
                    bootstrap_state: BootstrapState::NotInstalled,
                    runtime_version: "unresolved".into(),
                    backend_url: String::new(),
                    logs_path: String::new(),
                    backend_health: BackendHealth {
                        healthy: false,
                        detail: "Backend has not started yet.".into(),
                    },
                    status_message: "Preparing desktop shell bootstrap.".into(),
                    dev_mode: cfg!(debug_assertions),
                    last_error: None,
                },
                startup_inflight: false,
            })),
            backend_child: Arc::new(Mutex::new(None)),
        }
    }
}

impl DesktopManager {
    fn snapshot(&self) -> DesktopStatus {
        self.state
            .lock()
            .expect("desktop state poisoned")
            .status
            .clone()
    }

    fn begin_startup(&self) -> bool {
        let mut guard = self.state.lock().expect("desktop state poisoned");
        if guard.startup_inflight {
            return false;
        }
        guard.startup_inflight = true;
        true
    }

    fn finish_startup(&self) {
        let mut guard = self.state.lock().expect("desktop state poisoned");
        guard.startup_inflight = false;
    }

    fn update_status<F>(&self, updater: F)
    where
        F: FnOnce(&mut DesktopStatus),
    {
        let mut guard = self.state.lock().expect("desktop state poisoned");
        updater(&mut guard.status);
    }

    fn set_installing(
        &self,
        message: impl Into<String>,
        logs_path: &Path,
        runtime_version: impl Into<String>,
    ) {
        let runtime_version = runtime_version.into();
        let logs = logs_path.to_string_lossy().to_string();
        self.update_status(|status| {
            status.bootstrap_state = BootstrapState::Installing;
            status.runtime_version = runtime_version;
            status.logs_path = logs;
            status.status_message = message.into();
            status.backend_health = BackendHealth {
                healthy: false,
                detail: "Backend is starting.".into(),
            };
            status.last_error = None;
        });
    }

    fn set_ready(
        &self,
        backend_url: String,
        logs_path: &Path,
        runtime_version: String,
        detail: String,
    ) {
        let logs = logs_path.to_string_lossy().to_string();
        self.update_status(|status| {
            status.bootstrap_state = BootstrapState::Ready;
            status.backend_url = backend_url;
            status.runtime_version = runtime_version;
            status.logs_path = logs;
            status.backend_health = BackendHealth {
                healthy: true,
                detail,
            };
            status.status_message = "Desktop shell is ready. Backend health checks passed.".into();
            status.last_error = None;
        });
    }

    fn set_broken(
        &self,
        message: impl Into<String>,
        logs_path: &Path,
        runtime_version: impl Into<String>,
        error: Option<String>,
    ) {
        let runtime_version = runtime_version.into();
        let logs = logs_path.to_string_lossy().to_string();
        self.update_status(|status| {
            status.bootstrap_state = BootstrapState::Broken;
            status.runtime_version = runtime_version;
            status.logs_path = logs;
            status.status_message = message.into();
            status.backend_health = BackendHealth {
                healthy: false,
                detail: "Backend is unavailable.".into(),
            };
            status.last_error = error;
        });
    }

    fn store_child(&self, child: Child) {
        let mut guard = self.backend_child.lock().expect("backend child poisoned");
        *guard = Some(child);
    }

    pub fn shutdown_backend_process(&self) {
        let mut guard = self.backend_child.lock().expect("backend child poisoned");
        if let Some(child) = guard.as_mut() {
            let _ = child.kill();
        }
        *guard = None;
    }

    async fn refresh_health(&self) -> DesktopStatus {
        let snapshot = self.snapshot();
        if !matches!(snapshot.bootstrap_state, BootstrapState::Ready)
            || snapshot.backend_url.is_empty()
        {
            return snapshot;
        }

        let health_url = format!("{}/health", snapshot.backend_url);
        let result = Client::new()
            .get(&health_url)
            .timeout(Duration::from_millis(800))
            .send()
            .await;

        match result {
            Ok(response) if response.status().is_success() => {
                self.update_status(|status| {
                    status.backend_health = BackendHealth {
                        healthy: true,
                        detail: "Backend service is healthy.".into(),
                    };
                });
            }
            Ok(response) => {
                self.update_status(|status| {
                    status.bootstrap_state = BootstrapState::Broken;
                    status.backend_health = BackendHealth {
                        healthy: false,
                        detail: format!("Backend health returned {}", response.status()),
                    };
                    status.status_message =
                        "Backend stopped responding. Retry setup to restart the service.".into();
                    status.last_error =
                        Some(format!("Health endpoint returned {}", response.status()));
                });
            }
            Err(error) => {
                self.update_status(|status| {
                    status.bootstrap_state = BootstrapState::Broken;
                    status.backend_health = BackendHealth {
                        healthy: false,
                        detail: "Backend health probe could not connect.".into(),
                    };
                    status.status_message =
                        "Backend is unreachable. Retry setup to launch a fresh service.".into();
                    status.last_error = Some(error.to_string());
                });
            }
        }

        self.snapshot()
    }
}

#[tauri::command]
pub async fn desktop_status(manager: State<'_, DesktopManager>) -> Result<DesktopStatus, String> {
    Ok(manager.refresh_health().await)
}

#[tauri::command]
pub fn initialize_desktop(
    app: AppHandle,
    manager: State<'_, DesktopManager>,
) -> Result<DesktopStatus, String> {
    let status = manager.snapshot();
    if matches!(
        status.bootstrap_state,
        BootstrapState::Broken | BootstrapState::NotInstalled
    ) {
        spawn_bootstrap(app, manager.inner().clone());
    }
    Ok(status)
}

pub fn spawn_bootstrap(app: AppHandle, manager: DesktopManager) {
    if !manager.begin_startup() {
        return;
    }

    tauri::async_runtime::spawn(async move {
        let result = bootstrap_and_launch(app.clone(), manager.clone()).await;
        if let Err(error) = result {
            let fallback_dir = app
                .path()
                .app_local_data_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join("logs");
            let logs_path = fallback_dir.join("desktop.log");
            let _ = fs::create_dir_all(&fallback_dir);
            let _ = append_log(&logs_path, &format!("bootstrap failed: {error:#}\n"));
            manager.set_broken(
                "Desktop bootstrap failed before the backend could become healthy.",
                &logs_path,
                "broken",
                Some(error.to_string()),
            );
        }
        manager.finish_startup();
    });
}

async fn bootstrap_and_launch(app: AppHandle, manager: DesktopManager) -> anyhow::Result<()> {
    let app_local_data_dir = app
        .path()
        .app_local_data_dir()
        .context("could not resolve app local data directory")?;
    let logs_dir = app_local_data_dir.join("logs");
    fs::create_dir_all(&logs_dir).context("could not create desktop log directory")?;
    let logs_path = logs_dir.join("desktop.log");

    let runtime = if cfg!(debug_assertions) {
        resolve_dev_runtime(&logs_path)?
    } else {
        resolve_packaged_runtime(&app, &logs_path, &app_local_data_dir)?
    };

    manager.set_installing(
        "Runtime is ready. Starting the local backend service.",
        &logs_path,
        runtime.runtime_version.clone(),
    );
    manager.shutdown_backend_process();

    let backend_port = find_open_port().context("could not allocate a local backend port")?;
    let backend_url = format!("http://127.0.0.1:{backend_port}");
    let service_root = app_local_data_dir.join("service-data");
    fs::create_dir_all(&service_root).context("could not create service data directory")?;

    let child = spawn_backend_process(&runtime, &backend_url, &service_root, &logs_path)
        .context("failed to launch backend process")?;
    manager.store_child(child);

    wait_for_backend(&backend_url, &logs_path)
        .await
        .with_context(|| format!("backend did not become healthy at {backend_url}"))?;
    manager.set_ready(
        backend_url,
        &logs_path,
        runtime.runtime_version,
        "Backend service is responding on localhost.".into(),
    );
    Ok(())
}

struct RuntimeResolution {
    python_path: PathBuf,
    backend_root: PathBuf,
    runtime_version: String,
}

fn resolve_dev_runtime(logs_path: &Path) -> anyhow::Result<RuntimeResolution> {
    let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .context("could not resolve repo root from src-tauri")?;
    let backend_root = std::env::var("AI_CAD_DEV_BACKEND_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| repo_root.join("backend"));
    let candidate_paths = [
        std::env::var("AI_CAD_DEV_PYTHON").ok().map(PathBuf::from),
        Some(repo_root.join(".venv-test").join("bin").join("python")),
        Some(repo_root.join(".venv").join("bin").join("python")),
        Some(PathBuf::from("python3")),
        Some(PathBuf::from("python")),
    ];

    let python_path = candidate_paths
        .into_iter()
        .flatten()
        .find(|candidate| {
            if candidate.is_absolute() || candidate.components().count() > 1 {
                candidate.exists()
            } else {
                true
            }
        })
        .ok_or_else(|| anyhow!("no developer Python interpreter was found"))?;

    append_log(
        logs_path,
        &format!(
            "dev runtime selected: python={} backend_root={}\n",
            python_path.display(),
            backend_root.display()
        ),
    )?;

    Ok(RuntimeResolution {
        python_path,
        backend_root,
        runtime_version: "dev-shell".into(),
    })
}

fn resolve_packaged_runtime(
    app: &AppHandle,
    logs_path: &Path,
    app_local_data_dir: &Path,
) -> anyhow::Result<RuntimeResolution> {
    let manifest_path = app
        .path()
        .resolve("runtime/runtime-manifest.json", BaseDirectory::Resource)
        .context("could not resolve bundled runtime manifest")?;
    let manifest: RuntimeManifest = serde_json::from_str(
        &fs::read_to_string(&manifest_path).context("could not read bundled runtime manifest")?,
    )
    .context("runtime manifest was not valid JSON")?;

    let runtime_root = app_local_data_dir
        .join("runtime")
        .join(&manifest.runtime_version);
    let python_path = runtime_root.join(&manifest.windows.python_path);
    if !python_path.exists() {
        fs::create_dir_all(&runtime_root)
            .context("could not create runtime extraction directory")?;
        append_log(
            logs_path,
            &format!(
                "extracting packaged runtime {} into {}\n",
                manifest.runtime_version,
                runtime_root.display()
            ),
        )?;
        let archive_path = app
            .path()
            .resolve(&manifest.windows.archive, BaseDirectory::Resource)
            .context("could not resolve bundled Windows runtime archive")?;
        if !archive_path.exists() {
            return Err(anyhow!(
                "bundled runtime archive is missing at {}. Build the Windows runtime bundle before packaging the installer.",
                archive_path.display()
            ));
        }
        extract_zip_archive(&archive_path, &runtime_root)?;
    }

    let backend_root = app
        .path()
        .resolve("backend", BaseDirectory::Resource)
        .context("could not resolve bundled backend resources")?;
    append_log(
        logs_path,
        &format!(
            "packaged runtime selected: python={} backend_root={}\n",
            python_path.display(),
            backend_root.display()
        ),
    )?;
    Ok(RuntimeResolution {
        python_path,
        backend_root,
        runtime_version: manifest.runtime_version,
    })
}

fn extract_zip_archive(archive_path: &Path, destination: &Path) -> anyhow::Result<()> {
    let archive_file = File::open(archive_path)
        .with_context(|| format!("could not open runtime archive {}", archive_path.display()))?;
    let mut archive = ZipArchive::new(archive_file).context("could not read zip archive")?;

    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .context("could not read archive entry")?;
        let Some(safe_path) = entry.enclosed_name().map(|path| destination.join(path)) else {
            continue;
        };
        if entry.is_dir() {
            fs::create_dir_all(&safe_path).context("could not create extracted directory")?;
            continue;
        }
        if let Some(parent) = safe_path.parent() {
            fs::create_dir_all(parent).context("could not create extracted parent directory")?;
        }
        let mut output = File::create(&safe_path)
            .with_context(|| format!("could not create extracted file {}", safe_path.display()))?;
        io::copy(&mut entry, &mut output).context("could not write extracted file")?;
    }
    Ok(())
}

fn spawn_backend_process(
    runtime: &RuntimeResolution,
    backend_url: &str,
    service_root: &Path,
    logs_path: &Path,
) -> anyhow::Result<Child> {
    let port = backend_url
        .rsplit(':')
        .next()
        .ok_or_else(|| anyhow!("backend URL did not contain a port"))?;
    let log_file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(logs_path)
        .with_context(|| format!("could not open log file {}", logs_path.display()))?;
    let stderr_log = log_file
        .try_clone()
        .context("could not clone log file handle")?;

    append_log(
        logs_path,
        &format!(
            "starting backend: python={} root={} port={port}\n",
            runtime.python_path.display(),
            runtime.backend_root.display(),
        ),
    )?;

    let mut command = Command::new(&runtime.python_path);
    command
        .arg("-m")
        .arg("uvicorn")
        .arg("app.main:app")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port)
        .current_dir(&runtime.backend_root)
        .stdout(Stdio::from(log_file))
        .stderr(Stdio::from(stderr_log))
        .env("PYTHONPATH", &runtime.backend_root)
        .env("AI_CAD_RUNTIME_ROOT", service_root)
        .env("AI_CAD_FRONTEND_ORIGIN", "http://tauri.localhost")
        .env("AI_CAD_APP_ENV", "desktop");

    command.spawn().context("could not spawn uvicorn process")
}

async fn wait_for_backend(backend_url: &str, logs_path: &Path) -> anyhow::Result<()> {
    let health_url = format!("{backend_url}/health");
    let client = Client::new();
    for attempt in 0..30 {
        match client
            .get(&health_url)
            .timeout(Duration::from_millis(700))
            .send()
            .await
        {
            Ok(response) if response.status().is_success() => {
                append_log(
                    logs_path,
                    &format!("backend ready after attempt {}\n", attempt + 1),
                )?;
                return Ok(());
            }
            Ok(response) => {
                append_log(
                    logs_path,
                    &format!(
                        "backend health attempt {} returned {}\n",
                        attempt + 1,
                        response.status()
                    ),
                )?;
            }
            Err(error) => {
                append_log(
                    logs_path,
                    &format!("backend health attempt {} failed: {}\n", attempt + 1, error),
                )?;
            }
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    Err(anyhow!("backend health check timed out"))
}

fn find_open_port() -> anyhow::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0").context("could not bind local port")?;
    let port = listener
        .local_addr()
        .context("could not inspect temporary listener")?
        .port();
    drop(listener);
    Ok(port)
}

fn append_log(path: &Path, message: &str) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("could not create log directory {}", parent.display()))?;
    }
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .with_context(|| format!("could not open log file {}", path.display()))?;
    use std::io::Write as _;
    file.write_all(message.as_bytes())
        .with_context(|| format!("could not write log file {}", path.display()))?;
    Ok(())
}
