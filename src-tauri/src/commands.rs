use crate::{
    config, daemon,
    models::{
        ChatRequest, ChatResponse, CompanionSettings, DaemonStatus, InviteRedeemRequest,
        InviteRedeemResponse, IssuedInvite, MachineInfo, OperatorBootstrapRequest,
        OperatorBootstrapResponse, RemoteProvisionRequest, RemoteProvisionResponse,
        ServerSetupBundle, ServerSetupRequest,
    },
};
use serde_json::{json, Value};
use std::fs;
use std::path::Path;
use std::process::{Command, Stdio};
use tauri::{AppHandle, Manager, Runtime};

const CENTRAL_API_ENV_TEMPLATE: &str = include_str!("../../deploy/central-api.env.example");
const CHAT_BRIDGE_ENV_TEMPLATE: &str = include_str!("../../deploy/chat-bridge.env.example");
const NGINX_BOOTSTRAP_TEMPLATE: &str =
    include_str!("../../deploy/nginx/hermes-companion-central.bootstrap.conf");
const NGINX_TLS_TEMPLATE: &str = include_str!("../../deploy/nginx/hermes-companion-central.conf");
const CENTRAL_SERVICE_TEMPLATE: &str =
    include_str!("../../deploy/systemd/hermes-companion-central.service");
const CHAT_BRIDGE_SERVICE_TEMPLATE: &str =
    include_str!("../../deploy/systemd/hermes-companion-chat-bridge.service");

#[tauri::command]
pub fn get_machine_info() -> Result<MachineInfo, String> {
    Ok(config::detect_machine_info())
}

#[tauri::command]
pub fn get_settings(app: AppHandle) -> Result<CompanionSettings, String> {
    config::load_settings(&app)
}

#[tauri::command]
pub fn get_daemon_status(app: AppHandle) -> Result<DaemonStatus, String> {
    config::load_daemon_status(&app)
}

#[tauri::command]
pub fn connect_to_central(
    app: AppHandle,
    settings: CompanionSettings,
) -> Result<DaemonStatus, String> {
    connect_to_central_impl(&app, settings)
}

#[tauri::command]
pub fn bootstrap_operator_connect(
    app: AppHandle,
    request: OperatorBootstrapRequest,
) -> Result<OperatorBootstrapResponse, String> {
    bootstrap_operator_connect_impl(&app, request)
}

#[tauri::command]
pub fn create_server_setup_bundle(
    app: AppHandle,
    request: ServerSetupRequest,
) -> Result<ServerSetupBundle, String> {
    create_server_setup_bundle_impl(&app, request)
}

#[tauri::command]
pub fn provision_server_setup_bundle(
    request: RemoteProvisionRequest,
) -> Result<RemoteProvisionResponse, String> {
    provision_server_setup_bundle_impl(request)
}

pub(crate) fn connect_to_central_impl<R: Runtime, M: Manager<R>>(
    app: &M,
    settings: CompanionSettings,
) -> Result<DaemonStatus, String> {
    let machine = config::detect_machine_info();
    let enrolled = if settings.invite_code_or_link.trim().is_empty() {
        settings
    } else {
        redeem_invite(&settings, &machine)?
    };
    let normalized = config::save_settings(app, enrolled)?;
    daemon::install_and_start(app, &normalized)?;
    config::load_daemon_status(app)
}

fn bootstrap_operator_connect_impl<R: Runtime, M: Manager<R>>(
    app: &M,
    request: OperatorBootstrapRequest,
) -> Result<OperatorBootstrapResponse, String> {
    let machine = config::detect_machine_info();
    let base_url = normalize_central_api_url(&request.central_api_url)?;
    let invite = issue_operator_invite(&base_url, &request, &machine)?;

    let mut settings = config::load_settings(app)?;
    settings.central_name = if request.central_name.trim().is_empty() {
        settings.central_name.clone()
    } else {
        request.central_name.trim().to_string()
    };
    settings.chat_model = if request.chat_model.trim().is_empty() {
        settings.chat_model.clone()
    } else {
        request.chat_model.trim().to_string()
    };
    settings.invite_code_or_link = invite.invite_url.clone();
    settings.invite_redeem_url = format!("{base_url}/api/device-invites/redeem");
    settings.central_ssh_public_key = request.central_ssh_public_key.trim().to_string();
    settings.ssh_authorized_user = if request.ssh_authorized_user.trim().is_empty() {
        machine.current_user.clone()
    } else {
        request.ssh_authorized_user.trim().to_string()
    };

    let status = connect_to_central_impl(app, settings)?;
    Ok(OperatorBootstrapResponse { invite, status })
}

