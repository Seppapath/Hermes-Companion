use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MachineInfo {
    pub hostname: String,
    pub os_type: String,
    pub arch: String,
    pub current_user: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct CompanionSettings {
    pub client_id: String,
    pub node_name: String,
    pub central_name: String,
    pub invite_code_or_link: String,
    pub invite_redeem_url: String,
    pub registration_url: String,
    pub chat_http_url: String,
    pub chat_ws_url: String,
    pub status_ws_url: String,
    pub heartbeat_url: String,
    pub api_token: String,
    pub chat_model: String,
    pub central_ssh_public_key: String,
    pub ssh_authorized_user: String,
    pub heartbeat_interval_seconds: u64,
    pub retry_interval_seconds: u64,
}

impl Default for CompanionSettings {
    fn default() -> Self {
        Self {
            client_id: String::new(),
            node_name: String::new(),
            central_name: "Central Hermes".into(),
            invite_code_or_link: String::new(),
            invite_redeem_url: String::new(),
            registration_url: String::new(),
            chat_http_url: String::new(),
            chat_ws_url: String::new(),
            status_ws_url: String::new(),
            heartbeat_url: String::new(),
            api_token: String::new(),
            chat_model: "gpt-5".into(),
            central_ssh_public_key: String::new(),
            ssh_authorized_user: String::new(),
            heartbeat_interval_seconds: 60,
            retry_interval_seconds: 15,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct DaemonStatus {
    pub state: String,
    pub registered: bool,
    pub node_id: Option<String>,
    pub last_registration_at: Option<String>,
    pub last_heartbeat_at: Option<String>,
    pub last_error: Option<String>,
    pub daemon_version: String,
    pub public_key_path: Option<String>,
    pub service_mode: String,
    pub ssh_access_configured: bool,
    pub ssh_authorized_user: Option<String>,
}

impl Default for DaemonStatus {
    fn default() -> Self {
        Self {
            state: "idle".into(),
            registered: false,
            node_id: None,
            last_registration_at: None,
            last_heartbeat_at: None,
            last_error: None,
            daemon_version: "0.1.0".into(),
            public_key_path: None,
            service_mode: current_service_mode(),
            ssh_access_configured: false,
            ssh_authorized_user: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatRequest {
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatResponse {
    pub message: String,
    pub raw_response: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OperatorBootstrapRequest {
    pub central_api_url: String,
    pub admin_secret: String,
    pub central_ssh_public_key: String,
    pub ssh_authorized_user: String,
    pub expires_in_minutes: u64,
    pub central_name: String,
    pub chat_model: String,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct IssuedInvite {
    pub invite_id: String,
    pub invite_code: String,
    pub invite_url: String,
    pub expires_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OperatorBootstrapResponse {
    pub invite: IssuedInvite,
    pub status: DaemonStatus,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerSetupRequest {
    pub central_hostname: String,
    pub central_name: String,
    pub repo_clone_url: String,
    pub server_ssh_target: String,
    pub admin_secret: String,
    pub chat_model: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerSetupBundle {
    pub bundle_dir: String,
    pub central_url: String,
    pub admin_secret: String,
    pub chat_bridge_token: String,
    pub bootstrap_script_path: String,
    pub upload_script_path: Option<String>,
    pub checklist_path: String,
    pub public_key_command: String,
    pub remote_bootstrap_command: String,
    pub certbot_command: String,
    pub validate_command: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RemoteProvisionRequest {
    pub bundle_dir: String,
    pub server_ssh_target: String,
    pub remote_dir: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RemoteProvisionResponse {
    pub upload_command: String,
    pub bootstrap_command: String,
    pub transcript: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DaemonRuntimeConfig {
    pub version: String,
    pub client_id: String,
    pub central_name: String,
    pub registration_url: String,
    pub chat_http_url: String,
    pub chat_ws_url: String,
    pub status_ws_url: String,
    pub heartbeat_url: String,
    pub api_token: String,
    pub api_token_keyring_service: String,
    pub api_token_keyring_account: String,
    pub chat_model: String,
    pub node_name: String,
    pub central_ssh_public_key: String,
    pub ssh_authorized_user: String,
    pub heartbeat_interval_seconds: u64,
    pub retry_interval_seconds: u64,
    pub status_file: String,
    pub state_file: String,
    pub log_file: String,
    pub private_key_path: String,
    pub public_key_path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InviteRedeemRequest {
    pub invite_code: String,
    pub machine: MachineInfo,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InviteRedeemResponse {
    pub central_name: String,
    pub registration_url: String,
    pub chat_http_url: String,
    pub chat_ws_url: String,
    pub status_ws_url: String,
    pub heartbeat_url: String,
    pub api_token: String,
    pub chat_model: String,
    pub central_ssh_public_key: String,
    pub ssh_authorized_user: String,
}

pub fn current_service_mode() -> String {
    #[cfg(target_os = "macos")]
    {
        return "launch-agent".into();
    }

    #[cfg(target_os = "windows")]
    {
        return "startup-folder".into();
    }

    #[cfg(target_os = "linux")]
    {
        return "systemd-user".into();
    }

    #[allow(unreachable_code)]
    "manual".into()
}
