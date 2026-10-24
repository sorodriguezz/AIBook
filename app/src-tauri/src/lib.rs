mod downloads;
mod hardware;
mod sidecars;

use std::sync::Arc;

use tauri::{AppHandle, Manager, RunEvent};

/// The curated model list shipped with the app.
#[tauri::command]
fn catalog(app: AppHandle) -> Result<serde_json::Value, String> {
    let bundled = app
        .path()
        .resource_dir()
        .map(|d| d.join("resources").join("models.json"))
        .ok()
        .filter(|p| p.exists());
    let path = bundled.unwrap_or_else(|| {
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../catalog/models.json")
    });
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(sidecars::Engine::default())
        .manage(Arc::new(downloads::Downloads::default()))
        .invoke_handler(tauri::generate_handler![
            catalog,
            hardware::hardware_info,
            sidecars::engine_info,
            sidecars::stop_engine,
            downloads::installed_models,
            downloads::partial_size,
            downloads::start_download,
            downloads::pause_download,
            downloads::cancel_download,
            downloads::delete_model,
        ])
        .build(tauri::generate_context!())
        .expect("error while building AIBook");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            // The engine stops llama-server on its own shutdown path.
            handle.state::<sidecars::Engine>().shutdown();
        }
    });
}
