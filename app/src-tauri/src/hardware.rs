//! Detects the memory available for running models.

use serde::Serialize;
use sysinfo::System;

#[derive(Serialize, Clone)]
pub struct Gpu {
    pub name: String,
    pub vram_bytes: u64,
}

#[derive(Serialize, Clone)]
pub struct Hardware {
    pub os: String,
    pub arch: String,
    pub cpu: String,
    pub cores: usize,
    pub ram_bytes: u64,
    pub available_ram_bytes: u64,
    /// Apple Silicon: GPU and CPU share the same memory pool.
    pub unified_memory: bool,
    pub gpus: Vec<Gpu>,
    /// Memory the model can use on the GPU (VRAM, or the GPU's share of unified memory).
    pub gpu_budget_bytes: u64,
    /// Inference backend shipped for this platform.
    pub backend: String,
}

#[tauri::command]
pub fn hardware_info() -> Hardware {
    let mut sys = System::new();
    sys.refresh_memory();
    sys.refresh_cpu_all();
    let ram = sys.total_memory();
    let cpu = sys
        .cpus()
        .first()
        .map(|c| c.brand().trim().to_string())
        .unwrap_or_default();
    let cores = System::physical_core_count().unwrap_or(sys.cpus().len());
    let arch = std::env::consts::ARCH.to_string();
    let os = std::env::consts::OS.to_string();

    let unified = cfg!(target_os = "macos");
    let gpus = detect_gpus();
    let gpu_budget = if unified {
        // macOS lets Metal wire roughly 2/3 of RAM on small machines and 3/4 on
        // larger ones (recommendedMaxWorkingSetSize).
        if ram <= 36 * GIB {
            ram * 2 / 3
        } else {
            ram * 3 / 4
        }
    } else {
        gpus.iter().map(|g| g.vram_bytes).max().unwrap_or(0)
    };
    let backend = if cfg!(target_os = "macos") {
        "Metal"
    } else if cfg!(all(windows, target_arch = "aarch64")) {
        "CPU (ARM64 NEON)"
    } else {
        "Vulkan"
    };

    Hardware {
        os,
        arch,
        cpu,
        cores,
        ram_bytes: ram,
        available_ram_bytes: sys.available_memory(),
        unified_memory: unified,
        gpus,
        gpu_budget_bytes: gpu_budget,
        backend: backend.into(),
    }
}

const GIB: u64 = 1024 * 1024 * 1024;

#[cfg(target_os = "macos")]
fn detect_gpus() -> Vec<Gpu> {
    let name = std::process::Command::new("sysctl")
        .args(["-n", "machdep.cpu.brand_string"])
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|| "Apple GPU".into());
    vec![Gpu {
        name: format!("{name} GPU"),
        vram_bytes: 0,
    }]
}

#[cfg(windows)]
fn detect_gpus() -> Vec<Gpu> {
    use windows::Win32::Graphics::Dxgi::{
        CreateDXGIFactory1, DXGI_ADAPTER_FLAG_SOFTWARE, IDXGIFactory1,
    };

    let mut out = Vec::new();
    unsafe {
        let Ok(factory) = CreateDXGIFactory1::<IDXGIFactory1>() else {
            return out;
        };
        let mut i = 0;
        while let Ok(adapter) = factory.EnumAdapters1(i) {
            i += 1;
            let Ok(desc) = adapter.GetDesc1() else {
                continue;
            };
            if (desc.Flags as i32 & DXGI_ADAPTER_FLAG_SOFTWARE.0) != 0 {
                continue;
            }
            let len = desc
                .Description
                .iter()
                .position(|&c| c == 0)
                .unwrap_or(desc.Description.len());
            let name = String::from_utf16_lossy(&desc.Description[..len]);
            out.push(Gpu {
                name,
                vram_bytes: desc.DedicatedVideoMemory as u64,
            });
        }
    }
    out
}

#[cfg(not(any(target_os = "macos", windows)))]
fn detect_gpus() -> Vec<Gpu> {
    Vec::new()
}
