//! Keeps the computer from sleeping while a book is being translated.

use std::sync::Mutex;

use tauri::State;

#[derive(Default)]
pub struct Awake(Mutex<Option<keepawake::KeepAwake>>);

/// Hold (or release) a "system is busy" assertion. The display may still turn off.
#[tauri::command]
pub fn set_keep_awake(state: State<'_, Awake>, on: bool) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    if on && guard.is_none() {
        let awake = keepawake::Builder::default()
            .idle(true)
            .reason("Translating a book")
            .app_name("AIBook")
            .app_reverse_domain("com.sorodriguezz.aibook")
            .create()
            .map_err(|e| e.to_string())?;
        *guard = Some(awake);
    } else if !on {
        *guard = None;
    }
    Ok(())
}