fn create_server_setup_bundle_impl<R: Runtime, M: Manager<R>>(
    app: &M,
    request: ServerSetupRequest,
) -> Result<ServerSetupBundle, String> {
    let paths = config::AppPaths::new(app)?;
    let hostname = normalize_central_hostname(&request.central_hostname)?;
    let central_url = format!("https://{hostname}");
    let central_name = if request.central_name.trim().is_empty() {
        "Central Hermes".to_string()
    } else {
        request.central_name.trim().to_string()
    };
    let repo_clone_url = if request.repo_clone_url.trim().is_empty() {
        "https://github.com/your-org/hermes-companion.git".to_string()
    } else {
        request.repo_clone_url.trim().to_string()
    };
    let admin_secret = if request.admin_secret.trim().is_empty() {
        generate_secret()
    } else {
        request.admin_secret.trim().to_string()
    };
    let chat_bridge_token = generate_secret();
    let chat_model = if request.chat_model.trim().is_empty() {
        "gpt-5.4-mini".to_string()
    } else {
        request.chat_model.trim().to_string()
    };
    let bundle_name = format!(
        "{}-{}",
        slugify_filename(&hostname),
        &uuid::Uuid::new_v4().simple().to_string()[..8]
    );
    let bundle_dir = paths.root.join("setup-bundles").join(bundle_name);
    fs::create_dir_all(&bundle_dir).map_err(|error| error.to_string())?;

    write_text_file(
        &bundle_dir.join("central-api.env"),
        &render_central_api_env(&admin_secret, &chat_bridge_token, &chat_model),
    )?;
    write_text_file(
        &bundle_dir.join("chat-bridge.env"),
        &render_chat_bridge_env(&chat_bridge_token, &chat_model),
    )?;
    write_text_file(
        &bundle_dir.join("hermes-companion-central.bootstrap.conf"),
        &replace_hostname(NGINX_BOOTSTRAP_TEMPLATE, &hostname),
    )?;
    write_text_file(
        &bundle_dir.join("hermes-companion-central.conf"),
        &replace_hostname(NGINX_TLS_TEMPLATE, &hostname),
    )?;
    write_text_file(
        &bundle_dir.join("hermes-companion-central.service"),
        CENTRAL_SERVICE_TEMPLATE,
    )?;
    write_text_file(
        &bundle_dir.join("hermes-companion-chat-bridge.service"),
        CHAT_BRIDGE_SERVICE_TEMPLATE,
    )?;

    let public_key_command = "sudo cat /etc/hermes-companion/keys/central-node-access.pub".to_string();
    let remote_bootstrap_command =
        "cd ~/hermes-companion-setup && chmod +x bootstrap-central.sh && ./bootstrap-central.sh"
            .to_string();
    let certbot_command = format!("sudo certbot --nginx -d {hostname}");
    let validate_command = format!(
        "python scripts/validate-central-api-deploy.py --base-url {} --admin-secret <your-admin-secret>",
        central_url
    );

    write_text_file(
        &bundle_dir.join("bootstrap-central.sh"),
        &render_bootstrap_script(&repo_clone_url, &hostname),
    )?;
    write_text_file(
        &bundle_dir.join("SETUP-CHECKLIST.md"),
        &render_setup_checklist(
            &central_name,
            &central_url,
            &repo_clone_url,
            request.server_ssh_target.trim(),
            &admin_secret,
            &public_key_command,
            &remote_bootstrap_command,
            &certbot_command,
            &validate_command,
        ),
    )?;

    let upload_script_path = if request.server_ssh_target.trim().is_empty() {
        None
    } else {
        let path = bundle_dir.join("upload-and-bootstrap.ps1");
        write_text_file(
            &path,
            &render_upload_script(request.server_ssh_target.trim(), &remote_bootstrap_command),
        )?;
        Some(path.display().to_string())
    };

    Ok(ServerSetupBundle {
        bundle_dir: bundle_dir.display().to_string(),
        central_url,
        admin_secret,
        chat_bridge_token,
        bootstrap_script_path: bundle_dir.join("bootstrap-central.sh").display().to_string(),
        upload_script_path,
        checklist_path: bundle_dir.join("SETUP-CHECKLIST.md").display().to_string(),
        public_key_command,
        remote_bootstrap_command,
        certbot_command,
        validate_command,
    })
}

