//! System Resource Collection
//!
//! Provides utilities for collecting system resource information (CPU, RAM, GPU, storage)
//! to be shared with the network for task distribution and capacity planning.

use std::process::Command;

use anyhow::Result;
use tokio::task;
use tracing::debug;

use crate::wire::NodeResources;

/// Collect current system resources
///
/// Gathers information about CPU cores, RAM, storage, and GPU availability.
/// This operation may take 100-500ms due to system probing.
pub async fn collect_resources() -> Result<NodeResources> {
    task::spawn_blocking(|| {
        let (cpu_cores, ram_total_mb, ram_available_mb) = get_system_info();
        let storage_gb = get_available_storage();
        let (gpu_available, gpu_model) = detect_gpu();
        let bandwidth_mbps = estimate_bandwidth();

        debug!(
            "Collected resources: CPU={} cores, RAM={}MB/{}MB, Storage={}GB, GPU={}",
            cpu_cores,
            ram_available_mb,
            ram_total_mb,
            storage_gb,
            if gpu_available { "Yes" } else { "No" }
        );

        Ok(NodeResources {
            cpu_cores,
            ram_mb: ram_total_mb,
            storage_gb,
            gpu_available,
            gpu_model,
            hashrate: None, // Will be populated by mining module if enabled
            bandwidth_mbps,
        })
    })
    .await?
}

/// Get CPU and RAM information
fn get_system_info() -> (u32, u64, u64) {
    use sysinfo::System;

    let mut sys = System::new_all();
    sys.refresh_all();

    let cpu_cores = num_cpus::get() as u32;
    let ram_total_mb = sys.total_memory() / 1_048_576; // bytes to MB
    let ram_available_mb = sys.available_memory() / 1_048_576;

    (cpu_cores, ram_total_mb, ram_available_mb)
}

/// Get available storage across all disks
fn get_available_storage() -> u64 {
    use sysinfo::Disks;

    let disks = Disks::new_with_refreshed_list();
    let total_available = disks.iter().map(|disk| disk.available_space()).sum::<u64>();

    total_available / 1_073_741_824 // bytes to GB
}

/// Detect GPU presence and model
///
/// Attempts to detect NVIDIA or AMD GPUs using filesystem checks and
/// command-line tools. Returns (is_available, model_name).
fn detect_gpu() -> (bool, Option<String>) {
    // Try NVIDIA first
    if let Ok(output) = Command::new("nvidia-smi")
        .args(["--query-gpu=name", "--format=csv,noheader"])
        .output()
    {
        if output.status.success() {
            if let Ok(name) = String::from_utf8(output.stdout) {
                let name = name.trim().to_string();
                if !name.is_empty() {
                    debug!("Detected NVIDIA GPU: {}", name);
                    return (true, Some(name));
                }
            }
        }
    }

    // Try AMD ROCm
    if let Ok(output) = Command::new("rocm-smi")
        .args(["--showproductname"])
        .output()
    {
        if output.status.success() {
            if let Ok(text) = String::from_utf8(output.stdout) {
                // Parse ROCm output for GPU name
                for line in text.lines() {
                    if line.contains("Card series:") || line.contains("Card model:") {
                        if let Some(name) = line.split(':').nth(1) {
                            let name = name.trim().to_string();
                            debug!("Detected AMD GPU: {}", name);
                            return (true, Some(name));
                        }
                    }
                }
            }
        }
    }

    // Fallback: check for device files (Linux)
    #[cfg(target_os = "linux")]
    {
        if std::path::Path::new("/dev/nvidia0").exists() {
            debug!("Detected NVIDIA GPU (via /dev/nvidia0, model unknown)");
            return (true, Some("NVIDIA GPU (model unknown)".to_string()));
        }

        if std::path::Path::new("/dev/dri/card0").exists() {
            debug!("Detected GPU (via /dev/dri/card0, model unknown)");
            return (true, Some("GPU (model unknown)".to_string()));
        }
    }

    // Fallback: check on macOS
    #[cfg(target_os = "macos")]
    {
        if let Ok(output) = Command::new("system_profiler")
            .args(["SPDisplaysDataType"])
            .output()
        {
            if output.status.success() {
                if let Ok(text) = String::from_utf8(output.stdout) {
                    if text.contains("AMD") || text.contains("NVIDIA") || text.contains("Intel") {
                        // Extract GPU name from system_profiler output
                        for line in text.lines() {
                            if line.trim().starts_with("Chipset Model:") {
                                if let Some(name) = line.split(':').nth(1) {
                                    let name = name.trim().to_string();
                                    debug!("Detected GPU on macOS: {}", name);
                                    return (true, Some(name));
                                }
                            }
                        }
                        return (true, Some("GPU (macOS)".to_string()));
                    }
                }
            }
        }
    }

    debug!("No GPU detected");
    (false, None)
}

/// Estimate network bandwidth
///
/// Currently returns None. Future implementation could:
/// - Measure actual upload/download speeds
/// - Query network interface capabilities
/// - Use historical measurements
fn estimate_bandwidth() -> Option<u32> {
    // TODO: Implement bandwidth measurement
    // For now, return None to indicate unknown bandwidth
    None
}

/// Get system hostname
///
/// Returns the system's hostname or None if it cannot be determined.
/// Can be overridden with APN_HOSTNAME environment variable.
pub fn get_hostname() -> Option<String> {
    // Check for override first
    if let Ok(custom_hostname) = std::env::var("APN_HOSTNAME") {
        return Some(custom_hostname);
    }

    // Fall back to system hostname
    hostname::get().ok().and_then(|h| h.into_string().ok())
}

/// Collect lightweight resource snapshot for heartbeat
///
/// Similar to collect_resources() but may skip expensive operations
/// or use cached values for static information.
pub async fn collect_heartbeat_resources() -> Result<NodeResources> {
    // For now, just use the full collection
    // In the future, we could cache static values (CPU cores, GPU model)
    // and only refresh dynamic values (RAM, storage)
    collect_resources().await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_collect_resources() {
        let resources = collect_resources().await.unwrap();

        // Basic sanity checks
        assert!(resources.cpu_cores > 0, "Should detect at least 1 CPU core");
        assert!(resources.ram_mb > 0, "Should detect some RAM");
        assert!(resources.storage_gb > 0, "Should detect some storage");

        println!("Collected resources: {:?}", resources);
    }

    #[test]
    fn test_system_info() {
        let (cpu_cores, ram_total, ram_available) = get_system_info();

        assert!(cpu_cores > 0);
        assert!(ram_total > 0);
        assert!(ram_available <= ram_total);

        println!(
            "System: {} cores, {}MB RAM total, {}MB available",
            cpu_cores, ram_total, ram_available
        );
    }

    #[test]
    fn test_storage() {
        let storage_gb = get_available_storage();
        assert!(storage_gb > 0, "Should detect some available storage");
        println!("Available storage: {}GB", storage_gb);
    }

    #[test]
    fn test_gpu_detection() {
        let (gpu_available, gpu_model) = detect_gpu();
        println!("GPU: available={}, model={:?}", gpu_available, gpu_model);
    }
}
