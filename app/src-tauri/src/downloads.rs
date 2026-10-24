//! Resumable model downloads with live speed and time-remaining estimates.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::fs::{self, OpenOptions};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::Mutex;
use tokio_util_compat::CancelFlag;

mod tokio_util_compat {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicU8, Ordering};

    /// 0 = running, 1 = pause (keep partial file), 2 = cancel (delete it).
    #[derive(Clone, Default)]
    pub struct CancelFlag(Arc<AtomicU8>);

    impl CancelFlag {
        pub fn set(&self, v: u8) {
            self.0.store(v, Ordering::SeqCst)
        }
        pub fn get(&self) -> u8 {
            self.0.load(Ordering::SeqCst)
        }
    }
}

#[derive(Default)]
pub struct Downloads {
    active: Mutex<HashMap<String, CancelFlag>>,
}

#[derive(Deserialize)]
pub struct DownloadRequest {
    pub key: String, // "<model-id>:<quant>"
    pub url: String,
    pub file: String,
    pub size: u64,
    pub sha256: String,
}

#[derive(Serialize, Clone)]
struct Progress {
    key: String,
    state: &'static str, // downloading | verifying | done | paused | cancelled | error
    downloaded: u64,
    total: u64,
    bytes_per_sec: f64,
    eta_seconds: Option<f64>,
    elapsed_seconds: f64,
    error: Option<String>,
}

pub fn models_dir(app: &AppHandle) -> PathBuf {
    app.path()
        .app_data_dir()
        .expect("app data dir")
        .join("models")
}

#[derive(Serialize)]
pub struct InstalledModel {
    file: String,
    path: String,
    size: u64,
}

#[tauri::command]
pub async fn installed_models(app: AppHandle) -> Result<Vec<InstalledModel>, String> {
    let dir = models_dir(&app);
    let mut out = Vec::new();
    let Ok(mut rd) = fs::read_dir(&dir).await else {
        return Ok(out);
    };
    while let Ok(Some(e)) = rd.next_entry().await {
        let p = e.path();
        if p.extension().and_then(|s| s.to_str()) == Some("gguf") {
            let size = e.metadata().await.map(|m| m.len()).unwrap_or(0);
            out.push(InstalledModel {
                file: e.file_name().to_string_lossy().into(),
                path: p.to_string_lossy().into(),
                size,
            });
        }
    }
    Ok(out)
}

/// Bytes already on disk for an interrupted download.
#[tauri::command]
pub async fn partial_size(app: AppHandle, file: String) -> u64 {
    let part = models_dir(&app).join(format!("{file}.part"));
    fs::metadata(part).await.map(|m| m.len()).unwrap_or(0)
}