fn provision_server_setup_bundle_impl(
    request: RemoteProvisionRequest,
) -> Result<RemoteProvisionResponse, String> {
    let bundle_dir = Path::new(request.bundle_dir.trim());
    if request.server_ssh_target.trim().is_empty() {
        return Err("Enter the server SSH target before uploading the setup pack.".into());
    }
    if !bundle_dir.exists() || !bundle_dir.is_dir() {
        return Err("The local setup bundle folder does not exist anymore. Generate it again first.".into());
    }

    ensure_command_available("ssh")?;
    ensure_command_available("scp")?;

    let remote_dir = if request.remote_dir.trim().is_empty() {
        "~/hermes-companion-setup".to_string()
    } else {
        request.remote_dir.trim().to_string()
    };
    let ssh_target = request.server_ssh_target.trim();
    let bundle_name = bundle_dir
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "The setup bundle folder name is invalid.".to_string())?;
    let upload_command = format!("scp -r \"{}\" \"{ssh_target}:~/\"", bundle_dir.display());
    let bootstrap_command = format!(
        "ssh \"{ssh_target}\" \"rm -rf {remote_dir} && mv ~/{bundle_name} {remote_dir} && cd {remote_dir} && chmod +x bootstrap-central.sh && ./bootstrap-central.sh\""
    );

    let mut transcript = String::new();
    transcript.push_str(&format!("Uploading setup pack to {ssh_target}...\n"));

    let scp_output = Command::new("scp")
        .arg("-r")
        .arg(bundle_dir)
        .arg(format!("{ssh_target}:~/"))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|error| format!("Failed to start scp: {error}"))?;

    transcript.push_str(&capture_output("scp", &scp_output));
    if !scp_output.status.success() {
        return Err(format!(
            "Failed to upload the setup pack.\n\n{}",
            capture_output("scp", &scp_output)
        ));
    }

    transcript.push_str("\nRunning remote bootstrap...\n");

    let ssh_output = Command::new("ssh")
        .arg(ssh_target)
        .arg(format!(
            "rm -rf {remote_dir} && mv ~/{bundle_name} {remote_dir} && cd {remote_dir} && chmod +x bootstrap-central.sh && ./bootstrap-central.sh"
        ))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|error| format!("Failed to start ssh: {error}"))?;

    transcript.push_str(&capture_output("ssh", &ssh_output));
    if !ssh_output.status.success() {
        return Err(format!(
            "The server bootstrap command failed.\n\n{}",
            capture_output("ssh", &ssh_output)
        ));
    }

    Ok(RemoteProvisionResponse {
        upload_command,
        bootstrap_command,
        transcript,
    })
}

#[tauri::command]
pub async fn send_chat_message(
    app: AppHandle,
    request: ChatRequest,
) -> Result<ChatResponse, String> {
    let settings = config::load_settings(&app)?;
    if settings.chat_http_url.trim().is_empty() {
        return Err("Add a chat HTTP URL in Settings before using the chat composer.".into());
    }

    let machine = config::detect_machine_info();
    let model = settings.chat_model.clone();
    let client = reqwest::Client::builder()
        .user_agent("HermesCompanion/0.1.0")
        .build()
        .map_err(|error| error.to_string())?;

    let payload = if settings.chat_http_url.contains("/chat/completions") {
        json!({
          "model": model,
          "messages": [
            {
              "role": "system",
              "content": format!(
                "You are central Hermes. You are talking to the Hermes Companion node named {} running on {} {}. Keep replies concise, operational, and safe for a desktop operator.",
                machine.hostname, machine.os_type, machine.arch
              )
            },
            {
              "role": "user",
              "content": request.message
            }
          ]
        })
    } else {
        json!({
          "model": model,
          "input": [
            {
              "role": "system",
              "content": [
                {
                  "type": "input_text",
                  "text": format!(
                    "You are central Hermes. You are talking to the Hermes Companion node named {} running on {} {}. Keep replies concise, operational, and safe for a desktop operator.",
                    machine.hostname, machine.os_type, machine.arch
                  )
                }
              ]
            },
            {
              "role": "user",
              "content": [
                {
                  "type": "input_text",
                  "text": request.message
                }
              ]
            }
          ],
          "metadata": {
            "client_id": settings.client_id,
            "hostname": machine.hostname,
            "platform": machine.os_type,
            "arch": machine.arch
          }
        })
    };

    let mut builder = client.post(&settings.chat_http_url).json(&payload);
    if !settings.api_token.trim().is_empty() {
        builder = builder.bearer_auth(settings.api_token.trim());
    }

    let response = builder.send().await.map_err(|error| error.to_string())?;
    let status = response.status();

    if !status.is_success() {
        let body = response.text().await.unwrap_or_default();
        return Err(format!("Central Hermes returned {}: {}", status, body));
    }

    let raw_response: Value = response.json().await.map_err(|error| error.to_string())?;
    let message = extract_assistant_text(&raw_response).unwrap_or_else(|| {
        "Central Hermes returned a response, but no assistant text was found.".into()
    });

    Ok(ChatResponse {
        message,
        raw_response,
    })
}

