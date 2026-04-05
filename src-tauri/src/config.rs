use crate::models::{
    current_service_mode, CompanionSettings, DaemonRuntimeConfig, DaemonStatus, MachineInfo,
};
use crate::secrets;
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
};
use tauri::{Manager, Runtime};
use uuid::Uuid;

pub struct AppPaths {
    pub root: PathBuf,
    pub config_path: PathBuf,
    pub daemon_root: PathBuf,
    pub daemon_bin_dir: PathBuf,
    pub daemon_binary_path: PathBuf,
    pub daemon_script_path: PathBuf,
    pub daemon_config_path: PathBuf,
    pub state_path: PathBuf,
    pub keys_dir: PathBuf,
    pub private_key_path: PathBuf,
    pub public_key_path: PathBuf,
    pub status_dir: PathBuf,
    pub status_path: PathBuf,
    pub logs_dir: PathBuf,
    pub log_path: PathBuf,
    pub service_definition_path: PathBuf,
}

impl AppPaths {
    pub fn new<R: Runtime, M: Manager<R>>(app: &M) -> Result<Self, String> {
        let root = app
            .path()
            .app_data_dir()
            .map_err(|error| error.to_string())?;
        let daemon_root = root.join("daemon");
        let daemon_bin_dir = daemon_root.join("bin");
        let keys_dir = root.join("keys");
        let status_dir = root.join("status");
        let logs_dir = root.join("logs");

        Ok(Self {
            root: root.clone(),
            config_path: root.join("client-config.json"),
            daemon_root: daemon_root.clone(),
            daemon_bin_dir: daemon_bin_dir.clone(),
            daemon_binary_path: daemon_bin_dir.join(if cfg!(target_os = "windows") {
                "hermes-node-daemon.exe"
            } else {
                "hermes-node-daemon"
            }),
            daemon_script_path: daemon_bin_dir.join("hermes-node-daemon.py"),
            daemon_config_path: daemon_root.join("daemon-config.json"),
            state_path: daemon_root.join("daemon-state.json"),
            keys_dir: keys_dir.clone(),
            private_key_path: keys_dir.join("hermes_node_ed25519"),
            public_key_path: keys_dir.join("hermes_node_ed25519.pub"),
            status_dir: status_dir.clone(),
            status_path: status_dir.join("node-status.json"),
            logs_dir: logs_dir.clone(),
            log_path: logs_dir.join("hermes-node-daemon.log"),
            service_definition_path: daemon_root.join(if cfg!(target_os = "macos") {
                "com.hermes.node.daemon.plist"
            } else if cfg!(target_os = "windows") {
                "start-windows-daemon.vbs"
            } else {
                "hermes-node-daemon.service"
            }),
        })
    }
}

pub fn bootstrap<R: Runtime, M: Manager<R>>(app: &M) -> Result<(), String> {
    let paths = AppPaths::new(app)?;
    ensure_runtime_layout(&paths)?;

    if !paths.status_path.exists() {
        let status = default_status(&paths);
        save_daemon_status(app, &status)?;
    }

    if !paths.config_path.exists() {
        let settings = normalize_settings(CompanionSettings::default(), &detect_machine_info());
        save_settings(app, settings)?;
    }

    Ok(())
}

pub fn detect_machine_info() -> MachineInfo {
    let hostname = hostname::get()
        .ok()
        .and_then(|value| value.into_string().ok())
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "unknown-node".into());

    let current_user = std::env::var("USER")
        .or_else(|_| std::env::var("USERNAME"))
        .unwrap_or_else(|_| "unknown".into());

    MachineInfo {
        hostname,
        os_type: std::env::consts::OS.into(),
        arch: std::env::consts::ARCH.into(),
        current_user,
    }
}

pub fn load_settings<R: Runtime, M: Manager<R>>(app: &M) -> Result<CompanionSettings, String> {
    let paths = AppPaths::new(app)?;
    ensure_runtime_layout(&paths)?;

    if !paths.config_path.exists() {
        return save_settings(app, CompanionSettings::default());
    }

    let file = fs::File::open(&paths.config_path).map_err(|error| error.to_string())?;
    let mut settings: CompanionSettings =
        serde_json::from_reader(file).map_err(|error| error.to_string())?;
    settings.api_token = secrets::load_api_token(&settings.client_id, &settings.api_token);

    Ok(normalize_settings(settings, &detect_machine_info()))
}

pub fn save_settings<R: Runtime, M: Manager<R>>(
    app: &M,
    settings: CompanionSettings,
) -> Result<CompanionSettings, String> {
    let paths = AppPaths::new(app)?;
    ensure_runtime_layout(&paths)?;

    let normalized = normalize_settings(settings, &detect_machine_info());
    let token_persistence =
        secrets::persist_api_token(&normalized.client_id, &normalized.api_token);

    let mut persisted = normalized.clone();
    persisted.api_token = token_persistence.config_token.clone();

    write_json(&paths.config_path, &persisted)?;
    write_daemon_runtime_config(&paths, &normalized, &token_persistence)?;

    let mut status = load_daemon_status(app)?;
    status.public_key_path = Some(paths.public_key_path.display().to_string());
    status.service_mode = current_service_mode();
    save_daemon_status(app, &status)?;

    Ok(normalized)
}

pub fn load_daemon_status<R: Runtime, M: Manager<R>>(app: &M) -> Result<DaemonStatus, String> {
    let paths = AppPaths::new(app)?;
    ensure_runtime_layout(&paths)?;

    if !paths.status_path.exists() {
        let status = default_status(&paths);
        write_json(&paths.status_path, &status)?;
        return Ok(status);
    }

    let file = fs::File::open(&paths.status_path).map_err(|error| error.to_string())?;
    let mut status: DaemonStatus =
        serde_json::from_reader(file).map_err(|error| error.to_string())?;
    status.service_mode = current_service_mode();
    status.public_key_path = Some(paths.public_key_path.display().to_string());

    Ok(status)
}