#[tauri::command]
pub async fn delete_model(app: AppHandle, file: String) -> Result<(), String> {
    if file.contains('/') || file.contains('\\') {
        return Err("invalid file name".into());
    }
    let dir = models_dir(&app);
    for p in [dir.join(&file), dir.join(format!("{file}.part"))] {
        if p.exists() {
            fs::remove_file(p).await.map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

#[tauri::command]
pub async fn pause_download(state: State<'_, Arc<Downloads>>, key: String) -> Result<(), String> {
    if let Some(f) = state.active.lock().await.get(&key) {
        f.set(1);
    }
    Ok(())
}

#[tauri::command]
pub async fn cancel_download(state: State<'_, Arc<Downloads>>, key: String) -> Result<(), String> {
    if let Some(f) = state.active.lock().await.get(&key) {
        f.set(2);
    }
    Ok(())
}

#[tauri::command]
pub async fn start_download(
    app: AppHandle,
    state: State<'_, Arc<Downloads>>,
    req: DownloadRequest,
) -> Result<(), String> {
    if req.file.contains('/') || req.file.contains('\\') {
        return Err("invalid file name".into());
    }
    let flag = CancelFlag::default();
    {
        let mut active = state.active.lock().await;
        if active.contains_key(&req.key) {
            return Ok(()); // already running
        }
        active.insert(req.key.clone(), flag.clone());
    }
    let downloads = state.inner().clone();
    tauri::async_runtime::spawn(async move {
        let key = req.key.clone();
        let started = Instant::now();
        let result = run_download(&app, &req, &flag, started).await;
        downloads.active.lock().await.remove(&key);
        let (state, error) = match result {
            Ok(s) => (s, None),
            Err(e) => ("error", Some(e)),
        };
        let _ = app.emit(
            "download-progress",
            Progress {
                key,
                state,
                downloaded: 0,
                total: req.size,
                bytes_per_sec: 0.0,
                eta_seconds: None,
                elapsed_seconds: started.elapsed().as_secs_f64(),
                error,
            },
        );
    });
    Ok(())
}

async fn run_download(
    app: &AppHandle,
    req: &DownloadRequest,
    flag: &CancelFlag,
    started: Instant,
) -> Result<&'static str, String> {
    let dir = models_dir(app);
    fs::create_dir_all(&dir).await.map_err(|e| e.to_string())?;
    let final_path = dir.join(&req.file);
    let part_path = dir.join(format!("{}.part", req.file));

    // Hash what we already have so the checksum covers the whole file on resume.
    let mut hasher = Sha256::new();
    let mut have: u64 = 0;
    if let Ok(mut f) = fs::File::open(&part_path).await {
        let mut buf = vec![0u8; 1 << 20];
        loop {
            let n = f.read(&mut buf).await.map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            hasher.update(&buf[..n]);
            have += n as u64;
        }
    }

    let client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(20))
        .user_agent("AIBook/0.1")
        .build()
        .map_err(|e| e.to_string())?;
    let mut request = client.get(&req.url);
    if have > 0 {
        request = request.header("Range", format!("bytes={have}-"));
    }
    let resp = request
        .send()
        .await
        .map_err(|e| format!("network error: {e}"))?;
    let status = resp.status();
    if have > 0 && status.as_u16() == 200 {
        // Server ignored the range: start over.
        have = 0;
        hasher = Sha256::new();
    } else if !status.is_success() {
        return Err(format!("server returned {status}"));
    }
    let total = if req.size > 0 {
        req.size
    } else {
        have + resp.content_length().unwrap_or(0)
    };

    let mut file = OpenOptions::new()
        .create(true)
        .write(true)
        .append(have > 0)
        .truncate(have == 0)
        .open(&part_path)
        .await
        .map_err(|e| e.to_string())?;

    let mut stream = resp.bytes_stream();
    let mut downloaded = have;
    // Exponential moving average of throughput over ~1 s windows: stable but
    // quick to react when the connection speeds up or slows down.
    let mut ema: Option<f64> = None;
    let mut window_start = Instant::now();
    let mut window_bytes: u64 = 0;
    let mut last_emit = Instant::now() - Duration::from_secs(1);

    while let Some(chunk) = stream.next().await {
        match flag.get() {
            1 => {
                file.flush().await.ok();
                return Ok("paused");
            }
            2 => {
                drop(file);
                fs::remove_file(&part_path).await.ok();
                return Ok("cancelled");
            }
            _ => {}
        }
        let chunk = chunk.map_err(|e| format!("connection lost: {e}"))?;
        file.write_all(&chunk).await.map_err(|e| e.to_string())?;
        hasher.update(&chunk);
        downloaded += chunk.len() as u64;
        window_bytes += chunk.len() as u64;

        let win = window_start.elapsed().as_secs_f64();
        if win >= 1.0 {
            let inst = window_bytes as f64 / win;
            ema = Some(match ema {
                None => inst,
                Some(prev) => 0.8 * prev + 0.2 * inst,
            });
            window_start = Instant::now();
            window_bytes = 0;
        }
        if last_emit.elapsed() >= Duration::from_millis(250) {
            last_emit = Instant::now();
            let speed = ema.unwrap_or(0.0);
            let eta = (speed > 0.0).then(|| (total.saturating_sub(downloaded)) as f64 / speed);
            let _ = app.emit(
                "download-progress",
                Progress {
                    key: req.key.clone(),
                    state: "downloading",
                    downloaded,
                    total,
                    bytes_per_sec: speed,
                    eta_seconds: eta,
                    elapsed_seconds: started.elapsed().as_secs_f64(),
                    error: None,
                },
            );
        }
    }
    file.flush().await.map_err(|e| e.to_string())?;
    drop(file);

    let _ = app.emit(
        "download-progress",
        Progress {
            key: req.key.clone(),
            state: "verifying",
            downloaded,
            total,
            bytes_per_sec: 0.0,
            eta_seconds: Some(0.0),
            elapsed_seconds: started.elapsed().as_secs_f64(),
            error: None,
        },
    );
    let digest = hex::encode(hasher.finalize());
    if !req.sha256.is_empty() && digest != req.sha256.to_lowercase() {
        fs::remove_file(&part_path).await.ok();
        return Err("the downloaded file is corrupted (checksum mismatch); please retry".into());
    }
    fs::rename(&part_path, &final_path)
        .await
        .map_err(|e| e.to_string())?;
    Ok("done")
}