fn extract_assistant_text(value: &Value) -> Option<String> {
    if let Some(text) = value.get("output_text").and_then(Value::as_str) {
        return Some(text.trim().to_string());
    }

    if let Some(content) = value
        .get("choices")
        .and_then(|choices| choices.get(0))
        .and_then(|choice| choice.get("message"))
        .and_then(|message| message.get("content"))
    {
        if let Some(text) = content.as_str() {
            return Some(text.trim().to_string());
        }

        if let Some(array) = content.as_array() {
            let merged = array
                .iter()
                .filter_map(|item| item.get("text").and_then(Value::as_str))
                .collect::<Vec<_>>()
                .join("\n");
            if !merged.is_empty() {
                return Some(merged);
            }
        }
    }

    let response_segments = value
        .get("output")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .flat_map(|item| {
                    item.get("content")
                        .and_then(Value::as_array)
                        .into_iter()
                        .flatten()
                })
                .filter_map(|content| {
                    content
                        .get("text")
                        .and_then(Value::as_str)
                        .or_else(|| content.get("output_text").and_then(Value::as_str))
                })
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default();

    if !response_segments.is_empty() {
        return Some(response_segments);
    }

    None
}

fn redeem_invite(
    settings: &CompanionSettings,
    machine: &MachineInfo,
) -> Result<CompanionSettings, String> {
    let invite_code = extract_invite_code(&settings.invite_code_or_link).ok_or_else(|| {
        "The invite field must contain either a redeemable invite URL or invite code.".to_string()
    })?;
    let redeem_url = resolve_invite_redeem_url(settings)?;

    let client = reqwest::blocking::Client::builder()
        .user_agent("HermesCompanion/0.1.0")
        .build()
        .map_err(|error| error.to_string())?;

    let request = InviteRedeemRequest {
        invite_code,
        machine: machine.clone(),
    };

    let response = client
        .post(&redeem_url)
        .json(&request)
        .send()
        .map_err(|error| error.to_string())?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().unwrap_or_default();
        return Err(format!(
            "Invite redemption failed with {}: {}",
            status, body
        ));
    }

    let bundle: InviteRedeemResponse = response.json().map_err(|error| error.to_string())?;
    let mut merged = settings.clone();
    merged.invite_code_or_link = String::new();
    merged.central_name = bundle.central_name;
    merged.registration_url = bundle.registration_url;
    merged.chat_http_url = bundle.chat_http_url;
    merged.chat_ws_url = bundle.chat_ws_url;
    merged.status_ws_url = bundle.status_ws_url;
    merged.heartbeat_url = bundle.heartbeat_url;
    merged.api_token = bundle.api_token;
    merged.chat_model = bundle.chat_model;
    merged.central_ssh_public_key = bundle.central_ssh_public_key;
    merged.ssh_authorized_user = if bundle.ssh_authorized_user.trim().is_empty() {
        machine.current_user.clone()
    } else {
        bundle.ssh_authorized_user
    };
    Ok(merged)
}