pub fn save_daemon_status<R: Runtime, M: Manager<R>>(
    app: &M,
    status: &DaemonStatus,
) -> Result<(), String> {
    let paths = AppPaths::new(app)?;
    ensure_runtime_layout(&paths)?;
    write_json(&paths.status_path, status)
}

fn write_daemon_runtime_config(
    paths: &AppPaths,
    settings: &CompanionSettings,
    token_persistence: &secrets::TokenPersistence,
) -> Result<(), String> {
    let runtime_config = DaemonRuntimeConfig {
        version: "0.1.0".into(),
        client_id: settings.client_id.clone(),
        central_name: settings.central_name.clone(),
        registration_url: settings.registration_url.clone(),
        chat_http_url: settings.chat_http_url.clone(),
        chat_ws_url: settings.chat_ws_url.clone(),
        status_ws_url: settings.status_ws_url.clone(),
        heartbeat_url: settings.heartbeat_url.clone(),
        api_token: daemon_runtime_api_token(settings, token_persistence),
        api_token_keyring_service: token_persistence.keyring_service.clone(),
        api_token_keyring_account: token_persistence.keyring_account.clone(),
        chat_model: settings.chat_model.clone(),
        node_name: settings.node_name.clone(),
        central_ssh_public_key: settings.central_ssh_public_key.clone(),
        ssh_authorized_user: settings.ssh_authorized_user.clone(),
        heartbeat_interval_seconds: settings.heartbeat_interval_seconds,
        retry_interval_seconds: settings.retry_interval_seconds,
        status_file: paths.status_path.display().to_string(),
        state_file: paths.state_path.display().to_string(),
        log_file: paths.log_path.display().to_string(),
        private_key_path: paths.private_key_path.display().to_string(),
        public_key_path: paths.public_key_path.display().to_string(),
    };

    write_json(&paths.daemon_config_path, &runtime_config)
}

fn daemon_runtime_api_token(
    settings: &CompanionSettings,
    token_persistence: &secrets::TokenPersistence,
) -> String {
    if !settings.api_token.trim().is_empty() {
        return settings.api_token.clone();
    }

    token_persistence.config_token.clone()
}

fn normalize_settings(mut settings: CompanionSettings, machine: &MachineInfo) -> CompanionSettings {
    if settings.client_id.trim().is_empty() {
        settings.client_id = Uuid::new_v4().to_string();
    }

    if settings.node_name.trim().is_empty() {
        settings.node_name = machine.hostname.clone();
    }

    if settings.central_name.trim().is_empty() {
        settings.central_name = "Central Hermes".into();
    }

    if settings.invite_redeem_url.trim().is_empty()
        && settings.invite_code_or_link.starts_with("http")
    {
        if let Some(origin) = extract_origin(&settings.invite_code_or_link) {
            settings.invite_redeem_url = format!("{origin}/api/device-invites/redeem");
        }
    }

    if settings.chat_model.trim().is_empty() {
        settings.chat_model = "gpt-4.1-mini".into();
    }

    if settings.ssh_authorized_user.trim().is_empty() {
        settings.ssh_authorized_user = machine.current_user.clone();
    }

    if settings.heartbeat_interval_seconds == 0 {
        settings.heartbeat_interval_seconds = 60;
    }

    if settings.retry_interval_seconds == 0 {
        settings.retry_interval_seconds = 15;
    }

    settings
}

fn default_status(paths: &AppPaths) -> DaemonStatus {
    let mut status = DaemonStatus::default();
    status.public_key_path = Some(paths.public_key_path.display().to_string());
    status
}

fn extract_origin(value: &str) -> Option<String> {
    let trimmed = value.trim();
    let scheme_end = trimmed.find("://")?;
    let host_start = scheme_end + 3;
    let path_start = trimmed[host_start..]
        .find('/')
        .map(|index| host_start + index)
        .unwrap_or(trimmed.len());
    Some(trimmed[..path_start].trim_end_matches('/').to_string())
}

fn write_json<T: serde::Serialize>(path: &Path, value: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }

    let serialized = serde_json::to_vec_pretty(value).map_err(|error| error.to_string())?;
    let mut file = fs::File::create(path).map_err(|error| error.to_string())?;
    file.write_all(&serialized)
        .map_err(|error| error.to_string())?;
    file.write_all(b"\n").map_err(|error| error.to_string())?;
    protect_file(path)?;

    Ok(())
}

fn ensure_runtime_layout(paths: &AppPaths) -> Result<(), String> {
    for directory in [
        &paths.root,
        &paths.daemon_root,
        &paths.daemon_bin_dir,
        &paths.keys_dir,
        &paths.status_dir,
        &paths.logs_dir,
    ] {
        fs::create_dir_all(directory).map_err(|error| error.to_string())?;
    }

    Ok(())
}

fn protect_file(path: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))
            .map_err(|error| error.to_string())?;
    }

    #[cfg(not(unix))]
    {
        let _ = path;
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::daemon_runtime_api_token;
    use crate::{models::CompanionSettings, secrets::TokenPersistence};

    #[test]
    fn daemon_runtime_prefers_in_memory_token_over_keyring_placeholder() {
        let settings = CompanionSettings {
            api_token: "node-token-123".into(),
            ..CompanionSettings::default()
        };
        let persistence = TokenPersistence {
            config_token: String::new(),
            keyring_service: "com.hermes.companion.api-token".into(),
            keyring_account: "client-123".into(),
        };

        assert_eq!(
            daemon_runtime_api_token(&settings, &persistence),
            "node-token-123"
        );
    }
}
