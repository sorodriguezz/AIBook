//! Launches the Python translation engine and tells it where llama-server lives.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Manager, State};

#[derive(Default)]
pub struct Engine {
    inner: Mutex<Option<Running>>,
}

struct Running {
    child: Child,
    port: u16,
    token: String,
}

#[derive(Serialize, Clone)]
pub struct EngineInfo {
    pub url: String,
    pub token: String,
}

impl Engine {
    /// Ask the engine to stop llama-server and exit; kill it only if it doesn't.
    /// A hard kill would orphan llama-server (and on Windows keep files locked
    /// while an update installs).
    pub fn shutdown(&self) {
        let Some(mut r) = self.inner.lock().unwrap().take() else {
            return;
        };
        request_shutdown(r.port, &r.token);
        for _ in 0..50 {
            if matches!(r.child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let _ = r.child.kill();
        let _ = r.child.wait();
    }
}

/// Minimal blocking HTTP request to the engine, outside any async runtime.
/// Returns the status line, e.g. "HTTP/1.1 200 OK".
fn raw_request(port: u16, token: &str, method: &str, path: &str, timeout: Duration) -> Option<String> {
    use std::io::{Read, Write};
    use std::net::{SocketAddr, TcpStream};

    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_secs(1)).ok()?;
    let _ = s.set_read_timeout(Some(timeout));
    let req = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nx-folio-token: {token}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    s.write_all(req.as_bytes()).ok()?;
    let mut buf = [0u8; 256];
    let n = s.read(&mut buf).ok()?;
    let head = String::from_utf8_lossy(&buf[..n]);
    head.lines().next().map(str::to_owned)
}

fn request_shutdown(port: u16, token: &str) {
    let _ = raw_request(port, token, "POST", "/shutdown", Duration::from_secs(5));
}

/// The engine's log (its stderr), rewritten on every start.
fn log_path(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_log_dir().ok().map(|d| d.join("engine.log"))
}

fn log_tail(app: &AppHandle) -> String {
    let text = log_path(app).and_then(|p| std::fs::read_to_string(p).ok()).unwrap_or_default();
    let lines: Vec<&str> = text.lines().collect();
    lines[lines.len().saturating_sub(8)..].join("\n")
}

/// Where the engine log lives, so the UI can reveal it when something fails.
#[tauri::command]
pub fn engine_log(app: AppHandle) -> Option<String> {
    log_path(&app).map(|p| p.to_string_lossy().into_owned())
}

/// Stops the engine (and its model server) so an update can replace their files.
#[tauri::command]
pub async fn stop_engine(state: State<'_, Engine>) -> Result<(), String> {
    state.shutdown();
    Ok(())
}

fn llama_server_path(app: &AppHandle) -> Option<PathBuf> {
    let exe = if cfg!(windows) {
        "llama-server.exe"
    } else {
        "llama-server"
    };
    let bundled = app
        .path()
        .resource_dir()
        .ok()?
        .join("resources")
        .join("llama")
        .join(exe);
    if bundled.exists() {
        return Some(bundled);
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("resources")
        .join("llama")
        .join(exe);
    dev.exists().then_some(dev)
}

/// The engine command: the PyInstaller bundle in release builds, `uv run` in development.
fn engine_command(app: &AppHandle) -> Command {
    let exe = if cfg!(windows) {
        "folio-engine.exe"
    } else {
        "folio-engine"
    };
    if let Ok(res) = app.path().resource_dir() {
        let bundled = res.join("resources").join("engine").join(exe);
        if bundled.exists() {
            return Command::new(bundled);
        }
    }
    let engine_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../engine");
    let mut cmd = Command::new("uv");
    cmd.current_dir(engine_dir)
        .args(["run", "--extra", "pdf", "python", "-m", "folio_engine"]);
    cmd
}

fn spawn(app: &AppHandle) -> Result<Running, String> {
    let token = uuid::Uuid::new_v4().simple().to_string();
    let data_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&data_dir).map_err(|e| e.to_string())?;
    let mut cmd = engine_command(app);
    cmd.arg("--port")
        .arg("0")
        .env("FOLIO_TOKEN", &token)
        .env("FOLIO_DATA_DIR", &data_dir)
        .env("FOLIO_PARENT_PID", std::process::id().to_string())
        .stdout(Stdio::piped());
    // A GUI app on Windows has no console, so inheriting stderr would lose
    // every engine error; keep it in a file instead.
    let log = log_path(app).and_then(|p| {
        std::fs::create_dir_all(p.parent()?).ok()?;
        std::fs::File::create(p).ok()
    });
    cmd.stderr(log.map(Stdio::from).unwrap_or_else(Stdio::null));
    if let Some(llama) = llama_server_path(app) {
        cmd.env("FOLIO_LLAMA_SERVER", llama);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("could not start the engine: {e}"))?;
    let stdout = child.stdout.take().ok_or("no stdout")?;
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(p) = line.strip_prefix("FOLIO_ENGINE_PORT=") {
                let _ = tx.send(p.trim().parse::<u16>().ok());
            }
        }
    });
    // First start in development may need `uv` to sync dependencies.
    let port = match rx.recv_timeout(Duration::from_secs(180)) {
        Ok(Some(port)) => port,
        _ => {
            let _ = child.kill();
            return Err(format!("engine_start_failed\n{}", log_tail(app)));
        }
    };
    // The port is announced before the server finishes starting up; only hand
    // it to the UI once it really answers.
    for _ in 0..120 {
        if let Ok(Some(_)) = child.try_wait() {
            return Err(format!("engine_start_failed\n{}", log_tail(app)));
        }
        if raw_request(port, &token, "GET", "/health", Duration::from_secs(2))
            .is_some_and(|l| l.contains(" 200"))
        {
            return Ok(Running { child, port, token });
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    let _ = child.kill();
    Err(format!("engine_start_failed\n{}", log_tail(app)))
}

#[tauri::command]
pub async fn engine_info(app: AppHandle, state: State<'_, Engine>) -> Result<EngineInfo, String> {
    {
        let mut guard = state.inner.lock().unwrap();
        if let Some(r) = guard.as_mut() {
            if r.child.try_wait().map(|s| s.is_none()).unwrap_or(false) {
                return Ok(EngineInfo {
                    url: format!("http://127.0.0.1:{}", r.port),
                    token: r.token.clone(),
                });
            }
            *guard = None; // crashed: restart below
        }
    }
    let app2 = app.clone();
    let running = tauri::async_runtime::spawn_blocking(move || spawn(&app2))
        .await
        .map_err(|e| e.to_string())??;
    let info = EngineInfo {
        url: format!("http://127.0.0.1:{}", running.port),
        token: running.token.clone(),
    };
    *state.inner.lock().unwrap() = Some(running);
    Ok(info)
}