fn issue_operator_invite(
    base_url: &str,
    request: &OperatorBootstrapRequest,
    machine: &MachineInfo,
) -> Result<IssuedInvite, String> {
    if request.admin_secret.trim().is_empty() {
        return Err("Enter the central admin secret before creating an invite.".into());
    }

    if request.central_ssh_public_key.trim().is_empty() {
        return Err("Enter the central SSH public key before creating an invite.".into());
    }

    let invite_url = format!("{base_url}/api/device-invites");
    let note = if request.note.trim().is_empty() {
        format!(
            "Hermes Companion self-enrollment for {} ({})",
            machine.hostname, machine.current_user
        )
    } else {
        request.note.trim().to_string()
    };
    let central_name = if request.central_name.trim().is_empty() {
        "Central Hermes".to_string()
    } else {
        request.central_name.trim().to_string()
    };
    let chat_model = if request.chat_model.trim().is_empty() {
        "gpt-5".to_string()
    } else {
        request.chat_model.trim().to_string()
    };
    let ssh_authorized_user = if request.ssh_authorized_user.trim().is_empty() {
        machine.current_user.clone()
    } else {
        request.ssh_authorized_user.trim().to_string()
    };

    let payload = json!({
        "note": note,
        "expires_in_minutes": request.expires_in_minutes.clamp(5, 7 * 24 * 60),
        "central_name": central_name,
        "chat_model": chat_model,
        "central_ssh_public_key": request.central_ssh_public_key.trim(),
        "ssh_authorized_user": ssh_authorized_user,
    });

    let client = reqwest::blocking::Client::builder()
        .user_agent("HermesCompanion/0.1.0")
        .build()
        .map_err(|error| error.to_string())?;

    let response = client
        .post(&invite_url)
        .header("X-Hermes-Admin-Secret", request.admin_secret.trim())
        .json(&payload)
        .send()
        .map_err(|error| error.to_string())?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().unwrap_or_default();
        return Err(format!("Invite creation failed with {}: {}", status, body));
    }

    let payload: Value = response.json().map_err(|error| error.to_string())?;
    Ok(IssuedInvite {
        invite_id: required_text_field(&payload, "inviteId")?,
        invite_code: required_text_field(&payload, "inviteCode")?,
        invite_url: required_text_field(&payload, "inviteUrl")?,
        expires_at: required_text_field(&payload, "expiresAt")?,
    })
}

fn resolve_invite_redeem_url(settings: &CompanionSettings) -> Result<String, String> {
    if !settings.invite_redeem_url.trim().is_empty() {
        return Ok(settings.invite_redeem_url.trim().to_string());
    }

    let trimmed = settings.invite_code_or_link.trim();
    if let Some(origin) = extract_origin(trimmed) {
        return Ok(format!("{origin}/api/device-invites/redeem"));
    }

    Err("Invite redemption URL is missing. Paste a full invite link or set the redeem URL in Advanced Settings.".into())
}

fn extract_invite_code(value: &str) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return None;
    }

    if !trimmed.starts_with("http://") && !trimmed.starts_with("https://") {
        return Some(trimmed.to_string());
    }

    let query_marker = "code=";
    if let Some(index) = trimmed.find(query_marker) {
        let remaining = &trimmed[index + query_marker.len()..];
        let code = remaining
            .split('&')
            .next()
            .unwrap_or_default()
            .trim_matches('/')
            .trim();
        if !code.is_empty() {
            return Some(code.to_string());
        }
    }

    trimmed
        .trim_end_matches('/')
        .rsplit('/')
        .next()
        .filter(|segment| !segment.is_empty() && !segment.contains('?'))
        .map(ToString::to_string)
}

fn extract_origin(value: &str) -> Option<String> {
    let scheme_end = value.find("://")?;
    let host_start = scheme_end + 3;
    let path_start = value[host_start..]
        .find('/')
        .map(|index| host_start + index)
        .unwrap_or(value.len());
    Some(value[..path_start].trim_end_matches('/').to_string())
}

fn normalize_central_api_url(value: &str) -> Result<String, String> {
    let mut normalized = value.trim().to_string();
    if normalized.is_empty() {
        return Err(
            "Enter the public central Hermes URL first, for example https://companion.example.com."
                .into(),
        );
    }

    if !normalized.starts_with("http://") && !normalized.starts_with("https://") {
        normalized = format!("https://{normalized}");
    }

    normalized = normalized.trim_end_matches('/').to_string();

    for suffix in [
        "/api/device-invites/redeem",
        "/api/device-invites",
        "/api/register-node",
        "/api/node-heartbeat",
        "/api/health",
        "/api",
    ] {
        if normalized.ends_with(suffix) {
            normalized.truncate(normalized.len() - suffix.len());
            break;
        }
    }

    Ok(normalized.trim_end_matches('/').to_string())
}

fn normalize_central_hostname(value: &str) -> Result<String, String> {
    let normalized = normalize_central_api_url(value)?;
    let without_scheme = normalized
        .strip_prefix("https://")
        .or_else(|| normalized.strip_prefix("http://"))
        .unwrap_or(&normalized)
        .trim()
        .trim_matches('/');

    if without_scheme.is_empty() || without_scheme.contains('/') {
        return Err("Enter a public hostname such as companion.example.com.".into());
    }

    Ok(without_scheme.to_string())
}

fn ensure_command_available(command: &str) -> Result<(), String> {
    Command::new(command)
        .arg("-V")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .or_else(|_| {
            Command::new(command)
                .arg("-v")
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
        })
        .map(|_| ())
        .map_err(|_| format!("`{command}` was not found on this machine. Install OpenSSH first."))
}

fn capture_output(command: &str, output: &std::process::Output) -> String {
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    format!(
        "$ {command}\n{}\n{}",
        stdout.trim_end(),
        stderr.trim_end()
    )
    .trim()
    .to_string()
}

fn generate_secret() -> String {
    format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    )
}

fn slugify_filename(value: &str) -> String {
    let slug = value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() {
                character.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect::<String>();

    slug.trim_matches('-')
        .split('-')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("-")
}

fn replace_hostname(template: &str, hostname: &str) -> String {
    template.replace("companion.example.com", hostname)
}

fn render_central_api_env(admin_secret: &str, chat_bridge_token: &str, chat_model: &str) -> String {
    CENTRAL_API_ENV_TEMPLATE
        .replace(
            "HERMES_ADMIN_SECRET=replace-with-a-long-random-secret",
            &format!("HERMES_ADMIN_SECRET={admin_secret}"),
        )
        .replace(
            "HERMES_PROXY_RESPONSES_URL=",
            "HERMES_PROXY_RESPONSES_URL=http://127.0.0.1:8788/v1/responses",
        )
        .replace(
            "HERMES_PROXY_BEARER_TOKEN=",
            &format!("HERMES_PROXY_BEARER_TOKEN={chat_bridge_token}"),
        )
        .replace(
            "# HERMES_DEFAULT_CHAT_MODEL=gpt-5.4-mini",
            &format!("HERMES_DEFAULT_CHAT_MODEL={chat_model}"),
        )
}

fn render_chat_bridge_env(chat_bridge_token: &str, chat_model: &str) -> String {
    CHAT_BRIDGE_ENV_TEMPLATE
        .replace(
            "HERMES_CHAT_BRIDGE_TOKEN=replace-with-a-long-random-secret",
            &format!("HERMES_CHAT_BRIDGE_TOKEN={chat_bridge_token}"),
        )
        .replace(
            "HERMES_CHAT_BRIDGE_DEFAULT_MODEL=gpt-5.4-mini",
            &format!("HERMES_CHAT_BRIDGE_DEFAULT_MODEL={chat_model}"),
        )
}

fn render_bootstrap_script(repo_clone_url: &str, hostname: &str) -> String {
    format!(
        r#"#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
APP_DIR="/opt/hermes-companion"
CONFIG_DIR="/etc/hermes-companion"
NGINX_SITE="/etc/nginx/sites-available/hermes-companion-central.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/hermes-companion-central.conf"

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y git python3 python3-venv nginx certbot python3-certbot-nginx

echo "==> Creating service users"
if ! id -u hermes-companion >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir /var/lib/hermes-companion --shell /usr/sbin/nologin hermes-companion
fi

if ! id -u hermes-companion-chat >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir /var/lib/hermes-companion-chat --shell /usr/sbin/nologin hermes-companion-chat
fi

echo "==> Creating directories"
sudo mkdir -p /etc/hermes-companion /var/lib/hermes-companion /var/lib/hermes-companion-chat /etc/hermes-companion/keys /opt

echo "==> Installing or updating the repo"
if [ ! -d "$APP_DIR/.git" ]; then
  sudo git clone "{repo_clone_url}" "$APP_DIR"
else
  sudo git -C "$APP_DIR" pull --ff-only
fi

echo "==> Installing Python dependencies"
sudo python3 -m venv "$APP_DIR/.venv"
sudo "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/central-api/requirements.txt"

echo "==> Installing generated config files"
sudo install -m 600 "$SCRIPT_DIR/central-api.env" "$CONFIG_DIR/central-api.env"
sudo install -m 600 "$SCRIPT_DIR/chat-bridge.env" "$CONFIG_DIR/chat-bridge.env"
sudo install -m 644 "$SCRIPT_DIR/hermes-companion-central.service" /etc/systemd/system/hermes-companion-central.service
sudo install -m 644 "$SCRIPT_DIR/hermes-companion-chat-bridge.service" /etc/systemd/system/hermes-companion-chat-bridge.service
sudo install -m 644 "$SCRIPT_DIR/hermes-companion-central.bootstrap.conf" "$NGINX_SITE"
sudo ln -sf "$NGINX_SITE" "$NGINX_ENABLED"

echo "==> Generating dedicated central SSH key if missing"
if [ ! -f /etc/hermes-companion/keys/central-node-access ]; then
  sudo ssh-keygen -t ed25519 -f /etc/hermes-companion/keys/central-node-access -N '' -C 'hermes-companion-central'
fi

echo "==> Enabling services"
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-companion-central.service
sudo systemctl enable hermes-companion-chat-bridge.service

echo "==> Validating nginx config"
sudo nginx -t
sudo systemctl restart nginx

echo
echo "Bootstrap complete for {hostname}."
echo "Next:"
echo "1. Run: sudo certbot --nginx -d {hostname}"
echo "2. Replace the bootstrap nginx config with hermes-companion-central.conf after certbot succeeds."
echo "3. Restart nginx."
echo "4. Print the public key with: sudo cat /etc/hermes-companion/keys/central-node-access.pub"
"#
    )
}

fn render_setup_checklist(
    central_name: &str,
    central_url: &str,
    repo_clone_url: &str,
    server_ssh_target: &str,
    admin_secret: &str,
    public_key_command: &str,
    remote_bootstrap_command: &str,
    certbot_command: &str,
    validate_command: &str,
) -> String {
    let ssh_block = if server_ssh_target.is_empty() {
        "1. Copy this whole bundle to your Ubuntu server.\n2. SSH into the server.\n3. Run `chmod +x bootstrap-central.sh && ./bootstrap-central.sh`.\n".to_string()
    } else {
        format!(
            "1. Copy this whole bundle to the server:\n   `scp -r . {server_ssh_target}:~/hermes-companion-setup`\n2. SSH into the server:\n   `ssh {server_ssh_target}`\n3. Run the bootstrap script:\n   `{remote_bootstrap_command}`\n"
        )
    };

    format!(
        r#"# Hermes Companion Server Setup

This bundle was generated locally for **{central_name}**.

## What This Bundle Contains

- `central-api.env`: central control-plane environment
- `chat-bridge.env`: safe local chat bridge environment
- `hermes-companion-central.bootstrap.conf`: nginx config for first HTTP bootstrap
- `hermes-companion-central.conf`: nginx config for HTTPS after certbot
- `hermes-companion-central.service`: systemd unit for central-api
- `hermes-companion-chat-bridge.service`: systemd unit for the safe chat bridge
- `bootstrap-central.sh`: guided Ubuntu bootstrap script

## Your Public URL

- `{central_url}`

## Admin Secret

Keep this private:

`{admin_secret}`

## Repo URL

- `{repo_clone_url}`

## Step By Step

{ssh_block}
4. Request a TLS certificate:
   `{certbot_command}`
5. Replace the bootstrap nginx config with the TLS config:
   `sudo install -m 644 hermes-companion-central.conf /etc/nginx/sites-available/hermes-companion-central.conf && sudo nginx -t && sudo systemctl reload nginx`
6. Print the dedicated central SSH public key:
   `{public_key_command}`
7. Return to Hermes Companion on your local machine and use:
   - Central URL: `{central_url}`
   - Admin secret: the secret above
   - Central SSH public key: the output from the command above
8. Validate the live deployment:
   `{validate_command}`

## Notes

- The app never stores your admin secret in the repo.
- The generated files live only on this machine under the Hermes Companion app data directory until you copy them elsewhere.
- The chat bridge files are included because they are the recommended safe production shape, but they still require a compatible Hermes agent runtime on the server.
"#
    )
}

fn render_upload_script(server_ssh_target: &str, remote_bootstrap_command: &str) -> String {
    format!(
        r#"$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RemoteDir = "~/hermes-companion-setup"

Write-Host "Copying setup bundle to {server_ssh_target}..."
scp -r "$ScriptDir\*" "{server_ssh_target}:$RemoteDir/"

Write-Host "Running bootstrap script on {server_ssh_target}..."
ssh "{server_ssh_target}" "{remote_bootstrap_command}"

Write-Host "Done. Next: run certbot on the server and then return to Hermes Companion."
"#
    )
}

fn write_text_file(path: &Path, content: &str) -> Result<(), String> {
    fs::write(path, content).map_err(|error| error.to_string())
}

fn required_text_field(value: &Value, field: &str) -> Result<String, String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(|text| text.to_string())
        .filter(|text| !text.trim().is_empty())
        .ok_or_else(|| format!("Central Hermes did not return {field}."))
}

#[cfg(test)]
mod tests {
    use super::{
        extract_assistant_text, extract_invite_code, normalize_central_api_url,
        render_setup_checklist, render_upload_script,
    };
    use serde_json::json;

    #[test]
    fn extracts_output_text_field() {
        let payload = json!({
            "output_text": "Central Hermes says hello."
        });

        assert_eq!(
            extract_assistant_text(&payload).as_deref(),
            Some("Central Hermes says hello.")
        );
    }

    #[test]
    fn extracts_chat_completions_string_content() {
        let payload = json!({
            "choices": [
                {
                    "message": {
                        "content": "Completion style response"
                    }
                }
            ]
        });

        assert_eq!(
            extract_assistant_text(&payload).as_deref(),
            Some("Completion style response")
        );
    }

    #[test]
    fn extracts_responses_api_content_array() {
        let payload = json!({
            "output": [
                {
                    "content": [
                        { "type": "output_text", "text": "Line one" },
                        { "type": "output_text", "text": "Line two" }
                    ]
                }
            ]
        });

        assert_eq!(
            extract_assistant_text(&payload).as_deref(),
            Some("Line one\nLine two")
        );
    }

    #[test]
    fn extracts_invite_code_from_url() {
        assert_eq!(
            extract_invite_code("https://hermes.example.com/invite?code=abc123").as_deref(),
            Some("abc123")
        );
    }

    #[test]
    fn normalizes_base_url_from_health_endpoint() {
        assert_eq!(
            normalize_central_api_url("https://companion.example.com/api/health").unwrap(),
            "https://companion.example.com"
        );
    }

    #[test]
    fn normalizes_base_url_from_redeem_endpoint() {
        assert_eq!(
            normalize_central_api_url("https://companion.example.com/api/device-invites/redeem")
                .unwrap(),
            "https://companion.example.com"
        );
    }

    #[test]
    fn adds_https_for_bare_hostnames() {
        assert_eq!(
            normalize_central_api_url("companion.example.com").unwrap(),
            "https://companion.example.com"
        );
    }

    #[test]
    fn setup_checklist_mentions_certbot_and_validation() {
        let checklist = render_setup_checklist(
            "Central Hermes",
            "https://companion.example.com",
            "https://github.com/your-org/hermes-companion.git",
            "ubuntu@example-host",
            "super-secret",
            "sudo cat /etc/hermes-companion/keys/central-node-access.pub",
            "cd ~/hermes-companion-setup && chmod +x bootstrap-central.sh && ./bootstrap-central.sh",
            "sudo certbot --nginx -d companion.example.com",
            "python scripts/validate-central-api-deploy.py --base-url https://companion.example.com --admin-secret <your-admin-secret>",
        );

        assert!(checklist.contains("sudo certbot --nginx -d companion.example.com"));
        assert!(checklist.contains("validate-central-api-deploy.py"));
        assert!(checklist.contains("super-secret"));
    }

    #[test]
    fn upload_script_targets_requested_host() {
        let script = render_upload_script(
            "ubuntu@example-host",
            "cd ~/hermes-companion-setup && chmod +x bootstrap-central.sh && ./bootstrap-central.sh",
        );

        assert!(script.contains("ubuntu@example-host"));
        assert!(script.contains("scp -r"));
        assert!(script.contains("ssh"));
    }
}
